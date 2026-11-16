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

## Architecture

- `src/core/` — settings and logging.
- `src/db/` — PostgREST client and row models.
- `src/services/` — composition root, Telegram storage backend, file operations.
- `src/bot/` — dispatcher, middleware, keyboards, views, states and routers.
- `src/web/` — FastAPI keep-alive app.
- `src/utils/` — classification, formatting, sorting and Telegram helpers.

## Single-screen UX

The bot keeps one persistent message per chat. Commands, the library and the
search flow render that same message with `edit_message_text` through
`views.set_screen` / `views.set_screen_cb`, so navigation never floods the chat.
The only extra messages are the upload status bubble and copies explicitly
requested with the «إرسال» button.

Callback data is typed with `CallbackData` factories in `src/bot/keyboards.py`
(`MenuCB`, `CategoryCB`, `FileCB`, `SearchCB`, `FolderCB`, `JoinCB`, `SortCB`), so
routers filter and unpack it instead of parsing raw strings.

## Sorting

Inside a library category or a folder, «🔃 فرز / ترتيب» opens a panel with size,
date and name orderings in both directions (the active one is marked ✅). The
choice is kept in the screen key (`s` / `d`), so it survives opening a file and
coming back, and the list is re-rendered with `edit_message_text`. Names are
compared without Arabic diacritics and with the alef/ya variants folded, so
Arabic and Latin names sort as one alphabet.

## Files, duplicates and hashes

Every incoming file is copied into its category group and recorded in Supabase.
`file_unique_id` is unique in the database, so sending the same Telegram file
twice shows «الملف موجود أصلاً» and stores no second copy. Files up to
`HASH_MAX_BYTES` also get a SHA-256 `content_hash`, shown on the file screen, so
two files with the same name but different content stay distinguishable.

The «🩺 الحالة العامة» screen (and the startup report sent to `OWNER_ID`) reaches
every configured group, including the fallback, and lists the ids the bot cannot
use yet.

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
- Add a UptimeRobot HTTP(s) monitor pointing at `https://<service>.onrender.com/health`
  with a 5-minute interval to keep the instance awake.

The HTTP endpoints never touch Supabase or Telegram, so a ping cannot fail
because of a database hiccup.

## Optional environment variables

- `TG_SPACE_BOT_HOST` — bind address (default `0.0.0.0`).
- `TG_SPACE_BOT_PORT` — HTTP port when `PORT` is not set (default `8000`).
- `TG_SPACE_BOT_WEBHOOK_URL` — reserved for a future webhook mode.
- `ALLOWED_USER_IDS` — comma-separated Telegram user ids allowed to use the bot
  (leave empty to allow everybody).
- `PAGE_SIZE` — search results per page (default `10`, capped at `20`).
- `TG_STORAGE_CHAT_FALLBACK` — one group that receives every category without
  its own `TG_STORAGE_CHAT_*` id, instead of refusing the upload (for example the
  group named `TG-Storage-Chat-Fallback`). It is also listed in the
  «🔗 انضمام للمجموعات» gate, with the same one-time invite and automatic admin
  promotion as the per-category groups.
- `HASH_MAX_BYTES` — largest file (in bytes) that gets a SHA-256 content hash
  (default `20971520`, i.e. 20 MB). Telegram does not let a bot download more than
  20 MB, so raising it has no effect; larger files are stored without a hash.
- `LOG_LEVEL` — logging level (default `INFO`).
- `REQUEST_TIMEOUT` — Supabase request timeout in seconds (default `30`).

Storage group variables use the **singular** category name —
`TG_STORAGE_CHAT_DOCUMENT`, `TG_STORAGE_CHAT_IMAGE`, `TG_STORAGE_CHAT_VIDEO`,
`TG_STORAGE_CHAT_AUDIO`, `TG_STORAGE_CHAT_ARCHIVE`, `TG_STORAGE_CHAT_OTHER`
(one group may serve several categories). A category with no group of its own is
stored in `TG_STORAGE_CHAT_FALLBACK` when it is set; without a fallback,
uploads of that category are refused.

`SUPABASE_SERVICE_KEY` accepts both key formats: the legacy `service_role` JWT
and the new `sb_secret_...` secret key. The new keys are sent on the `apikey`
header only, because PostgREST rejects non-JWT values on `Authorization`.
