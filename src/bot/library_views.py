"""Text builders for the upload result and the shared file-list header.

These arrived later than the library ones, so their text lives here instead of
growing :mod:`src.bot.views` any further. Every string still goes through
:func:`src.core.i18n.pick`, so both languages stay side by side.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from src.bot.views import category_label
from src.core.i18n import pick
from src.db.models import StoredFile
from src.services.files import local_zone
from src.utils.formatters import escape_text, format_number

if TYPE_CHECKING:
    from src.services.files import FileListPage

HISTORY_SPANS: tuple[str, ...] = ("day", "week", "month", "year", "all")


def history_span_label(span: str) -> str:
    """The period filters of the list, in the active language."""
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


def history_period_label(page: FileListPage) -> str:
    """The period on screen, e.g. «📅 Today: 2026-11-18» or the week's range.

    Only the current period is written out: the filters live on the buttons, so
    the message has no «Showing …» or «Page x of y» line to read through. The day
    is the local one, the same day the filter windows are built from, so «Today»
    appears at the user's midnight and not at UTC's.
    """
    today = datetime.now(local_zone()).date()
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
                f"📨 Saved-file screen: <b>{state}</b> — an album is walked file by file.",
                f"📨 شاشة «تم حفظ الملف»: <b>{state}</b> — يمكن التنقل بين ملفات الألبوم.",
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


def saved_screen_label(show: bool = True) -> str:
    """The saved-file card toggle, with its current state on the button."""
    state = pick("✅ On", "✅ مفعّلة") if show else pick("⛔ Off", "⛔ معطّلة")
    return pick(
        f"📨 Saved-file screen: {state}",
        f"📨 شاشة «تم حفظ الملف»: {state}",
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
                f"🗂 Keep the opened files: <b>{state}</b> — «↩️ Back» leaves the file in the chat.",
                f"🗂 الإبقاء على الملفات المفتوحة: <b>{state}</b> — «↩️ الرجوع» يترك الملف في المحادثة.",
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


def preferences_text() -> str:
    """The header of «⚙️ Preferences», the display settings of the screens."""
    return "\n".join(
        [
            pick("⚙️ <b>Preferences</b>", "⚙️ <b>التفضيلات</b>"),
            pick(
                "How the screens behave and what the files they open leave in the chat.",
                "كيف تتصرف الشاشات وما تتركه الملفات التي تفتحها في المحادثة.",
            ),
        ]
    )


def single_view_label(enabled: bool = False) -> str:
    """The «one file at a time» toggle, with its current state on the button."""
    state = pick("✅ On", "✅ مفعّل") if enabled else pick("⛔ Off", "⛔ معطّل")
    return pick(
        f"📄 Single file view: {state}",
        f"📄 عرض ملف واحد: {state}",
    )


def single_view_settings_text(enabled: bool = False) -> str:
    """The settings block of the «single file view» toggle."""
    state = pick("✅ On", "✅ مفعّل") if enabled else pick("⛔ Off", "⛔ معطّل")
    return "\n".join(
        [
            "",
            pick(
                f"📄 Single file view: <b>{state}</b> — a new file replaces the one the bot sent before it.",
                f"📄 عرض ملف واحد: <b>{state}</b> — الملف الجديد يحل محل الملف الذي أرسله البوت قبله.",
            ),
        ]
    )


def single_view_changed_text(enabled: bool) -> str:
    """The confirmation after flipping the «single file view» setting."""
    if enabled:
        return pick(
            "✅ Single file view is on: opening a file removes the previous file the "
            "bot sent, so only the newest one stays in the chat. «↩️ Back» and the "
            "main menu keep working exactly as before.",
            "✅ عرض ملف واحد مفعّل: فتح ملف يحذف الملف السابق الذي أرسله البوت، فيبقى "
            "الأحدث فقط في المحادثة. «↩️ الرجوع» والقائمة الرئيسية يعملان كما هما.",
        )
    return pick(
        "⛔ Single file view is off: every opened file stays in the chat until "
        "«↩️ Back» or the main menu clears it.",
        "⛔ عرض ملف واحد معطّل: يبقى كل ملف مفتوح في المحادثة إلى أن يمسحه "
        "«↩️ الرجوع» أو القائمة الرئيسية.",
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
        f"<b>{escape_text(record.file_name)}</b>",
        pick(
            f"Category: {category_label(record.category)}",
            f"الفئة: {category_label(record.category)}",
        ),
        pick(f"Size: {escape_text(record.size_human)}", f"الحجم: {escape_text(record.size_human)}"),
        pick(
            f"Saved on: {escape_text(record.created_human)}",
            f"تاريخ الحفظ: {escape_text(record.created_human)}",
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
                f"Fingerprint: <code>{escape_text(record.hash_short)}</code>",
                f"بصمة المحتوى: <code>{escape_text(record.hash_short)}</code>",
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
