-- =============================================================================
-- TG Space Bot :: the folder list and its totals in one read
-- Idempotent: safe to execute multiple times.
-- =============================================================================
-- The folders screen used to ask PostgREST twice on every render: once for
-- ``folders`` and once for ``folder_totals``. This view joins the two, so one
-- request returns every folder with its own file count and byte total.
--
-- The security model is the same as 011: the view checks the caller's own rights
-- (``security_invoker``) instead of its owner's, and only the bot's service role
-- may read it, so neither ``anon`` nor ``authenticated`` can list the folders of
-- any user.

create or replace view public.folders_overview as
select folder.id,
       folder.user_id,
       folder.name,
       folder.is_favorite,
       folder.favorite_order,
       coalesce(totals.files, 0)::bigint as files,
       coalesce(totals.bytes, 0)::bigint as bytes
from public.folders as folder
left join public.folder_totals as totals on totals.folder_id = folder.id;

alter view public.folders_overview set (security_invoker = true);

revoke all privileges on public.folders_overview from public, anon, authenticated;
grant select on public.folders_overview to service_role;

comment on view public.folders_overview is
    'Every virtual folder with its file count and byte total (one row per folder).';
