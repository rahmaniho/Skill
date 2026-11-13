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


def _center(box: list[float]) -> tuple[float, float]:
    return ((float(box[0]) + float(box[1])) / 2.0, (float(box[2]) + float(box[3])) / 2.0)


def cluster_boxes(cities: list[dict], k: int) -> list[dict]:
    """شهرهای یک استان را در k خوشهٔ جغرافیایی می‌ریزد تا «همهٔ» شهرها پوشش بیابند.

    چرا؟ بعضی استان‌ها بیش از سقف کاشی شهر دارند (اصفهان ۱۱۱ شهر، فارس ۱۰۸) و
    «۴۰ شهر پرجمعیت» یعنی بقیه بی‌صدا جا می‌مانند. این‌جا شهرهای همسایه در یک
    جعبه جمع می‌شوند؛ هم تعداد جعبه‌ها زیر سقف می‌ماند، هم هیچ شهری حذف نمی‌شود.

    الگوریتم: k-means ساده و **قطعی** (بذرها بزرگ‌ترین شهرها، ۵ تکرار)، پس
    اجرای دوباره/ادامهٔ اسکراب همان برچسب‌ها را می‌دهد.
    """
    k = max(1, int(k))
    if len(cities) <= k:
        return [{"label": c["fa"], "bbox": [float(x) for x in c["bbox"]],
                 "kind": "city", "pop": int(c.get("pop") or 0)} for c in cities]
    ordered = sorted(cities, key=lambda c: (-int(c.get("pop") or 0), str(c.get("fa") or "")))
    seeds = ordered[:k]
    centers = [_center(c["bbox"]) for c in seeds]
    groups: list[list[dict]] = [[] for _ in range(k)]
    for _ in range(5):
        groups = [[] for _ in range(k)]
        for city in cities:
            lat, lon = _center(city["bbox"])
            best = min(range(k), key=lambda i: (lat - centers[i][0]) ** 2
                       + (lon - centers[i][1]) ** 2)
            groups[best].append(city)
        for i, group in enumerate(groups):
            if group:
                centers[i] = (sum(_center(c["bbox"])[0] for c in group) / len(group),
                              sum(_center(c["bbox"])[1] for c in group) / len(group))
    out: list[dict] = []
    for group in groups:
        if not group:
            continue
        group.sort(key=lambda c: (-int(c.get("pop") or 0), str(c.get("fa") or "")))
        boxes = [[float(x) for x in c["bbox"]] for c in group]
        s, n = min(b[0] for b in boxes), max(b[1] for b in boxes)
        w, e = min(b[2] for b in boxes), max(b[3] for b in boxes)
        pad_lat, pad_lon = (n - s) * 0.02 + 0.002, (e - w) * 0.02 + 0.002
        main = group[0]["fa"]
        label = main if len(group) == 1 else f"{main} و {len(group) - 1} شهر"
        out.append({"label": label,
                    "bbox": [round(s - pad_lat, 5), round(n + pad_lat, 5),
                             round(w - pad_lon, 5), round(e + pad_lon, 5)],
                    "kind": "cluster", "pop": sum(int(c.get("pop") or 0) for c in group),
                    "cities": [c["fa"] for c in group]})
    out.sort(key=lambda item: -item["pop"])
    return out


def plan(level: str, *, bbox: list[float] | None = None, city_fa: str = "",
         province: str = "", max_tiles: int = MAX_TILES) -> list[dict]:
    """برنامهٔ کاشی‌های یک اجرا برای سطح خواسته‌شده."""
    if level == "province" and province:
        cities = regions.province_boxes(province, limit=0)          # همهٔ شهرها
        if not cities:
            return []
        limit = max(1, int(max_tiles))
        if len(cities) > limit:
            # همهٔ شهرها می‌مانند؛ فقط در جعبه‌های خوشه‌ای جمع می‌شوند.
            return cluster_boxes(cities, limit)
        out = [{"label": c["fa"], "bbox": [float(x) for x in c["bbox"]],
                "kind": "city", "pop": int(c.get("pop") or 0)} for c in cities]
        out.sort(key=lambda item: -item["pop"])
        return out[:limit]
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
