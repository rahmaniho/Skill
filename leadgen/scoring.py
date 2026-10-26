"""امتیازدهی هوشمند لیدها (Lead Scoring) + بخش‌بندی + برچسب‌های فرصت.

ایده: داده خام گوگل‌مپ به خودی خود «فهرست» است، نه «فهرست فروش». این ماژول هر لید را
با یک روبریک شفاف و قابل توضیح (بدون جعبه سیاه) از ۰ تا ۱۰۰ امتیاز می‌دهد و می‌گوید
**چرا**، کدام کانال برای تماس بهتر است و چه «شکاف دیجیتالی» برای پیشنهاد خدمات وجود دارد.

امتیاز به «پروفایل هدف» شما وابسته است:
  • web_design  — شما سایت/فروشگاه می‌فروشید → کسب‌وکار بدون سایت = لید داغ
  • marketing   — تبلیغات/سئو/اینستاگرام   → حضور دیجیتال ضعیف + نظرات کم = فرصت
  • b2b_supply  — کالا/تجهیزات به کسب‌وکارها → بزرگی و اعتبار (نظرات، رتبه) مهم‌تر است
  • recruitment— جذب نیرو/همکار            → فعالیت و رشد مهم است
  • generic     — حالت عمومی و متوازن
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── پروفایل‌های هدف: وزن هر عامل (مجموع ≈ ۱۰۰) ────────────────────────────────
PROFILES: dict[str, dict] = {
    "web_design": {
        "fa": "فروش سایت و فروشگاه اینترنتی",
        # شکاف دیجیتال عامل غالب است: کسب‌وکار بدون سایت، داغ‌ترین لید این پروفایل است
        "weights": {"digital_gap": 44, "reachability": 18, "reputation": 14,
                    "activity": 12, "fit": 12},
        "gap_priority": ["no_website", "weak_website", "no_booking", "no_social"],
        "hot_threshold": 76,
    },
    "marketing": {
        "fa": "تبلیغات، سئو و اینستاگرام",
        "weights": {"digital_gap": 30, "reachability": 20, "reputation": 18,
                    "activity": 20, "fit": 12},
        "gap_priority": ["few_reviews", "no_social", "low_rating", "no_website"],
        "hot_threshold": 73,
    },
    "b2b_supply": {
        "fa": "فروش کالا و تجهیزات (B2B)",
        "weights": {"digital_gap": 8, "reachability": 34, "reputation": 26,
                    "activity": 20, "fit": 12},
        "gap_priority": ["no_email", "no_phone"],
        "hot_threshold": 76,
    },
    "recruitment": {
        "fa": "جذب نیرو و همکاری",
        "weights": {"digital_gap": 12, "reachability": 24, "reputation": 22,
                    "activity": 30, "fit": 12},
        "gap_priority": ["few_reviews", "no_website"],
        "hot_threshold": 74,
    },
    "generic": {
        "fa": "عمومی (متوازن)",
        "weights": {"digital_gap": 22, "reachability": 26, "reputation": 18,
                    "activity": 20, "fit": 14},
        "gap_priority": ["no_email", "no_website", "no_social"],
        "hot_threshold": 73,
    },
}

SEGMENTS = {"hot": "داغ 🔥", "warm": "گرم ☀️", "cold": "سرد ❄️"}


@dataclass
class ScoredLead:
    score: int
    segment: str
    segment_fa: str
    factors: list[dict] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    channel: str = ""
    channel_fa: str = ""
    pitch: str = ""
    profile: str = "generic"

    def to_dict(self) -> dict:
        return {
            "score": self.score, "segment": self.segment, "segment_fa": self.segment_fa,
            "factors": self.factors, "gaps": self.gaps, "tags": self.tags,
            "channel": self.channel, "channel_fa": self.channel_fa,
            "pitch": self.pitch, "profile": self.profile,
        }


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def detect_gaps(row: dict) -> list[str]:
    """شکاف‌های دیجیتالی یک کسب‌وکار — همان چیزی که فرصت فروش می‌سازد."""
    signals = row.get("signals") or {}
    gaps: list[str] = []
    if not row.get("has_website"):
        gaps.append("no_website")
    elif signals.get("wix") or signals.get("squarespace") or not signals.get("contact_form"):
        gaps.append("weak_website")
    if not row.get("has_email"):
        gaps.append("no_email")
    if not row.get("has_phone"):
        gaps.append("no_phone")
    if not row.get("has_social"):
        gaps.append("no_social")
    if not signals.get("booking") and row.get("reservations") not in ("true", True):
        gaps.append("no_booking")
    reviews = int(row.get("reviews") or 0)
    rating = float(row.get("rating") or 0)
    if reviews and reviews < 25:
        gaps.append("few_reviews")
    if reviews and rating and rating < 3.8:
        gaps.append("low_rating")
    if not signals.get("google_analytics") and row.get("has_website"):
        gaps.append("no_analytics")
    return gaps


GAP_FA = {
    "no_website": "بدون وب‌سایت",
    "weak_website": "سایت ضعیف/سازنده سایت آماده",
    "no_email": "بدون ایمیل",
    "no_phone": "بدون تلفن",
    "no_social": "بدون شبکه اجتماعی",
    "no_booking": "بدون رزرو/نوبت‌دهی آنلاین",
    "few_reviews": "نظرات کم (دیده‌نشده در گوگل)",
    "low_rating": "امتیاز پایین",
    "no_analytics": "بدون آنالیتیکس/ردیابی",
}


def score_lead(row: dict, profile: str = "generic", niche=None) -> ScoredLead:
    """امتیاز ۰-۱۰۰ + توضیح عامل‌به‌عامل + کانال پیشنهادی + جمله زاویه فروش."""
    profile_cfg = PROFILES.get(profile) or PROFILES["generic"]
    weights = profile_cfg["weights"]

    signals = row.get("signals") or {}
    rating = float(row.get("rating") or 0)
    reviews = int(row.get("reviews") or 0)
    gaps = detect_gaps(row)

    # ── عامل ۱: شکاف دیجیتال (فرصت فروش) ────────────────────────────────────
    gap_score = 0.0
    if "no_website" in gaps:
        gap_score += 0.60          # نبودِ وب‌سایت، بزرگ‌ترین و روشن‌ترین فرصت فروش است
    elif "weak_website" in gaps:
        gap_score += 0.22
    if "no_social" in gaps:
        gap_score += 0.16
    if "no_booking" in gaps:
        gap_score += 0.14
    if "few_reviews" in gaps:
        gap_score += 0.13
    if "no_analytics" in gaps:
        gap_score += 0.06
    if "low_rating" in gaps:
        gap_score += 0.06          # نیاز به مدیریت شهرت
    gap_score = _clamp(gap_score)

    # ── عامل ۲: دسترس‌پذیری (چقدر می‌توانیم تماس بگیریم) ─────────────────────
    reach = 0.0
    if row.get("has_email"):
        reach += 0.45
    if row.get("has_phone"):
        reach += 0.30
    if row.get("has_social"):
        reach += 0.15
    if row.get("has_website"):
        reach += 0.10
    reach = _clamp(reach)

    # ── عامل ۳: اعتبار (رتبه و کیفیت) ───────────────────────────────────────
    rep = 0.0
    if rating:
        rep += _clamp((rating - 2.5) / 2.5) * 0.70
    if row.get("price_range") in ("$$", "$$$", "متوسط", "گران"):
        rep += 0.15
    if signals.get("contact_form") or signals.get("live_chat"):
        rep += 0.15
    rep = _clamp(rep)

    # ── عامل ۴: فعالیت/بزرگی (تعداد نظرات به‌عنوان پراکسی حجم مشتری) ─────────
    act = _clamp((reviews ** 0.5) / 12) if reviews else 0.0
    if signals.get("online_order") or row.get("order_online") in ("true", True):
        act = _clamp(act + 0.10)

    # ── عامل ۵: تناسب با صنف هدف ────────────────────────────────────────────
    fit = 0.5
    if niche is not None:
        need = getattr(niche, "digital_need", "medium")
        ticket = getattr(niche, "ticket", "medium")
        fit += {"high": 0.35, "medium": 0.15, "low": 0.0}.get(need, 0.1)
        fit += {"high": 0.20, "medium": 0.10, "low": 0.0}.get(ticket, 0.05)
        if getattr(niche, "custom", False):
            fit -= 0.10
    fit = _clamp(fit)

    parts = {
        "digital_gap": (gap_score, "شکاف دیجیتال"),
        "reachability": (reach, "دسترس‌پذیری"),
        "reputation": (rep, "اعتبار و کیفیت"),
        "activity": (act, "فعالیت و حجم مشتری"),
        "fit": (fit, "تناسب با صنف"),
    }
    factors = []
    total = 0.0
    for key, (value, label) in parts.items():
        weight = weights.get(key, 0)
        points = round(value * weight, 1)
        total += points
        factors.append({
            "key": key, "fa": label, "weight": weight,
            "value": round(value, 3), "points": points,
            "detail": _factor_detail(key, row, rating, reviews, gaps),
        })

    score = int(round(total))
    threshold = profile_cfg["hot_threshold"]
    if score >= threshold:
        segment = "hot"
    elif score >= threshold - 22:
        segment = "warm"
    else:
        segment = "cold"

    channel, channel_fa = suggest_channel(row)
    tags = build_tags(row, gaps, rating, reviews, signals)

    return ScoredLead(
        score=score, segment=segment, segment_fa=SEGMENTS[segment], factors=factors,
        gaps=gaps, tags=tags, channel=channel, channel_fa=channel_fa,
        pitch=build_pitch(row, gaps, profile, niche), profile=profile,
    )


def _factor_detail(key: str, row: dict, rating: float, reviews: int, gaps: list[str]) -> str:
    if key == "digital_gap":
        if not gaps:
            return "حضور دیجیتال کامل — شکاف محسوسی برای پیشنهاد نیست"
        return "فرصت‌ها: " + "، ".join(GAP_FA.get(g, g) for g in gaps[:4])
    if key == "reachability":
        have = [x for x, ok in (("ایمیل", row.get("has_email")), ("تلفن", row.get("has_phone")),
                                ("شبکه اجتماعی", row.get("has_social")), ("سایت", row.get("has_website")))
                if ok]
        return "کانال‌های موجود: " + ("، ".join(have) if have else "هیچ")
    if key == "reputation":
        if not rating:
            return "بدون امتیاز ثبت‌شده"
        return f"امتیاز {rating} از ۵" + (f" با {reviews} نظر" if reviews else "")
    if key == "activity":
        return f"{reviews} نظر ثبت‌شده" + (" (پرفعالیت)" if reviews >= 200 else
                                        (" (متوسط)" if reviews >= 25 else " (کم‌فعال)"))
    return "مطابقت با صنف انتخابی و ارزش تقریبی مشتری آن صنف"


def suggest_channel(row: dict) -> tuple[str, str]:
    """بهترین کانال تماس: ایمیل > دایرکت اینستاگرام > واتس‌اپ > تماس تلفنی."""
    if row.get("has_email"):
        return "email", "ایمیل"
    if row.get("instagram"):
        return "instagram", "دایرکت اینستاگرام"
    phone = str(row.get("phone") or "")
    if phone and (phone.startswith("+989") or row.get("signals", {}).get("whatsapp")):
        return "whatsapp", "واتس‌اپ"
    if phone:
        return "phone", "تماس تلفنی"
    if row.get("has_website"):
        return "form", "فرم تماس سایت"
    return "visit", "مراجعه حضوری"


def build_tags(row: dict, gaps: list[str], rating: float, reviews: int, signals: dict) -> list[str]:
    """برچسب‌های کوتاه برای فیلتر کردن در داشبورد."""
    tags = [GAP_FA[g] for g in gaps if g in GAP_FA]
    if rating >= 4.6 and reviews >= 50:
        tags.append("ستاره محله ⭐")
    if reviews >= 300:
        tags.append("پرمراجعه")
    if row.get("has_website") and row.get("has_email") and row.get("has_phone"):
        tags.append("اطلاعات تماس کامل")
    if signals.get("booking") or row.get("reservations") in ("true", True):
        tags.append("نوبت‌دهی آنلاین دارد")
    if signals.get("live_chat"):
        tags.append("چت آنلاین دارد")
    if signals.get("instagram_embed"):
        tags.append("اینستاگرام فعال")
    # حذف تکراری با حفظ ترتیب
    return list(dict.fromkeys(tags))[:8]


def build_pitch(row: dict, gaps: list[str], profile: str, niche=None) -> str:
    """یک جمله «زاویه ورود» فارسی که متن کمپین روی آن سوار می‌شود."""
    hooks = list(getattr(niche, "hooks", []) or [])
    mapping = {
        "no_website": "سایت حرفه‌ای و موبایلی با فرم درخواست و نقشه",
        "weak_website": "بازطراحی سایت فعلی با سرعت و فرم تبدیل بهتر",
        "no_booking": "نوبت‌دهی/رزرو آنلاین برای حذف تماس‌های از دست رفته",
        "few_reviews": "افزایش نظرات گوگل و بالا آمدن در جست‌وجوی محلی",
        "low_rating": "مدیریت شهرت و پاسخ‌دهی به نظرات منفی",
        "no_social": "حضور منظم در اینستاگرام با محتوای محلی",
        "no_analytics": "ردیابی تبلیغات و اندازه‌گیری بازگشت سرمایه",
        "no_email": "ساخت کانال ایمیل و پیگیری خودکار مشتری‌ها",
        "no_phone": "دیده‌شدن در نقشه با شماره تماس معتبر",
    }
    cfg = PROFILES.get(profile, PROFILES["generic"])
    ordered = [g for g in cfg["gap_priority"] if g in gaps] + [g for g in gaps if g not in cfg["gap_priority"]]
    if ordered:
        return mapping.get(ordered[0], hooks[0] if hooks else "بهبود حضور دیجیتال")
    if hooks:
        return hooks[0]
    return "رشد مشتری با تبلیغات محلی هدفمند"


def score_all(rows: list[dict], profile: str = "generic", niche=None) -> list[dict]:
    """روی همه ردیف‌ها امتیاز می‌گذارد و فیلدهای امتیاز را به همان ردیف اضافه می‌کند."""
    for row in rows:
        result = score_lead(row, profile=profile, niche=niche)
        row.update({
            "score": result.score,
            "segment": result.segment,
            "segment_fa": result.segment_fa,
            "score_factors": result.factors,
            "gaps": result.gaps,
            "gaps_fa": [GAP_FA.get(g, g) for g in result.gaps],
            "tags": result.tags,
            "channel": result.channel,
            "channel_fa": result.channel_fa,
            "pitch": result.pitch,
            "profile": profile,
        })
    rows.sort(key=lambda r: (-int(r.get("score") or 0), -int(r.get("reviews") or 0)))
    return rows


def profile_list() -> list[dict]:
    return [{"id": key, "fa": cfg["fa"], "weights": cfg["weights"],
             "hot_threshold": cfg["hot_threshold"]} for key, cfg in PROFILES.items()]
