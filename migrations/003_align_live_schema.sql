-- =============================================================================
-- TG Space Bot :: align an existing live database with migrations 001 + 002
-- =============================================================================
-- The live project created public.files by hand: ``id`` is an integer sequence
-- instead of the uuid 001 used at the time (001 now creates a bigint identity,
-- matching this live schema), and none of the triggers, constraints or indexes
-- from 001/002 existed there. This migration is idempotent and safe to run on the
-- live database *and* on one created from 001 + 002.
--
-- Applied to the live project on 2026-11-15. Verified afterwards:
--   * both triggers present and enabled,
--   * a test row (1024 bytes, then updated to 2048, then deleted) returned
--     files_stats.total_bytes to exactly 0, so all three trigger branches work,
--   * indexes, unique key, check constraints and RLS in place.
--
-- Run with:  SUPABASE_DATABASE_URL=postgresql://... python -m scripts.init_db
-- =============================================================================

begin;

-- ---------------------------------------------------------------------------
-- 1. Maintained size totals (migration 002)
-- ---------------------------------------------------------------------------
create table if not exists public.files_stats (
    setting     text         primary key,
    value       bigint       not null default 0,
    updated_at  timestamptz  not null default timezone('utc', now())
);

comment on table public.files_stats is
    'Cached aggregates for the files table, kept in sync by triggers.';

insert into public.files_stats (setting, value)
    values ('total_bytes', 0)
    on conflict (setting) do nothing;

-- security definer keeps the counter maintenance working even when the caller
-- is restricted by RLS or lacks direct privileges on files_stats.
create or replace function public.sync_files_stats()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if tg_op = 'INSERT' then
        update public.files_stats
            set value = value + coalesce(new.file_size, 0),
                updated_at = timezone('utc', now())
            where setting = 'total_bytes';
    elsif tg_op = 'UPDATE' then
        update public.files_stats
            set value = value + (coalesce(new.file_size, 0) - coalesce(old.file_size, 0)),
                updated_at = timezone('utc', now())
            where setting = 'total_bytes';
    elsif tg_op = 'DELETE' then
        update public.files_stats
            set value = value - coalesce(old.file_size, 0),
                updated_at = timezone('utc', now())
            where setting = 'total_bytes';
    end if;
    return coalesce(new, old);
end;
$$;

drop trigger if exists files_sync_stats on public.files;
create trigger files_sync_stats
    after insert or update of file_size or delete on public.files
    for each row
    execute function public.sync_files_stats();

-- ---------------------------------------------------------------------------
-- 2. updated_at maintenance (migration 001)
-- ---------------------------------------------------------------------------
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = timezone('utc', now());
    return new;
end;
$$;

drop trigger if exists files_touch_updated_at on public.files;
create trigger files_touch_updated_at
    before update on public.files
    for each row
    execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- 3. Duplicate protection and value checks (were missing on the live table)
-- ---------------------------------------------------------------------------
create unique index if not exists files_storage_message_key
    on public.files (chat_id, message_id);

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'files_category_check'
          and conrelid = 'public.files'::regclass
    ) then
        alter table public.files
            add constraint files_category_check
            check (category in ('document', 'image', 'video', 'audio', 'archive', 'other'));
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'files_file_size_check'
          and conrelid = 'public.files'::regclass
    ) then
        alter table public.files
            add constraint files_file_size_check
            check (file_size >= 0);
    end if;
end;
$$;

-- ---------------------------------------------------------------------------
-- 4. Indexes used by browse and search
-- ---------------------------------------------------------------------------
create extension if not exists pg_trgm;

create index if not exists files_created_at_idx
    on public.files (created_at desc);
create index if not exists files_category_created_at_idx
    on public.files (category, created_at desc);
create index if not exists files_file_name_trgm_idx
    on public.files using gin (file_name gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 5. Backfill the counter, then lock the tables down
-- ---------------------------------------------------------------------------
-- The bot must talk to PostgREST with the service_role key: that role bypasses
-- RLS, while anon/authenticated keys stay locked out of these tables.
update public.files_stats
    set value = (select coalesce(sum(file_size), 0) from public.files),
        updated_at = timezone('utc', now())
    where setting = 'total_bytes';

alter table public.files enable row level security;
alter table public.files_stats enable row level security;

commit;
