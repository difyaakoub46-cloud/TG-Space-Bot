-- =============================================================================
-- TG Space Bot :: counts and byte totals per category and per folder
-- Idempotent: safe to execute multiple times.
-- =============================================================================
-- PostgREST aggregates are disabled on this project (PGRST123), so the sums are
-- exposed as plain views: one cheap row per category / folder instead of summing
-- the whole table in the bot.

create or replace view public.files_by_category as
select category,
       count(*)::bigint                          as files,
       coalesce(sum(file_size), 0)::bigint       as bytes
from public.files
group by category;

create or replace view public.folder_totals as
select ff.folder_id,
       count(*)::bigint                          as files,
       coalesce(sum(f.file_size), 0)::bigint     as bytes
from public.folder_files ff
join public.files f on f.id = ff.file_id
group by ff.folder_id;

grant select on public.files_by_category to anon, authenticated, service_role;
grant select on public.folder_totals    to anon, authenticated, service_role;

comment on view public.files_by_category is
    'Files and bytes per category (one row per category).';
comment on view public.folder_totals is
    'Files and bytes per virtual folder.';
