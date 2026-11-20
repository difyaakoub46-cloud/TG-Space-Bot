"""The general-status screen: bot, database and storage groups at a glance.

The same checks that run at startup are run again here, so the owner can confirm
at any moment that every storage group still answers before uploading anything.
The statistics, the group permissions and the reserve section are opened from this
screen.

Administrator rights have two levels of detail on purpose: one summary line in the
overview (are they all enabled, some, or none), and a separate
«🛡 Group permissions» screen — reached like «📊 Statistics» — that names every
missing right per group.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from aiogram.exceptions import TelegramAPIError

from src.bot import views
from src.core.i18n import pick, plural
from src.db.repository import SupabaseError
from src.services.container import Services
from src.services.storage import REQUIRED_RIGHTS, StorageChatCheck
from src.utils.filetypes import FileCategory
from src.utils.formatters import escape_text, format_number

logger = logging.getLogger(__name__)

_RIGHT_LABELS: dict[str, tuple[str, str]] = {
    name: (english, arabic) for name, english, arabic in REQUIRED_RIGHTS
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _uptime(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes, _ = divmod(rest, 60)
    parts: list[str] = []
    if days:
        parts.append(
            plural(days, "day", "days", ("يوم واحد", "يومان", "أيام", "يوماً"))
        )
    if hours:
        parts.append(
            plural(
                hours, "hour", "hours", ("ساعة واحدة", "ساعتان", "ساعات", "ساعة")
            )
        )
    parts.append(
        plural(
            minutes,
            "minute",
            "minutes",
            ("دقيقة واحدة", "دقيقتان", "دقائق", "دقيقة"),
        )
    )
    return pick(" and ".join(parts), " و".join(parts))


def right_labels(missing: Sequence[str]) -> str:
    """Missing rights, named the way Telegram's own screen names them."""
    return " · ".join(
        pick(*_RIGHT_LABELS[name]) for name in missing if name in _RIGHT_LABELS
    )


def _state(check: StorageChatCheck) -> str:
    """``ok``, ``partial`` or ``none`` — the only thing the overview needs."""
    if check.is_admin is False:
        return "none"
    return "partial" if check.missing_rights else "ok"


def _permissions_summary(checks: Sequence[StorageChatCheck]) -> str:
    readable = [check for check in checks if check.reachable]
    if not readable:
        return pick(
            "🛡 Permissions: ⚠️ could not be checked (no reachable group)",
            "🛡 الصلاحيات: ⚠️ تعذّر التحقق (لا مجموعة قابلة للوصول)",
        )
    total = len(readable)
    full = sum(1 for check in readable if _state(check) == "ok")
    off = sum(1 for check in readable if _state(check) == "none")
    groups = plural(total, "group", "groups", ("مجموعة", "مجموعتان", "مجموعات", "مجموعة"))
    if full == total:
        return pick(
            f"🛡 Permissions: ✅ every right is enabled in all {groups}",
            f"🛡 الصلاحيات: ✅ كل الحقوق مفعّلة في المجموعات ({total})",
        )
    if full == 0 and off == total:
        return pick(
            f"🛡 Permissions: ⛔ enabled in none of the {groups}",
            f"🛡 الصلاحيات: ⛔ غير مفعّلة في أي مجموعة من {total}",
        )
    return pick(
        f"🛡 Permissions: ⚠️ partial — complete in {full} of {total}",
        f"🛡 الصلاحيات: ⚠️ جزئية — مكتملة في {full} من {total}",
    )


def permissions_text(checks: Sequence[StorageChatCheck]) -> str:
    """The «Group permissions» screen: a summary line, then one line per group."""
    lines = [
        pick("🛡 <b>Group permissions</b>", "🛡 <b>الصلاحيات للمجموعات</b>"),
        "",
        _permissions_summary(checks),
        "",
    ]
    readable = [check for check in checks if check.reachable]
    if not readable:
        lines.append(
            pick(
                "⚠️ No storage group could be reached, so no right can be checked.",
                "⚠️ لم يصل البوت إلى أي مجموعة تخزين، فلا يمكن فحص أي حق.",
            )
        )
        return "\n".join(lines)

    for check in readable:
        state = _state(check)
        label = views.storage_group_label(check)
        label = f"{label} — <code>{check.chat_id}</code>"
        if state == "ok":
            lines.append(f"✅ {label}")
        elif state == "none":
            lines.append(f"⛔ {label} — " + pick("not an administrator", "ليس مشرفاً"))
        else:
            lines.append(
                f"⚠️ {label} — "
                + pick("missing: ", "ناقص: ")
                + right_labels(check.missing_rights)
            )

    lines.append("")
    lines.append(
        pick(
            "Enable the seven rights for the bot in each group's administrator "
            "screen; the check runs again on every visit.",
            "فعّل الحقوق السبعة للبوت في شاشة مشرفي كل مجموعة؛ ويتكرّر الفحص في كل زيارة.",
        )
    )
    return "\n".join(lines)


async def _storage_checks(services: Services) -> list[StorageChatCheck]:
    """The storage checks, or an empty list when they cannot be read.

    The answer is reused for a few seconds by :class:`StorageService`, so opening
    these screens again does not re-ask Telegram about every group.
    """
    try:
        return await services.storage.check_chats()
    except Exception:  # noqa: BLE001 - the screen must still render
        logger.exception("Could not verify the storage groups")
        return []


async def _timed_stats(services: Services) -> tuple[dict[str, Any], float]:
    """The library totals and how long the database took to produce them."""
    started = time.perf_counter()
    stats = await services.files.stats()
    return stats, (time.perf_counter() - started) * 1000


async def statistics_text(services: Services) -> str:
    """The «Statistics» screen: the library totals and the count of each category.

    Nothing here reaches Telegram: the storage groups have their own screen, so
    opening the statistics never waits for the group checks. The database latency
    is measured around the read that produced these numbers.
    """
    stats, latency_ms = await _timed_stats(services)
    return views.stats_text(stats, latency_ms)


async def group_permissions_text(services: Services) -> str:
    """The «Group permissions» screen, re-reading the groups before listing them."""
    return permissions_text(await _storage_checks(services))


# The bot's own account never changes while it runs, so it is asked for once.
_IDENTITY_CACHE: dict[int, str] = {}


async def _bot_identity(bot) -> str:
    if bot is None:
        return pick("unavailable", "غير متاح")
    cached = _IDENTITY_CACHE.get(id(bot))
    if cached is not None:
        return cached
    try:
        me = await bot.get_me()
    except TelegramAPIError:
        logger.warning("Could not read the bot identity", exc_info=True)
        return pick("could not reach Telegram", "تعذّر الاتصال بتيليغرام")
    handle = f"@{me.username}" if me.username else me.full_name
    text = f"{escape_text(handle)} · <code>{me.id}</code>"
    _IDENTITY_CACHE[id(bot)] = text
    return text


async def status_text(
    services: Services, checks: Sequence[StorageChatCheck] | None = None
) -> str:
    """Build the status screen; the storage check is run when not supplied."""
    settings = services.settings

    # Only the reachability of the database matters here: asking for one row
    # proves it answers, where the totals (which «Statistics» shows) would count
    # the whole library for a screen that never displays them.
    try:
        if checks is None:
            # The database check and the group check are independent: one wait for
            # both, and neither is repeated on the next tap.
            _, checks = await asyncio.gather(
                services.files.ping(), _storage_checks(services)
            )
        else:
            await services.files.ping()
        database_error = None
    except (SupabaseError, OSError):
        # The technical text (PostgREST codes, the failing URL) belongs to the
        # logs; the screen only says that the database did not answer.
        logger.error("Status screen: the database call failed", exc_info=True)
        database_error = True
    checks = list(checks or ())

    configured = settings.storage_chats()
    fallback = settings.storage_chat_fallback
    links = settings.storage_chat_links
    reachable = [check for check in checks if check.reachable]
    broken = [check for check in checks if not check.reachable]
    allowed = settings.allowed_users()

    # Both branches of ``pick`` are built, so a value written into each of them is
    # produced twice unless it is computed once here first.
    identity = await _bot_identity(services.bot)
    uptime = _uptime(services.uptime_seconds())
    updated = _now()

    lines = [
        pick("🩺 <b>General status</b>", "🩺 <b>الحالة العامة</b>"),
        pick(f"Last update: {updated}", f"آخر تحديث: {updated}"),
        "",
        pick("🤖 <b>Bot</b>", "🤖 <b>البوت</b>"),
        pick(f"Account: {identity}", f"الحساب: {identity}"),
        pick("Mode: long polling", "وضع التشغيل: Long polling (استقبال مباشر)"),
        pick(f"Uptime: {uptime}", f"مدة التشغيل: {uptime}"),
        pick(
            f"Owner: <code>{settings.owner_id or 'not set'}</code>",
            f"المالك: <code>{settings.owner_id or 'غير محدد'}</code>",
        ),
        pick(
            f"Allowed users: {format_number(len(allowed)) if allowed else 'not configured'}",
            f"المستخدمون المسموحون: {format_number(len(allowed)) if allowed else 'غير مهيّأ'}",
        ),
        "",
        pick("🗄 <b>Database</b> (Supabase)", "🗄 <b>قاعدة البيانات</b> (Supabase)"),
        pick(
            f"Table: <code>{escape_text(settings.supabase_table)}</code>",
            f"الجدول: <code>{escape_text(settings.supabase_table)}</code>",
        ),
        # The counts, the total size and the latency live in «Statistics»; this
        # screen only proves that the database answered.
    ]
    if database_error:
        lines.append(
            pick(
                "⛔ The database did not answer, so no number could be read. "
                "The details are in the logs.",
                "⛔ تعذّر الوصول إلى قاعدة البيانات، فلم تُقرأ الأرقام. "
                "التفاصيل في السجلات.",
            )
        )

    lines.extend(
        [
            "",
            pick("🗃 <b>Storage groups</b>", "🗃 <b>مجموعات التخزين</b>"),
            pick(
                f"Configured: {format_number(len(configured))} of "
                f"{format_number(len(FileCategory))} categories · "
                + (f"🛟 <code>{fallback}</code>" if fallback else "🛟 not configured")
                + (f" · 🔗 <code>{links}</code>" if links else ""),
                f"مهيّأة: {format_number(len(configured))} من "
                f"{format_number(len(FileCategory))} فئة · "
                + (f"🛟 <code>{fallback}</code>" if fallback else "🛟 غير مهيّأة")
                + (f" · 🔗 <code>{links}</code>" if links else ""),
            ),
            pick(
                f"Reachable now: ✅ {format_number(len(reachable))} · "
                f"⛔ {format_number(len(broken))}",
                f"الوصول الآن: ✅ {format_number(len(reachable))} · "
                f"⛔ {format_number(len(broken))}",
            ),
            pick(
                f"🛟 Reserve: {views.count_files(services.pending.total)} pending",
                f"🛟 الاحتياط: {views.count_files(services.pending.total)} في الانتظار",
            ),
            # Only the overall state here; the per-right detail and the group ids
            # have their own screen.
            _permissions_summary(checks),
        ]
    )
    if not checks:
        lines.append(
            pick(
                "⚠️ No storage group is configured yet.",
                "⚠️ لم تُهيّأ أي مجموعة تخزين بعد.",
            )
        )

    if broken:
        lines.extend(
            [
                "",
                pick(
                    "⚠️ Add the bot as an admin in the groups marked ⛔ in "
                    "«🛡 Group permissions», or fix their ids in Render.",
                    "⚠️ أضف البوت مشرفاً في المجموعات المعلّمة بـ⛔ داخل "
                    "«🛡 الصلاحيات للمجموعات» أو صحّح معرّفها في Render.",
                ),
            ]
        )
    uncovered = settings.uncovered_categories()
    if uncovered:
        lines.append(
            pick(
                "⚠️ Categories without a storage group (the reserve does not cover "
                "them; their uploads are refused): ",
                "⚠️ فئات بلا مجموعة تخزين (الاحتياط لا يغطيها، ورفعها سيُرفض): ",
            )
            + "، ".join(views.category_label(category) for category in uncovered)
        )

    if not settings.join_guard_enabled():
        lines.extend(
            [
                "",
                pick(
                    "⚠️ <b>Join guard is off</b>: links from «🔗 Join the groups» "
                    "join directly, and an unlisted account that gets into a "
                    "storage group is not removed. Set JOIN_GUARD=true and restart.",
                    "⚠️ <b>حماية الانضمام معطّلة</b>: روابط «🔗 الانضمام إلى "
                    "المجموعات» تُدخل مباشرة، ولا يُخرَج أي حساب غير مسموح إن دخل "
                    "إحدى مجموعات التخزين. اضبط JOIN_GUARD=true ثم أعد التشغيل.",
                ),
            ]
        )

    return "\n".join(lines)


__all__ = [
    "group_permissions_text",
    "permissions_text",
    "right_labels",
    "statistics_text",
    "status_text",
]
