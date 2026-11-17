"""Operational reports: the startup health message and the repair result.

These are messages, not screens, so they build their own text instead of going
through the screen renderer. Both languages are written side by side, like the
screens.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from src.bot.views import category_label, fallback_label
from src.core.i18n import pick
from src.services.files import RepairReport
from src.services.storage import StorageChatCheck
from src.utils.filetypes import FileCategory
from src.utils.formatters import format_number

_MAX_REASON = 200


def _escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def storage_check_text(
    checks: Sequence[StorageChatCheck],
    missing: Sequence[FileCategory] = (),
) -> str:
    """Report which configured storage groups answer and which ones do not."""
    ready = [check for check in checks if check.reachable]
    broken = [check for check in checks if not check.reachable]

    lines = [pick("🩺 <b>Storage group check</b>", "🩺 <b>فحص مجموعات التخزين</b>"), ""]
    if not checks:
        lines.append(
            pick(
                "⚠️ No storage group is configured: every upload is refused until "
                "you add <code>TG_STORAGE_CHAT_*</code> variables.",
                "⚠️ لا توجد أي مجموعة تخزين مهيّأة: سيرفض البوت كل عمليات الرفع "
                "حتى تضيف متغيرات <code>TG_STORAGE_CHAT_*</code>.",
            )
        )
    else:
        lines.append(
            pick(
                f"✅ Ready: {format_number(len(ready))} of {format_number(len(checks))}",
                f"✅ جاهزة: {format_number(len(ready))} من {format_number(len(checks))}",
            )
        )

    fallback = next((check for check in checks if check.is_fallback), None)
    if fallback is not None:
        state = (
            pick("ready", "جاهزة") if fallback.reachable else pick("unreachable", "غير قابلة للوصول")
        )
        lines.append(
            pick(
                f"🛟 Emergency group: {state} (<code>{fallback.chat_id}</code>) — used "
                "only to hold a file whose registration in the database fails.",
                f"🛟 مجموعة الاحتياط: {state} (<code>{fallback.chat_id}</code>) — "
                "تُستخدم فقط لحفظ أي ملف يفشل تسجيله في قاعدة البيانات.",
            )
        )

    if broken:
        lines.append("")
        lines.append(
            pick("⛔ <b>Unreachable groups</b>", "⛔ <b>مجموعات غير قابلة للوصول</b>")
        )
        for check in broken:
            labels = (
                fallback_label()
                if check.is_fallback
                else pick("، ", "، ").join(
                    category_label(category)
                    for category in sorted(check.categories, key=lambda item: item.value)
                )
            )
            lines.append(f"• {labels} — <code>{check.chat_id}</code>")
            if check.reason:
                lines.append(
                    pick(
                        f"  Reason: {_escape(check.reason[:_MAX_REASON])}",
                        f"  السبب: {_escape(check.reason[:_MAX_REASON])}",
                    )
                )
        lines.append("")
        lines.append(
            pick(
                "Make sure the bot is a member or an admin of the group, and that "
                "the id in Render is correct.",
                "تأكد أن البوت عضو أو مشرف في المجموعة، وأن المعرّف صحيح في Render.",
            )
        )

    # Without any configured group the first line already says it all.
    if missing and checks:
        lines.append("")
        lines.append(
            pick("⚠️ Categories without a storage group: ", "⚠️ فئات بلا مجموعة تخزين: ")
            + "، ".join(category_label(category) for category in missing)
            + pick(
                " — uploads of these types are refused.",
                " — سيرفض رفع هذه الأنواع.",
            )
        )

    if checks and not broken and not missing:
        lines.append("")
        lines.append(pick("You can upload files now.", "يمكنك رفع الملفات الآن."))
    return "\n".join(lines)


def repair_report_text(report: RepairReport, remaining: int) -> str:
    """The outcome of «Repair»: registered, moved, and what is still waiting."""
    lines = [pick("🔧 <b>Repair result</b>", "🔧 <b>نتيجة الإصلاح</b>"), ""]
    if report.database_error:
        lines.append(
            pick(
                "⛔ The database is not answering, so no file was moved.",
                "⛔ قاعدة البيانات لا تستجيب الآن، فلم يُنقل أي ملف.",
            )
        )
        lines.append(
            pick(
                f"Reason: {_escape(report.database_error[:_MAX_REASON])}",
                f"السبب: {_escape(report.database_error[:_MAX_REASON])}",
            )
        )
        lines.append("")
        lines.append(
            pick(
                "Try again once the database is back; the files stay in the "
                "emergency group.",
                "أعد المحاولة بعد عودة القاعدة؛ الملفات باقية في مجموعة الاحتياط.",
            )
        )
        lines.append(
            pick(
                f"⏳ Pending now: {format_number(remaining)}",
                f"⏳ المعلّق الآن: {format_number(remaining)}",
            )
        )
        return "\n".join(lines)

    lines.append(
        pick(
            f"✅ Registered and moved to its group: {format_number(report.repaired)}",
            f"✅ سُجّل ونُقل إلى مجموعته: {format_number(report.repaired)}",
        )
    )
    if report.duplicates:
        lines.append(
            pick(
                f"♻️ Already registered, so the held copy was removed: "
                f"{format_number(report.duplicates)}",
                f"♻️ كان مسجّلاً من قبل فحُذفت نسخة الاحتياط: {format_number(report.duplicates)}",
            )
        )
    if report.failed:
        lines.append(
            pick(
                f"⛔ Could not be moved: {format_number(report.failed)}",
                f"⛔ فشل نقله: {format_number(report.failed)}",
            )
        )
    if report.kept:
        lines.append(
            pick(
                f"⏸ No storage group for its category: {format_number(report.kept)} — add "
                "the category variable in Render.",
                f"⏸ بلا مجموعة تخزين لفئته: {format_number(report.kept)} — أضف متغير "
                "الفئة في Render.",
            )
        )
    lines.append(
        pick(
            f"⏳ Pending now: {format_number(remaining)}",
            f"⏳ المعلّق الآن: {format_number(remaining)}",
        )
    )
    if report.errors:
        lines.append("")
        lines.append(pick("<b>Details</b>", "<b>تفاصيل</b>"))
        for error in report.errors[:10]:
            lines.append(f"• {_escape(error[:160])}")
    if not remaining:
        lines.append("")
        lines.append(
            pick(
                "✅ The emergency group is empty now.",
                "✅ مجموعة الاحتياط فارغة الآن.",
            )
        )
    return "\n".join(lines)


__all__ = ["repair_report_text", "storage_check_text"]
