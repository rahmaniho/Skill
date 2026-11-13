"""موتور Google Places API (New) — مسیر تجاری، بدون داکر.

فعال‌سازی: `PLACES_API_KEY=...` (یا `GOOGLE_MAPS_API_KEY`) را در محیط بگذارید.
هزینه: هر جست‌وجوی Text Search در سطح Pro حدود ۳۲ دلار به‌ازای هر ۱۰۰۰ درخواست؛
با گزینهٔ محدودِ FieldMask فقط همان فیلدهایی را می‌گیریم که لازم داریم (هزینهٔ کمتر).

چرا این موتور؟ برای محصول تجاری، استفاده از API رسمی گوگل هم قانونی‌تر است و هم
پایدارتر از اسکرپ. موتور داکر (اسکرپ) برای حجم بالا/دادهٔ غنی‌تر می‌ماند.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .. import config

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id", "places.displayName", "places.formattedAddress", "places.shortFormattedAddress",
    "places.nationalPhoneNumber", "places.internationalPhoneNumber", "places.websiteUri",
    "places.rating", "places.userRatingCount", "places.location", "places.types",
    "places.regularOpeningHours", "places.googleMapsUri", "places.priceLevel",
    "places.businessStatus",
])


class PlacesError(RuntimeError):
    pass


def available() -> bool:
    return bool(config.PLACES_API_KEY)


def _post(body: dict, *, timeout: float) -> dict:
    if not config.PLACES_API_KEY:
        raise PlacesError("PLACES_API_KEY تنظیم نشده است")
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "X-Goog-Api-Key": config.PLACES_API_KEY,
                 "X-Goog-FieldMask": FIELD_MASK},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise PlacesError(f"Places HTTP {exc.code}: {detail or exc.reason}") from exc
    except Exception as exc:
        raise PlacesError(f"{type(exc).__name__}: {exc}") from exc


def row_from_place(place: dict, *, input_id: str = "") -> dict | None:
    name = ((place.get("displayName") or {}).get("text") or "").strip()
    if not name:
        return None
    loc = place.get("location") or {}
    try:
        lat = round(float(loc.get("latitude")), 6)
        lon = round(float(loc.get("longitude")), 6)
    except (TypeError, ValueError):
        return None
    types = [t for t in (place.get("types") or []) if t]
    hours = " · ".join((place.get("regularOpeningHours") or {}).get("weekdayDescriptions") or [])
    phone = place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber") or ""
    pid = place.get("id") or ""
    return {
        "input_id": input_id,
        "link": place.get("googleMapsUri") or (f"https://www.google.com/maps/place/?q=place_id:{pid}" if pid else ""),
        "title": name,
        "category": types[0] if types else "",
        "address": place.get("formattedAddress") or place.get("shortFormattedAddress") or "",
        "open_hours": hours,
        "popular_times": "",
        "website": place.get("websiteUri") or "",
        "phone": phone,
        "plus_code": "",
        "review_count": place.get("userRatingCount") or "",
        "review_rating": place.get("rating") or "",
        "reviews_per_rating": "",
        "latitude": str(lat),
        "longitude": str(lon),
        "cid": pid,
        "status": (place.get("businessStatus") or "").lower(),
        "descriptions": "",
        "reviews_link": "",
        "thumbnail": "",
        "timezone": "",
        "price_range": (place.get("priceLevel") or "").replace("PRICE_LEVEL_", "").lower(),
        "data_id": pid,
        "place_id": f"gp:{pid}" if pid else "",
        "images": "",
        "reservations": "",
        "order_online": "",
        "menu": "",
        "owner": "",
        "complete_address": place.get("formattedAddress") or "",
        "about": "",
        "user_reviews": "",
        "user_reviews_extended": "",
        "emails": "",
        "source": "google_places",
        "source_url": place.get("googleMapsUri") or "",
    }


def _rect(bbox: list) -> dict | None:
    """[s, n, w, e] → مستطیل مجاز Places (سخت‌گیرانه‌تر از دایره)."""
    try:
        south, north, west, east = (float(x) for x in bbox)
    except (TypeError, ValueError):
        return None
    if not (north > south and east > west):
        return None
    return {"rectangle": {"low": {"latitude": south, "longitude": west},
                          "high": {"latitude": north, "longitude": east}}}


def search(term: str, *, lat: float | None = None, lon: float | None = None,
           radius: int = 10000, limit: int = 20, lang: str = "fa",
           timeout: float = 15.0, input_id: str = "", bbox: list | None = None,
           pages: int = 1, strict: bool | None = None,
           page_wait: float = 1.2) -> list[dict]:
    """Text Search گوگل — با پشتیبانی از مستطیل (bbox) و صفحه‌بندی.

    چرا مستطیل؟ برای «خیابان/محله» دایرهٔ ۱۰ کیلومتری همسایه‌ها را هم می‌آورد؛
    `locationRestriction.rectangle` فقط همان محدوده را برمی‌گرداند. صفحه‌بندی هم
    سقف ۲۰ نتیجهٔ هر درخواست را تا ۶۰ بالا می‌برد — در شهرهای کوچک تفاوت زیاد است.
    """
    rect = _rect(bbox) if bbox else None
    restricted = rect is not None and (strict if strict is not None else True)
    body: dict = {
        "textQuery": term,
        "languageCode": "fa" if str(lang).startswith("fa") else "en",
        "maxResultCount": max(1, min(20, int(limit))),
    }
    if restricted:
        body["locationRestriction"] = rect
    elif lat is not None and lon is not None:
        body["locationBias"] = {"circle": {"center": {"latitude": float(lat),
                                                      "longitude": float(lon)},
                                           "radius": float(max(500, min(50_000, int(radius))))}}
    started = time.time()
    rows: list[dict] = []
    seen: set[str] = set()
    token = ""
    for page in range(max(1, min(3, int(pages)))):
        left = timeout - (time.time() - started)
        if left <= 2:
            break
        if page and token:
            body = dict(body, pageToken=token)
            if page_wait:
                time.sleep(min(float(page_wait), max(0.0, left - 2)))
        data = _post(body, timeout=min(left, float(timeout)))
        for place in data.get("places") or []:
            if not isinstance(place, dict):
                continue
            row = row_from_place(place, input_id=input_id)
            if not row:
                continue
            pid = row.get("place_id") or row.get("title")
            if pid in seen:
                continue
            seen.add(pid)
            rows.append(row)
            if len(rows) >= int(limit):
                return rows
        token = str(data.get("nextPageToken") or "")
        if not token:
            break
    return rows


def health(*, force: bool = False) -> dict:
    if not config.PLACES_API_KEY:
        return {"up": False, "endpoint": ENDPOINT, "error": "PLACES_API_KEY تنظیم نشده",
                "key_set": False}
    started = time.time()
    try:
        rows = search("dentist", lat=35.72, lon=51.40, radius=3000, limit=1, timeout=8.0)
        return {"up": True, "endpoint": ENDPOINT, "key_set": True,
                "ms": int((time.time() - started) * 1000),
                "sample": rows[0]["title"] if rows else "", "error": ""}
    except Exception as exc:
        return {"up": False, "endpoint": ENDPOINT, "key_set": True,
                "ms": int((time.time() - started) * 1000),
                "error": f"{type(exc).__name__}: {exc}"[:200]}
