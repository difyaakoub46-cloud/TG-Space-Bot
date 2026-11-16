"""Startup reports: the storage-group health message sent to the owner.

These are operational messages, not screens, so they build their own text instead
of going through the screen renderer in :mod:`src.bot.views`.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from src.bot.views import FALLBACK_LABEL, category_label
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

    fallback_ok = next(
        (check for check in checks if check.is_fallback and check.reachable), None
    )
    if fallback_ok is not None:
        lines.append(
            "🛟 المجموعة الاحتياطية مستخدمة للفئات غير المهيّأة "
            f"(<code>{fallback_ok.chat_id}</code>)."
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


__all__ = ["storage_check_text"]
