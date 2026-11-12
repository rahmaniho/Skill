#!/usr/bin/env bash
# آماده‌سازی Codespace: موتور واقعی (داکر) + بررسی اتصال به Supabase.
# این اسکریپت idempotent است؛ هر بار که Codespace ساخته/بازسازی شود اجرا می‌شود.
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1;36m▸ %s\033[0m\n' "$1"; }
ok()  { printf '  \033[1;32m✔\033[0m %s\n' "$1"; }
warn(){ printf '  \033[1;33m⚠\033[0m %s\n' "$1"; }

say "۱) فایل تنظیمات (.env)"
if [[ ! -f .env ]]; then
  cp .env.example .env
  warn ".env از روی .env.example ساخته شد — کلیدهای Supabase/Gmail را در آن بگذارید."
else
  ok ".env موجود است"
fi

say "۲) موتور اسکراپر (gosom/google-maps-scraper در داکر)"
if ! command -v docker >/dev/null 2>&1; then
  warn "داکر پیدا نشد؛ ورکر با موتور دمو کار می‌کند (دادهٔ نمونه)."
else
  docker compose up -d || warn "docker compose up ناموفق بود — لاگ: docker compose logs"
  for i in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${SCRAPER_PORT:-8080}/api/v1/jobs" >/dev/null 2>&1; then
      ok "موتور واقعی آماده است (http://127.0.0.1:${SCRAPER_PORT:-8080})"
      break
    fi
    [[ $i -eq 30 ]] && warn "موتور بعد از ۳۰ ثانیه آماده نشد — docker compose logs را ببینید."
    sleep 1
  done
fi

say "۳) پایگاه‌دادهٔ مشترک (Supabase)"
if python3 - <<'PY'
import sys
sys.path.insert(0, ".")
from leadgen import config
print("supabase" if config.supabase_ready() else "sqlite")
PY
then :; fi
if grep -q '^SUPABASE_URL=https' .env 2>/dev/null; then
  if python3 -c "
import sys; sys.path.insert(0,'.')
from leadgen import storage
storage.init_db()
print('  ✔ اتصال به Supabase برقرار است؛ جدول‌ها پیدا شدند.')
" 2>/dev/null; then :; else
    warn "اتصال به Supabase برقرار نشد. کلیدها را بررسی کنید و مطمئن شوید"
    warn "supabase/schema.sql یک‌بار در SQL Editor اجرا شده است."
  fi
else
  warn "SUPABASE_URL تنظیم نشده — داده‌ها محلی (SQLite) می‌مانند."
  warn "برای کار با اپِ Vercel، کلیدهای Supabase را در .env بگذارید."
fi

say "۴) آزمون‌ها"
if python3 -m unittest discover -s tests >/dev/null 2>&1; then
  ok "همهٔ آزمون‌ها سبز هستند"
else
  warn "بعضی آزمون‌ها ناموفق بودند: python3 -m unittest discover -s tests"
fi

cat <<'TXT'

┌──────────────────────────────────────────────────────────────────────────┐
│ مرحلهٔ بعد:                                                              │
│   bash .devcontainer/start-worker.sh     # ورکر صف را شروع کنید          │
│   python3 -m leadgen.worker --status     # وضعیت صف و موتور              │
│   bash run.sh                            # داشبورد محلی روی پورت ۸۰۰۰   │
│                                                                          │
│ اگر می‌خواهید موتور را بیرون از Codespace هم قابل دسترسی کنید:           │
│   python3 scripts/engine_proxy.py --port 8081 --api-key "$SCRAPER_API_KEY"│
└──────────────────────────────────────────────────────────────────────────┘
TXT
