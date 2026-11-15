"""Owner-only gate: one-time invites to the storage groups, admin on join.

The gate asks for nothing: pressing the button checks the caller's Telegram id
against ``ALLOWED_USER_IDS`` (and the optional ``OWNER_ID``) silently, and only a
listed account ever gets to see the group buttons. Every refusal happens before
any invite link exists.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated

from src.bot import navigation, views
from src.bot.keyboards import JoinCB, MenuCB
from src.core.config import ConfigurationError
from src.services.container import Services
from src.utils.filetypes import parse_category

logger = logging.getLogger(__name__)

router = Router(name="access")

DENIED_TEXT = "⛔ هذا الأمر مقصور على المالك."
NOT_CONFIGURED_TEXT = "⚠️ ميزة الانضمام غير مهيّأة: أضف معرّفك في المتغير ALLOWED_USER_IDS."

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


def _allowed_ids(services: Services) -> frozenset[int]:
    """The Telegram ids that may use the gate: ALLOWED_USER_IDS plus the owner."""
    ids = set(services.settings.allowed_users())
    owner = services.settings.owner_id
    if owner is not None:
        ids.add(int(owner))
    return frozenset(ids)


def _is_allowed(services: Services, user_id: int | None) -> bool:
    if user_id is None:
        return False
    return int(user_id) in _allowed_ids(services)


def _reason(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text[:160] or type(exc).__name__


@router.callback_query(MenuCB.filter(F.action == "join"))
async def menu_join_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Open the group picker for a listed account, silently refuse everybody else."""
    if not _allowed_ids(services):
        await callback.answer(NOT_CONFIGURED_TEXT, show_alert=True)
        return
    if not _is_allowed(services, callback.from_user.id):
        logger.warning(
            "Ignored a join request from unlisted user %s", callback.from_user.id
        )
        await callback.answer(DENIED_TEXT, show_alert=True)
        return

    await views.reset_flow(state)
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "join_groups"},
        push=False,
    )


@router.callback_query(JoinCB.filter(F.action == "link"))
async def join_link_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    if not _allowed_ids(services):
        await callback.answer(NOT_CONFIGURED_TEXT, show_alert=True)
        return
    if not _is_allowed(services, callback.from_user.id):
        logger.warning(
            "Ignored an invite request from unlisted user %s", callback.from_user.id
        )
        await callback.answer(DENIED_TEXT, show_alert=True)
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
        notice=views.invite_text(link.invite_link, views.category_label(category)),
        push=False,
    )


@router.chat_member()
async def on_chat_member(
    update: ChatMemberUpdated, services: Services, bot: Bot
) -> None:
    """Promote a listed account the moment it joins a storage group."""
    user_id = int(update.new_chat_member.user.id)
    if not _is_allowed(services, user_id):
        return
    if update.chat.id not in set(services.settings.storage_chats().values()):
        return

    status = update.new_chat_member.status
    if status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        return

    try:
        promoted = await bot.promote_chat_member(
            chat_id=update.chat.id, user_id=user_id, **OWNER_RIGHTS
        )
    except TelegramAPIError:
        logger.exception("Could not promote %s in %s", user_id, update.chat.id)
        return

    if not promoted:
        logger.warning("Telegram refused to promote %s in %s", user_id, update.chat.id)
        return

    logger.info("Promoted %s in %s", user_id, update.chat.id)
    title = update.chat.title or str(update.chat.id)
    try:
        await bot.send_message(
            user_id,
            f"✅ تمت ترقيتك إلى مشرف في مجموعة «{title}» تلقائياً.",
        )
    except TelegramAPIError:
        logger.debug("Could not notify the promoted user", exc_info=True)
