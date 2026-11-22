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
from src.bot.keyboards import (
    LINKS_TEXTS,
    LinkCB,
    LinkNameCB,
    MenuCB,
    main_menu_kb,
)
from src.bot.states import LinkStates
from src.core.i18n import pick
from src.db.models import StoredFile
from src.services.container import Services
from src.services.files import LinkSaveReport
from src.utils.formatters import escape_text
from src.utils.links import GLOBAL, TELEGRAM, Link, extract_links
from src.utils.telegram import safe_delete_messages

router = Router(name="links")

# A link's name is as short as a file's: one line, never an essay.
LINK_NAME_MAX = 120


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


def _display_name(record) -> str:
    """The name a link is shown by: the saved one, else its address."""
    return (
        getattr(record, "link_display_name", None)
        or record.link_url
        or record.file_name
    )


def saved_text(report: LinkSaveReport) -> str:
    """What the message with the links produced, link by link."""
    counts = [
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
    if report.rejected:
        counts.append(
            pick(
                f"🚫 {len(report.rejected)} not a bot",
                f"🚫 {len(report.rejected)} ليس بوتاً",
            )
        )
    lines = [
        pick("🔗 <b>Links</b>", "🔗 <b>الروابط</b>"),
        " · ".join(counts),
        "",
    ]
    for record in report.saved:
        lines.append(
            f"✅ {escape_text(_display_name(record))} — "
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
    for rejected in report.rejected:
        handle = f"@{rejected.link.handle or ''}"
        reason = (
            pick(
                "could not confirm this is a bot, so nothing was saved",
                "تعذّر التأكد أن هذا الحساب بوت، فلم يُحفظ شيء",
            )
            if rejected.unconfirmed
            else pick(
                "this account is not a bot, so nothing was saved",
                "هذا الحساب ليس بوتاً، فلم يُحفظ شيء",
            )
        )
        lines.append(f"🚫 {escape_text(handle)} — {reason}")
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
# Naming the link that was just saved
# --------------------------------------------------------------------------- #
def _name_question() -> str:
    """The one question the screen asks about a fresh global link."""
    return pick(
        "❓ Give this link a name? Send it, or press ⏭️ Skip.",
        "❓ هل تريد إعطاء هذا الرابط اسماً؟ أرسل الاسم أو اضغط ⏭️ تخطي.",
    )


def _askable(links: list[Link], report: LinkSaveReport) -> StoredFile | None:
    """The one link worth asking a name for, if the message has exactly one.

    Telegram links bring their own name, a repeat is already on record, and a
    message with several links has no single name to ask about, so none of them
    opens the question: it is reserved for one freshly saved global link.
    """
    if len(links) != 1 or links[0].link_type != GLOBAL:
        return None
    if len(report.saved) != 1:
        return None
    if report.duplicates or report.failures or report.rejected:
        return None
    return report.saved[0]


async def _screen_of(state: FSMContext, user_id: int) -> dict[str, object]:
    """The links screen the question was drawn on, without the question."""
    stored = await state.get_data()
    screen = dict(
        stored.get("link_screen")
        or file_list.new_screen("links", TELEGRAM, user_id)
    )
    screen.pop("ask", None)
    return screen


async def _end_question(state: FSMContext) -> None:
    """Forget the question: the link keeps the name it already has."""
    await state.set_state(None)
    await state.update_data(link_id=None, link_screen=None)


@router.message(LinkStates.waiting_name)
async def link_name_message(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    """The typed answer: the link takes it as its name, links included.

    This handler is registered before the links filter, so a name that looks like
    an address is stored as the name and never saved as a second link.
    """
    name = " ".join((message.text or "").split())
    stored = await state.get_data()
    link_id = str(stored.get("link_id") or "")
    user_id = int(message.from_user.id) if message.from_user else 0

    if not name or len(name) > LINK_NAME_MAX:
        await views.remember(state, message.message_id)
        await message.answer(
            pick(
                "⚠️ Send a name between 1 and 120 characters.",
                "⚠️ أرسل اسماً بين 1 و120 حرفاً.",
            )
        )
        return

    updated = await services.files.set_link_name(link_id, name) if link_id else None
    if updated is None:
        await views.remember(state, message.message_id)
        await message.answer(
            pick(
                "⚠️ Could not update the name, please try again.",
                "⚠️ تعذّر تحديث الاسم، حاول مرة أخرى.",
            )
        )
        return

    screen = await _screen_of(state, user_id)
    await _end_question(state)
    # The answer has been consumed: the chat keeps the screen, not the text.
    await views.delete_screen_message(bot, message.chat.id, message.message_id)
    await navigation.forget(state, message.message_id)

    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        screen,
        notice=pick(
            f"✅ Saved as «{escape_text(updated.link_name or name)}».",
            f"✅ تم الحفظ باسم «{escape_text(updated.link_name or name)}».",
        ),
    )


@router.callback_query(LinkNameCB.filter(F.action.in_({"skip", "cancel"})))
async def link_name_skip(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """«⏭️ Skip» and «❌ Cancel»: the derived name stays, the question goes.

    The question is drawn on the persistent screen, so it is removed by redrawing
    that screen without it — deleting the message would leave the chat without
    the list the link was saved into.
    """
    screen = await _screen_of(state, int(callback.from_user.id))
    await _end_question(state)
    await navigation.navigate_callback(
        callback, state, bot, services, screen, push=False
    )


# --------------------------------------------------------------------------- #
# A link's own screen: open it, rename it, delete it
# --------------------------------------------------------------------------- #
@router.callback_query(LinkCB.filter(F.action == "open"))
async def link_open_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """«✏️ Edit mode» turned the button into this: the link's actions, not the link."""
    data = LinkCB.unpack(callback.data)
    record = await services.files.get(data.link_id)
    if record is None:
        await views.ack(
            callback,
            pick("The link no longer exists.", "الرابط غير موجود."),
            show_alert=True,
        )
        return
    stored = dict((await state.get_data()).get("screen_key") or {})
    # The list is remembered so a deletion can return to the same page, and it is
    # pushed once: coming back from the confirmation keeps the actions in place.
    if stored.get("k") == "list":
        await state.update_data(link_list_screen=stored)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "link", "id": record.id},
        push=stored.get("k") != "link_delete",
        record=record,
    )


@router.callback_query(LinkCB.filter(F.action == "rename"))
async def link_rename_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Ask for the new name; the next text message is the answer."""
    data = LinkCB.unpack(callback.data)
    record = await services.files.get(data.link_id)
    if record is None:
        await views.ack(
            callback,
            pick("The link no longer exists.", "الرابط غير موجود."),
            show_alert=True,
        )
        return
    await state.set_state(LinkStates.renaming)
    await state.update_data(link_rename_id=record.id)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "link", "id": record.id},
        notice=views.link_rename_text(record),
        push=False,
        record=record,
    )


@router.message(LinkStates.renaming)
async def link_rename_message(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    """The typed answer: the link takes it as its name, keeping its bot badge.

    This handler is registered before the links filter, so a name that looks like
    an address is stored as the name and never saved as a second link.
    """
    name = " ".join((message.text or "").split())
    stored = await state.get_data()
    link_id = str(stored.get("link_rename_id") or "")
    user_id = int(message.from_user.id) if message.from_user else 0

    if not name or len(name) > LINK_NAME_MAX:
        await views.remember(state, message.message_id)
        await message.answer(
            pick(
                "⚠️ Send a name between 1 and 120 characters.",
                "⚠️ أرسل اسماً بين 1 و120 حرفاً.",
            )
        )
        return

    record = await services.files.get(link_id) if link_id else None
    updated = (
        await services.files.set_link_name(
            link_id, name, bool(record and record.link_is_bot)
        )
        if link_id
        else None
    )
    if updated is None:
        await views.remember(state, message.message_id)
        await message.answer(
            pick(
                "⚠️ Could not update the name, please try again.",
                "⚠️ تعذّر تحديث الاسم، حاول مرة أخرى.",
            )
        )
        return

    await state.set_state(None)
    await state.update_data(link_rename_id=None)
    # The answer has been consumed: the chat keeps the screen, not the text.
    await views.delete_screen_message(bot, message.chat.id, message.message_id)
    await navigation.forget(state, message.message_id)

    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        {"k": "link", "id": link_id},
        notice=pick(
            f"✅ Saved as «{escape_text(updated.link_name or name)}».",
            f"✅ تم الحفظ باسم «{escape_text(updated.link_name or name)}».",
        ),
    )


@router.callback_query(LinkCB.filter(F.action == "delete"))
async def link_delete_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Ask before deleting the row and the copy in the links group."""
    data = LinkCB.unpack(callback.data)
    record = await services.files.get(data.link_id)
    if record is None:
        await views.ack(
            callback,
            pick("The link no longer exists.", "الرابط غير موجود."),
            show_alert=True,
        )
        return
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "link_delete", "id": record.id},
        push=False,
        record=record,
    )


@router.callback_query(LinkCB.filter(F.action == "confirm"))
async def link_delete_confirm_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Delete the row and its Telegram copy, then redraw the list it came from."""
    data = LinkCB.unpack(callback.data)
    deleted = await services.files.delete(data.link_id)
    if not deleted:
        await views.ack(
            callback,
            pick("Could not delete the link.", "تعذّر حذف الرابط."),
            show_alert=True,
        )
        return
    stored = await state.get_data()
    target = dict(
        stored.get("link_list_screen")
        or file_list.new_screen("links", TELEGRAM, int(callback.from_user.id))
    )
    await state.update_data(link_list_screen=None)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        target,
        notice=pick(
            "✅ The link was removed from the list and from the links group.",
            "✅ تم حذف الرابط من القائمة ومن مجموعة الروابط.",
        ),
        push=False,
    )


# --------------------------------------------------------------------------- #
# Saving the links of a message
# --------------------------------------------------------------------------- #
@router.message(F.chat.type == ChatType.PRIVATE, HasLinks())
async def save_links_message(
    message: Message, services: Services, state: FSMContext, bot
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

    await views.reset_flow(state)
    # Track the incoming message too, so a copy that survives any failure is
    # removed the next time the main menu is opened (the same as an upload).
    await views.remember(state, message.message_id)

    report = await services.files.save_links(message, links)

    # The message did its job once its link is stored (or already on record), so
    # it is removed exactly like an uploaded file's message and the chat stays
    # clean. A failed link keeps its message, so the text can still be read and
    # sent again.
    kept_message = bool(report.failures or report.rejected)
    if not kept_message:
        await safe_delete_messages(bot, message.chat.id, [message.message_id])

    user_id = int(message.from_user.id) if message.from_user else 0
    show_saved = services.preferences.show_saved_screen(user_id)
    if not kept_message and not show_saved:
        return

    # The result is drawn on the single persistent screen, never as a new message
    # with its own main-menu keyboard: that would leave one more menu box behind
    # on every link and fill the chat with duplicates.
    screen = file_list.new_screen("links", TELEGRAM, user_id)
    notice = saved_text(report)
    target = _askable(links, report) if show_saved else None
    if target is not None:
        # One fresh global link: the screen asks for its name, and the state waits
        # for the answer the question's own buttons or the text can give.
        screen["ask"] = target.id
        notice = f"{notice}\n\n{_name_question()}"
        await state.set_state(LinkStates.waiting_name)
        await state.update_data(link_id=target.id, link_screen=screen)

    await navigation.navigate_message(
        message,
        state,
        bot,
        services,
        screen,
        notice=notice,
    )
