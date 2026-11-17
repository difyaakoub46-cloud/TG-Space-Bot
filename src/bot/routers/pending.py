"""The reserve section: files held in the emergency group, and their repair.

A file whose row cannot be written to Supabase never enters the library: it is
copied into ``TG_STORAGE_CHAT_FALLBACK`` and recorded in the local registry. This
screen shows what is waiting, and «Repair» registers every entry and moves it to
its category group once the database answers again.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from src.bot import navigation, reports
from src.bot.keyboards import MenuCB, pending_kb
from src.core.i18n import pick
from src.services.container import Services

logger = logging.getLogger(__name__)

router = Router(name="pending")

PENDING_SCREEN: dict[str, object] = {"k": "pending"}


def denied_text() -> str:
    return pick(
        "⛔ Repairing is limited to the owner.",
        "⛔ الإصلاح مقصور على المالك.",
    )


def not_configured_text() -> str:
    return pick(
        "⚠️ No emergency group is configured (TG_STORAGE_CHAT_FALLBACK).",
        "⚠️ لا توجد مجموعة احتياطية مهيّأة (TG_STORAGE_CHAT_FALLBACK).",
    )


def _is_owner(services: Services, user_id: int | None) -> bool:
    """Repairing touches real files, so it follows the owner setting.

    Without ``OWNER_ID`` the bot is personal and the allow-list is already the
    gate, so any listed account may repair.
    """
    if user_id is None:
        return False
    owner = services.settings.owner_id
    if owner is None:
        return int(user_id) in services.settings.allowed_users()
    return int(user_id) == int(owner)


@router.callback_query(MenuCB.filter(F.action == "pending"))
async def menu_pending_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Open the reserve screen; every number is read from the registry."""
    await navigation.navigate_callback(
        callback, state, bot, services, dict(PENDING_SCREEN), push=False
    )


@router.callback_query(MenuCB.filter(F.action == "repair"))
async def repair_callback(
    callback: CallbackQuery, services: Services, state: FSMContext, bot
) -> None:
    """Register every held file, then move it into its category group."""
    user_id = int(callback.from_user.id) if callback.from_user else None
    if not _is_owner(services, user_id):
        await callback.answer(denied_text(), show_alert=True)
        return
    if services.settings.storage_chat_fallback is None:
        await callback.answer(not_configured_text(), show_alert=True)
        return

    report = await services.files.repair_pending()
    remaining = services.pending.total
    logger.info(
        "Repair requested by %s: %s repaired, %s left",
        user_id,
        report.repaired,
        remaining,
    )
    await navigation.navigate_callback(
        callback,
        state,
        bot,
        services,
        dict(PENDING_SCREEN),
        notice=reports.repair_report_text(report, remaining),
        markup=pending_kb(remaining),
        push=False,
    )
