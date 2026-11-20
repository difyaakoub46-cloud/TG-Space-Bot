"""Link ingestion: every link in a message becomes a row and a group copy.

The media router only sees documents, photos and the like, and the search router
only sees a typed query while it waits for one, so a link used to fall through to
«🤔 I did not understand that message». This router sits between the two: it
matches a text message only when a link is actually in it, copies the message
into the links group once per link, and records each link with its section
(Telegram or Global).

The order of the routers matters. It is registered before «🔍 Search», so a link
pasted on the search screen is saved instead of being searched for.
"""

from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from src.bot import file_list, navigation, views
from src.bot.keyboards import LINKS_TEXTS, MenuCB, main_menu_kb
from src.core.i18n import pick
from src.services.container import Services
from src.services.files import LinkSaveReport
from src.utils.formatters import escape_text
from src.utils.links import TELEGRAM, extract_links

router = Router(name="links")


class HasLinks(Filter):
    """A text message that carries at least one link.

    It is a filter, not a check inside the handler: a handler that answered every
    text message would swallow the screens that expect one (a search query, a
    folder name).
    """

    async def __call__(self, message: Message) -> bool:
        return bool(extract_links(message.text or ""))


def _reason(exc: str) -> str:
    """One short, escaped line saying why a link was not saved."""
    return html.escape(" ".join(str(exc).split())[:200] or "error")


def saved_text(report: LinkSaveReport) -> str:
    """What the message with the links produced, link by link."""
    lines = [
        pick("🔗 <b>Links</b>", "🔗 <b>الروابط</b>"),
        " · ".join(
            [
                pick(
                    f"✅ {len(report.saved)} saved",
                    f"✅ {len(report.saved)} محفوظ",
                ),
                pick(
                    f"♻️ {len(report.duplicates)} already there",
                    f"♻️ {len(report.duplicates)} موجود أصلاً",
                ),
                pick(
                    f"❌ {len(report.failures)} failed",
                    f"❌ {len(report.failures)} فشل",
                ),
            ]
        ),
        "",
    ]
    for record in report.saved:
        lines.append(
            f"✅ {escape_text(record.link_url or '')} — "
            + (
                pick("Telegram", "تيليغرام")
                if record.link_type == "telegram"
                else pick("Global", "خارجي")
            )
        )
    for record in report.duplicates:
        lines.append(
            f"♻️ {escape_text(record.link_url or '')} — "
            + pick("already in the list", "موجود في القائمة أصلاً")
        )
    for url, reason in report.failures:
        lines.append(f"❌ {escape_text(url)} — {_reason(reason)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The links screen
# --------------------------------------------------------------------------- #
@router.message(Command("links"))
@router.message(F.text.in_(LINKS_TEXTS))
async def cmd_links(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    """Open the links list, on the Telegram section."""
    user_id = int(message.from_user.id) if message.from_user else 0
    await views.reset_flow(state)
    await views.remember(state, message.message_id)
    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        file_list.new_screen("links", TELEGRAM, user_id),
    )


@router.callback_query(MenuCB.filter(F.action == "links"))
async def menu_links_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """«🔗 Links» from the main menu: Telegram Links first, as requested."""
    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        file_list.new_screen("links", TELEGRAM, int(callback.from_user.id)),
    )


# --------------------------------------------------------------------------- #
# Saving the links of a message
# --------------------------------------------------------------------------- #
@router.message(F.chat.type == ChatType.PRIVATE, HasLinks())
async def save_links_message(
    message: Message, services: Services, state: FSMContext
) -> None:
    """Save every link of the message, then report the result."""
    links = extract_links(message.text or "")
    if not links:
        return

    if services.settings.storage_chat_links is None:
        sent = await message.answer(
            pick(
                "⚠️ No links group is configured yet, so nothing could be saved. "
                "Set TG_STORAGE_CHAT_LINKS first.",
                "⚠️ لا توجد مجموعة روابط مهيّأة بعد، فلم يُحفظ شيء. "
                "اضبط TG_STORAGE_CHAT_LINKS أولاً.",
            ),
            reply_markup=main_menu_kb(),
        )
        await views.remember(state, sent.message_id)
        return

    report = await services.files.save_links(message, links)
    sent = await message.answer(
        saved_text(report),
        reply_markup=main_menu_kb(),
        disable_web_page_preview=True,
    )
    # The summary is part of the screen the main menu wipes, like every other
    # answer the bot leaves in the chat.
    await views.remember(state, sent.message_id)
