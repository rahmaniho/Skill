#!/usr/bin/env python3
"""ساخت فیکسچر هم‌ارزی SQLite ↔ Postgres (Supabase) — قطعی و بازتولیدپذیر.

چرا این فیکسچر؟
  جست‌وجوی لیدها دو پیاده‌سازی دارد: یکی در SQLite (`leadgen/storage.py`) و یکی به‌صورت
  توابع SQL در `supabase/schema.sql` (`public.search_leads`). هر دو باید **دقیقاً یک
  خروجی** بدهند، وگرنه اپِ روی Vercel چیزی khác از داشبورد محلی نشان می‌دهد.

  این اسکریپت ۸ لید واقعیِ نمونه (`site/data/sample.json`) را با شناسه‌های قطعی انتخاب
  می‌کند، پرچم‌های تماس (has_phone/has_email/has_website/has_social) را محاسبه می‌کند،
  سپس:
    1. یک پایگاه SQLite موقت می‌سازد و `expected.json` را از آن می‌گیرد؛
    2. `insert.sql` می‌سازد تا **همان ردیف‌ها با همان شناسه‌ها** در Supabase درج شوند.

  شناسه‌ها قطعی‌اند (`eq-run-1-l01`…`l08`) تا بتوان انتظارها را در ریپو commit کرد و
  در CI بدون شبکه سنجید.

اجرا:
    python3 tests/fixtures/cloud_parity/build.py                 # ساخت هر دو فایل
    python3 tests/fixtures/cloud_parity/build.py --data-dir /tmp/x
    python3 scripts/cloud_parity.py            # مقایسهٔ SQLite با انتظارهای commit‌شده
    python3 scripts/cloud_parity.py --live     # مقایسهٔ SQLite با Supabase واقعی
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT))

RUN_ID = "eq-run-1"
RUN = {"id": RUN_ID, "created_at": 1760000000.0, "niche_fa": "نمونهٔ چندصنفی",
       "city": "تهران", "area_label": "تهران، ونک", "status": "done", "engine": "demo",
       "profile": "web_design", "live": False}
# مرکز دایرهٔ جست‌وجوی جغرافیایی (ونک تهران) — فاصله‌ها از مختصات واقعی ردیف‌ها می‌آید
CENTER = (35.7592, 51.4094)
PER_SEGMENT = 3                    # ۳ داغ + ۳ گرم + ۲ سرد = ۸ ردیف
TOTAL = 8

COLUMNS = ["id", "run_id", "title", "category", "niche_id", "niche_fa", "city", "address",
           "phone", "emails", "email_primary", "website", "domain", "instagram",
           "latitude", "longitude", "rating", "reviews", "score", "segment",
           "has_phone", "has_email", "has_website", "has_social", "gaps", "tags",
           "status", "channel", "completeness", "place_id", "created_at"]

# سناریوهای هم‌ارزی — هر دو پیاده‌سازی باید یکی برگردانند
SCENARIOS: dict[str, dict] = {
    "all": {},
    "q_tehran": {"q": "تهران"},
    "q_dental": {"q": "دندانپزشکی"},
    "hot": {"segment": "hot"},
    "email_strong": {"has_email": True, "min_score": 70},
    "geo_20km": {"lat": CENTER[0], "lon": CENTER[1], "radius_m": 20000},
    "geo_2km": {"lat": CENTER[0], "lon": CENTER[1], "radius_m": 2000},
    "no_website": {"has_website": False},
    "sort_rating": {"sort": "rating", "order": "desc"},
    "sort_title": {"sort": "title", "order": "asc"},
    "instagram": {"has_instagram": True},
    "paged": {"limit": 3, "offset": 3},
    "gap_no_website": {"gap": "بدون وب‌سایت"},
}


def with_flags(lead: dict) -> dict:
    """پرچم‌های تماس را محاسبه می‌کند — همان کاری که پایپ‌لاین برای دادهٔ واقعی می‌کند."""
    row = dict(lead)
    row["has_website"] = 1 if (row.get("website") or "").strip() else 0
    row["has_email"] = 1 if (row.get("email_primary") or row.get("emails") or "").strip() else 0
    row["has_phone"] = 1 if (row.get("phone") or "").strip() else 0
    row["has_social"] = 1 if any((row.get(k) or "").strip()
                                for k in ("instagram", "facebook", "linkedin")) else 0
    row["gaps"] = row.get("gaps") or []
    row["tags"] = row.get("tags") or []
    row["emails"] = row.get("emails") or row.get("email") or ""
    row["created_at"] = row.get("created_at") or RUN["created_at"]
    row["run_id"] = RUN_ID
    return row


def build_leads() -> list[dict]:
    """۸ لید قطعی از دادهٔ نمونهٔ عمومی (ترتیب فایل = ترتیب انتخاب)."""
    sample = json.loads((ROOT / "site" / "data" / "sample.json").read_text(encoding="utf-8"))
    picked: list[dict] = []
    seen = {"hot": 0, "warm": 0, "cold": 0}
    for lead in sample["leads"]:
        segment = lead.get("segment")
        if segment not in seen or seen[segment] >= PER_SEGMENT:
            continue
        picked.append(lead)
        seen[segment] += 1
        if len(picked) >= TOTAL:
            break
    if len(picked) != TOTAL:
        raise SystemExit(f"❌ دادهٔ نمونه تغییر کرده است: {len(picked)} ردیف انتخاب شد "
                         f"(انتظار {TOTAL}). site/data/sample.json را بررسی کنید.")
    leads = []
    for index, lead in enumerate(picked, 1):
        row = with_flags(lead)
        row["id"] = f"{RUN_ID}-l{index:02d}"          # شناسهٔ قطعی برای commit انتظارها
        leads.append(row)
    return leads


def seed_sqlite(leads: list[dict], data_dir: Path) -> None:
    """پایگاه SQLite موقت را با همان ردیف‌ها پر می‌کند."""
    os.environ["LEADGEN_DATA_DIR"] = str(data_dir)
    shutil.rmtree(data_dir, ignore_errors=True)
    from leadgen import storage
    storage.init_db()
    storage.create_run(dict(RUN))
    storage.save_leads(RUN_ID, [dict(lead) for lead in leads])


def sql_literal(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, dict)):
        return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    return "'" + str(value).replace("'", "''") + "'"


def build_insert_sql(leads: list[dict]) -> str:
    """SQL درج برای Supabase — همان شناسه‌ها، همان مقدارها (idempotent)."""
    rows = ["(" + ", ".join(sql_literal(lead.get(col)) for col in COLUMNS) + ")"
            for lead in leads]
    run_cols = ["id", "created_at", "niche_fa", "city", "area_label", "status", "engine",
                "profile", "live"]
    run_vals = ", ".join(sql_literal(RUN[col]) for col in run_cols)
    return (
        "-- فیکسچر هم‌ارزی SQLite ↔ Postgres — با tests/fixtures/cloud_parity/build.py ساخته شده\n"
        "-- اجرا: Supabase → SQL Editor → Run (یا scripts/cloud_parity.py --live --apply)\n"
        f"insert into public.runs ({', '.join(run_cols)}) values ({run_vals})\n"
        "on conflict (id) do update set city = excluded.city, area_label = excluded.area_label,\n"
        "    niche_fa = excluded.niche_fa, status = excluded.status;\n\n"
        f"insert into public.leads ({', '.join(COLUMNS)}) values\n" + ",\n".join(rows) + "\n"
        "on conflict (id) do update set score = excluded.score, segment = excluded.segment,\n"
        "    has_email = excluded.has_email, has_phone = excluded.has_phone,\n"
        "    has_website = excluded.has_website, has_social = excluded.has_social,\n"
        "    latitude = excluded.latitude, longitude = excluded.longitude,\n"
        "    gaps = excluded.gaps, tags = excluded.tags, rating = excluded.rating,\n"
        "    reviews = excluded.reviews, title = excluded.title, address = excluded.address,\n"
        "    phone = excluded.phone, emails = excluded.emails,\n"
        "    email_primary = excluded.email_primary, website = excluded.website;\n")


def scenario_params(name: str) -> dict:
    """پارامترهای یک سناریو + محدودسازی به اجرای فیکسچر.

    محدود کردن به `run_id` نتیجه را مستقل از داده‌های دیگری می‌کند که ممکن است هم‌زمان
    در پایگاه‌داده باشند (مثلاً وقتی همهٔ تست‌ها با یک DB موقت اجرا می‌شوند).
    """
    params = dict(SCENARIOS[name])
    params["run_id"] = RUN_ID
    return params


def build_expectations(leads: list[dict]) -> dict:
    """خروجی `storage.search_leads` (SQLite) برای همهٔ سناریوها."""
    from leadgen import storage
    out: dict[str, dict] = {}
    for name in SCENARIOS:
        params = scenario_params(name)
        result = storage.search_leads(limit=params.pop("limit", 50),
                                      offset=params.pop("offset", 0), **params)
        facets = result.get("facets") or {}
        out[name] = {
            "total": result["total"], "returned": result["returned"],
            "ids": [lead["id"] for lead in result["leads"]],
            "segments": facets.get("segment") or {},
            "contact": facets.get("contact") or {},
            "score_min": (facets.get("score") or {}).get("min"),
            "score_max": (facets.get("score") or {}).get("max"),
            "distances": [lead.get("distance_m") for lead in result["leads"]][:6],
        }
    out["_fixture"] = {"run_id": RUN_ID, "center": list(CENTER), "count": len(leads),
                       "ids": [lead["id"] for lead in leads],
                       "scenarios": list(SCENARIOS)}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ساخت فیکسچر هم‌ارزی SQLite/Postgres")
    parser.add_argument("--data-dir", default="/tmp/leadgen-parity",
                        help="پوشهٔ موقت پایگاه SQLite")
    parser.add_argument("--out", default=str(HERE), help="پوشهٔ خروجی (پیش‌فرض همین‌جا)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    leads = build_leads()
    seed_sqlite(leads, Path(args.data_dir))
    expectations = build_expectations(leads)
    sql = build_insert_sql(leads)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "expected.json").write_text(
        json.dumps(expectations, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out_dir / "insert.sql").write_text(sql, encoding="utf-8")
    (out_dir / "leads.json").write_text(
        json.dumps({"run": RUN, "center": CENTER, "leads": leads}, ensure_ascii=False, indent=1)
        + "\n", encoding="utf-8")

    print(f"✔ {len(leads)} لید قطعی ساخته شد")
    for name, values in expectations.items():
        if name == "_fixture":
            continue
        print(f"   {name:16s} total={values['total']:2d} returned={values['returned']:2d} "
              f"segments={values['segments']} ids={[i[-3:] for i in values['ids']]}")
    print(f"✔ نوشتن: {out_dir/'expected.json'} · {out_dir/'insert.sql'} · {out_dir/'leads.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
