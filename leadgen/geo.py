"""جغرافیا: ژئوکد شهرها، شبکه‌بندی (grid) برای پوشش کامل شهر، و فیلتر محدوده.

اول از یک جدول داخلی شهرها استفاده می‌کند (آفلاین، بدون کلید API و بدون درخواست شبکه)
و اگر شهر پیدا نشد به OpenStreetMap Nominatim می‌رود (رایگان، ~۱ درخواست در ثانیه،
با User-Agent مشخص). این دقیقاً همان الگویی است که اسکیل کیت توصیه می‌کند.
"""
from __future__ import annotations

import json
import math
import re
import time
import unicodedata
import urllib.parse
import urllib.request

from . import config

# نام شهر → (lat, lon, کشور, [south, north, west, east] تقریبی برای grid)
# bbox حدودی است؛ برای --grid کافی است و باعث می‌شود حالت آفلاین هم کامل کار کند.
CITIES: dict[str, tuple[float, float, str, list[float]]] = {
    # ── ایران ────────────────────────────────────────────────────────────────
    "tehran": (35.6892, 51.3890, "IR", [35.5500, 35.8300, 51.1500, 51.6200]),
    "mashhad": (36.2972, 59.6067, "IR", [36.1800, 36.4200, 59.4200, 59.8000]),
    "isfahan": (32.6539, 51.6660, "IR", [32.5300, 32.7900, 51.5000, 51.8200]),
    "shiraz": (29.5918, 52.5836, "IR", [29.4800, 29.7000, 52.4200, 52.7200]),
    "tabriz": (38.0962, 46.2738, "IR", [37.9800, 38.2000, 46.1000, 46.4200]),
    "karaj": (35.8400, 50.9391, "IR", [35.7400, 35.9400, 50.7800, 51.0900]),
    "ahvaz": (31.3183, 48.6706, "IR", [31.2000, 31.4400, 48.5200, 48.8200]),
    "qom": (34.6416, 50.8746, "IR", [34.5400, 34.7600, 50.7200, 51.0200]),
    "kermanshah": (34.3277, 47.0778, "IR", [34.2200, 34.4300, 46.9200, 47.2200]),
    "urmia": (37.5448, 45.0758, "IR", [37.4400, 37.6500, 44.9200, 45.2200]),
    "rasht": (37.2808, 49.5832, "IR", [37.1800, 37.3800, 49.4500, 49.7200]),
    "zahedan": (29.4963, 60.8629, "IR", [29.4000, 29.6000, 60.7200, 61.0000]),
    "kerman": (30.2839, 57.0834, "IR", [30.1800, 30.4000, 56.9400, 57.2200]),
    "yazd": (31.8974, 54.3569, "IR", [31.8000, 32.0000, 54.2200, 54.5000]),
    "hamedan": (34.7988, 48.5150, "IR", [34.7000, 34.9000, 48.3800, 48.6500]),
    "arak": (34.0917, 49.6892, "IR", [33.9900, 34.1900, 49.5500, 49.8200]),
    "bandar-abbas": (27.1832, 56.2666, "IR", [27.0800, 27.2800, 56.1200, 56.4000]),
    "zanjan": (36.6830, 48.5087, "IR", [36.5800, 36.7800, 48.3800, 48.6500]),
    "sanandaj": (35.3149, 46.9988, "IR", [35.2200, 35.4200, 46.8700, 47.1300]),
    "qazvin": (36.2688, 50.0040, "IR", [36.1700, 36.3700, 49.8800, 50.1400]),
    "khorramabad": (33.4878, 48.3558, "IR", [33.3900, 33.5900, 48.2300, 33.6000]),
    "babol": (36.5513, 52.6790, "IR", [36.4600, 36.6500, 52.5500, 36.7000]),
    "sari": (36.5633, 53.0601, "IR", [36.4700, 36.6600, 52.9400, 53.1800]),
    "kish": (26.5581, 53.9861, "IR", [26.4800, 26.6300, 53.9000, 54.0700]),
    # ── خاورمیانه ────────────────────────────────────────────────────────────
    "dubai": (25.2048, 55.2708, "AE", [25.0500, 25.4000, 55.1000, 55.5500]),
    "abu-dhabi": (24.4539, 54.3773, "AE", [24.2800, 24.6000, 54.2000, 54.6500]),
    "doha": (25.2854, 51.5310, "QA", [25.1500, 25.4200, 51.3500, 51.7000]),
    "riyadh": (24.7136, 46.6753, "SA", [24.5000, 24.9000, 46.4500, 46.9000]),
    "jeddah": (21.4858, 39.1925, "SA", [21.3000, 21.6800, 39.0000, 39.4000]),
    "istanbul": (41.0082, 28.9784, "TR", [40.8000, 41.2000, 28.7000, 29.2500]),
    "ankara": (39.9334, 32.8597, "TR", [39.7800, 40.0800, 32.6000, 33.1000]),
    "izmir": (38.4237, 27.1428, "TR", [38.2800, 38.5700, 26.9500, 27.3500]),
    "baghdad": (33.3152, 44.3661, "IQ", [33.1500, 33.5000, 44.1500, 44.6000]),
    "kabul": (34.5553, 69.2075, "AF", [34.4200, 34.7000, 69.0000, 69.4200]),
    # ── اروپا ────────────────────────────────────────────────────────────────
    "frankfurt": (50.1109, 8.6821, "DE", [49.9800, 50.2500, 8.4500, 8.9500]),
    "berlin": (52.5200, 13.4050, "DE", [52.3300, 52.6800, 13.0800, 13.7600]),
    "munich": (48.1351, 11.5820, "DE", [48.0000, 48.2800, 11.3500, 11.8000]),
    "hamburg": (53.5511, 9.9937, "DE", [53.3900, 53.7200, 9.7300, 10.3300]),
    "cologne": (50.9375, 6.9603, "DE", [50.8200, 51.0800, 6.7500, 7.1800]),
    "stuttgart": (48.7758, 9.1829, "DE", [48.6600, 48.9000, 9.0000, 9.3800]),
    "dusseldorf": (51.2277, 6.7735, "DE", [51.1200, 51.3500, 6.6000, 6.9500]),
    "vienna": (48.2082, 16.3738, "AT", [48.1000, 48.3200, 16.1800, 16.5800]),
    "london": (51.5074, -0.1278, "GB", [51.2800, 51.7000, -0.5200, 0.3400]),
    "manchester": (53.4808, -2.2426, "GB", [53.3500, 53.6000, -2.5000, -2.0000]),
    "paris": (48.8566, 2.3522, "FR", [48.7500, 49.0000, 2.1000, 2.6000]),
    "amsterdam": (52.3676, 4.9041, "NL", [52.2500, 52.4800, 4.7000, 5.1000]),
    "barcelona": (41.3874, 2.1686, "ES", [41.2800, 41.5000, 1.9800, 2.3500]),
    "madrid": (40.4168, -3.7038, "ES", [40.2800, 40.5600, -3.9000, -3.5000]),
    "rome": (41.9028, 12.4964, "IT", [41.7800, 42.0200, 12.3000, 12.7000]),
    "milan": (45.4642, 9.1900, "IT", [45.3400, 45.5800, 9.0000, 9.3800]),
    "zurich": (47.3769, 8.5417, "CH", [47.2800, 47.4700, 8.4000, 8.7000]),
    "warsaw": (52.2297, 21.0122, "PL", [52.0800, 52.3800, 20.8000, 21.2000]),
    "stockholm": (59.3293, 18.0686, "SE", [59.2000, 59.4500, 17.8000, 18.3000]),
    # ── آمریکا / کانادا / آسیا-اقیانوسیه ─────────────────────────────────────
    "new-york": (40.7128, -74.0060, "US", [40.4700, 40.9200, -74.2600, -73.7000]),
    "los-angeles": (34.0522, -118.2437, "US", [33.7000, 34.3400, -118.6700, -118.1500]),
    "chicago": (41.8781, -87.6298, "US", [41.6400, 42.0200, -87.9000, -87.5200]),
    "denver": (39.7392, -104.9903, "US", [39.5500, 39.9200, -105.2000, -104.8000]),
    "austin": (30.2672, -97.7431, "US", [30.0500, 30.5200, -98.0000, -97.5500]),
    "miami": (25.7617, -80.1918, "US", [25.5500, 25.9500, -80.4500, -80.1500]),
    "phoenix": (33.4484, -112.0740, "US", [33.2000, 33.7000, -112.3500, -111.9000]),
    "dallas": (32.7767, -96.7970, "US", [32.5500, 33.0000, -97.0500, -96.5000]),
    "seattle": (47.6062, -122.3321, "US", [47.4500, 47.7500, -122.5000, -122.2000]),
    "toronto": (43.6532, -79.3832, "CA", [43.5000, 43.8200, -79.6500, -79.1000]),
    "vancouver": (49.2827, -123.1207, "CA", [49.1500, 49.4000, -123.3000, -122.9500]),
    "sydney": (-33.8688, 151.2093, "AU", [-34.0500, -33.6500, 150.9000, 151.4500]),
    "melbourne": (-37.8136, 144.9631, "AU", [-38.0000, -37.6500, 144.7000, 145.2000]),
    "singapore": (1.3521, 103.8198, "SG", [1.1500, 1.4800, 103.6000, 104.0500]),
    "tokyo": (35.6762, 139.6503, "JP", [35.4500, 35.9000, 139.3500, 139.9200]),
    "delhi": (28.7041, 77.1025, "IN", [28.4000, 28.9000, 76.8000, 77.3500]),
    "mumbai": (19.0760, 72.8777, "IN", [18.8500, 19.2500, 72.7000, 73.0500]),
}

# نام‌های نمایشی و نام‌های مستعار (فارسی/لاتین) برای تطبیق بهتر ورودی کاربر
ALIASES: dict[str, list[str]] = {
    "tehran": ["تهران", "تهران، ایران", "tehran iran"],
    "mashhad": ["مشهد", "مشهد مقدس"],
    "isfahan": ["اصفهان", "شهر اصفهان"],
    "shiraz": ["شیراز"],
    "tabriz": ["تبریز"],
    "karaj": ["کرج"],
    "ahvaz": ["اهواز"],
    "qom": ["قم"],
    "kermanshah": ["کرمانشاه"],
    "urmia": ["ارومیه"],
    "rasht": ["رشت"],
    "zahedan": ["زاهدان"],
    "kerman": ["کرمان"],
    "yazd": ["یزد"],
    "hamedan": ["همدان"],
    "arak": ["اراک"],
    "bandar-abbas": ["بندرعباس", "بندر عباس"],
    "zanjan": ["زنجان"],
    "sanandaj": ["سنندج"],
    "qazvin": ["قزوین"],
    "khorramabad": ["خرم‌آباد", "خرم آباد"],
    "babol": ["بابل"],
    "sari": ["ساری"],
    "kish": ["کیش"],
    "dubai": ["دبی", "دوبی"],
    "abu-dhabi": ["ابوظبی"],
    "doha": ["دوحه"],
    "riyadh": ["ریاض"],
    "jeddah": ["جده"],
    "istanbul": ["استانبول"],
    "ankara": ["آنکارا"],
    "izmir": ["ازمیر"],
    "baghdad": ["بغداد"],
    "kabul": ["کابل"],
    "frankfurt": ["فرانکفورت", "فرانکفورت آم ماین"],
    "berlin": ["برلین"],
    "munich": ["مونیخ", "مونیخ آلمان"],
    "hamburg": ["هامبورگ"],
    "cologne": ["کلن", "کلن آلمان"],
    "stuttgart": ["اشتوتگارت"],
    "dusseldorf": ["دوسلدورف"],
    "vienna": ["وین"],
    "london": ["لندن"],
    "manchester": ["منچستر"],
    "paris": ["پاریس"],
    "amsterdam": ["آمستردام"],
    "barcelona": ["بارسلونا"],
    "madrid": ["مادرید"],
    "rome": ["رم"],
    "milan": ["میلان"],
    "zurich": ["زوریخ"],
    "warsaw": ["ورشو"],
    "stockholm": ["استکهلم"],
    "new-york": ["نیویورک", "new york ny"],
    "los-angeles": ["لس‌آنجلس", "los angeles ca"],
    "chicago": ["شیکاگو"],
    "denver": ["دنور", "denver co"],
    "austin": ["آستین", "austin tx"],
    "miami": ["میامی", "miami fl"],
    "phoenix": ["فینیکس", "phoenix az"],
    "dallas": ["دالاس"],
    "seattle": ["سیاتل"],
    "toronto": ["تورنتو"],
    "vancouver": ["ونکوور"],
    "sydney": ["سیدنی"],
    "melbourne": ["ملبورن"],
    "singapore": ["سنگاپور"],
    "tokyo": ["توکیو"],
    "delhi": ["دهلی"],
    "mumbai": ["بمبئی", "میمبی"],
}

# محله‌های چند شهر مهم — برای گسترش کلیدواژه‌ها در حالت «پوشش کامل شهر»
NEIGHBORHOODS: dict[str, list[str]] = {
    "tehran": [
        "ونک", "نیاوران", "زعفرانیه", "شهرک غرب", "سعادت‌آباد", "پاسداران", "الهیه",
        "جردن", "ولیعصر", "انقلاب", "صادقیه", "پونک", "تهرانپارس", "نارمک", "یوسف‌آباد",
        "امیرآباد", "آریاشهر", "اکباتان", "فرمانیه", "دروس", "عباس‌آباد", "بازار تهران",
    ],
    "mashhad": ["احمدآباد", "سجاد", "وکیل‌آباد", "طرقبه", "گلبهار", "هنرستان", "رضا شهر", "خیام شمالی"],
    "isfahan": ["خانه اصفهان", "مرداویج", "آبشار", "شهرک بهار", "خیابان جلفا", "چهارباغ"],
    "shiraz": ["معالی‌آباد", "زرهی", "ستارخان", "افزونه", "قصردشت", "معالی اباد"],
    "dubai": ["Downtown Dubai", "Dubai Marina", "Jumeirah", "Business Bay", "Deira", "Al Barsha", "Karama"],
    "frankfurt": ["Innenstadt", "Sachsenhausen", "Bockenheim", "Nordend", "Westend", "Bornheim"],
    "berlin": ["Mitte", "Kreuzberg", "Prenzlauer Berg", "Charlottenburg", "Neukölln", "Friedrichshain"],
    "istanbul": ["Kadıköy", "Beşiktaş", "Şişli", "Üsküdar", "Bakırköy", "Beyoğlu"],
}


def _normalize(text: str) -> str:
    """یکسان‌سازی متن فارسی/عربی/لاتین برای تطبیق: نیم‌فاصله، اعراب، ی/ک، بزرگی حروف."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    text = re.sub(r"[\s,،;؛.\-_]+", " ", text).strip().lower()
    for a, b in (("ي", "ی"), ("ك", "ک"), ("ة", "ه"), ("آ", "ا"), ("أ", "ا"), ("إ", "ا"),
                 ("ؤ", "و"), ("ئ", "ی")):
        text = text.replace(a, b)
    return text


_CITY_LOOKUP: dict[str, str] = {}
for _key in CITIES:
    _CITY_LOOKUP[_key] = _key
    _CITY_LOOKUP[_key.replace("-", " ")] = _key
    for _alias in ALIASES.get(_key, []):
        _CITY_LOOKUP[_normalize(_alias)] = _key


def offline_lookup(place: str) -> str | None:
    """تطبیق نام شهر با جدول داخلی → کلید شهر یا None."""
    if not place:
        return None
    key = _CITY_LOOKUP.get(_normalize(place))
    if key:
        return key
    # تطبیق جزئی: «رستوران در تهران» یا «tehran, iran»
    norm = _normalize(place)
    for candidate, mapped in _CITY_LOOKUP.items():
        if len(candidate) >= 3 and candidate in norm:
            return mapped
    return None


def default_bbox(lat: float, lon: float, half_deg: float = 0.10) -> list[float]:
    """وقتی bbox واقعی نداریم، یک جعبه تقریبی حول مرکز شهر می‌سازیم."""
    return [lat - half_deg, lat + half_deg, lon - half_deg, lon + half_deg]


def geocode(place: str, with_bbox: bool = False, allow_network: bool = True):
    """نام شهر → (lat, lon) به‌صورت رشته، مطابق قرارداد API موتور.

    با with_bbox=(True) خروجی (lat, lon, bbox) است. اول جدول داخلی، بعد Nominatim.
    """
    key = offline_lookup(place)
    if key:
        lat, lon, country, bbox = CITIES[key]
        if with_bbox:
            return f"{lat:.4f}", f"{lon:.4f}", bbox, key
        return f"{lat:.4f}", f"{lon:.4f}", None, key

    if allow_network:
        remote = _nominatim(place, with_bbox)
        if remote:
            return remote
    if with_bbox:
        return None, None, None, None
    return None, None, None, None


def _nominatim(place: str, with_bbox: bool):
    """ژئوکد آنلاین از OpenStreetMap (رایگان، بدون کلید) — با احترام به ~۱ درخواست/ثانیه."""
    query = urllib.parse.urlencode({"format": "json", "limit": 1, "q": place})
    url = f"https://nominatim.openstreetmap.org/search?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            hits = json.loads(resp.read().decode("utf-8", "replace"))
        time.sleep(1.0)  # سیاست Nominatim
        if not hits:
            return None
        lat, lon = str(hits[0]["lat"]), str(hits[0]["lon"])
        raw_box = hits[0].get("boundingbox")
        bbox = [float(x) for x in raw_box] if raw_box else default_bbox(float(lat), float(lon))
        if with_bbox:
            return lat, lon, bbox, None
        return lat, lon, None, None
    except Exception:
        return None


def city_label(key: str | None, fallback: str) -> str:
    """نام لاتین/استاندارد شهر — برای کلیدواژه‌های انگلیسی که به موتور می‌روند."""
    if not key:
        return fallback
    return key.replace("-", " ").title()


def local_label(key: str | None, fallback: str) -> str:
    """نام محلی (فارسی اگر باشد) — برای کلیدواژه فارسی، نشانی و متن کمپین.

    اول نام فارسی ثبت‌شده در جدول، بعد همان چیزی که کاربر نوشته، و در نهایت نام لاتین.
    """
    if key:
        aliases = [a for a in ALIASES.get(key, []) if a and re.search(r"[\u0600-\u06FF]", a)]
        if aliases:
            return aliases[0]
    if fallback and fallback.strip():
        return fallback.strip()
    return city_label(key, fallback)


def grid_cells(bbox: list[float], n: int) -> list[dict]:
    """جعبه شهر را به n×n کاشی تقسیم می‌کند → [{lat, lon, radius, zoom}].

    radius (متر) = ۱.۱۵ × نصف قطر کاشی، دست‌کم ۱۰۰۰ متر؛ zoom طوری انتخاب می‌شود که
    نمای ~۱۰۰۰ پیکسلی گوگل‌مپ تقریباً هم‌اندازه کاشی باشد (بین ۱۱ و ۱۷).
    هر کاشی = یک job جدا که «سقف ~۱۲۰ نتیجه در هر جست‌وجو» را می‌شکند.
    """
    s, nn, w, e = (float(x) for x in bbox)
    dlat, dlon = (nn - s) / n, (e - w) / n
    m_per_deg = 6371000 * math.pi / 180
    cells: list[dict] = []
    for i in range(n):
        for j in range(n):
            lat, lon = s + (i + 0.5) * dlat, w + (j + 0.5) * dlon
            cos = math.cos(math.radians(lat))
            width = max(1.0, dlon * m_per_deg * cos)
            diag = math.hypot(dlat * m_per_deg, width)
            zoom = round(math.log2(156543.03 * cos * 1000 / width))  # ۱۵۶۵۴۳ متر/پیکسل در زوم ۰
            cells.append({
                "lat": f"{lat:.6f}",
                "lon": f"{lon:.6f}",
                "radius": max(1000, math.ceil(1.15 * diag / 2)),
                "zoom": min(17, max(11, zoom)),
            })
    return cells


def in_bbox(row: dict, bbox: list[float], margin: float = 0.10) -> bool:
    """آیا مختصات ردیف داخل جعبه شهر (با ۱۰٪ حاشیه) است؟ ردیف بدون مختصات → قبول."""
    try:
        lat, lon = float(row.get("latitude")), float(row.get("longitude"))
    except (TypeError, ValueError):
        return True
    s, n, w, e = (float(x) for x in bbox)
    my, mx = (n - s) * margin, (e - w) * margin
    return s - my <= lat <= n + my and w - mx <= lon <= e + mx


def dedupe(rows: list[dict]) -> list[dict]:
    """حذف تکراری‌ها: اول place_id، بعد cid، بعد link، در نهایت عنوان+نشانی."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        key = next(
            (f"{k}:{r[k]}" for k in ("place_id", "cid", "link") if r.get(k)),
            f"ta:{r.get('title', '')}|{r.get('address', '')}",
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def known_cities() -> list[dict]:
    """فهرست شهرهای موجود برای UI (جست‌وجوی شهر بدون نیاز به اینترنت)."""
    items = []
    for key, (lat, lon, country, bbox) in CITIES.items():
        label = key.replace("-", " ").title()
        aliases = [a for a in ALIASES.get(key, []) if a]
        items.append({
            "id": key,
            "label": label,
            "country": country,
            "lat": lat,
            "lon": lon,
            "fa": aliases[0] if aliases else "",
            "aliases": aliases,
            "neighborhoods": NEIGHBORHOODS.get(key, []),
        })
    return sorted(items, key=lambda c: (c["country"], c["label"]))
