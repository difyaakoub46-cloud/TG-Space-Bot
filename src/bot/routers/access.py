"""Owner-only gate: one-time invites to the storage groups, admin on join.

Two independent checks guard the gate: the Telegram id must be the owner id from
the environment, *and* the password must match. A failure in either check refuses
the request immediately.
"""

from __future__ import annotations

import hmac
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from src.bot import navigation, views
from src.bot.keyboards import JoinCB, MenuCB
from src.bot.states import JoinStates
from src.core.config import ConfigurationError
from src.services.container import Services
from src.utils.filetypes import parse_category
from src.utils.telegram import safe_delete_message

logger = logging.getLogger(__name__)

router = Router(name="access")

DENIED_TEXT = "⛔ هذا الأمر مقصور على المالك."
WRONG_PASSWORD_TEXT = "❌ كلمة السر غير صحيحة. تم رفض الطلب."
NO_PASSWORD_TEXT = "⚠️ ميزة الانضمام غير مهيّأة: أضف المتغير JOIN_PASSWORD في الخادم."

# The owner joins as a plain member, so the bot must hand out the rights itself.
# can_promote_members is deliberately left off: granting it needs the bot itself to
# hold it, and a single unavailable right would fail the whole promotion.
OWNER_RIGHTS: dict[str, bool] = {
    "can_manage_chat": True,
    "can_delete_messages": True,
    "can_invite_users": True,
    "can_pin_messages": True,
    "can_restrict_members": True,
    "can_change_info": True,
    "can_manage_video_chats": True,
}


def _is_owner(services: Services, user_id: int | None) -> bool:
    owner = services.settings.owner_id
    if owner is None or user_id is None:
        return False
    return int(user_id) == int(owner)


def _password(services: Services) -> str:
    """The configured gate password, or an empty string when it is not set."""
    secret = services.settings.join_password
    return secret.get_secret_value() if secret is not None else ""


def _reason(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text[:160] or type(exc).__name__


@router.callback_query(MenuCB.filter(F.action == "join"))
async def menu_join_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    if not _is_owner(services, callback.from_user.id):
        await callback.answer(DENIED_TEXT, show_alert=True)
        return
    if not _password(services):
        await callback.answer(NO_PASSWORD_TEXT, show_alert=True)
        return

    await views.reset_flow(state)
    await state.set_state(JoinStates.waiting_password)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "join_password"},
        push=False,
    )


@router.message(JoinStates.waiting_password)
async def join_password_message(
    message: Message, services: Services, state: FSMContext, bot
) -> None:
    # The password must not stay in the chat, whatever the outcome.
    await safe_delete_message(bot, message.chat.id, message.message_id)
    await state.set_state(None)

    if not _is_owner(services, message.from_user.id if message.from_user else None):
        await message.answer(DENIED_TEXT)
        return

    expected = _password(services)
    given = (message.text or "").strip()
    if not expected:
        await message.answer(NO_PASSWORD_TEXT)
        return
    if not given or not hmac.compare_digest(given, expected):
        logger.warning("Rejected a join attempt with a wrong password")
        await message.answer(WRONG_PASSWORD_TEXT)
        return

    await navigation.navigate_message(
        message, state, bot, services, {"k": "join_groups"}
    )


@router.callback_query(JoinCB.filter(F.action == "link"))
async def join_link_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    if not _is_owner(services, callback.from_user.id):
        await callback.answer(DENIED_TEXT, show_alert=True)
        return
    if not _password(services):
        await callback.answer(NO_PASSWORD_TEXT, show_alert=True)
        return

    data = JoinCB.unpack(callback.data)
    category = parse_category(data.value)
    try:
        chat_id = services.settings.storage_chat_for(category)
    except ConfigurationError:
        await callback.answer(
            "⚠️ لا توجد مجموعة تخزين مهيّأة لهذه الفئة.", show_alert=True
        )
        return

    try:
        link = await bot.create_chat_invite_link(
            chat_id=chat_id,
            member_limit=1,
            name=f"owner-{category.value}"[:32],
        )
    except TelegramAPIError as exc:
        logger.warning("Could not create an invite link for %s: %s", chat_id, exc)
        await callback.answer(
            f"⚠️ تعذّر إنشاء الرابط: {_reason(exc)}\n"
            "تأكد أن البوت مشرف في المجموعة مع صلاحية دعوة المستخدمين.",
            show_alert=True,
        )
        return

    logger.info("Created a one-time invite link for %s", category.value)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "join_groups"},
        notice=views.invite_text(
            link.invite_link, views.category_label(category)
        ),
        push=False,
    )


@router.chat_member()
async def on_chat_member(
    update: ChatMemberUpdated, services: Services, bot: Bot
) -> None:
    """Promote the owner the moment they join a storage group."""
    owner = services.settings.owner_id
    if owner is None:
        return
    if update.chat.id not in set(services.settings.storage_chats().values()):
        return
    if int(update.new_chat_member.user.id) != int(owner):
        return

    status = update.new_chat_member.status
    if status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        return

    try:
        promoted = await bot.promote_chat_member(
            chat_id=update.chat.id, user_id=owner, **OWNER_RIGHTS
        )
    except TelegramAPIError:
        logger.exception("Could not promote the owner in %s", update.chat.id)
        return

    if not promoted:
        logger.warning("Telegram refused to promote the owner in %s", update.chat.id)
        return

    logger.info("Promoted the owner in %s", update.chat.id)
    title = update.chat.title or str(update.chat.id)
    try:
        await bot.send_message(
            owner,
            f"✅ تمت ترقيتك إلى مشرف في مجموعة «{title}» تلقائياً.",
        )
    except TelegramAPIError:
        logger.debug("Could not notify the owner about the promotion", exc_info=True)
