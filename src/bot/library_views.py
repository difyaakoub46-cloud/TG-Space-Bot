"""Text builders for the upload result and the upload history.

The two screens arrived later than the library ones, so their text lives here
instead of growing :mod:`src.bot.views` any further. Every string still goes
through :func:`src.core.i18n.pick`, so both languages stay side by side.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from src.bot.views import category_label
from src.core.i18n import pick
from src.db.models import StoredFile
from src.utils.formatters import format_number

if TYPE_CHECKING:
    from src.services.files import HistoryPage

HISTORY_SPANS: tuple[str, ...] = ("day", "week", "month", "year", "all")


def _escape(value: object) -> str:
    return html.escape(str(value), quote=False)


def history_span_label(span: str) -> str:
    """The period filters of «History uploads», in the active language."""
    return {
        "day": pick("📅 Today", "📅 اليوم"),
        "week": pick("📅 This Week", "📅 هذا الأسبوع"),
        "month": pick("📅 This Month", "📅 هذا الشهر"),
        "year": pick("📅 This Year", "📅 هذه السنة"),
        "all": pick("📁 All time", "📁 كل الوقت"),
    }.get(str(span or "day"), str(span))


def _period_range(first: date, last: date) -> str:
    """A compact ``12 Nov - 18 Nov`` range, in the active language."""
    return pick(
        f"{first:%d %b} - {last:%d %b}",
        f"{first:%d %b} - {last:%d %b}",
    )


def history_period_label(page: HistoryPage) -> str:
    """The period on screen, e.g. «📅 Today: 2026-11-18» or the week's range.

    Only the current period is written out: the filters live on the buttons, so
    the message has no «Showing …» or «Page x of y» line to read through.
    """
    today = datetime.now(timezone.utc).date()
    if page.span == "all":
        return pick("📁 All time", "📁 كل الوقت")
    if page.span == "day":
        if page.anchor == today:
            return pick(
                f"📅 Today: {page.anchor.isoformat()}",
                f"📅 اليوم: {page.anchor.isoformat()}",
            )
        return f"📅 {page.anchor.isoformat()}"
    if page.span == "week":
        first = page.anchor - timedelta(days=page.anchor.weekday())
        last = first + timedelta(days=6)
        current = first <= today <= last
        header = (
            pick("📅 This Week", "📅 هذا الأسبوع")
            if current
            else pick("📅 Week", "📅 الأسبوع")
        )
        return f"{header}: {_period_range(first, last)}"
    if page.span == "month":
        current = (page.anchor.year, page.anchor.month) == (today.year, today.month)
        header = (
            pick("📅 This Month", "📅 هذا الشهر")
            if current
            else pick("📅 Month", "📅 الشهر")
        )
        return f"{header}: {page.anchor:%Y-%m}"
    current = page.anchor.year == today.year
    header = (
        pick("📅 This Year", "📅 هذه السنة")
        if current
        else pick("📅 Year", "📅 السنة")
    )
    return f"{header}: {page.anchor.year}"


def saved_screen_settings_text(show: bool = True) -> str:
    """The settings block of the «📨 Saved-file screen» toggle."""
    state = pick("✅ On", "✅ مفعّلة") if show else pick("⛔ Off", "⛔ معطّلة")
    return "\n".join(
        [
            "",
            pick(
                f"📨 Saved-file screen: <b>{state}</b>",
                f"📨 شاشة «تم حفظ الملف»: <b>{state}</b>",
            ),
            pick(
                "When it is on, every upload ends with the saved card, and an album "
                "is walked file by file with «Next ▶». When it is off, files are "
                "stored silently and no card appears.",
                "عند التفعيل تنتهي كل عملية رفع ببطاقة الحفظ، ويمكن التنقل بين ملفات "
                "الألبوم بزرّي «التالي ▶» و«السابق ◀». وعند التعطيل تُحفظ الملفات "
                "بصمت دون إظهار أي بطاقة.",
            ),
        ]
    )


def saved_screen_changed_text(show: bool) -> str:
    """The confirmation after flipping the saved-file card setting."""
    if show:
        return pick(
            "✅ The saved-file screen is on: every upload ends with its card, and "
            "an album can be walked with «Next ▶».",
            "✅ شاشة «تم حفظ الملف» مفعّلة: كل رفع ينتهي ببطاقته، ويمكن التنقل بين "
            "ملفات الألبوم بزر «التالي ▶».",
        )
    return pick(
        "⛔ The saved-file screen is off: uploads are stored silently without any "
        "card. You can turn it back on here at any time.",
        "⛔ شاشة «تم حفظ الملف» معطّلة: تُحفظ الملفات بصمت دون أي بطاقة. يمكنك "
        "إعادة تفعيلها من هنا في أي وقت.",
    )


def keep_sent_label(keep: bool = False) -> str:
    """The «keep the opened files» toggle, with its current state on the button."""
    state = pick("✅ On", "✅ مفعّل") if keep else pick("⛔ Off", "⛔ معطّل")
    return pick(
        f"🗂 Keep the opened files: {state}",
        f"🗂 الإبقاء على الملفات المفتوحة: {state}",
    )


def keep_sent_settings_text(keep: bool = False) -> str:
    """The settings block of the «keep the opened files» toggle."""
    state = pick("✅ On", "✅ مفعّل") if keep else pick("⛔ Off", "⛔ معطّل")
    return "\n".join(
        [
            "",
            pick(
                f"🗂 Keep the opened files: <b>{state}</b>",
                f"🗂 الإبقاء على الملفات المفتوحة: <b>{state}</b>",
            ),
            pick(
                "When it is on, a file you open with «📤 Send» stays in the chat even "
                "after «↩️ Back», through the library, until the main menu removes "
                "everything.",
                "عند التفعيل يبقى الملف الذي تفتحه بزر «📤 إرسال» في المحادثة حتى بعد "
                "«↩️ الرجوع»، عبر المكتبة، إلى أن يمسح زر «القائمة الرئيسية» كل شيء.",
            ),
            pick(
                "When it is off, pressing «↩️ Back» on a file removes its preview "
                "from the chat immediately; the main menu still clears everything.",
                "عند التعطيل يمسح زر «↩️ الرجوع» معاينة الملف من المحادثة فوراً، ويبقى "
                "زر «القائمة الرئيسية» يمسح كل شيء.",
            ),
        ]
    )


def keep_sent_changed_text(keep: bool) -> str:
    """The confirmation after flipping the «keep the opened files» setting."""
    if keep:
        return pick(
            "✅ Opened files are kept: they stay in the chat after «↩️ Back» until "
            "the main menu clears the chat.",
            "✅ الملفات المفتوحة تُبقى: تبقى في المحادثة بعد «↩️ الرجوع» إلى أن "
            "تمسحها القائمة الرئيسية.",
        )
    return pick(
        "⛔ Opened files are removed on «↩️ Back»: the preview leaves the chat as "
        "soon as you go back. The main menu still clears everything.",
        "⛔ الملفات المفتوحة تُمسح بزر «↩️ الرجوع»: تختفي المعاينة من المحادثة "
        "بمجرد الرجوع، ويبقى زر القائمة الرئيسية يمسح كل شيء.",
    )


def saved_batch_text(
    records: Sequence[StoredFile],
    index: int = 0,
    duplicates: Sequence[str] = (),
    pending: int = 0,
    failures: int = 0,
) -> str:
    """One file of an upload batch, under a «file i of N» header.

    An album produces several files at once, so the result is walked file by file:
    «Next ▶» / «◀ Previous» move through the whole batch while the buttons below
    always belong to the file on screen.
    """
    total = len(records)
    index = min(max(0, int(index)), total - 1) if total else 0
    record = records[index]
    duplicate_ids = set(duplicates)
    lines = [
        pick(
            f"✅ <b>{format_number(total)} files were saved</b>",
            f"✅ <b>تم حفظ {format_number(total)} ملفات</b>",
        ),
        pick(
            f"📄 File {index + 1} of {total}",
            f"📄 الملف {index + 1} من {total}",
        ),
        "",
        f"<b>{_escape(record.file_name)}</b>",
        pick(
            f"Category: {category_label(record.category)}",
            f"الفئة: {category_label(record.category)}",
        ),
        pick(f"Size: {_escape(record.size_human)}", f"الحجم: {_escape(record.size_human)}"),
        pick(
            f"Saved on: {_escape(record.created_human)}",
            f"تاريخ الحفظ: {_escape(record.created_human)}",
        ),
    ]
    if record.id in duplicate_ids:
        lines.append(
            pick(
                "♻️ Already stored — no second copy was made.",
                "♻️ موجود أصلاً — لم تُحفظ نسخة ثانية.",
            )
        )
    if record.hash_short:
        lines.append(
            pick(
                f"Fingerprint: <code>{_escape(record.hash_short)}</code>",
                f"بصمة المحتوى: <code>{_escape(record.hash_short)}</code>",
            )
        )

    notes: list[str] = []
    if pending:
        notes.append(pick(f"🛟 {pending} held in reserve", f"🛟 {pending} في الاحتياط"))
    if failures:
        notes.append(pick(f"❌ {failures} not saved", f"❌ {failures} لم تُحفظ"))
    if notes:
        lines.extend(["", " · ".join(notes)])

    lines.extend(
        [
            "",
            pick(
                "Use «Next ▶» to reach the next uploaded file, or the buttons below "
                "to act on this one.",
                "استخدم «التالي ▶» للانتقال إلى الملف التالي في الدفعة، أو الأزرار "
                "أدناه للعمل على هذا الملف.",
            ),
        ]
    )
    return "\n".join(lines)


def history_text(page: HistoryPage) -> str:
    """The archive screen: only its title, the period on screen and its files.

    The files are buttons, so the message stays clean: no “Newest first”, no
    category labels, no counters and no “Page x of y”. The period line is the only
    context kept, and an empty period says so instead of listing nothing.
    """
    lines = [
        pick("📂 <b>History uploads</b>", "📂 <b>سجل الرفع</b>"),
        _escape(history_period_label(page)),
    ]
    if not page.files:
        lines.extend(
            [
                "",
                pick(
                    "No uploads in this period.",
                    "لا توجد ملفات في هذه الفترة.",
                ),
            ]
        )
    return "\n".join(lines)
