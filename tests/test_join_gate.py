"""The join gate: invite links, join requests, and who may stay in a group.

The gate is the only thing between a leaked invite link and every stored file, so
these tests pin it end to end: the link is short-lived and single-use (or a join
request when ``JOIN_GUARD`` is on), an allowed request is approved and the link
burned, an unlisted request is declined, an unlisted account that gets in is
removed, and the bot never mistakes its own membership for an intruder.

Only Telegram is replaced: the handlers under test are the real ones, and the
storage service is real, so the group-id mapping shared by uploads and the gate is
exercised too.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatInviteLink

import main
from src.bot import status
from src.bot.keyboards import JoinCB
from src.bot.routers import access
from src.services.storage import StorageService
from tests.conftest import DOCUMENTS_GROUP, make_settings

ALLOWED = 42
STRANGER = 99
OWNER = 7
BOT_ID = 777
OTHER_GROUP = -501


class GateBot:
    """The Telegram methods the join gate calls, with a call log."""

    def __init__(self, *, promote: bool = True) -> None:
        self.id = BOT_ID
        self.promote = promote
        self.created: list[dict[str, object]] = []
        self.actions: list[tuple] = []
        self.messages: list[tuple[int, str]] = []

    async def create_chat_invite_link(self, chat_id: int, **kwargs):
        self.created.append({"chat_id": int(chat_id), **kwargs})
        return ChatInviteLink.model_construct(
            invite_link=f"https://t.me/+{chat_id}",
            creator=SimpleNamespace(id=self.id),
        )

    async def revoke_chat_invite_link(self, chat_id: int, invite_link: str):
        self.actions.append(("revoke", int(chat_id), invite_link))
        return True

    async def approve_chat_join_request(self, chat_id: int, user_id: int):
        self.actions.append(("approve", int(chat_id), int(user_id)))
        return True

    async def decline_chat_join_request(self, chat_id: int, user_id: int):
        self.actions.append(("decline", int(chat_id), int(user_id)))
        return True

    async def ban_chat_member(self, chat_id: int, user_id: int):
        self.actions.append(("ban", int(chat_id), int(user_id)))
        return True

    async def unban_chat_member(self, chat_id: int, user_id: int, **kwargs):
        self.actions.append(("unban", int(chat_id), int(user_id), dict(kwargs)))
        return True

    async def promote_chat_member(self, chat_id: int, user_id: int, **kwargs):
        self.actions.append(("promote", int(chat_id), int(user_id), dict(kwargs)))
        return self.promote

    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.messages.append((int(chat_id), text))
        return SimpleNamespace(message_id=1)


def services_for(settings, bot: GateBot) -> SimpleNamespace:
    """The services the gate reads: the settings and the shared storage backend."""
    return SimpleNamespace(settings=settings, storage=StorageService(bot, settings))


def invite_link() -> ChatInviteLink:
    """A link the bot itself created, so the handler is allowed to revoke it."""
    return ChatInviteLink.model_construct(
        invite_link="https://t.me/+secret", creator=SimpleNamespace(id=BOT_ID)
    )


def join_request(user_id: int = ALLOWED, chat_id: int = DOCUMENTS_GROUP):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Documents"),
        from_user=SimpleNamespace(id=user_id),
        invite_link=invite_link(),
    )


def member_update(
    user_id: int,
    *,
    chat_id: int = DOCUMENTS_GROUP,
    member_status: str = ChatMemberStatus.MEMBER,
):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title="Documents"),
        new_chat_member=SimpleNamespace(
            user=SimpleNamespace(id=user_id), status=member_status
        ),
    )


async def link_from_button(monkeypatch, settings, bot: GateBot) -> dict[str, object]:
    """Press «Join the groups» → a group, and return what the screen was told."""
    captured: dict[str, object] = {}

    async def navigate(callback, state, bot_, services, target, **kwargs):
        captured["screen"] = target
        captured["notice"] = kwargs.get("notice")

    monkeypatch.setattr(access.navigation, "navigate_callback", navigate)
    callback = SimpleNamespace(
        data=JoinCB(action="link", value="document").pack(),
        from_user=SimpleNamespace(id=ALLOWED),
        message=None,
    )
    await access.join_link_callback(
        callback, services=services_for(settings, bot), state=None, bot=bot
    )
    return captured


# --------------------------------------------------------------------------- #
# The link itself
# --------------------------------------------------------------------------- #
async def test_a_guarded_link_is_a_join_request_that_expires(monkeypatch, tmp_path) -> None:
    settings = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED))
    bot = GateBot()

    captured = await link_from_button(monkeypatch, settings, bot)

    assert len(bot.created) == 1
    created = bot.created[0]
    assert created["chat_id"] == DOCUMENTS_GROUP
    # Telegram refuses creates_join_request together with member_limit, so the
    # approval step replaces the single-use cap.
    assert created["creates_join_request"] is True
    assert "member_limit" not in created
    assert created["expire_date"] == timedelta(seconds=600)
    assert captured["screen"] == {"k": "join_groups"}
    assert "https://t.me/+-100222" in str(captured["notice"])


async def test_without_the_guard_the_link_is_single_use_and_still_expires(
    monkeypatch, tmp_path
) -> None:
    settings = make_settings(
        tmp_path, allowed_user_ids_raw=str(ALLOWED), join_guard=False, join_link_ttl_minutes=15
    )
    bot = GateBot()

    await link_from_button(monkeypatch, settings, bot)

    created = bot.created[0]
    assert created["member_limit"] == 1
    assert "creates_join_request" not in created
    assert created["expire_date"] == timedelta(minutes=15)


# --------------------------------------------------------------------------- #
# Join requests
# --------------------------------------------------------------------------- #
async def test_an_allowed_request_is_approved_and_the_link_burned(tmp_path) -> None:
    settings = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED), owner_id=OWNER)
    bot = GateBot()
    services = services_for(settings, bot)

    await access.on_join_request(join_request(), services=services, bot=bot)

    assert bot.actions == [
        ("approve", DOCUMENTS_GROUP, ALLOWED),
        ("revoke", DOCUMENTS_GROUP, "https://t.me/+secret"),
    ]
    assert bot.messages == []


async def test_an_unlisted_request_is_declined_and_the_link_burned(tmp_path) -> None:
    settings = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED), owner_id=OWNER)
    bot = GateBot()
    services = services_for(settings, bot)

    await access.on_join_request(
        join_request(user_id=STRANGER), services=services, bot=bot
    )

    assert bot.actions == [
        ("revoke", DOCUMENTS_GROUP, "https://t.me/+secret"),
        ("decline", DOCUMENTS_GROUP, STRANGER),
    ]
    # The owner is told, with the account id and no invite link.
    assert [chat for chat, _text in bot.messages] == [OWNER]
    assert str(STRANGER) in bot.messages[0][1]


async def test_a_request_for_another_group_is_ignored_with_a_warning(
    tmp_path, caplog
) -> None:
    settings = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED), owner_id=OWNER)
    bot = GateBot()
    services = services_for(settings, bot)

    with caplog.at_level(logging.WARNING, logger=access.__name__):
        await access.on_join_request(
            join_request(chat_id=OTHER_GROUP), services=services, bot=bot
        )

    assert bot.actions == [] and bot.messages == []
    assert any("join guard does not cover it" in record.message for record in caplog.records)


# --------------------------------------------------------------------------- #
# Membership updates
# --------------------------------------------------------------------------- #
async def test_an_unlisted_member_is_removed(tmp_path) -> None:
    settings = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED), owner_id=OWNER)
    bot = GateBot()
    services = services_for(settings, bot)

    await access.on_chat_member(
        member_update(STRANGER), services=services, bot=bot
    )

    assert [action[:3] for action in bot.actions] == [
        ("ban", DOCUMENTS_GROUP, STRANGER),
        ("unban", DOCUMENTS_GROUP, STRANGER),
    ]
    # Banned then unbanned: out of the group without being barred for good.
    assert bot.actions[1][3] == {"only_if_banned": True}
    assert [chat for chat, _text in bot.messages] == [OWNER]


async def test_the_bot_ignores_its_own_membership(tmp_path) -> None:
    settings = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED), owner_id=OWNER)
    bot = GateBot()
    services = services_for(settings, bot)

    await access.on_chat_member(member_update(BOT_ID), services=services, bot=bot)

    assert bot.actions == []
    assert bot.messages == []


async def test_an_allowed_member_is_promoted(tmp_path) -> None:
    settings = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED), owner_id=OWNER)
    bot = GateBot()
    services = services_for(settings, bot)

    await access.on_chat_member(member_update(ALLOWED), services=services, bot=bot)

    assert bot.actions[0][:3] == ("promote", DOCUMENTS_GROUP, ALLOWED)
    assert bot.actions[0][3]["can_restrict_members"] is True
    assert [chat for chat, _text in bot.messages] == [ALLOWED]


async def test_a_membership_update_from_another_group_is_ignored_with_a_warning(
    tmp_path, caplog
) -> None:
    settings = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED), owner_id=OWNER)
    bot = GateBot()
    services = services_for(settings, bot)

    with caplog.at_level(logging.WARNING, logger=access.__name__):
        await access.on_chat_member(
            member_update(STRANGER, chat_id=-777_001), services=services, bot=bot
        )

    assert bot.actions == []
    assert any("join guard does not cover it" in record.message for record in caplog.records)


async def test_the_group_id_learned_by_the_storage_service_covers_the_gate(tmp_path) -> None:
    """The upload path and the gate share one mapping, so a supergroup stays guarded."""
    settings = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED), owner_id=OWNER)
    bot = GateBot()
    services = services_for(settings, bot)
    supergroup = -1_000_222_999
    services.storage.remember_migration(DOCUMENTS_GROUP, supergroup)
    assert services.storage.current_chat_id(DOCUMENTS_GROUP) == supergroup

    await access.on_chat_member(
        member_update(STRANGER, chat_id=supergroup), services=services, bot=bot
    )

    assert [action[:3] for action in bot.actions] == [
        ("ban", supergroup, STRANGER),
        ("unban", supergroup, STRANGER),
    ]


# --------------------------------------------------------------------------- #
# The warning that the guard is off
# --------------------------------------------------------------------------- #
async def test_the_status_screen_warns_when_the_guard_is_off(tmp_path) -> None:
    class Files:
        async def stats(self):
            return None

    def services(settings) -> SimpleNamespace:
        return SimpleNamespace(
            settings=settings,
            files=Files(),
            pending=SimpleNamespace(total=0),
            uptime_seconds=lambda: 0.0,
            bot=None,
        )

    off = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED), join_guard=False)
    text = await status.status_text(services(off), checks=[])
    assert "Join guard is off" in text

    on = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED))
    assert "Join guard is off" not in await status.status_text(services(on), checks=[])


async def test_the_boot_log_warns_when_the_guard_is_off(tmp_path, caplog) -> None:
    async def checks():
        return []

    settings = make_settings(tmp_path, allowed_user_ids_raw=str(ALLOWED), join_guard=False)
    services = SimpleNamespace(storage=SimpleNamespace(check_chats=checks), bot=GateBot())

    with caplog.at_level(logging.WARNING):
        await main.report_storage_health(settings, services)

    assert any("JOIN_GUARD is off" in record.message for record in caplog.records)


@pytest.mark.parametrize("guard", [True, False])
def test_the_settings_read_the_guard_flag(guard: bool, tmp_path) -> None:
    settings = make_settings(
        tmp_path, allowed_user_ids_raw=str(ALLOWED), join_guard=guard
    )
    assert settings.join_guard_enabled() is guard
