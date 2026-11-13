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

import math
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable

from . import regions, tiling
from . import analytics, campaign, config, enrich, exports, geo, niches, osm_terms, scoring, storage
from .engine import EmptyResult, Engine, EngineError, make_engine

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
    enrich_budget: float | None = None   # بودجهٔ زمانی واکشی سایت‌ها (ثانیه)
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
    city_key: str = ""               # کلید استاندارد شهر (tehran) — انتخاب قطعی رابط
    area_unresolved: list = field(default_factory=list)  # خیابان/منطقه‌ای که پیدا نشد
    # ── اسکراب کامل منطقه — «هیچ داده‌ای جا نماند» ────────────────────────
    scan_full: bool = True           # کاشی‌به‌کاشی/شهر‌به‌شهر پوشش کامل منطقه
    resume_of: str = ""              # ادامهٔ اسکراب یک اجرای نیمه‌کاره (همان شناسه)
    resume_state: dict = field(default_factory=dict)  # بخش‌های انجام‌شده + شمارهٔ بخش
    watch_id: str = ""               # اگر اجرا از «دیدبان» آمده، شناسهٔ آن دیدبان
    area_applied: bool = False       # آیا نقطهٔ منطقه یک‌بار محاسبه شده؟
    zoom_set: bool = False           # zoom صریحاً داده شده؟ وگرنه از سطح منطقه می‌آید

    @classmethod
    def from_dict(cls, data: dict) -> "ScrapeRequest":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in (data or {}).items() if k in known}
        clean["zoom_set"] = "zoom" in clean
        if clean.get("enrich_budget") is not None:
            try:
                clean["enrich_budget"] = max(3.0, min(120.0, float(clean["enrich_budget"])))
            except (TypeError, ValueError):
                clean.pop("enrich_budget", None)
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
                         "keep_full_columns", "all_niches", "area_applied", "scan_full"):
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
        # انتخاب سادهٔ شهر: نقطه/جعبه همان شهر از resolve_city می‌آید؛ فقط سطح را
        # علامت می‌زنیم تا اسکراب کامل (کاشی‌بندی) برای شهر هم فعال شود.
        request.area_applied = True
        request.area_level = request.area_level or "city"
        return {"lat": request.lat, "lon": request.lon, "label": request.area_label,
                "level": request.area_level, "radius": request.radius,
                "bbox": list(request.bbox or [])}

    try:
        lat_f = float(request.lat) if request.lat else None
        lon_f = float(request.lon) if request.lon else None
    except (TypeError, ValueError):
        lat_f = lon_f = None

    # انتخاب شهر از سمت رابط (`city_key`) قطعی است؛ نام تایپ‌شده فقط وقتی
    # به کار می‌آید که کلید نداشته باشیم. قبلاً برچسب ترکیبی («خیابان ونک، تهران»)
    # به‌جای شهر می‌رفت و جست‌وجو جای دیگری می‌افتاد.
    city_arg = (request.city_key or "").strip() or request.city_query
    area = regions.resolve(province=request.province, city=city_arg,
                           district=request.district, street=request.street,
                           lat=lat_f, lon=lon_f, lang=request.lang or "fa")
    request.area_applied = True
    request.area_unresolved = list(area.get("unresolved") or [])
    if not area.get("lat"):
        return {"lat": request.lat, "lon": request.lon, "label": request.area_label,
                "level": "", "radius": request.radius}

    request.lat = f"{area['lat']:.6f}"
    request.lon = f"{area['lon']:.6f}"
    request.radius = int(request.radius or area["radius"])
    # زوم مناسبِ سطح: خیابان ۱۶، منطقه ۱۵، شهر ۱۳، استان ۱۰ (مگر کاربر خودش zoom داده باشد)
    if area.get("zoom") and not request.zoom_set:
        request.zoom = int(area["zoom"])
    if area.get("city_key"):
        request.city_key = area["city_key"]
    # نام تمیز برای نمایش/کلیدواژه؛ برچسب کامل («خیابان ولیعصر، تهران») جدا می‌ماند
    request.city_query = (area.get("city_fa") or area["label"] or request.city_query)
    request.area_label = area.get("keyword_label") or area["label"]
    request.area_level = area["level"]
    if area["level"] in ("street", "district"):
        request.grid = 0                     # شعاع کوچک است؛ کاشی‌بندی لازم نیست
        request.fast_mode = bool(request.fast_mode)
    # جعبهٔ دقیق همان سطح (خیابان، منطقه، شهر) — نه شعاع سرانگشتی
    if area.get("bbox") and area["level"] in ("street", "district"):
        request.bbox = [float(x) for x in area["bbox"]]
    return {"lat": request.lat, "lon": request.lon, "label": request.area_label,
            "level": request.area_level, "radius": request.radius, "bbox": request.bbox}


_STREET_PREFIXES = ("خیابانِ ", "خیابان ", "بلوار ", "بزرگراه ", "اتوبان ", "کوچه ",
                    "میدان ", "بن‌بست ", "بن بست ", "بازار ")


def _strip_street_prefix(text: str) -> str:
    """«خیابان کاشانی» → «کاشانی» (همان کاری که کاربر در ذهنش می‌کند)."""
    value = (text or "").strip()
    for prefix in _STREET_PREFIXES:
        if value.startswith(prefix) and len(value) > len(prefix) + 2:
            return value[len(prefix):].strip()
    return value


def _area_query_variants(street: str, district: str, city_fa: str) -> list[str]:
    """شکل‌هایی که از یک خیابان به OSM می‌پرسیم، از دقیق‌ترین به کلی‌ترین.

    تجربهٔ عملی: Nominatim با «خیابان کاشانی، یزد» گاهی هیچ نمی‌دهد ولی با
    «کاشانی، یزد» یا «کاشانی یزد» جعبهٔ خیابان را می‌دهد. قبلاً فقط یک شکل
    پرسیده می‌شد و خیابان‌های واقعی «پیدا نشد» می‌شدند.
    """
    name = (street or district or "").strip()
    short = _strip_street_prefix(name)
    city = (city_fa or "").strip()
    out: list[str] = []

    def add(value: str) -> None:
        value = " ".join((value or "").split()).strip(" ،,")
        if value and value not in out:
            out.append(value)

    if name and city:
        add(f"{name}، {city}")
        add(f"{name} {city}")
    if short and short != name:
        if city:
            add(f"{short}، {city}")
        add(short)
    if name:
        add(name)
    return out


def refine_area_bbox(request: "ScrapeRequest", city: dict, *, allow_network: bool,
                     warnings: list | None = None) -> list[float]:
    """جعبهٔ واقعیِ خیابان/محله را از OSM می‌گیرد و روی درخواست می‌گذارد.

    چرا لازم است؟ `regions` برای خیابان/محله فقط یک نقطهٔ تقریبی می‌دهد و
    اسکراب با شعاع، «تکه‌ای از خیابان» را می‌گیرد. Nominatim برای خیابان‌ها
    جعبهٔ واقعی می‌دهد؛ همان را می‌گیریم و به محدودهٔ شهر محدود می‌کنیم تا
    «دادهٔ جای دیگر» اسکراب نشود.

    اگر سرویس در دسترس نبود، بی‌صدا به همان شعاع قبلی برمی‌گردد (اجرا نمی‌شکند).
    """
    if not config.OSM_AREA_LOOKUP or not allow_network or config._looks_like_unittest():
        return list(request.bbox or [])
    city_fa = request.city_query or city.get("label_local") or ""
    variants = _area_query_variants(request.street, request.district, city_fa)
    if not variants:
        return list(request.bbox or [])
    city_box = city.get("bbox") if isinstance(city.get("bbox"), (list, tuple)) else None
    label = request.area_label or variants[0]
    try:
        from .providers import osm as osm_provider
    except Exception:                                  # pragma: no cover - import guard
        return list(request.bbox or [])

    def too_big(candidate: list[float]) -> bool:
        """جعبهٔ «شهر» را به‌جای خیابان نپذیر (وگرنه کل شهر اسکراب می‌شود)."""
        if not (city_box and len(city_box) == 4):
            return False
        try:
            c_dlat = float(candidate[1]) - float(candidate[0])
            c_dlon = float(candidate[3]) - float(candidate[2])
            b_dlat = float(city_box[1]) - float(city_box[0])
            b_dlon = float(city_box[3]) - float(city_box[2])
        except (TypeError, ValueError):
            return False
        return c_dlat >= 0.85 * b_dlat and c_dlon >= 0.85 * b_dlon

    found: dict = {}
    last_error = ""
    for query in variants:
        try:
            candidate = osm_provider.lookup_area(
                query, bbox=list(city_box) if city_box else None,
                country=city.get("country") or None, limit=10, merge=True)
        except Exception as exc:  # noqa: BLE001 — شبکه نباید اجرا را بشکند
            last_error = f"{type(exc).__name__}: {exc}"
            break                                      # سرویس پایین است؛ بقیهٔ شکل‌ها بی‌فایده‌اند
        box_c = list(candidate.get("bbox") or [])
        if len(box_c) != 4 or too_big(box_c):
            continue
        found = candidate
        break
    # Nominatim برای خیابان‌های بلند فقط یک «تکه» می‌دهد (ولیعصر: ۱ کیلومتر از ۱۷).
    # اگر Overpass در دسترس باشد، همهٔ wayهای هم‌نام را می‌گیریم و اگر بزرگ‌تر بود
    # همان را ترجیح می‌دهیم — «کل خیابان»، نه یک تکه از آن.
    if found and city_box and len(city_box) == 4:
        try:
            wide = osm_provider.overpass_street_bbox(
                [_strip_street_prefix(request.street or request.district or ""),
                 request.street or request.district or ""],
                bbox=list(city_box), timeout=min(20.0, float(getattr(request, "max_time", 60) or 60)),
                budget_s=12.0)
        except Exception:  # noqa: BLE001 — Overpass پایین باشد، همان Nominatim کافی است
            wide = {}
        wide_box = list(wide.get("bbox") or [])
        if len(wide_box) == 4 and not too_big(wide_box):
            try:
                nb = [float(x) for x in found["bbox"]]
                # فقط جعبه‌ای را می‌پذیریم که تکهٔ Nominatim را کامل در خود داشته باشد
                contains = (wide_box[0] <= nb[0] + 1e-6 and wide_box[1] >= nb[1] - 1e-6 and
                            wide_box[2] <= nb[2] + 1e-6 and wide_box[3] >= nb[3] - 1e-6)
                wider = contains and ((wide_box[1] - wide_box[0]) * (wide_box[3] - wide_box[2])
                                      > (nb[1] - nb[0]) * (nb[3] - nb[2]))
            except (KeyError, TypeError, ValueError):
                wider = False
            if wider:
                found = {"bbox": wide_box, "label": (found.get("label") or ""),
                         "streets": int(wide.get("ways") or 0)}
    if not found:
        if warnings is not None:
            if last_error:
                warnings.append(
                    f"محدودهٔ دقیق «{label}» از OSM گرفته نشد ({last_error})؛ "
                    f"با شعاع تخمینی ادامه دادیم.")
            else:
                warnings.append(
                    f"«{label}» در OSM پیدا نشد؛ برای اینکه دادهٔ جای دیگری اسکراب نشود، "
                    f"فقط محدودهٔ همین نام (اگر وجود داشت) بررسی می‌شود.")
        return list(request.bbox or [])
    box = list(found.get("bbox") or [])
    if len(box) != 4:
        return list(request.bbox or [])
    if city_box and len(city_box) == 4:
        # برش به محدودهٔ شهر — خیابان هم‌نام در شهر دیگر نباید وارد شود
        box = [max(float(box[0]), float(city_box[0])), min(float(box[1]), float(city_box[1])),
               max(float(box[2]), float(city_box[2])), min(float(box[3]), float(city_box[3]))]
        if box[1] <= box[0] or box[3] <= box[2]:
            box = list(found.get("bbox") or [])
    # حاشیهٔ کوچک (حداقل ~۱۵۰ متر) تا مغازه‌های دو طرف خیابان هم داخل بیایند؛
    # بی‌حاشیه، باریکهٔ Nominatim فقط محور خیابان را می‌گیرد و لبه‌ها جا می‌مانند.
    pad_lat = max(0.0015, (float(box[1]) - float(box[0])) * 0.02)
    pad_lon = max(0.0015, (float(box[3]) - float(box[2])) * 0.02)
    box = [round(float(box[0]) - pad_lat, 5), round(float(box[1]) + pad_lat, 5),
           round(float(box[2]) - pad_lon, 5), round(float(box[3]) + pad_lon, 5)]
    if city_box and len(city_box) == 4:
        box = [max(box[0], float(city_box[0])), min(box[1], float(city_box[1])),
               max(box[2], float(city_box[2])), min(box[3], float(city_box[3]))]
    request.bbox = [float(x) for x in box]
    request.area_unresolved = []
    # سطح را از «شهر» به سطح واقعیِ همان چیزی که پیدا شد ارتقا بده؛ وگرنه
    # گزارش/رابط می‌گوید «یزد» در حالی که فقط یک خیابان اسکراب شده است.
    if request.street:
        request.area_level = "street"
    elif request.district:
        request.area_level = "district"
    # نقطهٔ مبنا = مرکز جعبه (برای موتورهایی که فقط نقطه می‌فهمند)
    mid_lat = (box[0] + box[1]) / 2
    request.lat = f"{mid_lat:.6f}"
    request.lon = f"{(box[2] + box[3]) / 2:.6f}"
    height_km = (box[1] - box[0]) * 111
    width_km = (box[3] - box[2]) * 111 * max(0.2, abs(math.cos(math.radians(mid_lat))))
    span_km = max(height_km, width_km)
    # شعاع موتورهای نقطه‌ای هم به اندازهٔ همان خیابان/محله شود، نه شعاع ۱۰ کیلومتری شهر.
    request.radius = int(max(250, min(20000, span_km * 500)))
    if warnings is not None:
        extra = (f" — {found.get('streets')} قطعهٔ خیابان با همین نام جمع شد"
                 if found.get("streets") else "")
        warnings.append(
            f"محدودهٔ دقیق «{label}» از OSM گرفته شد "
            f"(در حدود {span_km:.1f} کیلومتر){extra} — کل این محدوده اسکراب می‌شود.")
    return request.bbox


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


def _scan_report(engine, plan_labels: list[str], done_boxes: set, resume_state: dict,
                 request: "ScrapeRequest",
                 run_id: str = "") -> tuple[dict, list[str], list[str], dict | None]:
    """گزارش «چه بخشی از منطقه اسکراب شد» + درخواست ادامهٔ خودکار.

    خروجی: (scan_info, بخش‌های انجام‌شده در این اجرا, بخش‌های باقی‌مانده, continuation)
    """
    tile_rows = []
    for item in (getattr(engine, "tile_coverage", None) or []):
        tile_rows.append({"label": item.get("label") or "",
                          "rows": int(item.get("rows") or 0),
                          "via": item.get("via") or "",
                          "error": item.get("error") or ""})
    coverage = list(getattr(engine, "coverage", None) or [])
    scan_done = []
    scan_remaining = list(plan_labels)
    if plan_labels:
        tile_map = {t["label"]: t for t in tile_rows}
        cov_map = {str(c.get("city") or ""): c for c in coverage}
        for label in plan_labels:
            tile = tile_map.get(label)
            cover = cov_map.get(label)
            attempted = bool(tile and not str(tile.get("error") or "").startswith("بودجه"))
            attempted = attempted or bool(cover and cover.get("via"))
            if attempted:
                scan_done.append(label)
        scan_remaining = [label for label in plan_labels if label not in set(scan_done)]
    chunk = int(resume_state.get("chunk") or 0)
    # «انجام‌شده» باید مجموع بخش‌های قبلی و همین بخش باشد؛ وگرنه در ادامهٔ
    # اسکراب، نوار پوشش از صفر شروع می‌شود و کاربر فکر می‌کند کاری نشده.
    labels_done = list(dict.fromkeys(scan_done + list(done_boxes)))
    all_labels = list(dict.fromkeys(list(done_boxes) + list(plan_labels)))
    # سقف زنجیرهٔ «ادامهٔ خودکار»: حداقل به اندازهٔ تعداد بخش‌های خودِ برنامه
    # (یک کاشی شهرِ بزرگ نباید فقط چون سقف ۸ بخش پر شده، بی‌ادامه بماند).
    # سقف سخت ۴۸ بخش است؛ بیشتر از آن با دکمهٔ «ادامهٔ اسکراب» اجرا می‌شود.
    max_chunks = max(int(config.SCAN_MAX_CHUNKS or 8),
                     min(48, len(all_labels) + 1))
    scan_info = {
        "full": bool(all_labels),
        "chunk": chunk,
        "total": len(all_labels),
        "done": len(labels_done),
        "labels_done": labels_done,
        "remaining": scan_remaining,
        "tiles": tile_rows,
        # درخواست فشرده تا بخش بعدی دقیقاً همین اسکراب را ادامه دهد
        "request": {k: getattr(request, k) for k in (
            "niche_query", "keywords", "city_query", "city_key", "province", "district",
            "street", "area_level", "area_label", "lang", "depth", "limit", "max_time",
            "radius", "socials", "emails", "profile", "engine_mode", "bbox", "scan_full",
            "price_range", "country", "campaign_channel", "watch_id") if hasattr(request, k)},
    }
    if tile_rows and len(tile_rows) > 1:
        scan_info["tiles_with_data"] = len([t for t in tile_rows if t["rows"]])
    continuation = None
    if all_labels and scan_remaining and chunk + 1 < max_chunks \
            and not getattr(request, "no_auto_continue", False):
        cont = {k: getattr(request, k) for k in request.__dataclass_fields__}
        this_run = run_id or request.resume_of or request.run_id
        cont.update({"resume_of": this_run, "run_id": this_run,
                     "resume_state": {"done": labels_done, "chunk": chunk + 1},
                     "area_applied": False})
        continuation = {"request": cont, "remaining": len(scan_remaining),
                        "chunk": chunk + 1, "run_id": this_run}
    return scan_info, scan_done, scan_remaining, continuation


def run_scrape(request: ScrapeRequest, on_progress: ProgressFn | None = None) -> ScrapeResult:
    """اجرای کامل پایپ‌لاین. exceptions به‌صورت اجرای failed ثبت می‌شود."""
    storage.init_db()
    resuming = bool(request.resume_of)
    run_id = request.resume_of or request.run_id or storage.new_run_id()
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
    if resuming:
        # بخش بعدی همان اسکراب است، نه اجرای تازه: ردیف قبلی حفظ می‌شود و
        # لیدهای بخش‌های قبل هم دست‌نخورده می‌مانند.
        prev: dict = {}
        try:
            prev = storage.get_run(run_id) or {}
        except Exception:  # pragma: no cover - پایگاه‌داده در دسترس نیست
            prev = {}
        if isinstance(prev.get("created_at"), (int, float)):
            run_meta["created_at"] = prev["created_at"]      # زمان شروع اجرا جابه‌جا نشود
        storage.update_run(run_id, status="running", progress={
            "stage": "working", "status": "running", "percent": 3,
            "message": "ادامهٔ اسکراب — بخش بعدی…", "run_id": run_id, "ts": time.time()})
    else:
        storage.create_run(run_meta)

    try:
        # ── ۱) صنف و شهر ─────────────────────────────────────────────────────
        apply_area(request)
        niche = resolve_niche(request.niche_query)
        _progress(on_progress, run_id, {"stage": "resolve", "status": "running", "percent": 4,
                                     "message": f"صنف: «{niche.fa}» — شهر: «{request.city_query}»"})
        if request.area_level == "province" and not request.city_key:
            # انتخاب «کل استان»: مرکز استان نقطهٔ مبناست و جعبه، کل استان است.
            prov_meta = regions.province_info(request.province)
            city = resolve_city(prov_meta.get("capital") or request.city_query,
                                request.lat, request.lon, allow_network=engine.live)
        else:
            city = resolve_city(request.city_key or request.city_query,
                                request.lat, request.lon, allow_network=engine.live)
        if not city:
            raise EngineError(
                f"مکان «{request.city_query}» پیدا نشد. یک شهر شناخته‌شده بنویسید یا "
                f"lat/lon را دستی بدهید (فهرست شهرها: GET /api/cities)."
            )
        # ── محدودهٔ واقعی خیابان/محله از خودِ OSM (کل خیابان، نه یک نقطه) ─────
        if request.area_level in ("street", "district") or request.area_unresolved:
            refine_area_bbox(request, city, allow_network=engine.live, warnings=warnings)
        if request.area_unresolved:
            # خیابان/منطقهٔ ناشناخته: به‌جای چسبیدن به نزدیک‌ترین نام، صریح می‌گوییم
            # که پیدا نشد — وگرنه کاربر فکر می‌کند «خیابان X» را اسکراب کرده، در حالی
            # که دادهٔ جای دیگری آمده است.
            bad = [request.street if "street" in request.area_unresolved else "",
                   request.district if "district" in request.area_unresolved else ""]
            bad = [x for x in bad if x]
            label_fa = "خیابان" if "street" in request.area_unresolved else "منطقه"
            raise EngineError(
                f"{label_fa} «{'، '.join(bad)}» پیدا نشد؛ برای اینکه دادهٔ جای دیگری "
                f"اسکراب نشود، اجرا متوقف شد. املای نام را درست کنید یا از "
                f"جست‌وجوی منطقه (GET /api/regions/search) نام دقیق را بردارید."
            )
        if request.bbox and len(request.bbox) == 4:
            city = dict(city)
            city["bbox"] = list(request.bbox)
        elif request.area_level == "province":
            prov_box = regions.province_info(request.province).get("bbox")
            if prov_box:
                city = dict(city)
                city["bbox"] = list(prov_box)
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
            niche_label = ("همهٔ کسب‌وکارها (تگ‌محور OSM)" if engine.name == "osm"
                           else f"همه مشاغل ({len(sweep_niches)} صنف)")
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
        if request.all_niches and engine.name == "osm":
            # جاروی تگ‌محور: یک درخواست کل شهر را می‌گردد (نه ۹۱ کوئری متنی)
            chunks = [keywords[:8] or [primary_niche.fa]]
        elif request.all_niches and len(keywords) > chunk_size:
            chunks = [keywords[i:i + chunk_size] for i in range(0, len(keywords), chunk_size)]
        else:
            # اسکیل کیت: همه کلیدواژه‌ها در یک job (به‌جای شلیک jobهای زیاد)
            chunks = [keywords]

        province_meta = regions.province_info(request.province) if request.province else {}

        # ── برنامهٔ اسکراب کامل ────────────────────────────────────────────────
        # برای شهر: کاشی‌های کوچک تا سقف ۴۰تایی Nominatim داده را نصف نکند.
        # برای استان: همهٔ شهرهای استان (نه فقط چند شهر پرجمعیت).
        resume_state = dict(request.resume_state or {})
        done_boxes = {str(x) for x in (resume_state.get("done") or [])}
        scan_tiles: list[dict] = []
        sweep_boxes: list[dict] = []
        if engine.name == "osm" and request.scan_full and config.SCAN_TILES:
            if request.area_level == "province" and not (request.district or request.street):
                planned = tiling.plan("province", province=request.province,
                                      max_tiles=int(config.SCAN_MAX_BOXES or 40))
            elif request.area_level in ("street", "district"):
                # خیابان/منطقه: جعبهٔ دقیق خودش کافی است؛ یک «کاشی» تنها برای
                # اینکه پوشش ۱/۱ در گزارش بیاید و بی‌دلیل دوباره پرسیده نشود.
                area_box = request.bbox or city.get("bbox")
                planned = (tiling.plan(request.area_level, bbox=list(area_box),
                                       city_fa=request.area_label or request.city_query)
                           if isinstance(area_box, (list, tuple)) and len(area_box) == 4
                           else [])
            else:
                planned = tiling.plan("city", bbox=city.get("bbox"),
                                      city_fa=request.city_query or request.area_label,
                                      max_tiles=int(config.SCAN_MAX_TILES or 16))
            planned = [box for box in planned if box["label"] not in done_boxes]
            if request.area_level == "province":
                # هم‌مسیر با جاروی استانی: هر شهر یک جعبه
                sweep_boxes = [{"label": box["label"], "bbox": box["bbox"],
                                "pop": int(box.get("pop") or 0)} for box in planned]
            else:
                scan_tiles = planned
        scan_plan_labels = ([box["label"] for box in scan_tiles]
                            if scan_tiles else [box["label"] for box in sweep_boxes])

        if sweep_boxes and len(sweep_boxes) > 1:
            province_fa = province_meta.get("fa") or request.province
            warnings.append(
                f"اسکراب استانی: {len(sweep_boxes)} شهر «{province_fa}» در برنامه است؛ "
                f"کار تا آخر بودجهٔ زمانی پیش می‌رود و بقیه در اجرای بعدی خودکار ادامه "
                f"می‌یابد.")
        if scan_tiles and len(scan_tiles) > 1:
            warnings.append(
                f"اسکراب کامل: {len(scan_tiles)} کاشی برای «{place_fa}» — هر کاشی جدا "
                f"پرسیده می‌شود تا سقف ۴۰ نتیجهٔ هر درخواست، داده را نصف نکند.")

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
                "osm_terms": osm_terms.derive([niche.fa, *niche.keywords_fa[:2],
                                               *niche.keywords_en[:2],
                                               str(request.niche_query or "")]),
                "province_label": (province_meta.get("fa") if is_fa
                                   else province_meta.get("en")) or "",
                "province_bbox": province_meta.get("bbox"),
                "lang_fa": is_fa,
                # موتور OSM: یک درخواست تگ‌محور برای همهٔ اصناف (بدون ۹۱ کوئری)
                "all_business": bool(request.all_niches) and engine.name == "osm",
                # چند جعبهٔ شهری برای جست‌وجوی سطح استان (خالی = همان جعبهٔ شهر)
                "sweep_boxes": sweep_boxes,
                # کاشی‌های اسکراب کامل (پر کردن جاهایی که سقف ۴۰تایی جا گذاشته)
                "scan_tiles": scan_tiles,
                # «فقط همان منطقه»: موتور حق ندارد نتیجهٔ بیرون از محدوده بدهد
                "strict_region": True,
                # پرش به کل استان فقط وقتی کاربر خودش «کل استان» را خواسته باشد
                "allow_province_fallback": request.area_level == "province",
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
        empty_hit: EmptyResult | None = None

        def on_job_progress(payload: dict, spec_index: int) -> None:
            """پیشرفت موتور را به بازه ۸٪ تا ۷۰٪ کل اجرا نقشه می‌کند."""
            event = dict(payload)
            percent = event.get("percent")
            if isinstance(percent, (int, float)):
                event["percent"] = int(8 + 62 * (spec_index - 1 + float(percent) / 100) / total_specs)
            if total_specs > 1:
                event["message"] = f"[job {spec_index}/{total_specs}] {event.get('message', '')}"
            _progress(on_progress, run_id, event)

        mode = ("province" if sweep_boxes else
                "grid" if cells else ("sweep" if request.all_niches else "job"))
        label = (f"حالت شبکه {request.grid}×{request.grid} — {len(cells)} کاشی"
                 if cells else
                 (f"جاروی تمامی مشاغل — {len(sweep_niches)} صنف، {len(specs)} job"
                  if request.all_niches else
                  f"اجرای job با {len(keywords)} کلیدواژه"))
        _progress(on_progress, run_id, {"stage": mode, "status": "working", "percent": 8,
                                        "message": f"{label} @ {city['lat']},{city['lon']}"})

        coverage: list = []          # پوشش شهرها (فقط جست‌وجوی استانی)
        for index, spec in enumerate(specs, start=1):
            try:
                job_id, spec_rows = engine.run_job(
                    spec, lambda payload, i=index: on_job_progress(payload, i))
            except EmptyResult as exc:
                # «چیزی پیدا نشد» خطا نیست؛ اجرا با صفر لید تمام می‌شود
                empty_hit = exc
                if total_specs == 1:
                    break
                warnings.append(f"job {index}/{total_specs}: {str(exc).strip()[:200]}")
                continue
            except Exception as exc:
                # شکست یک job در حالت چندjob ≠ شکست کل اجرا
                if total_specs == 1:
                    raise
                warnings.append(f"job {index}/{total_specs} رد شد: {str(exc).strip()[:160]}")
                continue
            job_ids.append(job_id)
            rows = geo.dedupe(rows + spec_rows)
            # گزارش پوشش شهرها (فقط جست‌وجوی استانی): صادقانه بگو کدام شهر داده نداشت.
            coverage = getattr(engine, "coverage", None) or coverage
            if sweep_boxes and coverage:
                served = [c for c in coverage if c.get("rows")]
                empty = [c for c in coverage if not c.get("rows")]
                if empty:
                    names = "، ".join(str(c.get("city") or "شهر") for c in empty[:6])
                    warnings.append(
                        f"پوشش استانی: {len(served)} شهر دادهٔ واقعی داد و برای "
                        f"{len(empty)} شهر ({names}) چیزی پیدا نشد.")
                    if len(served) < len(sweep_boxes):
                        warnings.append(
                            "اگر سرویس نقشه شلوغ بود، چند دقیقهٔ دیگر دوباره اجرا کنید؛ "
                            "نتیجه‌های موفق در کش می‌مانند و اجرای بعدی سریع‌تر است.")
            _progress(on_progress, run_id, {
                "stage": mode, "status": "working",
                "percent": int(8 + 62 * index / total_specs),
                "message": (f"job {index}/{total_specs}: +{len(spec_rows)} ردیف "
                            f"(یکتا: {len(rows)}) — {', '.join(spec['keywords'][:2])}"
                            + ("…" if len(spec["keywords"]) > 2 else "")),
            })

        if not rows:
            if empty_hit is not None or engine.name in ("osm", "places"):
                # «خالی» با «خطا» فرق دارد: اجرا با صفر لید تمام می‌شود تا کاربر
                # پیام روشن ببیند و صف سه بار بیهوده تکرار نکند.
                note = str(empty_hit) if empty_hit is not None else "در این محدوده چیزی پیدا نشد."
                warnings.append(note)
                attempts = getattr(engine, "attempts", None) or []
                if attempts:
                    detail = "، ".join(
                        f"«{a.get('term')}» {a.get('rows', 0)}"
                        + (f" از {a['city']}" if a.get("city") else "")
                        for a in attempts[:6])
                    warnings.append(f"مسیر جست‌وجوی متنی هم امتحان شد ({detail}) "
                                    f"و هیچ‌کدام نتیجه نداد.")
                warnings.append(
                    "پیشنهاد: محدودهٔ بزرگ‌تری (کل شهر یا استان) یا صنف دیگری را امتحان کن. "
                    "برای پوشش بیشتر، کلید Google Places API را در تنظیمات بگذار.")
                _progress(on_progress, run_id, {
                    "stage": "done", "status": "done", "percent": 100,
                    "message": note, "run_id": run_id, "empty": True})
                empty_run = dict(run_meta)
                empty_run.update({"id": run_id, "total": 0, "hot": 0, "warm": 0, "cold": 0,
                                  "avg_score": 0.0, "job_ids": job_ids, "status": "done"})
                # بخش خالی هم بخشی از اسکراب کامل است: باید بگوید کجای منطقه
                # انجام شده و بقیه کجاست — وگرنه «صفر لید» یعنی دادهٔ گم‌شده.
                empty_scan, _done, _left, empty_cont = _scan_report(
                    engine, scan_plan_labels, done_boxes, resume_state, request, run_id)
                empty_run["analytics"] = analytics.build([], empty_run, niche)
                if empty_scan.get("full"):
                    empty_run["analytics"]["scan"] = empty_scan
                empty_run["analytics"]["warnings"] = list(warnings)
                if sweep_boxes and coverage:
                    empty_run["analytics"]["cities"] = [
                        {"city": c.get("city") or "", "rows": int(c.get("rows") or 0),
                         "via": c.get("via") or "", "error": c.get("error") or ""}
                        for c in coverage
                    ]
                storage.update_run(run_id, **empty_run)
                return ScrapeResult(
                    run_id=run_id, rows=[], analytics=empty_run["analytics"],
                    run=storage.get_run(run_id) or empty_run, warnings=warnings,
                    export_paths=[],
                    stats={"total": 0, "hot": 0, "warm": 0, "cold": 0,
                           "coverage": analytics.coverage([]),
                           "enrichment": {"skipped": True}, "job_ids": job_ids,
                           "engine": engine.name, "live": engine.live,
                           "city": city["label_local"], "niche": niche.fa,
                           "keywords": keywords, "sweep_niches": [], "jobs": len(job_ids),
                           "continuation": empty_cont,
                           "scan": empty_scan if empty_scan.get("full") else {},
                           "empty": True})
            raise EngineError(
                "همه jobها خالی/ناموفق بودند. احتمالاً IP موقتاً محدود شده — چند دقیقه صبر کنید، "
                "depth را کم کنید یا پراکسی اضافه کنید."
            )
        # ── ۳٫۵) فقط همان منطقه ─────────────────────────────────────────────
        # آخرین سد: هر ردیفی که مختصاتش بیرون از محدودهٔ انتخاب‌شده باشد حذف می‌شود.
        # (خیابان = جعبهٔ خیابان، منطقه = جعبهٔ منطقه، شهر = مرز شهر، استان = کل استان)
        region_box = None
        if request.area_level in ("street", "district") and request.bbox \
                and len(request.bbox) == 4:
            region_box = [float(x) for x in request.bbox]
        elif cells:
            region_box = list(city["bbox"])
        elif request.area_level == "province" and city.get("bbox"):
            region_box = list(city["bbox"])          # جعبهٔ کل استان
        elif city.get("bbox"):
            region_box = list(city["bbox"])          # مرز شهر
        if region_box:
            kept = [r for r in rows if geo.in_bbox(r, region_box, margin=0.02)]
            dropped = len(rows) - len(kept)
            if dropped:
                warnings.append(
                    f"{dropped} ردیف خارج از محدودهٔ «{request.area_label or city['label_local']}» "
                    f"بود و حذف شد (فقط همان منطقه اسکراب می‌شود).")
                rows = kept
            if not rows:
                # همه بیرون بودند → صادقانه بگو، نه اینکه صفر لید بی‌صدا بماند
                warnings.append(
                    "همهٔ نتایج بیرون از محدودهٔ انتخاب‌شده بود؛ موتور دادهٔ این محدوده را "
                    "برنگرداند. محدوده را بزرگ‌تر کنید یا صنف دیگری را امتحان کنید.")

        # سقف نهایی بعد از امتیازدهی اعمال می‌شود تا «بهترین‌ها» بمانند نه «اولین‌ها».
        # اینجا فقط یک سقف محافظه‌کارانه می‌گذاریم تا هزینه غنی‌سازی منفجر نشود.
        pre_cap = max(request.limit * 3, 600) if request.limit else 0
        if config.MAX_LEADS_PER_RUN:
            pre_cap = min(pre_cap, config.MAX_LEADS_PER_RUN) if pre_cap else config.MAX_LEADS_PER_RUN
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
            # جاروی تگ‌محور: صنف هر ردیف از برچسب‌های خودش می‌آید
            provided = str(row.get("niche_id") or "")
            source = (niches.BY_ID.get(provided)
                      or keyword_niche.get(str(row.get("input_id") or ""))
                      or primary_niche)
            row["niche_id"] = source.id
            row["niche_fa"] = source.fa

        # ── ۵) غنی‌سازی ─────────────────────────────────────────────────────
        allow_network = engine.live if request.enrich_live is None else bool(request.enrich_live)
        enrich_stats = {"skipped": True}
        want_enrich = request.emails or request.socials or request.signals
        if want_enrich and rows:
            _progress(on_progress, run_id, {"stage": "enrich", "status": "enriching", "percent": 76,
                                             "message": "غنی‌سازی: ایمیل عمیق، شبکه‌های اجتماعی و سیگنال‌های سایت"})
            # بودجهٔ زمانی غنی‌سازی: تا کل اجرا (مثلاً روی Vercel) از سقف تابع بیرون نزند
            enrich_stats = enrich.enrich_rows(
                rows, socials=request.socials, deep_emails=request.deep_emails and request.emails,
                signals=request.signals, allow_network=allow_network,
                budget_s=(request.enrich_budget or config.ENRICH_BUDGET),
                on_progress=lambda p: _progress(on_progress, run_id, {**p, "stage": "enrich"}),
            )
            if enrich_stats.get("skipped_due_time"):
                warnings.append(
                    f"به دلیل محدودیت زمان، {enrich_stats['skipped_due_time']} کسب‌وکار بدون "
                    f"واکشی سایت ذخیره شد (تلفن/نشانی/لوکیشن کامل است).")
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

        # ── ۶٫۵) تازه یا تکراری؟ ─────────────────────────────────────────────
        # اجرای دوبارهٔ همان شهر/صنف نباید «۴۵ لید جدید» جا بزند؛ باید بگوید
        # کدام‌ها تازه‌اند. برچسب روی signals می‌نشیند (بدون تغییر schema).
        history = {}
        try:
            history = storage.seen_places([r.get("place_id") for r in rows if r.get("place_id")],
                                          exclude_run=run_id)
        except Exception as exc:  # noqa: BLE001 — این تزئین نباید اجرا را بشکند
            warnings.append(f"بررسی سابقهٔ لیدها انجام نشد ({exc}).")
        fresh = repeat = 0
        for row in rows:
            seen_in = history.get(str(row.get("place_id") or ""))
            if not isinstance(row.get("signals"), dict):
                row["signals"] = {}
            if seen_in:
                row["signals"]["repeat"] = True
                row["signals"]["first_seen_run"] = seen_in
                repeat += 1
            else:
                row["signals"].pop("repeat", None)
                row["signals"].pop("first_seen_run", None)
                fresh += 1
        if repeat:
            warnings.append(
                f"{fresh} نتیجه از {len(rows)} تازه بود؛ {repeat} مورد در اجراهای قبلی هم "
                f"دیده شده بود (در فهرست با نشان «قبلاً دیده شده» مشخص‌اند).")

        # ── ۶٫۷) ادغام با بخش‌های قبلی همین اسکراب ──────────────────────────
        # ادامهٔ اسکراب همان run_id است؛ لیدهای بخش قبل باید بمانند و فقط
        # وضعیت پیگیری (status/note) و شناسهٔ پایگاه‌داده‌شان حفظ شود.
        if resuming:
            prior: dict = {}
            try:
                for old_row in storage.load_leads(run_id, limit=5000):
                    key = str(old_row.get("place_id") or old_row.get("id") or "")
                    if key:
                        prior[key] = old_row
            except Exception as exc:  # noqa: BLE001 — ادغام نباید اجرا را بشکند
                warnings.append(f"بازیابی لیدهای بخش قبلی انجام نشد ({exc}).")
            if prior:
                prior_total = len(prior)
                merged: dict = {}
                for row in rows:
                    key = str(row.get("place_id") or "")
                    old_row = prior.pop(key, None) if key else None
                    if old_row:
                        row["id"] = old_row.get("id") or row.get("id")
                        if old_row.get("status"):
                            row["status"] = old_row["status"]
                        if old_row.get("note"):
                            row["note"] = old_row["note"]
                    merged[key or str(row.get("id") or "")] = row
                for key, old_row in prior.items():          # لیدهای بخش قبل که این‌بار نیامدند
                    merged[key] = old_row
                rows = list(merged.values())
                warnings.append(
                    f"ادامهٔ اسکراب: {prior_total} لید از بخش‌های قبلی حفظ شد؛ "
                    f"مجموع این اجرا {len(rows)} است.")

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
        stats_pack["fresh"] = {"new": fresh, "repeat": repeat}
        # هشدارهای اجرا را هم نگه می‌داریم؛ وگرنه بعد از بسته شدن مرورگر هیچ‌جا نمی‌ماند.
        stats_pack["warnings"] = list(warnings)

        # ── ۸٫۵) گزارش اسکراب کامل: چه بخشی از منطقه پوشش داده شد؟ ────────────
        scan_info, scan_done, scan_remaining, continuation = _scan_report(
            engine, scan_plan_labels, done_boxes, resume_state, request, run_id)
        all_labels = list(dict.fromkeys(list(done_boxes) + list(scan_plan_labels)))
        if all_labels:
            stats_pack["scan"] = scan_info
            if scan_remaining:
                tail = ("و خودکار ادامه داده می‌شود." if continuation
                        else "؛ با دکمهٔ «ادامهٔ اسکراب» اجرا می‌شود.")
                warnings.append(
                    f"اسکراب کامل: {len(scan_done)} بخش از {len(scan_plan_labels)} در این "
                    f"بخش زمانی انجام شد؛ {len(scan_remaining)} بخش باقی مانده "
                    f"{tail}")
            else:
                warnings.append(
                    f"اسکراب کامل: همهٔ {len(all_labels)} بخش منطقه پوشش داده شد.")
        stats_pack["warnings"] = list(warnings)
        if sweep_boxes and coverage:
            # پوشش شهری فقط برای جست‌وجوی استانی معنا دارد (یک شهر یعنی یک ردیف خالی).
            stats_pack["cities"] = [
                {"city": c.get("city") or "", "rows": int(c.get("rows") or 0),
                 "via": c.get("via") or "", "error": c.get("error") or ""}
                for c in coverage
            ]
        # analytics فقط نسخه سبک meta را نگه می‌دارد، پس ارجاع دایره‌ای ساخته نمی‌شود
        run_full["analytics"] = stats_pack
        # منطقهٔ واقعاً اسکراب‌شده را هم روی ردیف اجرا بنویس (خیابان/شهر/استان)؛
        # وگرنه فهرست اجراها فقط نام شهرِ تایپ‌شده در لحظهٔ ثبت را نشان می‌دهد.
        run_full.update({
            "city": place_fa, "city_key": city.get("key") or request.city_key or "",
            "province": request.province, "district": request.district,
            "street": request.street, "area_label": request.area_label,
            "area_level": request.area_level, "country": country,
            "lat": request.lat, "lon": request.lon,
        })
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
                # بخش بعدی (اگر مانده) — ورکر با همین، اجرا را خودکار ادامه می‌دهد
                "continuation": continuation,
                "scan": scan_info if all_labels else {},
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


def engine_status(engine: Engine | str | None = None) -> dict:
    """وضعیت موتور.

    هم شیء `Engine` (که `run_scrape` می‌سازد) و هم نام حالت («docker»/«demo»/«auto»،
    که ورکر و API می‌فرستند) پذیرفته می‌شود. پیش‌تر آرگومان نادیده گرفته می‌شد و همیشه
    `ENGINE_MODE` محیط گزارش می‌شد — یعنی ورکر با `--engine docker` هم «دمو» می‌دید.
    """
    from .engine import engine_status as _status
    if engine is None or isinstance(engine, str):
        return _status(engine or None)
    return _status(getattr(engine, "name", None) or config.ENGINE_MODE)


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


def enrich_run(run_id: str, *, budget_s: float = 20.0, limit: int = 0,
               socials: bool = True, emails: bool = True,
               on_progress: ProgressFn | None = None) -> dict:
    """غنی‌سازی مجدد لیدهای یک اجرای ذخیره‌شده — بدون اسکراپ دوباره.

    چرا لازم است؟ لیدهای OSM اغلب سایت دارند ولی ایمیل/شبکه اجتماعی‌شان در همان
    اجرا استخراج نشده (بودجهٔ زمانی تپش کوتاه بود). حالا کاربر می‌تواند بعداً
    همان اجرا را «کامل‌تر» کند؛ ردیف‌های کامل‌شده دوباره واکشی نمی‌شوند، پس اجرای
    دوباره ارزان و ایمن است (idempotent).
    """
    storage.init_db()
    run = storage.get_run(run_id)
    if not run:
        raise EngineError(f"اجرای {run_id} پیدا نشد.")
    rows = storage.all_leads_for_export(run_id)
    if not rows:
        raise EngineError("این اجرا لیدی ندارد.")

    before = {"email": sum(1 for r in rows if r.get("email_primary")),
              "social": sum(1 for r in rows if any(r.get(k) for k in
                                                   ("instagram", "facebook", "linkedin")))}
    # نامزدها: سایت دارند و یا ایمیل ندارند یا شبکه اجتماعی؛ بقیه کاری برای انجام ندارند
    todo = [r for r in rows
            if r.get("website")
            and (not r.get("email_primary") or not any(
                r.get(k) for k in ("instagram", "facebook", "linkedin")))]
    if limit and limit > 0:
        todo = todo[:int(limit)]

    _progress(on_progress, run_id, {
        "stage": "enrich", "status": "enriching", "percent": 20,
        "message": f"غنی‌سازی مجدد {len(todo)} کسب‌وکار (از {len(rows)} لید)"})
    stats = enrich.enrich_rows(todo, socials=socials, deep_emails=emails, signals=False,
                               allow_network=True, budget_s=budget_s,
                               on_progress=lambda p: _progress(on_progress, run_id,
                                                               {**p, "stage": "enrich"}))
    if todo:
        storage.save_leads(run_id, todo)

    # همهٔ ردیف‌ها یک‌بار دیگر نرمال می‌شوند (پرچم/درصد/شبکه از آدرس سایت) و
    # امتیاز با همان پروفایل قبلی بازمحاسبه می‌شود تا شکاف‌ها و بخش‌ها درست بمانند.
    saved = storage.all_leads_for_export(run_id)
    saved = [enrich.normalize_row(r) for r in saved]
    profile = run.get("profile") or "generic"
    niche = niches.find_niche(run.get("niche_id") or run.get("niche_fa") or "")
    saved = scoring.score_all(saved, profile=profile, niche=niche)
    storage.save_leads(run_id, saved)

    saved = storage.all_leads_for_export(run_id)
    after = {"email": sum(1 for r in saved if r.get("email_primary")),
             "social": sum(1 for r in saved if any(r.get(k) for k in
                                                   ("instagram", "facebook", "linkedin")))}
    seg = analytics.segments(saved)
    pack = analytics.build(saved, dict(run, profile=profile, hot=seg["hot"]["count"],
                                      warm=seg["warm"]["count"], cold=seg["cold"]["count"]), niche)
    old_pack = run.get("analytics") or {}
    for key in ("cities", "warnings"):        # بستهٔ قبلی را از دست نمی‌دهیم
        if old_pack.get(key):
            pack[key] = old_pack[key]
    note = (f"غنی‌سازی مجدد: ایمیل {before['email']}→{after['email']}، "
            f"شبکه اجتماعی {before['social']}→{after['social']} "
            f"({len(todo)} کسب‌وکار بررسی شد).")
    pack["warnings"] = list(pack.get("warnings") or []) + [note]
    storage.update_run(run_id, analytics=pack, hot=seg["hot"]["count"],
                       warm=seg["warm"]["count"], cold=seg["cold"]["count"],
                       avg_score=pack["avg_score"])
    _progress(on_progress, run_id, {"stage": "done", "status": "done", "percent": 100,
                                    "message": note})
    return {"ok": True, "run_id": run_id, "checked": len(todo), "total": len(saved),
            "email": {"before": before["email"], "after": after["email"]},
            "social": {"before": before["social"], "after": after["social"]},
            "skipped_due_time": int(stats.get("skipped_due_time") or 0),
            "note": note, "analytics": pack}


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
