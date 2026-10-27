#!/usr/bin/env python3
"""ساخت دادهٔ نمونه برای توسعه/نمایش (موتور دمو، بدون داکر و بدون اینترنت).

چند اجرای منطقه‌محور می‌سازد تا «نسخهٔ جمع‌وجور» چیزی برای جست‌وجو و فیلتر کردن داشته باشد:
سطح خیابان، سطح منطقه، چند شهر مختلف و یک جاروی چندصنفی.

اجرا:
    python3 scripts/seed_demo_data.py            # افزودن به داده‌های موجود
    python3 scripts/seed_demo_data.py --fresh    # پاک کردن همه و ساخت دوباره
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LEADGEN_DATA_DIR", str(ROOT / "data"))
os.environ.setdefault("ENGINE_MODE", "demo")

from leadgen import pipeline, storage  # noqa: E402

# هر سناریو: (عنوان، بدنهٔ درخواست) — همه با موتور دمو و پروفایل «فروش سایت»
SCENARIOS: list[tuple[str, dict]] = [
    ("خیابان ملاصدرا — ونک، تهران (سطح خیابان)", {
        "all_niches": True, "city_query": "تهران", "province": "tehran-province",
        "district": "ونک", "street": "خیابان ملاصدرا", "lang": "fa", "profile": "web_design",
        "socials": True, "emails": True, "depth": 3, "grid": 0, "limit": 0,
        "max_niches": 6, "keywords_per_niche": 2, "keywords_per_job": 6, "cols": "contact",
    }),
    ("چیتگر، تهران (سطح منطقه)", {
        "all_niches": True, "city_query": "تهران", "province": "tehran-province",
        "district": "چیتگر", "lang": "fa", "profile": "web_design",
        "socials": True, "emails": True, "depth": 3, "grid": 0, "limit": 0,
        "max_niches": 7, "keywords_per_niche": 2, "keywords_per_job": 7, "cols": "contact",
    }),
    ("تجریش، تهران — غذا و کافه", {
        "niche_groups": ["غذا و نوشیدنی"], "all_niches": True, "city_query": "تهران",
        "province": "tehran-province", "district": "تجریش", "lang": "fa", "profile": "web_design",
        "socials": True, "emails": True, "depth": 4, "grid": 0, "limit": 0,
        "max_niches": 5, "keywords_per_niche": 2, "keywords_per_job": 6, "cols": "contact",
    }),
    ("یافت‌آباد، تهران — خدمات فنی و ساختمان", {
        "niche_groups": ["خدمات فنی و ساختمان"], "all_niches": True, "city_query": "تهران",
        "province": "tehran-province", "district": "یافت‌آباد", "lang": "fa",
        "profile": "web_design", "socials": True, "emails": True, "depth": 4, "grid": 0,
        "limit": 0, "max_niches": 6, "keywords_per_niche": 2, "keywords_per_job": 6,
        "cols": "contact",
    }),
    ("مشهد — دندانپزشکی و کلینیک", {
        "niche_query": "دندانپزشکی", "city_query": "مشهد", "province": "razavi-khorasan",
        "lang": "fa", "profile": "web_design", "socials": True, "emails": True,
        "depth": 4, "grid": 0, "limit": 0, "cols": "contact",
    }),
    ("معالی‌آباد شیراز — همهٔ کسب‌وکارها", {
        "all_niches": True, "city_query": "شیراز", "province": "fars",
        "district": "معالی‌آباد", "lang": "fa", "profile": "web_design",
        "socials": True, "emails": True, "depth": 3, "grid": 0, "limit": 0,
        "max_niches": 6, "keywords_per_niche": 2, "keywords_per_job": 6, "cols": "contact",
    }),
    ("مرداویج اصفهان — زیبایی و آرایشگاه", {
        "niche_groups": ["زیبایی و آرایشگاه"], "all_niches": True, "city_query": "اصفهان",
        "province": "isfahan", "district": "مرداویج", "lang": "fa", "profile": "marketing",
        "socials": True, "emails": True, "depth": 3, "grid": 0, "limit": 0,
        "max_niches": 5, "keywords_per_niche": 2, "keywords_per_job": 5, "cols": "contact",
    }),
]


def seed(fresh: bool = False) -> int:
    storage.init_db()
    if fresh:
        removed = 0
        for run in storage.list_runs(limit=500):
            storage.delete_run(run["id"])
            removed += 1
        print(f"  • {removed} اجرای قبلی پاک شد")

    total = 0
    for title, body in SCENARIOS:
        request = pipeline.ScrapeRequest.from_dict({**body, "engine_mode": "demo", "export_fmt": ""})
        result = pipeline.run_scrape(request)
        stats = result.stats or {}
        total += len(result.rows)
        print(f"  • {title}: {len(result.rows)} لید "
              f"({stats.get('hot', 0)} داغ / {stats.get('warm', 0)} گرم) — {result.run_id}")

    runs = storage.list_runs(limit=500)
    leads = storage.count_leads()
    print(f"\n✓ {len(runs)} اجرا و {leads} لید در {storage.data_dir()} (مجموع سناریوها: {total})")
    return 0


if __name__ == "__main__":
    raise SystemExit(seed(fresh="--fresh" in sys.argv))
