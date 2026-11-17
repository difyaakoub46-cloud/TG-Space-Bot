"""Owner-only gate: one-time invites to the storage groups, admin on join.

The gate asks for nothing: pressing the button checks the caller's Telegram id
against ``ALLOWED_USER_IDS`` (and the optional ``OWNER_ID``) silently, and only a
listed account ever gets to see the group buttons. Every refusal happens before
any invite link exists.

Groups that were upgraded to supergroups keep answering with their old id, so a
``TelegramMigrateToChat`` answer is followed once with the new id and the mapping
is remembered for the promotion handler.
"""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramMigrateToChat
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, ChatInviteLink

from src.bot import navigation, views
from src.bot.keyboards import JoinCB, MenuCB
from src.core.config import ConfigurationError
from src.core.i18n import pick
from src.services.container import Services
from src.utils.filetypes import parse_category

logger = logging.getLogger(__name__)

router = Router(name="access")

def denied_text() -> str:
    return pick(
        "⛔ This command is limited to the owner.",
        "⛔ هذا الأمر مقصور على المالك.",
    )


def not_configured_text() -> str:
    return pick(
        "⚠️ Joining is not configured: add your id to ALLOWED_USER_IDS.",
        "⚠️ ميزة الانضمام غير مهيّأة: أضف معرّفك في المتغير ALLOWED_USER_IDS.",
    )
# Telegram rejects an alert longer than 200 characters, which used to turn the
# error text itself into an unhandled MESSAGE_TOO_LONG failure.
_MAX_ALERT = 190

# Old group id -> supergroup id, learned from Telegram's migrate answer.
_MIGRATED: dict[int, int] = {}

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


def _storage_ids(services: Services) -> set[int]:
    """Configured storage chats plus the supergroup ids learned for them."""
    ids: set[int] = set()
    configured = list(services.settings.storage_chats().values())
    fallback = services.settings.storage_chat_fallback
    if fallback is not None:
        configured.append(fallback)
    for chat_id in configured:
        ids.add(int(chat_id))
        migrated = _MIGRATED.get(int(chat_id))
        if migrated is not None:
            ids.add(migrated)
    return ids


async def _alert(callback: CallbackQuery, text: str) -> None:
    """Show a short alert; a failed acknowledgement must never break the handler."""
    try:
        await callback.answer(text[:_MAX_ALERT], show_alert=True)
    except TelegramAPIError:
        logger.debug("Could not show an alert", exc_info=True)


def _remember_migration(old_id: int, new_id: int) -> None:
    if _MIGRATED.get(old_id) == new_id:
        return
    _MIGRATED[old_id] = new_id
    logger.warning(
        "Storage chat %s is now the supergroup %s: update TG_STORAGE_CHAT_* in the "
        "environment to the new id.",
        old_id,
        new_id,
    )


async def _create_invite(
    bot: Bot, chat_id: int, name: str
) -> tuple[ChatInviteLink, int, int | None]:
    """Create a one-time link, following a supergroup migration once.

    Returns the link, the chat id that actually answered, and the old id when a
    migration was needed (so the caller can tell the owner to update the setting).
    """
    try:
        link = await bot.create_chat_invite_link(
            chat_id=chat_id, member_limit=1, name=name
        )
        return link, chat_id, None
    except TelegramMigrateToChat as exc:
        new_id = int(exc.migrate_to_chat_id)
        _remember_migration(int(chat_id), new_id)
        link = await bot.create_chat_invite_link(
            chat_id=new_id, member_limit=1, name=name
        )
        return link, new_id, int(chat_id)


@router.callback_query(MenuCB.filter(F.action == "join"))
async def menu_join_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Open the group picker for a listed account, silently refuse everybody else."""
    if not _allowed_ids(services):
        await _alert(callback, not_configured_text())
        return
    if not _is_allowed(services, callback.from_user.id):
        logger.warning(
            "Ignored a join request from unlisted user %s", callback.from_user.id
        )
        await _alert(callback, denied_text())
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
        await _alert(callback, not_configured_text())
        return
    if not _is_allowed(services, callback.from_user.id):
        logger.warning(
            "Ignored an invite request from unlisted user %s", callback.from_user.id
        )
        await _alert(callback, denied_text())
        return

    data = JoinCB.unpack(callback.data)
    if data.value == navigation.JOIN_FALLBACK:
        chat_id = services.settings.storage_chat_fallback
        if chat_id is None:
            await _alert(
                callback,
                pick(
                    "⚠️ No emergency group is configured.",
                    "⚠️ لا توجد مجموعة احتياطية مهيّأة.",
                ),
            )
            return
        label = views.fallback_label()
        invite_name = "owner-fallback"
    else:
        category = parse_category(data.value)
        try:
            chat_id = services.settings.storage_chat_for(category)
        except ConfigurationError:
            await _alert(
                callback,
                pick(
                    "⚠️ No storage group is configured for this category.",
                    "⚠️ لا توجد مجموعة تخزين مهيّأة لهذه الفئة.",
                ),
            )
            return
        label = views.category_label(category)
        invite_name = f"owner-{category.value}"[:32]

    try:
        link, used_id, old_id = await _create_invite(bot, chat_id, invite_name)
    except TelegramMigrateToChat as exc:
        new_id = int(exc.migrate_to_chat_id)
        _remember_migration(int(chat_id), new_id)
        logger.warning("Could not create an invite link for %s: %s", chat_id, exc)
        await _alert(
            callback,
            pick(
                f"ℹ️ This group became a supergroup with the id {new_id}. "
                "Update its id in Render and try again.",
                f"ℹ️ هذه المجموعة صارت «مجموعة عظمى» بمعرّف {new_id}. "
                "حدّث معرّفها في Render ثم أعد المحاولة.",
            ),
        )
        return
    except TelegramAPIError as exc:
        logger.warning("Could not create an invite link for %s: %s", chat_id, exc)
        await _alert(
            callback,
            pick(
                "⚠️ Could not create the link. Make sure the bot is an admin with "
                "the invite-users right, and that the group is a supergroup.",
                "⚠️ تعذّر إنشاء الرابط. تأكد أن البوت مشرف في المجموعة مع صلاحية "
                "دعوة المستخدمين، وأن المجموعة «مجموعة عظمى» لا مجموعة عادية.",
            ),
        )
        return

    logger.info("Created a one-time invite link for %s", label)
    notice = views.invite_text(link.invite_link, label)
    if old_id is not None:
        notice += (
            pick(
                "\n\nℹ️ The group was upgraded to a supergroup. Update its id in Render "
                f"to <code>{used_id}</code> so the automatic promotion keeps working "
                "after a restart.",
                "\n\nℹ️ المجموعة ترقّت إلى «مجموعة عظمى». حدّث معرّفها في Render إلى "
                f"<code>{used_id}</code> لتبقى الترقية التلقائية تعمل بعد إعادة التشغيل.",
            )
        )
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        {"k": "join_groups"},
        notice=notice,
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
    if update.chat.id not in _storage_ids(services):
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
            pick(
                f"✅ You were promoted to admin in «{title}» automatically.",
                f"✅ تمت ترقيتك إلى مشرف في مجموعة «{title}» تلقائياً.",
            ),
        )
    except TelegramAPIError:
        logger.debug("Could not notify the promoted user", exc_info=True)
