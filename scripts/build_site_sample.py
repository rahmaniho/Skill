#!/usr/bin/env python3
"""ساخت داده نمونه برای سایت GitHub Pages (`site/data/sample.json`).

داده‌ها **کاملاً مصنوعی‌اند** (موتور دمو، بدون داکر و بدون اینترنت) تا همه قابلیت‌ها روی
یک صفحه ثابت قابل نمایش باشد؛ هیچ داده شخصی واقعی commit نمی‌شود.

روش کار: چند «اجرای منطقه‌محور» با موتور دمو ساخته می‌شود (سطح خیابان، سطح منطقه و چند شهر)،
سپس نمونه‌برداری لایه‌ای (stratified) انجام می‌شود تا نمونه فقط از لیدهای داغ تشکیل نشود و
آمارها روی همان نمونه دوباره محاسبه می‌شوند.

علاوه بر لیدها، بستهٔ `mini` هم ساخته می‌شود: درخت استان ← شهر ← منطقه ← خیابان + فهرست
نام‌ها (فارسی و لاتین) + کاتالوگ اصناف — تا «نسخهٔ جمع‌وجور» روی Pages هم کامل کار کند.

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

from leadgen import (analytics, exports, geo, niches, pipeline, regions, scoring,  # noqa: E402
                     smart, storage)

SITE_DATA = ROOT / "site" / "data"
SAMPLE_SIZE = 150

SENDER = {"name": "سارا محمدی", "company": "استودیو رشد",
          "role": "مشاور رشد کسب‌وکارهای محلی",
          "phone": "+98 912 000 0000", "email": "sara@roshd.studio",
          "website": "https://roshd.studio", "offer": "۳۰ دقیقه مشاوره رایگان"}

# سناریوهای نمونه: ترکیبی از سطح خیابان، منطقه و چند شهر مختلف
SCENARIOS: list[dict] = [
    {"all_niches": True, "city_query": "تهران", "province": "tehran-province",
     "district": "ونک", "street": "خیابان ملاصدرا", "lang": "fa", "profile": "web_design",
     "depth": 3, "grid": 0, "limit": 0, "max_niches": 6, "keywords_per_niche": 2,
     "keywords_per_job": 6},
    {"all_niches": True, "city_query": "تهران", "province": "tehran-province",
     "district": "چیتگر", "lang": "fa", "profile": "generic", "depth": 3, "grid": 0,
     "limit": 0, "max_niches": 7, "keywords_per_niche": 2, "keywords_per_job": 7},
    {"niche_groups": ["غذا و نوشیدنی"], "all_niches": True, "city_query": "تهران",
     "province": "tehran-province", "district": "تجریش", "lang": "fa", "profile": "web_design",
     "depth": 2, "grid": 0, "limit": 0, "max_niches": 5, "keywords_per_niche": 2,
     "keywords_per_job": 6, "emails": False},
    {"niche_groups": ["خدمات فنی و ساختمان"], "all_niches": True, "city_query": "تهران",
     "province": "tehran-province", "district": "یافت‌آباد", "lang": "fa",
     "profile": "b2b_supply", "depth": 2, "grid": 0, "limit": 0, "max_niches": 6,
     "keywords_per_niche": 2, "keywords_per_job": 6},
    {"niche_query": "دندانپزشکی", "city_query": "مشهد", "province": "razavi-khorasan",
     "lang": "fa", "profile": "recruitment", "depth": 3, "grid": 0, "limit": 0},
    {"all_niches": True, "city_query": "شیراز", "province": "fars", "district": "معالی‌آباد",
     "lang": "fa", "profile": "marketing", "depth": 2, "grid": 0, "limit": 0,
     "max_niches": 5, "keywords_per_niche": 2, "keywords_per_job": 5},
]

FIELDS = ("title", "niche_fa", "niche_id", "category", "city", "address", "phone", "email",
          "email_primary", "emails", "status", "status_fa", "note", "website", "domain",
          "instagram", "facebook", "linkedin", "lat", "lon", "latitude", "longitude",
          "location", "maps_link", "plus_code", "place_id", "input_id", "profile",
          "place_status", "rating", "reviews", "price_range", "score", "segment", "segment_fa",
          "channel", "channel_fa", "pitch", "gaps_fa", "tags", "completeness", "contact_channels",
          "run_id")


def slim(row: dict) -> dict:
    out = {k: ("" if row.get(k) is None else row.get(k)) for k in FIELDS}
    out["email"] = out.get("email") or out.get("email_primary") or ""
    out["profile"] = out.get("profile") or "web_design"
    # تعداد کانال‌های تماس از درصد «کامل بودن» بازسازی می‌شود (۶ کانال)
    out["contact_channels"] = round(int(out.get("completeness") or 0) * 6 / 100)
    out["lat"] = round(float(row.get("lat") or 0), 6)
    out["lon"] = round(float(row.get("lon") or 0), 6)
    out["rating"] = round(float(row.get("rating") or 0), 1)
    out["reviews"] = int(row.get("reviews") or 0)
    out["score"] = int(row.get("score") or 0)
    out["completeness"] = int(out.get("completeness") or 0)
    out["factors"] = [{"fa": f.get("fa", ""), "points": round(float(f.get("points") or 0), 1),
                       "weight": int(f.get("weight") or 0), "detail": f.get("detail", "")}
                      for f in row.get("score_factors", [])]
    return out


def stratified(rows: list[dict], size: int) -> list[dict]:
    """نمونه‌برداری لایه‌ای بر اساس بخش (داغ/گرم/سرد) تا توزیع واقعی حفظ شود.

    اگر فقط روی امتیاز مرتب کنیم و یکنواخت برداریم، لیدهای ضعیف‌تر تقریباً حذف می‌شوند؛
    پس سهم هر بخش جدا حساب می‌شود و داخل هر بخش هم پله‌پله برمی‌داریم.
    """
    if len(rows) <= size:
        return rows
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(row.get("segment") or "cold", []).append(row)
    total = len(rows)
    picked: list[dict] = []
    for segment, items in buckets.items():
        quota = max(2, round(size * len(items) / total))
        quota = min(quota, len(items))
        step = len(items) / quota
        picked.extend(items[min(len(items) - 1, int(i * step))] for i in range(quota))
    picked.sort(key=lambda r: -(r.get("score") or 0))
    if len(picked) > size:                 # گردکردن سهمیه‌ها ممکن است کمی بیش‌تر کند
        keep = max(1, size // 2)
        picked = picked[:keep] + picked[-(size - keep):]
    return picked[:size]


def mini_payload() -> dict:
    """بستهٔ جغرافیایی + کاتالوگ برای «نسخهٔ جمع‌وجور» (کاملاً آفلاین)."""
    provinces = []
    for province in regions.provinces():
        provinces.append({
            "id": province["id"], "fa": province["fa"], "en": province["en"],
            "capital": province["capital"], "capital_fa": province["capital_fa"],
            "lat": province["lat"], "lon": province["lon"],
            "cities": [{"id": c["id"], "fa": c["fa"], "label": c["label"], "lat": c["lat"],
                        "lon": c["lon"], "districts": c["districts"], "streets": c["streets"]}
                       for c in province["cities"]],
        })
    countries = []
    for country in regions.countries():
        countries.append({
            "id": country["id"], "fa": country["fa"],
            "cities": [{"id": c["id"], "fa": c["fa"], "label": c["label"], "lat": c["lat"],
                        "lon": c["lon"], "districts": c["districts"], "streets": c["streets"]}
                       for c in country["cities"]],
        })
    return {
        "provinces": provinces,
        "countries": countries,
        "districts": {city: [[fa, lat, lon] for fa, lat, lon in rows]
                      for city, rows in regions.DISTRICTS.items()},
        "streets": {city: [[fa, lat, lon, district] for fa, lat, lon, district in rows]
                    for city, rows in regions.STREETS.items()},
        # فهرست نام‌ها (شامل شکل‌های کوتاه و لاتین) برای جست‌وجوی متنی در جمله
        "names": [[variant, canonical, city, kind]
                  for variant, canonical, city, kind, _lat, _lon in regions._name_index()],
        "radius": regions.RADIUS,
        "levels_fa": regions.LEVEL_FA,
        "niches": [{"id": n["id"], "fa": n["fa"], "group": n["group"], "en": n.get("en", ""),
                    "aliases": (n.get("aliases") or [])[:4]} for n in niches.all_niches()],
        "groups": niches.GROUPS,
        "options": {key: smart.options()[key] for key in ("goals", "sizes", "presets", "formats")},
        "cities": len(geo.CITIES),
    }


def build() -> dict:
    population: list[dict] = []
    areas: list[str] = []
    for body in SCENARIOS:
        request = pipeline.ScrapeRequest.from_dict({
            **body, "cols": "contact", "export_fmt": "", "engine_mode": "demo",
            "socials": True, "emails": body.get("emails", True), "sender": SENDER,
        })
        result = pipeline.run_scrape(request)
        run = storage.get_run(result.run_id) or {}
        for row in result.rows:
            row["run_id"] = result.run_id
            row.setdefault("city", run.get("city", ""))
        population.extend(result.rows)
        areas.append(f"{run.get('area_label') or run.get('city') or body.get('city_query')}"
                     f" ({len(result.rows)})")
        storage.delete_run(result.run_id)          # پایگاه داده نمونه در /tmp پاک می‌شود
        print(f"  • {areas[-1]}", file=sys.stderr)

    population.sort(key=lambda r: -(r.get("score") or 0))
    sample = stratified(population, SAMPLE_SIZE)
    pack = analytics.build(sample, {"id": "sample", "city": "چند منطقه",
                                    "niche_fa": "نمونه چندصنفی/چندمنطقه‌ای", "engine": "demo",
                                    "limit": len(sample)})

    return {
        "generated_at": "2026-10-27",
        "disclaimer": ("داده‌های این صفحه کاملاً مصنوعی‌اند (موتور دمو) تا همه قابلیت‌ها بدون "
                       "دسترسی به گوگل روی یک صفحه ثابت نمایش داده شود. در نصب واقعی، داده از "
                       "موتور gosom/google-maps-scraper داخل داکر روی دستگاه خودتان استخراج می‌شود."),
        "meta": {
            "city": "تهران، مشهد، شیراز", "profile": "web_design", "engine": "demo",
            "jobs": len(SCENARIOS), "population": len(population), "areas": areas,
            "niches": sorted({r.get("niche_fa") or "" for r in sample if r.get("niche_fa")}),
            "plan": [n.fa for n in niches.sweep_plan(limit=6)],
            "catalog": {"niches": len(niches.CATALOG), "groups": len(niches.GROUPS),
                        "cities": len(geo.CITIES), "provinces": len(regions.PROVINCES),
                        "districts": sum(len(v) for v in regions.DISTRICTS.values()),
                        "streets": sum(len(v) for v in regions.STREETS.values()),
                        "profiles": len(scoring.PROFILES),
                        "channels": len(exports.COLUMN_PRESETS)},
        },
        "analytics": pack,
        "columns": {preset: [[key, fa] for key, fa in cfg["columns"]]
                    for preset, cfg in exports.COLUMN_PRESETS.items()},
        "leads": [slim(r) for r in sample],
        "mini": mini_payload(),
    }


def main() -> int:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    payload = build()
    out = SITE_DATA / "sample.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    seg = Counter(l["segment"] for l in payload["leads"])
    cities = Counter((l.get("city") or "?") for l in payload["leads"])
    print(f"✓ {out.relative_to(ROOT)} — {len(payload['leads'])} لید، "
          f"{round(out.stat().st_size / 1024, 1)} کیلوبایت، بخش‌ها: {dict(seg)}")
    print(f"  محدوده‌ها: {dict(cities.most_common(6))}")
    print(f"  بستهٔ mini: {len(payload['mini']['provinces'])} استان، "
          f"{sum(len(p['cities']) for p in payload['mini']['provinces'])} شهر ایرانی، "
          f"{len(payload['mini']['names'])} نام منطقه/خیابان")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
