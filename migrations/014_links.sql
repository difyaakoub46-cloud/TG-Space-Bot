-- =============================================================================
-- TG Space Bot :: links live in the same table as the files
-- Idempotent: safe to execute multiple times.
-- =============================================================================
-- A link is not a file, but it is stored and listed like one: a row per link,
-- with its own copy in the links group. ``link_type`` is the one column that
-- tells them apart, so ``NULL`` keeps meaning «a file» and every existing query,
-- every count and the category check keep working untouched:
--
--   link_type is null      -> a file (the six library categories)
--   link_type = 'telegram' -> a Telegram link  (t.me / tg://)
--   link_type = 'global'   -> any other link   (http/https)
--
-- The category of a link row stays 'other' and its file_size stays 0, so the
-- existing constraints hold without a second table.

alter table public.files
    add column if not exists link_type text;

alter table public.files
    add column if not exists link_url text;

-- The two lists of the screen, and nothing else.
alter table public.files
    drop constraint if exists files_link_type_check;
alter table public.files
    add constraint files_link_type_check
    check (link_type is null or link_type in ('telegram', 'global'));

-- A row is either a file (no link columns) or a link (both of them, with a URL).
alter table public.files
    drop constraint if exists files_link_shape_check;
alter table public.files
    add constraint files_link_shape_check
    check (
        (link_type is null and link_url is null)
        or (
            link_type is not null
            and link_url is not null
            and length(btrim(link_url)) > 0
        )
    );

-- One row per link, whatever message carried it: sending the same link twice is
-- answered with «موجود أصلاً» instead of making a second copy in the group.
create unique index if not exists files_link_url_key
    on public.files (link_url)
    where link_type is not null;

-- The links screen reads one section at a time, newest first.
create index if not exists files_link_type_created_at_idx
    on public.files (link_type, created_at desc)
    where link_type is not null;

comment on column public.files.link_type is
    'NULL for a file; telegram or global for a saved link.';
comment on column public.files.link_url is
    'The link itself, unique per stored link (NULL for a file).';

-- The library totals count files: a link is not a category of the library, so
-- «Other» and the storage counters keep describing the files alone. The security
-- model is the one set in 011 (the caller's own rights, service role only).
create or replace view public.files_by_category as
select category,
       count(*)::bigint                    as files,
       coalesce(sum(file_size), 0)::bigint as bytes
from public.files
where link_type is null
group by category;

alter view public.files_by_category set (security_invoker = true);
revoke all privileges on public.files_by_category from anon, authenticated;
grant select on public.files_by_category to service_role;

-- A folder holds files; a link is neither counted nor sized there.
create or replace view public.folder_totals as
select ff.folder_id,
       count(*)::bigint                      as files,
       coalesce(sum(f.file_size), 0)::bigint as bytes
from public.folder_files ff
join public.files f on f.id = ff.file_id
where f.link_type is null
group by ff.folder_id;

alter view public.folder_totals set (security_invoker = true);
revoke all privileges on public.folder_totals from anon, authenticated;
grant select on public.folder_totals to service_role;
