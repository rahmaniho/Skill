# راهنمای استقرار ابری — Vercel + Supabase + موتور واقعی در Codespaces

این سند «راهنمای عملیاتی» برنامه است: چطور از یک مخزن کد به یک سامانهٔ زنده برسید که
دادهٔ **واقعی** گوگل‌مپ را می‌گیرد، در پایگاه‌دادهٔ ابری نگه می‌دارد و گزارش لیدهای داغ را
**با ایمیل** برایتان می‌فرستد.

> خلاصهٔ معماری:
>
> ```
>  کاربر ──▶  Vercel (رابط فارسی + API)  ──▶  Supabase (Postgres: لیدها، صف، گزارش‌ها)
>                                                        ▲
>                                                        │ نوشتن نتیجهٔ اجرا
>              Codespaces ── docker ──▶ gosom/google-maps-scraper (موتور واقعی)
>                     └── leadgen.worker  (صف را برمی‌دارد، غنی‌سازی/امتیازدهی، سپس ایمیل)
>                                                        │
>                                                        ▼
>                                                 Gmail (گزارش + پیش‌نویس تماس)
> ```
>
> چرا سه تکه؟ چون تابع serverless روی Vercel نمی‌تواند چند دقیقه باز بماند و داکر هم ندارد؛
> پس **صف** کار را می‌سپارد به کسی که کنار موتور واقعی نشسته است. پایگاه‌دادهٔ مشترک
> باعث می‌شود همه یک داده را ببینند.
>
> **تازه (نسخهٔ زنده):** برای کار کردن اپ، ورکر دیگر **اجباری نیست**. سه حالت پشتیبانی می‌شود:
>
> | حالت | چه کسی صف را اجرا می‌کند | لازم دارد |
> |---|---|---|
> | `SELF_DRIVE=1` (پیش‌فرض) | خود رابط با `POST /api/worker/tick` | هیچ‌چیز — موتور `osm` بدون کلید |
> | Google Places | همان tick، با موتور رسمی گوگل | `PLACES_API_KEY` |
> | ورکر بیرونی | `leadgen.worker` در Codespace/Actions/VPS | داکر برای دادهٔ غنی‌تر |
>
> اگر ورکر بیرونی روشن باشد، tick خودش کنار می‌رود (`worker_online: true`).

---

## ۰) آنچه هم‌اکنون آماده است

| بخش | وضعیت | نشانی / شناسه |
| --- | --- | --- |
| پایگاه‌دادهٔ Supabase | ساخته و schema نصب شده | پروژهٔ `leadgen-ops` → ref `pavjktfopfgqdvdntzit` (منطقهٔ `eu-central-1`) |
| جدول‌ها و توابع SQL | ۴ جدول + ۴۷ تابع (`search_leads`, `save_run`, `claim_job`, …) | `supabase/schema.sql` |
| هم‌ارزی جست‌وجو SQLite ↔ Postgres | **هر ۱۳ سناریو تأیید شد** | `python3 scripts/cloud_parity.py` |
| اپ Vercel | کد و پیکربندی آماده (`api/index.py`, `vercel.json`) | در انتظار استقرار + متغیرهای محیطی |
| موتور در Codespaces | اسکریپت‌ها آماده (`.devcontainer/*`, `scripts/engine_proxy.py`) | باید یک Codespace بسازید |
| ایمیل (Gmail) | ماژول + مسیرهای API + راهنمای توکن | `scripts/gmail_token.py` |
| آزمون‌ها | سبز | `python3 -m unittest discover -s tests` |

---

## ۱) Supabase — پایگاه‌دادهٔ مشترک

### ۱٫۱ ساخت پروژه (یک‌بار)
1. در <https://supabase.com/dashboard> → **New project**.
2. نام (مثلاً `leadgen-ops`)، رمز پایگاه‌داده (حداقل ۶ نویسه — یادداشتش کنید)، منطقهٔ نزدیک
   به کاربر (برای ایران/اروپا: `eu-central-1` فرانکفورت)، پلن **Free** کافی است.
3. صبر کنید تا وضعیت `ACTIVE_HEALTHY` شود (~۲ دقیقه).

### ۱٫۲ نصب طرح پایگاه‌داده (یک‌بار)
1. منوی **SQL Editor** → **New query**.
2. همهٔ محتوای `supabase/schema.sql` را بچسبانید → **Run**.
   - این فایل idempotent است (`create … if not exists` / `create or replace`)؛ اجرای دوباره
     ضرر ندارد.
3. بررسی: **Table Editor** باید `runs`, `leads`, `jobs`, `deliveries` را نشان دهد.

> **RLS:** روی جدول‌ها روشن است و **هیچ policy‌ای تعریف نشده** → از مرورگر/کلید عمومی چیزی
> خوانده نمی‌شود. اپ و ورکر با کلید `service_role` کار می‌کنند. اگر روزی خواستید کلید `anon`
> مستقیم بخواند، باید policy بنویسید؛ توصیه نمی‌شود (دادهٔ تماس، شخصی است).

### ۱٫۳ کلیدها
از **Project Settings → API**:
- `Project URL` → متغیر `SUPABASE_URL`
- `service_role` (محرمانه) → متغیر `SUPABASE_SERVICE_KEY`
- `anon public` → فقط اگر لازم شد، `SUPABASE_ANON_KEY`

### ۱٫۴ بررسی اتصال از همان ماشین
```bash
export SUPABASE_URL="https://<ref>.supabase.co"
export SUPABASE_SERVICE_KEY="<service_role>"
python3 -m leadgen.cli doctor        # باید backend: supabase را نشان دهد
python3 scripts/cloud_parity.py --live --apply   # فیکسچر ۸ لیدی را در ابر می‌نویسد
python3 scripts/cloud_parity.py --live           # هم‌ارزی SQLite ↔ Postgres را می‌سنجد
python3 scripts/cloud_parity.py --live --cleanup # فیکسچر آزمون را پاک می‌کند (اختیاری)
```

---

## ۲) Vercel — اپ و رابط کاربری

### ۲٫۱ ساخت پروژه
1. <https://vercel.com/new> → **Import** همان مخزن GitHub (`rahmaniho/Skill`).
2. Framework: **Other** (نیاز به build نیست). Root Directory: ریشهٔ مخزن.
3. Vercel خودش `vercel.json` را می‌خواند: یک تابع پایتون (`api/index.py`) با
   `maxDuration=60s` و بازنویسی همهٔ مسیرها به همان تابع:

   ```json
   { "source": "/:path*", "destination": "/api/index?__lg_path=/:path*" }
   ```

   یعنی رابط‌های وب و همهٔ `/api/*` از **یک کد مسیریابی** سرو می‌شوند (همان
   `leadgen/server.py`) — هیچ تفاوت رفتاری بین محلی و ابر نیست.

   > **چرا `__lg_path`؟** بازنویسی داخلی، مسیرِ *مقصد* را به تابع می‌دهد؛ پس تابع همیشه
   > `PATH_INFO=/api/index` می‌بیند و اگر مسیر واقعی همراهش نیاید، **هر صفحه‌ای ۴۰۴ می‌شود**.
   > هیچ هدر مستندی مسیرِ پیش از بازنویسی را حمل نمی‌کند، برای همین خودِ بازنویسی آن را در
   > پارامتر `__lg_path` می‌فرستد و `leadgen/wsgi.py` پیش از مسیریابی آن را به `PATH_INFO`
   > برمی‌گرداند (و از کوئری پاک می‌کند). این کار در اجرای محلی بی‌اثر است.

### ۲٫۲ متغیرهای محیطی (Project → Settings → Environment Variables)
حداقل‌ها برای کار واقعی:

| متغیر | مقدار | چرا |
| --- | --- | --- |
| `SUPABASE_URL` | `https://<ref>.supabase.co` | پایگاه‌دادهٔ مشترک با ورکر |
| `SUPABASE_SERVICE_KEY` | کلید `service_role` | خواندن/نوشتن از راه PostgREST |
| `LEADGEN_QUEUE` | `always` | تابع serverless نباید منتظر اجرا بماند؛ کار به صف می‌رود |
| `LEADGEN_DATA_DIR` | `/tmp/leadgen-data` | تنها مسیر نوشتنی روی Vercel |
| `APP_BASE_URL` | `https://<app>.vercel.app` | لینک‌های داخل ایمیل |
| `DIGEST_TO` | `you@gmail.com,sales@…` | گیرندهٔ پیش‌فرض گزارش |
| `GMAIL_REFRESH_TOKEN` | از `scripts/gmail_token.py` | ارسال با Gmail API |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | از کنسول گوگل | همان |

اختیاری: `LEADGEN_ROOT_UI` (`nova` پیش‌فرض — رابط نسل بعد؛ یا `smart` / `mini`)، `DIGEST_MAX_LEADS`.

**برای دادهٔ واقعی بدون داکر** (پیشنهادی برای شروع):
`SELF_DRIVE=1` (پیش‌فرض) + یکی از این دو:
- هیچ‌چیز! موتور `osm` بی‌کلید کار می‌کند (ENGINE_MODE=auto خودش انتخابش می‌کند).
- یا `PLACES_API_KEY=...` برای Google Places API (ستاره/نظر/دقت بالاتر).

**قفل کردن نسخهٔ عمومی:** `LEADGEN_ACCESS_CODE=...` را در Vercel → Settings → Environment
Variables بگذارید (در `vercel.json` نگذارید؛ آن فایل عمومی است). سهمیه هم با
`LEADGEN_MAX_PENDING` و `LEADGEN_RATE_RUNS` تنظیم می‌شود.

> **چرا Gmail API و نه SMTP؟** خروجی SMTP (پورت ۵۸۷) از توابع serverless معمولاً بسته است؛
> Gmail API روی HTTPS کار می‌کند. مسیر SMTP (`GMAIL_APP_PASSWORD`) برای اجرای محلی/Codespace
> ساده‌ترین راه است و برنامه خودش اولویت را این‌طور می‌چیند:
> `gmail_api` → `smtp` → `none` (فقط ساخت فایل `.eml`).

### ۲٫۳ بررسی پس از استقرار
```bash
curl -s https://<app>.vercel.app/api/health | python3 -m json.tool
# انتظار: ok:true و db.backend: "supabase"
curl -s https://<app>.vercel.app/api/queue | python3 -m json.tool
# انتظار: mode:"always", active:true, backend:"supabase"
curl -s https://<app>.vercel.app/api/delivery/status | python3 -m json.tool
# انتظار: transport:"gmail_api", configured:true
```
سپس در مرورگر: `/` (رابط ساده)، `/studio/` (داشبورد حرفه‌ای)، `/mini/` (نسخهٔ جمع‌وجور).

> **Deployment Protection:** داده‌ها شخصی‌اند. در Vercel یا «Deployment Protection» را روشن
> نگه دارید یا دامنهٔ عمومی ندهید.

---

## ۳) Codespaces — موتور واقعی + ورکر

این لایه همان جایی است که **دادهٔ واقعی گوگل‌مپ** تولید می‌شود. اگر داکر روی لپ‌تاپ
ندارید، Codespaces داکر دارد.

### ۳٫۱ ساخت Codespace
1. در صفحهٔ مخزن GitHub → **Code** → **Codespaces** → **Create codespace on main**.
2. `.devcontainer/devcontainer.json` به‌طور خودکار:
   - داکر-داخل-داکر را نصب می‌کند،
   - `postCreateCommand` → `bash .devcontainer/setup.sh` را اجرا می‌کند (ساخت `.env`،
     بالا آوردن موتور با `docker compose up -d`، بررسی اتصال Supabase، اجرای آزمون‌ها)،
   - `postStartCommand` → `bash .devcontainer/start-worker.sh` (ورکر صف در پس‌زمینه).

### ۳٫۲ اگر دستی اجرا می‌کنید
```bash
cp .env.example .env      # و کلیدهای Supabase/Gmail را بگذارید
bash .devcontainer/setup.sh
docker compose up -d && curl -s http://127.0.0.1:8080/api/v1/jobs   # موتور واقعی
bash .devcontainer/start-worker.sh
python3 -m leadgen.worker --status        # وضعیت صف و موتور
tail -f data/logs/worker.log              # لاگ زندهٔ ورکر
```

ورکر چه می‌کند؟ `claim_job` (با `FOR UPDATE SKIP LOCKED` — چند ورکر هم‌زمان امن است) →
اجرای موتور → یکتاسازی/غنی‌سازی/امتیازدهی → `save_run` + `save_leads` در Supabase →
`heartbeat_job` برای پیشرفت زنده → در صورت شکست، تا `max_attempts` (پیش‌فرض ۳) دوباره در
صف؛ و اگر `--deliver` روشن باشد (پیش‌فرض در `start-worker.sh`) بعد از هر اجرای موفق گزارش
ایمیلی می‌فرستد.

### ۳٫۳ دسترسی به موتور از بیرون Codespace (اختیاری)
موتور کیت **هیچ احراز هویتی ندارد** → در `docker-compose.yml` روی `127.0.0.1` بسته شده.
اگر می‌خواهید ورکری جای دیگر (یا لپ‌تاپ) از آن استفاده کند، پراکسی کلیددار بگذارید:

```bash
python3 scripts/engine_proxy.py --generate-key      # یک کلید تصادفی چاپ می‌کند
python3 scripts/engine_proxy.py --port 8081 --api-key "<کلید>"
# در .env همان ماشین:
#   SCRAPER_BASE_URL=https://<codespace>-8081.app.github.dev
#   SCRAPER_API_KEY=<کلید>
```
پراکسی `/healthz` بدون کلید پاسخ می‌دهد (برای سلامت‌سنجی) و بقیهٔ مسیرها را فقط با
`X-API-Key` یا `Authorization: Bearer` عبور می‌دهد؛ کلید را به موتور نمی‌فرستد. برای بیرون
از Codespaces حتماً از forward port با HTTPS خودِ Codespaces استفاده کنید، نه پورت خام.

### ۳٫۴ چند نکتهٔ موتور (از skill کیت)
- `lat`/`lon` باید **رشته** باشند و `max_time` به **ثانیه** — وگرنه موتور `422` می‌دهد.
- وضعیت کار با فیلد `"Status"` خوانده می‌شود: `working` → `ok` | `failed`.
- برای پوشش کل شهر از کاشی‌بندی استفاده کنید (`grid=3`) و هم‌زمان بیش از یک job سنگین
  نفرستید؛ برای حجم بالا `SCRAPER_PROXIES` را پر کنید.
- خروجی خام ۳۴ ستون دارد؛ یکتاسازی روی `place_id` (و بعد `cid`/`link`) انجام می‌شود و
  ردیف‌های بیرون از محدوده دور ریخته می‌شوند.

---

## ۴) Gmail — تحویل خروجی

دو نوع خروجی ساخته می‌شود:
1. **گزارش لیدهای داغ** (`POST /api/delivery/digest` یا `python3 -m leadgen.delivery --digest`)
   — ایمیل RTL با KPIها، بینش‌ها، جدول لیدهای داغ و **پیوست اکسل**.
2. **پیش‌نویس تماس** برای هر لید (`POST /api/delivery/drafts`) — در Gmail به‌صورت Draft
   ساخته می‌شود تا خودتان بازبینی و ارسال کنید (بدون اسپم).

### ۴٫۱ مسیر A — Gmail API (برای Vercel/ابر)
1. <https://console.cloud.google.com> → یک پروژه بسازید → **APIs & Services → Library** →
   «Gmail API» را فعال کنید.
2. **OAuth consent screen** را بسازید (External، در حالت Testing ایمیل خودتان را اضافه کنید).
3. **Credentials → Create credentials → OAuth client ID → Desktop app** →
   `client_id` و `client_secret` را در `.env` بگذارید.
4. توکن بلندمدت بگیرید:
   ```bash
   python3 scripts/gmail_token.py --port 8085        # مرورگر باز می‌شود؛ دسترسی بدهید
   # در Codespaces/سرور بدون مرورگر:
   python3 scripts/gmail_token.py --manual --no-browser --redirect-uri http://localhost:8085
   python3 scripts/gmail_token.py --test             # توکن را آزمایش می‌کند
   python3 scripts/gmail_token.py --send-test you@gmail.com
   ```
   خروجی `GMAIL_REFRESH_TOKEN=…` را در `.env` (محلی) و در Vercel (Environment Variables)
   بگذارید. Scopes: `gmail.send` و `gmail.compose`.

### ۴٫۲ مسیر B — SMTP با «رمز برنامه» (محلی/Codespaces)
Gmail → **Security** → 2-Step Verification → **App passwords** → یک رمز ۱۶ نویسه:
```bash
GMAIL_SENDER=you@gmail.com
GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
DIGEST_TO=you@company.com
```

### ۴٫۳ بازبینی بدون ارسال
```bash
LEADGEN_DELIVERY_DRY=1 python3 -m leadgen.delivery --digest --limit 5
# فایل .eml در data/exports/ ساخته می‌شود (با پیوست اکسل) — چیزی ارسال نمی‌شود
LEADGEN_DELIVERY_DRY=1 python3 -m leadgen.delivery --drafts --limit 3
python3 -m leadgen.delivery --preview      # data/exports/digest-preview.html
```
در رابط‌ها هم همین گزینه هست: داشبورد `/studio/` → تب **«صف و ارسال»** → تیک
«فقط پیش‌نمایش».

> **رعایت قانون:** ایمیل و تلفن دادهٔ شخصی‌اند. هویت فرستنده، مبنای قانونی و گزینهٔ
> «لغو دریافت» را در هر پیام نگه دارید (GDPR / CCPA / CAN-SPAM). لیدها را «نیازمند
> تأیید» بدانید و دادهٔ خام را نفروشید.

---

## ۵) اولین اجرای واقعی (چک‌لیست پایان‌به‌پایان)

1. Vercel: `/api/health` → `db.backend: supabase`.
2. Codespace: ورکر در حال اجرا (`python3 -m leadgen.worker --status`) و موتور واقعی
   (`curl -s http://127.0.0.1:8080/api/v1/jobs`).
3. در `/` بنویسید: **«دندانپزشکی در تهران»** → «شروع کن».
   - پاسخ `202 queued` می‌آید و بنر «⏳ در صف» جای شما در صف و زمان انتظار را نشان می‌دهد.
4. در Codespace لاگ ورکر را ببینید؛ در UI پیشرفت زنده از `heartbeat_job` به‌روز می‌شود.
5. پس از «تکمیل شد»: لیدها با نام/نشانی/تلفن/سایت/شبکه‌های اجتماعی/**lat-lon** و امتیاز.
6. داشبورد `/studio/` → تب «صف و ارسال» → «📧 گزارش لیدهای داغ» (اول با تیک پیش‌نمایش،
   بعد بردارید و واقعی بفرستید).
7. راستی‌آزمایی پایگاه: `GET /api/runs` و `GET /api/search?q=دندانپزشکی&has_email=true`.

---

## ۶) نگهداری و عملیات

```bash
# صف و کارها
python3 -m leadgen.worker --status
python3 -m leadgen.worker --once --engine docker      # یک دور (مناسب cron)
curl -s "$APP/api/queue/jobs?status=failed"

# کارهای گیرکرده (ورکر مرده) دوباره به صف می‌روند
#   requeue_stale_jobs(seconds) — پیش‌فرض ورکر: ۱۸۰۰ ثانیه

# هم‌ارزی موتورهای داده (بعد از هر تغییر در جست‌وجو/امتیازدهی)
python3 scripts/cloud_parity.py                # آفلاین، بدون شبکه (CI هم همین را می‌زند)
python3 scripts/cloud_parity.py --live         # در برابر Supabase واقعی
python3 scripts/cloud_parity.py --rebuild      # وقتی عمدی تغییر دادید: انتظارها را بسازید و commit کنید

# سلامت منابع داده (سریع: فقط کش) و سنجش واقعی
curl -s "$APP/api/health" | python3 -m json.tool | grep -A3 '"overpass"'
curl -s "$APP/api/health?fresh=1" | python3 -m json.tool | grep -A3 '"overpass"'

# «همین حالا، همین شهر، همین صنف واقعاً نتیجه می‌دهد؟» (بدون نوشتن در پایگاه‌داده)
curl -s "$APP/api/diag/osm?city=تهران&niche=دندانپزشکی" | python3 -m json.tool
curl -s "$APP/api/diag/osm?city=اوز&all=1"                  # جاروی همهٔ اصناف
curl -s "$APP/api/diag/osm?city=مشهد&niche=کافه&deep=1"     # با شکستن محدودهٔ شلوغ

# آزمون‌ها
python3 -m unittest discover -s tests
python3 scripts/check_deploy_config.py         # سازگاری vercel.json/api/index.py/WSGI

# پاک‌سازی داده
curl -X DELETE "$APP/api/runs/<run-id>"        # اجرا + لیدهایش
# یا در SQL Editor:  select public.delete_run('<run-id>');
```

**تفاوت پذیرفته‌شده:** مرتب‌سازی بر اساس عنوان (`sort=title`) در Postgres با collation
فارسی انجام می‌شود و در SQLite با ترتیب بایتی → **مجموعه یکی است، ترتیب متفاوت**. ابزار
parity این را به‌عنوان یادداشت نشان می‌دهد، نه خطا.

---

## ۷) عیب‌یابی

| نشانه | علت معمول | راه‌حل |
| --- | --- | --- |
| `/api/health` → `db.backend: sqlite` روی Vercel | `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` تنظیم نشده | متغیرها را بگذارید و **Redeploy** کنید |
| خطای `401/403` از Supabase | کلید `anon` به‌جای `service_role`، یا RLS بدون policy | از `service_role` استفاده کنید |
| `relation "public.leads" does not exist` | `supabase/schema.sql` اجرا نشده | در SQL Editor اجرا کنید |
| اجرا برای همیشه «در صف» | ورکری در حال اجرا نیست | `bash .devcontainer/start-worker.sh` در Codespace |
| کار `failed` با `Connection refused` | موتور داکر پایین است | `docker compose up -d` و `curl :8080/api/v1/jobs` |
| موتور `422` می‌دهد | `lat/lon` عدد فرستاده شده یا `max_time` کم | رشته بفرستید؛ `max_time` به ثانیه |
| ایمیل `skipped` / `transport: none` | متغیرهای Gmail نیست یا `DIGEST_TO` خالی | `/api/delivery/status` را ببینید و راهنمایش را دنبال کنید |
| `invalid_grant` از Gmail | refresh token باطل شده (تغییر رمز/لغو دسترسی) | `scripts/gmail_token.py` را دوباره اجرا کنید |
| `/api/diag/osm` صفر برمی‌گرداند | صنف/شهر اشتباه، یا Overpass شلوغ (۴۲۹/۵۰۴) | `?all=1` را امتحان کنید؛ `sources.overpass.up` و `error` را ببینید؛ کش ۱۵ دقیقه‌ای را با `OVERPASS_CACHE_TTL=0` خاموش کنید |
| جست‌وجو «نیمه» برمی‌گردد | نتیجه به سقف `out … LIMIT` خورده | `?deep=1` (یا جست‌وجوی «کامل» در UI) محدوده را ۲×۲ می‌شکند |
| UI در صف گیر کرده ولی ورکر تمام کرده | پیشرفت در DB تازه‌تر از حافظه است | تازه‌سازی کنید؛ قاعدهٔ کد: «تازه‌ترین `ts` برنده است» |
| پیش‌نمایش Vercel صفحهٔ ورود Vercel می‌دهد | `ssoProtection` روی پروژه | Production را باز کنید یا محافظ را بردارید |
| `500 FUNCTION_INVOCATION_FAILED` | پاسخ WSGI نامعتبر: `status` با پیشوند «HTTP/1.1» یا هدرهای hop-by-hop | `leadgen/wsgi.py` این‌ها را نرمال می‌کند؛ در کد تازه خودتان این کار را نکنید |
| همهٔ مسیرها ۴۰۴ ولی تابع بالا است | بازنویسی، `PATH_INFO` را به `/api/index` تغییر داده | مقصد باید `?__lg_path=/:path*` داشته باشد؛ `scripts/check_deploy_config.py` همین را می‌سنجد |
| هشدار بیلد دربارهٔ `memory` | روی Active CPU نادیده گرفته می‌شود | کلید `memory` را از `vercel.json` بردارید |

---

## ۸) هزینه و محدودیت‌ها

- **Supabase Free:** ۵۰۰ MB پایگاه‌داده، ۲ پروژه، API نامحدود منصفانه — برای ده‌ها هزار لید
  کافی است (هر لید ~۱ KB).
- **Vercel Hobby:** توابع serverless با `maxDuration=60s`؛ چون اجراها صفی‌اند، خودِ اپ فقط
  میلی‌ثانیه کار می‌کند.
- **Codespaces:** پلن رایگان GitHub ماهانه ساعت محدودی می‌دهد؛ موتور را فقط وقتی لازم است
  روشن نگه دارید (`docker compose stop` برای توقف).
- **گوگل:** اسکراپ گوگل‌مپ با شرایط استفادهٔ گوگل هم‌خوانی ندارد؛ حجم را متعادل نگه دارید،
  برای اجراهای بزرگ پروکسی بگذارید و خروجی را «لیدِ نیازمند تأیید» بدانید.
- **جایگزین بدون داکر:** اگر موتور در دسترس نبود، برنامه با موتور دمو کامل کار می‌کند
  (همهٔ مراحل پایپ‌لاین با دادهٔ نمونه) — برای آموزش/نمایش، نه فروش.
