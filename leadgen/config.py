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
    "LEADGEN_HOST",
    "LEADGEN_PORT",
    "LEADGEN_DATA_DIR",
    "ENRICH_WORKERS",
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


# ── موتور اسکراپر ─────────────────────────────────────────────────────────────
SCRAPER_PORT = env_int("SCRAPER_PORT", 8080)
SCRAPER_BASE_URL = env("SCRAPER_BASE_URL") or f"http://localhost:{SCRAPER_PORT}"
SCRAPER_API_KEY = env("SCRAPER_API_KEY")
SCRAPER_PROXIES = [p.strip() for p in env("SCRAPER_PROXIES").split(",") if p.strip()]

# auto | docker | demo
ENGINE_MODE = env("ENGINE_MODE", "auto").lower() or "auto"

# ── سرویس وب ──────────────────────────────────────────────────────────────────
HOST = env("LEADGEN_HOST", "0.0.0.0")
PORT = env_int("LEADGEN_PORT", 8000)

# ── داده‌ها ───────────────────────────────────────────────────────────────────
DATA_DIR = (ROOT / (env("LEADGEN_DATA_DIR", "data") or "data")).resolve()
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "leadgen.db"

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
    }
