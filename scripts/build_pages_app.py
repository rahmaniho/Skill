#!/usr/bin/env python3
"""رابط‌های وب را برای انتشار روی GitHub Pages کپی می‌کند.

  smart/  →  site/app/    (رابط سادهٔ نسخهٔ کامل)
  mini/   →  site/mini/   (نسخهٔ جمع‌وجور: جست‌وجو + استان/شهر/منطقه/خیابان)

چرا کپی؟ چون هر دو رابط دو حالت دارند: زنده (سرور پایتون) و دمو (فقط مرورگر).
با یک کپی کوچک، همان کد — بدون تغییر در منطق — روی Pages هم کلیک‌خوردنی می‌شود و
دادهٔ نمونهٔ `site/data/sample.json` را می‌خواند (برای mini، بستهٔ `mini` داخل همان
فایل، درخت استان/شهر/منطقه/خیابان و فهرست نام‌ها را هم به‌صورت آفلاین می‌دهد).

تغییرات هنگام کپی:
  • smart/index.html:  <body> → <body data-mode="demo"> + عنوان «نسخهٔ نمایشی»
  • mini/index.html:   عنوان «نسخهٔ نمایشی» (تشخیص حالت دمو در خودش خودکار است)

اجرا:
  python3 scripts/build_pages_app.py            # ساخت / به‌روزرسانی
  python3 scripts/build_pages_app.py --check    # فقط بررسی به‌روز بودن (برای CI)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

APPS = [
    {
        "src": ROOT / "smart",
        "dst": ROOT / "site" / "app",
        "url": "https://rahmaniho.github.io/Skill/app/",
        "files": ["index.html", "styles.css", "app.js", "campaign.js", "favicon.svg"],
        "body_demo": True,
        "titles": [("<title>LeadGen Studio — شکار لید کسب‌وکارهای محلی</title>",
                    "<title>LeadGen Studio — رابط ساده (نسخهٔ نمایشی)</title>")],
    },
    {
        "src": ROOT / "mini",
        "dst": ROOT / "site" / "mini",
        "url": "https://rahmaniho.github.io/Skill/mini/",
        "files": ["index.html", "styles.css", "app.js", "favicon.svg"],
        "body_demo": False,
        "titles": [("<title>لیدژن مینی — پیدا کردن همهٔ کسب‌وکارهای یک منطقه</title>",
                    "<title>لیدژن مینی — نسخهٔ نمایشی (دادهٔ نمونه)</title>")],
    },
]


def transform(app: dict, name: str, text: str) -> str:
    """فقط index.html دست می‌خورد؛ بقیهٔ فایل‌ها عیناً کپی می‌شوند."""
    if name != "index.html":
        return text
    if app["body_demo"] and '<body data-mode="demo">' not in text:
        text = text.replace("<body>", '<body data-mode="demo">', 1)
    for old, new in app["titles"]:
        text = text.replace(old, new, 1)
    return text


def build() -> int:
    failed = False
    for app in APPS:
        src, dst = app["src"], app["dst"]
        dst.mkdir(parents=True, exist_ok=True)
        missing = [f for f in app["files"] if not (src / f).is_file()]
        if missing:
            print(f"✗ {src.name}/: فایل‌های مبدأ پیدا نشد: {', '.join(missing)}", file=sys.stderr)
            failed = True
            continue
        print(f"→ {src.relative_to(ROOT)}/ ⟶ {dst.relative_to(ROOT)}/")
        for name in app["files"]:
            out = transform(app, name, (src / name).read_text(encoding="utf-8"))
            (dst / name).write_text(out, encoding="utf-8")
            print(f"  ✓ {dst.relative_to(ROOT)}/{name} ({len(out.encode('utf-8')):,} بایت)")
        print(f"  آدرس انتشار: {app['url']}")
    sample = ROOT / "site" / "data" / "sample.json"
    print(f"\nدادهٔ نمونه: {'✓ ' + str(sample.relative_to(ROOT)) if sample.is_file() else '✗ پیدا نشد'}")
    return 1 if failed else 0


def check() -> int:
    bad = []
    count = 0
    for app in APPS:
        for name in app["files"]:
            src = app["src"] / name
            dst = app["dst"] / name
            if not src.is_file():
                bad.append(f"{app['src'].name}/{name}: مبدأ وجود ندارد")
                continue
            if not dst.is_file():
                bad.append(f"{app['src'].name}/{name}: {dst.relative_to(ROOT)} ساخته نشده")
                continue
            if transform(app, name, src.read_text(encoding="utf-8")) != dst.read_text(encoding="utf-8"):
                bad.append(f"{app['src'].name}/{name}: با {dst.relative_to(ROOT)} هم‌خوان نیست")
            count += 1
    if bad:
        print("✗ نسخهٔ Pages به‌روز نیست:", file=sys.stderr)
        for item in bad:
            print(f"   - {item}", file=sys.stderr)
        print("   → python3 scripts/build_pages_app.py", file=sys.stderr)
        return 1
    print(f"✓ نسخهٔ Pages به‌روز است ({len(APPS)} رابط، {count} فایل)")
    return 0


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else build())
