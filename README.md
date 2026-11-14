# TG-Space-Bot

Telegram bot that saves incoming files into the right Telegram storage group (document / image / video / audio / archive / other) and lets the user browse, search and manage the saved library.

## Stack

- Python 3.12
- aiogram 3.x (async)
- Supabase (PostgreSQL + PostgREST)

## Local setup

1. Copy `.env.example` to `.env` and fill in the values.
2. Install dependencies: `pip install -r requirements.txt`.
3. Run migrations against your Supabase project (see `migrations/`).
4. Start the bot: `python main.py`.

## Architecture

- `src/core/` — settings and logging.
- `src/db/` — PostgREST client and row models.
- `src/services/` — storage routing and file operations.
- `src/bot/` — dispatcher, middlewares, keyboards, views and routers.
- `src/utils/` — file-type classification and formatting helpers.

## Single-screen UX

The bot keeps one persistent interactive message per screen. Commands, browse and search flows render that same message via edits instead of sending new messages. Callback handlers use `callback.message.edit_text` and `await callback.answer()` to clear the loading state. The upload flow is the only path that intentionally sends a separate status message during file copy.
