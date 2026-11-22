-- =============================================================================
-- TG Space Bot :: a saved link has a display name, and a bot is marked as one
-- Idempotent: safe to execute multiple times.
-- =============================================================================
-- 014 stored every link in the files table with ``link_type`` / ``link_url``.
-- The screen showed the address itself, because that was the only name a link
-- had. These two columns give it a readable name:
--
--   link_name   the name shown in the list: typed by the user («موقع جوجل»),
--               read from Telegram for a channel/group/bot, or derived from the
--               domain of a global link. NULL means «use the fallback», which is
--               how every row saved before this migration keeps rendering.
--   link_is_bot TRUE only for a Telegram bot account, so the list can tell it
--               apart from a channel or a group («Helper | Bot»).
--
-- ``file_name`` keeps holding the link itself for a link row (the URL is the
-- identity), and stays untouched for a file, so no existing query changes.

alter table public.files
    add column if not exists link_name text;

alter table public.files
    add column if not exists link_is_bot boolean not null default false;

-- A name belongs to a link, never to a file, and it is a short label.
alter table public.files
    drop constraint if exists files_link_name_check;
alter table public.files
    add constraint files_link_name_check
    check (
        link_name is null
        or (
            link_type is not null
            and length(btrim(link_name)) between 1 and 120
        )
    );

-- Only a Telegram link can be a bot: a global link is never one.
alter table public.files
    drop constraint if exists files_link_bot_check;
alter table public.files
    add constraint files_link_bot_check
    check (link_is_bot = false or link_type = 'telegram');

comment on column public.files.link_name is
    'The name shown for a saved link (custom, read from Telegram, or the domain); NULL for a file or an unnamed link.';
comment on column public.files.link_is_bot is
    'TRUE only for a saved Telegram link that is a bot account (never for a file or a global link).';
