-- =============================================================================
-- TG Space Bot :: manual order of the favourite folders
-- Idempotent: safe to execute multiple times.
-- =============================================================================
-- The library screen lists the favourites by ``favorite_order`` (0 = first).
-- A folder without an order falls back to the alphabetical position, and the
-- existing favourites are normalised once so the list keeps its current look.

alter table public.folders
    add column if not exists favorite_order integer;

update public.folders as folder
set favorite_order = ranked.position - 1
from (
    select
        id,
        row_number() over (partition by user_id order by lower(name)) as position
    from public.folders
    where is_favorite and favorite_order is null
) as ranked
where folder.id = ranked.id;
