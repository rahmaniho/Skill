"""تنظیمات مرکزی — بدون هیچ وابستگی بیرونی، فقط کتابخانه استاندارد پایتون.

همه چیز از فایل `.env` (اختیاری) و متغیرهای محیطی خوانده می‌شود. متغیر محیطی
از فایل برتری دارد، همان رفتاری که `docker compose` و اسکریپت‌های کیت اصلی دارند.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# کلیدهایی که از .env خوانده می‌شوند (همان‌هایی که کیت اصلی و این برنامه استفاده می‌کنند)
ENV_KEYS = (
    "SCRAPER_BASE_URL",
    "SCRAPER_PORT",
    "SCRAPER_API_KEY",
    "SCRAPER_PROXIES",
    "ENGINE_MODE",
    # ── موتورهای واقعیِ بدون داکر (برای اجرای زنده روی Vercel) ────────────────
    "OSM_ENDPOINT",          # سرویس جست‌وجوی OpenStreetMap (Nominatim)
    "OSM_MIN_INTERVAL",      # فاصلهٔ حداقلی بین دو درخواست به OSM (ثانیه)
    "OSM_LIMIT",             # حداکثر نتیجه در هر کوئری OSM
    "OSM_COUNTRY",           # پیش‌فرض کشور (ir) برای محدود کردن نتایج
    "PLACES_API_KEY",        # کلید Google Places API (مسیر تجاری)
    "SELF_DRIVE",            # اجرای خودکار صف از سمت رابط (بدون ورکر)
    # ── محافظ‌های تجاری ──────────────────────────────────────────────────────
    "LEADGEN_ACCESS_CODE",   # اگر بگذارید، اپ با کد دسترسی قفل می‌شود
    "LEADGEN_MAX_PENDING",   # سقف کارهای در صف (۰ = بی‌سقف)
    "LEADGEN_RATE_RUNS",     # سقف اجرا در ساعت برای هر IP
    "LEADGEN_HOST",
    "LEADGEN_PORT",
    "LEADGEN_DATA_DIR",
    "LEADGEN_ROOT_UI",
    "ENRICH_WORKERS",
    # ── لایهٔ عملیاتی ابری ────────────────────────────────────────────────────
    "LEADGEN_DB",            # sqlite | supabase
    "SUPABASE_URL",          # https://<ref>.supabase.co
    "SUPABASE_SERVICE_KEY",  # کلید service_role (فقط سمت سرور)
    "SUPABASE_ANON_KEY",     # کلید anon (فقط وقتی RLS را خودتان باز کرده‌اید)
    "SUPABASE_SCHEMA",       # پیش‌فرض public
    "LEADGEN_QUEUE",         # auto | always | never — صف اجراها برای ورکر
    "WORKER_ID",             # شناسهٔ ورکر (پیش‌فرض: نام میزبان)
    "WORKER_POLL_SECONDS",   # فاصلهٔ بررسی صف
    "WORKER_MAX_JOBS",       # سقف اجرا در هر چرخهٔ ورکر (۰ = نامحدود)
    "WORKER_MAX_ATTEMPTS",   # تلاش مجدد پیش از شکست قطعی
    "APP_BASE_URL",          # آدرس عمومی اپ (برای لینک‌های ایمیل)
    # ── تحویل خروجی (ایمیل) ──────────────────────────────────────────────────
    "DIGEST_TO",             # گیرنده‌های digest (با کاما جدا شوند)
    "DIGEST_FROM",           # فرستندهٔ نمایشی
    "DIGEST_SUBJECT",        # موضوع پیش‌فرض
    "DIGEST_MAX_LEADS",      # چند لید داغ در ایمیل listed شود
    "GMAIL_SENDER",          # آدرس Gmail
    "GMAIL_APP_PASSWORD",    # رمز برنامه (مسیر SMTP)
    "GMAIL_REFRESH_TOKEN",   # مسیر Gmail API (HTTPS — روی Vercel تنها راه است)
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
)


def _strip_quotes(value: str) -> str:
    v = value.strip().rstrip("\r")
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def load_env_file(path: Path | None = None) -> None:
    """`.env` را می‌خواند؛ مقادیری که از قبل در محیط هستند را بازنویسی نمی‌کند."""
    path = path or (ROOT / ".env")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, sep, value = line.partition("=")
                key = key.strip()
                if sep and key in ENV_KEYS and key not in os.environ:
                    os.environ[key] = _strip_quotes(value)
    except OSError:
        pass


load_env_file()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)) or default)
    except ValueError:
        return default
def env_float(name: str, default: float) -> float:
    try:
        return float(env(name) or default)
    except (TypeError, ValueError):
        return default


# ── موتور اسکراپر ─────────────────────────────────────────────────────────────
SCRAPER_PORT = env_int("SCRAPER_PORT", 8080)
SCRAPER_BASE_URL = env("SCRAPER_BASE_URL") or f"http://localhost:{SCRAPER_PORT}"
SCRAPER_API_KEY = env("SCRAPER_API_KEY")
SCRAPER_PROXIES = [p.strip() for p in env("SCRAPER_PROXIES").split(",") if p.strip()]

# auto | docker | demo
ENGINE_MODE = env("ENGINE_MODE", "auto").lower() or "auto"

# ── موتورهای واقعی بدون داکر ──────────────────────────────────────────────────
# OSM/Nominatim: دادهٔ واقعی، بدون کلید و بدون داکر — برای شروع و نصب‌های کوچک.
# Google Places API: دقیق‌تر و سریع‌تر؛ مسیر تجاری (کلید لازم است).
OSM_ENDPOINT = (env("OSM_ENDPOINT") or "https://nominatim.openstreetmap.org").rstrip("/")
OSM_MIN_INTERVAL = max(1.0, env_float("OSM_MIN_INTERVAL", 1.1))
OSM_LIMIT = max(1, min(40, env_int("OSM_LIMIT", 40)))
OSM_COUNTRY = env("OSM_COUNTRY", "ir").lower()
PLACES_API_KEY = env("PLACES_API_KEY") or env("GOOGLE_MAPS_API_KEY")
SELF_DRIVE = env("SELF_DRIVE", "1") != "0"

# ── محافظ‌های تجاری (ضد سوءاستفاده) ───────────────────────────────────────────
ACCESS_CODE = env("LEADGEN_ACCESS_CODE").strip()
MAX_PENDING_JOBS = max(0, env_int("LEADGEN_MAX_PENDING", 3))
RATE_RUNS_PER_HOUR = max(0, env_int("LEADGEN_RATE_RUNS", 30))


# ── سرویس وب ──────────────────────────────────────────────────────────────────
HOST = env("LEADGEN_HOST", "0.0.0.0")
PORT = env_int("LEADGEN_PORT", 8000)

# ── داده‌ها ───────────────────────────────────────────────────────────────────
DATA_DIR = (ROOT / (env("LEADGEN_DATA_DIR", "data") or "data")).resolve()
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "leadgen.db"

# ── پایگاه‌دادهٔ ابری (Supabase/Postgres) ────────────────────────────────────
# LEADGEN_DB=supabase یا صرفاً وجود SUPABASE_URL → داده‌ها در Postgres ابری
# نگه داشته می‌شوند تا اپ روی Vercel و ورکر داخل Codespace یک دادهٔ مشترک ببینند.
SUPABASE_URL = env("SUPABASE_URL").rstrip("/")
SUPABASE_SERVICE_KEY = env("SUPABASE_SERVICE_KEY")
SUPABASE_ANON_KEY = env("SUPABASE_ANON_KEY")
SUPABASE_SCHEMA = env("SUPABASE_SCHEMA", "public") or "public"
SUPABASE_KEY = SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY


def _default_db_backend() -> str:
    explicit = env("LEADGEN_DB").lower()
    if explicit in ("sqlite", "supabase", "postgres", "pg"):
        return "supabase" if explicit != "sqlite" else "sqlite"
    return "supabase" if (SUPABASE_URL and SUPABASE_KEY) else "sqlite"


DB_BACKEND = _default_db_backend()


def supabase_ready() -> bool:
    return bool(DB_BACKEND == "supabase" and SUPABASE_URL and SUPABASE_KEY)


# ── صف اجراها (ورکر جدا از وب) ───────────────────────────────────────────────
# auto    = وقتی پایگاه‌داده ابری است یا ورکر جدا خواسته شده، اجراها به صف می‌روند
# always  = همیشه صف (حالت سرورلس: Vercel)
# never   = اجرای همزمان داخل همان فرایند (حالت محلی/تک‌کاربره)
QUEUE_MODE = (env("LEADGEN_QUEUE", "auto").lower() or "auto")
WORKER_ID = env("WORKER_ID") or f"worker-{os.uname().nodename if hasattr(os, 'uname') else 'local'}"
WORKER_POLL_SECONDS = max(1, env_int("WORKER_POLL_SECONDS", 5))
WORKER_MAX_JOBS = env_int("WORKER_MAX_JOBS", 0)
WORKER_MAX_ATTEMPTS = max(1, env_int("WORKER_MAX_ATTEMPTS", 3))
APP_BASE_URL = env("APP_BASE_URL").rstrip("/")


def queue_enabled() -> bool:
    if QUEUE_MODE == "always":
        return True
    if QUEUE_MODE == "never":
        return False
    return supabase_ready() or env("LEADGEN_WORKER") == "1"


# ── تحویل خروجی (ایمیل) ──────────────────────────────────────────────────────
DIGEST_TO = [x.strip() for x in env("DIGEST_TO").split(",") if x.strip()]
DIGEST_FROM = env("DIGEST_FROM") or env("GMAIL_SENDER") or env("SMTP_USER")
DIGEST_SUBJECT = env("DIGEST_SUBJECT") or "گزارش لیدهای داغ — LeadGen"
DIGEST_MAX_LEADS = max(1, env_int("DIGEST_MAX_LEADS", 12))
GMAIL_SENDER = env("GMAIL_SENDER")
GMAIL_APP_PASSWORD = env("GMAIL_APP_PASSWORD")
GMAIL_REFRESH_TOKEN = env("GMAIL_REFRESH_TOKEN")
GMAIL_CLIENT_ID = env("GMAIL_CLIENT_ID")
GMAIL_CLIENT_SECRET = env("GMAIL_CLIENT_SECRET")
SMTP_HOST = env("SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = env_int("SMTP_PORT", 587)
SMTP_USER = env("SMTP_USER") or GMAIL_SENDER
SMTP_PASSWORD = env("SMTP_PASSWORD") or GMAIL_APP_PASSWORD


def gmail_credentials() -> dict:
    """اعتبارنامهٔ Gmail API — زنده از محیط خوانده می‌شود (بدون نیاز به ری‌استارت)."""
    return {"sender": env("GMAIL_SENDER") or GMAIL_SENDER,
            "app_password": env("GMAIL_APP_PASSWORD") or GMAIL_APP_PASSWORD,
            "refresh_token": env("GMAIL_REFRESH_TOKEN") or GMAIL_REFRESH_TOKEN,
            "client_id": env("GMAIL_CLIENT_ID") or GMAIL_CLIENT_ID,
            "client_secret": env("GMAIL_CLIENT_SECRET") or GMAIL_CLIENT_SECRET}


def smtp_credentials() -> dict:
    """اعتبارنامهٔ SMTP (پیش‌فرض: Gmail با رمز برنامه)."""
    return {"host": env("SMTP_HOST") or SMTP_HOST,
            "port": env_int("SMTP_PORT", SMTP_PORT),
            "user": env("SMTP_USER") or env("GMAIL_SENDER") or SMTP_USER,
            "password": env("SMTP_PASSWORD") or env("GMAIL_APP_PASSWORD") or SMTP_PASSWORD}


def email_transport() -> str:
    """gmail_api (روی Vercel/ابر) یا smtp (محلی/Codespace) یا none.

    روی توابع serverless فقط `gmail_api` کار می‌کند — خروجی SMTP معمولاً بسته است.
    """
    gmail = gmail_credentials()
    if gmail["refresh_token"] and gmail["client_id"] and gmail["client_secret"]:
        return "gmail_api"
    smtp = smtp_credentials()
    if smtp["user"] and smtp["password"]:
        return "smtp"
    return "none"


def digest_recipients() -> list[str]:
    """گیرنده‌های گزارش ایمیلی (زنده از `DIGEST_TO`)."""
    live = [x.strip() for x in env("DIGEST_TO").split(",") if x.strip()]
    return live or list(DIGEST_TO)


def digest_from() -> str:
    """آدرس فرستندهٔ گزارش."""
    return env("DIGEST_FROM") or env("GMAIL_SENDER") or env("SMTP_USER") or DIGEST_FROM


def app_base_url() -> str:
    """نشانی اپ برای لینک‌های داخل ایمیل — بدون اسلش پایانی."""
    return (env("APP_BASE_URL") or APP_BASE_URL or "").rstrip("/")


# ── غنی‌سازی ─────────────────────────────────────────────────────────────────
ENRICH_WORKERS = env_int("ENRICH_WORKERS", 8)

USER_AGENT = "leadgen-studio/1.0 (built on Mahanaicoach/google-maps-scraper-kit)"

# ۸ فیلد اصلی لید (مطابق اسکیل کیت) — بقیه ۲۶ ستون خام به‌صورت پیش‌فرض دور ریخته می‌شوند
LEAD_FIELDS = [
    "title",
    "phone",
    "emails",
    "website",
    "category",
    "address",
    "review_rating",
    "review_count",
]
SOCIAL_FIELDS = ["instagram", "facebook", "linkedin"]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def summary() -> dict:
    """وضعیت فعلی تنظیمات — برای نمایش در داشبورد و خروجی `--doctor`."""
    return {
        "engine_mode": ENGINE_MODE,
        "scraper_base_url": SCRAPER_BASE_URL,
        "scraper_api_key_set": bool(SCRAPER_API_KEY),
        "proxies": len(SCRAPER_PROXIES),
        "host": HOST,
        "port": PORT,
        "data_dir": str(DATA_DIR),
        "enrich_workers": ENRICH_WORKERS,
        "lead_fields": LEAD_FIELDS,
        # لایهٔ عملیاتی
        "db_backend": DB_BACKEND,
        "supabase_url": SUPABASE_URL,
        "supabase_ready": supabase_ready(),
        "queue_mode": QUEUE_MODE,
        "queue_enabled": queue_enabled(),
        "worker_id": WORKER_ID,
        "app_base_url": APP_BASE_URL,
        "email_transport": email_transport(),
        "digest_to": DIGEST_TO,
        # موتورهای واقعی و محافظ‌ها
        "osm_endpoint": OSM_ENDPOINT,
        "places_key_set": bool(PLACES_API_KEY),
        "self_drive": SELF_DRIVE,
        "access_code_set": bool(ACCESS_CODE),
        "max_pending": MAX_PENDING_JOBS,
        "rate_runs_per_hour": RATE_RUNS_PER_HOUR,
    }
