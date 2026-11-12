#!/usr/bin/env python3
"""ساخت `leadgen/data/iran_cities.json` — همهٔ شهرهای ایران با مختصات و نام فارسی.

منبع: بستهٔ npm «@geomelon/iran» (۵۰٬۰۰۰ سکونتگاه ایران، برگرفته از Wikidata،
مجوز CC0-1.0). فقط ردیف‌هایی نگه داشته می‌شوند که Wikidata آن‌ها را «شهر/شهرک»
می‌داند (نه روستا، محله یا حومه) — حدود ۱٬۳۰۰ شهر.

اجرا (یک‌بار، برای بازسازی داده):

    npm pack @geomelon/iran            # → geomelon-iran-1.0.0.tgz
    tar xzf geomelon-iran-1.0.0.tgz -C /tmp/geomelon
    python3 scripts/build_iran_cities.py --source /tmp/geomelon/package/data.json

خروجی کوچک و پایدار است (کلیدهای یک‌حرفی) تا در مخزن بماند و اپ بدون شبکه
همهٔ شهرها را بشناسد.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "leadgen" / "data" / "iran_cities.json"

# Wikidata برای این انواع، «شهر» را می‌سازد؛ بقیه (village/neighborhood/suburb) نه.
CITY_TYPES = {"city of Iran", "big city", "city", "border city", "town", "city or town"}

# نگاشت نام انگلیسی استان dataset → کلید داخلی اپ
PROVINCE_MAP = {
    "tehran": "tehran-province",
    "alborz": "alborz",
    "qom": "qom",
    "markazi": "markazi",
    "qazvin": "qazvin",
    "zanjan": "zanjan",
    "east azerbaijan": "east-azarbaijan",
    "west azerbaijan": "west-azarbaijan",
    "ardabil": "ardabil",
    "kurdistan": "kurdistan",
    "kermanshah": "kermanshah",
    "ilam": "ilam",
    "hamadan": "hamadan",
    "lorestan": "lorestan",
    "khuzestan": "khuzestan",
    "kohgiluyeh and boyer-ahmad": "kohgiluyeh",
    "chaharmahal and bakhtiari": "chaharmahal",
    "isfahan": "isfahan",
    "fars": "fars",
    "bushehr": "bushehr",
    "hormozgan": "hormozgan",
    "kerman": "kerman",
    "yazd": "yazd",
    "razavi khorasan": "razavi-khorasan",
    "north khorasan": "north-khorasan",
    "south khorasan": "south-khorasan",
    "sistan and baluchestan": "sistan",
    "semnan": "semnan",
    "golestan": "golestan",
    "mazandaran": "mazandaran",
    "gilan": "gilan",
}

# مرکز تقریبی استان‌ها — برای انتساب ردیف‌های بدون استان
PROVINCE_CENTER = {
    "tehran-province": (35.69, 51.39), "alborz": (35.84, 50.94), "qom": (34.64, 50.87),
    "markazi": (34.09, 49.69), "qazvin": (36.27, 50.00), "zanjan": (36.68, 48.51),
    "east-azarbaijan": (38.08, 46.29), "west-azarbaijan": (37.55, 45.07),
    "ardabil": (38.25, 48.29), "kurdistan": (35.31, 46.99), "kermanshah": (34.31, 47.07),
    "ilam": (33.63, 46.43), "hamadan": (34.80, 48.51), "lorestan": (33.49, 48.36),
    "khuzestan": (31.32, 48.67), "kohgiluyeh": (30.67, 51.59), "chaharmahal": (32.33, 50.86),
    "isfahan": (32.65, 51.67), "fars": (29.59, 52.58), "bushehr": (28.97, 50.84),
    "hormozgan": (27.18, 56.27), "kerman": (30.28, 57.08), "yazd": (31.90, 54.36),
    "razavi-khorasan": (36.30, 59.61), "north-khorasan": (37.47, 57.33),
    "south-khorasan": (32.86, 59.22), "sistan": (29.50, 60.86), "semnan": (35.57, 53.40),
    "golestan": (36.84, 54.43), "mazandaran": (36.56, 53.06), "gilan": (37.28, 49.58),
}

# کلیدهای دستی موجود در geo.py که باید حفظ شوند (نام انگلیسی → کلید داخلی)
# ردیف‌هایی که ویکی‌داده نام فارسی ندارد (یا انگلیسی است) — دستی تکمیل شده
FA_OVERRIDES: dict[str, str] = {
    "mehdiyeh": "مهدیه", "guyim": "گوییم", "hajiabad": "حاجی‌آباد",
    "asgharabad": "اصغرآباد", "shahrak e shahid pur mohammadi": "شهرک شهید پورمحمدی",
    "mehrian e olya": "مهرین علیا",
    "naysar": "نیسر", "choqab dar": "چقاب‌در",
}

KEEP_KEYS = {
    "qods": "qods-city", "khomeyni shahr": "khomeynishahr", "khomeyni-shahr": "khomeynishahr",
    "bandar anzali": "bandar-anzali", "bandar-abbas": "bandar-abbas",
    "bandar abbas": "bandar-abbas", "masjed soleyman": "masjed-soleiman",
    "masjed-soleiman": "masjed-soleiman", "torbat-e heydarieh": "torbat-heydarieh",
    "torbat heydarieh": "torbat-heydarieh", "torbat-e jam": "torbat-jam",
    "torbat jam": "torbat-jam", "gonbad-e kavus": "gonbad-kavous",
    "gonbad kavus": "gonbad-kavous", "qaim shahr": "qaimshahr", "qaimshahr": "qaimshahr",
    "shahr-e kord": "shahrekord", "shahr kord": "shahrekord", "shahrekord": "shahrekord",
    "neyshabur": "neyshabur", "neishabur": "neyshabur", "nishapur": "neyshabur",
    "ghouchan": "ghouchan", "quchan": "ghouchan", "quchan": "ghouchan",
    "ghaen": "ghaen", "qayen": "ghaen", "qaen": "ghaen",
    "dogonbadan": "dogonbadan", "bandar-e mahshahr": "mahshahr", "mahshahr": "mahshahr",
    "bandar mahshahr": "mahshahr", "bandar-e ganaveh": "ganaveh", "ganaveh": "ganaveh",
    "bandar ganaveh": "ganaveh", "bandar-imam khomeini": "bandar-imam",
    "bandar imam khomeini": "bandar-imam", "bandar-e emam khomeyni": "bandar-imam",
    "eslamshahr": "eslamshahr", "islamshahr": "eslamshahr",
    "veramin": "veramin", "varamin": "veramin", "pakdasht": "pakdasht",
    "shahriyar": "shahriar", "shahriar": "shahriar", "parand": "parand", "pardis": "pardis",
    "fardis": "fardis", "nazarabad": "nazarabad", "hashtgerd": "hashtgerd",
    "damavand": "damavand", "rey": "rey", "ray": "rey", "shahr-e rey": "rey",
    "kish": "kish", "qeshm": "qeshm", "asaluyeh": "asaluyeh", "asaluyeh port": "asaluyeh",
    "sirjan": "sirjan", "rafsanjan": "rafsanjan", "jiroft": "jiroft", "bam": "bam",
    "borujerd": "borujerd", "boroujerd": "borujerd", "malayer": "malayer", "saveh": "saveh",
    "shahroud": "shahroud", "shahrud": "shahroud", "damghan": "damghan", "tabas": "tabas",
    "ferdows": "ferdows", "dehloran": "dehloran", "abdanan": "abdanan", "mehran": "mehran",
    "dehdasht": "dehdasht", "borazjan": "borazjan", "kangan": "kangan", "bastak": "bastak",
    "lordegan": "lordegan", "borujen": "borujen", "daran": "daran", "meibod": "meibod",
    "ardakan": "ardakan", "baft": "baft", "marvdasht": "marvdasht", "jahrom": "jahrom",
    "fasa": "fasa", "kazeroon": "kazeroon", "lar": "lar", "minab": "minab", "saqqez": "saqqez",
    "marivan": "marivan", "baneh": "baneh", "miandoab": "miandoab", "salmas": "salmas",
    "parsabad": "parsabad", "meshginshahr": "meshginshahr", "abhar": "abhar",
    "takestan": "takestan", "shirvan": "shirvan", "esfarayen": "esfarayen",
    "khorramshahr": "khorramshahr", "dezful": "dezful", "abadan": "abadan",
    "lahijan": "lahijan", "amol": "amol", "sari": "sari", "babol": "babol",
    "qazvin": "qazvin", "kashan": "kashan", "najafabad": "najafabad",
}


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("ي", "ی").replace("ك", "ک").replace("آ", "ا").replace("أ", "ا")
    text = re.sub(r"[^0-9a-zA-Z\u0600-\u06FF]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^0-9a-zA-Z]+", "-", text).strip("-").lower()
    return text or "city"


def province_key(region: dict) -> str:
    en = (region.get("translations") or {}).get("en") or region.get("name") or ""
    en = re.sub(r"\s+province$", "", en.strip(), flags=re.I)
    return PROVINCE_MAP.get(_norm(en), "")


def nearest_province(lat: float, lon: float) -> str:
    def distance(center: tuple[float, float]) -> float:
        dlat = (lat - center[0]) * 111
        dlon = (lon - center[1]) * 111 * math.cos(math.radians(lat))
        return math.hypot(dlat, dlon)
    return min(PROVINCE_CENTER, key=lambda key: distance(PROVINCE_CENTER[key]))


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat1 - lat2) * 111.0
    dlon = (lon1 - lon2) * 111.0 * math.cos(math.radians(lat1))
    return math.hypot(dlat, dlon)


def _curated_reference() -> dict[str, dict]:
    """جدول دستی موجود در geo.py — منبع «حقیقت» برای کلید و مختصات شهرهای مهم."""
    import sys

    sys.path.insert(0, str(ROOT))
    from leadgen import geo  # noqa: PLC0415 — فقط برای همین اسکریپت

    reference = {}
    for key, (lat, lon, country, _bbox) in geo.CURATED_CITIES.items():
        if country != "IR":
            continue
        fa = next((a for a in geo.CURATED_ALIASES.get(key, [])
                   if any("\u0600" <= ch <= "\u06FF" for ch in a)), "")
        reference[key] = {"key": key, "fa": fa, "lat": float(lat), "lon": float(lon)}
    return reference


def build(source: Path) -> dict:
    raw = json.loads(source.read_text(encoding="utf-8"))
    regions = {r["id"]: r for r in raw.get("regions", [])}
    curated = _curated_reference()
    by_fa = {_norm(c["fa"]): c["key"] for c in curated.values() if c["fa"]}

    def match_curated(fa: str, en: str, lat: float, lon: float) -> str:
        """کلید دستی همان شهر — فقط اگر مختصات نزدیک باشد (هم‌نام در استان دیگر نه)."""
        for candidate in (by_fa.get(_norm(fa)), KEEP_KEYS.get(_norm(en)), slug(en)):
            ref = curated.get(candidate or "")
            if ref and _km(lat, lon, ref["lat"], ref["lon"]) <= 25.0:
                return ref["key"]
        return ""

    picked: dict[str, dict] = {}
    dropped = 0
    rows = []
    for item in raw.get("cities", []):
        types = set(item.get("settlementTypes") or [])
        if not (types & CITY_TYPES):
            continue
        lat, lon = item.get("latitude"), item.get("longitude")
        if lat is None or lon is None:
            dropped += 1
            continue
        lat, lon = float(lat), float(lon)
        if not (24.0 <= lat <= 40.5 and 43.0 <= lon <= 64.5):
            dropped += 1
            continue
        wikidata = item.get("wikidataId") or item.get("id")
        en = ((item.get("translations") or {}).get("en") or item.get("name") or "").strip()
        fa = ((item.get("translations") or {}).get("fa") or "").strip()
        if not any("\u0600" <= ch <= "\u06FF" for ch in fa):
            fa = FA_OVERRIDES.get(_norm(en), fa)
        if not en:
            dropped += 1
            continue
        rows.append({"wd": wikidata, "item": item, "lat": lat, "lon": lon, "en": en, "fa": fa})

    # شهرهای بزرگ‌تر اول: کلید سادهٔ انگلیسی را همان شهر معروف می‌گیرد
    rows.sort(key=lambda r: -int(r["item"].get("population") or 0))
    for row in rows:
        lat, lon, en, fa = row["lat"], row["lon"], row["en"], row["fa"]
        region = regions.get(row["item"].get("regionId") or "", {})
        province = province_key(region) or nearest_province(lat, lon)
        key = match_curated(fa, en, lat, lon)
        if key and key in picked:
            key = ""
        if not key:
            base = KEEP_KEYS.get(_norm(en)) or slug(en)
            key = base
            if key in curated or key in picked:      # نام تکراری در جای دیگر
                key = f"{base}-{province}"
            suffix = 2
            while key in picked or (key in curated and not match_curated(fa, en, lat, lon)):
                key = f"{base}-{province}-{suffix}"
                suffix += 1
        ref = curated.get(key)
        picked[key] = {
            "k": key,
            "fa": (ref["fa"] if ref else "") or fa or en,
            "en": en,
            "p": province,
            "lat": round(ref["lat"] if ref else lat, 5),
            "lon": round(ref["lon"] if ref else lon, 5),
            "pop": int(row["item"].get("population") or 0),
        }

    # ردیف‌های منبع با نام فارسی خراب/نوشتار متفاوت (مثل «خدا» برای قائم‌شهر)
    # را با نزدیک‌ترین ردیف بی‌صاحب همان محدوده به کلید دستی تبدیل می‌کنیم.
    for key, ref in curated.items():
        if key in picked:
            continue
        near = [(kid, c) for kid, c in picked.items()
                if _km(ref["lat"], ref["lon"], c["lat"], c["lon"]) <= 25.0]
        if not near:
            continue
        kid, city = min(near, key=lambda kv: _km(ref["lat"], ref["lon"], kv[1]["lat"], kv[1]["lon"]))
        if kid in curated and _km(ref["lat"], ref["lon"],
                                  curated[kid]["lat"], curated[kid]["lon"]) <= 5.0:
            continue                            # این ردیف مال شهر دستی دیگری است
        renamed = dict(city, k=key, fa=ref["fa"] or city["fa"],
                       lat=ref["lat"], lon=ref["lon"], pop=max(int(city["pop"]), 1))
        del picked[kid]
        picked[key] = renamed
        if kid in curated:                     # کلید دستی دیگری اشغال شده — برگردان
            del picked[key]

    missing = [k for k in curated if k not in picked]

    cities = sorted(picked.values(), key=lambda c: (c["p"], -c["pop"], c["fa"]))
    if missing:
        print(f"  ⚠ شهرهای دستی بدون ردیف در منبع ({len(missing)}): {', '.join(sorted(missing)[:12])}")
    return {
        "meta": {
            "source": "@geomelon/iran (Wikidata)",
            "license": "CC0-1.0",
            "generated": _dt.date.today().isoformat(),
            "count": len(cities),
            "provinces": len({c["p"] for c in cities if c["p"]}),
            "dropped": dropped,
            "curated_missing": sorted(missing),
        },
        "cities": cities,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="مسیر data.json از بستهٔ @geomelon/iran")
    args = parser.parse_args()
    payload = build(args.source)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    per_province: dict[str, int] = {}
    for city in payload["cities"]:
        per_province[city["p"]] = per_province.get(city["p"], 0) + 1
    print(f"✓ {OUT.relative_to(ROOT)} — {payload['meta']['count']} شهر، "
          f"{len(per_province)} استان، {OUT.stat().st_size / 1024:.0f} کیلوبایت")
    print("  کم‌ترین/بیش‌ترین شهر در استان:",
          min(per_province.items(), key=lambda kv: kv[1]),
          max(per_province.items(), key=lambda kv: kv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
