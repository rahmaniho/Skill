#!/usr/bin/env bash
# شروع سریع LeadGen Studio
#   ./run.sh              → وب‌اپ روی پورت ۸۰۰۰: رابط ساده (/) + نسخهٔ جمع‌وجور (/mini/) + داشبورد حرفه‌ای (/studio/)
#   ./run.sh demo         → ساخت یک اجرای نمونه فوری (بدون داکر) و چاپ مسیر فایل‌ها
#   ./run.sh seed         → ساخت دادهٔ نمونهٔ منطقه‌محور (۷ اجرا در ۵ محدوده) برای نمایش/توسعه
#   ./run.sh test         → اجرای همه تست‌های پایتون
#   ./run.sh smoke        → تست سریع رابط‌ها بدون مرورگر (site + smart + mini)
#   ./run.sh doctor       → بررسی سلامت محیط (موتور، داکر، پایگاه‌داده)
#   ./run.sh engine       → بالا آوردن موتور واقعی با داکر (docker compose up -d)
#   ./run.sh pages        → ساخت دادهٔ نمونه + کپی هر دو رابط برای GitHub Pages
set -euo pipefail
cd "$(dirname "$0")"

CMD="${1:-serve}"
PORT="${LEADGEN_PORT:-8000}"

case "$CMD" in
  serve|"")
    if ! command -v python3 >/dev/null 2>&1; then
      echo "✗ python3 پیدا نشد. پایتون ۳٫۹+ نصب کنید." >&2; exit 1
    fi
    echo "▶ وب‌اپ:           http://localhost:$PORT/        (رابط ساده — یک جمله بنویسید)"
    echo "▶ نسخهٔ جمع‌وجور:  http://localhost:$PORT/mini/   (جست‌وجو + استان/شهر/منطقه/خیابان)"
    echo "▶ داشبورد حرفه‌ای: http://localhost:$PORT/studio/ (همهٔ تنظیمات)"
    echo "  (برای توقف: Ctrl+C)"
    exec python3 -m leadgen.server --host "${LEADGEN_HOST:-0.0.0.0}" --port "$PORT"
    ;;
  demo)
    exec python3 -m leadgen.cli run "دندانپزشکی" --city "تهران" --lang fa \
      --profile web_design --socials --engine demo --depth 3 --export xlsx
    ;;
  seed)
    exec python3 scripts/seed_demo_data.py "${2:-}"
    ;;
  test)
    exec python3 -m unittest discover -s tests -v
    ;;
  smoke)
    node scripts/smoke_site.js
    node scripts/smoke_smart.js
    node scripts/smoke_mini.js
    echo "✓ هر سه رابط بدون خطا اجرا شدند"
    ;;
  doctor)
    exec python3 -m leadgen.cli doctor
    ;;
  engine)
    if ! command -v docker >/dev/null 2>&1; then
      echo "✗ داکر نصب نیست → برنامه با موتور دمو کار می‌کند (بدون داده واقعی)." >&2
      echo "  نصب: https://www.docker.com/products/docker-desktop" >&2; exit 1
    fi
    docker compose up -d
    echo "▶ منتظر بالا آمدن موتور…"
    for i in $(seq 1 20); do
      if curl -s -m 3 "http://localhost:${SCRAPER_PORT:-8080}/api/v1/jobs" >/dev/null 2>&1; then
        echo "✓ موتور آماده است: http://localhost:${SCRAPER_PORT:-8080}"; exit 0
      fi
      sleep 2
    done
    echo "⚠ موتور هنوز پاسخ نمی‌دهد؛ docker compose logs را ببینید." >&2; exit 1
    ;;
  pages)
    python3 scripts/build_site_sample.py
    python3 scripts/build_pages_app.py
    echo "✓ برای پیش‌نمایش محلی: python3 -m http.server -d site --bind 0.0.0.0 8090"
    ;;
  *)
    echo "کاربرد: ./run.sh [serve|demo|seed|test|smoke|doctor|engine|pages]" >&2; exit 1
    ;;
esac
