#!/usr/bin/env python3
"""یک (یا چند) «تپش» صف را از بیرون بزن — بدون نیاز به باز بودن مرورگر.

چرا؟ روی Vercel پروسهٔ همیشه‌روشن نداریم؛ اجراها در صف Supabase می‌مانند و تا
حالا فقط رابط کاربری آن‌ها را جلو می‌برد. اگر کاربر تب را ببندد، کار در صف
می‌ماند. این اسکریپت همان نقطهٔ امن `/api/worker/tick` را صدا می‌زند و می‌تواند
از یک کرون (GitHub Actions) هر چند دقیقه اجرا شود — یعنی محصول واقعاً عملیاتی
می‌شود: اجراها حتی بدون مرورگر باز، تمام می‌شوند.

نمونه‌ها:

    python3 scripts/queue_tick.py                          # یک تپش روی اپ پیش‌فرض
    python3 scripts/queue_tick.py --url http://localhost:8000 --repeat 3
    python3 scripts/queue_tick.py --url https://<app> --budget 45 --max-ticks 6
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_APP = "https://leadgen-studio-umber.vercel.app"
TIMEOUT = 70          # سقف تابع serverless ~۶۰ ثانیه + حاشیه


def tick(base: str, *, budget: int = 45, code: str = "") -> dict:
    """یک تپش؛ پاسخ JSON را برمی‌گرداند (حتی وقتی کاری در صف نیست)."""
    url = base.rstrip("/") + "/api/worker/tick?" + urllib.parse.urlencode({"budget": budget})
    headers = {"accept": "application/json", "user-agent": "leadgen-queue-tick/1.0"}
    if code:
        headers["x-leadgen-code"] = code
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", "replace")
        status = getattr(resp, "status", 200)
    try:
        payload = json.loads(raw)
    except ValueError:
        raise RuntimeError(f"پاسخ JSON نبود (HTTP {status}): {raw[:200]}")
    payload.setdefault("_http", status)
    return payload


def describe(payload: dict) -> str:
    if payload.get("idle"):
        queue = payload.get("queue") or {}
        return (f"بیکار · در انتظار {queue.get('pending', 0)} · "
                f"در حال اجرا {queue.get('running', 0)} · تمام‌شده {queue.get('done', 0)}")
    total = payload.get("total")
    state = "خطا" if payload.get("ok") is False else "انجام شد"
    parts = [f"{state} · run {payload.get('run_id') or '—'}"]
    if total is not None:
        parts.append(f"{total} لید")
    if payload.get("hot") is not None:
        parts.append(f"{payload.get('hot')} داغ")
    if payload.get("seconds") is not None:
        parts.append(f"{payload.get('seconds')} ثانیه")
    if payload.get("error"):
        parts.append(str(payload["error"])[:160])
    return " · ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="تپش صف اجراها از بیرون")
    parser.add_argument("--url", default=os.environ.get("APP_BASE_URL") or DEFAULT_APP,
                        help=f"آدرس اپ (پیش‌فرض: {DEFAULT_APP})")
    parser.add_argument("--budget", type=int, default=int(os.environ.get("TICK_BUDGET") or 45))
    parser.add_argument("--repeat", type=int, default=1,
                        help="چند بار پشت‌سرهم تپش بزند (حداکثر با --max-ticks)")
    parser.add_argument("--max-ticks", type=int, default=6,
                        help="سقف تپش‌ها وقتی صف خالی نشده (پیش‌فرض ۶ ≈ ۵ دقیقه)")
    parser.add_argument("--code", default=os.environ.get("LEADGEN_ACCESS_CODE") or "",
                        help="کد دسترسی اپ (اگر فعال است)")
    parser.add_argument("--json", action="store_true", help="پاسخ خام را هم چاپ کن")
    args = parser.parse_args(argv)

    budget = max(12, min(55, args.budget))
    max_ticks = max(1, min(20, args.max_ticks))
    # --repeat 1 یعنی «یک تپش»؛ بیشتر از ۱ یعنی «تا خالی‌شدن صف، حداکثر این تعداد»
    rounds = 1 if args.repeat <= 1 else min(max_ticks, args.repeat)
    results: list[dict] = []
    failures = 0
    for index in range(rounds):
        try:
            payload = tick(args.url, budget=budget, code=args.code)
        except urllib.error.HTTPError as exc:
            failures += 1
            print(f"✗ تپش {index + 1}: HTTP {exc.code} — {exc.reason}", file=sys.stderr)
            if exc.code in (401, 403):
                print("  کد دسترسی را با --code یا LEADGEN_ACCESS_CODE بدهید.", file=sys.stderr)
                break
            continue
        except Exception as exc:                       # noqa: BLE001 — گزارش روشن
            failures += 1
            print(f"✗ تپش {index + 1}: {type(exc).__name__}: {exc}", file=sys.stderr)
            break
        results.append(payload)
        print(f"• تپش {index + 1}: {describe(payload)}")
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        if payload.get("idle"):
            break
        if args.repeat <= 1:
            break
        time.sleep(1.0)

    processed = sum(1 for item in results if not item.get("idle"))
    errors = sum(1 for item in results if item.get("ok") is False)
    print(f"\nنتیجه: {processed} کار اجرا شد، {errors} خطا، {failures} تپش ناموفق.")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
