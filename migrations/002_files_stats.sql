-- =============================================================================
-- TG Space Bot :: maintained size totals (avoids disallowed PostgREST aggregates)
-- =============================================================================
-- Replaces any ``size_bytes.sum()`` / ``file_size.sum()`` aggregate query that raises
-- PGRST123 when Supabase has aggregate queries disabled. The counter is kept in sync by
-- a trigger, so ``total_size()`` reads a single cheap row instead of summing the table.

create table if not exists public.files_stats (
    setting     text         primary key,
    value       bigint       not null default 0,
    updated_at  timestamptz  not null default timezone('utc', now())
);

comment on table public.files_stats is
    'Cached aggregates for the files table, kept in sync by triggers.';

-- Ensure the counter rows exist (idempotent).
insert into public.files_stats (setting, value)
    values ('total_bytes', 0)
    on conflict (setting) do update
        set value = coalesce(excluded.value, 0),
            updated_at = timezone('utc', now());

-- The app inserts into ``files`` using the column name ``size_bytes`` (alias for the
-- real ``file_size`` column), so the trigger must react to changes of ``file_size``.
create or replace function public.sync_files_stats()
returns trigger
language plpgsql
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

-- Backfill from existing data (safe when the table is empty).
update public.files_stats
    set value = (select coalesce(sum(file_size), 0) from public.files),
        updated_at = timezone('utc', now())
    where setting = 'total_bytes';
