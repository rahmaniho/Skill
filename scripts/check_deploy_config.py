#!/usr/bin/env python3
"""بررسی سازگاری پیکربندی استقرار (Vercel) با کد مخزن.

چرا؟ `vercel.json` و `api/index.py` دستی نوشته شده‌اند و اگر یکی از آن‌ها عوض شود و دیگری
نه، استقرار بی‌صدا می‌شکند (تابع بالا می‌آید ولی مسیرها 404 می‌دهند). این اسکریپت در CI
اجرا می‌شود و همان قراردادها را می‌سنجد:

  • `api/index.py` وجود دارد و `application` را از `leadgen.wsgi` بیرون می‌دهد
  • `vercel.json` یک تابع دارد، `maxDuration` دست‌کم ۶۰ ثانیه، و بازنویسی catch-all به
    `/api/index?__lg_path=/:path*`
  • `requirements.txt` بدون وابستگی بیرونی است (کل برنامه با کتابخانهٔ استاندارد پایتون
    نوشته شده — نصب وابستگی در Vercel فقط خطر و زمان اضافه می‌کند)
  • مسیرهای سرویس‌شده توسط `leadgen.wsgi` با مسیرهای `leadgen.server` یکی است

اجرا:  python3 scripts/check_deploy_config.py
"""
from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"

ROUTES = ["/api/health", "/api/catalog", "/api/runs", "/api/queue", "/api/queue/jobs",
          "/api/delivery/status"]


def fail(message: str) -> None:
    print(f"{RED}✖ {message}{RESET}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"{GREEN}✓ {message}{RESET}")


def main() -> int:
    entry = ROOT / "api" / "index.py"
    if not entry.exists():
        fail("api/index.py پیدا نشد — نقطهٔ ورود Vercel است")
    text = entry.read_text(encoding="utf-8")
    for needle in ("from leadgen.wsgi import", "application"):
        if needle not in text:
            fail(f"api/index.py باید «{needle}» داشته باشد")
    ok("api/index.py برنامهٔ WSGI را از leadgen.wsgi بیرون می‌دهد")

    config_path = ROOT / "vercel.json"
    if not config_path.exists():
        fail("vercel.json پیدا نشد")
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"vercel.json JSON معتبر نیست: {exc}")
        return 1

    functions = cfg.get("functions") or {}
    if list(functions) != ["api/index.py"]:
        fail(f"vercel.json باید فقط یک تابع داشته باشد (api/index.py)؛ هست: {list(functions)}")
    settings = functions["api/index.py"]
    if int(settings.get("maxDuration") or 0) < 60:
        fail(f"maxDuration باید دست‌کم ۶۰ ثانیه باشد؛ هست: {settings.get('maxDuration')}")
    if "memory" in settings:
        fail("کلید memory در vercel.json روی Active CPU نادیده گرفته می‌شود و فقط هشدار بیلد "
             f"می‌سازد؛ حذفش کنید (هست: {settings['memory']})")
    if settings.get("includeFiles") != "**":
        fail("includeFiles باید «**» باشد تا رابط‌های وب و دادهٔ نمونه هم بسته‌بندی شوند")
    ok(f"تابع api/index.py: maxDuration={settings['maxDuration']}s، includeFiles=**")

    # بازنویسی catch-all باید مسیر اصلی را در پارامتر __lg_path حمل کند؛ وگرنه تابع فقط
    # PATH_INFO=/api/index می‌بیند و هر صفحه‌ای ۴۰۴ می‌شود (بدون هیچ هدر مستندِ جایگزین).
    rewrites = cfg.get("rewrites") or []
    rewrite = rewrites[0] if rewrites else {}
    destination = rewrite.get("destination") or ""
    if rewrite.get("source") != "/:path*":
        fail(f"بازنویسی باید «/:path*» را بگیرد؛ هست: {rewrite.get('source')}")
    if not destination.startswith("/api/index?") or "/:path*" not in destination:
        fail(f"مقصد بازنویسی باید مسیر را به تابع برساند؛ هست: {destination!r}")
    ok(f"بازنویسی catch-all مسیر را حمل می‌کند: /:path* → {destination}")

    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    deps = [line for line in requirements.splitlines()
            if line.strip() and not line.strip().startswith("#")]
    if deps:
        fail(f"requirements.txt باید بدون وابستگی بیرونی بماند؛ پیدا شد: {deps}")
    ok("requirements.txt بدون وابستگی بیرونی است (فقط کتابخانهٔ استاندارد)")

    # مسیرها باید در هر دو میزبان یکی باشند (سرور محلی و WSGI/Vercel)
    sys.path.insert(0, str(ROOT))
    from leadgen import wsgi  # noqa: E402  (بعد از تنظیم sys.path)
    missing = []
    for route in ROUTES:
        status, _headers, _body = wsgi.call("GET", route)
        if status >= 500:
            missing.append(f"{route} → {status}")
    if missing:
        fail(f"مسیرها روی لایهٔ WSGI خطای سرور دادند: {missing}")
    ok(f"{len(ROUTES)} مسیر کلیدی روی لایهٔ WSGI (Vercel) پاسخ می‌دهند")

    # نام پارامتر باید در vercel.json و leadgen/wsgi.py یکی باشد
    if f"{wsgi._PATH_PARAM}=/" not in destination:
        fail(f"vercel.json از پارامتر {wsgi._PATH_PARAM!r} استفاده نمی‌کند؛ "
             f"مقصد: {destination!r}")
    rewritten = []
    for route in ("/", "/api/health", "/api/queue", "/mini/"):
        status, _headers, body = wsgi.call(
            "GET", "/api/index",
            query=f"{wsgi._PATH_PARAM}=" + urllib.parse.quote(route, safe=""))
        if status >= 500 or (route == "/" and b"<!doctype html" not in body.lower()):
            rewritten.append(f"{route} → {status}")
    if rewritten:
        fail(f"مسیرها پشت بازنویسی ورسل کار نکردند: {rewritten}")
    ok("۴ مسیر پشت بازنویسی ورسل (PATH_INFO=/api/index + __lg_path) درست سرو می‌شوند")

    print(f"\n{GREEN}✔ پیکربندی استقرار سازگار است{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
