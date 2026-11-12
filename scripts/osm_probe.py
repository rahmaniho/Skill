#!/usr/bin/env python3
"""آزمون دودی موتور OSM: آیا Overpass و Nominatim از این شبکه در دسترس‌اند؟

چرا؟ سندباکس توسعه به برخی سرویس‌ها دسترسی ندارد؛ این اسکریپت همان مسیری را
اجرا می‌کند که اپ در تولید طی می‌کند و نتیجه را چاپ می‌کند. هم محلی اجرا می‌شود
(`python3 scripts/osm_probe.py`) و هم در GitHub Actions (workflow «OSM probe»).

    python3 scripts/osm_probe.py                      # تهران/دندانپزشکی + رامسر/رستوران
    python3 scripts/osm_probe.py --city اوز --niche کافه
    python3 scripts/osm_probe.py --all                # جاروی همهٔ اصناف یک شهر کوچک
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LEADGEN_DATA_DIR", "/tmp/leadgen-osm-probe")

from leadgen import geo, niches, osm_tags, pipeline, regions  # noqa: E402
from leadgen.providers import osm  # noqa: E402


def probe(city: str, niche: str = "", all_business: bool = False, limit: int = 400) -> dict:
    started = time.time()
    area = regions.resolve(city=city)
    if not area.get("lat"):
        print(f"✗ شهر «{city}» پیدا نشد")
        return {"ok": False, "city": city}
    bbox = None
    if area.get("city_key") and area["city_key"] in geo.CITIES:
        bbox = [float(x) for x in geo.CITIES[area["city_key"]][3]]
    bbox = bbox or geo.default_bbox(area["lat"], area["lon"], 0.12)
    span = max(bbox[1] - bbox[0], bbox[3] - bbox[2])
    with_names = span < 0.6

    if all_business:
        filters = osm_tags.all_business_filters()
        label = "همهٔ کسب‌وکارها"
    else:
        niche_obj = niches.find_niche(niche or "رستوران")
        filters = osm_tags.filters_for_niche(niche_obj.id, with_names=with_names)
        label = niche_obj.fa

    print(f"• {label} در {area['label']} — bbox {[round(x, 3) for x in bbox]} "
          f"({len(filters)} فیلتر، نام‌محور: {with_names})")
    query = osm.build_overpass_query(filters, bbox, limit=limit)
    t0 = time.time()
    try:
        rows = osm.overpass_search(filters, bbox=bbox, limit=limit, input_id="probe")
        elapsed = round(time.time() - t0, 1)
        print(f"  ✓ Overpass: {len(rows)} کسب‌وکار در {elapsed}s")
        for row in rows[:5]:
            print(f"    - {row['title']} | {row['category'] or '—'} | "
                  f"{row['latitude']},{row['longitude']} | {row['phone'] or '—'}")
        if rows:
            with_phone = sum(1 for r in rows if r.get("phone"))
            with_site = sum(1 for r in rows if r.get("website"))
            with_addr = sum(1 for r in rows if r.get("address"))
            cats: dict[str, int] = {}
            for row in rows:
                cats[row.get("category") or "—"] = cats.get(row.get("category") or "—", 0) + 1
            print(f"    تلفن {with_phone} · سایت {with_site} · نشانی {with_addr} | "
                  f"دسته‌ها: {dict(sorted(cats.items(), key=lambda kv: -kv[1])[:5])}")
        result = {"ok": bool(rows), "city": area["label"], "rows": len(rows),
                  "seconds": elapsed, "label": label, "query_chars": len(query)}
    except Exception as exc:                             # noqa: BLE001 — گزارش روشن
        elapsed = round(time.time() - t0, 1)
        print(f"  ✗ Overpass: {type(exc).__name__}: {exc} ({elapsed}s)")
        result = {"ok": False, "city": area["label"], "error": str(exc)[:200], "seconds": elapsed}

    # مقایسه: مسیر متنی قدیمی (Nominatim) روی همان شهر
    try:
        t1 = time.time()
        term = "restaurant" if all_business else (niches.find_niche(niche or "رستوران").keywords_en or ["restaurant"])[0]
        legacy = osm.search(term, bbox=bbox, country="ir", limit=40, timeout=15)
        print(f"  • Nominatim «{term}»: {len(legacy)} ردیف در {round(time.time() - t1, 1)}s")
    except Exception as exc:                             # noqa: BLE001
        print(f"  • Nominatim: {type(exc).__name__}: {exc}")

    result["total_seconds"] = round(time.time() - started, 1)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="آزمون دودی موتور دادهٔ OSM")
    parser.add_argument("--city", default="")
    parser.add_argument("--niche", default="")
    parser.add_argument("--all", action="store_true", help="جاروی همهٔ اصناف (تگ‌محور)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.city:
        targets = [(args.city, args.niche, args.all)]
    else:
        targets = [("تهران", "دندانپزشکی", False), ("رامسر", "رستوران", False),
                   ("اوز", "", True)]

    results = [probe(city, niche, all_business) for city, niche, all_business in targets]
    ok = sum(1 for r in results if r.get("ok"))
    print(f"\nنتیجه: {ok}/{len(results)} درخواست نتیجهٔ واقعی داد.")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
