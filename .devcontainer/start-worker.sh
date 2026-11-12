#!/usr/bin/env bash
# ورکر صف را در پس‌زمینهٔ Codespace راه می‌اندازد (اگر در حال اجراست، دست نمی‌زند).
#
# این ورکر کارها را از پایگاه‌دادهٔ مشترک (Supabase یا SQLite) برمی‌دارد، با موتور
# واقعی (داکر) اجرا می‌کند و نتایج را همان‌جا ذخیره می‌کند؛ اپِ Vercel فقط صف را
# پر می‌کند و وضعیت را نشان می‌دهد.
set -euo pipefail
cd "$(dirname "$0")/.."

LOG_DIR="${LEADGEN_LOG_DIR:-data/logs}"
LOG_FILE="$LOG_DIR/worker.log"
PID_FILE="$LOG_DIR/worker.pid"
mkdir -p "$LOG_DIR"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "✔ ورکر از قبل در حال اجرا است (PID $(cat "$PID_FILE")) — لاگ: $LOG_FILE"
  exit 0
fi

ENGINE="${ENGINE:-auto}"          # auto = داکر اگر بود، وگرنه دمو
EXTRA_ARGS=()
if [[ "${WORKER_DELIVER:-1}" == "1" ]]; then
  EXTRA_ARGS+=(--deliver)         # بعد از هر اجرای موفق، گزارش ایمیلی بفرست
fi

nohup python3 -m leadgen.worker \
  --engine "$ENGINE" \
  --interval "${WORKER_POLL_SECONDS:-5}" \
  --stale-seconds "${WORKER_STALE_SECONDS:-1800}" \
  "${EXTRA_ARGS[@]}" \
  >>"$LOG_FILE" 2>&1 &

echo $! > "$PID_FILE"
sleep 2
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "✔ ورکر شروع به کار کرد (PID $(cat "$PID_FILE"), موتور: $ENGINE)"
  echo "  لاگ زنده:  tail -f $LOG_FILE"
  echo "  وضعیت صف:  python3 -m leadgen.worker --status"
  echo "  توقف:      kill \$(cat $PID_FILE)"
else
  echo "⚠ ورکر شروع نشد — آخرین خطوط لاگ:"
  tail -n 20 "$LOG_FILE" || true
  exit 1
fi
