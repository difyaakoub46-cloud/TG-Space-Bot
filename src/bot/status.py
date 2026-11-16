"""The general-status screen: bot, database and storage groups at a glance.

The same checks that run at startup are run again here, so the owner can confirm
at any moment that every storage group still answers before uploading anything.
"""

from __future__ import annotations

import html
import logging
import time
from collections.abc import Sequence
from datetime import datetime, timezone

from aiogram.exceptions import TelegramAPIError

from src.bot import views
from src.db.repository import SupabaseError
from src.services.container import Services
from src.services.storage import StorageChatCheck
from src.utils.filetypes import FileCategory
from src.utils.formatters import format_number, human_total

logger = logging.getLogger(__name__)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _plural(count: int, one: str, two: str, many: str) -> str:
    if count == 1:
        return one
    if count == 2:
        return two
    if 3 <= count <= 10:
        return f"{count} {many}"
    return f"{count} {one}"


def _uptime(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes, _ = divmod(rest, 60)
    parts = []
    if days:
        parts.append(_plural(days, "يوم", "يومان", "أيام"))
    if hours:
        parts.append(_plural(hours, "ساعة", "ساعتان", "ساعات"))
    parts.append(_plural(minutes, "دقيقة", "دقيقتان", "دقائق"))
    return " و".join(parts)


def _check_labels(check: StorageChatCheck) -> str:
    if check.is_fallback:
        return views.FALLBACK_LABEL
    if not check.categories:
        return "—"
    return "، ".join(
        views.category_label(category)
        for category in sorted(check.categories, key=lambda item: item.value)
    )


async def _bot_identity(bot) -> str:
    if bot is None:
        return "غير متاح"
    try:
        me = await bot.get_me()
    except TelegramAPIError:
        logger.warning("Could not read the bot identity", exc_info=True)
        return "تعذّر الاتصال بتيليغرام"
    handle = f"@{me.username}" if me.username else me.full_name
    return f"{_escape(handle)} · <code>{me.id}</code>"


async def status_text(
    services: Services, checks: Sequence[StorageChatCheck] | None = None
) -> str:
    """Build the status screen; the storage check is run when not supplied."""
    settings = services.settings

    started = time.perf_counter()
    try:
        stats = await services.files.stats()
        database_error = None
    except (SupabaseError, OSError) as exc:
        stats = {"total": 0, "total_bytes": 0, "categories": {}}
        database_error = str(exc)
    latency_ms = (time.perf_counter() - started) * 1000

    if checks is None:
        try:
            checks = await services.storage.check_chats()
        except Exception:  # noqa: BLE001 - the screen must still render
            logger.exception("Could not verify the storage groups")
            checks = []
    checks = list(checks)

    configured = settings.storage_chats()
    fallback = settings.storage_chat_fallback
    reachable = [check for check in checks if check.reachable]
    broken = [check for check in checks if not check.reachable]
    allowed = settings.allowed_users()
    categories = stats.get("categories") or {}

    lines = [
        "🩺 <b>الحالة العامة</b>",
        f"آخر تحديث: {_now()}",
        "",
        "🤖 <b>البوت</b>",
        f"الحساب: {await _bot_identity(services.bot)}",
        "وضع التشغيل: Long polling (استقبال مباشر)",
        f"مدة التشغيل: {_uptime(services.uptime_seconds())}",
        f"المالك: <code>{settings.owner_id or 'غير محدد'}</code>",
        f"المستخدمون المسموحون: {format_number(len(allowed)) if allowed else 'الجميع'}",
        "",
        "🗄 <b>قاعدة البيانات</b> (Supabase)",
        f"الجدول: <code>{_escape(settings.supabase_table)}</code> · "
        f"زمن الاستجابة: {latency_ms:.0f} م.ث",
        f"الملفات: <b>{format_number(int(stats.get('total', 0)))}</b> · "
        f"الحجم: <b>{_escape(human_total(int(stats.get('total_bytes', 0) or 0)))}</b>",
    ]
    if database_error:
        lines.append(f"⛔ تعذّر قراءة الإحصائيات: {_escape(database_error[:160])}")
    for category in views.CATEGORY_ORDER:
        count = int(categories.get(category, 0) or 0)
        lines.append(f"{views.category_label(category)}: {format_number(count)}")

    lines.extend(
        [
            "",
            "🗃 <b>مجموعات التخزين</b>",
            f"مهيّأة: {format_number(len(configured))} من "
            f"{format_number(len(FileCategory))} فئة · "
            + (f"🛟 <code>{fallback}</code>" if fallback else "🛟 غير مهيّأة"),
            f"الوصول الآن: ✅ {format_number(len(reachable))} · "
            f"⛔ {format_number(len(broken))}",
        ]
    )
    if not checks:
        lines.append("⚠️ لم تُهيّأ أي مجموعة تخزين بعد.")
    for check in checks:
        mark = "✅" if check.reachable else "⛔"
        lines.append(f"{mark} {_check_labels(check)} — <code>{check.chat_id}</code>")
        if not check.reachable and check.reason:
            lines.append(f"   السبب: {_escape(check.reason[:160])}")

    if broken:
        lines.extend(
            [
                "",
                "⚠️ أضف البوت مشرفاً في المجموعات المعلّمة بـ⛔ أو صحّح "
                "معرّفها في Render.",
            ]
        )
    uncovered = settings.uncovered_categories()
    if uncovered:
        lines.append(
            "⚠️ بلا مجموعة ولا احتياطية: "
            + "، ".join(views.category_label(category) for category in uncovered)
        )
    return "\n".join(lines)


__all__ = ["status_text"]
