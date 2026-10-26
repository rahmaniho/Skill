"""پایپ‌لاین اصلی: از «صنف + شهر» تا «فایل خروجی قابل فروش».

مراحل (مطابق اسکیل کیت + لایه هوش این برنامه):
  ۱. resolve  — صنف و شهر (جداول داخلی + Nominatim در صورت نیاز)
  ۲. keywords — گسترش کلیدواژه با مکان bake شده («dentists in Denver CO»)
  ۳. job(s)   — یک job با آرایه کلیدواژه‌ها؛ در حالت grid، n×n job پشت‌سرهم
  ۴. clean    — حذف تکراری (place_id)، فیلتر محدوده شهر، نرمال‌سازی
  ۵. enrich   — ایمیل عمیق + شبکه‌های اجتماعی + سیگنال‌های سایت (صفر توکن)
  ۶. score    — امتیاز ۰-۱۰۰، بخش‌بندی داغ/گرم/سرد، برچسب‌ها، کانال، زاویه فروش
  ۷. save     — SQLite (اجرا + لیدها) تا داشبورد تاریخچه و پیگیری داشته باشد
  ۸. analyze  — قیف، توزیع‌ها، شکاف‌ها، نقشه، بینش‌ها
  ۹. export   — CSV / JSON / XLSX در data/exports

قاعده اسکیل رعایت می‌شود: برای jobهای بزرگ فقط **هشدار** می‌دهیم، متوقف نمی‌کنیم.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

from . import analytics, campaign, config, enrich, exports, geo, niches, scoring, storage
from .engine import Engine, EngineError, make_engine

ProgressFn = Callable[[dict], None]

# هشدار اسکیل: آستانه‌هایی که job را «بزرگ» می‌کنند
LARGE_DEPTH = 15
LARGE_KEYWORDS = 10


@dataclass
class ScrapeRequest:
    niche_query: str = ""
    city_query: str = ""
    lat: str = ""
    lon: str = ""
    lang: str = "en"                 # زبان کلیدواژه‌های ارسالی به موتور
    keywords: list[str] = field(default_factory=list)   # کلیدواژه دستی (اختیاری)
    depth: int = 5
    grid: int = 0                    # ۰ = بدون شبکه؛ ۲..۱۰ = n×n کاشی
    radius: int | None = None
    zoom: int = 15
    max_time: int = 600
    fast_mode: bool = False
    emails: bool = True
    deep_emails: bool = True
    socials: bool = True
    signals: bool = True
    profile: str = "generic"
    enrich_live: bool | None = None  # None = خودکار (فقط اگر موتور واقعی باشد)
    engine_mode: str | None = None
    export_fmt: str = "xlsx"         # csv | json | xlsx
    sender: dict | None = None
    campaign_channel: str = "email"
    keep_full_columns: bool = False
    limit: int = 0                   # ۰ = همه
    run_id: str = ""                 # اگر server از قبل شناسه ساخته، همان را ادامه می‌دهیم

    @classmethod
    def from_dict(cls, data: dict) -> "ScrapeRequest":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in (data or {}).items() if k in known}
        for int_key in ("depth", "grid", "radius", "zoom", "max_time", "limit"):
            if int_key in clean and clean[int_key] is not None:
                try:
                    clean[int_key] = int(clean[int_key])
                except (TypeError, ValueError):
                    clean.pop(int_key)
        for bool_key in ("fast_mode", "emails", "deep_emails", "socials", "signals", "keep_full_columns"):
            if bool_key in clean and clean[bool_key] is not None:
                clean[bool_key] = bool(clean[bool_key])
        return cls(**clean)


@dataclass
class ScrapeResult:
    run_id: str
    rows: list[dict]
    analytics: dict
    run: dict
    warnings: list[str]
    export_paths: list[str]
    stats: dict


def _progress(on_progress: ProgressFn | None, run_id: str, payload: dict | None = None) -> None:
    """ارسال یک رویداد پیشرفت به تماس‌گیرنده (CLI یا سرور وب)."""
    if not on_progress:
        return
    event = dict(payload or {})
    event["run_id"] = run_id                      # همیشه شناسه جاری برنده است
    event.setdefault("ts", time.time())
    on_progress(event)


def resolve_niche(query: str) -> niches.Niche:
    return niches.find_niche(query or "")


def resolve_city(query: str, lat: str = "", lon: str = "", allow_network: bool = True) -> dict:
    """شهر → {lat, lon, bbox, key, label, label_local, country}.

    `label` نام استاندارد/لاتین است (برای کلیدواژه انگلیسی که به موتور می‌رود) و
    `label_local` نام محلی یا همان چیزی است که کاربر نوشته (برای کلیدواژه فارسی،
    نشانی و متن کمپین). اگر مختصات دستی داده شود همان مبناست.
    """
    key = geo.offline_lookup(query)
    if lat and lon:
        label = query or f"{lat},{lon}"
        return {
            "lat": str(lat), "lon": str(lon),
            "bbox": geo.default_bbox(float(lat), float(lon)),
            "key": key, "label": geo.city_label(key, label),
            "label_local": geo.local_label(key, label),
            "country": geo.CITIES.get(key, (0, 0, "", []))[2] if key else "",
        }
    lat_s, lon_s, bbox, found_key = geo.geocode(query, with_bbox=True, allow_network=allow_network)
    if not lat_s:
        return {}
    key = key or found_key
    country = geo.CITIES.get(key, (0, 0, "", []))[2] if key else ""
    return {
        "lat": lat_s, "lon": lon_s, "bbox": bbox or geo.default_bbox(float(lat_s), float(lon_s)),
        "key": key, "label": geo.city_label(key, query),
        "label_local": geo.local_label(key, query), "country": country,
    }


def build_keywords(request: ScrapeRequest, niche: niches.Niche, city: dict, limit: int = 6) -> list[str]:
    """کلیدواژه‌های نهایی job — همیشه مکان داخلشان هست (قاعده اسکیل)."""
    manual = [k.strip() for k in (request.keywords or []) if k and k.strip()]
    is_fa = request.lang.startswith("fa")
    place = city.get("label_local" if is_fa else "label") or city.get("label") or ""
    if manual:
        out = []
        for kw in manual:
            if place and place.lower() not in kw.lower() and " in " not in kw and " در " not in kw:
                sep = " در " if any("\u0600" <= ch <= "\u06FF" for ch in kw) else " in "
                kw = kw + sep + place
            out.append(kw)
        return out[:max(1, limit * 2)]
    return niches.expand_keywords(
        niche, place, lang=request.lang, limit=limit,
        with_neighborhoods=bool(request.grid), city_key=city.get("key"),
    )


def large_job_warning(request: ScrapeRequest, keywords: list[str]) -> list[str]:
    """هشدار کوتاه (یک‌بار) بدون مسدود کردن اجرا — دقیقاً قاعده «warn, don't block»."""
    warnings = []
    tiles = (request.grid or 1) ** 2
    if request.depth >= LARGE_DEPTH or len(keywords) >= LARGE_KEYWORDS or tiles >= 4:
        warnings.append(
            "این یک job بزرگ است (عمق بالا، کلیدواژه زیاد یا حالت شبکه). اجرای پشت‌سرهم بدون "
            "پراکسی ممکن است IP شما را موقتاً نزد گوگل محدود کند (دقیقه تا ساعت؛ jobها failed یا "
            "خالی برمی‌گردند). برای اجراهای بزرگ/تکراری پراکسی اضافه کنید. اجرا ادامه دارد…"
        )
    if tiles >= 9:
        warnings.append(
            f"حالت شبکه {request.grid}×{request.grid} = {tiles} job پشت‌سرهم؛ کندتر است و احتمال "
            f"rate-limit بیشتر. عمق را پایین نگه دارید."
        )
    return warnings


def run_scrape(request: ScrapeRequest, on_progress: ProgressFn | None = None) -> ScrapeResult:
    """اجرای کامل پایپ‌لاین. exceptions به‌صورت اجرای failed ثبت می‌شود."""
    storage.init_db()
    run_id = request.run_id or storage.new_run_id()
    warnings: list[str] = []

    engine: Engine = make_engine(request.engine_mode)
    status = engine_status(engine)

    run_meta = {
        "id": run_id,
        "created_at": time.time(),
        "engine": engine.name,
        "live": engine.live,
        "status": "running",
        "profile": request.profile,
        "socials": request.socials,
        "emails": request.emails,
        "depth": request.depth,
        "grid": request.grid or 1,
        "lang": request.lang,
    }
    storage.create_run(run_meta)

    try:
        # ── ۱) صنف و شهر ─────────────────────────────────────────────────────
        niche = resolve_niche(request.niche_query)
        _progress(on_progress, run_id, {"stage": "resolve", "status": "running", "percent": 4,
                                     "message": f"صنف: «{niche.fa}» — شهر: «{request.city_query}»"})
        city = resolve_city(request.city_query, request.lat, request.lon,
                            allow_network=engine.live)
        if not city:
            raise EngineError(
                f"مکان «{request.city_query}» پیدا نشد. یک شهر شناخته‌شده بنویسید یا "
                f"lat/lon را دستی بدهید (فهرست شهرها: GET /api/cities)."
            )
        country = city.get("country") or ""

        # ── ۲) کلیدواژه‌ها ───────────────────────────────────────────────────
        keywords = build_keywords(request, niche, city)
        if not keywords:
            raise EngineError("هیچ کلیدواژه‌ای ساخته نشد.")
        warnings.extend(large_job_warning(request, keywords))

        radius = request.radius or niche.radius
        depth = request.depth or niche.depth

        run_meta.update({
            "niche_id": niche.id, "niche_fa": niche.fa, "city": city["label"],
            "city_key": city.get("key"), "country": country,
            "lat": city["lat"], "lon": city["lon"], "keywords": keywords,
            "radius": radius, "depth": depth,
        })
        storage.update_run(run_id, **run_meta)

        # ── ۳) jobها ────────────────────────────────────────────────────────
        base_body = {
            "name": f"leadgen-{niche.id}",
            "keywords": keywords,
            "lang": "fa" if request.lang.startswith("fa") else "en",
            "zoom": request.zoom,
            "lat": str(city["lat"]),
            "lon": str(city["lon"]),
            "fast_mode": request.fast_mode,
            "radius": radius,
            "depth": depth,
            "email": bool(request.emails),
            "max_time": request.max_time,
            # فیلدهای داخلی که فقط موتور دمو می‌خواند (موتور واقعی نادیده می‌گیرد)
            "_niche": niche,
            "city_label": city["label_local"] if request.lang.startswith("fa") else city["label"],
            "city_key": city.get("key") or "",
            "country": country,
            "bbox": city["bbox"],
            "lang_fa": request.lang.startswith("fa"),
        }

        rows: list[dict] = []
        job_ids: list[str] = []
        cells = geo.grid_cells(city["bbox"], request.grid) if request.grid and request.grid > 1 else []

        def on_job_progress(payload: dict) -> None:
            """پیشرفت موتور را به بازه ۸٪ تا ۷۰٪ نقشه می‌کند."""
            event = dict(payload)
            percent = event.get("percent")
            if isinstance(percent, (int, float)):
                event["percent"] = int(8 + 62 * float(percent) / 100)
            _progress(on_progress, run_id, event)

        if not cells:
            _progress(on_progress, run_id, {"stage": "job", "status": "working", "percent": 8,
                                             "message": f"اجرای job با {len(keywords)} کلیدواژه "
                                                        f"@ {city['lat']},{city['lon']}"})
            job_id, raw_rows = engine.run_job(base_body, on_job_progress)
            job_ids.append(job_id)
            rows = raw_rows
        else:
            _progress(on_progress, run_id, {"stage": "grid", "status": "working", "percent": 8,
                                             "message": f"حالت شبکه {request.grid}×{request.grid}: "
                                                        f"{len(cells)} job پشت‌سرهم"})
            for index, cell in enumerate(cells, start=1):
                cell_body = dict(base_body, **cell)
                cell_body["name"] = f"leadgen-{niche.id}-tile{index}"

                def cell_progress(payload: dict, index=index) -> None:
                    payload = dict(payload)
                    payload["_cell_frac"] = 1.0
                    payload["percent"] = int(100 * (index - 1 + (payload.get("percent") or 0) / 100) / len(cells))
                    payload["message"] = f"کاشی {index}/{len(cells)} — " + str(payload.get("message", ""))
                    on_job_progress(payload)

                try:
                    job_id, cell_rows = engine.run_job(cell_body, cell_progress)
                except Exception as exc:  # یک کاشی شکست خورد ≠ کل اجرا شکست خورد
                    warnings.append(f"کاشی {index}/{len(cells)} رد شد: {str(exc).strip()[:160]}")
                    continue
                job_ids.append(job_id)
                before = len(rows)
                rows = geo.dedupe(rows + cell_rows)
                _progress(on_progress, run_id, {"stage": "grid", "status": "working",
                                                 "percent": int(8 + 62 * index / len(cells)),
                                                 "message": f"کاشی {index}/{len(cells)}: +{len(cell_rows)} ردیف "
                                                            f"(یکتا: {len(rows)})"})
            if not rows:
                raise EngineError("همه کاشی‌ها خالی/ناموفق بودند. احتمالاً rate-limit — صبر کنید یا پراکسی اضافه کنید.")
            kept = [r for r in rows if geo.in_bbox(r, city["bbox"])]
            dropped = len(rows) - len(kept)
            if dropped:
                warnings.append(f"{dropped} ردیف بیرون از محدوده شهر حذف شد.")
            rows = kept

        if request.limit and len(rows) > request.limit:
            rows = rows[:request.limit]

        # ── ۴) پاک‌سازی ─────────────────────────────────────────────────────
        before = len(rows)
        rows = geo.dedupe(rows)
        if before - len(rows):
            warnings.append(f"{before - len(rows)} ردیف تکراری حذف شد (بر اساس place_id).")
        _progress(on_progress, run_id, {"stage": "clean", "status": "working", "percent": 72,
                                         "message": f"پاک‌سازی: {len(rows)} ردیف منحصر به فرد"})

        for row in rows:
            row.setdefault("city", city.get("label_local") or city["label"])
            row.setdefault("input_id", row.get("input_id") or (keywords[0] if keywords else ""))

        # ── ۵) غنی‌سازی ─────────────────────────────────────────────────────
        allow_network = engine.live if request.enrich_live is None else bool(request.enrich_live)
        enrich_stats = {"skipped": True}
        want_enrich = request.emails or request.socials or request.signals
        if want_enrich and rows:
            _progress(on_progress, run_id, {"stage": "enrich", "status": "enriching", "percent": 76,
                                             "message": "غنی‌سازی: ایمیل عمیق، شبکه‌های اجتماعی و سیگنال‌های سایت"})
            enrich_stats = enrich.enrich_rows(
                rows, socials=request.socials, deep_emails=request.deep_emails and request.emails,
                signals=request.signals, allow_network=allow_network,
                on_progress=lambda p: _progress(on_progress, run_id, {**p, "stage": "enrich"}),
            )
            if allow_network and enrich_stats.get("emails_after", 0) > enrich_stats.get("emails_before", 0):
                gained = enrich_stats["emails_after"] - enrich_stats["emails_before"]
                warnings.append(f"پاس عمیق ایمیل {gained} ایمیل جدید از صفحه‌های تماس/درباره پیدا کرد.")
        else:
            rows = [enrich.normalize_row(r) for r in rows]

        # ── ۶) امتیازدهی ────────────────────────────────────────────────────
        _progress(on_progress, run_id, {"stage": "score", "status": "scoring", "percent": 90,
                                         "message": "امتیازدهی با پروفایل «"
                                                    + str(scoring.PROFILES.get(request.profile, {}).get("fa", request.profile))
                                                    + "»"})
        rows = scoring.score_all(rows, profile=request.profile, niche=niche)

        # ── ۷) ذخیره ────────────────────────────────────────────────────────
        storage.save_leads(run_id, rows)
        saved_rows = storage.all_leads_for_export(run_id)   # با idهای پایگاه‌داده
        seg = analytics.segments(saved_rows)
        cov = analytics.coverage(saved_rows)

        # ── ۸) تحلیل ────────────────────────────────────────────────────────
        run_full = dict(run_meta)
        run_full.update({
            "id": run_id, "total": len(saved_rows), "hot": seg["hot"]["count"],
            "warm": seg["warm"]["count"], "cold": seg["cold"]["count"],
            "avg_score": round(sum(int(r.get("score") or 0) for r in saved_rows) / max(1, len(saved_rows)), 1),
            "job_ids": job_ids, "status": "done",
        })
        stats_pack = analytics.build(saved_rows, run_full, niche)
        # analytics فقط نسخه سبک meta را نگه می‌دارد، پس ارجاع دایره‌ای ساخته نمی‌شود
        run_full["analytics"] = stats_pack
        storage.update_run(run_id, **run_full)

        # ── ۹) خروجی فایل + کمپین ───────────────────────────────────────────
        _progress(on_progress, run_id, {"stage": "export", "status": "working", "percent": 96,
                                         "message": "ساخت فایل‌های خروجی و متن کمپین"})
        sender = campaign.Sender.from_dict(request.sender)
        top = [r for r in saved_rows if r.get("segment") in ("hot", "warm")][:400]
        campaign_rows = []
        for row in top:
            channel = row.get("channel") if row.get("channel") in campaign.CHANNELS else request.campaign_channel
            if channel == "email" and not row.get("email_primary"):
                channel = "whatsapp" if row.get("phone") else request.campaign_channel
            message = campaign.generate(row, channel, sender, niche)
            campaign_rows.append({
                **row, "channel": channel, "channel_fa": campaign.CHANNEL_FA.get(channel, channel),
                "to": message.get("to", ""), "subject": message.get("subject", ""),
                "body": message.get("text") or message.get("body", ""),
            })

        slug = (f"{niche.id}-{(city.get('key') or exports.safe_filename(city['label']))}-{run_id}")
        paths = []
        main_fmt = request.export_fmt or "xlsx"
        if main_fmt in ("xlsx", "excel"):
            sheets = exports.build_workbook(saved_rows, stats_pack, run_full, campaign_rows,
                                            full=request.keep_full_columns)
            paths.append(exports.write_xlsx(exports.default_path(slug, "xlsx"), sheets))
        elif main_fmt == "json":
            paths.append(exports.write_json(
                exports.default_path(slug, "json"),
                {"run": run_full, "analytics": stats_pack, "leads": saved_rows}))
        else:
            paths.append(exports.write_csv(exports.default_path(slug, "csv"), saved_rows,
                                           full=request.keep_full_columns))
        # همیشه یک CSV ساده هم کنارش (سازگار با گوگل‌شیت)
        if main_fmt != "csv":
            paths.append(exports.write_csv(exports.default_path(slug + "-leads", "csv"), saved_rows))
        # فایل کمپین آماده ارسال
        if campaign_rows:
            paths.append(exports.write_csv(
                exports.default_path(slug + f"-campaign-{request.campaign_channel}", "csv"),
                campaign_rows))

        _progress(on_progress, run_id, {"stage": "done", "status": "done", "percent": 100,
                                         "message": f"تکمیل شد — {len(saved_rows)} لید "
                                                    f"({seg['hot']['count']} داغ)"})

        return ScrapeResult(
            run_id=run_id,
            rows=saved_rows,
            analytics=stats_pack,
            run=storage.get_run(run_id) or run_full,
            warnings=warnings,
            export_paths=[str(p) for p in paths],
            stats={
                "total": len(saved_rows),
                "hot": seg["hot"]["count"], "warm": seg["warm"]["count"], "cold": seg["cold"]["count"],
                "coverage": cov,
                "enrichment": enrich_stats,
                "jobs": job_ids,
                "engine": engine.name,
                "live": engine.live,
                "city": city["label_local"],
                "niche": niche.fa,
                "keywords": keywords,
            },
        )

    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        failed_meta = {k: v for k, v in run_meta.items() if k not in ("id", "status")}
        failed_meta.update(status="failed", error=detail)
        storage.update_run(run_id, **failed_meta)
        _progress(on_progress, run_id, {"stage": "failed", "status": "failed", "percent": 100,
                                        "message": f"خطا: {detail}", "error": detail,
                                        "trace": traceback.format_exc().splitlines()[-8:]})
        raise


def engine_status(engine: Engine | None = None) -> dict:
    from .engine import engine_status as _status
    return _status()


def rescore_run(run_id: str, profile: str, on_progress: ProgressFn | None = None) -> dict:
    """امتیازدهی مجدد یک اجرای ذخیره‌شده با پروفایل دیگر — بدون اسکراپ دوباره."""
    storage.init_db()
    run = storage.get_run(run_id)
    if not run:
        raise EngineError(f"اجرای {run_id} پیدا نشد.")
    rows = storage.all_leads_for_export(run_id)
    niche = niches.find_niche(run.get("niche_id") or run.get("niche_fa") or "")
    _progress(on_progress, run_id, {"stage": "score", "status": "scoring", "percent": 50,
                                     "message": f"امتیازدهی مجدد {len(rows)} لید با پروفایل «{profile}»"})
    rows = scoring.score_all(rows, profile=profile, niche=niche)
    storage.save_leads(run_id, rows)
    saved = storage.all_leads_for_export(run_id)
    seg = analytics.segments(saved)
    pack = analytics.build(saved, dict(run, profile=profile, hot=seg["hot"]["count"],
                                       warm=seg["warm"]["count"], cold=seg["cold"]["count"]), niche)
    storage.update_run(run_id, profile=profile, analytics=pack,
                       hot=seg["hot"]["count"], warm=seg["warm"]["count"], cold=seg["cold"]["count"],
                       avg_score=pack["avg_score"])
    return {"run_id": run_id, "analytics": pack, "total": len(saved),
            "hot": seg["hot"]["count"], "profile": profile}


def import_leads(rows: list[dict], *, niche_query: str = "", city: str = "",
                 profile: str = "generic", source: str = "import") -> dict:
    """فهرست موجود (CSV/JSON) را می‌گیرد و همان لایه هوش را رویش اجرا می‌کند.

    این قابلیت «داده از قبل دارید» را پوشش می‌دهد: فایل CRM قدیمی → امتیاز + کمپین.
    """
    storage.init_db()
    run_id = storage.new_run_id()
    alias = {
        "name": "title", "business": "title", "نام": "title", "کسب و کار": "title",
        "tel": "phone", "telephone": "phone", "mobile": "phone", "تلفن": "phone", "موبایل": "phone",
        "mail": "emails", "ایمیل": "emails", "site": "website", "سایت": "website",
        "وب سایت": "website", "نشانی": "address", "آدرس": "address", "category": "category",
        "صنف": "category", "rating": "review_rating", "امتیاز": "review_rating",
        "reviews": "review_count", "instagram": "instagram", "اینستاگرام": "instagram",
    }
    cleaned = []
    for raw in rows:
        row = {}
        for key, value in (raw or {}).items():
            target = alias.get(str(key).strip().lower(), str(key).strip().lower())
            row[target] = value
        if not (row.get("title") or row.get("phone") or row.get("emails") or row.get("website")):
            continue
        row.setdefault("city", city)
        cleaned.append(row)

    niche = niches.find_niche(niche_query or (cleaned[0].get("category") if cleaned else ""))
    storage.create_run({
        "id": run_id, "created_at": time.time(), "niche_id": niche.id, "niche_fa": niche.fa,
        "city": city, "engine": source, "live": False, "status": "enriching",
        "keywords": ["(وارد شده از فایل)"], "profile": profile,
    })
    cleaned = [enrich.normalize_row(r) for r in cleaned]
    cleaned = geo.dedupe(cleaned)
    cleaned = scoring.score_all(cleaned, profile=profile, niche=niche)
    storage.save_leads(run_id, cleaned)
    saved = storage.all_leads_for_export(run_id)
    seg = analytics.segments(saved)
    pack = analytics.build(saved, {"id": run_id, "niche_fa": niche.fa, "city": city,
                                  "engine": source, "keywords": ["(وارد شده از فایل)"]}, niche)
    storage.update_run(run_id, status="done", total=len(saved), hot=seg["hot"]["count"],
                       warm=seg["warm"]["count"], cold=seg["cold"]["count"],
                       avg_score=pack["avg_score"], analytics=pack)
    return {"run_id": run_id, "total": len(saved), "hot": seg["hot"]["count"],
            "analytics": pack}
