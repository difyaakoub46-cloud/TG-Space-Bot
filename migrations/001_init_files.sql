-- =============================================================================
-- TG Space Bot :: initial schema
-- Run against the Supabase Postgres database of the bot.
-- Idempotent: safe to execute multiple times.
-- =============================================================================

create extension if not exists pg_trgm;

create table if not exists public.files (
    id                  uuid        primary key default gen_random_uuid(),
    file_id             text        not null,
    file_unique_id      text,
    file_name           text        not null,
    category            text        not null,
    mime_type           text,
    size_bytes          bigint      not null default 0,
    uploader_id         bigint,
    storage_chat_id     bigint      not null,
    storage_message_id  bigint      not null,
    created_at          timestamptz not null default timezone('utc', now()),
    updated_at          timestamptz not null default timezone('utc', now()),
    constraint files_category_check
        check (category in ('document', 'image', 'video', 'audio', 'archive', 'other')),
    constraint files_size_bytes_check
        check (size_bytes >= 0),
    constraint files_storage_message_key
        unique (storage_chat_id, storage_message_id)
);

comment on table public.files is
    'Metadata for files physically stored inside Telegram storage groups.';
comment on column public.files.storage_chat_id is
    'Telegram chat id of the storage group holding the file.';
comment on column public.files.storage_message_id is
    'Message id of the stored copy inside the storage group.';

create index if not exists files_created_at_idx
    on public.files (created_at desc);
create index if not exists files_category_created_at_idx
    on public.files (category, created_at desc);
create index if not exists files_file_name_trgm_idx
    on public.files using gin (file_name gin_trgm_ops);

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

-- The bot talks to Postgres with the service role key, which bypasses RLS.
-- Enabling RLS without policies keeps the table closed to anon/authenticated keys.
alter table public.files enable row level security;
