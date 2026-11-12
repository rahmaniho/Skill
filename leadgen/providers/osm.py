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

from .. import config

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


def row_from_item(item: dict, *, input_id: str = "", lang: str = "fa") -> dict | None:
    """یک نتیجهٔ Nominatim را به ردیف قرارداد موتور تبدیل می‌کند (بدون نام → None)."""
    tags = dict(item.get("extratags") or {})
    name = (item.get("name") or tags.get("name") or "").strip()
    if not name:
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


def search(
    term: str,
    *,
    bbox: list[float] | None = None,
    country: str | None = None,
    limit: int | None = None,
    timeout: float = 12.0,
    lang: str = "fa",
    input_id: str = "",
) -> list[dict]:
    """یک جست‌وجوی واقعی POI؛ نتیجه محدود به bbox (اگر داده شود)."""
    params = {
        "q": term,
        "format": "jsonv2",
        "limit": int(limit or config.OSM_LIMIT),
        "extratags": 1,
        "addressdetails": 1,
        "accept-language": "fa,en",
    }
    if bbox:
        params.update({"viewbox": viewbox_param(bbox), "bounded": 1})
    cc = (country or config.OSM_COUNTRY or "").strip().lower()
    if cc and len(cc) == 2:
        params["countrycodes"] = cc
    url = f"{config.OSM_ENDPOINT}/search?" + urllib.parse.urlencode(params)
    _throttle()
    data = _http_json(url, timeout=timeout)
    if not isinstance(data, list):
        raise OSMDownError("پاسخ OSM فهرست نبود")
    rows = []
    for item in data:
        if not isinstance(item, dict):
            continue
        row = row_from_item(item, input_id=input_id, lang=lang)
        if row:
            rows.append(row)
    return rows


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
