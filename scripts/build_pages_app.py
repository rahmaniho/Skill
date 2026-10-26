#!/usr/bin/env python3
"""رابط ساده (smart/) را برای انتشار روی GitHub Pages زیر site/app/ کپی می‌کند.

چرا؟ چون `smart/app.js` دو حالت دارد: زنده (سرور پایتون) و دمو (فقط مرورگر).
با یک کپی کوچک، همان رابط کاربری — بدون هیچ تغییری در کد — روی Pages هم کلیک‌خوردنی
می‌شود و دادهٔ نمونهٔ `site/data/sample.json` را می‌خواند.

تغییرات هنگام کپی:
  • <body>            →  <body data-mode="demo">   (بدون تلاش برای رسیدن به سرور)
  • عنوان صفحه        →  افزودن «نسخهٔ نمایشی» تا با نسخهٔ زنده اشتباه نشود

اجرا:
  python3 scripts/build_pages_app.py            # ساخت / به‌روزرسانی site/app/
  python3 scripts/build_pages_app.py --check    # فقط بررسی به‌روز بودن (برای CI)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "smart"
DST = ROOT / "site" / "app"

FILES = ["index.html", "styles.css", "app.js", "campaign.js", "favicon.svg"]


def transform(name: str, text: str) -> str:
    """فقط index.html دست می‌خورد؛ بقیهٔ فایل‌ها عیناً کپی می‌شوند."""
    if name != "index.html":
        return text
    if "<body data-mode=\"demo\">" not in text:
        text = text.replace("<body>", "<body data-mode=\"demo\">", 1)
    text = text.replace(
        "<title>LeadGen Studio — شکار لید کسب‌وکارهای محلی</title>",
        "<title>LeadGen Studio — رابط ساده (نسخهٔ نمایشی)</title>", 1)
    return text


def build() -> int:
    DST.mkdir(parents=True, exist_ok=True)
    missing = [f for f in FILES if not (SRC / f).is_file()]
    if missing:
        print(f"✗ فایل‌های مبدأ پیدا نشد: {', '.join(missing)}", file=sys.stderr)
        return 1
    for name in FILES:
        out = transform(name, (SRC / name).read_text(encoding="utf-8"))
        (DST / name).write_text(out, encoding="utf-8")
        print(f"  ✓ site/app/{name} ({len(out.encode('utf-8')):,} بایت)")
    sample = ROOT / "site" / "data" / "sample.json"
    print(f"\nدادهٔ نمونه: {'✓ ' + str(sample.relative_to(ROOT)) if sample.is_file() else '✗ پیدا نشد'}")
    print("آدرس انتشار: https://rahmaniho.github.io/Skill/app/")
    return 0


def check() -> int:
    bad = []
    for name in FILES:
        src = SRC / name
        dst = DST / name
        if not src.is_file():
            bad.append(f"{name}: مبدأ وجود ندارد")
            continue
        if not dst.is_file():
            bad.append(f"{name}: site/app/ ساخته نشده")
            continue
        if transform(name, src.read_text(encoding="utf-8")) != dst.read_text(encoding="utf-8"):
            bad.append(f"{name}: با smart/ هم‌خوان نیست")
    if bad:
        print("✗ نسخهٔ Pages به‌روز نیست:", file=sys.stderr)
        for item in bad:
            print(f"   - {item}", file=sys.stderr)
        print("   → python3 scripts/build_pages_app.py", file=sys.stderr)
        return 1
    print(f"✓ site/app/ با smart/ هم‌خوان است ({len(FILES)} فایل)")
    return 0


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else build())
