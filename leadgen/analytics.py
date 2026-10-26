"""تحلیل یک اجرا (Run) برای داشبورد: قیف، توزیع‌ها، پوشش تماس، نقشه و بینش‌ها.

همه محاسبات محلی و قطعی هستند — هیچ سرویس بیرونی صدا زده نمی‌شود.
"""
from __future__ import annotations

import statistics
from collections import Counter

from .scoring import GAP_FA


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _num(value, default=0.0):
    """مقدارهای None/خالی از پایگاه‌داده را به عدد تبدیل می‌کند (محاسبه‌ها نمی‌شکنند)."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mean(values, default=0.0):
    numbers = [_num(v) for v in values]
    return round(statistics.fmean(numbers), 1) if numbers else default


def coverage(rows: list[dict]) -> dict:
    """پوشش فیلدهای تماس — اولین چیزی که یک تیم فروش می‌پرسد."""
    total = len(rows)
    return {
        "total": total,
        "website": sum(1 for r in rows if r.get("has_website")),
        "email": sum(1 for r in rows if r.get("has_email")),
        "phone": sum(1 for r in rows if r.get("has_phone")),
        "instagram": sum(1 for r in rows if r.get("instagram")),
        "facebook": sum(1 for r in rows if r.get("facebook")),
        "linkedin": sum(1 for r in rows if r.get("linkedin")),
        "any_social": sum(1 for r in rows if r.get("has_social")),
        "reachable": sum(1 for r in rows if r.get("has_email") or r.get("has_phone") or r.get("has_social")),
        "avg_completeness": _mean([r.get("completeness") for r in rows]),
    }


def coverage_percent(cov: dict) -> dict:
    total = cov["total"] or 1
    return {key: _pct(value, total) for key, value in cov.items() if key not in ("total", "avg_completeness")}


def segments(rows: list[dict]) -> dict:
    counts = Counter(r.get("segment", "cold") for r in rows)
    out = {}
    for key in ("hot", "warm", "cold"):
        out[key] = {
            "count": counts.get(key, 0),
            "percent": _pct(counts.get(key, 0), len(rows)),
            "avg_score": _mean([r.get("score") for r in rows if r.get("segment") == key]),
        }
    return out


def score_distribution(rows: list[dict], buckets: int = 10) -> list[dict]:
    out = []
    for i in range(buckets):
        lo, hi = i * (100 // buckets), (i + 1) * (100 // buckets)
        count = sum(1 for r in rows if lo <= int(_num(r.get("score"))) <= hi)
        out.append({"range": f"{lo}-{hi}", "count": count})
    return out


def rating_distribution(rows: list[dict]) -> list[dict]:
    labels = ["۵.۰-۴.۵", "۴.۴-۴.۰", "۳.۹-۳.۵", "۳.۴-۳.۰", "زیر ۳.۰", "بدون امتیاز"]
    buckets = [0, 0, 0, 0, 0, 0]
    for r in rows:
        rating = _num(r.get("rating"))
        if not rating:
            buckets[5] += 1
        elif rating >= 4.5:
            buckets[0] += 1
        elif rating >= 4.0:
            buckets[1] += 1
        elif rating >= 3.5:
            buckets[2] += 1
        elif rating >= 3.0:
            buckets[3] += 1
        else:
            buckets[4] += 1
    return [{"label": label, "count": count} for label, count in zip(labels, buckets)]


def gaps_breakdown(rows: list[dict]) -> list[dict]:
    counts = Counter(gap for r in rows for gap in (r.get("gaps") or []))
    return [{"key": key, "fa": GAP_FA.get(key, key), "count": count,
             "percent": _pct(count, len(rows))}
            for key, count in counts.most_common()]


def categories_breakdown(rows: list[dict], limit: int = 12) -> list[dict]:
    counts = Counter((r.get("category") or "—") for r in rows)
    return [{"label": label, "count": count} for label, count in counts.most_common(limit)]


def channels_breakdown(rows: list[dict]) -> list[dict]:
    counts = Counter((r.get("channel_fa") or "—") for r in rows)
    return [{"label": label, "count": count, "percent": _pct(count, len(rows))}
            for label, count in counts.most_common()]


def keywords_breakdown(rows: list[dict]) -> list[dict]:
    counts = Counter((r.get("input_id") or "—") for r in rows)
    return [{"label": label, "count": count} for label, count in counts.most_common(20)]


def funnel(rows: list[dict]) -> list[dict]:
    """قیف لید: از ردیف خام تا لید قابل تماس داغ."""
    cov = coverage(rows)
    seg = segments(rows)
    steps = [
        {"fa": "رکوردهای خام", "count": cov["total"], "percent": 100.0},
        {"fa": "بدون تکرار و داخل محدوده شهر", "count": cov["total"], "percent": 100.0},
        {"fa": "دارای وب‌سایت", "count": cov["website"], "percent": _pct(cov["website"], cov["total"])},
        {"fa": "دارای ایمیل", "count": cov["email"], "percent": _pct(cov["email"], cov["total"])},
        {"fa": "قابل تماس (ایمیل/تلفن/شبکه)", "count": cov["reachable"], "percent": _pct(cov["reachable"], cov["total"])},
        {"fa": "لید داغ", "count": seg["hot"]["count"], "percent": _pct(seg["hot"]["count"], cov["total"])},
        {"fa": "تماس گرفته‌شده", "count": sum(1 for r in rows if r.get("status") not in ("", None, "new")),
         "percent": _pct(sum(1 for r in rows if r.get("status") not in ("", None, "new")), cov["total"])},
    ]
    return steps


def map_points(rows: list[dict], limit: int = 1200) -> list[dict]:
    """نقاط برای نقشه شماتیک (بدون کاشی بیرونی → کاملاً آفلاین)."""
    points = []
    for r in rows[:limit]:
        try:
            lat, lon = float(r.get("latitude")), float(r.get("longitude"))
        except (TypeError, ValueError):
            continue
        points.append({
            "lat": lat, "lon": lon,
            "title": r.get("title", ""),
            "score": int(r.get("score") or 0),
            "segment": r.get("segment", "cold"),
            "category": r.get("category", ""),
        })
    return points


def insights(rows: list[dict], niche=None, city: str = "") -> list[dict]:
    """بینش‌های اجرایی به زبان فارسی — همان چیزی که مدیر فروش باید بخواند."""
    out: list[dict] = []
    if not rows:
        return [{"kind": "warn", "fa": "داده‌ای برای تحلیل وجود ندارد."}]

    cov = coverage(rows)
    seg = segments(rows)
    gaps = gaps_breakdown(rows)
    total = cov["total"]

    no_site = sum(1 for r in rows if not r.get("has_website"))
    if total and no_site / total >= 0.3:
        out.append({
            "kind": "opportunity",
            "fa": f"{no_site} کسب‌وکار ({_pct(no_site, total)}٪) اصلاً وب‌سایت ندارند — "
                  f"بزرگ‌ترین فرصت برای پیشنهاد «{rows[0].get('pitch', 'سایت')}».",
        })
    no_email = total - cov["email"]
    if no_email:
        out.append({
            "kind": "action",
            "fa": f"{no_email} ردیف ایمیل ندارند؛ با «غنی‌سازی مجدد» یا کانال واتس‌اپ/تلفن پوشش دهید "
                  f"(پوشش فعلی ایمیل: {_pct(cov['email'], total)}٪).",
        })
    if seg["hot"]["count"]:
        out.append({
            "kind": "priority",
            "fa": f"{seg['hot']['count']} لید داغ (میانگین امتیاز {seg['hot']['avg_score']}) "
                  f"برای شروع تماس امروز آماده است.",
        })
    top_gap = gaps[0] if gaps else None
    if top_gap:
        out.append({
            "kind": "gap",
            "fa": f"پرتکرارترین شکاف: {top_gap['fa']} در {top_gap['count']} مورد ({top_gap['percent']}٪).",
        })
    ratings = [_num(r.get("rating")) for r in rows if _num(r.get("rating")) > 0]
    if ratings:
        avg = round(statistics.fmean(ratings), 2)
        out.append({
            "kind": "market",
            "fa": f"میانگین امتیاز بازار {city or 'شهر'}: {avg} از ۵ — "
                  f"موارد زیر ۳٫۸ ({sum(1 for x in ratings if x < 3.8)} مورد) به مدیریت شهرت نیاز دارند.",
        })
    reviews = [int(_num(r.get("reviews"))) for r in rows if _num(r.get("reviews")) > 0]
    if reviews:
        out.append({
            "kind": "market",
            "fa": f"میانه تعداد نظرات: {int(statistics.median(reviews))} — "
                  f"نیمه بازار هنوز زیر ۵۰ نظر دارد یعنی با کمی SEO محلی جابه‌جا می‌شود.",
        })
    if niche is not None and getattr(niche, "custom", False):
        out.append({
            "kind": "note",
            "fa": "صنف سفارشی استفاده شده؛ کلیدواژه‌ها را در بخش «کلیدواژه‌ها» دقیق‌تر کنید تا کیفیت بالاتر برود.",
        })
    return out


# فیلدهای سبکی که در بسته آمار نگه داشته می‌شوند (تا ارجاع دایره‌ای run ↔ analytics نسازیم)
_META_KEYS = ("id", "created_at", "niche_id", "niche_fa", "city", "city_key", "country",
              "lat", "lon", "keywords", "depth", "radius", "grid", "lang", "profile",
              "engine", "live", "status", "total", "hot", "warm", "cold", "avg_score")


def slim_meta(meta: dict | None) -> dict:
    """فقط فیلدهای ساده اجرا را نگه می‌دارد؛ از ارجاع دایره‌ای هنگام JSON جلوگیری می‌کند."""
    meta = meta or {}
    return {key: meta.get(key) for key in _META_KEYS if key in meta and not isinstance(meta.get(key), (dict,))}


def build(rows: list[dict], meta: dict | None = None, niche=None) -> dict:
    """بسته کامل آمار برای یک اجرا."""
    meta = meta or {}
    cov = coverage(rows)
    return {
        "meta": slim_meta(meta),
        "coverage": cov,
        "coverage_percent": coverage_percent(cov),
        "segments": segments(rows),
        "score_distribution": score_distribution(rows),
        "rating_distribution": rating_distribution(rows),
        "gaps": gaps_breakdown(rows),
        "categories": categories_breakdown(rows),
        "channels": channels_breakdown(rows),
        "keywords": keywords_breakdown(rows),
        "funnel": funnel(rows),
        "map": map_points(rows),
        "insights": insights(rows, niche, meta.get("city", "")),
        "avg_score": _mean([r.get("score") for r in rows]),
    }
