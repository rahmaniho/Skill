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

from . import regions
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
    # «جست‌وجوی تمامی مشاغل» — جاروی چندصنفی یک شهر
    all_niches: bool = False
    niche_groups: list = field(default_factory=list)
    max_niches: int = 12
    keywords_per_niche: int = 2
    keywords_per_job: int = 8
    cols: str = "lead"               # پیش‌تنظیم ستون‌های خروجی: lead|contact|location|all
    run_id: str = ""                 # اگر server از قبل شناسه ساخته، همان را ادامه می‌دهیم
    # ── سطح منطقه‌ای (استان/شهر/منطقه/خیابان) — رابط جمع‌وجور ──────────────
    province: str = ""               # شناسهٔ استان یا کد کشور
    district: str = ""               # نام منطقه/محله
    street: str = ""                 # نام خیابان
    bbox: list = field(default_factory=list)   # جعبهٔ محدودکنندهٔ دلخواه [s,n,w,e]
    area_label: str = ""             # برچسب خوانای منطقه (برای نمایش در خروجی)
    area_level: str = ""             # street|district|city|province
    area_applied: bool = False       # آیا نقطهٔ منطقه یک‌بار محاسبه شده؟
    zoom_set: bool = False           # zoom صریحاً داده شده؟ وگرنه از سطح منطقه می‌آید

    @classmethod
    def from_dict(cls, data: dict) -> "ScrapeRequest":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in (data or {}).items() if k in known}
        clean["zoom_set"] = "zoom" in clean
        for int_key in ("depth", "grid", "radius", "zoom", "max_time", "limit", "area_radius"):
            if int_key in clean and clean[int_key] is not None:
                try:
                    clean[int_key] = int(clean[int_key])
                except (TypeError, ValueError):
                    clean.pop(int_key)
        if isinstance(clean.get("niche_groups"), str):
            clean["niche_groups"] = [g.strip() for g in clean["niche_groups"].split(",") if g.strip()]
        if clean.get("bbox"):
            try:
                clean["bbox"] = [float(x) for x in clean["bbox"]]
                if len(clean["bbox"]) != 4:
                    clean.pop("bbox")
            except (TypeError, ValueError):
                clean.pop("bbox")
        for bool_key in ("fast_mode", "emails", "deep_emails", "socials", "signals",
                         "keep_full_columns", "all_niches", "area_applied"):
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


def apply_area(request: "ScrapeRequest") -> dict:
    """سطح منطقه‌ای (استان/شهر/منطقه/خیابان) را به یک نقطه + شعاع + جعبه تبدیل می‌کند.

    این تابع `request` را درجا به‌روز می‌کند تا هر سه مسیر (CLI، API، وب‌اپ) یکسان کار کنند
    و دومین فراخوانی آن بی‌اثر است (idempotent).
    """
    if getattr(request, "area_applied", False):
        return {"lat": request.lat, "lon": request.lon, "label": request.area_label,
                "level": request.area_level, "radius": request.radius,
                "bbox": list(request.bbox or [])}
    if not (request.province or request.district or request.street):
        request.area_applied = True
        return {"lat": request.lat, "lon": request.lon, "label": request.area_label,
                "level": request.area_level, "radius": request.radius,
                "bbox": list(request.bbox or [])}

    try:
        lat_f = float(request.lat) if request.lat else None
        lon_f = float(request.lon) if request.lon else None
    except (TypeError, ValueError):
        lat_f = lon_f = None

    area = regions.resolve(province=request.province, city=request.city_query,
                           district=request.district, street=request.street,
                           lat=lat_f, lon=lon_f, lang=request.lang or "fa")
    request.area_applied = True
    if not area.get("lat"):
        return {"lat": request.lat, "lon": request.lon, "label": request.area_label,
                "level": "", "radius": request.radius}

    request.lat = f"{area['lat']:.6f}"
    request.lon = f"{area['lon']:.6f}"
    request.radius = int(request.radius or area["radius"])
    # زوم مناسبِ سطح: خیابان ۱۶، منطقه ۱۵، شهر ۱۳، استان ۱۰ (مگر کاربر خودش zoom داده باشد)
    if area.get("zoom") and not request.zoom_set:
        request.zoom = int(area["zoom"])
    request.city_query = area["label"] or request.city_query
    request.area_label = area.get("keyword_label") or area["label"]
    request.area_level = area["level"]
    if area["level"] in ("street", "district"):
        request.grid = 0                     # شعاع کوچک است؛ کاشی‌بندی لازم نیست
        request.fast_mode = bool(request.fast_mode)
    # جعبهٔ محدودکننده فقط برای سطح خیابان/منطقه (وگرنه شهرهای دیگرِ استان بیرون می‌افتند)
    if area["level"] in ("street", "district"):
        half = max(0.004, request.radius * 1.3 / 111000.0)
        lat_f, lon_f = area["lat"], area["lon"]
        request.bbox = [round(lat_f - half, 4), round(lat_f + half, 4),
                        round(lon_f - half, 4), round(lon_f + half, 4)]
    return {"lat": request.lat, "lon": request.lon, "label": request.area_label,
            "level": request.area_level, "radius": request.radius, "bbox": request.bbox}


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
        apply_area(request)
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
        if request.bbox and len(request.bbox) == 4:
            city = dict(city)
            city["bbox"] = list(request.bbox)
        if request.area_label:
            # کلیدواژه باید نام منطقه/خیابان را هم داشته باشد، نه فقط شهر
            city = dict(city)
            if request.lang != "en":
                city["label_local"] = request.area_label
            elif city.get("country") != "IR":
                city["label"] = request.area_label
        country = city.get("country") or ""

        # ── ۲) کلیدواژه‌ها و نقشه jobها ───────────────────────────────────────
        place_fa = city.get("label_local") or city["label"]
        place_en = city.get("label") or place_fa
        place = place_fa if request.lang.startswith("fa") else place_en
        is_fa = request.lang.startswith("fa")

        sweep_niches: list[niches.Niche] = []
        keyword_niche: dict[str, niches.Niche] = {}

        if request.all_niches:
            # «جست‌وجوی تمامی مشاغل»: چند صنف پشت‌سرهم، با یکتاسازی بین همه صنف‌ها
            sweep_niches = niches.sweep_plan(groups=request.niche_groups, limit=request.max_niches)
            if not sweep_niches:
                raise EngineError("هیچ صنفی برای جارو پیدا نشد.")
            if request.grid and request.grid > 1:
                warnings.append("حالت شبکه با «همه مشاغل» ترکیب نشد (تعداد jobها ضرب می‌شد و "
                                "احتمال rate-limit بالا می‌رفت)؛ جاروی مرکزی انجام شد.")
                request.grid = 0
            keywords = []
            for sweep_niche in sweep_niches:
                for term in niches.expand_keywords(sweep_niche, place, lang=request.lang,
                                                   limit=max(1, request.keywords_per_niche)):
                    if term not in keyword_niche:
                        keyword_niche[term] = sweep_niche
                        keywords.append(term)
            primary_niche = sweep_niches[0]
            niche_label = f"همه مشاغل ({len(sweep_niches)} صنف)"
            warnings.append(
                f"حالت «همه مشاغل»: {len(sweep_niches)} صنف × تا {request.keywords_per_niche} کلیدواژه "
                f"= {len(keywords)} کلیدواژه. jobها پشت‌سرهم اجرا می‌شوند (هر بار یکی) تا گوگل "
                f"شما را محدود نکند؛ برای حجم بالاتر پراکسی بگذارید. اجرا ادامه دارد…"
            )
        else:
            keywords = build_keywords(request, niche, city)
            if not keywords:
                raise EngineError("هیچ کلیدواژه‌ای ساخته نشد.")
            for term in keywords:
                keyword_niche[term] = niche
            primary_niche = niche
            niche_label = niche.fa
        warnings.extend(large_job_warning(request, keywords))

        radius = request.radius or primary_niche.radius
        depth = request.depth or primary_niche.depth

        run_meta.update({
            "niche_id": primary_niche.id, "niche_fa": niche_label,
            "city": place_fa, "city_key": city.get("key"), "country": country,
            "lat": city["lat"], "lon": city["lon"], "keywords": keywords,
            "radius": radius, "depth": depth,
            "province": request.province, "district": request.district,
            "street": request.street, "area_label": request.area_label,
            "area_level": request.area_level,
        })
        storage.update_run(run_id, **run_meta)

        # ── ۳) jobها ────────────────────────────────────────────────────────
        cells = geo.grid_cells(city["bbox"], request.grid) if request.grid and request.grid > 1 else []
        chunk_size = max(1, request.keywords_per_job)
        if request.all_niches and len(keywords) > chunk_size:
            chunks = [keywords[i:i + chunk_size] for i in range(0, len(keywords), chunk_size)]
        else:
            # اسکیل کیت: همه کلیدواژه‌ها در یک job (به‌جای شلیک jobهای زیاد)
            chunks = [keywords]

        def make_body(term_list: list[str], name: str, cell: dict | None = None) -> dict:
            body = {
                "name": name,
                "keywords": term_list,
                "lang": "fa" if is_fa else "en",
                "zoom": request.zoom,
                "area_hood": request.district or request.street or "",
                "lat": str(city["lat"]),          # قرارداد اسکیل: رشته
                "lon": str(city["lon"]),
                "fast_mode": request.fast_mode,
                "radius": radius,
                "depth": depth,
                "email": bool(request.emails),     # پیش‌فرض اسکیل: روشن
                "max_time": request.max_time,      # ثانیه
                # فیلدهای داخلی که فقط موتور دمو می‌خواند (موتور واقعی نادیده می‌گیرد)
                "_niche": keyword_niche.get(term_list[0], primary_niche) if term_list else primary_niche,
                "_keyword_niches": {t: keyword_niche[t].id for t in term_list if t in keyword_niche},
                "city_label": place_fa if is_fa else place_en,
                "city_key": city.get("key") or "",
                "country": country,
                "bbox": city["bbox"],
                "lang_fa": is_fa,
            }
            if cell:
                body.update(cell)                 # lat/lon/radius/zoom همان کاشی
            return body

        specs: list[dict] = []
        for chunk_index, chunk in enumerate(chunks, start=1):
            if cells:
                for tile_index, cell in enumerate(cells, start=1):
                    specs.append(make_body(chunk, f"leadgen-c{chunk_index}-t{tile_index}", cell))
            else:
                specs.append(make_body(chunk, f"leadgen-c{chunk_index}"))

        total_specs = max(1, len(specs))
        rows: list[dict] = []
        job_ids: list[str] = []

        def on_job_progress(payload: dict, spec_index: int) -> None:
            """پیشرفت موتور را به بازه ۸٪ تا ۷۰٪ کل اجرا نقشه می‌کند."""
            event = dict(payload)
            percent = event.get("percent")
            if isinstance(percent, (int, float)):
                event["percent"] = int(8 + 62 * (spec_index - 1 + float(percent) / 100) / total_specs)
            if total_specs > 1:
                event["message"] = f"[job {spec_index}/{total_specs}] {event.get('message', '')}"
            _progress(on_progress, run_id, event)

        mode = "grid" if cells else ("sweep" if request.all_niches else "job")
        label = (f"حالت شبکه {request.grid}×{request.grid} — {len(cells)} کاشی"
                 if cells else
                 (f"جاروی تمامی مشاغل — {len(sweep_niches)} صنف، {len(specs)} job"
                  if request.all_niches else
                  f"اجرای job با {len(keywords)} کلیدواژه"))
        _progress(on_progress, run_id, {"stage": mode, "status": "working", "percent": 8,
                                        "message": f"{label} @ {city['lat']},{city['lon']}"})

        for index, spec in enumerate(specs, start=1):
            try:
                job_id, spec_rows = engine.run_job(
                    spec, lambda payload, i=index: on_job_progress(payload, i))
            except Exception as exc:
                # شکست یک job در حالت چندjob ≠ شکست کل اجرا
                if total_specs == 1:
                    raise
                warnings.append(f"job {index}/{total_specs} رد شد: {str(exc).strip()[:160]}")
                continue
            job_ids.append(job_id)
            rows = geo.dedupe(rows + spec_rows)
            _progress(on_progress, run_id, {
                "stage": mode, "status": "working",
                "percent": int(8 + 62 * index / total_specs),
                "message": (f"job {index}/{total_specs}: +{len(spec_rows)} ردیف "
                            f"(یکتا: {len(rows)}) — {', '.join(spec['keywords'][:2])}"
                            + ("…" if len(spec["keywords"]) > 2 else "")),
            })

        if not rows:
            raise EngineError(
                "همه jobها خالی/ناموفق بودند. احتمالاً IP موقتاً محدود شده — چند دقیقه صبر کنید، "
                "depth را کم کنید یا پراکسی اضافه کنید."
            )
        if cells:
            kept = [r for r in rows if geo.in_bbox(r, city["bbox"])]
            dropped = len(rows) - len(kept)
            if dropped:
                warnings.append(f"{dropped} ردیف بیرون از محدوده شهر حذف شد.")
            rows = kept

        # سقف نهایی بعد از امتیازدهی اعمال می‌شود تا «بهترین‌ها» بمانند نه «اولین‌ها».
        # اینجا فقط یک سقف محافظه‌کارانه می‌گذاریم تا هزینه غنی‌سازی منفجر نشود.
        pre_cap = max(request.limit * 3, 600) if request.limit else 0
        if pre_cap and len(rows) > pre_cap:
            warnings.append(f"{len(rows) - pre_cap} ردیف برای کنترل هزینه غنی‌سازی موقتاً کنار "
                            f"گذاشته شد؛ سقف نهایی ({request.limit}) بعد از امتیازدهی اعمال می‌شود.")
            rows = rows[:pre_cap]

        # ── ۴) پاک‌سازی ─────────────────────────────────────────────────────
        before = len(rows)
        rows = geo.dedupe(rows)
        if before - len(rows):
            warnings.append(f"{before - len(rows)} ردیف تکراری حذف شد (بر اساس place_id).")
        _progress(on_progress, run_id, {"stage": "clean", "status": "working", "percent": 72,
                                         "message": f"پاک‌سازی: {len(rows)} ردیف منحصر به فرد"})

        for row in rows:
            row.setdefault("city", place_fa)
            row.setdefault("input_id", row.get("input_id") or (keywords[0] if keywords else ""))
            source = keyword_niche.get(str(row.get("input_id") or "")) or primary_niche
            row["niche_id"] = source.id
            row["niche_fa"] = source.fa

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
        if request.limit and len(rows) > request.limit:
            rows = rows[:request.limit]           # rows مرتب‌شده بر اساس امتیاز است

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

        slug_niche = "all-trades" if request.all_niches else primary_niche.id
        # نام فایل: صنف + شهر + (منطقه/خیابان اگر انتخاب شده باشد) + شناسهٔ اجرا
        slug_area = exports.safe_filename(request.street or request.district or "")
        slug = (f"{slug_niche}-{(city.get('key') or exports.safe_filename(city['label']))}"
                f"{f'-{slug_area}' if slug_area else ''}-{run_id}")
        paths = []
        main_fmt = request.export_fmt or "xlsx"
        preset = request.cols if not request.keep_full_columns else "all"
        if main_fmt in ("xlsx", "excel"):
            sheets = exports.build_workbook(saved_rows, stats_pack, run_full, campaign_rows,
                                            full=request.keep_full_columns, preset=preset)
            paths.append(exports.write_xlsx(exports.default_path(slug, "xlsx"), sheets))
        elif main_fmt == "json":
            paths.append(exports.write_json(
                exports.default_path(slug, "json"),
                {"run": run_full, "analytics": stats_pack, "leads": saved_rows}))
        else:
            paths.append(exports.write_csv(exports.default_path(slug, "csv"), saved_rows,
                                           full=request.keep_full_columns, preset=preset))
        # همیشه یک CSV ساده هم کنارش (سازگار با گوگل‌شیت)
        if main_fmt != "csv":
            paths.append(exports.write_csv(exports.default_path(slug + "-leads", "csv"),
                                           saved_rows, preset="contact"))
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
                "job_ids": job_ids,
                "engine": engine.name,
                "live": engine.live,
                "city": city["label_local"],
                "niche": niche.fa,
                "keywords": keywords,
                "sweep_niches": [n.fa for n in sweep_niches],
                "jobs": len(job_ids),
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
