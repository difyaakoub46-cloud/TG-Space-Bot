-- =============================================================================
-- TG Space Bot :: the totals views and the date-picker function are not public
-- Idempotent: safe to execute multiple times.
-- =============================================================================
-- The bot reaches PostgREST with the service key, so neither of these objects
-- has to be readable by the public roles. A view runs with its owner's rights by
-- default, which let anyone holding the anon key read every row behind
-- ``files_by_category`` and ``folder_totals``; ``security_invoker`` makes the
-- view check the caller's own rights instead, and the revokes take those rights
-- away.

alter view public.files_by_category set (security_invoker = true);
alter view public.folder_totals    set (security_invoker = true);

revoke all privileges on public.files_by_category from anon, authenticated;
revoke all privileges on public.folder_totals    from anon, authenticated;

grant select on public.files_by_category to service_role;
grant select on public.folder_totals    to service_role;

-- A new function is executable by PUBLIC unless that grant is removed, and anon
-- inherits PUBLIC, so revoking only from anon would leave the function callable.
revoke execute on function public.files_periods(text, text, text, integer, integer)
    from public, anon;

grant execute on function public.files_periods(text, text, text, integer, integer)
    to authenticated, service_role;
