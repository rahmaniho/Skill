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
    # اول «وضعیت سرویس» را می‌سنجیم: تفاوت «شبکه بسته است» و «کوئری غلط است»
    # بدون این خط، خرابیِ شبکه با خرابیِ کد اشتباه گرفته می‌شود.
    status_probe = osm.overpass_health(force=True)
    print(f"  • وضعیت Overpass: up={status_probe.get('up')} "
          f"({status_probe.get('ms', '?')}ms) {status_probe.get('error') or ''}".rstrip())
    query = osm.build_overpass_query(filters, bbox, limit=limit)
    t0 = time.time()
    try:
        # force: probe باید واقعاً Overpass را امتحان کند، حتی اگر همین چند لحظه
        # پیش یک درخواست دیگر شکست خورده باشد (علامت «پایین» سه‌دقیقه‌ای).
        rows = osm.overpass_search(filters, bbox=bbox, limit=limit, input_id="probe",
                                   force=True)
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
        result = {"overpass_ok": bool(rows), "city": area["label"], "rows": len(rows),
                  "seconds": elapsed, "label": label, "query_chars": len(query),
                  "service": status_probe}
    except Exception as exc:                             # noqa: BLE001 — گزارش روشن
        elapsed = round(time.time() - t0, 1)
        print(f"  ✗ Overpass: {type(exc).__name__}: {exc} ({elapsed}s)")
        result = {"overpass_ok": False, "city": area["label"], "rows": 0,
                  "error": str(exc)[:300], "seconds": elapsed, "service": status_probe}

    # مقایسه: مسیر متنی قدیمی (Nominatim) روی همان شهر
    nominatim_rows = 0
    try:
        t1 = time.time()
        term = "restaurant" if all_business else (niches.find_niche(niche or "رستوران").keywords_en or ["restaurant"])[0]
        legacy = osm.search(term, bbox=bbox, country="ir", limit=40, timeout=15)
        nominatim_rows = len(legacy)
        print(f"  • Nominatim «{term}»: {nominatim_rows} ردیف در {round(time.time() - t1, 1)}s")
    except Exception as exc:                             # noqa: BLE001
        print(f"  • Nominatim: {type(exc).__name__}: {exc}")

    result["nominatim_rows"] = nominatim_rows
    # «موفق» یعنی محصول از این مسیر دادهٔ واقعی می‌گیرد؛ Overpass یکی از دو مسیر است.
    result["ok"] = bool(result.get("overpass_ok") or nominatim_rows)
    result["total_seconds"] = round(time.time() - started, 1)
    return result


def probe_street(city: str, street: str) -> dict:
    """سنجش «کل خیابان»: Nominatim یک تکه می‌دهد، Overpass باید همهٔ تکه‌ها را بدهد."""
    area = regions.resolve(city=city)
    if not area.get("lat"):
        print(f"✗ شهر «{city}» پیدا نشد")
        return {"ok": False, "city": city}
    bbox = None
    if area.get("city_key") and area["city_key"] in geo.CITIES:
        bbox = [float(x) for x in geo.CITIES[area["city_key"]][3]]
    bbox = bbox or geo.default_bbox(area["lat"], area["lon"], 0.12)
    seed: list[float] = []
    try:
        found = osm.lookup_area(f"{street}، {city}", bbox=bbox, country="IR",
                                limit=10, merge=True)
        seed = list(found.get("bbox") or [])
    except Exception as exc:                             # noqa: BLE001
        print(f"  • Nominatim: {type(exc).__name__}: {exc}")
    print(f"• خیابان «{street}» در {area['label']} — تکهٔ Nominatim: "
          f"{[round(x, 4) for x in seed] or '—'}")
    t0 = time.time()
    diag: list[str] = []
    try:
        wide = osm.overpass_street_bbox(
            [osm._strip_street_prefix(street), street], bbox=bbox,
            timeout=25.0, budget_s=30.0, near=seed or None, diag=diag)
    except Exception as exc:                             # noqa: BLE001
        wide = {}
        diag.append(f"{type(exc).__name__}: {exc}")
    elapsed = round(time.time() - t0, 1)
    for line in diag:
        print(f"  · {line}")
    if wide:
        span = max(wide["bbox"][1] - wide["bbox"][0],
                   (wide["bbox"][3] - wide["bbox"][2]) * 0.9) * 111.0
        print(f"  ✓ Overpass: {wide['ways']} تکه ({wide.get('via')}) → "
              f"{[round(x, 4) for x in wide['bbox']]} ≈ {span:.1f} کیلومتر در {elapsed}s")
    else:
        print(f"  ✗ Overpass تکه‌ای برنگرداند ({elapsed}s)")
        # عیب‌یابی: OSM این خیابان را با چه نامی ذخیره کرده؟
        base = osm._strip_street_prefix(street)
        s_, n_, w_, e_ = bbox
        probe_q = (f'[out:json][timeout:20];way["name"~"{base}"]'
                   f'({s_},{w_},{n_},{e_});out tags 8;')
        try:
            data = osm.overpass_raw(probe_q, timeout=25, budget_s=30)
            names = []
            for element in (data.get("elements") or [])[:8]:
                name = str((element.get("tags") or {}).get("name") or "")
                if name and name not in names:
                    names.append(name)
            print(f"  · نام‌های موجود شامل «{base}»: {names} "
                  f"({len(data.get('elements') or [])} المان)")
        except Exception as exc:                         # noqa: BLE001
            print(f"  · عیب‌یابی نام: {type(exc).__name__}: {exc}")
    result = {"ok": bool(wide), "city": area["label"], "street": street,
              "seed_bbox": seed, "street_bbox": wide.get("bbox"),
              "ways": wide.get("ways", 0), "via": wide.get("via"),
              "seconds": elapsed, "diag": diag}
    # یک خط جمع‌بندی برای اینکه در گزارش Actions هم دیده شود (لاگ کامل در دسترس نیست)
    print("SUMMARY " + json.dumps(result, ensure_ascii=False))
    return result


def probe_nominatim(query: str) -> dict:
    """چند تکه از یک خیابان را Nominatim می‌دهد؟ (dedupe=0 — برای پوشش کامل)"""
    import urllib.parse
    params = {"q": query, "format": "jsonv2", "limit": 50, "dedupe": 0,
              "extratags": 1, "addressdetails": 0, "accept-language": "fa,en",
              "countrycodes": "ir"}
    url = f"{osm.config.OSM_ENDPOINT}/search?" + urllib.parse.urlencode(params)
    try:
        data = osm._http_json(url, timeout=30)
    except Exception as exc:                             # noqa: BLE001
        print(f"  ✗ Nominatim: {type(exc).__name__}: {exc}")
        return {"ok": False, "error": str(exc)[:200]}
    rows = [d for d in (data or []) if isinstance(d, dict)
            and (d.get("type") in osm.STREET_TYPES or d.get("class") in osm.STREET_TYPES)]
    print(f"• Nominatim «{query}»: {len(data or [])} پاسخ، {len(rows)} خیابان")
    for row in rows[:8]:
        print(f"    - {row.get('type')}/{row.get('class')} {row.get('display_name')} "
              f"{row.get('boundingbox')}")
    return {"ok": bool(rows), "total": len(data or []), "streets": len(rows),
            "boxes": [r.get("boundingbox") for r in rows],
            "labels": [r.get("display_name") for r in rows]}


def probe_parts(city: str, street: str, parts: list[str]) -> dict:
    """آیا پرسیدن «خیابان + محله» تکه‌های مختلف یک خیابان بلند را می‌دهد؟"""
    area = regions.resolve(city=city)
    bbox = None
    if area.get("city_key") and area["city_key"] in geo.CITIES:
        bbox = [float(x) for x in geo.CITIES[area["city_key"]][3]]
    bbox = bbox or geo.default_bbox(area["lat"], area["lon"], 0.12)
    seed = list(osm.lookup_area(f"{street}، {city}", bbox=bbox, country="ir",
                                limit=5, merge=True).get("bbox") or [])
    print(f"• پایه («{street}، {city}»): {[round(x, 4) for x in seed] or '—'}")
    boxes: list[list[float]] = []
    for part in parts:
        query = f"{street}، {part}، {city}"
        try:
            found = osm.lookup_area(query, bbox=bbox, country="ir", limit=5)
        except Exception as exc:                         # noqa: BLE001
            print(f"  ✗ {part}: {type(exc).__name__}")
            continue
        box = found.get("bbox") or []
        if box:
            boxes.append(box)
        print(f"  • {part}: {[round(x, 4) for x in box] or '—'} — {found.get('label', '')[:60]}")
    merged = osm._grow_street_cluster(boxes, seed) if (boxes and seed) else boxes
    if merged:
        out = [min(b[0] for b in merged), max(b[1] for b in merged),
               min(b[2] for b in merged), max(b[3] for b in merged)]
        span = max(out[1] - out[0], out[3] - out[2]) * 111.0
        print(f"  ✓ جمع {len(merged)} تکه → {[round(x, 4) for x in out]} ≈ {span:.1f} کیلومتر")
    else:
        out = []
        print("  ✗ چیزی جمع نشد")
    return {"ok": bool(out), "seed": seed, "boxes": boxes, "bbox": out}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="آزمون دودی موتور دادهٔ OSM")
    parser.add_argument("--city", default="")
    parser.add_argument("--niche", default="")
    parser.add_argument("--all", action="store_true", help="جاروی همهٔ اصناف (تگ‌محور)")
    parser.add_argument("--street", default="", help="سنجش پوشش کامل یک خیابان")
    parser.add_argument("--endpoints", action="store_true", help="سنجش آینه‌های Overpass")
    parser.add_argument("--nominatim", default="", help="شمارش تکه‌های یک خیابان در Nominatim")
    parser.add_argument("--parts", default="",
                        help="محله‌ها برای تکه‌کردن خیابان: «ونک,تجریش,...» (با --city/--street)")
    parser.add_argument("--raw", default="",
                        help="کوئری‌های Overpass جداشده با ||| برای عیب‌یابی")
    parser.add_argument("--coverage", action="store_true",
                        help="کدام آینه دادهٔ تهران/زوریخ دارد؟ (جهانی یا منطقه‌ای)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.raw:
        for query in [q.strip() for q in args.raw.split("|||") if q.strip()]:
            t0 = time.time()
            try:
                data = osm.overpass_raw(query, timeout=25, budget_s=40)
            except Exception as exc:                     # noqa: BLE001
                print(f"  ✗ {type(exc).__name__}: {str(exc)[:120]} | {query[:70]}")
                continue
            elements = data.get("elements") or []
            with_bb = sum(1 for e in elements if isinstance(e, dict) and e.get("bounds"))
            names = sorted({str((e.get("tags") or {}).get("name") or "")
                            for e in elements[:20] if isinstance(e, dict)})
            print(f"  • {len(elements)} المان، {with_bb} با bounds، "
                  f"{round(time.time() - t0, 1)}s | {query[:70]}")
            if names:
                print(f"    نام‌ها: {[n for n in names if n][:6]}")
        return 0

    if args.parts:
        parts = [p.strip() for p in args.parts.split(",") if p.strip()]
        result = probe_parts(args.city or "تهران", args.street or "ولیعصر", parts)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if args.coverage:
        checks = [
            ("زوریخ", '[out:json][timeout:20];way["name"~"Bahnhofstrasse"]'
                      '(47.36,8.52,47.39,8.56);out bb;'),
            ("تهران", '[out:json][timeout:20];nwr["amenity"="cafe"]'
                      '(35.70,51.41,35.71,51.42);out 3;'),
        ]
        report = {}
        for label, query in checks:
            rows = osm.probe_overpass_endpoints(timeout=15, query=query)
            report[label] = rows
            for row in rows:
                mark = "✓" if row["ok"] else "✗"
                print(f"  {mark} [{label}] {row['host']} — {row['ms']}ms {row['error']}".rstrip())
        print(f"\nTehran rows: {{'zurich': ..., 'tehran': ...}}")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.nominatim:
        result = probe_nominatim(args.nominatim)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if args.endpoints:
        rows = osm.probe_overpass_endpoints()
        for row in rows:
            mark = "✓" if row["ok"] else "✗"
            # یک خط فشرده تا در گزارش Actions هم کامل دیده شود (n = تعداد نتیجه)
            print(f"  {mark} {row['host']} n={row.get('n', 0)} {row['ms']}ms "
                  f"{row['error']}".rstrip())
        print(f"\nسالم: {sum(1 for r in rows if r['ok'])}/{len(rows)} | "
              f"با دادهٔ تهران: {sum(1 for r in rows if r.get('n'))}/{len(rows)}")
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0 if any(r["ok"] for r in rows) else 1

    if args.street:
        result = probe_street(args.city or "تهران", args.street)
        if args.json:
            print(json.dumps([result], ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if args.city:
        targets = [(args.city, args.niche, args.all)]
    else:
        targets = [("تهران", "دندانپزشکی", False), ("رامسر", "رستوران", False),
                   ("اوز", "", True)]

    results = [probe(city, niche, all_business) for city, niche, all_business in targets]
    ok = sum(1 for r in results if r.get("ok"))
    overpass_ok = sum(1 for r in results if r.get("overpass_ok"))
    print(f"\nنتیجه: {ok}/{len(results)} درخواست دادهٔ واقعی داد "
          f"(Overpass در {overpass_ok} مورد).")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
