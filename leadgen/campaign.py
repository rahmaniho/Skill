"""تولید کمپین outreach فارسی (و انگلیسی) از روی لیدهای امتیازدهی‌شده.

برای هر لید و هر کانال، متن آماده ارسال تولید می‌کند:
  • ایمیل: دنباله ۳ لمسی (آشنایی → ارزش/نمونه → پیگیری کوتاه)
  • واتس‌اپ / پیامک: کوتاه و محاوره‌ای
  • دایرکت اینستاگرام: دو قدمی

متن‌ها با داده واقعی لید شخصی‌سازی می‌شوند: نام کسب‌وکار، صنف، شهر، شکاف دیجیتال
شناسایی‌شده و جمله زاویه فروش. خروجی هم به‌صورت متن و هم CSV آماده ارسال است.

⚠ رعایت قانون: ایمیل/پیامک انبوه در بسیاری کشورها (GDPR، CCPA، CAN-SPAM، مقررات
   ارتباطات ایران) نیاز به مبنای قانونی، امکان انصراف و هویت فرستنده دارد. این ماژول
   به همین دلیل در هر متن، امضا و «لغو دریافت» را نگه می‌دارد و هرگز داده شخصی
   پنهان‌کاری نمی‌کند.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

CHANNELS = ("email", "whatsapp", "sms", "instagram")

CHANNEL_FA = {
    "email": "ایمیل",
    "whatsapp": "واتس‌اپ",
    "sms": "پیامک",
    "instagram": "دایرکت اینستاگرام",
    "phone": "تماس تلفنی",
    "form": "فرم سایت",
    "visit": "حضوری",
}


@dataclass
class Sender:
    name: str = "تیم رشد ما"
    company: str = "استودیو دیجیتال"
    role: str = "مشاور رشد کسب‌وکارهای محلی"
    phone: str = ""
    email: str = ""
    website: str = ""
    offer: str = "۳۰ دقیقه مشاوره رایگان"
    case_study: str = "در پروژه مشابه، مشتری‌های محلی یک کسب‌وکار طی ۶۰ روز ۲٫۳ برابر شد"

    @classmethod
    def from_dict(cls, data: dict | None) -> "Sender":
        data = data or {}
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known and v is not None})


def _first_name(title: str) -> str:
    """نام کوتاه و محترمانه برای شروع پیام (بدون شکستن حریم شخصی)."""
    clean = re.sub(r"[()|]", " ", title or "").strip()
    return clean or "کسب‌وکار شما"


def _city_of(row: dict) -> str:
    address = row.get("address") or ""
    parts = re.split(r"[،,]", address)
    return (parts[-1] if parts else "").strip() or row.get("city", "")


def build_context(row: dict, sender: Sender, niche=None) -> dict:
    gaps_fa = row.get("gaps_fa") or []
    return {
        "business": _first_name(row.get("title", "")),
        "category": row.get("category") or (niche.fa if niche else "کسب‌وکار شما"),
        "city": _city_of(row),
        "gap": gaps_fa[0] if gaps_fa else "حضور دیجیتال",
        "pitch": row.get("pitch") or "بهبود حضور دیجیتال",
        "rating": row.get("rating") or "",
        "reviews": row.get("reviews") or 0,
        "phone": row.get("phone") or "",
        "email": row.get("email_primary") or "",
        "website": row.get("website") or "",
        "instagram": row.get("instagram") or "",
        "score": row.get("score") or 0,
        "sender": sender,
    }


# ── ایمیل: دنباله ۳ لمسی ──────────────────────────────────────────────────────
EMAIL_SEQUENCE = [
    {
        "step": 1,
        "day": 0,
        "subject": "{business} — یک پیشنهاد مشخص برای {category} در {city}",
        "body": """سلام تیم {business}،

من {sender.role} {sender.company} هستم. وقتی پروفایل «{business}» را در گوگل‌مپ دیدم،
یک نکته برایم روشن شد: {gap}. یعنی بخش قابل توجهی از مشتری‌های محلی که همین حالا
«{category}» را جست‌وجو می‌کنند، شما را نمی‌بینند یا نمی‌توانند راحت درخواست بدهند.

کاری که ما می‌کنیم مشخص است: {pitch}.
{sender.case_study}.

اگر موافق باشید، {sender.offer} رزرو کنیم؛ در همان جلسه یک بررسی کوتاه از وضعیت
جست‌وجوی محلی «{business}» به شما می‌دهم — بدون تعهد.

با احترام،
{sender.name} — {sender.company}
{sender.phone} | {sender.email} | {sender.website}

(اگر ترجیح می‌دهید دیگر پیام نگیرید، فقط بنویسید «لغو».)""",
    },
    {
        "step": 2,
        "day": 3,
        "subject": "پاسخ: {business} — ۳ عددی که درباره جست‌وجوی محلی جالب است",
        "body": """سلام دوباره،

کوتاه و عددی می‌گویم تا تصمیم‌گیری راحت باشد:

۱) حدود ۴۶٪ از جست‌وجوهای گوگل «محلی» هستند.
۲) کسب‌وکارهایی که پروفایل گوگل‌مپ کامل و عکس دارند، تا ~۵۲۰٪ تماس بیشتر می‌گیرند.
۳) برای «{category}» در {city}، رقبا معمولاً با {gap} کار می‌کنند — یعنی هنوز جای رشد هست.

پیشنهاد من برای «{business}» همچنان همان است: {pitch}.

اگر الان زمان مناسبی نیست، فقط یک «بعداً» بفرستید تا سه ماه دیگر پیام بدهم.

{sender.name} — {sender.company} | {sender.phone}""",
    },
    {
        "step": 3,
        "day": 8,
        "subject": "پرونده {business} را ببندم؟",
        "body": """سلام تیم {business}،

نمی‌خواهم صندوق ورودی‌تان را اشغال کنم. این آخرین پیام من درباره {pitch} برای
«{business}» ({category} در {city}) است.

دو حالت دارد:
• اگر علاقه‌مندید، {sender.offer} — این لینک/شماره کافی است: {sender.phone}
• اگر نه، هیچ اشکالی ندارد؛ فقط «لغو» بنویسید تا از فهرست خارج شوید.

برایتان در {city} پرفروش‌ترین فصل را آرزو می‌کنم.
{sender.name}""",
    },
]

WHATSAPP_MESSAGE = """سلام، وقتتون بخیر 🌷
«{business}» رو توی گوگل‌مپ برای {category} در {city} دیدم.
یک مورد مشخص به چشمم اومد: {gap}.

ما روی {pitch} کار می‌کنیم و {sender.case_study}.

اگر مایل باشید یک نمونه و هزینه تقریبی بفرستم؟ فقط یک «بله» کافیه.
{sender.name} — {sender.company}
(اگر مزاحم شدم، عذرخواهی می‌کنم؛ با «لغو» دیگه پیام نمی‌دم.)"""

SMS_MESSAGE = """{business} عزیز، {gap} باعث می‌شه مشتری محلی شما رو پیدا نکنه.
{pitch} — {sender.offer}. {sender.name} {sender.phone} | لغو=۱"""

INSTAGRAM_SEQUENCE = [
    {
        "step": 1,
        "body": """سلام 👋 پروفایل «{business}» رو دیدم؛ کارتون توی {category} واقعاً تمیزه.
یک نکته کوچیک که معمولاً مشتری از دست می‌ده: {gap}.
اگر دوست داشتید بگید تا یک پیشنهاد کوتاه و عملی براتون بفرستم (بدون هزینه).""",
    },
    {
        "step": 2,
        "body": """خیلی کوتاه: ما {pitch} رو برای کسب‌وکارهای محلی {city} انجام می‌دیم.
{sender.case_study}.
اگر مایل بودید، نمونه‌کار و هزینه تقریبی رو همین‌جا می‌فرستم 🙂
{sender.name} — {sender.company}""",
    },
]

PHONE_SCRIPT = """۱) سلام، {sender.name} هستم از {sender.company}. مزاحم نیستم؟ (۱۰ ثانیه)
۲) پروفایل «{business}» رو در گوگل‌مپ دیدم؛ {rating} ستاره با {reviews} نظر — اعتبارتون خوبه.
۳) اما یک شکاف مشخص هست: {gap}. یعنی مشتری محلی یا پیداتون نمی‌کنه یا راحت درخواست نمی‌ده.
۴) کار ما: {pitch}. {sender.case_study}.
۵) پیشنهاد: {sender.offer}. فردا ساعت ۱۰ یا ۴ بعدازظهر کدام مناسب‌تره؟
۶) اگر نه: ممنون از وقتتون؛ اجازه هست دو ماه دیگر یک بار دیگر تماس بگیرم؟"""


def render(template: str, ctx: dict) -> str:
    """جای‌گذاری امن {field} و {sender.field} بدون نیاز به کتابخانه قالب."""
    def replace(match: re.Match) -> str:
        expr = match.group(1)
        if expr.startswith("sender."):
            return str(getattr(ctx["sender"], expr.split(".", 1)[1], ""))
        return str(ctx.get(expr, ""))

    text = re.sub(r"\{([a-zA-Z_.][\w.]*)\}", replace, template)
    # پاک‌سازی خطوط خالیِ اضافه و فاصله‌های زائد
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def generate(row: dict, channel: str = "email", sender: Sender | None = None,
             niche=None, language: str = "fa") -> dict:
    """متن آماده ارسال برای یک لید و یک کانال."""
    sender = sender or Sender()
    ctx = build_context(row, sender, niche)
    channel = (channel or "email").lower()

    if language.startswith("en"):
        return _english(row, ctx, channel, sender)

    if channel == "email":
        touches = [
            {"step": t["step"], "day": t["day"], "subject": render(t["subject"], ctx),
             "body": render(t["body"], ctx)}
            for t in EMAIL_SEQUENCE
        ]
        text = "\n\n---\n\n".join(f"[لمس {t['step']} — روز {t['day']}]\nموضوع: {t['subject']}\n\n{t['body']}"
                                 for t in touches)
        return {"channel": channel, "channel_fa": CHANNEL_FA[channel], "to": ctx["email"],
                "subject": touches[0]["subject"], "body": touches[0]["body"],
                "sequence": touches, "text": text, "context": _safe_ctx(ctx)}
    if channel == "whatsapp":
        body = render(WHATSAPP_MESSAGE, ctx)
        return {"channel": channel, "channel_fa": CHANNEL_FA[channel], "to": ctx["phone"],
                "subject": "", "body": body, "sequence": [], "text": body,
                "context": _safe_ctx(ctx)}
    if channel == "sms":
        body = render(SMS_MESSAGE, ctx)
        body = body[:280]  # سقف رایج پیامک فارسی
        return {"channel": channel, "channel_fa": CHANNEL_FA[channel], "to": ctx["phone"],
                "subject": "", "body": body, "sequence": [], "text": body,
                "context": _safe_ctx(ctx)}
    if channel == "instagram":
        touches = [{"step": t["step"], "day": t["step"] * 2, "subject": "",
                    "body": render(t["body"], ctx)} for t in INSTAGRAM_SEQUENCE]
        text = "\n\n---\n\n".join(f"[پیام {t['step']}]\n{t['body']}" for t in touches)
        return {"channel": channel, "channel_fa": CHANNEL_FA[channel], "to": ctx["instagram"],
                "subject": "", "body": touches[0]["body"], "sequence": touches, "text": text,
                "context": _safe_ctx(ctx)}
    if channel == "phone":
        body = render(PHONE_SCRIPT, ctx)
        return {"channel": channel, "channel_fa": CHANNEL_FA[channel], "to": ctx["phone"],
                "subject": "سناریوی تماس", "body": body, "sequence": [], "text": body,
                "context": _safe_ctx(ctx)}
    return {"channel": channel, "channel_fa": CHANNEL_FA.get(channel, channel), "to": "",
            "subject": "", "body": "", "sequence": [], "text": "", "context": _safe_ctx(ctx)}


def _safe_ctx(ctx: dict) -> dict:
    safe = dict(ctx)
    safe["sender"] = {k: getattr(ctx["sender"], k) for k in ctx["sender"].__dataclass_fields__}  # type: ignore[attr-defined]
    return safe


def _english(row: dict, ctx: dict, channel: str, sender: Sender) -> dict:
    """نسخه انگلیسی برای بازارهای خارج از ایران (همان ساختار دنباله)."""
    business, category, city = ctx["business"], ctx["category"], ctx["city"]
    gap_en = {
        "بدون وب‌سایت": "no website",
        "بدون ایمیل": "no public email",
        "بدون شبکه اجتماعی": "no active social profiles",
        "بدون رزرو/نوبت‌دهی آنلاین": "no online booking",
        "نظرات کم (دیده‌نشده در گوگل)": "very few Google reviews",
        "امتیاز پایین": "a low rating",
        "سایت ضعیف/سازنده سایت آماده": "a template-built website",
    }.get(ctx["gap"], "a digital gap")

    if channel == "email":
        subject = f"{business} — one concrete idea for {category} in {city}"
        body = (
            f"Hi {business} team,\n\n"
            f"I found your Google Maps listing while looking at {category} in {city}. "
            f"One thing stood out: {gap_en}. That usually means local customers search, "
            f"but never reach you.\n\n"
            f"We fix exactly that: {ctx['pitch']}.\n\n"
            f"Would a free 30-minute audit be useful? I'll show the numbers for your area.\n\n"
            f"{sender.name} — {sender.company}\n{sender.phone} | {sender.email}\n\n"
            f"(Reply 'unsubscribe' and I won't contact you again.)"
        )
        return {"channel": channel, "channel_fa": CHANNEL_FA.get(channel, channel),
                "to": ctx["email"], "subject": subject, "body": body, "sequence": [],
                "text": f"{subject}\n\n{body}", "context": _safe_ctx(ctx)}

    body = (f"Hi {business} — I saw your listing for {category} in {city}. "
            f"{gap_en.capitalize()} is costing you local customers. We can help with "
            f"{ctx['pitch']}. Worth a quick chat? — {sender.name}, {sender.company}")
    return {"channel": channel, "channel_fa": CHANNEL_FA.get(channel, channel),
            "to": ctx["phone"], "subject": "", "body": body, "sequence": [],
            "text": body, "context": _safe_ctx(ctx)}


def mailto_link(row: dict, channel: str = "email", sender: Sender | None = None, niche=None) -> str:
    """لینک mailto آماده برای کلیک در داشبورد."""
    import urllib.parse
    msg = generate(row, "email", sender, niche)
    to = msg.get("to") or ""
    if not to:
        return ""
    return ("mailto:" + urllib.parse.quote(to, safe="@")
            + f"?subject={urllib.parse.quote(msg['subject'], safe='')}"
            + f"&body={urllib.parse.quote(msg['body'], safe='')}")


def wa_link(row: dict, sender: Sender | None = None, niche=None) -> str:
    """لینک wa.me برای واتس‌اپ."""
    digits = re.sub(r"\D", "", row.get("phone") or "")
    if not digits:
        return ""
    if digits.startswith("98") is False and len(digits) == 11 and digits.startswith("0"):
        digits = "98" + digits[1:]
    msg = generate(row, "whatsapp", sender, niche)
    import urllib.parse
    return f"https://wa.me/{digits}?text={urllib.parse.quote(msg['body'])}"
