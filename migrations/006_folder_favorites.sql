-- =============================================================================
-- TG Space Bot :: favourite virtual folders
-- Idempotent: safe to execute multiple times.
-- =============================================================================
-- A favourite folder is only a shortcut on the library screen: it keeps its
-- place in the full, alphabetically sorted folder list as well, so nothing is
-- duplicated and no rename or reordering happens.

alter table public.folders
    add column if not exists is_favorite boolean not null default false;

-- The library screen reads the favourites of one user on every render.
create index if not exists folders_favorites_idx
    on public.folders (user_id)
    where is_favorite;

comment on column public.folders.is_favorite is
    'Shows the folder as a one-tap shortcut on the library screen.';
