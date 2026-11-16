"""Startup reports: the storage-group health message sent to the owner.

These are operational messages, not screens, so they build their own text instead
of going through the screen renderer in :mod:`src.bot.views`.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from src.bot.views import FALLBACK_LABEL, category_label
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

    lines = ["🩺 <b>فحص مجموعات التخزين</b>", ""]
    if not checks:
        lines.append(
            "⚠️ لا توجد أي مجموعة تخزين مهيّأة: سيرفض البوت كل عمليات الرفع "
            "حتى تضيف متغيرات <code>TG_STORAGE_CHAT_*</code>."
        )
    else:
        lines.append(
            f"✅ جاهزة: {format_number(len(ready))} من {format_number(len(checks))}"
        )

    fallback = next((check for check in checks if check.is_fallback), None)
    if fallback is not None:
        state = "جاهزة" if fallback.reachable else "غير قابلة للوصول"
        lines.append(
            f"🛟 مجموعة الاحتياط: {state} (<code>{fallback.chat_id}</code>) — "
            "تُستخدم فقط لحفظ أي ملف يفشل تسجيله في قاعدة البيانات."
        )

    if broken:
        lines.append("")
        lines.append("⛔ <b>مجموعات غير قابلة للوصول</b>")
        for check in broken:
            labels = (
                FALLBACK_LABEL
                if check.is_fallback
                else "، ".join(
                    category_label(category)
                    for category in sorted(check.categories, key=lambda item: item.value)
                )
            )
            lines.append(f"• {labels} — <code>{check.chat_id}</code>")
            if check.reason:
                lines.append(f"  السبب: {_escape(check.reason[:_MAX_REASON])}")
        lines.append("")
        lines.append(
            "تأكد أن البوت عضو أو مشرف في المجموعة، وأن المعرّف صحيح في Render."
        )

    # Without any configured group the first line already says it all.
    if missing and checks:
        lines.append("")
        lines.append(
            "⚠️ فئات بلا مجموعة تخزين: "
            + "، ".join(category_label(category) for category in missing)
            + " — سيرفض رفع هذه الأنواع."
        )

    if checks and not broken and not missing:
        lines.append("")
        lines.append("يمكنك رفع الملفات الآن.")
    return "\n".join(lines)


def repair_report_text(report: RepairReport, remaining: int) -> str:
    """The outcome of «🔧 إصلاح»: registered, moved, and what is still waiting."""
    lines = ["🔧 <b>نتيجة الإصلاح</b>", ""]
    if report.database_error:
        lines.append("⛔ قاعدة البيانات لا تستجيب الآن، فلم يُنقل أي ملف.")
        lines.append(f"السبب: {_escape(report.database_error[:_MAX_REASON])}")
        lines.append("")
        lines.append(
            "أعد المحاولة بعد عودة القاعدة؛ الملفات باقية في مجموعة الاحتياط."
        )
        lines.append(f"⏳ المعلّق الآن: {format_number(remaining)}")
        return "\n".join(lines)

    lines.append(f"✅ سُجّل ونُقل إلى مجموعته: {format_number(report.repaired)}")
    if report.duplicates:
        lines.append(
            "♻️ كان مسجّلاً من قبل فحُذفت نسخة الاحتياط: "
            f"{format_number(report.duplicates)}"
        )
    if report.failed:
        lines.append(f"⛔ فشل نقله: {format_number(report.failed)}")
    if report.kept:
        lines.append(
            "⏸ بلا مجموعة تخزين لفئته: "
            f"{format_number(report.kept)} — أضف متغير الفئة في Render."
        )
    lines.append(f"⏳ المعلّق الآن: {format_number(remaining)}")
    if report.errors:
        lines.append("")
        lines.append("<b>تفاصيل</b>")
        for error in report.errors[:10]:
            lines.append(f"• {_escape(error[:160])}")
    if not remaining:
        lines.append("")
        lines.append("✅ مجموعة الاحتياط فارغة الآن.")
    return "\n".join(lines)


__all__ = ["repair_report_text", "storage_check_text"]
