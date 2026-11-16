-- 008: content fingerprint and duplicate protection.
--
-- content_hash is the SHA-256 of the file bytes, computed at upload time for
-- files the bot can download (up to HASH_MAX_BYTES). Two files with the same
-- name but different content therefore stay distinguishable.
--
-- file_unique_id is Telegram's stable identity of a file, so the same file sent
-- twice can never produce a second row. A partial unique index leaves the
-- historical NULLs of older rows alone.

alter table public.files add column if not exists content_hash text;

-- Older rows may predate the duplicate rule: drop the extra copies first so the
-- index can be created (the first stored copy is kept).
delete from public.files f
 using public.files keep
 where f.file_unique_id is not null
   and f.file_unique_id = keep.file_unique_id
   and f.id > keep.id;

create unique index if not exists files_unique_file_id_idx
    on public.files (file_unique_id)
    where file_unique_id is not null;

create index if not exists files_content_hash_idx
    on public.files (content_hash)
    where content_hash is not null;
