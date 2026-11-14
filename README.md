# 🗄 TG Space Bot

بوت تيليغرام لإدارة الملفات والتخزين السحابي، مبني بلغة **Python** و **aiogram 3** بشكل غير متزامن بالكامل.
تُستخدم **مجموعات تيليغرام خاصة** كخلفية تخزين فيزيائية (لا تحميل ولا رفع إلى خوادم وسيطة)، بينما تُحفظ
البيانات الوصفية في **Supabase (PostgreSQL)**.

---

## ✨ المزايا

| الميزة | التفاصيل |
| --- | --- |
| رفع تلقائي | كل ملف يُرسل للبوت يُنسخ مباشرة إلى مجموعة التخزين المخصصة لفئته |
| توجيه ذكي | التصنيف عبر نوع محتوى تيليغرام + MIME + الامتداد (مستندات، صور/فيديو، صوتيات، مضغوطات، أخرى) |
| بحث سريع | بحث بالاسم (ILIKE + فهرس trigram) مع تصفية حسب الفئة وترقيم صفحات |
| وصول فوري | نقرة واحدة على النتيجة تُعيد إرسال الملف نفسه من مجموعة التخزين |
| إدارة كاملة | عرض التفاصيل، الإرسال، والحذف النهائي (سجل Supabase + رسالة مجموعة التخزين) |
| واجهة عربية | لوحة مفاتيح ثابتة + أزرار Inline + قائمة أوامر مسجّلة في تيليغرام |
| صلابة | احترام `retry_after`، إعادة محاولة أخطاء الشبكة، معالجة ترحيل المجموعة إلى Supergroup |
| حماية | قائمة مستخدمين مسموح لهم (اختيارية) + RLS مُفعّل على الجدول |

---

## 📁 هيكل المشروع

```
.
├── main.py                       نقطة التشغيل (تهيئة الخدمات وبدء polling)
├── requirements.txt
├── .env.example
├── migrations/
│   └── 001_init_files.sql        سكربت إنشاء الجدول والفهارس والـ trigger
├── scripts/
│   └── init_db.py                تطبيق الـ migrations على Postgres
└── src/
    ├── core/
    │   ├── config.py             إعدادات Pydantic من البيئة
    │   └── logging.py
    ├── db/
    │   ├── models.py             نموذج StoredFile
    │   └── repository.py         عميل Supabase REST غير متزامن (httpx)
    ├── services/
    │   ├── storage.py            النسخ/الإرسال/الحذف داخل مجموعات تيليغرام
    │   ├── files.py              منطق الأعمال (رفع، بحث، حذف، إحصائيات)
    │   └── container.py          حاوية التبعيات
    ├── utils/
    │   ├── filetypes.py          تصنيف الملفات واستخراجها من رسائل تيليغرام
    │   ├── formatters.py         تنسيق الأحجام والتواريخ و HTML
    │   └── telegram.py           إعادة المحاولة وتعديل الرسائل بأمان
    └── bot/
        ├── dispatcher.py         ربط الوسطاء والراوترات
        ├── middlewares.py        الصلاحيات + حقن الخدمات
        ├── keyboards.py          كل الأزرار و CallbackData
        ├── states.py
        ├── views.py              نصوص كل الشاشات
        └── routers/
            ├── commands.py       /start /help /cancel والقائمة
            ├── upload.py         استقبال الملفات ورفعها
            ├── search.py         البحث والتصفية والترقيم
            ├── browse.py         الفئات، التفاصيل، الإرسال، الحذف
            └── fallback.py       الرسائل غير المدعومة
```

---

## 🚀 التجهيز

### 1) المتطلبات

* Python **3.11+**
* بوت من [@BotFather](https://t.me/BotFather)
* مشروع Supabase
* خمس مجموعات (أو قنوات) تيليغرام خاصة، والبوت **مشرف** فيها

### 2) تثبيت الحزم

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3) إنشاء مجموعات التخزين

أنشئ المجموعات التالية وأضف البوت كعضو مع صلاحية **نشر/حذف الرسائل**:

| المتغير | تُخزَّن فيه |
| --- | --- |
| `STORAGE_CHAT_DOCUMENTS` | PDF, Word, Excel, نصوص, أكواد… |
| `STORAGE_CHAT_MEDIA` | الصور والفيديو (بما فيها GIF و Video Note) |
| `STORAGE_CHAT_AUDIO` | MP3, WAV, صوتيات ورسائل صوتية |
| `STORAGE_CHAT_ARCHIVES` | ZIP, RAR, 7z, TAR… |
| `STORAGE_CHAT_DEFAULT` | أي نوع غير معروف |

معرّف المجموعة يبدأ بـ `-100…`، ويمكن الحصول عليه بإضافة البوت ثم فتح
`https://api.telegram.org/bot<TOKEN>/getUpdates` بعد إرسال رسالة في المجموعة، أو عبر بوتات مثل
`@username_to_id_bot`.

### 4) تهيئة Supabase

1. من **Project Settings → API** انسخ `Project URL` و `service_role` key.
2. طبّق المخطط (أحد الخيارين):

```bash
# الطريقة الأولى: سكربت جاهز (يحتاج Connection String من Project Settings → Database)
export SUPABASE_DB_URL="postgresql://postgres:<PASSWORD>@db.<REF>.supabase.co:5432/postgres"
python -m scripts.init_db
```

```text
# الطريقة الثانية: Supabase Dashboard → SQL Editor → الصق migrations/001_init_files.sql → Run
```

الجدول الناتج `public.files` يحتوي:
`id (uuid)`, `file_id`, `file_unique_id`, `file_name`, `category`, `mime_type`,
`size_bytes`, `uploader_id`, `storage_chat_id`, `storage_message_id`,
`created_at`, `updated_at` — مع فهارس على التاريخ والفئة وبحث trigram على الاسم.

### 5) الإعدادات

```bash
cp .env.example .env
```

املأ `BOT_TOKEN` و `SUPABASE_URL` و `SUPABASE_KEY` ومعرّفات المجموعات.
`ALLOWED_USER_IDS` اختياري (مفصول بفواصل، فارغ = الجميع).

### 6) التشغيل

```bash
python main.py
```

عند الإقلاع يسجّل البوت بياناته، ويسجّل الأوامر في واجهة تيليغرام، ويتحقق من الوصول إلى كل مجموعة تخزين
(رسالة خطأ واضحة إذا لم يكن مشرفاً).

---

## 🧭 طريقة الاستخدام

| الإجراء | الطريقة |
| --- | --- |
| حفظ ملف | أرسله في المحادثة الخاصة مع البوت (كمستند للحفاظ على الاسم الأصلي) |
| البحث | زر «🔍 بحث» أو `/search` ثم اكتب حرفين على الأقل |
| التصفية | أزرار الفئات أسفل نتائج البحث |
| تصفح المكتبة | زر «📂 مكتبتي» أو `/library` |
| إرسال ملف | افتح الملف من نتيجة البحث ثم «⬇️ إرسال الملف» |
| حذف نهائي | «🗑 حذف الملف» ثم تأكيد |
| الإحصائيات | زر «📊 الإحصائيات» أو `/stats` |

---

## 🩺 ملاحظات تشغيلية

* **الأحجام الكبيرة:** البوت لا ينزّل الملفات، بل يستخدم `copy_message`، لذا يصلح لملفات حتى
  2 GB (4 GB لحساب بريميوم) دون استهلاك ذاكرة أو قرص.
* **حدود المعدل:** كل استدعاءات تيليغرام تمر عبر `call_with_retry` الذي يحترم `retry_after`
  ويعيد المحاولة مع تراجع أسّي لأخطاء الشبكة.
* **الحذف:** إذا كانت الرسالة الأصلية محذوفة يدوياً من المجموعة، يُحذف السجل من Supabase ويُبلَّغ
  المستخدم بأن الملف لم يعد متاحاً.
* **الأمان:** جدول `files` عليه RLS بدون سياسات، أي أن مفتاح `anon` لا يستطيع قراءته؛
  البوت يستخدم `service_role` فقط على الخادم.
* **التشغيل الدائم:** استخدم `systemd` أو `supervisor` أو أي مدير عمليات:

```ini
[Unit]
Description=TG Space Bot
After=network-online.target

[Service]
WorkingDirectory=/opt/tg-space-bot
ExecStart=/opt/tg-space-bot/.venv/bin/python main.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/tg-space-bot/.env

[Install]
WantedBy=multi-user.target
```

---

## ⚠️ ملاحظات

لا تُرفع ملف `.env` إلى Git؛ فهو مستبعد عبر `.gitignore`. راجع المفاتيح دورياً، واستخدم
`service_role` فقط في بيئة الخادم الموثوقة.
