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
import html
import logging
import time
from collections.abc import Sequence
from datetime import datetime, timezone

from aiogram.exceptions import TelegramAPIError

from src.bot import views
from src.core.i18n import pick
from src.db.repository import SupabaseError
from src.services.container import Services
from src.services.storage import REQUIRED_RIGHTS, StorageChatCheck
from src.utils.filetypes import FileCategory
from src.utils.formatters import format_number

logger = logging.getLogger(__name__)

_RIGHT_LABELS: dict[str, tuple[str, str]] = {
    name: (english, arabic) for name, english, arabic in REQUIRED_RIGHTS
}


def _escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _plural_ar(count: int, one: str, two: str, many: str) -> str:
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
    parts: list[str] = []
    if days:
        parts.append(
            pick(
                f"{days} day{'s' if days != 1 else ''}",
                _plural_ar(days, "يوم", "يومان", "أيام"),
            )
        )
    if hours:
        parts.append(
            pick(
                f"{hours} hour{'s' if hours != 1 else ''}",
                _plural_ar(hours, "ساعة", "ساعتان", "ساعات"),
            )
        )
    parts.append(
        pick(
            f"{minutes} minute{'s' if minutes != 1 else ''}",
            _plural_ar(minutes, "دقيقة", "دقيقتان", "دقائق"),
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


def _check_labels(check: StorageChatCheck) -> str:
    if check.is_fallback:
        return views.fallback_label()
    if not check.categories:
        return "—"
    return "، ".join(
        views.category_label(category)
        for category in sorted(check.categories, key=lambda item: item.value)
    )


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
    if full == total:
        return pick(
            f"🛡 Permissions: ✅ every right is enabled in all {total} group(s)",
            f"🛡 الصلاحيات: ✅ كل الحقوق مفعّلة في المجموعات ({total})",
        )
    if full == 0 and off == total:
        return pick(
            f"🛡 Permissions: ⛔ enabled in none of the {total} group(s)",
            f"🛡 الصلاحيات: ⛔ غير مفعّلة في أي مجموعة من {total}",
        )
    return pick(
        f"🛡 Permissions: ⚠️ partial — complete in {full} of {total} group(s)",
        f"🛡 الصلاحيات: ⚠️ جزئية — مكتملة في {full} من {total} مجموعة",
    )


def permissions_text(checks: Sequence[StorageChatCheck]) -> str:
    """The «Group permissions» screen: every group, every missing right."""
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
        label = _check_labels(check)
        if state == "ok":
            lines.append(f"✅ {label} — <code>{check.chat_id}</code>")
            lines.append(
                "   "
                + pick(
                    "all the required rights are enabled",
                    "كل الحقوق المطلوبة مفعّلة",
                )
            )
            continue
        if state == "none":
            lines.append(f"⛔ {label} — <code>{check.chat_id}</code>")
            lines.append(
                "   "
                + pick(
                    "the bot is not an administrator here, so no right applies",
                    "البوت ليس مشرفاً هنا، فلا ينطبق أي حق",
                )
            )
            continue

        lines.append(f"⚠️ {label} — <code>{check.chat_id}</code>")
        lines.append(
            "   " + pick("missing: ", "ناقص: ") + right_labels(check.missing_rights)
        )
        if "can_invite_users" in check.missing_rights:
            lines.append(
                "   "
                + pick(
                    "⛔ no join request arrives, so links and approvals stop working",
                    "⛔ لا يصل أي طلب انضمام، فتتعطّل الروابط والموافقات",
                )
            )
        if "can_restrict_members" in check.missing_rights:
            lines.append(
                "   "
                + pick(
                    "⚠️ a non-allowed account that gets in cannot be removed",
                    "⚠️ لا يمكن إخراج حساب غير مسموح إذا دخل",
                )
            )
        if "can_delete_messages" in check.missing_rights:
            lines.append(
                "   "
                + pick(
                    "⚠️ a copy stays in the library when its registration fails",
                    "⚠️ تبقى نسخة في المكتبة إذا فشل تسجيل الملف",
                )
            )
        promotion_rights = {
            "can_promote_members",
            "can_change_info",
            "can_pin_messages",
            "can_manage_video_chats",
        }
        if promotion_rights.intersection(check.missing_rights):
            lines.append(
                "   "
                + pick(
                    "⚠️ the automatic promotion of the owner fails",
                    "⚠️ تفشل الترقية التلقائية للمالك",
                )
            )

    lines.append("")
    lines.append(
        pick(
            "The seven rights come from the administrator screen of each group. "
            "Enable all of them for the bot; the check runs again every time you "
            "open this section.",
            "الحقوق السبعة هي نفسها في شاشة المشرفين لكل مجموعة. فعّلها كلها للبوت؛ "
            "ويتكرّر الفحص في كل مرة تفتح فيها هذا القسم.",
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


async def statistics_text(services: Services) -> str:
    """The «Statistics» screen: the counts next to the group holding each category."""
    # The totals and the group check are independent: one wait instead of two.
    stats, checks = await asyncio.gather(
        services.files.stats(), _storage_checks(services)
    )
    return views.stats_text(stats, checks)


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
    text = f"{_escape(handle)} · <code>{me.id}</code>"
    _IDENTITY_CACHE[id(bot)] = text
    return text


async def status_text(
    services: Services, checks: Sequence[StorageChatCheck] | None = None
) -> str:
    """Build the status screen; the storage check is run when not supplied."""
    settings = services.settings

    # The call itself is what the latency line reports; its numbers belong to
    # «Statistics», so they are not shown here.
    started = time.perf_counter()
    try:
        if checks is None:
            # The database latency and the group check are independent: one wait
            # for both, and neither is repeated on the next tap.
            _, checks = await asyncio.gather(
                services.files.stats(), _storage_checks(services)
            )
        else:
            await services.files.stats()
        database_error = None
    except (SupabaseError, OSError):
        # The technical text (PostgREST codes, the failing URL) belongs to the
        # logs; the screen only says that the database did not answer.
        logger.error("Status screen: the database call failed", exc_info=True)
        database_error = True
    latency_ms = (time.perf_counter() - started) * 1000
    checks = list(checks or ())

    configured = settings.storage_chats()
    fallback = settings.storage_chat_fallback
    reachable = [check for check in checks if check.reachable]
    broken = [check for check in checks if not check.reachable]
    allowed = settings.allowed_users()

    lines = [
        pick("🩺 <b>General status</b>", "🩺 <b>الحالة العامة</b>"),
        pick(f"Last update: {_now()}", f"آخر تحديث: {_now()}"),
        "",
        pick("🤖 <b>Bot</b>", "🤖 <b>البوت</b>"),
        pick(f"Account: {await _bot_identity(services.bot)}", f"الحساب: {await _bot_identity(services.bot)}"),
        pick("Mode: long polling", "وضع التشغيل: Long polling (استقبال مباشر)"),
        pick(f"Uptime: {_uptime(services.uptime_seconds())}", f"مدة التشغيل: {_uptime(services.uptime_seconds())}"),
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
            f"Table: <code>{_escape(settings.supabase_table)}</code> · "
            f"Latency: {latency_ms:.0f} ms",
            f"الجدول: <code>{_escape(settings.supabase_table)}</code> · "
            f"زمن الاستجابة: {latency_ms:.0f} م.ث",
        ),
        # The file counts and the total size live in «Statistics», so they are not
        # repeated here: this screen only proves the database answered and how fast.
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
                + (f"🛟 <code>{fallback}</code>" if fallback else "🛟 not configured"),
                f"مهيّأة: {format_number(len(configured))} من "
                f"{format_number(len(FileCategory))} فئة · "
                + (f"🛟 <code>{fallback}</code>" if fallback else "🛟 غير مهيّأة"),
            ),
            pick(
                f"Reachable now: ✅ {format_number(len(reachable))} · "
                f"⛔ {format_number(len(broken))}",
                f"الوصول الآن: ✅ {format_number(len(reachable))} · "
                f"⛔ {format_number(len(broken))}",
            ),
            pick(
                f"🛟 Reserve: {format_number(services.pending.total)} pending file(s)",
                f"🛟 الاحتياط: {format_number(services.pending.total)} ملفاً معلّقاً",
            ),
            # Only the overall state here; the per-right detail has its own section,
            # and the per-group ids live in «Statistics».
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
                    "«📊 Statistics», or fix their ids in Render.",
                    "⚠️ أضف البوت مشرفاً في المجموعات المعلّمة بـ⛔ داخل "
                    "«📊 الإحصائيات» أو صحّح معرّفها في Render.",
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

    return "\n".join(lines)


__all__ = [
    "group_permissions_text",
    "permissions_text",
    "right_labels",
    "statistics_text",
    "status_text",
]
