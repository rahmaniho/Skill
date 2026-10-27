"""مغز رابط ساده (Smart Mode): زبان طبیعی → اجرای کامل و قابل توضیح.

کاربر فقط یک جمله می‌نویسد («دندانپزشکی در تهران»، «همه مشاغل در مشهد»،
«plumbers in Denver CO») و هدفش را با یک چیپ انتخاب می‌کند («سایت می‌فروشم»).
این ماژول از همان لایه‌های موجود (اصناف، ژئوکد، پایپ‌لاین) استفاده می‌کند تا:

  • صنف و شهر را تشخیص دهد (یا «جاروی تمامی مشاغل»)
  • اندازهٔ اجرا (سریع/استاندارد/کامل) را به depth/grid/limit ترجمه کند
  • هدف کاربر را به پروفایل امتیازدهی نگاشت کند
  • یک درخواست آمادهٔ `POST /api/runs` بسازد
  • خلاصهٔ انسانی، پیش‌نمایش کلیدواژه‌ها، زمان تقریبی و هشدارها را برگرداند

هیچ تصمیمی پنهان نیست: هر چه تشخیص داده شود در `detected` و `summary` برمی‌گردد
تا رابط بتواند به کاربر نشان دهد و در صورت نیاز اصلاحش کند.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from . import geo, niches, pipeline, regions, scoring

# ─────────────────────────────────────────────────────────────────────────────
# ۱) هدف کاربر → پروفایل امتیازدهی (بدون اصطلاح فنی)
# ─────────────────────────────────────────────────────────────────────────────
GOALS: list[dict] = [
    {"id": "site", "icon": "🖥️", "fa": "سایت یا فروشگاه می‌فروشم", "profile": "web_design",
     "hint": "کسب‌وکارهای بدون سایت یا با سایت ضعیف داغ‌ترین لید شما هستند."},
    {"id": "ads", "icon": "📣", "fa": "تبلیغات، سئو یا اینستاگرام", "profile": "marketing",
     "hint": "روی دیده‌شدن و نظرات کم تمرکز می‌کند؛ مناسب آژانس‌ها و فریلنسرها."},
    {"id": "supply", "icon": "📦", "fa": "کالا یا تجهیزات به کسب‌وکارها", "profile": "b2b_supply",
     "hint": "اولویت با دسترسی آسان (تلفن/ایمیل) و اعتبار بالاست."},
    {"id": "hire", "icon": "🧑‍💼", "fa": "نیرو یا همکار جذب می‌کنم", "profile": "recruitment",
     "hint": "کسب‌وکارهای پرحجم و فعال، گزینهٔ بهتری برای همکاری‌اند."},
    {"id": "list", "icon": "📋", "fa": "فقط یک فهرست تمیز می‌خواهم", "profile": "generic",
     "hint": "امتیازدهی متوازن؛ بدون اولویت خاص."},
]

# ─────────────────────────────────────────────────────────────────────────────
# ۲) اندازهٔ اجرا → پارامترهای موتور (ساده برای کاربر، دقیق برای موتور)
# ─────────────────────────────────────────────────────────────────────────────
SIZES: list[dict] = [
    {"id": "quick", "icon": "⚡", "fa": "سریع", "hint": "چند دقیقه، نمونهٔ کوچک بازار",
     "depth": 2, "grid": 0, "limit": 60, "keywords_per_niche": 1, "max_niches": 6,
     "max_time": 300, "minutes": (1, 3)},
    {"id": "standard", "icon": "🎯", "fa": "استاندارد", "hint": "پوشش خوب یک صنف در شهر",
     "depth": 4, "grid": 0, "limit": 200, "keywords_per_niche": 2, "max_niches": 12,
     "max_time": 600, "minutes": (3, 8)},
    {"id": "deep", "icon": "🛰️", "fa": "کامل (کل شهر)", "hint": "کاشی‌بندی شهر + حجم بالا",
     "depth": 5, "grid": 3, "limit": 0, "keywords_per_niche": 2, "max_niches": 12,
     "max_time": 1200, "minutes": (10, 35)},
]

EXAMPLES = [
    "دندانپزشکی در تهران",
    "همه مشاغل در مشهد",
    "کافه و رستوران در اصفهان",
    "خدمات فنی و ساختمان در شیراز",
    "plumbers in Denver CO",
]

# عبارت‌هایی که یعنی «همهٔ مشاغل را جارو بزن»
_ALL_TRADES = (
    "همه مشاغل", "همهٔ مشاغل", "تمام مشاغل", "تمامی مشاغل", "همه کسب و کار",
    "همهٔ کسب‌وکار", "تمام کسب و کار", "کل مشاغل", "همه اصناف", "تمام اصناف",
    "همه ی مشاغل", "all businesses", "all business", "all trades", "every business",
    "all companies", "whole market",
)

# نام‌های کوتاه گروه‌ها (کاربر معمولاً کامل نمی‌نویسد)
_GROUP_HINTS = {
    "سلامت": "سلامت و پزشکی", "پزشکی": "سلامت و پزشکی", "درمان": "سلامت و پزشکی",
    "زیبایی": "زیبایی و آرایشی", "آرایشی": "زیبایی و آرایشی", "آرایشگاه": "زیبایی و آرایشی",
    "غذا": "غذا و نوشیدنی", "رستوران": "غذا و نوشیدنی", "کافه": "غذا و نوشیدنی",
    "فنی": "خدمات فنی و ساختمان", "ساختمان": "خدمات فنی و ساختمان", "تعمیرات": "خدمات فنی و ساختمان",
    "خودرو": "خودرو و حمل‌ونقل", "ماشین": "خودرو و حمل‌ونقل",
    "حقوقی": "حقوقی و مالی", "مالی": "حقوقی و مالی", "وکیل": "حقوقی و مالی",
    "آموزش": "آموزش و ورزش", "ورزش": "آموزش و ورزش", "باشگاه": "آموزش و ورزش",
    "رویداد": "رویداد و رسانه", "رسانه": "رویداد و رسانه", "عکاسی": "رویداد و رسانه",
    "خرده": "خرده‌فروشی", "فروشگاه": "خرده‌فروشی", "پوشاک": "خرده‌فروشی",
    "سفر": "سفر و اقامت", "اقامت": "سفر و اقامت", "هتل": "سفر و اقامت",
    "فناوری": "فناوری و خدمات حرفه‌ای", "تکنولوژی": "فناوری و خدمات حرفه‌ای",
    "صنعت": "صنعت و B2B", "b2b": "صنعت و B2B", "تولیدی": "صنعت و B2B",
    "محلی": "خدمات محلی", "خدمات محلی": "خدمات محلی",
}

_PERSIAN_RE = re.compile(r"[\u0600-\u06FF]")
_FILLER_FA = ("در", "و", "های", "ها", "شهر", "محدوده", "کل", "اطراف", "منطقه", "یه", "یک", "چند", "تا")
_FILLER_EN = ("in", "the", "and", "near", "around", "of", "for", "a", "an", "city", "area")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u200c", " ").strip())


def _persian_ratio(text: str) -> float:
    if not text:
        return 0.0
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _PERSIAN_RE.match(c)) / len(letters)


def detect_all_trades(text: str) -> bool:
    """آیا کاربر «تمامی مشاغل» را خواسته است؟"""
    flat = _norm(text).lower()
    return any(phrase in flat for phrase in _ALL_TRADES)


def detect_groups(text: str) -> list[str]:
    """گروه‌های شغلی که کاربر صریحاً نام برده (نام کامل یا کوتاه)."""
    flat = _norm(text).lower()
    found: list[str] = []
    for group in niches.GROUPS:
        if group.lower() in flat and group not in found:
            found.append(group)
    for hint, group in _GROUP_HINTS.items():
        if re.search(rf"(^|\s|،|,){re.escape(hint)}(\s|$|،|,)", flat) and group not in found:
            found.append(group)
    return found


_CITY_INDEX: list[tuple[str, dict]] | None = None


def _city_index() -> list[tuple[str, dict]]:
    """فهرست نام شهرها (فارسی/لاتین/مستعار) → کلید و کشور؛ بلندترین نام اول."""
    global _CITY_INDEX
    if _CITY_INDEX is None:
        rows: list[tuple[str, dict]] = []
        for city in geo.known_cities():
            info = {"key": city["id"], "country": city.get("country", ""),
                    "label": city.get("label", ""), "fa": city.get("fa", "")}
            names = {str(city.get("fa") or "").strip().lower(),
                     str(city.get("label") or "").strip().lower(),
                     *(str(a).strip().lower() for a in city.get("aliases", []))}
            for name in names:
                if len(name) >= 3:
                    rows.append((name, info))
        rows.sort(key=lambda item: -len(item[0]))
        _CITY_INDEX = rows
    return _CITY_INDEX


def _find_city_name(text: str) -> tuple[str, dict] | None:
    """اولین (بلندترین) نام شهری که واقعاً در متن هست — نه کل جمله."""
    flat = _norm(text).lower()
    if not flat:
        return None
    for name, info in _city_index():
        index = flat.find(name)
        if index >= 0:
            original = _norm(text)
            return original[index:index + len(name)] if index + len(name) <= len(original) else name, info
    return None


def _lookup_segment(segment: str) -> tuple[str, dict] | None:
    """جست‌وجوی شهر در یک بخش کوتاه (نام شهر معمولاً حداکثر ۳ کلمه است)."""
    words = _norm(segment).split()
    if not words or len(words) > 3 or len(segment) > 34:
        return None
    key = geo.offline_lookup(segment)
    if not key:
        return None
    lat, lon, country, _bbox = geo.CITIES[key]
    return segment, {"key": key, "country": country,
                     "label": geo.city_label(key, segment), "fa": geo.local_label(key, segment)}


_NONE_CITY = {"key": "", "label": "", "label_local": "", "country": "", "matched": "", "source": "none"}


def detect_city(text: str, *, allow_network: bool = False) -> dict:
    """شهر را از جمله بیرون می‌کشد.

    ترتیب تلاش: بخش بعد از «در / in / ،» ← نام شهر در کل جمله ← بخش کوتاه (offline_lookup)
    ← ژئوکد Nominatim (فقط اگر شبکه مجاز باشد).
    خروجی: {key, label, label_local, country, matched, source} — `matched` همان تکه‌ای است
    که باید از جمله حذف شود تا صنف خالص بماند.
    """
    flat = _norm(text)
    if not flat:
        return dict(_NONE_CITY)

    for pattern in (r"\bدر\s+(.+)$", r"\bاز\s+(.+)$", r"\bin\s+(.+)$", r"\bnear\s+(.+)$",
                    r"[،,]\s*([^،,]+)$"):
        match = re.search(pattern, flat, re.IGNORECASE)
        if not match:
            continue
        segment = _norm(match.group(1))
        found = _find_city_name(segment) or _lookup_segment(segment)
        if found:
            matched, info = found
            return {"key": info["key"], "label": info.get("label") or matched,
                    "label_local": info.get("fa") or matched, "country": info["country"],
                    "matched": matched, "source": "offline"}

    found = _find_city_name(flat)
    if found:
        matched, info = found
        return {"key": info["key"], "label": info.get("label") or matched,
                "label_local": info.get("fa") or matched, "country": info["country"],
                "matched": matched, "source": "offline"}

    for segment in [part for part in re.split(r"[،,]", flat) if part.strip()]:
        found = _lookup_segment(segment)
        if found:
            matched, info = found
            return {"key": info["key"], "label": info.get("label") or matched,
                    "label_local": info.get("fa") or matched, "country": info["country"],
                    "matched": matched, "source": "offline"}

    if allow_network:
        lat, lon, _bbox, gkey = geo.geocode(flat, with_bbox=True, allow_network=True)
        if lat and gkey:
            return {"key": gkey, "label": flat, "label_local": flat, "country": "",
                    "matched": flat, "source": "nominatim"}
    return dict(_NONE_CITY)


def matched_niches(text: str) -> list[niches.Niche]:
    """صنف‌هایی که نام کاملشان (فارسی/انگلیسی/مستعار) در جملهٔ کاربر آمده است."""
    flat = " " + _norm(text).lower() + " "
    hits: list[tuple[int, niches.Niche]] = []
    for niche in niches.CATALOG:
        names = {niche.fa.lower(), niche.en.lower(), *(a.lower() for a in niche.aliases)}
        length = max((len(n) for n in names if n and n in flat), default=0)
        if length >= 3:
            hits.append((length, niche))
    hits.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    out: list[niches.Niche] = []
    for _, niche in hits:
        if niche.id not in seen:
            seen.add(niche.id)
            out.append(niche)
    return out


def choose_mode(raw: str, remainder: str, groups: list[str]) -> tuple[str, list[niches.Niche], list[str]]:
    """تصمیم «تک‌صنفی یا جاروی گروه» — با قواعد صریح و قابل توضیح.

    خروجی: (mode, named_niches, groups) که mode یکی از empty|single|sweep است.
    """
    named = matched_niches(remainder)
    if not _norm(raw):
        return "empty", named, groups
    if detect_all_trades(raw):
        return "sweep", named, groups
    if len(named) >= 2:                                  # «کافه و رستوران» → کل گروه
        return "sweep", named, groups or sorted({n.group for n in named})
    if named:
        return "single", named, groups
    single = niches.find_niche(remainder) if _norm(remainder) else None
    if groups and not _norm(remainder):                  # فقط نام گروه + شهر
        return "sweep", named, groups
    if single and not single.custom:
        return "single", named, groups
    if groups:
        return "sweep", named, groups
    return "single", named, groups


def _preview_keywords(all_trades: bool, groups: list[str], niche, max_niches: int,
                      keywords_per_niche: int, place: dict, lang: str) -> tuple[list[str], int]:
    """پیش‌نمایش کلیدواژه‌ها (بدون اجرای موتور) + تعداد کل کلیدواژه‌های اجرا."""
    label = place.get("label_local") if lang == "fa" else place.get("label")
    if all_trades:
        plan = niches.sweep_plan(groups, max_niches)
        preview = [kw for n in plan[:6]
                   for kw in niches.expand_keywords(n, label or "", lang=lang,
                                                    limit=1)[:1]]
        return preview, len(plan) * max(1, keywords_per_niche)
    if niche is None:
        return [], 0
    preview = niches.expand_keywords(niche, label or "", lang=lang, limit=6)
    return preview, len(preview)


def _strip_known(text: str, city: dict, groups: list[str]) -> str:
    """شهر، گروه‌ها و حروف اضافه را از جمله برمی‌دارد تا صنف خالص بماند."""
    out = _norm(text)
    for piece in [city.get("matched"), *groups, *(g.split(" و ") for g in groups)]:
        if piece:
            out = re.sub(re.escape(str(piece).strip()), " ", out, flags=re.IGNORECASE)
    for phrase in _ALL_TRADES:
        out = re.sub(re.escape(phrase), " ", out, flags=re.IGNORECASE)
    words = [w for w in out.split() if w.strip(".,،;:!?\"'()") not in _FILLER_FA + _FILLER_EN]
    return " ".join(words).strip(" .,،;:!?")


def goal_of(goal_id: str) -> dict:
    return next((g for g in GOALS if g["id"] == (goal_id or "").lower()), GOALS[0])


def size_of(size_id: str) -> dict:
    return next((s for s in SIZES if s["id"] == (size_id or "").lower()), SIZES[1])


# برآورد زمان: موتور واقعی ~۱۲۰ مکان در دقیقه (c=8, depth=1) و هر depth ≈ ۱۸ ردیف به ازای کلیدواژه
_RESULTS_PER_KEYWORD_PER_DEPTH = 18
_PLACES_PER_MINUTE = 110


def _eta_minutes(depth: int, keyword_count: int, grid: int) -> tuple[int, int]:
    """بازهٔ دقیقه‌های تقریبی بر اساس حجم واقعی کار (کلیدواژه × عمق × کاشی)."""
    tiles = (grid * grid) if grid > 1 else 1
    results = max(keyword_count, 1) * max(depth, 1) * _RESULTS_PER_KEYWORD_PER_DEPTH * tiles
    base = results / _PLACES_PER_MINUTE
    return max(1, round(base * 0.6)), max(2, round(base * 1.7))


def parse(text: str = "", goal: str = "site", size: str = "standard", city: str = "",
          socials: bool = True, emails: bool = True, engine_mode: str = "auto",
          cols: str = "contact", export_fmt: str = "xlsx", allow_network: bool = False,
          lat: str = "", lon: str = "", province: str = "", district: str = "",
          street: str = "", radius: int = 0, niche_id: str = "",
          all_trades: bool | None = None, limit: int = 0,
          area_from_text: bool = True) -> dict:
    """جملهٔ کاربر + انتخاب‌های منطقه‌ای → درخواست آمادهٔ اجرا + خلاصهٔ انسانی.

    سه ورودی هم‌زمان پشتیبانی می‌شود و اولویت با دقیق‌ترین است:
      ۱. انتخاب صریح رابط: `province` / `city` / `district` / `street` / `niche_id` / `all_trades`
      ۲. متن آزاد: «کافه در خیابان ملاصدرا ونک» → صنف + شهر + منطقه + خیابان از خود جمله
      ۳. مختصات دستی `lat`/`lon` (برای نقطه‌ای که در جدول‌ها نیست)

    خروجی کاملاً قابل توضیح است: `detected` (چه فهمیدیم، از جمله `area`)، `summary`،
    `keywords` (پیش‌نمایش)، `eta_minutes`، `warnings` (هشدار بدون توقف)، `ready`/`missing`
    و `request` (بدنهٔ `POST /api/runs`).
    """
    raw = _norm(text)
    is_fa = _persian_ratio(raw) >= 0.3 or not raw
    groups = detect_groups(raw)

    # ── ۱) منطقه/خیابان از داخل جمله (فقط اگر کاربر صریح انتخاب نکرده باشد) ──
    areas_in_text = regions.detect_area(raw) if area_from_text else []
    for hit in areas_in_text:
        if hit["kind"] == "district" and not district:
            district = hit["name"]
        elif hit["kind"] == "street" and not street:
            street = hit["name"]
    area_city_key = next((h["city"] for h in areas_in_text if h.get("city")), "")

    # ── ۲) شهر: انتخاب صریح > نام داخل جمله > شهرِ منطقهٔ پیدا‌شده ───────────
    found_city = detect_city(raw, allow_network=allow_network)
    detected_city = regions.city_key(city) or found_city.get("key") or area_city_key or ""
    city_key = detected_city
    city_fa = regions.city_fa(city_key) if city_key else ""
    city_query = _norm(city) or found_city["label_local"] or city_fa
    if lat and lon:
        city_query = city_query or f"{lat},{lon}"
    if not city_query:
        city_query = "تهران" if is_fa else "Tehran"
    if not city_key:
        city_key = regions.city_key(city_query)      # برای آبشار منطقه/خیابان در رابط
    lang = "fa" if is_fa else "en"

    # ── ۳) استان: انتخاب صریح یا استنباط از شهر ────────────────────────────
    if not province:
        province = regions.CITY_PROVINCE.get(city_key, "")

    # ── ۴) نقطهٔ دقیق + شعاع جست‌وجو بر اساس سطح انتخاب‌شده ─────────────────
    try:
        manual_lat = float(lat) if lat else None
        manual_lon = float(lon) if lon else None
    except (TypeError, ValueError):
        manual_lat = manual_lon = None
    area = regions.resolve(province=province, city=city_query, district=district,
                           street=street, lat=manual_lat, lon=manual_lon, lang=lang)
    if area.get("lat") and not (manual_lat and manual_lon):
        lat, lon = f"{area['lat']:.6f}", f"{area['lon']:.6f}"
    area_level = area.get("level") or ("city" if (lat and lon) else "")
    area_label = area.get("label") or city_query          # کلی → جزئی (نمایش)
    area_keyword = area.get("keyword_label") or city_query  # جزئی → کلی (کلیدواژه)
    resolved_radius = int(radius or area.get("radius") or 0)
    resolved_zoom = int(area.get("zoom") or 15)
    half = max(0.004, resolved_radius * 1.3 / 111000.0) if resolved_radius else 0.0
    area_bbox: list = []
    if half and area.get("lat") and area_level in ("street", "district"):
        area_bbox = [round(area["lat"] - half, 4), round(area["lat"] + half, 4),
                     round(area["lon"] - half, 4), round(area["lon"] + half, 4)]

    # ── ۵) صنف: نام‌های جغرافیایی از جمله حذف می‌شوند تا صنف خالص بماند ────
    query_for_niche = raw
    if found_city["matched"]:
        query_for_niche = re.sub(re.escape(found_city["matched"]), " ", query_for_niche,
                                 flags=re.IGNORECASE)
    for hit in areas_in_text:
        query_for_niche = re.sub(re.escape(hit["name"]), " ", query_for_niche, flags=re.IGNORECASE)
    remainder = _strip_known(query_for_niche, found_city, groups)

    forced_niche = niches.find_niche(niche_id) if _norm(niche_id) else None
    if forced_niche is not None and forced_niche.custom:
        forced_niche = None
    if all_trades:
        mode, named = "sweep", matched_niches(remainder)
        groups = groups or sorted({n.group for n in named})
    elif forced_niche is not None:
        mode, named = "single", [forced_niche]
    elif not raw and not (district or street or city or province):
        mode, named = "empty", []
    else:
        mode, named, groups = choose_mode(raw, remainder, groups)
    all_trades = mode == "sweep"

    niche = None
    if mode == "single":
        niche = named[0] if named else niches.find_niche(remainder or raw)

    goal_cfg, size_cfg = goal_of(goal), size_of(size)
    if lang == "en" and area_keyword:
        # کلیدواژهٔ انگلیسی نباید نام فارسی شهر یا ویرگول عربی داشته باشد
        latin_city = geo.city_label(city_key, city_query) if city_key else city_query
        for persian in (city_fa, city_query):
            if persian and persian in area_keyword:
                area_keyword = area_keyword.replace(persian, latin_city)
        area_keyword = re.sub(r"\s*،\s*", ", ", area_keyword).strip(", ")

    request: dict = {
        "city_query": area_label, "lat": lat, "lon": lon, "lang": lang,
        "profile": goal_cfg["profile"], "socials": bool(socials), "emails": bool(emails),
        "deep_emails": bool(emails), "signals": True, "engine_mode": engine_mode,
        "export_fmt": export_fmt, "cols": cols,
        "depth": size_cfg["depth"], "grid": size_cfg["grid"], "limit": size_cfg["limit"],
        "max_time": size_cfg["max_time"], "fast_mode": size_cfg["id"] == "quick",
        # سطح منطقه‌ای — پایپ‌لاین از همین عدد‌ها نقطه و شعاع را برمی‌دارد
        "province": province, "district": district, "street": street,
        "area_label": area_keyword, "area_level": area_level, "bbox": area_bbox,
        "area_applied": True,
    }
    if resolved_radius:
        request["radius"] = resolved_radius
        request["zoom"] = resolved_zoom
    if area_level in ("street", "district"):
        request["grid"] = 0                     # شعاع کوچک است؛ کاشی‌بندی لازم نیست
    if limit:
        request["limit"] = int(limit)
    if all_trades:
        request.update({"all_niches": True, "niche_groups": groups,
                        "max_niches": size_cfg["max_niches"],
                        "keywords_per_niche": size_cfg["keywords_per_niche"],
                        "keywords_per_job": 8,
                        # در جارو، کاشی‌بندی در تعداد صنف‌ها ضرب می‌شد → خاموش
                        "grid": 0})
    elif niche is not None:
        request["niche_query"] = remainder or raw or niche.fa
    else:
        request["niche_query"] = ""

    # پیش‌نمایش کلیدواژه‌ها و زمان تقریبی — با همان توابع پایپ‌لاین، بدون اجرای موتور
    try:
        place = pipeline.resolve_city(city_query, lat, lon, allow_network=allow_network)
        place = dict(place or {})
        if lang == "fa":
            place["label_local"] = area_keyword
        elif place.get("country") != "IR":
            place["label"] = area_keyword
        keywords, keyword_count = _preview_keywords(
            all_trades, groups, niche, request.get("max_niches", 6),
            request.get("keywords_per_niche", 2), place, lang)
        place_label = area_label
        country = place.get("country") or found_city["country"]
        city_key = city_key or place.get("key") or found_city["key"]
        if all_trades:
            plan_size = max(1, keyword_count // max(1, request.get("keywords_per_niche", 1)))
            niche_fa, niche_id_out = f"همهٔ مشاغل ({plan_size} صنف)", "all-trades"
        else:
            niche_fa, niche_id_out = (niche.fa if niche else ""), (niche.id if niche else "")
    except Exception:                                  # پیش‌نمایش هرگز نباید جلوی اجرا را بگیرد
        keywords, keyword_count = [], 0
        place_label, city_key, country = area_label, city_key, found_city["country"]
        niche_fa = "همهٔ مشاغل" if all_trades else (niche.fa if niche else "")
        niche_id_out = "all-trades" if all_trades else (niche.id if niche else "")

    eta = _eta_minutes(request["depth"], max(1, keyword_count), request["grid"])

    # ── خلاصهٔ انسانی ──────────────────────────────────────────────────────
    ready = mode != "empty" and bool(request.get("niche_query") or all_trades)
    scope = f" (شعاع {resolved_radius:,} متر)".replace(",", "٬") if resolved_radius else ""
    if not ready:
        summary = ("چه شغلی را در کدام منطقه می‌خواهید؟ مثال: «دندانپزشکی در ونک تهران» یا "
                   "استان/شهر/منطقه را از فهرست‌ها انتخاب کنید و «همهٔ کسب‌وکارها» را بزنید.")
    else:
        limit_txt = f"{request['limit']} لید" if request["limit"] else "هرچه پیدا شود"
        summary = (f"{limit_txt} «{niche_fa}» در {place_label}{scope} برای هدف "
                   f"«{goal_cfg['fa']}» — حالت {size_cfg['fa']}، {keyword_count} کلیدواژه، "
                   f"حدود {eta[0]}–{eta[1]} دقیقه")

    warnings: list[str] = []
    if not ready:
        warnings.append("صنف (یا «همهٔ کسب‌وکارها») را انتخاب کنید تا اجرا شروع شود.")
    if not detected_city and not (manual_lat and manual_lon):
        warnings.append(f"شهر از جمله تشخیص داده نشد؛ «{place_label}» در نظر گرفته شد. "
                        "می‌توانید نام شهر را بنویسید یا از فهرست انتخاب کنید.")
    if niche and niche.custom and not all_trades:
        warnings.append(f"«{niche.fa}» در کاتالوگ ۹۱ صنفی نبود؛ به‌عنوان صنف سفارشی جست‌وجو می‌شود.")
    if area_level == "street":
        warnings.append(f"جست‌وجو در سطح خیابان است ({resolved_radius} متر). اگر نتیجه کم بود، "
                        "خیابان را خالی بگذارید تا کل منطقه جست‌وجو شود.")
    elif area_level == "province":
        warnings.append("سطح استان بسیار وسیع است؛ بهتر است شهر را هم انتخاب کنید تا نتیجه "
                        "دقیق‌تر و سریع‌تر شود.")
    elif district and not area.get("lat"):
        warnings.append(f"منطقهٔ «{district}» در جدول مختصات نبود؛ مرکز شهر مبنا قرار گرفت.")
    if size_cfg["id"] == "deep":
        warnings.append("حالت «کامل» سنگین است (کاشی‌بندی شهر + عمق بالا). بدون پراکسی ممکن است IP شما "
                        "موقتاً نزد گوگل محدود شود؛ اجرا متوقف نمی‌شود ولی هشدارش را جدی بگیرید.")
    if all_trades and groups:
        warnings.append(f"جارو به گروه‌های «{'، '.join(groups)}» محدود شد.")

    return {
        "query": raw,
        "mode": mode,
        "ready": ready,
        "missing": [] if ready else ["niche"],
        "detected": {
            "niche_id": niche_id_out, "niche_fa": niche_fa,
            "custom_niche": bool(niche and niche.custom),
            "group": niche.group if niche else (groups[0] if groups else ""),
            "named_niches": [n.fa for n in named],
            "city": place_label, "city_key": city_key, "country": country,
            "all_trades": all_trades, "groups": groups, "lang": lang,
            "goal": goal_cfg, "size": size_cfg,
            "keyword_count": keyword_count,
            "area": {
                "province": province,
                "province_fa": regions.PROVINCES[province]["fa"] if province in regions.PROVINCES else "",
                "city": city_query, "city_key": city_key, "city_fa": city_fa,
                "district": district, "street": street,
                "label": area_label, "keyword_label": area_keyword,
                "level": area_level, "level_fa": regions.LEVEL_FA.get(area_level, ""),
                "lat": area.get("lat"), "lon": area.get("lon"),
                "radius": resolved_radius, "zoom": resolved_zoom, "bbox": area_bbox,
                "districts": len(regions.DISTRICTS.get(city_key, [])),
                "streets": len(regions.STREETS.get(city_key, [])),
                "in_text": [h["name"] for h in areas_in_text],
            },
        },
        "keywords": keywords,
        "eta_minutes": list(eta),
        "summary": summary,
        "warnings": warnings,
        "request": request,
        "needs_confirmation": bool(not detected_city and not (manual_lat and manual_lon)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ۳) پیشنهاد زندهٔ تایپ (اصناف + شهرها)
# ─────────────────────────────────────────────────────────────────────────────
def _score_match(query: str, haystacks: list[str], allow_fuzzy: float = 0.0) -> float:
    """شباهت پرس‌وجو با فهرستی از رشته‌ها.

    «allow_fuzzy» کفِ امتیازِ مطابقت تقریبی است؛ صفر یعنی بدون محدودیت (برای تجزیهٔ جمله
    که باید غلط تایپی را هم تحمل کند). در تکمیل خودکار، مقدار بالاتر نویز را حذف می‌کند.
    """
    query = query.strip().lower()
    if not query:
        return 0.0
    best = 0.0
    for hay in haystacks:
        hay = (hay or "").strip().lower()
        if not hay:
            continue
        if hay == query:
            return 1.0
        if hay.startswith(query):
            best = max(best, 0.92)
        elif query in hay:
            best = max(best, 0.78)
        else:
            fuzzy = SequenceMatcher(None, query, hay).ratio() * 0.75
            if allow_fuzzy and fuzzy < allow_fuzzy:
                continue
            best = max(best, fuzzy)
    return best


def suggest(query: str = "", limit: int = 6) -> dict:
    """پیشنهاد صنف و شهر برای تکمیل خودکار (هم فارسی هم انگلیسی)."""
    q = _norm(query)
    niche_hits, city_hits = [], []

    if q:
        for niche in niches.CATALOG:
            score = _score_match(q, [niche.fa, niche.en, *niche.aliases, *niche.keywords_fa[:3]],
                                 allow_fuzzy=0.62)
            if score >= 0.42:
                niche_hits.append((score, {"id": niche.id, "fa": niche.fa, "en": niche.en,
                                           "group": niche.group, "hint": niche.hooks[0] if niche.hooks else ""}))
        for city in geo.known_cities():
            score = _score_match(q, [city["fa"], city["label"], *city.get("aliases", [])],
                                 allow_fuzzy=0.62)
            if score >= 0.62:
                city_hits.append((score, {"id": city["id"], "fa": city["fa"], "label": city["label"],
                                          "country": city["country"]}))
    niche_hits.sort(key=lambda x: (-x[0], x[1]["fa"]))
    city_hits.sort(key=lambda x: (-x[0], x[1]["fa"]))

    return {
        "query": q,
        "niches": [item for _, item in niche_hits[:limit]],
        "cities": [item for _, item in city_hits[:limit]],
        "groups": [g for g in niches.GROUPS if q and (q in g or _score_match(q, [g]) >= 0.6)][:4],
        "all_trades": detect_all_trades(q),
    }


def options() -> dict:
    """هرچه رابط ساده برای نمایش نیاز دارد، یک‌جا."""
    return {
        "goals": GOALS,
        "sizes": [{k: v for k, v in s.items()} for s in SIZES],
        "examples": EXAMPLES,
        "groups": niches.GROUPS,
        "presets": [{"id": key, "fa": cfg["fa"], "note": cfg["note"], "columns": len(cfg["columns"])}
                    for key, cfg in _column_presets().items()],
        "formats": [{"id": "xlsx", "fa": "اکسل (پیشنهادی)"}, {"id": "csv", "fa": "CSV"},
                    {"id": "json", "fa": "JSON"}],
        "profiles": scoring.profile_list(),
        "statuses": None,
    }


def _column_presets() -> dict:
    from . import exports                       # import تنبل تا وابستگی دایره‌ای نشود
    return exports.COLUMN_PRESETS
