"""Text rendering for every bot screen."""

from __future__ import annotations

from aiogram.types import CallbackQuery, InlineKeyboardMarkup

from src.core.config import Settings
from src.db.models import StoredFile
from src.services.files import FilePage, LibraryStats
from src.utils.filetypes import CATEGORY_LABELS, FileCategory, parse_category
from src.utils.formatters import escape, human_size, truncate
from src.utils.telegram import safe_edit_text

DIVIDER = "━━━━━━━━━━━━━━━"


def welcome_text(settings: Settings) -> str:
    scope = ""
    if settings.allowed_users:
        scope = f"\n🔐 البوت مخصص لـ {len(settings.allowed_users)} مستخدم/مستخدمين.\n"
    return (
        "<b>🗄 TG Space Bot</b>\n"
        f"{DIVIDER}\n"
        "مساحتك السحابية داخل تيليغرام.\n"
        f"{scope}\n"
        "📤 <b>أرسل أي ملف</b> في هذه المحادثة وسيُرفع تلقائياً إلى مجموعة "
        "التخزين المناسبة حسب نوعه، مع حفظ بياناته في Supabase.\n\n"
        "🔍 ابحث عن أي ملف بالاسم أو تصفّح الفئات، ثم اضغط عليه لإرساله فوراً.\n"
        "🗑 يمكنك حذف أي ملف من المكتبة نهائياً في أي وقت.\n\n"
        "<i>الحد الأقصى لحجم الملف الواحد يتبع حدود تيليغرام: ٢ غيغابايت "
        "للبوت العادي و٤ غيغابايت لحساب بريميوم.</i>"
    )


def help_text(settings: Settings) -> str:
    lines = [
        "<b>❓ دليل الاستخدام</b>",
        DIVIDER,
        "<b>الأوامر</b>",
        "/start — القائمة الرئيسية",
        "/search — البحث بالاسم",
        "/library — تصفح المكتبة حسب الفئة",
        "/stats — إحصائيات التخزين",
        "/help — هذه المساعدة",
        "/cancel — إلغاء العملية الحالية",
        "",
        "<b>الرفع</b>",
        "أرسل الملف كمستند (Document) للحفاظ على اسمه الأصلي — الصور والفيديو "
        "والصوتيات تُقبل أيضاً.",
        "",
        "<b>التوجيه التلقائي</b>",
    ]
    for category in FileCategory:
        lines.append(f"• {CATEGORY_LABELS[category]} ← مجموعة/قناة مخصصة")
    lines += [
        "",
        "<b>البحث</b>",
        "اكتب حرفين على الأقل من اسم الملف، أو صفِّ النتائج حسب الفئة من الأزرار.",
        "",
        f"<b>الفئات المتاحة:</b> {len(FileCategory)}",
    ]
    if settings.allowed_users:
        lines += ["", f"🔐 البوت مخصص لـ {len(settings.allowed_users)} مستخدم/مستخدمين."]
    return "\n".join(lines)


def file_card(record: StoredFile) -> str:
    return (
        f"<b>📄 {escape(truncate(record.file_name, 70))}</b>\n"
        f"{DIVIDER}\n"
        f"الفئة: {CATEGORY_LABELS[record.category]}\n"
        f"الحجم: {record.size_human}\n"
        f"تاريخ الرفع: {record.created_human}\n"
        f"نوع MIME: <code>{escape(record.mime_type or 'غير معروف')}</code>\n"
        f"المعرّف: <code>{record.id}</code>"
    )


def categories_text(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    lines = [f"<b>📂 المكتبة · {total} ملف</b>", DIVIDER]
    for category in FileCategory:
        lines.append(f"{CATEGORY_LABELS[category]}: <b>{counts.get(category.value, 0)}</b>")
    lines += ["", "اختر فئة لعرض ملفاتها."]
    return "\n".join(lines)


def search_text(page: FilePage, query: str | None, category: str | None) -> str:
    header = f"🔍 <b>نتائج البحث عن:</b> <i>{escape(query)}</i>" if query else "📂 <b>المكتبة</b>"
    if category:
        header += f"\nالفئة: {CATEGORY_LABELS[parse_category(category)]}"
    lines = [header, DIVIDER, f"النتائج: <b>{page.total}</b> · الصفحة {page.page}/{page.pages}"]
    if not page.items:
        lines += ["", "لا توجد ملفات مطابقة. جرّب كلمات أقل أو فئة أخرى."]
    else:
        lines += ["", "اضغط على الملف لعرض تفاصيله وإرساله."]
    return "\n".join(lines)


def stats_text(stats: LibraryStats) -> str:
    total_size = human_size(stats.total_bytes) if stats.total_bytes is not None else "غير متاح"
    lines = [
        "<b>📊 إحصائيات التخزين</b>",
        DIVIDER,
        f"إجمالي الملفات: <b>{stats.total}</b>",
        f"إجمالي الحجم: <b>{total_size}</b>",
        "",
    ]
    for category in FileCategory:
        count = stats.counts.get(category.value, 0)
        share = (count / stats.total * 100) if stats.total else 0
        lines.append(f"{CATEGORY_LABELS[category]}: <b>{count}</b> ({share:.0f}%)")
    return "\n".join(lines)


def saved_text(record: StoredFile) -> str:
    return f"✅ <b>تم حفظ الملف بنجاح</b>\n{DIVIDER}\n{file_card(record)}"


def delete_prompt_text(record: StoredFile) -> str:
    return (
        "⚠️ <b>تأكيد الحذف</b>\n"
        f"{DIVIDER}\n"
        "سيُحذف هذا الملف نهائياً من Supabase ومن مجموعة التخزين:\n"
        f"<b>{escape(truncate(record.file_name, 60))}</b>"
    )


def deletion_text(record: StoredFile) -> str:
    return (
        f"🗑 <b>تم حذف الملف نهائياً</b>\n"
        f"{DIVIDER}\n"
        f"{escape(truncate(record.file_name, 70))}\n"
        "أُزيل السجل من Supabase والنسخة من مجموعة التخزين."
    )


async def show(
    callback: CallbackQuery,
    text: str,
    keyboard: InlineKeyboardMarkup | None = None,
) -> None:
    """Edit the current screen, falling back to a new message when needed."""
    if callback.message is None:
        return
    await safe_edit_text(callback.message, text, keyboard)
