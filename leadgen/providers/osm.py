"""موتور دادهٔ واقعی روی OpenStreetMap — بدون داکر، بدون کلید، قابل اجرا روی Vercel.

چرا این موتور؟
    موتور اصلی (گوگل‌مپ/داکر) روی لپ‌تاپ و Codespace عالی است، ولی روی توابع
    serverless نه داکر هست و نه زمان کافی. OSM از طریق سرویس عمومی Nominatim
    **دادهٔ واقعی** می‌دهد: نام، نشانی، تلفن، سایت، اینستاگرام، ساعات کاری و
    مختصات دقیق — و برای شروع کار (و نسخهٔ رایگان) کافی است.

سیاست استفاده (مهم — رعایت می‌شود):
    • حداکثر ~۱ درخواست در ثانیه با فاصلهٔ حداقلی و قفل سراسری (`OSM_MIN_INTERVAL`).
    • User-Agent شناسانامه با نشانی پروژه (الزام Nominatim).
    • هر اجرا فقط چند کوئری محدود؛ محدود به bbox شهر → بدون دانلود دسته‌ای.
    • دادهٔ ODbL است؛ نسبت‌دهی «© OpenStreetMap contributors» در اپ و خروجی‌ها می‌آید.

نتیجه با همان قرارداد موتور اصلی برمی‌گردد (ردیف‌هایی با کلیدهای CSV خام).
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import config, osm_tags

# فاصلهٔ حداقلی بین دو درخواست در *همهٔ* رشته‌های همین پروسه
_LOCK = threading.Lock()
_LAST_CALL = [0.0]
_HEALTH_CACHE: dict = {"ts": 0.0, "payload": None}
HEALTH_TTL = 300.0

USER_AGENT = (
    "LeadGenNova/1.0 (+https://github.com/rahmaniho/Skill; business lead finder; "
    "contact: rahmani.hosseine@gmail.com)"
)

# برچسب‌های OSM → دستهٔ خوانا (برای ستون category)
_CATEGORY_FA = {
    "restaurant": "رستوران", "fast_food": "فست‌فود", "cafe": "کافه", "bakery": "نانوایی",
    "dentist": "دندانپزشکی", "doctors": "پزشک", "clinic": "درمانگاه", "pharmacy": "داروخانه",
    "hospital": "بیمارستان", "veterinary": "دامپزشکی", "car_repair": "تعمیرگاه خودرو",
    "car": "فروش خودرو", "car_wash": "کارواش", "hairdresser": "آرایشگاه", "beauty": "سالن زیبایی",
    "clothes": "پوشاک", "shoes": "کفش", "jewelry": "طلا و جواهر", "florist": "گل‌فروشی",
    "furniture": "مبلمان", "electronics": "لوازم برقی", "mobile_phone": "موبایل",
    "hardware": "ابزار و یراق", "supermarket": "سوپرمارکت", "convenience": "فروشگاه",
    "carpenter": "نجاری", "plumber": "لوله‌کشی", "electrician": "برق‌کاری", "painter": "نقاشی ساختمان",
    "hvac": "تهویه و تاسیسات", "glaziery": "شیشه‌بری", "tailor": "خیاطی", "gym": "باشگاه ورزشی",
    "fitness_centre": "باشگاه ورزشی", "lawyer": "وکیل", "accountant": "حسابداری",
    "estate_agent": "مشاور املاک", "travel_agency": "آژانس مسافرتی", "hotel": "هتل",
    "guest_house": "اقامتگاه", "kindergarten": "مهدکودک", "school": "آموزشگاه",
    "driving_school": "آموزشگاه رانندگی", "laundry": "خشکشویی", "dry_cleaning": "خشکشویی",
    "butcher": "قصابی", "greengrocer": "میوه و تره‌بار", "kiosk": "سوپرمارکت",
    "computer": "کامپیوتر", "stationery": "لوازم‌التحریر", "sports": "لوازم ورزشی",
    "toys": "اسباب‌بازی", "books": "کتاب‌فروشی", "optician": "عینک‌سازی", "photo": "عکاسی",
    "printing": "چاپ و تبلیغات", "advertising_agency": "تبلیغات", "it": "خدمات کامپیوتری",
    "insurance": "بیمه", "bank": "بانک", "confectionery": "قنادی", "fastfood": "فست‌فود",
}

_KIND_KEYS = ("amenity", "shop", "craft", "office", "healthcare", "tourism", "leisure", "club")


class OSMDownError(RuntimeError):
    """سرویس OSM پاسخ نداد / محدود کرد."""


def _http_json(url: str, *, timeout: float) -> dict | list:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "fa,en;q=0.8",
    }, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = (resp.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as exc:
        raise OSMDownError(f"OSM HTTP {exc.code}") from exc
    except Exception as exc:  # شبکه/تایم‌اوت/DNS
        raise OSMDownError(f"{type(exc).__name__}: {exc}") from exc
    if "json" not in ctype and not raw.lstrip().startswith((b"[", b"{")):
        raise OSMDownError(f"پاسخ غیر JSON از OSM ({ctype or 'ناشناخته'})")
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise OSMDownError(f"JSON نامعتبر از OSM: {exc}") from exc


def _throttle() -> None:
    """فاصلهٔ حداقلی بین درخواست‌ها (سیاست استفادهٔ منصفانهٔ Nominatim)."""
    gap = float(config.OSM_MIN_INTERVAL or 1.1)
    with _LOCK:
        wait = _LAST_CALL[0] + gap - time.monotonic()
        if wait > 0:
            time.sleep(min(wait, 5.0))
        _LAST_CALL[0] = time.monotonic()


def _bbox_from_radius(lat: float, lon: float, radius_m: int) -> list[float]:
    """جعبهٔ تقریبی از شعاع (متر) — برای bounded search."""
    dlat = radius_m / 111_320.0
    dlon = radius_m / max(1.0, 111_320.0 * math.cos(math.radians(lat)))
    return [lat - dlat, lat + dlat, lon - dlon, lon + dlon]


def viewbox_param(bbox: list[float]) -> str:
    """[s, n, w, e] → «w,n,e,s» = چپ,بالا,راست,پایین (ترتیب viewbox در Nominatim)."""
    s, n, w, e = (float(x) for x in bbox)
    return f"{w:.6f},{n:.6f},{e:.6f},{s:.6f}"


# ─────────────────────────────────────────────────────────────────────────────
# Overpass: خواندن برچسب‌های OSM — صدها کسب‌وکار در یک درخواست
# ─────────────────────────────────────────────────────────────────────────────
_OVERPASS_LOCK = threading.Lock()
_OVERPASS_LAST = [0.0]
_OVERPASS_HEALTH: dict = {"ts": 0.0, "payload": None}
# آخرین جست‌وجوی موفق: نقطهٔ /status گاهی کند/بی‌جواب است ولی سرویس سالم است.
_OVERPASS_LAST_OK = [0.0]
# اگر اتصال کامل شکست خورد، مدتی از پرسیدن دوباره صرف‌نظر می‌کنیم تا هر جست‌وجو
# ده‌ها ثانیه معطل نشود و مسیر جایگزین (Nominatim) سریع جواب دهد.
_OVERPASS_DOWN_UNTIL = [0.0]
OVERPASS_DOWN_SECONDS = 120.0


def _overpass_throttle() -> None:
    gap = float(config.OVERPASS_MIN_INTERVAL or 1.0)
    with _OVERPASS_LOCK:
        wait = _OVERPASS_LAST[0] + gap - time.monotonic()
        if wait > 0:
            time.sleep(min(wait, 8.0))
        _OVERPASS_LAST[0] = time.monotonic()


def build_overpass_query(filters: list[str], bbox: list[float], *,
                         limit: int | None = None, timeout: int | None = None) -> str:
    """ساخت کوئری Overpass برای چند فیلتر تگی داخل یک جعبه.

    bbox داخلی [s, n, w, e] است؛ ترتیب Overpass (s,w,n,e) است.
    """
    if not filters:
        raise ValueError("هیچ فیلتری برای Overpass نبود")
    s, n, w, e = (float(x) for x in bbox)
    box = f"({s:.6f},{w:.6f},{n:.6f},{e:.6f})"
    parts = ";".join(f"nwr{f}{box}" for f in filters)
    cap = max(1, int(limit or config.OVERPASS_LIMIT))
    seconds = max(5, int(timeout or config.OVERPASS_TIMEOUT))
    return f"[out:json][timeout:{seconds}];({parts};);out center {cap};"


# جعبهٔ کلِ خیابان (همهٔ wayهای هم‌نام) کش می‌شود: اجرای دوباره همان خیابان
# نباید دوباره Overpass را درگیر کند.
_STREET_CACHE: dict = {}
_STREET_CACHE_LOCK = threading.Lock()

# آینه‌هایی که اخیراً «شلوغ/محدود» جواب داده‌اند، موقتاً کنار می‌روند.
_OVERPASS_COOLDOWN: dict[str, float] = {}
_OVERPASS_COOLDOWN_SECONDS = 120.0
# کدهای HTTP که تلاش دوباره معنا دارد: Overpass زیر بار معمولاً 429/504 می‌دهد.
_RETRYABLE = {429, 500, 502, 503, 504}


def _endpoints_in_order() -> list[str]:
    now = time.monotonic()
    fresh = [e for e in config.OVERPASS_ENDPOINTS if _OVERPASS_COOLDOWN.get(e, 0) <= now]
    cooled = [e for e in config.OVERPASS_ENDPOINTS if e not in fresh]
    return fresh + cooled          # اگر همه سرد بودند، باز هم همان‌ها را امتحان می‌کنیم


# آینه‌های شناخته‌شدهٔ Overpass. سرویس‌های عمومی زیر بار سنگین مرتب 502/تایم‌اوت
# می‌دهند؛ آینهٔ سالم با سنجش واقعی انتخاب می‌شود، نه با حدس.
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.osm.rambler.ru/cgi/interpreter",
    "https://gall.openstreetmap.de/api/interpreter",
    "https://lambert.openstreetmap.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
    "https://overpass.monicz.dev/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]


# کوئری سنجش: کوچک است ولی دادهٔ واقعی تهران را می‌خواهد؛ آینهٔ منطقه‌ای (سوئیس)
# صفر برمی‌گرداند و همین، «جهانی بودن» را از «سالم بودن» جدا می‌کند.
DEFAULT_PROBE_QUERY = ('[out:json][timeout:10];'
                       'nwr["amenity"="cafe"](35.700,51.410,35.712,51.420);out 3;')


def probe_overpass_endpoints(candidates: list[str] | None = None, *,
                             timeout: float = 10.0,
                             query: str | None = None) -> list[dict]:
    """سنجش زندهٔ آینه‌ها با یک کوئری کوچک — کدام‌ها همین حالا پاسخ می‌دهند."""
    query = query or DEFAULT_PROBE_QUERY
    payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
    results: list[dict] = []
    for url in candidates or OVERPASS_MIRRORS:
        host = url.split("/")[2] if "//" in url else url
        started = time.monotonic()
        try:
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                         "Content-Type": "application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            elements = data.get("elements") if isinstance(data, dict) else None
            results.append({"url": url, "host": host, "ok": isinstance(elements, list),
                            "n": len(elements or []),
                            "ms": int((time.monotonic() - started) * 1000), "error": ""})
        except urllib.error.HTTPError as exc:
            results.append({"url": url, "host": host, "ok": False,
                            "ms": int((time.monotonic() - started) * 1000),
                            "error": f"HTTP {exc.code}"})
        except Exception as exc:                      # noqa: BLE001 — گزارش روشن
            results.append({"url": url, "host": host, "ok": False,
                            "ms": int((time.monotonic() - started) * 1000),
                            "error": f"{type(exc).__name__}: {str(exc)[:80]}"})
    return results


def overpass_raw(query: str, *, timeout: float = 20.0,
                 budget_s: float | None = None) -> dict:
    """اجرای خام یک کوئری Overpass (برای عیب‌یابی/سنجش؛ به کش و شکستن مدار دست نمی‌زند)."""
    return _overpass_request(query, timeout=timeout, retries=0,
                             deadline_s=budget_s, trip_breaker=False,
                             per_endpoint_s=12.0)


def _overpass_request(query: str, *, timeout: float, retries: int = 1,
                      deadline_s: float | None = None, trip_breaker: bool = True,
                      per_endpoint_s: float | None = None) -> dict:
    """اجرای کوئری روی آینه‌ها با تلاش دوبارهٔ کوتاه (تحمل خطای سرویس شلوغ).

    Overpass یک سرویس عمومی است؛ پاسخ 429/504 در ساعات شلوغ طبیعی است. یک تلاش
    دوباره با فاصلهٔ کوتاه، تفاوت «صفر نتیجه» و «نتیجهٔ واقعی» است.
    """
    payload = urllib.parse.urlencode({"data": query}).encode("utf-8")
    errors: list[str] = []
    # سقف زمانی کلِ این فراخوانی: یک جست‌وجو نباید ده‌ها ثانیه در انتظار بماند.
    # (تلاش دوباره فقط وقتی معنا دارد که زمان باقی مانده باشد.)
    deadline = time.monotonic() + max(6.0, float(deadline_s or (timeout + 10)))
    for endpoint in _endpoints_in_order():
        host = endpoint.split("/")[2] if "//" in endpoint else endpoint
        for attempt in range(retries + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 2:
                break
            _overpass_throttle()
            req = urllib.request.Request(
                endpoint, data=payload, method="POST",
                headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                         "Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                budget = float(timeout) + 10
                if per_endpoint_s:
                    budget = min(budget, float(per_endpoint_s))
                with urllib.request.urlopen(req, timeout=min(remaining, budget)) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as exc:
                detail = ""
                if exc.code == 400:
                    # پیام سرور دقیقاً می‌گوید کجای کوئری غلط است (خطای نگارشی ERE).
                    try:
                        detail = exc.read().decode("utf-8", "replace").strip()[:160]
                    except Exception:                     # noqa: BLE001
                        detail = ""
                errors.append(f"{host} HTTP {exc.code}" + (f" ({detail})" if detail else ""))
                if exc.code in _RETRYABLE:
                    _OVERPASS_COOLDOWN[endpoint] = time.monotonic() + _OVERPASS_COOLDOWN_SECONDS
                    if attempt < retries and deadline - time.monotonic() > 3:
                        time.sleep(min(2.0 * (attempt + 1), max(0.0, deadline - time.monotonic() - 1)))
                        continue
                break
            except Exception as exc:                  # شبکه/DNS/تایم‌اوت
                errors.append(f"{host} {type(exc).__name__}")
                if attempt < retries and deadline - time.monotonic() > 3:
                    time.sleep(1.5)
                    continue
                break
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                errors.append(f"{host} پاسخ JSON نبود")
                break
            remark = str((data or {}).get("remark") or "")
            if remark and ("error" in remark.lower() or "timed out" in remark.lower()):
                errors.append(f"{host}: {remark[:120]}")
                break
            _OVERPASS_COOLDOWN.pop(endpoint, None)
            _OVERPASS_LAST_OK[0] = time.time()
            _OVERPASS_DOWN_UNTIL[0] = 0.0
            return data
    # «کلید شکستن مدار» فقط برای جست‌وجوی اصلی؛ یک پرسش جانبی (مثل جعبهٔ خیابان)
    # نباید کل اجرا را به مسیر جایگزین بیندازد.
    if trip_breaker:
        _OVERPASS_DOWN_UNTIL[0] = time.monotonic() + OVERPASS_DOWN_SECONDS
    raise OSMDownError("Overpass پاسخ نداد (" + "; ".join(errors[:4]) + ")")


def _center_of(element: dict) -> tuple[float, float] | None:
    center = element.get("center") or {}
    try:
        if center.get("lat") is not None:
            return float(center["lat"]), float(center["lon"])
        return float(element["lat"]), float(element["lon"])
    except (KeyError, TypeError, ValueError):
        bounds = element.get("bounds") or {}
        try:
            return ((float(bounds["minlat"]) + float(bounds["maxlat"])) / 2,
                    (float(bounds["minlon"]) + float(bounds["maxlon"])) / 2)
        except (KeyError, TypeError, ValueError):
            return None


def row_from_overpass(element: dict, *, input_id: str = "", lang: str = "fa") -> dict | None:
    """یک المان Overpass → ردیف قرارداد موتور (همتای `row_from_item`)."""
    tags = element.get("tags") or {}
    if not isinstance(tags, dict) or not tags:
        return None
    name = str(tags.get("name") or tags.get("name:fa") or tags.get("brand") or "").strip()
    if not name or osm_tags.is_excluded(tags):
        return None
    point = _center_of(element)
    if not point:
        return None
    lat, lon = round(point[0], 6), round(point[1], 6)
    osm_type = str(element.get("type") or "")
    osm_id = element.get("id")
    if not osm_type or osm_id is None:
        return None

    niche_id = osm_tags.niche_for_tags(tags)
    category = osm_tags.describe(tags)

    def pick(*keys: str) -> str:
        for key in keys:
            value = (tags or {}).get(key)
            if value and str(value).strip():
                return str(value).strip()
        return ""

    address = pick("addr:full", "address")
    if not address:
        address = "، ".join(x for x in (pick("addr:housenumber"), pick("addr:street"),
                                        pick("addr:suburb", "addr:neighbourhood"),
                                        pick("addr:city")) if x)

    phone = pick("phone", "contact:phone", "mobile", "contact:mobile")
    website = pick("website", "contact:website", "url")
    email = pick("email", "contact:email")
    description = pick("description", "note", "healthcare:speciality")[:400]

    return {
        "input_id": input_id,
        "link": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "title": name,
        "category": category,
        "address": address,
        "open_hours": pick("opening_hours"),
        "popular_times": "",
        "website": website,
        "phone": phone,
        "plus_code": "",
        "review_count": "",
        "review_rating": "",
        "reviews_per_rating": "",
        "latitude": str(lat),
        "longitude": str(lon),
        "cid": f"{osm_type}/{osm_id}",
        "status": "open" if pick("opening_hours") else "",
        "descriptions": description,
        "reviews_link": "",
        "thumbnail": "",
        "timezone": "",
        "price_range": pick("price_range"),
        "data_id": f"osm:{osm_type}/{osm_id}",
        "place_id": f"osm:{osm_type}/{osm_id}",
        "images": "",
        "reservations": "",
        "order_online": "",
        "menu": "",
        "owner": pick("operator", "owner"),
        "complete_address": address,
        "about": description,
        "user_reviews": "",
        "user_reviews_extended": "",
        "emails": email,
        "instagram": pick("contact:instagram", "instagram"),
        "facebook": pick("contact:facebook", "facebook"),
        "linkedin": pick("contact:linkedin", "linkedin"),
        "niche_id": niche_id,
        "source": "openstreetmap",
        "source_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
    }


_OVERPASS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_OVERPASS_CACHE_LOCK = threading.Lock()
OVERPASS_CACHE_MAX = 48


def _cache_key(filters: list[str], bbox: list[float], limit: int, lang: str) -> str:
    s, n, w, e = (float(x) for x in bbox)
    # دقت ۴ رقم اعشار ≈ ۱۱ متر: برای کش کافی است و نوسان ریز مختصات را بی‌اثر می‌کند.
    return f"{lang}|{limit}|{','.join(sorted(filters))}|{s:.4f},{n:.4f},{w:.4f},{e:.4f}"


def cache_clear() -> None:
    """پاک‌کردن کش Overpass (برای آزمون‌ها و اجرای تازه)."""
    with _OVERPASS_CACHE_LOCK:
        _OVERPASS_CACHE.clear()


def overpass_available() -> bool:
    """آیا Overpass تا این لحظه «پاسخ‌گو» بوده است؟ (برای پرهیز از انتظار تکراری)"""
    return _OVERPASS_DOWN_UNTIL[0] <= time.monotonic()


def overpass_search(filters: list[str], *, bbox: list[float], limit: int | None = None,
                    timeout: float | None = None, input_id: str = "",
                    lang: str = "fa", cache: bool = True, force: bool = False,
                    budget_s: float | None = None) -> list[dict]:
    """یک درخواست Overpass → ردیف‌های کسب‌وکار (بدون تکراری، با کش کوتاه‌مدت).

    کش برای تکرار جست‌وجو (کاربر بعدی، اجرای دوباره، آزمون رابط) است؛ هم سرعت
    را بالا می‌برد و هم ادب استفاده از سرویس عمومی Overpass را رعایت می‌کند.
    """
    cap = int(limit or config.OVERPASS_LIMIT)
    if not force and not overpass_available():
        raise OSMDownError("Overpass در ۳ دقیقهٔ گذشته پاسخ نداد؛ مسیر جایگزین استفاده می‌شود")
    ttl = float(getattr(config, "OVERPASS_CACHE_TTL", 0) or 0)
    key = _cache_key(filters, bbox, cap, lang) if (cache and ttl > 0) else ""
    if key:
        with _OVERPASS_CACHE_LOCK:
            hit = _OVERPASS_CACHE.get(key)
        if hit and (time.time() - hit[0]) < ttl:
            return [dict(row, input_id=input_id) for row in hit[1]]
    query = build_overpass_query(filters, bbox, limit=cap, timeout=timeout)
    data = _overpass_request(query, timeout=float(timeout or config.OVERPASS_TIMEOUT),
                             deadline_s=budget_s)
    elements = data.get("elements") if isinstance(data, dict) else None
    rows: dict[str, dict] = {}
    for element in elements or []:
        if not isinstance(element, dict):
            continue
        row = row_from_overpass(element, input_id=input_id, lang=lang)
        if row:
            rows.setdefault(row["place_id"], row)
    out = list(rows.values())
    if out:                      # پاسخ سالم = سرویس بالاست (مسیر کش/تست هم همین را می‌بیند)
        _OVERPASS_DOWN_UNTIL[0] = 0.0
        _OVERPASS_LAST_OK[0] = time.time()
    if key and out:
        with _OVERPASS_CACHE_LOCK:
            if len(_OVERPASS_CACHE) >= OVERPASS_CACHE_MAX:
                oldest = min(_OVERPASS_CACHE, key=lambda k: _OVERPASS_CACHE[k][0])
                _OVERPASS_CACHE.pop(oldest, None)
            _OVERPASS_CACHE[key] = (time.time(), [dict(row) for row in out])
    return out


def _boxes_touch(a: list[float], b: list[float], gap: float) -> bool:
    """آیا دو جعبه به هم می‌رسند (با فاصلهٔ تحمل `gap`)؟"""
    return not (a[1] < b[0] - gap or b[1] < a[0] - gap or
                a[3] < b[2] - gap or b[3] < a[2] - gap)


def _km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """فاصلهٔ تقریبی دو نقطه بر حسب کیلومتر (haversine ساده)."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371.0 * math.asin(min(1.0, math.sqrt(h)))


def _ways_from_elements(data: dict) -> list[dict]:
    """المان‌های Overpass → wayهای خیابان (با گره‌ها، جعبه و طول)."""
    ways: list[dict] = []
    for element in (data.get("elements") if isinstance(data, dict) else None) or []:
        if not isinstance(element, dict):
            continue
        geometry = element.get("geometry") or []
        points: list[tuple[float, float]] = []
        for point in geometry:
            try:
                points.append((float(point["lat"]), float(point["lon"])))
            except (KeyError, TypeError, ValueError):
                continue
        if points:
            box = [min(p[0] for p in points), max(p[0] for p in points),
                   min(p[1] for p in points), max(p[1] for p in points)]
        else:
            bounds = element.get("bounds") or {}
            try:
                box = [float(bounds["minlat"]), float(bounds["maxlat"]),
                       float(bounds["minlon"]), float(bounds["maxlon"])]
            except (KeyError, TypeError, ValueError):
                continue
        # قطعهٔ کاملاً شمالی-جنوبی یا شرقی-غربی «پهنا» ندارد؛ صفرِ کامل را با یک
        # اپسیلون باز می‌کنیم تا از دست نرود (در OSM واقعی نادر ولی ممکن است).
        if box[1] <= box[0]:
            box[1] = box[0] + 1e-9
        if box[3] <= box[2]:
            box[3] = box[2] + 1e-9
        length = sum(_km_between(points[i], points[i + 1]) for i in range(len(points) - 1))
        ways.append({
            "id": element.get("id"),
            "nodes": {n for n in (element.get("nodes") or []) if n is not None},
            "box": box,
            "length_km": length,
        })
    return ways


def _box_overlaps(a: list[float], b: list[float], pad: float = 0.0) -> bool:
    """آیا دو جعبه هم‌پوشانی دارند (با حاشیهٔ اختیاری)؟"""
    return not (a[1] < b[0] - pad or b[1] < a[0] - pad or
                a[3] < b[2] - pad or b[3] < a[2] - pad)


def _bbox_gap(a: list[float], b: list[float]) -> float:
    """فاصلهٔ دو جعبه بر حسب درجه (۰ = هم‌پوشان یا چسبیده)."""
    return max(max(b[0] - a[1], a[0] - b[1], 0.0),
               max(b[2] - a[3], a[2] - b[3], 0.0))


def _street_components(ways: list[dict]) -> list[dict]:
    """تکه‌های خیابان → مؤلفه‌های به‌هم‌پیوسته (اتصال از راه گره‌های مشترک).

    چرا گره و نه «جعبه‌های چسبیده»؟ «ولیعصر» در تهران ده‌ها کوچهٔ هم‌نام هم دارد؛
    چسباندن جعبه‌به‌جعبه زنجیره‌ای تا محله‌های دور (منطقه ۱۴) می‌رفت. تکه‌های یک
    خیابان واقعی گرهٔ مشترک دارند؛ فقط قطعه‌های کوچکِ جداافتاده (تونل/تقاطع) با
    چسبیدن به یک قطعهٔ بلند به همان خیابان وصل می‌شوند.
    """
    parent = list(range(len(ways)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        root_a, root_b = find(first), find(second)
        if root_a != root_b:
            parent[root_b] = root_a

    owner: dict = {}
    for index, way in enumerate(ways):
        for node in way["nodes"]:
            owner.setdefault(node, []).append(index)
    for group in owner.values():
        for other in group[1:]:
            union(group[0], other)

    # پل فقط برای قطعهٔ کوتاه که به یک قطعهٔ بلند می‌چسبد (نه کوتاه-به-کوتاه)
    long_indexes = [i for i, w in enumerate(ways) if w["length_km"] >= 0.6]
    for index, way in enumerate(ways):
        if way["length_km"] > 0.6:
            continue
        for other in long_indexes:
            if _boxes_touch(way["box"], ways[other]["box"], 0.0018):
                union(index, other)

    grouped: dict = {}
    for index in range(len(ways)):
        grouped.setdefault(find(index), []).append(ways[index])
    components = []
    for members in grouped.values():
        box = [min(w["box"][0] for w in members), max(w["box"][1] for w in members),
               min(w["box"][2] for w in members), max(w["box"][3] for w in members)]
        components.append({
            "ways": members, "box": box,
            "length_km": sum(w["length_km"] for w in members),
        })
    return sorted(components, key=lambda c: -c["length_km"])


def _connected_street(ways: list[dict], seed: list[float] | None) -> dict:
    """مؤلفهٔ درستِ خیابان را از میان تکه‌های هم‌نام انتخاب می‌کند.

    قاعده: مؤلفه‌ای که جعبه‌اش با جعبهٔ Nominatim هم‌پوشانی دارد و **بلندترین** است
    (خیابان واقعی، نه کوچهٔ هم‌نامِ ۲۰۰ متری). اگر هیچ مؤلفه‌ای روی جعبه نبود،
    نزدیک‌ترین مؤلفه فقط وقتی پذیرفته می‌شود که ≤ ~۱ کیلومتر فاصله داشته باشد.

    خروجی: `{"ways": [...], "anchored": bool}`.
    """
    if not ways:
        return {"ways": [], "anchored": False}
    components = _street_components(ways)
    if not (seed and len(seed) == 4):
        best = components[0]                     # بدون مرجع: بلندترین خیابان
        return {"ways": best["ways"], "anchored": False}
    overlapping = [c for c in components if _bbox_gap(c["box"], seed) <= 0.0]
    if overlapping:
        best = max(overlapping, key=lambda c: c["length_km"])
        # بعضی خیابان‌های بلند در OSM چند مؤلفهٔ جدا دارند (میدان/زیرگذر گرهٔ مشترک
        # را قطع می‌کند). فقط مؤلفه‌هایی که ≤ ~۱۶۰ متر به خوشهٔ فعلی می‌چسبند اضافه
        # می‌شوند؛ کوچهٔ هم‌نام در محلهٔ دور هرگز به این فاصله نمی‌رسد.
        members = list(best["ways"])
        box = list(best["box"])
        rest = [c for c in components if c is not best]
        grew = True
        while grew:
            grew = False
            for comp in list(rest):
                if _bbox_gap(comp["box"], box) <= 0.0015:
                    members.extend(comp["ways"])
                    box = [min(box[0], comp["box"][0]), max(box[1], comp["box"][1]),
                           min(box[2], comp["box"][2]), max(box[3], comp["box"][3])]
                    rest.remove(comp)
                    grew = True
        return {"ways": members, "anchored": True}
    nearest = min(components, key=lambda c: _bbox_gap(c["box"], seed))
    if _bbox_gap(nearest["box"], seed) <= 0.01:      # ≤ ~۱ کیلومتر
        return {"ways": nearest["ways"], "anchored": False}
    return {"ways": [], "anchored": False}


# Overpass از POSIX ERE استفاده می‌کند: [[:space:]] مجاز است، \s و (?:...) نه.
_STREET_NAME_PREFIX_RE = ("(خیابان|بلوار|بزرگراه|اتوبان|کوچه|میدان|"
                          "بن بست|بن\u200cبست|بازار)?")
# در OSM ایران خیلی از خیابان‌ها با لقب ثبت شده‌اند: «آیت الله کاشانی»، «شهید بهشتی»،
# «امام رضا». کاربر «خیابان کاشانی» می‌نویسد؛ بدون تحمل لقب، هیچ تکه‌ای پیدا نمی‌شد.
_STREET_HONORIFIC_RE = ("(آیت[[:space:]]*الله|ایت[[:space:]]*الله|شهید|امام|"
                        "علامه|دکتر|استاد|مهندس|حاج|شهیدان|بزرگ|آیت الله)?")
_NAME_GAP = "[[:space:]]*"

# در OSM ایران نام خیابان گاهی یک پسوند دارد: «انقلاب اسلامی» (۴۶ تکه در تهران،
# ولی «انقلاب» فقط ۳ تکه). پسوند باید واژهٔ فارسی باشد، نه عدد: «کاشانی ۲۵» یا
# کوچهٔ «کاشانیان» خیابان اصلی نیست.
# پسوندهای رایج نام خیابان در OSM (فقط واژه‌های شناخته‌شده).
# چرا لیست و نه «هر واژه‌ای»؟ محدودهٔ نویسه‌های فارسی/رقم داخل کلاس منفی، Overpass
# را با HTTP 400 رد می‌کند و «کاشانی ۲۵» هم نباید با خیابان کاشانی قاطی شود.
_STREET_TAIL_RE = ("([[:space:]]+(اسلامی|شمالی|جنوبی|شرقی|غربی|مرکزی|"
                   "علیا|سفلی|جدید|قدیم|بزرگ|کوچک|اصلی|اعظم))?")


def _name_pattern(name: str) -> str:
    """الگوی نام خیابان با تحمل فاصله/نیم‌فاصله، پیشوند و پسوند.

    چرا؟ OSM «ولی عصر» ثبت کرده و کاربر «ولیعصر» می‌نویسد؛ تطابق دقیق رشته‌ای
    هیچ‌وقت جور در نمی‌آید و کل خیابان ۱۷ کیلومتری از دست می‌رفت. همچنین
    «خیابان انقلاب» در OSM «انقلاب اسلامی» ثبت شده و بدون تحمل پسوند پیدا نمی‌شد.
    """
    clean = _strip_street_prefix(str(name or "").strip())
    clean = "".join(_squash(clean).split())        # حذف فاصله/نیم‌فاصله
    if len(clean) < 3:
        return ""
    body = _NAME_GAP.join(re.escape(ch) for ch in clean)
    return (f"^{_NAME_GAP}{_STREET_NAME_PREFIX_RE}{_NAME_GAP}"
            f"{_STREET_HONORIFIC_RE}{_NAME_GAP}{body}{_STREET_TAIL_RE}{_NAME_GAP}$")


def overpass_street_bbox(names: list[str], *, bbox: list[float], timeout: float = 25.0,
                         budget_s: float | None = None,
                         near: list[float] | None = None,
                         diag: list | None = None) -> dict:
    """جعبهٔ **کل** یک خیابان از Overpass (همهٔ تکه‌های به‌هم‌پیوسته، نه یک تکه).

    چرا؟ Nominatim برای «خیابان ولیعصر» فقط یک way می‌دهد (≈۱ کیلومتر از ۱۷
    کیلومتر). Overpass همهٔ wayهای همان نام داخل جعبهٔ شهر را می‌دهد؛ با `near`
    فقط شبکهٔ به‌هم‌پیوستهٔ همان تکه (اتصال از راه گره‌های مشترک) نگه داشته می‌شود
    تا کوچه‌های هم‌نام در محله‌های دیگر وارد نشوند.

    ابتدا نام دقیق (سریع، از ایندکس Overpass) و اگر چیزی نبود الگوی فاصله‌تحمل.
    خروجی `{"bbox": [...], "ways": n, "boxes": [...], "length_km": x, "via": ...}`
    یا `{}`.
    """
    pats: list[str] = []
    for name in names or []:
        clean = _strip_street_prefix(str(name or "").strip())
        clean = clean.split("،")[0].split(",")[0].strip()
        if len(clean) >= 3 and clean not in pats:
            pats.append(clean)
    if not pats or not bbox or len(bbox) != 4:
        return {}
    ttl = float(getattr(config, "OVERPASS_CACHE_TTL", 0) or 0)
    cache_key = (tuple(pats), tuple(round(float(x), 3) for x in bbox),
                 tuple(round(float(x), 4) for x in near) if near else None)
    if ttl > 0:
        with _OVERPASS_CACHE_LOCK:
            hit = _STREET_CACHE.get(cache_key)
            if hit and time.time() - hit[0] < ttl:
                return dict(hit[1])

    s_, n_, w_, e_ = (float(x) for x in bbox)
    seconds = int(max(8, min(40, timeout)))
    box = f"({s_},{w_},{n_},{e_})"
    exact = "".join(f'way["name"="{p}"]{box};' for p in pats)
    patterns = [pat for pat in (_name_pattern(p) for p in pats) if pat]
    regex = (f'way["name"~"{pattern}",i]{box};'
             if (pattern := "|".join(patterns)) else "")
    found: dict = {}
    # بودجهٔ زمانی مشترک: پرسش «دقیق» (ایندکس، معمولاً سریع) نباید کل بودجه را
    # بخورد و پرسش «الگو» — که تنها راه رسیدن به خیابان‌های بلند است — را حذف کند.
    started = time.monotonic()
    total_budget = max(10.0, float(budget_s or 20.0))

    def _left() -> float:
        return total_budget - (time.monotonic() - started)

    for via, body in (("exact", exact), ("regex", regex)):
        if not body:
            continue
        remaining = _left()
        if remaining <= 5:
            if diag is not None:
                diag.append(f"{via}: بودجهٔ زمانی تمام شد")
            break
        # `out geom` هم مختصات گره‌ها را می‌دهد (برای اتصال توپولوژیک) هم bounds را
        query = f"[out:json][timeout:{seconds}];({body});out geom;"
        phase_budget = min(remaining, 10.0) if via == "exact" else remaining
        try:
            data = _overpass_request(query, timeout=timeout, retries=0,
                                     deadline_s=phase_budget, trip_breaker=False,
                                     per_endpoint_s=9.0)
        except OSMDownError as exc:
            if diag is not None:
                diag.append(f"{via}: {exc}")
            # شکست گذرا در پرسش «دقیق» نباید مسیر «الگو» را هم بکشد؛ همین‌جا
            # بود که ولیعصر با ۷۷ تکهٔ آماده، به تکهٔ ۱ کیلومتری Nominatim سقوط می‌کرد.
            continue
        ways = _ways_from_elements(data)
        if not ways:
            if diag is not None:
                diag.append(f"{via}: «{pats[0]}» تکه‌ای نداشت")
            continue
        cluster = _connected_street(ways, near)
        chosen = cluster["ways"]
        if not chosen:
            if diag is not None:
                diag.append(f"{via}: {len(ways)} تکه، هیچ‌کدام به تکهٔ Nominatim نچسبید")
            continue
        length = round(sum(b["length_km"] for b in chosen), 2)
        merged = [min(b["box"][0] for b in chosen), max(b["box"][1] for b in chosen),
                  min(b["box"][2] for b in chosen), max(b["box"][3] for b in chosen)]
        candidate = {
            "bbox": [round(x, 6) for x in merged], "ways": len(chosen), "via": via,
            # جعبهٔ هر تکه را هم می‌دهیم: پایپ‌لاین با آن‌ها فقط مغازه‌های «روی خیابان»
            # را نگه می‌دارد (جعبهٔ کل خیابان یک مستطیل بزرگ است، خیابان یک نوار باریک).
            "boxes": [[round(float(x), 5) for x in b["box"]] for b in chosen][:400],
            "length_km": length, "anchored": cluster["anchored"],
        }
        # نام دقیق در تهران کوچه‌های کوچکِ هم‌نام زیادی دارد؛ فقط خوشه‌ای که واقعاً روی
        # تکهٔ Nominatim بنشیند معتبر است. بین معتبرها، بلندترین (کامل‌ترین) را بردار.
        if near and len(near) == 4 and not cluster["anchored"]:
            # مؤلفهٔ نزدیک (≤۱km) هنوز ممکن است همان خیابان باشد؛ ولی فقط وقتی
            # می‌پذیریم که کوئری «همهٔ تکه‌های هم‌نام» را داده باشد (regex).
            if via != "regex":
                if diag is not None:
                    diag.append(f"{via}: {len(ways)} تکه، ولی هیچ‌کدام روی تکهٔ Nominatim نبود")
                continue
            if diag is not None:
                diag.append(f"{via}: نزدیک‌ترین مؤلفه {len(chosen)} تکه، نه روی جعبهٔ Nominatim")
        if not found or length > float(found.get("length_km") or 0):
            found = candidate
        if cluster["anchored"] and length >= 3.0:
            break                       # خیابان واقعی پیدا شد؛ پرسش بیشتر لازم نیست
    # ── خواهر-خیابان‌ها: یک خیابانِ تاریخی در OSM چند نام دارد ──────────────
    # «چهارباغ» اصفهان = چهارباغ عباسی + بالا + پایین + خواجو (۲۰ تکه). کاربر
    # «خیابان چهارباغ» می‌نویسد و انتظار دارد کل محور را ببیند، نه ۱٫۲ کیلومتر.
    # فقط وقتی امتدادِ همان محور است می‌چسبانیم: نام با همان واژه شروع شود و
    # جعبه‌اش به جعبهٔ فعلی بچسبد (≤ ~۴۵۰ متر). خیابان هم‌نامِ محلهٔ دور نمی‌آید.
    if (found and near and len(near) == 4 and _left() > 6
            and float(found.get("length_km") or 0) < 6.0 and pats):
        family = _street_family(pats[0], bbox=bbox, near=near, diag=diag,
                                budget_s=max(5.0, _left() - 1.0))
        if family:
            base_ways = int(found.get("ways") or 0)
            joined = _join_family(found, family)
            if joined:
                found = joined
                if diag is not None:
                    diag.append(f"family: {joined['ways'] - base_ways} تکه از خیابان‌های "
                                f"هم‌خانوادهٔ «{pats[0]}» به نوار اضافه شد")
    if found and ttl > 0:
        with _OVERPASS_CACHE_LOCK:
            if len(_STREET_CACHE) >= 256:
                _STREET_CACHE.pop(next(iter(_STREET_CACHE)), None)
            _STREET_CACHE[cache_key] = (time.time(), dict(found))
    return found


def _family_root(name: str) -> str:
    """ریشهٔ خانوادگی نام خیابان = نخستین واژهٔ معنادار («چهارباغ عباسی» → «چهارباغ»).

    چرا نخستین واژه؟ در OSM محورهای تاریخی به نام‌های چسبیده تقسیم می‌شوند
    («چهارباغ عباسی/بالا/پایین/خواجو») و فقط واژهٔ اول مشترک است. برای نام‌های
    کوتاه («ولی عصر») ریشه ساخته نمی‌شود تا خیابان‌های بی‌ربط «ولی…» نیایند.
    """
    clean = " ".join(_squash(_strip_street_prefix(str(name or ""))).split())
    if not clean:
        return ""
    first = clean.split(" ")[0]
    if len("".join(first.split())) < 4:
        return ""
    return first


def _is_sibling_name(candidate: str, root: str) -> bool:
    """آیا «چهارباغ بالا» خواهرِ ریشهٔ «چهارباغ» است (نه «چهارباغی» یا «پیام چهارباغ»)؟

    مقایسه با تحمل فاصله است (OSM «چهار باغ» و «چهارباغ» هر دو را دارد) ولی مرز
    واژه شرط است: «انقلابی» و «کاشانیان» خیابان دیگری‌اند، نه خواهرِ «انقلاب».
    """
    cand = " ".join(_squash(str(candidate or "")).split())
    base = "".join(" ".join(_squash(_strip_street_prefix(str(root or ""))).split()).split())
    if not cand or not base:
        return False
    i = j = 0
    while i < len(cand) and j < len(base):
        if cand[i] == " ":
            i += 1
            continue
        if cand[i] != base[j]:
            return False
        i += 1
        j += 1
    if j < len(base) or i >= len(cand):
        return False                       # ریشه کامل نشد یا چیز تازه‌ای نمانده
    if cand[i] not in " ":               # وسط یک واژهٔ بلندتر قطع شد
        return False
    rest = cand[i:].strip()
    word = rest.split(" ")[0] if rest else ""
    if not word or word[0] in "۰۱۲۳۴۵۶۷۸۹0123456789":
        return False                       # «کاشانی ۲۵» خیابان دیگری است
    return True


def _street_family(base: str, *, bbox: list[float], near: list[float] | None,
                   diag: list | None = None, budget_s: float = 8.0) -> list[dict]:
    """wayهای هم‌خانوادهٔ یک خیابان (نام با همین واژه ادامه یابد) داخل جعبهٔ شهر."""
    root = _family_root(base)
    if not root:
        return []
    body = _NAME_GAP.join(re.escape(ch) for ch in "".join(root.split()))
    s_, n_, w_, e_ = (float(x) for x in bbox)
    query = (f'[out:json][timeout:20];'
             f'(way["name"~"{body}",i]({s_},{w_},{n_},{e_}));out geom;')
    try:
        data = _overpass_request(query, timeout=20, retries=0, deadline_s=budget_s,
                                 trip_breaker=False, per_endpoint_s=8.0)
    except OSMDownError as exc:
        if diag is not None:
            diag.append(f"family: {exc}")
        return []
    ways: list[dict] = []
    for element, way in zip((data.get("elements") if isinstance(data, dict) else []) or [],
                            _ways_from_elements(data)):
        tags = (element or {}).get("tags") or {}
        if _is_sibling_name(str(tags.get("name") or ""), root):
            way["name"] = str(tags.get("name") or "")
            ways.append(way)
    if not ways and diag is not None:
        diag.append(f"family: «{root}» خواهر-خیابانی نداشت")
    return ways


def _join_family(found: dict, family: list[dict]) -> dict:
    """خواهر-خیابان‌های چسبیده به نوار فعلی را ادغام می‌کند (فقط امتداد همان محور)."""
    boxes = [list(b) for b in (found.get("boxes") or [])]
    total_length = float(found.get("length_km") or 0.0)
    added = 0
    merged = [min(b[0] for b in boxes), max(b[1] for b in boxes),
              min(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else []
    if not merged:
        return {}
    pool = list(family)
    grew = True
    while grew and len(boxes) < 400:
        grew = False
        for way in list(pool):
            if _bbox_gap(way["box"], merged) <= 0.004:      # ≤ ~۴۵۰ متر: امتداد محور
                boxes.append([round(float(x), 5) for x in way["box"]])
                total_length += float(way.get("length_km") or 0.0)
                merged = [min(merged[0], way["box"][0]), max(merged[1], way["box"][1]),
                          min(merged[2], way["box"][2]), max(merged[3], way["box"][3])]
                pool.remove(way)
                added += 1
                grew = True
    if not added:
        return {}
    result = dict(found)
    result.update({
        "bbox": [round(float(x), 6) for x in merged],
        "boxes": boxes[:400], "ways": int(found.get("ways") or 0) + added,
        "length_km": round(total_length, 2),
        "siblings": len({w.get("name") for w in family if w.get("name")}),
    })
    return result


def _bounds_from_elements(data: dict) -> list[list[float]]:
    """bounds المان‌های Overpass → جعبه‌های معتبر."""
    return [way["box"] for way in _ways_from_elements(data)]


def split_bbox(bbox: list[float], rows: int = 2, cols: int = 2) -> list[list[float]]:
    """تقسیم یک جعبه به شبکهٔ rows×cols — برای کامل‌ترشدن جاروی شهرهای بزرگ."""
    s, n, w, e = (float(x) for x in bbox)
    rows, cols = max(1, int(rows)), max(1, int(cols))
    dy, dx = (n - s) / rows, (e - w) / cols
    tiles: list[list[float]] = []
    for i in range(rows):
        for j in range(cols):
            tiles.append([s + i * dy, s + (i + 1) * dy, w + j * dx, w + (j + 1) * dx])
    return tiles


def cluster_boxes(boxes: list, groups: int = 6) -> list[list[float]]:
    """تکه‌های یک یا چند خیابان → چند جعبهٔ باریکِ پشت‌سرهم.

    چرا؟ جعبهٔ کل «خیابان ولیعصر» یک مستطیل ۱۶ کیلومتری روی نصف تهران است؛ Overpass
    در چنین مستطیلی سقف نتیجه را می‌خورد و مغازه‌های واقعی خیابان جا می‌مانند.
    پرسیدن چند جعبهٔ باریک روی خودِ خیابان، هم سریع‌تر است هم کامل‌تر.

    چند خیابانِ دور از هم: اول به زنجیره‌های جدا (تکه‌های نزدیک به هم) شکسته می‌شوند
    تا جعبهٔ هر زنجیره باریک بماند و سهم هر خیابان از سقف پرسش‌ها حفظ شود.
    """
    clean: list[list[float]] = []
    for item in boxes or []:
        try:
            box = [float(x) for x in item]
        except (TypeError, ValueError):
            continue
        if len(box) == 4 and box[1] >= box[0] and box[3] >= box[2]:
            clean.append(box)
    if len(clean) < 2 or groups <= 1:
        return []

    # ۱) زنجیره‌سازی: تکه‌هایی که به هم می‌رسند (یا ≤ ~۲۰۰ متر فاصله دارند)
    remaining = set(range(len(clean)))
    chains: list[list[list[float]]] = []
    while remaining:
        seed_index = remaining.pop()
        chain = [clean[seed_index]]
        queue = [clean[seed_index]]
        while queue:
            current = queue.pop()
            for index in list(remaining):
                if _boxes_touch(current, clean[index], 0.0025):
                    remaining.remove(index)
                    chain.append(clean[index])
                    queue.append(clean[index])
        chains.append(chain)

    # ۲) سهم هر زنجیره از سقف پرسش‌ها، به نسبت اندازه‌اش
    total = sum(len(chain) for chain in chains)
    alloc = [max(1, round(groups * len(chain) / total)) for chain in chains]
    while sum(alloc) > max(1, groups) and max(alloc) > 1:   # سقف را رعایت کن
        biggest = max(range(len(alloc)), key=lambda i: alloc[i])
        alloc[biggest] -= 1
    while sum(alloc) < groups and len(alloc) < groups:      # و از سقف کمتر نمان
        smallest = min(range(len(chains)), key=lambda i: alloc[i])
        if alloc[smallest] >= len(chains[smallest]):
            break
        alloc[smallest] += 1

    # ۳) هر زنجیره در امتداد محور بلندش به n جعبهٔ باریک تقسیم می‌شود
    out: list[list[float]] = []
    for chain, count in zip(chains, alloc):
        lat_span = max(b[1] for b in chain) - min(b[0] for b in chain)
        lon_span = max(b[3] for b in chain) - min(b[2] for b in chain)
        axis = 0 if lat_span >= lon_span else 2
        chain = sorted(chain, key=lambda b: (b[axis] + b[axis + 1]))
        size = max(1, len(chain) // max(1, count))
        for start in range(0, len(chain), size):
            chunk = chain[start:start + size]
            if not chunk:
                continue
            out.append([min(b[0] for b in chunk), max(b[1] for b in chunk),
                        min(b[2] for b in chunk), max(b[3] for b in chunk)])
    return out[:max(1, groups)]


def overpass_search_area(filters: list[str], *, bbox: list[float], limit: int | None = None,
                         timeout: float | None = None, input_id: str = "",
                         lang: str = "fa", tiles: int = 1, max_rows: int | None = None,
                         budget_s: float | None = None,
                         segments: list | None = None) -> list[dict]:
    """جاروی کامل‌تر یک محدوده: اگر نتیجه به سقف خورد، محدوده به قطعه‌ها شکسته می‌شود.

    Overpass با `out ... LIMIT` نتیجه را می‌بُرد؛ در شهرهای بزرگ این یعنی «کمبود
    ردیف» نه «نبود کسب‌وکار». شکستن جعبه به ۲×۲ (یا ۳×۳) این سقف را عملاً برمی‌دارد.
    """
    started = time.monotonic()
    per_call = float(timeout or config.OVERPASS_TIMEOUT)
    budget = float(budget_s or min(90.0, per_call * 2.5))
    base_limit = overpass_limit_for(bbox, limit)
    first = overpass_search(filters, bbox=bbox, limit=base_limit, timeout=per_call,
                            input_id=input_id, lang=lang, budget_s=budget)
    span = max(abs(float(bbox[1]) - float(bbox[0])), abs(float(bbox[3]) - float(bbox[2])))
    # «بُرش» وقتی رخ می‌دهد که یا نتیجه دقیقاً به سقف خورده، یا محدوده بزرگ است.
    truncated = len(first) >= base_limit
    big_area = span > 0.8

    # خیابان بلند: به‌جای مستطیل بزرگ، فقط روی خودِ نوار خیابان پرسیده می‌شود.
    pieces = cluster_boxes(segments, groups=max(2, min(8, int(tiles) * 2))) if segments \
        else []
    if len(pieces) >= 2:
        merged_rows: dict[str, dict] = {}
        for piece in pieces:
            for row in first:
                if row.get("place_id") in merged_rows:
                    continue
                try:
                    lat, lon = float(row.get("latitude")), float(row.get("longitude"))
                except (TypeError, ValueError):
                    continue
                if piece[0] - 0.0015 <= lat <= piece[1] + 0.0015 and \
                        piece[2] - 0.0015 <= lon <= piece[3] + 0.0015:
                    merged_rows[row["place_id"]] = row
        for piece in pieces:
            left = budget - (time.monotonic() - started)
            if left < 6:
                break
            try:
                found = overpass_search(filters, bbox=piece, limit=base_limit,
                                        timeout=min(per_call, left - 2),
                                        input_id=input_id, lang=lang, budget_s=left)
            except OSMDownError:
                continue
            for row in found:
                merged_rows.setdefault(row["place_id"], row)
            if max_rows and len(merged_rows) >= int(max_rows):
                break
        if merged_rows:
            out = list(merged_rows.values())
            return out[:int(max_rows)] if max_rows else out

    if int(tiles) <= 1 or not (truncated or big_area):
        return first

    side = 2 if int(tiles) <= 4 else 3
    merged: dict[str, dict] = {}
    for piece in split_bbox(bbox, side, side):
        left = budget - (time.monotonic() - started)
        if left < 6:
            break                     # وقت تمام شد؛ همان چیزی که داریم بهتر از تأخیر است
        try:
            found = overpass_search(filters, bbox=piece, limit=base_limit,
                                    timeout=min(per_call, left - 2),
                                    input_id=input_id, lang=lang, budget_s=left)
        except OSMDownError:
            continue
        for row in found:
            merged.setdefault(row["place_id"], row)
        if max_rows and len(merged) >= int(max_rows):
            break
    # اگر شکستن چیزی به دست نداد، همان نتیجهٔ اول را برمی‌گردانیم.
    if not merged:
        return first
    out = list(merged.values())
    if max_rows:
        out = out[:int(max_rows)]
    return out


def clean_term(keyword: str, place: str = "") -> str:
    """«دندانپزشکی در تهران» → «دندانپزشکی» (مکان با viewbox اعمال می‌شود)."""
    term = (keyword or "").strip()
    for sep in (" در ", " درِ ", " in "):
        if sep in term:
            head, _, tail = term.partition(sep)
            if head.strip() and (not place or place.strip() in tail or tail.strip()):
                term = head.strip()
                break
    return term.strip(" ,،")[:80]


def _address_of(item: dict) -> str:
    addr = item.get("address") or {}
    parts: list[str] = []
    if addr.get("house_number"):
        parts.append(str(addr["house_number"]))
    for key in ("road", "pedestrian", "footway", "neighbourhood", "quarter", "suburb",
                "city_district", "district", "town", "village", "city"):
        value = addr.get(key)
        if value and str(value) not in parts:
            parts.append(str(value))
    if not parts and item.get("display_name"):
        return str(item["display_name"]).strip()
    return "، ".join(parts)


def _pick(tags: dict, *keys: str) -> str:
    for key in keys:
        value = (tags or {}).get(key)
        if value and str(value).strip():
            return str(value).strip()
    return ""


# جفت‌های (دسته، نوع) که «کسب‌وکار» نیستند: پارک، مجسمه، بنای تاریخی، fountain…
# جست‌وجوی کلمه‌ای در OSM این‌ها را زیاد برمی‌گرداند (مثلاً «هنر» → پارک هنر).
NOISE_TYPES: frozenset[tuple[str, str]] = frozenset({
    ("tourism", "artwork"), ("tourism", "attraction"), ("tourism", "viewpoint"),
    ("tourism", "information"), ("tourism", "picnic_site"),
    ("historic", "memorial"), ("historic", "monument"), ("historic", "ruins"),
    ("historic", "archaeological_site"), ("historic", "city_gate"),
    ("leisure", "park"), ("leisure", "garden"), ("leisure", "playground"),
    ("leisure", "pitch"), ("leisure", "track"),
    ("amenity", "bench"), ("amenity", "shelter"), ("amenity", "fountain"),
    ("amenity", "clock"), ("man_made", "tower"), ("place", "neighbourhood"),
    ("place", "suburb"), ("place", "square"), ("boundary", "administrative"),
    ("highway", "bus_stop"), ("railway", "station"),
})


def row_from_item(item: dict, *, input_id: str = "", lang: str = "fa") -> dict | None:
    """یک نتیجهٔ Nominatim را به ردیف قرارداد موتور تبدیل می‌کند (بدون نام → None)."""
    tags = dict(item.get("extratags") or {})
    name = (item.get("name") or tags.get("name") or "").strip()
    if not name:
        return None
    if (str(item.get("category") or "").lower(), str(item.get("type") or "").lower()) in NOISE_TYPES:
        return None
    osm_type = (item.get("osm_type") or "").lower()
    osm_id = item.get("osm_id")
    try:
        lat = round(float(item.get("lat")), 6)
        lon = round(float(item.get("lon")), 6)
    except (TypeError, ValueError):
        return None

    kind_key = next((k for k in _KIND_KEYS if tags.get(k) or item.get("category") == k), "")
    kind_val = tags.get(kind_key) if kind_key else ""
    raw_kind = str(kind_val or item.get("type") or "").strip()
    category = _CATEGORY_FA.get(raw_kind, raw_kind)

    phone = _pick(tags, "phone", "contact:phone", "mobile", "contact:mobile")
    website = _pick(tags, "website", "contact:website", "url", "website:official")
    email = _pick(tags, "email", "contact:email")
    instagram = _pick(tags, "contact:instagram", "instagram")
    facebook = _pick(tags, "contact:facebook", "facebook")
    linkedin = _pick(tags, "contact:linkedin", "linkedin")
    description = _pick(tags, "description", "note", "healthcare:speciality")[:400]

    return {
        "input_id": input_id,
        "link": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "title": name,
        "category": category,
        "address": _address_of(item),
        "open_hours": _pick(tags, "opening_hours"),
        "popular_times": "",
        "website": website,
        "phone": phone,
        "plus_code": "",
        "review_count": "",
        "review_rating": "",
        "reviews_per_rating": "",
        "latitude": str(lat),
        "longitude": str(lon),
        "cid": f"{osm_type}/{osm_id}",
        "status": "open" if str(tags.get("opening_hours", "")).lower() not in ("", "closed") else "",
        "descriptions": description,
        "reviews_link": "",
        "thumbnail": "",
        "timezone": "",
        "price_range": "",
        "data_id": f"osm:{osm_type}/{osm_id}",
        "place_id": f"osm:{osm_type}/{osm_id}",
        "images": "",
        "reservations": "",
        "order_online": "",
        "menu": "",
        "owner": _pick(tags, "operator", "owner"),
        "complete_address": _address_of(item),
        "about": description,
        "user_reviews": "",
        "user_reviews_extended": "",
        "emails": email,
        "instagram": instagram,
        "facebook": facebook,
        "linkedin": linkedin,
        "source": "openstreetmap",
        "source_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
    }


NOMINATIM_MAX_LIMIT = 40          # سقف خودِ سرویس در هر درخواست


def search(
    term: str,
    *,
    bbox: list[float] | None = None,
    country: str | None = None,
    limit: int | None = None,
    timeout: float = 12.0,
    lang: str = "fa",
    input_id: str = "",
    pages: int = 1,
) -> list[dict]:
    """جست‌وجوی واقعی POI؛ نتیجه محدود به bbox (اگر داده شود).

    Nominatim در هر درخواست حداکثر ۴۰ نتیجه می‌دهد. برای شهرهای شلوغ این سقف
    یعنی «۴۰ کافه از ۲۰۰ کافه» — نه «شهر کوچک». `pages>1` با `exclude_place_ids`
    صفحهٔ بعد را می‌گیرد (یک درخواست/ثانیه؛ فقط وقتی لازم است).
    """
    want = int(limit or config.OSM_LIMIT)
    per_page = max(1, min(want, NOMINATIM_MAX_LIMIT))
    # سقف کل = اندازهٔ صفحه × تعداد صفحه‌ها؛ Nominatim به هر درخواست ۴۰ می‌دهد،
    # پس «۸۰ نتیجه» یعنی دو صفحهٔ ۴۰تایی، نه یک درخواست با limit=۸۰.
    cap = per_page * max(1, int(pages))
    params = {
        "q": term,
        "format": "jsonv2",
        "limit": per_page,
        "extratags": 1,
        "addressdetails": 1,
        "accept-language": "fa,en",
    }
    if bbox:
        params.update({"viewbox": viewbox_param(bbox), "bounded": 1})
    cc = (country or config.OSM_COUNTRY or "").strip().lower()
    if cc and len(cc) == 2:
        params["countrycodes"] = cc

    rows: list[dict] = []
    seen_ids: list[str] = []
    for page in range(max(1, int(pages))):
        if page:
            if len(rows) < per_page:      # صفحهٔ قبل پر نشد → چیزی نمانده
                break
            params["exclude_place_ids"] = ",".join(seen_ids)
        url = f"{config.OSM_ENDPOINT}/search?" + urllib.parse.urlencode(params)
        _throttle()
        data = _http_json(url, timeout=timeout)
        if not isinstance(data, list):
            raise OSMDownError("پاسخ OSM فهرست نبود")
        got = 0
        for item in data:
            if not isinstance(item, dict):
                continue
            row = row_from_item(item, input_id=input_id, lang=lang)
            if not row:
                continue
            got += 1
            place = row.get("place_id") or ""
            if place and place in seen_ids:
                continue
            rows.append(row)
            if place:
                seen_ids.append(place)
            if len(rows) >= cap:
                return rows
        if got < per_page:                # سرویس کمتر از سقف داد → تمام شد
            break
    return rows


# انواع نتیجه‌ای که «محدوده» حساب می‌شوند (خیابان، محله، شهر، مرز)
STREET_TYPES = {"highway", "road", "residential", "pedestrian", "footway", "service",
                "place", "neighbourhood", "suburb", "quarter", "borough",
                "city_district", "district", "administrative", "boundary",
                "locality", "town", "village", "city"}


def _squash(text: str) -> str:
    return " ".join((text or "").replace("\u200c", " ").split()).strip().lower()


def _name_matches(query: str, display_name: str) -> bool:
    """آیا نتیجهٔ Nominatim واقعاً همان خیابان/محله است؟ (نه هم‌نام در جای دیگر)"""
    wanted = _squash(_strip_street_prefix(query.split("،")[0].split(",")[0]))
    got = _squash(display_name)
    if not wanted or not got:
        return False
    return wanted in got


def _strip_street_prefix(text: str) -> str:
    for prefix in ("خیابان ", "بلوار ", "بزرگراه ", "اتوبان ", "کوچه ", "میدان ",
                   "بن‌بست ", "بن بست ", "بازار "):
        if text.startswith(prefix) and len(text) > len(prefix) + 2:
            return text[len(prefix):].strip()
    return text


def lookup_area(query: str, *, bbox: list[float] | None = None,
                country: str | None = None, timeout: float = 12.0,
                limit: int = 5, merge: bool = False) -> dict:
    """جعبهٔ واقعیِ یک خیابان/محله را از OSM می‌گیرد (نه یک نقطهٔ سرانگشتی).

    چرا؟ «خیابان ولیعصر» ۱۲ کیلومتر طول دارد؛ اگر با شعاع ۸۰۰ متری اسکراب شود،
    فقط یک تکه‌اش دیده می‌شود. Nominatim برای خیابان‌ها `boundingbox` واقعی
    می‌دهد؛ همان را برمی‌گردانیم تا پایپ‌لاین کل خیابان را بگردد.

    `merge=True` (برای خیابان/محله): همهٔ تکه‌های هم‌نام را در یک جعبه جمع می‌کند.
    Nominatim برای خیابان‌های بلند (ولیعصر تهران، ۱۷ کیلومتر) معمولاً **یک تکه**
    را برمی‌گرداند؛ بدون جمع‌کردن، فقط ۱ کیلومتر از خیابان اسکراب می‌شد.

    خروجی: `{"bbox": [s,n,w,e], "lat","lon","label","type","class"}` یا `{}`.
    """
    text = (query or "").strip()
    if len(text) < 3:
        return {}
    params = {
        "q": text, "format": "jsonv2", "limit": max(1, min(10, int(limit))),
        "extratags": 1, "addressdetails": 1, "accept-language": "fa,en",
    }
    if bbox and len(bbox) == 4:
        params.update({"viewbox": viewbox_param(bbox), "bounded": 1})
    cc = (country or config.OSM_COUNTRY or "").strip().lower()
    if cc and len(cc) == 2:
        params["countrycodes"] = cc
    url = f"{config.OSM_ENDPOINT}/search?" + urllib.parse.urlencode(params)
    _throttle()
    try:
        data = _http_json(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — نبودِ جعبه نباید اجرا را بشکند
        raise OSMDownError(f"جست‌وجوی محدوده در OSM ناموفق بود: {exc}") from exc
    if not isinstance(data, list):
        return {}
    picked: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        klass = str(item.get("class") or item.get("category") or "")
        if kind not in STREET_TYPES and klass not in STREET_TYPES:
            continue
        box = item.get("boundingbox")
        try:
            south, north, west, east = (float(x) for x in box)
        except (TypeError, ValueError):
            continue
        if not (north > south and east > west):
            continue
        detail = {
            "bbox": [round(south, 6), round(north, 6), round(west, 6), round(east, 6)],
            "lat": _float_or_none(item.get("lat")),
            "lon": _float_or_none(item.get("lon")),
            "label": str(item.get("display_name") or ""),
            "type": kind, "class": klass,
            "osm_type": str(item.get("osm_type") or ""),
            "osm_id": item.get("osm_id"),
        }
        if not merge:
            return detail
        picked.append(detail)
    if not picked:
        return {}
    if len(picked) == 1:
        return picked[0]
    # تکه‌های هم‌نام را یکی کن. اگر نام تطبیق نکند (نتیجهٔ نامرتبط)، همان اولی.
    named = [d for d in picked if _name_matches(query, d["label"])] or picked[:1]
    merged = [
        min(d["bbox"][0] for d in named), max(d["bbox"][1] for d in named),
        min(d["bbox"][2] for d in named), max(d["bbox"][3] for d in named),
    ]
    first = dict(named[0])
    first["bbox"] = [round(x, 6) for x in merged]
    first["segments"] = len(named)
    return first


def _float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def within(row: dict, bbox: list[float], *, margin: float = 0.2) -> bool:
    """آیا ردیف داخل جعبه است؟ (با حاشیه — برای فیلتر نتایج جست‌وجوی متنی)"""
    try:
        lat = float(row.get("latitude"))
        lon = float(row.get("longitude"))
        s, n, w, e = (float(x) for x in bbox)
    except (TypeError, ValueError):
        return False
    dlat, dlon = (n - s) * margin, (e - w) * margin
    return (s - dlat) <= lat <= (n + dlat) and (w - dlon) <= lon <= (e + dlon)


def overpass_health(*, force: bool = False, probe: bool = True) -> dict:
    """سلامت Overpass (کش ۵ دقیقه‌ای).

    `probe=False` یعنی «فقط کش»: /api/health هر بار منتظر سرویس بیرونی نمی‌ماند
    (سنجش واقعی با `?fresh=1` یا با یک جست‌وجوی واقعی انجام می‌شود).
    """
    now = time.time()
    cached = _OVERPASS_HEALTH.get("payload")
    if cached and not force and now - float(_OVERPASS_HEALTH.get("ts") or 0) < HEALTH_TTL:
        return dict(cached)
    endpoint = config.OVERPASS_ENDPOINTS[0] if config.OVERPASS_ENDPOINTS else ""
    if not probe:
        return {"up": None, "endpoint": endpoint, "mode": config.OSM_MODE,
                "note": "سنجیده نشده — با ?fresh=1 یا یک جست‌وجوی واقعی سنجیده می‌شود",
                "error": ""}
    started = time.time()
    payload: dict
    try:
        _overpass_throttle()
        req = urllib.request.Request(endpoint.replace("/interpreter", "/status"),
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = getattr(resp, "status", 200)
            text = resp.read().decode("utf-8", "replace")
        low = text.lower()
        up = status == 200 and ("connected" in low or "available" in low or low.startswith("ok"))
        payload = {"up": bool(up), "endpoint": endpoint,
                   "ms": int((time.time() - started) * 1000),
                   "error": "" if up else "وضعیت Overpass نامشخص"}
        if not up:
            # /status از بعضی شبکه‌ها (مثلاً Vercel) ۵۰۲ می‌دهد در حالی که
            # interpreter سالم است. پس با یک پرسش کوچک واقعی محک می‌زنیم.
            raise RuntimeError("status-not-ok")
    except Exception as exc:
        last_ok = float(_OVERPASS_LAST_OK[0] or 0)
        recent_ok = last_ok and (time.time() - last_ok) < 600
        # «تایم‌اوتِ /status» با «سرویسِ خراب» یکی نیست: روی GitHub Actions دیدیم
        # که /status کند است ولی همان لحظه ۳۹۲ نتیجهٔ واقعی برگشت. پس تایم‌اوت را
        # «نامعلوم» گزارش می‌کنیم، نه «خراب».
        timed_out = isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
        probed = False
        if not recent_ok:
            probed = _overpass_mini_probe()
        if recent_ok:
            up, note = True, "سرویس در ۱۰ دقیقهٔ گذشته پاسخ داده است"
        elif probed:
            up, note = True, "سنجیده شد با یک پرسش کوچک واقعی (مسیر /status در این شبکه معتبر نیست)"
        elif timed_out:
            up, note = None, "پاسخ وضعیت دیر آمد؛ نامعلوم (جست‌وجوی واقعی همچنان محک است)"
        else:
            up, note = False, "Overpass پاسخ نداد"
        payload = {"up": up, "endpoint": endpoint,
                   "ms": int((time.time() - started) * 1000),
                   "error": "" if (recent_ok or probed or timed_out)
                   else f"{type(exc).__name__}: {exc}"[:200],
                   "note": note,
                   "mini_probe": probed}
    _OVERPASS_HEALTH.update({"ts": now, "payload": payload})
    return dict(payload)


def _overpass_mini_probe(timeout: float = 6.0) -> bool:
    """محک واقعی و ارزان: یک کافه در جعبهٔ کوچک رامسر (اگر سرویس سالم باشد < ۱ ثانیه).

    چرا؟ `/status` گاهی از بعضی شبکه‌ها ۵۰۲ می‌دهد یا کند است، درحالی‌که همان لحظه
    interpreter پاسخ می‌دهد. سلامت باید با همان مسیری سنجیده شود که کار واقعی از آن
    می‌گذرد.
    """
    query = '[out:json][timeout:8];nwr["amenity"="cafe"](36.900,50.640,36.930,50.670);out 1;'
    try:
        data = _overpass_request(query, timeout=timeout, retries=0, deadline_s=8.0)
    except OSMDownError:
        return False
    return isinstance(data, dict) and "elements" in data


def overpass_limit_for(bbox: list[float] | None, default: int | None = None) -> int:
    """سقف نتیجه بر اساس وسعت جعبه — برای اینکه یک درخواست استان، سرویس را نکشد."""
    cap = int(default or config.OVERPASS_LIMIT)
    if not bbox or len(bbox) != 4:
        return cap
    s, n, w, e = (float(x) for x in bbox)
    span = max(abs(n - s), abs(e - w))
    if span > 2.5:                 # استان یا بزرگ‌تر
        return min(cap, 1200)
    if span > 0.8:
        return min(cap, 1500)
    return cap


def health(*, force: bool = False) -> dict:
    """سلامت سرویس OSM (کش‌شده تا هر ۵ دقیقه — برای /api/health سریع بماند)."""
    now = time.time()
    cached = _HEALTH_CACHE.get("payload")
    if cached and not force and now - float(_HEALTH_CACHE.get("ts") or 0) < HEALTH_TTL:
        return dict(cached)
    started = time.time()
    payload: dict
    try:
        # نقطهٔ /status سبک است (بدون جست‌وجو) — برای /api/health سریع می‌ماند
        _throttle()
        data = _http_json(f"{config.OSM_ENDPOINT}/status?format=json", timeout=6.0)
        up = isinstance(data, dict) and str(data.get("status")) in ("0", "True", "true")
        payload = {
            "up": up, "endpoint": config.OSM_ENDPOINT,
            "ms": int((time.time() - started) * 1000),
            "sample": str((data or {}).get("message") or "") if isinstance(data, dict) else "",
            "error": "" if up else "وضعیت OSM نامشخص",
        }
    except Exception as exc:
        payload = {"up": False, "endpoint": config.OSM_ENDPOINT,
                   "ms": int((time.time() - started) * 1000), "sample": "",
                   "error": f"{type(exc).__name__}: {exc}"[:200]}
    _HEALTH_CACHE.update({"ts": now, "payload": payload})
    return dict(payload)
