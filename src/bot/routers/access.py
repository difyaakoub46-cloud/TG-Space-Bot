"""Owner-only gate: one-time invites to the storage groups, admin on join.

The gate asks for nothing: pressing the button checks the caller's Telegram id
against ``ALLOWED_USER_IDS`` (and the optional ``OWNER_ID``) silently, and only a
listed account ever gets to see the group buttons. Every refusal happens before
any invite link exists.

A link is still only a secret, not a permission: with ``JOIN_GUARD`` on it creates
a join request that the bot approves for an allowed id alone, expires after
``JOIN_LINK_TTL_MINUTES`` and is revoked on its first use. An account that reaches
a storage group by another route is removed, because merely refusing its commands
would still leave it reading every stored file.

Groups that were upgraded to supergroups keep answering with their old id, so a
``TelegramMigrateToChat`` answer is followed once with the new id and the mapping
is remembered for the promotion handler.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramMigrateToChat
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    ChatInviteLink,
    ChatJoinRequest,
    ChatMemberUpdated,
)

from src.bot import navigation, views
from src.bot.keyboards import JoinCB, MenuCB
from src.core.config import ConfigurationError
from src.core.i18n import pick
from src.services.container import Services
from src.services.storage import StorageService
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

# A chat is named once: an unconfigured group would otherwise warn on every
# single membership update it produces.
_UNKNOWN_CHATS_WARNED: set[int] = set()

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


def _storage(services: Services) -> StorageService | None:
    """The storage backend, which owns the group-id mapping (None before binding)."""
    return getattr(services, "storage", None)


def _storage_ids(services: Services) -> set[int]:
    """Configured storage chats plus the supergroup ids learned for them.

    The mapping lives in the storage service, so the upload path and this gate
    always agree on the current id of an upgraded group — and a restart keeps the
    gate working instead of silently forgetting the new id.
    """
    ids: set[int] = set()
    configured = list(services.settings.storage_chats().values())
    fallback = services.settings.storage_chat_fallback
    if fallback is not None:
        configured.append(fallback)
    storage = _storage(services)
    for chat_id in configured:
        ids.add(int(chat_id))
        if storage is not None:
            ids.add(storage.current_chat_id(int(chat_id)))
    return ids


async def _alert(callback: CallbackQuery, text: str) -> None:
    """Show a short alert; a failed acknowledgement must never break the handler."""
    try:
        await views.ack(callback, text[:_MAX_ALERT], show_alert=True)
    except TelegramAPIError:
        logger.debug("Could not show an alert", exc_info=True)


def _remember_migration(services: Services, old_id: int, new_id: int) -> None:
    """Follow a group upgrade in the one place that owns the mapping."""
    storage = _storage(services)
    if storage is not None:
        storage.remember_migration(old_id, new_id)
        return
    logger.warning(
        "Storage chat %s is now the supergroup %s: update TG_STORAGE_CHAT_* in the "
        "environment to the new id.",
        old_id,
        new_id,
    )


def _warn_unknown_chat(kind: str, chat_id: int) -> None:
    """Say out loud that a group is outside the gate, instead of staying silent."""
    if chat_id in _UNKNOWN_CHATS_WARNED:
        return
    _UNKNOWN_CHATS_WARNED.add(chat_id)
    logger.warning(
        "Ignoring a %s update from chat %s: it is not one of the configured "
        "storage chats, so the join guard does not cover it. If that group was "
        "upgraded to a supergroup, update TG_STORAGE_CHAT_* to its new id.",
        kind,
        chat_id,
    )


async def _create_invite(
    bot: Bot,
    services: Services,
    chat_id: int,
    name: str,
    *,
    join_request: bool,
    ttl_seconds: int,
) -> tuple[ChatInviteLink, int, int | None]:
    """Create a short-lived invite link, following a supergroup migration once.

    With ``join_request`` the link only produces a join request, which the bot
    approves for allowed ids alone — a leaked link is then useless. Telegram does
    not accept ``creates_join_request`` together with ``member_limit``, so the
    approval step replaces the one-use cap; without the guard the link keeps its
    single-use limit. ``expire_date`` is always set: the link is the secret, so it
    must die on its own.

    Returns the link, the chat id that actually answered, and the old id when a
    migration was needed (so the caller can tell the owner to update the setting).
    """
    kwargs: dict[str, object] = {
        "name": name,
        "expire_date": timedelta(seconds=ttl_seconds),
    }
    if join_request:
        kwargs["creates_join_request"] = True
    else:
        kwargs["member_limit"] = 1
    try:
        link = await bot.create_chat_invite_link(chat_id=chat_id, **kwargs)
        return link, chat_id, None
    except TelegramMigrateToChat as exc:
        new_id = int(exc.migrate_to_chat_id)
        _remember_migration(services, int(chat_id), new_id)
        link = await bot.create_chat_invite_link(chat_id=new_id, **kwargs)
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

    ttl_seconds = services.settings.join_link_ttl()
    join_request = services.settings.join_guard_enabled()
    try:
        link, used_id, old_id = await _create_invite(
            bot,
            services,
            chat_id,
            invite_name,
            join_request=join_request,
            ttl_seconds=ttl_seconds,
        )
    except TelegramMigrateToChat as exc:
        new_id = int(exc.migrate_to_chat_id)
        _remember_migration(services, int(chat_id), new_id)
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
    notice = views.invite_text(link.invite_link, label, join_request=join_request)
    ttl_minutes = ttl_seconds // 60
    if join_request:
        notice += pick(
            "\n\n🛡 The link only creates a <b>join request</b>: the bot approves an "
            "allowed account, refuses anybody else, and revokes the link on its "
            "first use.",
            "\n\n🛡 الرابط يُنشئ <b>طلب انضمام</b> فقط: يوافق البوت على الحساب "
            "المسموح ويرفض أي حساب آخر، ويُبطل الرابط من أول استخدام.",
        )
        notice += pick(
            f"\n⏳ It also expires after {ttl_minutes} minute(s).",
            f"\n⏳ كما تنتهي صلاحيته بعد {ttl_minutes} دقيقة.",
        )
    else:
        # Without the guard the link joins directly, but it still expires: not
        # saying so made the owner think a leaked link stayed valid forever.
        notice += pick(
            f"\n\n⏳ The link expires after {ttl_minutes} minute(s).",
            f"\n\n⏳ ينتهي الرابط بعد {ttl_minutes} دقيقة.",
        )
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
    """Guard a storage group: promote listed accounts, remove everybody else."""
    user_id = int(update.new_chat_member.user.id)
    # The bot's own membership is never an intruder: a bot cannot ban itself, so
    # this used to fail and page the owner with a false alarm on every join.
    if user_id == int(getattr(bot, "id", 0) or 0):
        return
    if int(update.chat.id) not in _storage_ids(services):
        _warn_unknown_chat("membership", int(update.chat.id))
        return

    if not _is_allowed(services, user_id):
        # The allow-list is the only thing between a leaked link and every stored
        # file, so an unlisted account is taken back out instead of merely being
        # refused the bot's commands.
        if services.settings.join_guard_enabled():
            await _remove_intruder(bot, update, services)
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


async def _revoke_link(bot: Bot, chat_id: int, link: ChatInviteLink | None) -> None:
    """Kill a link the bot itself created; another admin's link is left alone."""
    if link is None or link.creator is None or int(link.creator.id) != int(bot.id):
        return
    try:
        await bot.revoke_chat_invite_link(
            chat_id=chat_id, invite_link=link.invite_link
        )
        logger.info("Revoked the invite link of %s", chat_id)
    except TelegramAPIError:
        logger.debug("Could not revoke the link of %s", chat_id, exc_info=True)


async def _notify_owner(bot: Bot, services: Services, text: str) -> None:
    """Tell the owner about a refusal; logging is enough when no id is set."""
    owner = services.settings.owner_id
    if owner is None:
        logger.info("OWNER_ID is not set, security notice only logged: %s", text)
        return
    try:
        await bot.send_message(int(owner), text)
    except TelegramAPIError:
        logger.debug("Could not send the security notice", exc_info=True)


async def _remove_intruder(
    bot: Bot, update: ChatMemberUpdated, services: Services
) -> None:
    """Take an unlisted account back out of a storage group."""
    chat_id = int(update.chat.id)
    user_id = int(update.new_chat_member.user.id)
    title = escape(update.chat.title or str(chat_id))

    if update.new_chat_member.status not in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.RESTRICTED,
    ):
        return

    try:
        # A ban followed by an unban removes the member without barring them for
        # good: supergroups have no separate "remove" call.
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await bot.unban_chat_member(
            chat_id=chat_id, user_id=user_id, only_if_banned=True
        )
    except TelegramAPIError:
        logger.exception("Could not remove unlisted user %s from %s", user_id, chat_id)
        await _notify_owner(
            bot,
            services,
            pick(
                "⚠️ <b>An unlisted account joined a storage group</b>\n\n"
                f"Account: <code>{user_id}</code>\nGroup: {title}\n\n"
                "The bot could not remove it: it needs the «Ban users» right in "
                "that group. Remove it by hand and revoke the invite links.",
                "⚠️ <b>دخل حساب غير مسموح إلى مجموعة تخزين</b>\n\n"
                f"الحساب: <code>{user_id}</code>\nالمجموعة: {title}\n\n"
                "لم يستطع البوت إخراجه: يحتاج صلاحية «حظر الأعضاء» في تلك "
                "المجموعة. أخرجه يدوياً وأبطِل روابط الدعوة.",
            ),
        )
        return

    logger.warning("Removed unlisted user %s from %s", user_id, chat_id)
    await _notify_owner(
        bot,
        services,
        pick(
            "🛡 <b>Removed an unlisted account</b>\n\n"
            f"Account: <code>{user_id}</code>\nGroup: {title}\n\n"
            "It was not in ALLOWED_USER_IDS. Check the invite links of that group.",
            "🛡 <b>أُخرج حساب غير مسموح</b>\n\n"
            f"الحساب: <code>{user_id}</code>\nالمجموعة: {title}\n\n"
            "لم يكن في ALLOWED_USER_IDS. راجع روابط الدعوة في تلك المجموعة.",
        ),
    )


@router.chat_join_request()
async def on_join_request(
    request: ChatJoinRequest, services: Services, bot: Bot
) -> None:
    """Approve the allow-list only, then burn the link that was used."""
    if not services.settings.join_guard_enabled():
        return
    chat_id = int(request.chat.id)
    if chat_id not in _storage_ids(services):
        _warn_unknown_chat("join request", chat_id)
        return

    user_id = int(request.from_user.id)
    title = escape(request.chat.title or str(chat_id))

    if not _is_allowed(services, user_id):
        logger.warning(
            "Refused a join request from unlisted user %s for %s", user_id, chat_id
        )
        # Whatever link this account holds is compromised: kill it and refuse.
        await _revoke_link(bot, chat_id, request.invite_link)
        try:
            await bot.decline_chat_join_request(chat_id=chat_id, user_id=user_id)
        except TelegramAPIError:
            logger.exception("Could not decline %s in %s", user_id, chat_id)
        await _notify_owner(
            bot,
            services,
            pick(
                "🛡 <b>Refused a join request</b>\n\n"
                f"Account: <code>{user_id}</code>\nGroup: {title}\n\n"
                "The account is not in ALLOWED_USER_IDS, and the link it used was "
                "revoked. If this was you, create a new link from the bot.",
                "🛡 <b>رُفض طلب انضمام</b>\n\n"
                f"الحساب: <code>{user_id}</code>\nالمجموعة: {title}\n\n"
                "الحساب ليس في ALLOWED_USER_IDS، وقد أُبطل الرابط الذي استخدمه. "
                "إن كنت أنت، أنشئ رابطاً جديداً من البوت.",
            ),
        )
        return

    try:
        await bot.approve_chat_join_request(chat_id=chat_id, user_id=user_id)
    except TelegramAPIError:
        logger.exception("Could not approve %s in %s", user_id, chat_id)
        return

    logger.info("Approved %s in %s", user_id, chat_id)
    # The link did its job; nothing else may use it.
    await _revoke_link(bot, chat_id, request.invite_link)
