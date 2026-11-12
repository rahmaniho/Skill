"""پیکربندی مشترک آزمون‌ها — پیش از هر import از `leadgen` اجرا می‌شود.

چرا لازم است؟
  در محیط واقعی (Codespaces/Vercel) متغیرهای `SUPABASE_URL` و `SUPABASE_SERVICE_KEY`
  از قبل در محیط هستند. اگر آزمون‌ها با همان محیط اجرا شوند، `storage` به پایگاه‌دادهٔ
  **زنده** می‌رود و تست‌های صف/ذخیره‌سازی دادهٔ واقعی را آلوده می‌کنند.

  پس به‌صورت پیش‌فرض آزمون‌ها روی SQLite محلی (در پوشهٔ موقت هر تست) قفل می‌شوند.
  تنها راه رفتن روی ابر، تنظیم صریح `LEADGEN_TEST_CLOUD=1` است (برای بررسی دستی).
"""
from __future__ import annotations

import os

if os.environ.get("LEADGEN_TEST_CLOUD") != "1":
    os.environ["LEADGEN_DB"] = "sqlite"
    for _key in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY"):
        os.environ.pop(_key, None)
