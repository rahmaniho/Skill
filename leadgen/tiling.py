"""کاشی‌بندی یک منطقه برای اسکراب کامل — «هیچ داده‌ای جا نماند».

چرا کاشی؟ هر جست‌وجوی Nominatim حداکثر ۴۰ نتیجه می‌دهد و Overpass هم برای
محدوده‌های بزرگ سقف دارد. اگر کل شهر را یک‌جا بپرسیم، در شهرهای شلوغ فقط
«۴۰ نتیجهٔ اول» را می‌گیریم و بقیه بی‌صدا جا می‌ماند.

پس محدوده به کاشی‌های کوچک شکسته می‌شود و هر کاشی جدا پرسیده می‌شود:
  • خیابان/منطقه → یک کاشی (خودِ محدوده)
  • شهر         → شبکهٔ n×n بر اساس اندازهٔ شهر (۲ تا ۴)
  • استان        → همهٔ شهرهای استان (نه فقط ۵ شهر پرجمعیت)

خروجی هر تابع، فهرستی از `{"label", "bbox", "kind", "pop"}` است؛ همان شکلی که
موتور OSM به‌عنوان `sweep_boxes` می‌شناسد (پس یک مسیر کد برای هر سه سطح).
"""
from __future__ import annotations

from . import regions

MAX_TILES = 24          # سقف کاشی‌های یک اجرا (کنترل هزینهٔ زمانی)
STREET_PAD = 0.02       # ۲٪ حاشیه دور جعبهٔ خیابان


def _span(bbox: list[float]) -> float:
    return max(abs(float(bbox[1]) - float(bbox[0])), abs(float(bbox[3]) - float(bbox[2])))


def grid_for(bbox: list[float]) -> int:
    """اندازهٔ شبکه بر اساس پهنای محدوده (درجه)."""
    span = _span(bbox)
    if span < 0.12:
        return 1
    if span < 0.30:
        return 2
    if span < 0.65:
        return 3
    return 4


def tiles_of(bbox: list[float], *, label: str, kind: str = "tile",
             max_tiles: int = MAX_TILES) -> list[dict]:
    """یک جعبه → شبکه‌ای از کاشی‌های هم‌پوشانِ کاملِ جعبه."""
    s, n, w, e = (float(x) for x in bbox[:4])
    k = max(1, min(4, grid_for([s, n, w, e])))
    while k * k > max_tiles and k > 1:
        k -= 1
    dlat = (n - s) / k
    dlon = (e - w) / k
    out: list[dict] = []
    for i in range(k):
        for j in range(k):
            box = [round(s + i * dlat, 5), round(s + (i + 1) * dlat, 5),
                   round(w + j * dlon, 5), round(w + (j + 1) * dlon, 5)]
            out.append({"label": label if k == 1 else f"{label} · کاشی {i * k + j + 1}/{k * k}",
                        "bbox": box, "kind": kind, "pop": 0})
    return out


def plan(level: str, *, bbox: list[float] | None = None, city_fa: str = "",
         province: str = "", max_tiles: int = MAX_TILES) -> list[dict]:
    """برنامهٔ کاشی‌های یک اجرا برای سطح خواسته‌شده."""
    if level == "province" and province:
        cities = regions.province_boxes(province, limit=0)          # همهٔ شهرها
        if not cities:
            return []
        out = [{"label": c["fa"], "bbox": [float(x) for x in c["bbox"]],
                "kind": "city", "pop": int(c.get("pop") or 0)} for c in cities]
        out.sort(key=lambda item: -item["pop"])
        return out[:max(1, int(max_tiles))]
    if not bbox or len(bbox) != 4:
        return []
    if level in ("street", "district"):
        s, n, w, e = (float(x) for x in bbox)
        dl, dn = (n - s) * STREET_PAD, (e - w) * STREET_PAD
        return [{"label": city_fa or "محدوده", "kind": level, "pop": 0,
                 "bbox": [round(s - dl, 5), round(n + dn, 5),
                          round(w - dl, 5), round(e + dn, 5)]}]
    return tiles_of([float(x) for x in bbox], label=city_fa or "شهر",
                    kind="tile", max_tiles=max_tiles)
