#!/usr/bin/env bash
# شروع سریع LeadGen Studio
#   ./run.sh              → داشبورد وب روی پورت ۸۰۰۰
#   ./run.sh demo         → ساخت یک اجرای نمونه فوری (بدون داکر) و چاپ مسیر فایل‌ها
#   ./run.sh test         → اجرای همه تست‌ها
#   ./run.sh doctor       → بررسی سلامت محیط (موتور، داکر، پایگاه‌داده)
#   ./run.sh engine       → بالا آوردن موتور واقعی با داکر (docker compose up -d)
set -euo pipefail
cd "$(dirname "$0")"

CMD="${1:-serve}"
PORT="${LEADGEN_PORT:-8000}"

case "$CMD" in
  serve|"")
    if ! command -v python3 >/dev/null 2>&1; then
      echo "✗ python3 پیدا نشد. پایتون ۳٫۹+ نصب کنید." >&2; exit 1
    fi
    echo "▶ داشبورد: http://localhost:$PORT  (برای توقف: Ctrl+C)"
    exec python3 -m leadgen.server --host "${LEADGEN_HOST:-0.0.0.0}" --port "$PORT"
    ;;
  demo)
    exec python3 -m leadgen.cli run "دندانپزشکی" --city "تهران" --lang fa \
      --profile web_design --socials --engine demo --depth 3 --export xlsx
    ;;
  test)
    exec python3 -m unittest discover -s tests -v
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
  *)
    echo "کاربرد: ./run.sh [serve|demo|test|doctor|engine]" >&2; exit 1
    ;;
esac
