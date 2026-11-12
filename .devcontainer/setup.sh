#!/usr/bin/env bash
# آماده‌سازی Codespace: موتور واقعی (داکر) + بررسی اتصال به Supabase.
# این اسکریپت idempotent است؛ هر بار که Codespace ساخته/بازسازی شود اجرا می‌شود.
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1;36m▸ %s\033[0m\n' "$1"; }
ok()  { printf '  \033[1;32m✔\033[0m %s\n' "$1"; }
warn(){ printf '  \033[1;33m⚠\033[0m %s\n' "$1"; }

say "۱) تنظیمات و اعتبارنامه‌ها"
# اگر کلیدها به‌صورت متغیر محیطی هستند (Codespaces Secrets یا صادرشده دستی)، `.env`
# لازم نیست — متغیر محیطی همیشه بر `.env` قالب است (config.load_env_file).
if [[ -n "${SUPABASE_URL:-}" && -n "${SUPABASE_SERVICE_KEY:-}" ]]; then
  ok "کلیدهای Supabase از محیط خوانده شدند (Codespaces Secrets)"
elif [[ ! -f .env ]]; then
  cp .env.example .env
  warn ".env از روی .env.example ساخته شد — کلیدهای Supabase/Gmail را در آن بگذارید"
  warn "یا در Settings → Secrets and variables → Codespaces مخزن بگذارید (پیشنهادی)"
else
  ok ".env موجود است"
fi

python3 - <<'PY'
import sys
sys.path.insert(0, ".")
from leadgen import config


def mask(value: str, keep: int = 4) -> str:
    value = (value or "").strip()
    if not value:
        return "— تنظیم نشده"
    return f"…{value[-keep:]}" if len(value) > keep + 4 else "…" + "*" * len(value)


url = config.env("SUPABASE_URL")
host = url.split("//")[-1].split("/")[0] if url else "— تنظیم نشده"
rows = [
    ("پایگاه‌داده", "supabase (ابر)" if config.supabase_ready() else "sqlite (محلی)"),
    ("SUPABASE_URL", host),
    ("SUPABASE_SERVICE_KEY", mask(config.env("SUPABASE_SERVICE_KEY"))),
    ("صف اجراها", "فعال" if config.queue_enabled() else "خاموش"),
    ("حمل‌کنندهٔ ایمیل", config.email_transport()),
    ("DIGEST_TO", config.env("DIGEST_TO") or "— تنظیم نشده"),
    ("APP_BASE_URL", config.app_base_url() or "— تنظیم نشده"),
    ("موتور", config.env("SCRAPER_BASE_URL") or "http://localhost:8080"),
]
print("  وضعیت فعلی (مقدار کلیدها پنهان است):")
for name, value in rows:
    print(f"    {name:<22} {value}")
if not config.supabase_ready():
    print("  ⚠ بدون Supabase، ورکر داده‌ها را محلی نگه می‌دارد و اپ Vercel آن‌ها را نمی‌بیند.")
PY

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

say "۳) اتصال واقعی به پایگاه‌دادهٔ مشترک (Supabase)"
if python3 - <<'PY'
import sys
sys.path.insert(0, ".")
from leadgen import config
sys.exit(0 if config.supabase_ready() else 1)
PY
then
  # یک خواندن واقعی: آمار داشبورد از توابع SQL خودِ پایگاه‌داده می‌آید
  set +e
  STATS=$(python3 - <<'PY' 2>&1
import sys
sys.path.insert(0, ".")
from leadgen import storage
stats = storage.dashboard_stats()
print(f"{stats.get('runs', 0)} اجرا و {stats.get('leads', 0)} لید در پایگاه‌دادهٔ ابری")
PY
)
  RC=$?
  set -e
  if [[ $RC -eq 0 ]]; then
    ok "اتصال برقرار — $STATS"
  else
    warn "کلیدها هست ولی اتصال برقرار نشد. این سه مورد را بررسی کنید:"
    warn "  ۱) supabase/schema.sql یک‌بار در SQL Editor اجرا شده باشد"
    warn "  ۲) کلید از نوع service_role باشد (کلید anon با RLS بسته کار نمی‌کند)"
    warn "  ۳) خروجی شبکهٔ Codespace باز باشد"
    printf '  جزئیات: %s\n' "$(printf '%s' "$STATS" | tail -n 2 | tr '\n' ' ')"
  fi
else
  warn "SUPABASE_URL/SUPABASE_SERVICE_KEY تنظیم نشده — داده‌ها محلی (SQLite) می‌مانند"
  warn "برای دیدن نتیجه در اپِ Vercel، این دو را در Codespaces Secrets مخزن بگذارید"
fi

say "۴) آزمون‌ها"
# آزمون‌ها همیشه روی SQLite محلی اجرا می‌شوند (tests/__init__.py) تا پایگاه‌دادهٔ زندهٔ
# Supabase آلوده نشود — حتی وقتی کلیدهای ابری در محیط هستند.
if python3 -m unittest discover -t . -s tests >/dev/null 2>&1; then
  ok "همهٔ آزمون‌ها سبز هستند (روی SQLite محلی؛ پایگاه‌دادهٔ ابری دست‌نخورده)"
else
  warn "بعضی آزمون‌ها ناموفق بودند: python3 -m unittest discover -t . -s tests"
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
