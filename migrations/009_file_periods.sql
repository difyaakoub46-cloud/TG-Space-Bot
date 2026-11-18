-- =============================================================================
-- TG Space Bot :: periods and their file counts, for the date picker
-- Idempotent: safe to execute multiple times.
-- =============================================================================
-- PostgREST aggregates are disabled on this project (PGRST123), so the grouping
-- lives in a SQL function: one request returns one row per day — week, month or
-- year when the picker asks for one — with the number of files it holds, instead
-- of the bot reading every matching row and counting them itself.
--
--     select date(created_at), count(*)
--     from files group by date(created_at) order by 1 desc;

create or replace function public.files_periods(
    p_span     text    default 'day',
    p_query    text    default null,
    p_category text    default null,
    p_limit    integer default 24
)
returns table (anchor date, files bigint)
language sql
stable
as $$
    select case p_span
               when 'week'  then date_trunc('week',  created_at at time zone 'utc')
               when 'month' then date_trunc('month', created_at at time zone 'utc')
               when 'year'  then date_trunc('year',  created_at at time zone 'utc')
               else              date_trunc('day',   created_at at time zone 'utc')
           end::date              as anchor,
           count(*)::bigint       as files
    from public.files
    where (p_category is null or category = p_category)
      and (p_query is null or file_name ilike '%' || p_query || '%')
    group by 1
    order by 1 desc
    limit coalesce(nullif(p_limit, 0), 24);
$$;

grant execute on function public.files_periods(text, text, text, integer)
    to anon, authenticated, service_role;

comment on function public.files_periods(text, text, text, integer) is
    'Periods that hold files, with their file count, newest first (the date picker).';
