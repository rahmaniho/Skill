"""نقطهٔ ورود Vercel (Python runtime) — کل اپ به‌صورت یک تابع WSGI.

Vercel هر چیزی را که به این فایل برسد به همین تابع می‌دهد (`vercel.json` همهٔ مسیرها را
به اینجا rewrite می‌کند)، پس رابط کاربری (`smart/`, `web/`, `mini/`, `site/`) و همهٔ
`/api/*` از یک کد مسیریابی سرو می‌شوند — همان `leadgen.server`.

متغیرهای محیطی لازم (Vercel → Project → Settings → Environment Variables):
    SUPABASE_URL, SUPABASE_SERVICE_KEY     پایگاه‌دادهٔ مشترک با ورکر
    LEADGEN_QUEUE=always                   اجراها به صف بروند (تابع serverless
                                           نمی‌تواند چند دقیقه باز بماند)
    LEADGEN_DATA_DIR=/tmp/leadgen-data     تنها مسیر نوشتنی روی Vercel
    GMAIL_REFRESH_TOKEN, GMAIL_CLIENT_ID,  ارسال گزارش ایمیلی (تنها راه ممکن
    GMAIL_CLIENT_SECRET, DIGEST_TO         در serverless؛ SMTP بسته است)
    APP_BASE_URL                           نشانی اپ برای لینک‌های داخل ایمیل
    LEADGEN_ROOT_UI=smart                  رابط پیش‌فرض روی /

نکتهٔ امنیتی: داده‌ها شخصی‌اند. اگر اپ را عمومی می‌کنید، در Vercel «Deployment
Protection» را روشن نگه دارید یا یک لایهٔ احراز هویت جلویش بگذارید.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# مسیرهای پروژه باید پیش از import در sys.path باشند (Vercel cwd را عوض می‌کند)
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.chdir(ROOT_DIR)                                  # فایل‌های رابط با مسیر نسبی خوانده می‌شوند
os.environ.setdefault("LEADGEN_DATA_DIR", "/tmp/leadgen-data")
os.environ.setdefault("LEADGEN_QUEUE", "always")     # حالت صف برای سرورلس
os.environ.setdefault("LEADGEN_ROOT_UI", "smart")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# هر دو نام بیرون داده می‌شود: Vercel معمولاً «app» را می‌گیرد و بعضی ابزارها/تست‌ها
# «application» (قرارداد استاندارد WSGI) را می‌خواهند. هر دو یک شیء هستند.
from leadgen.wsgi import app, application  # noqa: E402  — بعد از تنظیم مسیرها import شود

assert app is application

__all__ = ["app", "application"]
