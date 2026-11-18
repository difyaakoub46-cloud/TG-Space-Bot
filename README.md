# TG-Space-Bot

Telegram bot that saves incoming files into the right Telegram storage group (document / image / video / audio / archive / other) and lets the user browse, search, send back and delete the saved library.

## Stack

- Python 3.12
- aiogram 3.x (async, long polling)
- Supabase (PostgreSQL + PostgREST)
- FastAPI + Uvicorn (keep-alive health server)

## Local setup

1. Copy `.env.example` to `.env` and fill in the values.
2. Install dependencies: `pip install -r requirements.txt`.
3. Run migrations: `SUPABASE_DATABASE_URL=postgresql://... python -m scripts.init_db`.
4. Create one Telegram group per category, add the bot as an admin and put the
   group chat id in `TG_STORAGE_CHAT_<CATEGORY>`.
5. Start the bot: `python main.py`.

## Group setup

Every storage group needs the bot as an **administrator**, with these seven rights
turned on in the group's administrator screen. «🩺 General status» →
«🛡 Group permissions» lists every group with the rights it is missing, and
«🔍 Re-check permissions» reads them again without a restart.

| Right (as Telegram names it) | What breaks when it is off |
| --- | --- |
| Invite users via link | No invite link and no join request can be created, so «🔗 Join the groups» fails and no join request ever arrives. |
| Ban users | An account that is not in `ALLOWED_USER_IDS` and gets in by another route cannot be removed; the bot reports the failure to `OWNER_ID` instead. The automatic promotion grants this right too, so that fails as well. |
| Delete messages | When Supabase refuses a registration, the fresh copy cannot be pulled back out of the library, so the group keeps a file the library has no row for. |
| Add new admins | An allowed account that joins is not promoted automatically and stays a plain member. |
| Change group info | The automatic promotion is refused as a whole: Telegram rejects a promotion that grants a right the bot does not hold itself. |
| Pin messages | Same: the automatic promotion is refused. |
| Manage video chats | Same: the automatic promotion is refused. |

`can_manage_chat` needs no separate switch — it comes with the administrator
status. A group that cannot be reached is reported as unchecked, never as missing
rights, so the screen only claims what it actually read.

### BotFather

In `@BotFather` → `/mybots` → the bot:

- **`/setjoingroups` → Enable** — required, otherwise the bot cannot be added to
  any storage group.
- **`/setprivacy`** — leave it as it is. The bot never reads group messages: files
  arrive in the private chat and are copied into a group by id. Disable privacy
  only if you later add handlers that need group messages.
- **`/setcommands`** — optional, publishes the command list in the menu button.

If a group's state looks stale after you change a right, remove the bot from the
group and add it back as an administrator; Telegram caches the membership.

### Invite links and the join guard

Two variables control the gate behind «🔗 Join the groups»:

- `JOIN_GUARD` — default `true`. The link creates a **join request**
  (`creates_join_request`) that the bot approves for an allowed id only, and any
  unlisted account that reaches a storage group by another route is removed. With
  `false` the link is a plain one-use link: no approval, no removal (only for
  disposable groups).
- `JOIN_LINK_TTL_MINUTES` — default `10`, kept between `1` and `1440`. Every link
  expires after that, and is revoked on its first use either way.

`creates_join_request` cannot be combined with `member_limit`, so with the guard on
the revoke-on-first-use replaces the one-use cap. Without «Invite users via link»
no link exists at all, and without «Ban users» the removal cannot happen. The full
flow is under [Storage-group security](#storage-group-security).

## Checks

```bash
pip install -r requirements-dev.txt
python -m scripts.verify_schema   # rebuilds the schema on a throwaway database
python -m pytest -q               # emergency hold and repair, plus the registry
```

`scripts.verify_schema` applies every migration to a brand-new database, then
checks the objects, the identity columns, that every foreign key column has the
type of the column it references, and runs an insert/update/delete flow through
the triggers and views. It never touches the configured database: it creates a
temporary one on `VERIFY_DATABASE_URL`, or on a private PostgreSQL started by
`pgserver` when that variable is unset.

## Architecture

- `src/core/` — settings, logging and the two-language core (`i18n.py`).
- `src/db/` — PostgREST client and row models.
- `src/services/` — composition root, Telegram storage backend, file operations,
  the emergency registry and the per-user preferences.
- `src/bot/` — dispatcher, middleware, keyboards, views, navigation, states and routers.
- `src/web/` — FastAPI keep-alive app.
- `src/utils/` — classification, formatting, sorting and Telegram helpers.

## Single-screen UX

The bot keeps one persistent message per chat. Commands, the library and the
search flow render that same message with `edit_message_text` through
`views.set_screen` / `views.set_screen_cb`, so navigation never floods the chat.
The only extra messages are the upload status bubble and copies explicitly
requested with the «Send» button.

Callback data is typed with `CallbackData` factories in `src/bot/keyboards.py`
(`MenuCB`, `CategoryCB`, `FileCB`, `SearchCB`, `FolderCB`, `JoinCB`, `SortCB`), so
routers filter and unpack it instead of parsing raw strings. Screens are rebuilt
from a small JSON key by `src/bot/navigation.py`, which is what lets «↩️ Back»
return to the previous screen (the trail lives in the FSM data).

## Menu

```
📚 Library                        (one wide button)
🔍 Search   |   ⚙️ Settings
🩺 General status                 (one wide button)

⚙️ Settings        → 🌐 Change language · 🔗 Join the groups · ❓ Help
🩺 General status  → 🔍 Check and update status · 📊 Statistics ·
                     🛡 Group permissions · 🛟 Reserve and pending files
```

The statistics, the group permissions and the reserve section are opened from the
status screen, so «Back» returns there instead of jumping to the main menu. The
status screen itself keeps one line about the rights (all, some, or none); the
per-group detail lives only in «🛡 Group permissions».

## Languages

Every screen exists in English and in Arabic, chosen from «⚙️ Settings» →
«🌐 Change language». English is the default, and the choice is stored per user in
`PREFERENCES_PATH` (a small JSON file written atomically, next to the emergency
registry), so a restart does not forget it.

Texts are written at the call site with `pick(english, arabic)` from
`src/core/i18n.py`: both versions sit next to each other, so a screen is never
half translated. The middleware installs the caller's language for the duration of
one update and resets it afterwards, and the typed main-menu buttons are matched
in either language.

## Sorting and paging

Inside a library category or a folder, «🔃 Sort» opens a panel with size,
date and name orderings in both directions (the active one is marked ✅). The
choice is kept in the screen key (`s` / `d`), so it survives opening a file and
coming back, and the list is re-rendered with `edit_message_text`.

A category is paged eight files at a time, and both the ordering and the slice are
done by PostgREST, so the sort covers every file in the category and the last file
of the library is reachable. The page lives in the screen key (`p`) next to the
ordering, so opening a file and going back returns to the same page; changing the
ordering restarts at page one because the pages are renumbered. Folder files,
search results and «History uploads» are paged at eight files too; a folder is
sorted in memory (names are compared without Arabic diacritics and with the
alef/ya variants folded) and then sliced, so its page turns keep the ordering.
Folder listings are short enough to be sorted in memory as well.

## Files, duplicates and hashes

Every incoming file is copied into its category group and recorded in Supabase.
`file_unique_id` is unique in the database, so sending the same Telegram file
twice shows «the file is already stored» and stores no second copy. Files up to
`HASH_MAX_BYTES` also get a SHA-256 `content_hash`, shown on the file screen, so
two files with the same name but different content stay distinguishable.

The «🩺 General status» screen (and the startup report sent to `OWNER_ID`) reaches
every configured group, including the emergency one, and lists the ids the bot
cannot use yet.

## Emergency hold and repair

`TG_STORAGE_CHAT_FALLBACK` is an **emergency store**, not a library group and not a
backup of the other groups. A file is never left in the library without a row in
Supabase:

- The row is written after the copy, so if Supabase refuses the registration the
  fresh copy is pulled back out of the library group.
- The file is then copied into `TG_STORAGE_CHAT_FALLBACK` and recorded in a small
  JSON registry (`PENDING_STORE_PATH`).
- The «🛟 Reserve and pending files» screen (inside «🩺 General status») shows how
  many files are waiting and how they split by type, and «🔧 Repair» (إصلاح)
  registers every one of them and copies it into its category group, deleting the
  held copy only after its row exists.

Telegram's Bot API cannot read a chat's history, so the registry — not the group —
is what makes the repair possible. Point `PENDING_STORE_PATH` at a mounted Render
disk to keep it across restarts; on an ephemeral path the files stay in the group
but the list is lost. A file whose category has no `TG_STORAGE_CHAT_*` group is
kept during repair until that group is configured, and a file already on record is
recognised and its held copy removed.

## Keep-alive server (Render + UptimeRobot)

`main.py` starts the bot in long polling and, in parallel, serves a small
FastAPI app on `0.0.0.0:$PORT` (default `8000`):

| Endpoint | Purpose |
| --- | --- |
| `/` | basic status payload |
| `/ping` | plain liveness check |
| `/health` | health probe with uptime and pid |
| `/docs` | interactive API documentation |

On Render:

- Service type: **Web Service** (needs an open HTTP port).
- Start command: `python main.py`.
- Do not set `PORT`; Render injects it and the app reads it.
- Add an UptimeRobot HTTP(s) monitor pointing at `https://<service>.onrender.com/health`
  with a 5-minute interval to keep the instance awake.

The HTTP endpoints never touch Supabase or Telegram, so a ping cannot fail
because of a database hiccup.

## Optional environment variables

- `TG_SPACE_BOT_HOST` — bind address (default `0.0.0.0`).
- `TG_SPACE_BOT_PORT` — HTTP port when `PORT` is not set (default `8000`).
- `TG_SPACE_BOT_WEBHOOK_URL` — reserved for a future webhook mode.
- `PREFERENCES_PATH` — where each user's language is kept (default
  `data/user_preferences.json`). Point it at a persistent disk so the choice
  survives a restart; a missing file simply means everybody speaks English.
- `ALLOWED_USER_IDS` — comma-separated Telegram user ids allowed to use the bot
  (leave empty to allow everybody).
- `PAGE_SIZE` — folders per page in the folder list (default `10`, capped at
  `20`). Category, folder, search and history file lists always show eight files
  per page.
- `TG_STORAGE_CHAT_FALLBACK` — the emergency group (for example the group named
  `TG-Storage-Chat-Fallback`): every file whose registration in Supabase fails is
  copied here instead of the library, and «🔧 Repair» moves it to its real group
  once the database answers again. It is also listed in the «🔗 Join the groups»
  gate, with the same approved join request and automatic admin promotion as the
  per-category groups.
- `JOIN_GUARD` — `true` (default) makes storage-group invite links produce a
  **join request** that the bot approves only for an allowed account, and removes
  any unlisted account that gets in by another route. Set it to `false` to keep
  plain one-use links with no approval step (not recommended).
- `JOIN_LINK_TTL_MINUTES` — how long an invite link stays valid, in minutes
  (default `10`, kept between `1` and `1440`).
- `PENDING_STORE_PATH` — where the list of held files is kept (default
  `data/pending_files.json`). Point it at a persistent disk so the list survives a
  restart; it holds names and ids only, never file bytes.
- `HASH_MAX_BYTES` — largest file (in bytes) that gets a SHA-256 content hash
  (default `20971520`, i.e. 20 MB). Telegram does not let a bot download more than
  20 MB, so raising it has no effect; larger files are stored without a hash.
- `LOG_LEVEL` — logging level (default `INFO`).
- `REQUEST_TIMEOUT` — Supabase request timeout in seconds (default `30`).

## Storage-group security

An invite link is a bearer secret: whoever holds it can join, and membership means
reading every file in that group. So the link alone is never the permission.

- «🔗 Join the groups» still checks the caller against `ALLOWED_USER_IDS` (plus
  `OWNER_ID`) before a link exists, but the link it creates is a **join request**
  (`creates_join_request`), not open entry.
- `chat_join_request` approves only an allowed id. Anybody else is declined and the
  link they used is revoked, so a link stolen from the owner's chat is dead after
  its first attempt. The owner is notified when `OWNER_ID` is set.
- `chat_member` promotes an allowed account to admin, and **removes** an unlisted
  account from a storage group (a kick: ban followed by an unban). This needs the
  bot to be an admin with the «Ban users» right; when the kick fails, the owner gets
  a message instead of silence.
- Every link expires (`JOIN_LINK_TTL_MINUTES`, default ten minutes) and is revoked
  as soon as it is used once.
- `JOIN_GUARD=false` restores the old behaviour: a plain one-use link, no approval,
  no removal. Use it only if the storage groups are disposable.

The bot cannot undo a read that already happened, so the remaining risk is the
account itself: protect Telegram with a passcode and two-step verification, and end
unknown active sessions.

Storage group variables use the **singular** category name —
`TG_STORAGE_CHAT_DOCUMENT`, `TG_STORAGE_CHAT_IMAGE`, `TG_STORAGE_CHAT_VIDEO`,
`TG_STORAGE_CHAT_AUDIO`, `TG_STORAGE_CHAT_ARCHIVE`, `TG_STORAGE_CHAT_OTHER`
(one group may serve several categories). A category with no group of its own is
reported as missing (the emergency group does not cover it), and uploads of that
category are refused with a message naming the variable to add.

`SUPABASE_SERVICE_KEY` accepts both key formats: the legacy `service_role` JWT
and the new `sb_secret_...` secret key. The new keys are sent on the `apikey`
header only, because PostgREST rejects non-JWT values on `Authorization`.
