"""The general-status screen: bot, database and storage groups at a glance.

The same checks that run at startup are run again here, so the owner can confirm
at any moment that every storage group still answers before uploading anything.
The statistics and the reserve section are opened from this screen.
"""

from __future__ import annotations

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
from src.services.storage import (
    REQUIRED_RIGHTS,
    RIGHT_CONSEQUENCES,
    StorageChatCheck,
)
from src.utils.filetypes import FileCategory
from src.utils.formatters import format_number

logger = logging.getLogger(__name__)


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


_RIGHT_LABELS: dict[str, tuple[str, str]] = {
    name: (english, arabic) for name, english, arabic in REQUIRED_RIGHTS
}


def _rights_list(missing: Sequence[str]) -> str:
    """The missing rights, named the way Telegram's own screen names them."""
    return " · ".join(
        pick(*_RIGHT_LABELS[name]) for name in missing if name in _RIGHT_LABELS
    )


def _rights_lines(check: StorageChatCheck) -> list[str]:
    """One line per group: is the bot an admin, and which right is missing."""
    if check.is_admin is None:
        return []
    if not check.is_admin:
        return [
            pick(
                "   🛡 Admin: ⛔ the bot is not an administrator here — joining is "
                "unguarded and the promotion cannot work",
                "   🛡 الإشراف: ⛔ البوت ليس مشرفاً هنا — الدخول بلا حماية والترقية "
                "لا تعمل",
            )
        ]
    if not check.missing_rights:
        return [
            pick(
                "   🛡 Admin: ✅ admin with every required right",
                "   🛡 الإشراف: ✅ مشرف مع كل الحقوق المطلوبة",
            )
        ]
    return [
        pick(
            f"   🛡 Admin: ⚠️ missing {_rights_list(check.missing_rights)}",
            f"   🛡 الإشراف: ⚠️ ناقص: {_rights_list(check.missing_rights)}",
        )
    ]


def _check_labels(check: StorageChatCheck) -> str:
    if check.is_fallback:
        return views.fallback_label()
    if not check.categories:
        return "—"
    return "، ".join(
        views.category_label(category)
        for category in sorted(check.categories, key=lambda item: item.value)
    )


async def _bot_identity(bot) -> str:
    if bot is None:
        return pick("unavailable", "غير متاح")
    try:
        me = await bot.get_me()
    except TelegramAPIError:
        logger.warning("Could not read the bot identity", exc_info=True)
        return pick("could not reach Telegram", "تعذّر الاتصال بتيليغرام")
    handle = f"@{me.username}" if me.username else me.full_name
    return f"{_escape(handle)} · <code>{me.id}</code>"


async def status_text(
    services: Services, checks: Sequence[StorageChatCheck] | None = None
) -> str:
    """Build the status screen; the storage check is run when not supplied."""
    settings = services.settings

    # The call itself is what the latency line reports; its numbers belong to
    # «Statistics», so they are not shown here.
    started = time.perf_counter()
    try:
        await services.files.stats()
        database_error = None
    except (SupabaseError, OSError) as exc:
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
            f"Allowed users: {format_number(len(allowed)) if allowed else 'everybody'}",
            f"المستخدمون المسموحون: {format_number(len(allowed)) if allowed else 'الجميع'}",
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
                f"⛔ Could not read the stats: {_escape(database_error[:160])}",
                f"⛔ تعذّر قراءة الإحصائيات: {_escape(database_error[:160])}",
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
        ]
    )
    if not checks:
        lines.append(
            pick(
                "⚠️ No storage group is configured yet.",
                "⚠️ لم تُهيّأ أي مجموعة تخزين بعد.",
            )
        )
    for check in checks:
        mark = "✅" if check.reachable else "⛔"
        lines.append(f"{mark} {_check_labels(check)} — <code>{check.chat_id}</code>")
        if not check.reachable and check.reason:
            lines.append(
                pick(
                    f"   Reason: {_escape(check.reason[:160])}",
                    f"   السبب: {_escape(check.reason[:160])}",
                )
            )
        lines.extend(_rights_lines(check))

    # Rights are checked on their own block: a group with every upload working can
    # still leave the join guard blind, which is exactly the silent failure.
    rights_problems = [
        check
        for check in checks
        if check.reachable and (check.is_admin is False or check.missing_rights)
    ]
    if rights_problems:
        lines.extend(
            [
                "",
                pick(
                    "🛡 <b>Administrator rights</b>",
                    "🛡 <b>صلاحيات الإشراف</b>",
                ),
            ]
        )
        affected: list[str] = []
        for check in rights_problems:
            if check.is_admin is False:
                lines.append(f"⛔ {_check_labels(check)} — <code>{check.chat_id}</code>")
                lines.append(
                    "   • "
                    + pick(
                        "every required right is missing until the bot is added as an "
                        "administrator",
                        "كل الحقوق المطلوبة غائبة حتى يُضاف البوت مشرفاً",
                    )
                )
                continue
            lines.append(f"⚠️ {_check_labels(check)} — <code>{check.chat_id}</code>")
            for name in check.missing_rights:
                consequence = RIGHT_CONSEQUENCES.get(name)
                if consequence is not None:
                    lines.append(
                        f"   • {pick(*_RIGHT_LABELS[name])}: {pick(*consequence)}"
                    )
                    affected.append(name)
        if any(name == "can_invite_users" for name in affected):
            lines.append(
                pick(
                    "⛔ «Invite users» is off: Telegram sends no join request at all, "
                    "so links and approvals stop working in that group.",
                    "⛔ حق «دعوة المستخدمين» مغلق: تيليغرام لا يرسل أي طلب انضمام، "
                    "فتتعطّل الروابط والموافقات في تلك المجموعة.",
                )
            )
        if any(name == "can_restrict_members" for name in affected):
            lines.append(
                pick(
                    "⚠️ «Ban users» is off: a non-allowed account that gets in stays "
                    "there and reads every file.",
                    "⚠️ صلاحية «حظر الأعضاء» مغلقة: أي حساب غير مسموح يدخل يبقى "
                    "ويقرأ كل الملفات.",
                )
            )
        lines.append(
            pick(
                "Open the group → Administrators → the bot and enable every right "
                "listed above; the check refreshes with «🔍 Check and update status».",
                "افتح المجموعة ← المشرفون ← البوت وفعّل كل حق مذكور أعلاه؛ يتحدّث "
                "الفحص بزر «🔍 الفحص والتحديث الحالة».",
            )
        )

    if broken:
        lines.extend(
            [
                "",
                pick(
                    "⚠️ Add the bot as an admin in the groups marked ⛔, or fix their "
                    "ids in Render.",
                    "⚠️ أضف البوت مشرفاً في المجموعات المعلّمة بـ⛔ أو صحّح "
                    "معرّفها في Render.",
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


__all__ = ["status_text"]
