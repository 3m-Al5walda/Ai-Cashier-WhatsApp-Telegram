# AI WhatsApp Cashier

كاشير مطعم على واتساب يفهم العربية واللهجة الأردنية، ويجاوب **فقط** من `data/menu.json`
(الأسعار، الأحجام، التوفر، الاختيارات). أي شي مش بالمنيو ما بيخترعه.

## كيف يشتغل
```
رسالة الزبون ─► أوامر قطعية (تأكيد/إلغاء/ملخص/منيو/تحية)  ──► رد مباشر بدون LLM
              └► جواب على سؤال معلّق (حجم/نوع رز/نوع خبز)   ──► بدون LLM
              └► LLM (Groq) ينتج CustomerTurn منظّم  ──(فشل؟)──► rule_parser كبديل
                        │
                        ▼
        OrderConversation يتحقق من كل اسم/حجم/اختيار عبر MenuSearcher (menu.json)
```
- `app/services/menu_searcher.py` — إيجاد المنتج والحجم (تطبيع عربي، aliases، لا يخمّن عند الغموض).
- `app/services/choices.py` — يحوّل جمل `required_choices` في المنيو إلى خيارات منظمة (نوع الرز، نوع الخبز…)؛ لا شي ثابت بالكود.
- `app/services/order_conversation.py` — السلة، الأسئلة الناقصة، التوصيل/الاستلام، التأكيد. الأسئلة (سعر/توفر/منيو) لا تغيّر السلة.
- `app/bot.py` — الجلسات (TTL)، حفظ الطلب المؤكد، نفس المنطق للـ CLI والواتساب.
- `data/aliases.json` — تهجئات ولهجات فقط (زنقر، سندويش، رز…). أضف هون كلماتك.

## التشغيل (Windows PowerShell)
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env      # ضع GROQ_API_KEY
python -m pytest -q
python -m app.main               # تجربة محلية
```
أمثلة: `بدي 2 زنجر سوبر وشاورما دبل توصيل` · `كم سعر الفاهيتا؟` · `في مندي؟` · `شيل الزنجر` · `تأكيد`

## واتساب
```powershell
uvicorn app.whatsapp_webhook:app --host 0.0.0.0 --port 8000
```
Callback: `https://YOUR_HOST/webhook` + نفس `WHATSAPP_VERIFY_TOKEN` + اشتراك بحقل `messages`.
**ضع `WHATSAPP_APP_SECRET`** قبل النشر (يتحقق من توقيع كل طلب). المعالجة بالخلفية وبتتجاهل التكرار (Meta بتعيد الإرسال).

## الطلبات المؤكدة
تُحفظ في `data/orders.jsonl` (سطر JSON لكل طلب، فيه رقم الطلب ورقم الزبون)، ولو حطيت `KITCHEN_WEBHOOK_URL`
بتنرسل كمان بـ POST للمطبخ/الـ POS.

## حدود معروفة
- الجلسات بالذاكرة (عملية واحدة). للإنتاج مع أكثر من worker استخدم Redis.
- الرسائل الصوتية/الصور غير مدعومة (الزبون بيجيه رد يطلب نص). الموقع (location) مدعوم.
- المنيو ما فيه ساعات دوام/رسوم توصيل/دفع: هالأسئلة بتتحول للموظف (`needs_human` يُسجَّل بالـ log) ورسوم التوصيل ما بتنحسب.
- جودة فهم اللهجة تعتمد على النموذج؛ كل شي بيتحقق منه مقابل المنيو ويُعرض للزبون للتأكيد.
- اختبارات الـ LLM تستخدم نموذجاً وهمياً (بدون استهلاك رصيد). جرّب على رسائل حقيقية قبل الإطلاق.

## مراجعة v5 — إصلاحات إضافية
- إضافة تهجئات شائعة لزنجر (`زنغر` و`سنجر`) إلى `data/aliases.json` مع إبقاء المنيو هو مصدر الحقيقة.
- رفع حد الاقتراحات عند عدم العثور على منتج من 45 إلى 60 حتى لا تظهر اقتراحات بعيدة مثل اقتراح الفاهيتا عند طلب بيتزا.
- إصلاح تكرار طلب صنف لم يحدد حجمه بعد: تكرار `بدي زنجر` يزيد الكمية بدل تجاهل الرسالة.
- إضافة اختبارات regression لهذه الحالات. الاختبارات المحلية: `39 passed`.

## قبل اعتباره جاهزاً للإنتاج
هذه النسخة محسّنة ومختبرة محلياً، لكنها ليست إثباتاً على جاهزية الإنتاج. يلزم اختبارها مع نموذج Groq الحقيقي ورسائل أردنية متنوعة، وإكمال/اختبار تكامل واتساب على حساب Meta فعلي. كما أن تحويل الحالات للموظف يسجَّل حالياً في السجلات فقط، والجلسات داخل الذاكرة لا تناسب تشغيل عدة workers، ولا يوجد ربط فعلي بمخزون المطبخ في هذه النسخة.

## Telegram

1. Create a bot with `@BotFather` using `/newbot` and copy the token.
2. Add `TELEGRAM_BOT_TOKEN=...` to `.env` (never commit `.env`).
3. Install dependencies: `python -m pip install -r requirements.txt`.
4. Run locally: `python -m app.telegram_bot`.
5. For 24/7 operation, deploy as a continuously running worker/service on an always-on host. This adapter uses Telegram long polling, so it does not need a public webhook URL. Keep one running instance only because conversation sessions are currently in memory.

The Telegram adapter currently handles private text messages. Voice notes and images are not transcribed. Test with a real Telegram bot and a real `GROQ_API_KEY` before sharing it publicly.
