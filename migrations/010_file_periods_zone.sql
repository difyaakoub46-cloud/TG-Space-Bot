-- =============================================================================
-- TG Space Bot :: periods in the user's timezone, for the date picker
-- Idempotent: safe to execute multiple times.
-- =============================================================================
-- The file list builds its day/week windows from the local midnights, so the
-- picker has to group the same way: a file sent at 00:30 belongs to the local day,
-- not to the UTC day that was still running. ``p_offset`` is the offset of that
-- zone in minutes east of UTC (+180 for a zone three hours ahead), which is what
-- ``AT TIME ZONE`` takes as an interval.

drop function if exists public.files_periods(text, text, text, integer);

create or replace function public.files_periods(
    p_span     text    default 'day',
    p_query    text    default null,
    p_category text    default null,
    p_limit    integer default 24,
    p_offset   integer default 0
)
returns table (anchor date, files bigint)
language sql
stable
as $$
    select case p_span
               when 'week'  then date_trunc('week',  moment)
               when 'month' then date_trunc('month', moment)
               when 'year'  then date_trunc('year',  moment)
               else              date_trunc('day',   moment)
           end::date              as anchor,
           count(*)::bigint       as files
    from (
        select created_at at time zone make_interval(mins => coalesce(p_offset, 0))
                   as moment
        from public.files
        where (p_category is null or category = p_category)
          and (p_query is null or file_name ilike '%' || p_query || '%')
    ) as local_files
    group by 1
    order by 1 desc
    limit coalesce(nullif(p_limit, 0), 24);
$$;

grant execute on function public.files_periods(text, text, text, integer, integer)
    to anon, authenticated, service_role;

comment on function public.files_periods(text, text, text, integer, integer) is
    'Periods that hold files, with their file count, newest first, grouped in the offset of the caller (the date picker).';
