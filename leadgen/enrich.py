"""غنی‌سازی لیدها — همه با HTTP + regex، یعنی **صفر توکن LLM**.

۱) ایمیل عمیق: برای ردیف‌های دارای سایت ولی بدون ایمیل، صفحه اصلی و تا ۳ صفحه
   «تماس/درباره» بررسی می‌شود (دقیقاً همان منطق اسکیل کیت، کمی دقیق‌تر).
۲) شبکه‌های اجتماعی: Instagram / Facebook / LinkedIn از سایت کسب‌وکار استخراج می‌شود.
۳) سیگنال‌های سایت: واتس‌اپ/تلگرام، پلتفرم رزرو، وردپرس/ویکس/شاپیفای، فرم تماس —
   اینها برای امتیازدهی هوشمند و انتخاب زاویه کمپین استفاده می‌شوند.
۴) نرمال‌سازی: شماره تلفن، دامنه، عنوان، دسته.

در حالت دمو (بدون دسترسی شبکه) این توابع روی همان داده تولیدشده اجرا می‌شوند و
فقط نرمال‌سازی/سیگنال‌گیری انجام می‌شود؛ هیچ درخواست بیرونی زده نمی‌شود.
"""
from __future__ import annotations

import functools
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import config

# ── regexها ───────────────────────────────────────────────────────────────────
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r"mailto:([^\"'>\s?]+)", re.I)
HREF_RE = re.compile(r"<a\s[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)

SOCIAL_RE = {
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)", re.I),
    "facebook": re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/([A-Za-z0-9_.\-]+)", re.I),
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in|school)/([A-Za-z0-9_.\-%]+)", re.I),
}
# هندل‌های بی‌ارزش که نباید به‌عنوان پروفایل ثبت شوند
_SKIP_HANDLE = {
    "", "home", "pages", "people", "help", "about", "policies", "policy", "legal", "tos",
    "privacy", "settings", "sharer", "tr", "profile.php", "plugins", "dialog", "intent",
    "login", "share.php", "permalink.php", "p", "reel", "reels", "explore", "stories",
    "tv", "watch", "events", "groups", "marketplace", "gaming", "photo", "hashtag", "search",
}
_JUNK_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
_JUNK_DOMAIN = ("example", "sentry", "wixpress", "wix.com", "godaddy", "squarespace",
                "wordpress", "domain.com", "email.com", "yourdomain", "schema.org",
                "w3.org", "sentry.io", "gravatar", "cloudflare")
_JUNK_LOCAL = {"name", "email", "user", "you", "someone", "test", "info@", "admin@"}

# سیگنال‌های تکنولوژی/تماس در صفحه
SIGNAL_RE = {
    "whatsapp": re.compile(r"wa\.me/|whatsapp", re.I),
    "telegram": re.compile(r"t\.me/|telegram", re.I),
    "booking": re.compile(r"book(ing)?[-\s]?(online|now)|reserve|appointment|نوبت|رزرو", re.I),
    "online_order": re.compile(r"add[-\s]to[-\s]cart|online[-\s]order|سفارش آنلاین|خرید اینترنتی", re.I),
    "contact_form": re.compile(r"<form[^>]*>|type=\"email\"|فرم تماس", re.I),
    "wordpress": re.compile(r"wp-content|wp-includes|wordpress", re.I),
    "wix": re.compile(r"wix\.com|wixstatic|parastorage", re.I),
    "shopify": re.compile(r"cdn\.shopify|myshopify", re.I),
    "squarespace": re.compile(r"squarespace", re.I),
    "google_analytics": re.compile(r"gtag\(|google-analytics|googletagmanager", re.I),
    "instagram_embed": re.compile(r"instagram\.com/(?:p|reel)/|instafeed", re.I),
    "live_chat": re.compile(r"crisp\.chat|tawk\.to|intercom|zendesk|raychat|گفتگوی آنلاین", re.I),
}


def _fetch_html(url: str, timeout: int = 8) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    try:
        request = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; " + config.USER_AGENT + ")",
            "Accept": "text/html",
        })
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.read(400_000).decode("utf-8", "replace")
    except Exception:
        return ""


@functools.lru_cache(maxsize=1024)
def _fetch_cached(url: str) -> str:
    """واکشی صفحه اصلی با کش — صفحه‌های تماس/درباره و شبکه‌های اجتماعی یک‌بار سایت را می‌گیرند."""
    return _fetch_html(url, timeout=8)


def host_of(url: str) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    hostname = (urllib.parse.urlparse(url).hostname or "").lower()
    return hostname[4:] if hostname.startswith("www.") else hostname


def extract_emails(html: str, site_url: str = "", limit: int = 3) -> list[str]:
    """ایمیل‌ها از mailto و متن صفحه؛ هرزنامه‌ها حذف و ایمیل هم‌دامنه اولویت دارد."""
    site = host_of(site_url)
    found: list[str] = []
    for raw in MAILTO_RE.findall(html or "") + EMAIL_RE.findall(html or ""):
        email = urllib.parse.unquote(raw).strip().lower()
        local, _, domain = email.rpartition("@")
        if not EMAIL_RE.fullmatch(email):
            continue
        if email.endswith(_JUNK_EXT) or local in _JUNK_LOCAL:
            continue
        if any(junk in domain for junk in _JUNK_DOMAIN):
            continue
        if email in found:
            continue
        found.append(email)

    def on_site(email: str) -> bool:
        return bool(site) and (email.endswith("@" + site) or email.endswith("." + site))

    return sorted(found, key=lambda e: not on_site(e))[:limit]


def contact_links(html: str, base_url: str) -> list[str]:
    """تا ۳ لینک هم‌دامنه برای «تماس/درباره»: اول لینک‌های پیدا‌شده، بعد مسیرهای استاندارد."""
    if not base_url.startswith(("http://", "https://")):
        base_url = "http://" + base_url
    base_host, out = host_of(base_url), []
    for href, text in HREF_RE.findall(html or ""):
        if not re.search(r"contact|about|تماس|درباره", href + " " + text, re.I):
            continue
        url = urllib.parse.urldefrag(urllib.parse.urljoin(base_url, href.strip()))[0]
        if url.startswith(("http://", "https://")) and host_of(url) == base_host and url not in out:
            out.append(url)
    for path in ("/contact", "/contact-us", "/about", "/about-us", "/تماس-با-ما", "/درباره-ما"):
        url = urllib.parse.urljoin(base_url, path)
        if len(out) < 3 and url not in out:
            out.append(url)
    return out[:3]


def find_socials(html: str) -> dict[str, str]:
    out = {"instagram": "", "facebook": "", "linkedin": ""}
    for platform, regex in SOCIAL_RE.items():
        for match in regex.finditer(html or ""):
            handle = match.group(1).lower()
            if handle in _SKIP_HANDLE:
                continue
            if platform == "facebook" and (handle.isdigit() or len(handle) < 3):
                continue
            out[platform] = match.group(0).rstrip("\"'/").replace("\\", "")
            break
    return out


def detect_signals(html: str) -> dict[str, bool]:
    html = html or ""
    return {key: bool(regex.search(html)) for key, regex in SIGNAL_RE.items()}


# ── نرمال‌سازی ────────────────────────────────────────────────────────────────
def normalize_phone(phone: str) -> str:
    """یک‌دست‌سازی شماره: حذف نویز، فرمت بین‌المللی در صورت امکان، تشخیص موبایل ایران."""
    raw = (phone or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+"):
        if digits.startswith("0") and len(digits) >= 10:
            digits = "+98" + digits[1:]       # فرض: شماره ایرانی بدون کد کشور
        else:
            digits = "+" + digits if len(digits) >= 10 else digits
    return digits


def is_mobile_ir(phone: str) -> bool:
    digits = re.sub(r"\D", "", normalize_phone(phone))
    return digits.startswith("989") and len(digits) == 12


def maps_link(row: dict) -> str:
    """لینک «لوکیشن دقیق» روی گوگل‌مپ.

    اول لینک خود مکان (ستون `link` موتور) که مستقیم به پروفایل کسب‌وکار می‌رود؛
    اگر نبود، لینک جست‌وجو با مختصات lat/lon؛ و در نهایت نشانی متنی.
    """
    link = (row.get("link") or "").strip()
    if link.startswith("http"):
        return link
    try:
        lat, lon = float(row.get("latitude")), float(row.get("longitude"))
        return f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"
    except (TypeError, ValueError):
        pass
    title = urllib.parse.quote(str(row.get("title") or ""))
    address = urllib.parse.quote(str(row.get("address") or ""))
    if title or address:
        return f"https://www.google.com/maps/search/?api=1&query={title}+{address}".replace("+ ", "+")
    return ""


def normalize_row(row: dict) -> dict:
    """پاک‌سازی یک ردیف خام: فاصله‌ها، لیست ایمیل‌ها، اعداد، دامنه، لوکیشن دقیق."""
    out = dict(row)
    title = (out.get("title") or "").strip()
    out["title"] = re.sub(r"\s+", " ", title)
    emails_raw = (out.get("emails") or "").strip()
    emails = [e.strip().lower() for e in re.split(r"[,;|\s]+", emails_raw) if "@" in e]
    out["emails"] = ", ".join(dict.fromkeys(emails))          # حذف تکراری، حفظ ترتیب
    out["email_primary"] = emails[0] if emails else ""
    out["phone"] = normalize_phone(out.get("phone", ""))
    out["website"] = (out.get("website") or "").strip()
    out["domain"] = host_of(out["website"])
    out["category"] = (out.get("category") or "").strip()
    out["address"] = re.sub(r"\s+", " ", (out.get("address") or "").strip())
    for key in ("instagram", "facebook", "linkedin"):
        out[key] = (out.get(key) or "").strip()

    def to_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def to_int(value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    out["rating"] = to_float(out.get("review_rating"))
    out["reviews"] = to_int(out.get("review_count"))
    out["price_range"] = (out.get("price_range") or "").strip()
    # ── لوکیشن دقیق ─────────────────────────────────────────────────────────
    out["latitude"] = str(out.get("latitude") or "").strip()
    out["longitude"] = str(out.get("longitude") or "").strip()
    try:
        out["lat"] = round(float(out["latitude"]), 6)
        out["lon"] = round(float(out["longitude"]), 6)
        out["location"] = f"{out['lat']}, {out['lon']}"
        out["has_location"] = True
    except (TypeError, ValueError):
        out["lat"] = out["lon"] = 0.0
        out["location"] = ""
        out["has_location"] = False
    out["plus_code"] = (out.get("plus_code") or "").strip()
    out["maps_link"] = maps_link(out)
    # ستون خام «status» موتور (وضعیت مکان در گوگل) با «status» پیگیری CRM تداخل نکند
    if "status" in out and str(out.get("status")) not in (
            "new", "contacted", "replied", "qualified", "won", "lost", "unsubscribed"):
        out["place_status"] = str(out.pop("status"))
    out["has_website"] = bool(out["website"])
    out["has_email"] = bool(out["email_primary"])
    out["has_phone"] = bool(out["phone"])
    out["has_social"] = any(out.get(k) for k in ("instagram", "facebook", "linkedin"))
    # کامل بودن اطلاعات: ۶ کانال/فیلد کلیدی (سایت، ایمیل، تلفن، شبکه اجتماعی، لوکیشن، نشانی)
    out["contact_channels"] = sum([out["has_website"], out["has_email"], out["has_phone"],
                                   out["has_social"], out["has_location"], bool(out["address"])])
    out["completeness"] = round(100 * out["contact_channels"] / 6)
    return out


# ── اجرای موازی غنی‌سازی ──────────────────────────────────────────────────────
def enrich_rows(rows: list[dict], *, socials: bool = True, deep_emails: bool = True,
                signals: bool = True, workers: int | None = None,
                allow_network: bool = True, on_progress=None) -> dict:
    """غنی‌سازی دسته‌ای ردیف‌ها (در جا، بدون کپی). گزارش آماری برمی‌گرداند."""
    workers = workers or config.ENRICH_WORKERS
    # ⚠ در جا جایگزین می‌کنیم (slice) تا فهرستِ فراخوان هم به‌روز شود؛
    #   در غیر این صورت لایه امتیازدهی ردیف‌های نرمال‌نشده را می‌بیند.
    rows[:] = [normalize_row(r) for r in rows]
    stats = {"emails_before": sum(1 for r in rows if r.get("email_primary")),
             "emails_after": 0, "socials_found": 0, "sites_checked": 0,
             "signals": 0, "network_used": False}

    if not allow_network:
        # حالت دمو/آفلاین: فقط نرمال‌سازی و سیگنال‌های موجود در داده
        stats["emails_after"] = stats["emails_before"]
        stats["socials_found"] = sum(1 for r in rows if r.get("has_social"))
        if on_progress:
            on_progress({"stage": "enrich", "message": "غنی‌سازی آفلاین (بدون واکشی سایت)"})
        return stats

    stats["network_used"] = True
    todo = [r for r in rows if r.get("website")]

    def work(row: dict) -> None:
        home = _fetch_cached(row["website"])
        if not home:
            return
        # ایمیل عمیق
        if deep_emails and not row.get("email_primary"):
            found = extract_emails(home, row["website"])
            if not found:
                pages = "".join(_fetch_html(u, timeout=8) for u in contact_links(home, row["website"]))
                found = extract_emails(pages, row["website"])
            if found:
                row["emails"] = ", ".join(found)
                row["email_primary"] = found[0]
                row["has_email"] = True
                row["contact_channels"] = sum([row["has_website"], row["has_email"], row["has_phone"], row["has_social"]])
                row["completeness"] = round(100 * row["contact_channels"] / 4)
        # شبکه‌های اجتماعی
        if socials:
            found_socials = find_socials(home)
            for key, value in found_socials.items():
                if value and not row.get(key):
                    row[key] = value
            row["has_social"] = any(row.get(k) for k in ("instagram", "facebook", "linkedin"))
        # سیگنال‌های سایت
        if signals:
            row["signals"] = detect_signals(home)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(work, todo):
            done += 1
            if on_progress and (done % 5 == 0 or done == len(todo)):
                on_progress({"stage": "enrich", "status": "working",
                             "percent": int(50 + 40 * done / max(1, len(todo))),
                             "message": f"غنی‌سازی سایت‌ها: {done}/{len(todo)}"})

    stats["sites_checked"] = len(todo)
    stats["emails_after"] = sum(1 for r in rows if r.get("email_primary"))
    stats["socials_found"] = sum(1 for r in rows if r.get("has_social"))
    stats["signals"] = sum(1 for r in rows if r.get("signals"))
    return stats
