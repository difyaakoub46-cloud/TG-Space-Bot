-- =============================================================================
-- TG Space Bot :: reorder the favourites in one call
-- Idempotent: safe to execute multiple times.
-- =============================================================================
-- Moving one favourite used to send one PATCH per favourite (N round trips for N
-- folders). The bot already knows the new order, so it now sends the whole list
-- once and this function writes every position in a single statement.
--
-- Only the favourites of the given user are touched: a wrong user id cannot move
-- somebody else's folders. The function runs with the caller's rights
-- (``security invoker``) and only the bot's service role may execute it.

create or replace function public.set_folder_orders(
    p_user_id     bigint,
    p_folder_ids  bigint[],
    p_orders      integer[]
) returns void
language sql
security invoker
as $$
    update public.folders as folder
    set favorite_order = new_order.position
    from unnest(p_folder_ids, p_orders) as new_order(id, position)
    where folder.id = new_order.id
      and folder.user_id = p_user_id
      and folder.is_favorite;
$$;

revoke execute on function public.set_folder_orders(bigint, bigint[], integer[])
    from public, anon, authenticated;

grant execute on function public.set_folder_orders(bigint, bigint[], integer[])
    to service_role;

comment on function public.set_folder_orders(bigint, bigint[], integer[]) is
    'Writes the manual position of every favourite folder of one user at once.';
