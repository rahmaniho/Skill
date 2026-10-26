#!/usr/bin/env python3
"""ساخت داده نمونه برای سایت GitHub Pages (`site/data/sample.json`).

داده‌ها **کاملاً مصنوعی‌اند** (موتور دمو، بدون داکر و بدون اینترنت) تا همه قابلیت‌ها روی
یک صفحه ثابت قابل نمایش باشد؛ هیچ داده شخصی واقعی commit نمی‌شود.

روش کار: یک «جاروی تمامی مشاغل» واقعی روی تهران با موتور دمو اجرا می‌شود، سپس برای اینکه
نمونه فقط از لیدهای داغ تشکیل نشود، نمونه‌برداری لایه‌ای (stratified) روی جمعیت انجام می‌شود
و آمارها دوباره روی همان نمونه محاسبه می‌شوند.

اجرا:  python3 scripts/build_site_sample.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LEADGEN_DATA_DIR", "/tmp/leadgen-site-sample")
os.environ.setdefault("ENGINE_MODE", "demo")

from leadgen import analytics, exports, geo, niches, pipeline, scoring, storage  # noqa: E402

SITE_DATA = ROOT / "site" / "data"
SAMPLE_SIZE = 120
POPULATION_TARGET = 480        # حداکثر لید خام برای ساخت جمعیت نمونه

FIELDS = ("title", "niche_fa", "niche_id", "category", "city", "address", "phone", "email",
          "email_primary", "emails", "status", "status_fa", "note", "website", "domain",
          "instagram", "facebook", "linkedin", "lat", "lon", "latitude", "longitude",
          "location", "maps_link", "plus_code", "place_id", "input_id", "profile",
          "place_status", "rating", "reviews", "price_range", "score", "segment", "segment_fa",
          "channel", "channel_fa", "pitch", "gaps_fa", "tags", "completeness", "contact_channels")


def slim(row: dict) -> dict:
    out = {k: ("" if row.get(k) is None else row.get(k)) for k in FIELDS}
    out["email"] = out.get("email") or out.get("email_primary") or ""
    out["profile"] = out.get("profile") or "web_design"
    # تعداد کانال‌های تماس از درصد «کامل بودن» بازسازی می‌شود (۶ کانال)
    out["contact_channels"] = round(int(out.get("completeness") or 0) * 6 / 100)
    out["lat"] = round(float(row.get("lat") or 0), 6)
    out["lon"] = round(float(row.get("lon") or 0), 6)
    out["rating"] = round(float(row.get("rating") or 0), 1)
    out["score"] = int(row.get("score") or 0)
    out["completeness"] = int(row.get("completeness") or 0)
    out["factors"] = [{"fa": f.get("fa", ""), "points": round(float(f.get("points") or 0), 1),
                       "weight": int(f.get("weight") or 0), "detail": f.get("detail", "")}
                      for f in row.get("score_factors", [])]
    return out


def stratified(rows: list[dict], size: int) -> list[dict]:
    """از لیست مرتب‌شده بر اساس امتیاز به‌صورت یکنواخت نمونه برمی‌دارد تا توزیع واقعی بماند."""
    if len(rows) <= size:
        return rows
    step = len(rows) / size
    return [rows[int(i * step)] for i in range(size)]


def build() -> dict:
    request = pipeline.ScrapeRequest(
        all_niches=True, city_query="تهران", lang="fa", depth=3,
        profile="web_design", socials=True, engine_mode="demo",
        max_niches=6, keywords_per_niche=2, keywords_per_job=4,
        limit=POPULATION_TARGET, cols="contact", export_fmt="json",
        sender={"name": "سارا محمدی", "company": "استودیو رشد",
                "role": "مشاور رشد کسب‌وکارهای محلی",
                "phone": "+98 912 000 0000", "email": "sara@roshd.studio",
                "website": "https://roshd.studio", "offer": "۳۰ دقیقه مشاوره رایگان"},
    )
    result = pipeline.run_scrape(request)
    population = result.rows
    print(f"  • جمعیت جارو: {len(population)} لید از {result.stats.get('sweep_niches', 0)} صنف"
          f" ({result.stats.get('jobs', 1)} job)", file=sys.stderr)

    sample = stratified(population, SAMPLE_SIZE)
    plan = [n.fa for n in niches.sweep_plan(limit=6)]
    pack = analytics.build(sample, {"id": "sample", "city": "تهران",
                                    "niche_fa": "نمونه چندصنفی", "engine": "demo",
                                    "limit": len(sample)})
    storage.delete_run(result.run_id)          # پایگاه داده نمونه در /tmp پاک می‌شود

    return {
        "generated_at": "2026-10-26",
        "disclaimer": ("داده‌های این صفحه کاملاً مصنوعی‌اند (موتور دمو) تا همه قابلیت‌ها بدون "
                       "دسترسی به گوگل روی یک صفحه ثابت نمایش داده شود. در نصب واقعی، داده از "
                       "موتور gosom/google-maps-scraper داخل داکر روی دستگاه خودتان استخراج می‌شود."),
        "meta": {
            "city": "تهران", "profile": "web_design", "engine": "demo",
            "jobs": result.stats.get("jobs", 1),
            "population": len(population),
            "niches": sorted({r.get("niche_fa") or "" for r in sample if r.get("niche_fa")}),
            "plan": plan,
            "catalog": {"niches": len(niches.CATALOG), "groups": len(niches.GROUPS),
                        "cities": len(geo.CITIES), "profiles": len(scoring.PROFILES),
                        "channels": len(exports.COLUMN_PRESETS)},
        },
        "analytics": pack,
        "columns": {preset: [[key, fa] for key, fa in cfg["columns"]]
                    for preset, cfg in exports.COLUMN_PRESETS.items()},
        "leads": [slim(r) for r in sample],
    }


def main() -> int:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    payload = build()
    out = SITE_DATA / "sample.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    seg = Counter(l["segment"] for l in payload["leads"])
    print(f"✓ {out.relative_to(ROOT)} — {len(payload['leads'])} لید، "
          f"{round(out.stat().st_size / 1024, 1)} کیلوبایت، بخش‌ها: {dict(seg)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
