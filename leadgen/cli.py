"""رابط خط فرمان (CLI) — همان پایپ‌لاین، بدون مرورگر.

نمونه‌ها:
  python3 -m leadgen.cli run "دندانپزشکی" --city "تهران" --profile web_design --socials
  python3 -m leadgen.cli run "plumbers" --city "Denver, CO" --depth 5 --export csv
  python3 -m leadgen.cli run "رستوران" --city "دبی" --grid 3 --lang fa
  python3 -m leadgen.cli niches | cities | profiles
  python3 -m leadgen.cli leads <run-id> --segment hot --limit 20
  python3 -m leadgen.cli export <run-id> --fmt xlsx
  python3 -m leadgen.cli campaign <run-id> --channel whatsapp --limit 10
  python3 -m leadgen.cli score-file my-leads.csv --profile marketing
  python3 -m leadgen.cli doctor
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

from . import (analytics, campaign, config, engine as engine_mod, exports, geo, niches,
               pipeline, regions, scoring, storage)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    line = "  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("─" * w for w in widths)]
    for row in rows:
        out.append("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(out)


# ── دستور: run ───────────────────────────────────────────────────────────────
def cmd_run(args: argparse.Namespace) -> int:
    if args.all_niches and not args.niche:
        args.niche = ""                      # صنف از فهرست جارو می‌آید
    elif not args.niche and not args.keyword:
        print("✗ صنف را بنویسید یا --all-niches را برای جست‌وجوی تمامی مشاغل بگذارید.", file=sys.stderr)
        return 2
    request = pipeline.ScrapeRequest(
        niche_query=args.niche,
        city_query=args.city,
        lat=args.lat or "",
        lon=args.lon or "",
        keywords=args.keyword or [],
        lang=args.lang,
        depth=args.depth,
        grid=args.grid or 0,
        radius=args.radius,
        zoom=args.zoom,
        max_time=args.max_time,
        fast_mode=args.fast_mode,
        emails=not args.no_email,
        deep_emails=not args.no_deep_emails,
        socials=args.socials,
        signals=not args.no_signals,
        profile=args.profile,
        engine_mode=args.engine,
        export_fmt=args.export,
        keep_full_columns=args.full,
        limit=args.limit or 0,
        cols=args.cols,
        all_niches=args.all_niches,
        niche_groups=[g.strip() for g in (args.groups or "").split(",") if g.strip()],
        max_niches=args.max_niches,
        keywords_per_niche=args.keywords_per_niche,
        keywords_per_job=args.keywords_per_job,
        province=args.province or "",
        district=args.district or "",
        street=args.street or "",
    )

    def on_progress(payload: dict) -> None:
        message = payload.get("message") or payload.get("status") or ""
        percent = payload.get("percent")
        prefix = f"[{percent:>3}%]" if isinstance(percent, (int, float)) else "[    ]"
        print(f"\r{prefix} {message}"[:150], end="", flush=True)
        if payload.get("stage") in ("done", "failed"):
            print()

    if args.all_niches:
        groups = f" — دسته‌ها: {args.groups}" if args.groups else " (همه دسته‌ها)"
        print(f"▶ جست‌وجوی تمامی مشاغل: تا {args.max_niches} صنف × {args.keywords_per_niche} کلیدواژه"
              f"{groups}")
    else:
        print(f"▶ صنف: «{args.niche}»", end="")
    print(f"  شهر: «{args.city or f'{args.lat},{args.lon}'}»  "
          f"پروفایل: {args.profile}  شبکه‌های اجتماعی: {'بله' if args.socials else 'خیر'}"
          f"  ستون‌ها: {args.cols}")
    if not args.no_email:
        print("ℹ استخراج ایمیل روشن است (سایت هر کسب‌وکار بازدید می‌شود؛ کمی کندتر). "
              "برای اجرای سریع: --no-email")

    try:
        result = pipeline.run_scrape(request, on_progress=on_progress)
    except Exception as exc:
        print(f"\n✗ اجرا ناموفق: {exc}", file=sys.stderr)
        return 1

    stats = result.stats
    cov = stats["coverage"]
    print()
    print("━" * 68)
    print(f"✓ {stats['total']} لید  |  موتور: {stats['engine']}"
          f"{' (واقعی)' if stats['live'] else ' (دمو — داده نمونه)'}")
    print(f"  داغ 🔥 {stats['hot']}   گرم ☀️ {stats['warm']}   سرد ❄️ {stats['cold']}")
    print(f"  ایمیل: {cov['email']}  تلفن: {cov['phone']}  سایت: {cov['website']}  "
          f"شبکه اجتماعی: {cov['any_social']}")
    print(f"  شناسه اجرا: {result.run_id}")
    for path in result.export_paths:
        print(f"  📄 {path}")
    for warning in result.warnings:
        print(textwrap.fill(f"⚠ {warning}", 68, initial_indent="  ", subsequent_indent="    "))
    print("━" * 68)

    if not args.quiet:
        preview = result.rows[:args.preview]
        if preview:
            print("\nنمونه لیدهای برتر:")
            rows = [[r.get("score"), (r.get("title") or "")[:34], r.get("phone") or "—",
                     (r.get("email_primary") or "—")[:34], r.get("segment_fa") or ""]
                    for r in preview]
            print(_table(rows, ["امتیاز", "نام", "تلفن", "ایمیل", "بخش"]))

    if args.json_out:
        _print_json({"run_id": result.run_id, "stats": stats, "exports": result.export_paths})
    print("\n⚖ یادآوری قانونی: ایمیل و تلفن داده شخصی‌اند. برای تماس، مبنای قانونی، "
          "هویت فرستنده و گزینه «لغو دریافت» را رعایت کنید (GDPR / CAN-SPAM).")
    return 0


# ── دستورهای فهرست‌گیری ──────────────────────────────────────────────────────
def cmd_niches(args: argparse.Namespace) -> int:
    query = args.query
    if query:
        found = niches.find_niche(query)
        print(f"✓ نزدیک‌ترین صنف: {found.fa} ({found.en}) — شناسه: {found.id}")
        print(f"  کلیدواژه نمونه: {', '.join(niches.expand_keywords(found, args.city or 'Tehran'))}")
        return 0
    items = niches.all_niches()
    rows = [[n["group"], n["fa"], n["id"], n["digital_need"]] for n in items]
    print(_table(rows, ["دسته", "صنف", "شناسه", "نیاز دیجیتال"]))
    print(f"\n{len(items)} صنف آماده + هر صنف دلخواه (خودکار ساخته می‌شود).")
    return 0


def cmd_cities(args: argparse.Namespace) -> int:
    cities = geo.known_cities()
    rows = [[c["fa"] or c["label"], c["label"], c["country"], f'{c["lat"]:.4f}', f'{c["lon"]:.4f}']
            for c in cities]
    print(_table(rows, ["نام", "کلید", "کشور", "lat", "lon"]))
    print(f"\n{len(cities)} شهر داخلی (آفلاین). هر شهر دیگری با Nominatim یا lat/lon دستی.")
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    rows = []
    for profile in scoring.profile_list():
        weights = ", ".join(f"{k}:{v}" for k, v in profile["weights"].items())
        rows.append([profile["id"], profile["fa"], weights, profile["hot_threshold"]])
    print(_table(rows, ["شناسه", "پروفایل", "وزن‌ها", "آستانه داغ"]))
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    storage.init_db()
    runs = storage.list_runs(limit=args.limit)
    if not runs:
        print("هنوز اجرایی ثبت نشده. با `python3 -m leadgen.cli run …` شروع کنید.")
        return 0
    rows = [[r["id"], r.get("niche_fa") or "—", r.get("city") or "—", r["engine"],
             r["total"], r["hot"], r["status_fa"], r.get("avg_score")]
            for r in runs]
    print(_table(rows, ["شناسه", "صنف", "شهر", "موتور", "لید", "داغ", "وضعیت", "میانگین"]))
    return 0


def cmd_leads(args: argparse.Namespace) -> int:
    storage.init_db()
    rows = storage.load_leads(args.run, segment=args.segment, status=args.status,
                              query=args.q, min_score=args.min_score, limit=args.limit)
    if not rows:
        print("لیدی با این فیلترها پیدا نشد.")
        return 0
    table = [[r.get("score"), (r.get("title") or "")[:36], r.get("phone") or "—",
              (r.get("email_primary") or "—")[:32], r.get("channel_fa") or r.get("channel") or "—",
              storage.LEAD_STATUS_FA.get(r.get("status"), r.get("status"))] for r in rows]
    print(_table(table, ["امتیاز", "نام", "تلفن", "ایمیل", "کانال", "وضعیت"]))
    print(f"\n{len(rows)} ردیف نمایش داده شد.")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """جست‌وجوی قوی در همهٔ داده‌های جمع‌شده (همهٔ اجراها) با فیلترهای ترکیبی."""
    storage.init_db()
    lat = lon = None
    radius = args.radius_m or 0
    if args.district or args.street or (args.city and radius):
        area = regions.resolve(province=args.province or "", city=args.city or "",
                               district=args.district or "", street=args.street or "")
        if area.get("lat"):
            lat, lon = area["lat"], area["lon"]
            radius = radius or area.get("radius") or 0
            print(f"◈ محدوده: {area.get('label')} — شعاع {radius} متر "
                  f"({lat:.5f}, {lon:.5f})", file=sys.stderr)
        else:
            print("✗ محدوده پیدا نشد؛ بدون فیلتر شعاع ادامه می‌دهم.", file=sys.stderr)

    result = storage.search_leads(
        q=args.query or "", run_id=args.run, segment=args.segment, status=args.status,
        niche_id=args.niche_id, niche_group=args.group,
        has_phone=_tri(args.has_phone), has_email=_tri(args.has_email),
        has_website=_tri(args.has_website), has_social=_tri(args.has_social),
        has_instagram=_tri(args.has_instagram),
        min_rating=args.min_rating, min_reviews=args.min_reviews, min_score=args.min_score,
        lat=lat, lon=lon, radius_m=radius, sort=args.sort, order=args.order,
        limit=args.limit, offset=args.offset)
    rows = result["leads"]
    if not rows:
        print("لیدی با این فیلترها پیدا نشد.")
        return 0

    facets = result["facets"]
    seg = facets.get("segment", {})
    print(f"✓ {result['total']} لید (نمایش {len(rows)}) — "
          f"داغ {seg.get('hot', 0)} · گرم {seg.get('warm', 0)} · سرد {seg.get('cold', 0)} · "
          f"میانگین امتیاز {facets.get('score', {}).get('avg', 0)}")
    table = [[r.get("score"), (r.get("title") or "")[:34], r.get("niche_fa") or "—",
              (r.get("run_area") or r.get("city") or "—")[:24],
              r.get("phone") or "—", (r.get("email_primary") or "—")[:28],
              (f"{r.get('distance_m')}m" if r.get("distance_m") is not None else "—")]
             for r in rows]
    print(_table(table, ["امتیاز", "نام", "صنف", "محدوده", "تلفن", "ایمیل", "فاصله"]))

    if args.export:
        for row in rows:
            row["gaps_fa"] = [scoring.GAP_FA.get(g, g) for g in (row.get("gaps") or [])]
        fmt = args.export
        out = Path(args.out) if args.out else exports.default_path(f"search-{fmt}", fmt)
        meta = {"id": "search", "niche_fa": args.query or "جست‌وجوی ترکیبی",
                "city": rows[0].get("city") or "", "engine": "cli-search", "keywords": []}
        if fmt in ("xlsx", "excel"):
            path = exports.write_xlsx(out.with_suffix(".xlsx"), exports.build_workbook(
                rows, analytics.build(rows, meta), meta, full=args.full, preset=args.cols))
        elif fmt == "json":
            path = exports.write_json(out.with_suffix(".json"),
                                      {"filters": result["filters"], "total": result["total"],
                                       "leads": rows})
        else:
            path = exports.write_csv(out.with_suffix(".csv"), rows, full=args.full,
                                     preset=args.cols)
        print(f"✓ خروجی {fmt}: {path}")
    return 0


def cmd_areas(args: argparse.Namespace) -> int:
    """فهرست استان‌ها/شهرها/منطقه‌ها/خیابان‌های داخلی (آفلاین)."""
    if args.provinces:
        rows = [[p["fa"], p["en"], len(p["cities"]), p["capital_fa"]]
                for p in regions.provinces()]
        print(_table(rows, ["استان", "نام لاتین", "شهرها", "مرکز"]))
        return 0
    if args.city:
        key = regions.city_key(args.city)
        if not key:
            print(f"✗ شهر پیدا نشد: {args.city}", file=sys.stderr)
            return 1
        if args.streets:
            rows = [[r["fa"], r["district"] or "—", r["lat"], r["lon"]]
                    for r in regions.streets(key, args.district or "")]
            print(_table(rows, ["خیابان", "منطقه", "lat", "lon"]))
        else:
            rows = [[r["fa"], r["lat"], r["lon"]] for r in regions.districts(key)]
            print(_table(rows, ["منطقه", "lat", "lon"]))
        print(f"\n{len(rows)} ردیف برای {regions.city_fa(key)}")
        return 0
    rows = []
    for province in regions.provinces():
        for city in province["cities"]:
            rows.append([province["fa"], city["fa"], city.get("districts", 0),
                         city.get("streets", 0), city["lat"], city["lon"]])
    for country in regions.countries():
        for city in country["cities"]:
            rows.append([country["fa"], city["fa"], city.get("districts", 0),
                         city.get("streets", 0), city["lat"], city["lon"]])
    print(_table(rows[:args.limit], ["استان/کشور", "شهر", "منطقه", "خیابان", "lat", "lon"]))
    print(f"\n{len(rows)} شهر داخلی (نمایش {min(args.limit, len(rows))})")
    return 0


def _tri(value):
    """پرچم سه‌حالتهٔ CLI: None یعنی «فرقی نمی‌کند»."""
    if value is None:
        return None
    return value in ("1", "true", "yes", True)


def cmd_export(args: argparse.Namespace) -> int:
    storage.init_db()
    run = storage.get_run(args.run)
    if not run:
        print(f"✗ اجرای {args.run} پیدا نشد.", file=sys.stderr)
        return 1
    rows = storage.all_leads_for_export(args.run)
    if args.segment:
        rows = [r for r in rows if r.get("segment") == args.segment]
    for row in rows:
        row["gaps_fa"] = [scoring.GAP_FA.get(g, g) for g in (row.get("gaps") or [])]
    out = Path(args.out) if args.out else exports.default_path(f"{args.run}-{args.fmt}", args.fmt)
    fmt = args.fmt
    if fmt in ("xlsx", "excel"):
        sheets = exports.build_workbook(rows, run.get("analytics"), run, full=args.full,
                                        preset=args.cols)
        path = exports.write_xlsx(out.with_suffix(".xlsx"), sheets)
    elif fmt == "json":
        path = exports.write_json(out.with_suffix(".json"),
                                  {"run": run, "analytics": run.get("analytics"), "leads": rows})
    else:
        path = exports.write_csv(out.with_suffix(".csv"), rows, full=args.full, preset=args.cols)
    print(f"✓ {len(rows)} لید → {path}")
    return 0


def cmd_campaign(args: argparse.Namespace) -> int:
    storage.init_db()
    run = storage.get_run(args.run)
    if not run:
        print(f"✗ اجرای {args.run} پیدا نشد.", file=sys.stderr)
        return 1
    sender = campaign.Sender(
        name=args.sender_name or "تیم رشد ما", company=args.sender_company or "استودیو دیجیتال",
        phone=args.sender_phone or "", email=args.sender_email or "",
        website=args.sender_website or "", offer=args.offer or "۳۰ دقیقه مشاوره رایگان",
    )
    niche = niches.find_niche(run.get("niche_id") or "")
    rows = storage.load_leads(args.run, segment=args.segment, limit=args.limit)
    rows = [r for r in rows if r.get("channel") or True]

    if args.out:
        campaign_rows = []
        for row in rows:
            channel = args.channel or (row.get("channel") if row.get("channel") in campaign.CHANNELS else "email")
            if channel == "email" and not row.get("email_primary"):
                channel = "whatsapp" if row.get("phone") else channel
            message = campaign.generate(row, channel, sender, niche)
            campaign_rows.append({**row, "channel": channel,
                                  "channel_fa": campaign.CHANNEL_FA.get(channel, channel),
                                  "to": message.get("to", ""), "subject": message.get("subject", ""),
                                  "body": message.get("text") or message.get("body", "")})
        path = Path(args.out)
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            import csv
            writer = csv.DictWriter(fh, fieldnames=[fa for _, fa in exports.CAMPAIGN_COLUMNS])
            writer.writeheader()
            for row in campaign_rows:
                writer.writerow({fa: exports._value(row, key) for key, fa in exports.CAMPAIGN_COLUMNS})
        print(f"✓ {len(campaign_rows)} پیام آماده ارسال → {path}")
        return 0

    for row in rows[:args.limit]:
        channel = args.channel or row.get("channel") or "email"
        message = campaign.generate(row, channel, sender, niche)
        print("─" * 68)
        print(f"◆ {row.get('title')}  |  امتیاز {row.get('score')}  |  کانال: {message['channel_fa']}")
        print(f"  گیرنده: {message.get('to') or '—'}")
        if message.get("subject"):
            print(f"  موضوع: {message['subject']}")
        print(textwrap.fill(message.get("body", ""), 68, initial_indent="  ", subsequent_indent="  "))
    print("─" * 68)
    return 0


def cmd_score_file(args: argparse.Namespace) -> int:
    """فهرست موجود (CSV/JSON) را می‌گیرد و امتیاز + کمپین می‌دهد — بدون اسکراپ."""
    path = Path(args.file)
    if not path.is_file():
        print(f"✗ فایل پیدا نشد: {path}", file=sys.stderr)
        return 1
    if path.suffix.lower() == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(rows, dict):
            rows = rows.get("leads") or rows.get("rows") or []
    else:
        rows = exports.read_csv(path)
    result = pipeline.import_leads(rows, niche_query=args.niche, city=args.city,
                                   profile=args.profile, source=f"import:{path.name}")
    print(f"✓ {result['total']} لید امتیازدهی شد ({result['hot']} داغ) — اجرا: {result['run_id']}")
    pack = result["analytics"]
    for insight in pack.get("insights", [])[:6]:
        print(textwrap.fill(f"• {insight['fa']}", 78))
    out = exports.default_path(f"{path.stem}-scored", args.fmt)
    if args.fmt in ("xlsx", "excel"):
        leads = storage.all_leads_for_export(result["run_id"])
        final = exports.write_xlsx(out.with_suffix(".xlsx"),
                                   exports.build_workbook(leads, pack, {"id": result["run_id"]}))
    else:
        leads = storage.all_leads_for_export(result["run_id"])
        final = exports.write_csv(out.with_suffix(".csv"), leads)
    print(f"  📄 {final}")
    return 0


def cmd_rescore(args: argparse.Namespace) -> int:
    storage.init_db()
    result = pipeline.rescore_run(args.run, args.profile)
    print(f"✓ امتیازدهی مجدد {result['total']} لید با پروفایل «{args.profile}» — "
          f"داغ: {result['hot']}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """بررسی سلامت محیط: موتور، داکر، پایگاه‌داده، پوشه‌ها."""
    print("── بررسی سلامت ───────────────────────────────────────────────")
    status = engine_mod.engine_status()
    print(f"حالت تنظیم‌شده      : {status['mode']}")
    print(f"موتور فعال          : {status['active']} ({'واقعی' if status['live'] else 'دمو'})")
    print(f"دلیل                : {status['reason']}")
    docker = status["docker"]
    print(f"داکر @ {docker.get('base_url')}: {'UP' if docker.get('up') else 'DOWN'}"
          + (f" — {docker.get('error')}" if docker.get("error") else ""))
    if not docker.get("up"):
        print("   → برای داده واقعی: docker compose up -d  (کیت اصلی را ببینید)")
    print(f"تنظیمات             : {json.dumps(config.summary(), ensure_ascii=False)}")
    storage.init_db()
    stats = storage.pipeline_stats()
    print(f"پایگاه‌داده          : {config.DB_PATH} — {stats['runs']} اجرا، {stats['leads']} لید")
    print(f"اصناف               : {len(niches.CATALOG)} صنف آماده + صنف سفارشی")
    print(f"شهرهای داخلی        : {len(geo.CITIES)}")
    print("───────────────────────────────────────────────────────────────")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="leadgen",
        description="LeadGen Studio — لید کسب‌وکارهای محلی (روی اسکیل google-maps-scraper)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="اجرای اسکراپ کامل و تولید خروجی")
    p_run.add_argument("niche", nargs="?", default="",
                       help='صنف یا عبارت آزاد، مثال: "دندانپزشکی" یا "plumbers" '
                            '(با --all-niches اختیاری است)')
    p_run.add_argument("--city", help='شهر، مثال: "تهران" یا "Denver, CO"')
    p_run.add_argument("--province", help='استان (برای مرکز استان یا جست‌وجوی سطح استان)')
    p_run.add_argument("--district", help='منطقه/محله، مثال: "ونک" یا "چیتگر" (شعاع ۲۵۰۰ متر)')
    p_run.add_argument("--street", help='خیابان، مثال: "خیابان ملاصدرا" (شعاع ۹۰۰ متر)')
    p_run.add_argument("--lat"); p_run.add_argument("--lon")
    p_run.add_argument("--keyword", action="append", help="کلیدواژه دستی (تکرارپذیر)")
    p_run.add_argument("--lang", default="en", choices=["en", "fa"], help="زبان کلیدواژه‌های ارسالی به موتور")
    p_run.add_argument("--depth", type=int, default=5)
    p_run.add_argument("--grid", type=int, default=0, help="n×n کاشی برای پوشش کل شهر (۲ تا ۱۰)")
    p_run.add_argument("--radius", type=int)
    p_run.add_argument("--zoom", type=int, default=15)
    p_run.add_argument("--max-time", type=int, default=600)
    p_run.add_argument("--fast-mode", action="store_true")
    p_run.add_argument("--no-email", action="store_true", help="بدون استخراج ایمیل (سریع‌تر)")
    p_run.add_argument("--no-deep-emails", action="store_true")
    p_run.add_argument("--socials", action="store_true", help="اینستاگرام/فیسبوک/لینکدین از سایت‌ها")
    p_run.add_argument("--no-signals", action="store_true")
    p_run.add_argument("--profile", default="generic", choices=list(scoring.PROFILES))
    p_run.add_argument("--engine", default=None, choices=["auto", "docker", "demo"])
    p_run.add_argument("--export", default="xlsx", choices=["csv", "json", "xlsx"])
    p_run.add_argument("--cols", default="lead", choices=list(exports.COLUMN_PRESETS),
                       help="پیش‌تنظیم ستون‌های خروجی: lead | contact (کارت تماس کامل + لوکیشن دقیق) "
                            "| location (فقط مکان و تماس) | all (همه ستون‌ها)")
    p_run.add_argument("--full", action="store_true", help="همه ستون‌های خام در خروجی")
    p_run.add_argument("--all-niches", action="store_true",
                       help="جست‌وجوی تمامی مشاغل: جاروی چندصنفی یک شهر با یکتاسازی بین صنف‌ها")
    p_run.add_argument("--groups", help='محدود کردن جارو به دسته‌ها، با کاما: "سلامت و پزشکی,غذا و نوشیدنی"')
    p_run.add_argument("--max-niches", type=int, default=12, help="سقف تعداد صنف در حالت جارو (پیش‌فرض ۱۲)")
    p_run.add_argument("--keywords-per-niche", type=int, default=2, help="کلیدواژه به ازای هر صنف در جارو")
    p_run.add_argument("--keywords-per-job", type=int, default=8, help="کلیدواژه در هر job (جارو)")
    p_run.add_argument("--limit", type=int, default=0)
    p_run.add_argument("--preview", type=int, default=8)
    p_run.add_argument("--quiet", action="store_true")
    p_run.add_argument("--json-out", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_niches = sub.add_parser("niches", help="فهرست اصناف یا یافتن نزدیک‌ترین صنف")
    p_niches.add_argument("query", nargs="?", help="عبارت آزاد برای جست‌وجو")
    p_niches.add_argument("--city", default="Tehran")
    p_niches.set_defaults(func=cmd_niches)

    p_cities = sub.add_parser("cities", help="فهرست شهرهای داخلی")
    p_cities.set_defaults(func=cmd_cities)

    p_profiles = sub.add_parser("profiles", help="پروفایل‌های امتیازدهی")
    p_profiles.set_defaults(func=cmd_profiles)

    p_runs = sub.add_parser("runs", help="فهرست اجراها")
    p_runs.add_argument("--limit", type=int, default=30)
    p_runs.set_defaults(func=cmd_runs)

    p_leads = sub.add_parser("leads", help="نمایش لیدهای یک اجرا")
    p_leads.add_argument("run")
    p_leads.add_argument("--segment", choices=["hot", "warm", "cold"])
    p_leads.add_argument("--status", choices=list(storage.LEAD_STATUSES))
    p_leads.add_argument("--min-score", type=int)
    p_leads.add_argument("--q", help="جست‌وجو در نام/نشانی/دامنه/ایمیل")
    p_leads.add_argument("--limit", type=int, default=25)
    p_leads.set_defaults(func=cmd_leads)

    p_search = sub.add_parser("search", help="جست‌وجوی قوی در همهٔ داده‌های جمع‌شده")
    p_search.add_argument("query", nargs="?", default="", help="متن جست‌وجو (چند کلمه = AND)")
    p_search.add_argument("--run", help="محدود به یک اجرا")
    p_search.add_argument("--city"); p_search.add_argument("--province")
    p_search.add_argument("--district", help="منطقه برای فیلتر شعاع")
    p_search.add_argument("--street", help="خیابان برای فیلتر شعاع")
    p_search.add_argument("--radius-m", type=int, default=0, help="شعاع متر (پیش‌فرض از سطح محدوده)")
    p_search.add_argument("--segment", choices=["hot", "warm", "cold"])
    p_search.add_argument("--status", choices=list(storage.LEAD_STATUSES))
    p_search.add_argument("--niche-id"); p_search.add_argument("--group")
    p_search.add_argument("--has-phone", nargs="?", const="1", choices=["1", "0"])
    p_search.add_argument("--has-email", nargs="?", const="1", choices=["1", "0"])
    p_search.add_argument("--has-website", nargs="?", const="1", choices=["1", "0"])
    p_search.add_argument("--has-social", nargs="?", const="1", choices=["1", "0"])
    p_search.add_argument("--has-instagram", nargs="?", const="1", choices=["1", "0"])
    p_search.add_argument("--min-rating", type=float)
    p_search.add_argument("--min-reviews", type=int)
    p_search.add_argument("--min-score", type=int)
    p_search.add_argument("--sort", default="score",
                          choices=["score", "rating", "reviews", "title", "created_at", "completeness"])
    p_search.add_argument("--order", default="desc", choices=["desc", "asc"])
    p_search.add_argument("--limit", type=int, default=25)
    p_search.add_argument("--offset", type=int, default=0)
    p_search.add_argument("--export", choices=["csv", "json", "xlsx"], help="خروجی از نتیجهٔ فیلترشده")
    p_search.add_argument("--out", help="مسیر فایل خروجی")
    p_search.add_argument("--cols", default="contact", choices=list(exports.COLUMN_PRESETS))
    p_search.add_argument("--full", action="store_true")
    p_search.set_defaults(func=cmd_search)

    p_areas = sub.add_parser("areas", help="فهرست استان/شهر/منطقه/خیابان (آفلاین)")
    p_areas.add_argument("--city", help="شهر برای فهرست منطقه‌ها یا خیابان‌ها")
    p_areas.add_argument("--province", help="استان (اختیاری)")
    p_areas.add_argument("--district", help="فیلتر خیابان‌ها بر اساس منطقه")
    p_areas.add_argument("--streets", action="store_true", help="خیابان‌ها به‌جای منطقه‌ها")
    p_areas.add_argument("--provinces", action="store_true", help="فقط فهرست استان‌ها")
    p_areas.add_argument("--limit", type=int, default=40)
    p_areas.set_defaults(func=cmd_areas)

    p_export = sub.add_parser("export", help="خروجی فایل از یک اجرا")
    p_export.add_argument("run")
    p_export.add_argument("--fmt", default="xlsx", choices=["csv", "json", "xlsx"])
    p_export.add_argument("--segment", choices=["hot", "warm", "cold"])
    p_export.add_argument("--cols", default="lead", choices=list(exports.COLUMN_PRESETS),
                          help="پیش‌تنظیم ستون‌ها (contact = کارت تماس کامل + لوکیشن دقیق)")
    p_export.add_argument("--full", action="store_true")
    p_export.add_argument("--out")
    p_export.set_defaults(func=cmd_export)

    p_camp = sub.add_parser("campaign", help="تولید متن کمپین برای لیدها")
    p_camp.add_argument("run")
    p_camp.add_argument("--channel", choices=list(campaign.CHANNELS))
    p_camp.add_argument("--segment", choices=["hot", "warm", "cold"])
    p_camp.add_argument("--limit", type=int, default=5)
    p_camp.add_argument("--out", help="ذخیره در CSV آماده ارسال")
    p_camp.add_argument("--sender-name"); p_camp.add_argument("--sender-company")
    p_camp.add_argument("--sender-phone"); p_camp.add_argument("--sender-email")
    p_camp.add_argument("--sender-website"); p_camp.add_argument("--offer")
    p_camp.set_defaults(func=cmd_campaign)

    p_score = sub.add_parser("score-file", help="امتیازدهی به فهرست موجود (CSV/JSON)")
    p_score.add_argument("file")
    p_score.add_argument("--niche", default="")
    p_score.add_argument("--city", default="")
    p_score.add_argument("--profile", default="generic", choices=list(scoring.PROFILES))
    p_score.add_argument("--fmt", default="xlsx", choices=["csv", "xlsx"])
    p_score.set_defaults(func=cmd_score_file)

    p_rescore = sub.add_parser("rescore", help="امتیازدهی مجدد یک اجرا")
    p_rescore.add_argument("run")
    p_rescore.add_argument("--profile", required=True, choices=list(scoring.PROFILES))
    p_rescore.set_defaults(func=cmd_rescore)

    p_doctor = sub.add_parser("doctor", help="بررسی سلامت محیط")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
