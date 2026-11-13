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


def _overpass_request(query: str, *, timeout: float, retries: int = 1,
                      deadline_s: float | None = None) -> dict:
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
                with urllib.request.urlopen(req, timeout=min(remaining, float(timeout) + 10)) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as exc:
                errors.append(f"{host} HTTP {exc.code}")
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


def overpass_search_area(filters: list[str], *, bbox: list[float], limit: int | None = None,
                         timeout: float | None = None, input_id: str = "",
                         lang: str = "fa", tiles: int = 1, max_rows: int | None = None,
                         budget_s: float | None = None) -> list[dict]:
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
