"""موتورها: درایور موتور واقعی (داکر) و موتور شبیه‌ساز داخلی (دمو).

موتور واقعی دقیقاً همان قرارداد اسکیل کیت را دنبال می‌کند:
    POST /api/v1/jobs      → {"id": "..."}     (۴۲۲ بدون keywords / lat / lon / max_time)
    GET  /api/v1/jobs/{id} → {"Status": "working" | "ok" | "failed"}
    GET  /api/v1/jobs/{id}/download → CSV با ۳۴ ستون خام

موتور دمو برای وقتی است که داکر/اینترنت در دسترس نیست: همان ستون‌ها را با داده
واقع‌نمای تصادفیِ قطعی (seeded) تولید می‌کند تا کل پایپ‌لاین — غنی‌سازی، امتیازدهی،
کمپین و خروجی فایل — بدون وابستگی بیرونی قابل تست و نمایش باشد.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

from . import config
from .geo import NEIGHBORHOODS

ProgressFn = Callable[[dict], None]

# ۳۴ ستون خام خروجی موتور (مطابق مستندات اسکیل)
RAW_COLUMNS = [
    "input_id", "link", "title", "category", "address", "open_hours", "popular_times",
    "website", "phone", "plus_code", "review_count", "review_rating", "reviews_per_rating",
    "latitude", "longitude", "cid", "status", "descriptions", "reviews_link", "thumbnail",
    "timezone", "price_range", "data_id", "place_id", "images", "reservations",
    "order_online", "menu", "owner", "complete_address", "about", "user_reviews",
    "user_reviews_extended", "emails",
]


class EngineError(RuntimeError):
    pass


class Engine(Protocol):
    name: str
    live: bool

    def health(self) -> dict: ...

    def run_job(self, body: dict, on_progress: ProgressFn | None = None) -> tuple[str, list[dict]]: ...


@dataclass
class JobResult:
    job_id: str
    rows: list[dict]
    engine: str
    live: bool


# ─────────────────────────────────────────────────────────────────────────────
# ۱) موتور واقعی: gosom/google-maps-scraper داخل داکر روی localhost
# ─────────────────────────────────────────────────────────────────────────────
class DockerEngine:
    name = "docker"
    live = True

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or config.SCRAPER_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else config.SCRAPER_API_KEY

    # ── لایه HTTP ────────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, body: dict | None = None, timeout: int = 60):
        headers = {"Content-Type": "application/json", "User-Agent": config.USER_AGENT}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read()

    def health(self) -> dict:
        try:
            status, raw = self._request("GET", "/api/v1/jobs", timeout=6)
            jobs = json.loads(raw.decode("utf-8", "replace") or "[]")
            return {
                "up": True,
                "engine": self.name,
                "base_url": self.base_url,
                "jobs": len(jobs) if isinstance(jobs, list) else 0,
                "http_status": status,
            }
        except Exception as exc:  # connection refused / timeout / 4xx
            return {
                "up": False,
                "engine": self.name,
                "base_url": self.base_url,
                "error": f"{type(exc).__name__}: {exc}",
                "hint": "docker compose up -d  (سپس ~۱۰ ثانیه صبر کنید)",
            }

    def create_job(self, body: dict) -> str:
        required = ("keywords", "lat", "lon", "max_time")
        # مقدار باید پر باشد: رشته/فهرست خالی یا None یعنی API خطای 422 می‌دهد
        missing = [key for key in required
                   if body.get(key) is None or body.get(key) == "" or body.get(key) == []]
        if missing:
            raise EngineError(f"فیلدهای الزامی job ناقص است: {', '.join(missing)} (API خطای 422 می‌دهد)")
        payload = dict(body)
        # قرارداد اسکیل: lat/lon رشته، max_time ثانیه، email پیش‌فرض روشن
        payload["lat"] = str(payload["lat"])
        payload["lon"] = str(payload["lon"])
        payload.setdefault("name", "leadgen-studio")
        payload.setdefault("lang", "en")
        payload.setdefault("zoom", 15)
        payload.setdefault("fast_mode", False)
        payload.setdefault("radius", 10000)
        payload.setdefault("depth", 5)
        payload.setdefault("email", True)
        payload.setdefault("max_time", 600)
        if config.SCRAPER_PROXIES and not payload.get("proxies"):
            payload["proxies"] = config.SCRAPER_PROXIES
        try:
            _, raw = self._request("POST", "/api/v1/jobs", payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise EngineError(f"ساخت job ناموفق: HTTP {exc.code} — {detail}") from exc
        job_id = json.loads(raw.decode("utf-8", "replace")).get("id")
        if not job_id:
            raise EngineError("ساخت job: شناسه‌ای برگردانده نشد.")
        return job_id

    def poll(self, job_id: str, on_progress: ProgressFn | None = None,
             interval: int = 8, attempts: int = 120) -> str:
        status = None
        for attempt in range(1, attempts + 1):
            _, raw = self._request("GET", f"/api/v1/jobs/{job_id}")
            try:
                status = json.loads(raw.decode("utf-8", "replace")).get("Status")
            except json.JSONDecodeError:
                status = None
            if on_progress:
                on_progress({
                    "stage": "poll",
                    "status": status or "working",
                    "attempt": attempt,
                    "message": f"وضعیت job: {status or '?'} (تلاش {attempt}/{attempts})",
                })
            if status == "ok":
                return "ok"
            if status == "failed":
                raise EngineError(
                    "job شکست خورد. اگر تکرار می‌شود احتمالاً IP شما موقتاً محدود شده — "
                    "کمی صبر کنید، depth را کم کنید یا پراکسی اضافه کنید."
                )
            time.sleep(interval)
        raise EngineError("job در مهلت مقرر تمام نشد (timeout). depth را کم یا max_time را زیاد کنید.")

    def download(self, job_id: str) -> list[dict]:
        _, raw = self._request("GET", f"/api/v1/jobs/{job_id}/download", timeout=180)
        text = raw.decode("utf-8", "replace")
        return list(csv.DictReader(io.StringIO(text)))

    def delete_job(self, job_id: str) -> bool:
        try:
            self._request("DELETE", f"/api/v1/jobs/{job_id}")
            return True
        except Exception:
            return False

    def list_jobs(self) -> list[dict]:
        try:
            _, raw = self._request("GET", "/api/v1/jobs")
            data = json.loads(raw.decode("utf-8", "replace") or "[]")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def run_job(self, body: dict, on_progress: ProgressFn | None = None) -> tuple[str, list[dict]]:
        if on_progress:
            on_progress({"stage": "create", "status": "creating",
                         "message": f"ساخت job با {len(body.get('keywords', []))} کلیدواژه"})
        job_id = self.create_job(body)
        if on_progress:
            on_progress({"stage": "created", "status": "working", "job_id": job_id,
                         "message": f"job ساخته شد: {job_id}"})
        self.poll(job_id, on_progress)
        if on_progress:
            on_progress({"stage": "download", "status": "ok", "job_id": job_id,
                         "message": "دانلود CSV خام…"})
        return job_id, self.download(job_id)


# ─────────────────────────────────────────────────────────────────────────────
# ۲) موتور دمو: شبیه‌ساز آفلاین برای همه اصناف و همه شهرها
# ─────────────────────────────────────────────────────────────────────────────
# قالب‌های نام کسب‌وکار (فارسی و انگلیسی)
_NAME_TEMPLATES_FA = [
    "{biz} {name}", "{name} {biz}", "{biz} {name} ({hood})", "گروه {biz} {name}",
    "{biz} تخصصی {name}", "{name} | {biz}", "{biz} {name} شعبه {hood}",
]
_NAME_TEMPLATES_EN = [
    "{name} {biz}", "{name} {biz} Co.", "{biz} by {name}", "The {biz} Studio",
    "{name} & Partners {biz}", "{biz} {name} ({hood})",
]
_FAMILY_FA = ["پارسا", "آریا", "نگین", "سپهر", "دریا", "کیان", "مهر", "رها", "سهند", "تیدا",
              "بامداد", "شایان", "پرنیان", "رادین", "هیراد", "مانا", "ویانا", "آروین", "دیاکو", "نیلا",
              "کارن", "بهار", "سیاوش", "گلنار", "فرهاد", "مهسا", "امیر", "سارا", "رضا", "مریم",
              "حسین", "زهرا", "علی", "فاطمه", "محمد", "نرگس", "یاسمن", "پویا", "شیدا", "آرش"]
_FAMILY_EN = ["Atlas", "Nova", "Vertex", "Summit", "Harbor", "Maple", "Cedar", "Bright",
              "Iron", "Silver", "Golden", "Prime", "Urban", "North", "West", "Elite",
              "Pacific", "Riverside", "Oakwood", "Skyline"]
_STREETS_FA = ["خیابان ولیعصر", "خیابان شریعتی", "بلوار کشاورز", "خیابان انقلاب", "خیابان آزادی",
               "بلوار دریا", "خیابان مطهری", "خیابان بهشتی", "بلوار ناهارخوران", "خیابان امام خمینی",
               "خیابان سهروردی", "بلوار گلستان", "خیابان کارگر", "خیابان حافظ", "کوچه گل"]
_STREETS_EN = ["Main St", "Oak Ave", "Market St", "2nd Ave", "Park Blvd", "River Rd",
               "King St", "Queen St", "Hill Rd", "Lake Ave", "Church St", "Union Blvd"]
_CATEGORIES_HINT = "category"


class DemoEngine:
    """شبیه‌ساز خروجی موتور — داده واقع‌نما برای هر صنف و هر شهر.

    چرا لازم است؟ در محیطی که داکر یا دسترسی به گوگل نیست، برنامه باید همچنان
    «خروجی قابل استفاده» تولید کند تا کاربر کل جریان (لید → امتیاز → کمپین → فایل) را ببیند.
    داده‌ها با seed قطعی ساخته می‌شوند، بنابراین اجرای مجدد با همان ورودی‌ها همان نتیجه را می‌دهد.
    """

    name = "demo"
    live = False

    def __init__(self, seed_salt: str = ""):
        self.seed_salt = seed_salt

    def health(self) -> dict:
        return {
            "up": True,
            "engine": self.name,
            "base_url": "internal://demo",
            "note": "موتور شبیه‌ساز داخلی — برای تست پایپ‌لاین بدون داکر/اینترنت",
        }

    # ── تولید داده ───────────────────────────────────────────────────────────
    def _seed(self, *parts) -> int:
        blob = "|".join(str(p) for p in parts) + "|" + self.seed_salt
        return int(hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12], 16)

    def _rows_for_keyword(self, keyword: str, body: dict, rnd: random.Random,
                          index: int) -> list[dict]:
        depth = int(body.get("depth", 5))
        # depth ≈ تعداد ردیف در هر کلیدواژه (موتور واقعی حدود ۲۰-۲۴ ردیف به ازای هر depth)
        target = max(3, min(120, depth * rnd.randint(16, 24)))
        lang_fa = bool(body.get("lang_fa")) or bool(re.search(r"[\u0600-\u06FF]", keyword))
        city_label = str(body.get("city_label", "")).strip() or "City"
        city_key = body.get("city_key") or ""
        country = body.get("country", "US")
        lat0, lon0 = float(body["lat"]), float(body["lon"])
        bbox = body.get("bbox") or [lat0 - .1, lat0 + .1, lon0 - .1, lon0 + .1]
        niche = body.get("_niche")
        category = getattr(niche, "fa" if lang_fa else "en", keyword.split(" in ")[0].title())

        hoods = list(NEIGHBORHOODS.get(city_key, []))
        if not hoods:
            hoods = ["مرکز شهر", "شمال شهر", "خیابان اصلی"] if lang_fa else \
                    ["Downtown", "Midtown", "Riverside", "Old Town", "North Side"]
        family = _FAMILY_FA if lang_fa else _FAMILY_EN
        templates = _NAME_TEMPLATES_FA if lang_fa else _NAME_TEMPLATES_EN

        rows: list[dict] = []
        used_names: set[str] = set()
        for i in range(target):
            biz = category if lang_fa else keyword.split(" in ")[0].rstrip("s")
            name = rnd.choice(family)
            for _try in range(6):
                title = rnd.choice(templates).format(biz=biz, name=name,
                                                     hood=rnd.choice(hoods)).strip()
                if title not in used_names:
                    break
                name = rnd.choice(family)
            used_names.add(title)

            lat = rnd.uniform(bbox[0], bbox[1])
            lon = rnd.uniform(bbox[2], bbox[3])
            rating = round(rnd.uniform(2.6, 5.0), 1)
            review_count = int(max(0, rnd.triangular(0, 900, 45)))
            has_site = rnd.random() < 0.62
            has_phone = rnd.random() < 0.94
            domain = self._domain(title, lang_fa, rnd)
            website = f"https://{domain}" if has_site else ""
            emails = ""
            if has_site:
                if rnd.random() < 0.55:
                    local = rnd.choice(["info", "contact", "hello", "office", "mail", "support"])
                    emails = f"{local}@{domain}"
            elif rnd.random() < 0.18:
                emails = f"{rnd.choice(['info', 'contact'])}@{rnd.choice(['gmail.com', 'yahoo.com'])}"

            phone = self._phone(country, rnd) if has_phone else ""
            # نشانی: برای شهرهای غیرایرانی نام خیابان لاتین می‌ماند (حتی در خروجی فارسی)
            use_fa_streets = lang_fa and (country in ("IR", "") or not country)
            street = rnd.choice(_STREETS_FA if use_fa_streets else _STREETS_EN)
            if use_fa_streets:
                address = f"{street}، {rnd.choice(hoods)}، {city_label}"
            elif lang_fa:
                address = f"{street}، {rnd.choice(hoods)}، {city_label}"
            else:
                address = f"{rnd.randint(1, 900)} {street}, {city_label}"
            price = rnd.choice(["", "", "$", "$$", "$$$"]) if not lang_fa else \
                rnd.choice(["", "", "ارزان", "متوسط", "گران"])
            cid = f"{self._seed(title, i, index):d}"
            place_id = "ChIJ" + hashlib.sha1(f"{title}{lat:.4f}{lon:.4f}".encode()).hexdigest()[:24]

            rows.append({
                "input_id": keyword,
                "link": f"https://www.google.com/maps/place/?q=place_id:{place_id}",
                "title": title,
                "category": category,
                "address": address,
                "open_hours": json.dumps({"text": self._open_hours(lang_fa, rnd)},
                                         ensure_ascii=False),
                "popular_times": "",
                "website": website,
                "phone": phone,
                "plus_code": f"{abs(round(lat, 2)):.2f}{'N' if lat >= 0 else 'S'}+"
                             f"{abs(round(lon, 2)):.2f}{'E' if lon >= 0 else 'W'}",
                "review_count": str(review_count),
                "review_rating": str(rating) if review_count > 0 else "",
                "reviews_per_rating": "",
                "latitude": f"{lat:.6f}",
                "longitude": f"{lon:.6f}",
                "cid": cid,
                "status": "ok",
                "descriptions": "",
                "reviews_link": "",
                "thumbnail": "",
                "timezone": body.get("timezone", "Asia/Tehran" if country == "IR" else "UTC"),
                "price_range": price,
                "data_id": cid,
                "place_id": place_id,
                "images": "",
                "reservations": "true" if rnd.random() < 0.18 else "false",
                "order_online": "true" if rnd.random() < 0.2 else "false",
                "menu": "true" if rnd.random() < 0.25 else "false",
                "owner": "",
                "complete_address": json.dumps({"street": street, "city": city_label},
                                               ensure_ascii=False),
                "about": "",
                "user_reviews": "",
                "user_reviews_extended": "",
                "emails": emails,
                # فیلدهای غنی‌سازی که در حالت دمو محلی ساخته می‌شوند (به‌جای واکشی سایت)
                "instagram": self._handle(domain, has_site, rnd, 0.34),
                "facebook": self._handle(domain, has_site, rnd, 0.28),
                "linkedin": self._handle(domain, has_site, rnd, 0.16),
                "_demo": True,
            })
        return rows

    @staticmethod
    def _open_hours(lang_fa: bool, rnd: random.Random) -> list[str]:
        """ساعت کاری واقع‌نما (برای ستون خام open_hours)."""
        days = ("شنبه: 9:00–21:00", "یکشنبه: 9:00–21:00", "دوشنبه: 9:00–21:00",
                "سه‌شنبه: 9:00–21:00", "چهارشنبه: 9:00–21:00", "پنجشنبه: 9:00–18:00") \
            if lang_fa else \
            ("Monday: 9:00 AM–6:00 PM", "Tuesday: 9:00 AM–6:00 PM",
             "Wednesday: 9:00 AM–6:00 PM", "Thursday: 9:00 AM–6:00 PM",
             "Friday: Closed", "Saturday: 10:00 AM–4:00 PM")
        return list(days) if rnd.random() < 0.85 else []

    @staticmethod
    def _domain(title: str, lang_fa: bool, rnd: random.Random) -> str:
        import re as _re
        base = _re.sub(r"[^\w]+", "", title if not lang_fa else "site").lower() or "biz"
        base = base[:14] or "biz"
        tld = rnd.choice([".ir", ".com"]) if lang_fa else rnd.choice([".com", ".net", ".co", ".io"])
        suffix = rnd.choice(["", "", "group", "co", "official", rnd.choice(["24", "pro", "plus"])])
        return f"{base}{suffix}{tld}"

    @staticmethod
    def _handle(domain: str, has_site: bool, rnd: random.Random, chance: float) -> str:
        if not has_site or rnd.random() > chance:
            return ""
        root = domain.split(".")[0]
        return root + str(rnd.randint(1, 99)) if rnd.random() < 0.3 else root

    @staticmethod
    def _phone(country: str, rnd: random.Random) -> str:
        if country == "IR":
            kind = rnd.random()
            if kind < 0.6:
                return f"+98 9{rnd.randint(10, 39)} {rnd.randint(100, 999)} {rnd.randint(1000, 9999)}"
            return f"+98 21 {rnd.randint(2000, 9999)} {rnd.randint(1000, 9999)}"
        if country == "DE":
            return f"+49 {rnd.randint(30, 89)} {rnd.randint(1000000, 9999999)}"
        if country == "AE":
            return f"+971 4 {rnd.randint(2000000, 9999999)}"
        return f"+1 ({rnd.randint(201, 989)}) {rnd.randint(200, 999)}-{rnd.randint(1000, 9999)}"

    # ── اجرای job ────────────────────────────────────────────────────────────
    def run_job(self, body: dict, on_progress: ProgressFn | None = None) -> tuple[str, list[dict]]:
        keywords = list(body.get("keywords") or [])
        if not keywords:
            raise EngineError("هیچ کلیدواژه‌ای داده نشد.")
        seed = self._seed(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str))
        rnd = random.Random(seed)
        job_id = hashlib.sha1(str(seed).encode()).hexdigest()[:8]
        job_id = f"demo-{job_id}-{''.join(rnd.choice('0123456789abcdef') for _ in range(4))}"

        rows: list[dict] = []
        steps = max(1, len(keywords)) * 3
        for index, keyword in enumerate(keywords):
            if on_progress:
                on_progress({
                    "stage": "working",
                    "status": "working",
                    "job_id": job_id,
                    "percent": int(100 * (index + 0.2) / max(1, len(keywords))),
                    "message": f"[دمو] جست‌وجو: «{keyword}» ({index + 1}/{len(keywords)})",
                })
            rows.extend(self._rows_for_keyword(keyword, body, rnd, index))
            time.sleep(min(0.35, 1.0 / steps))  # شبیه‌سازی ریتم اجرا بدون کند کردن UI

        if on_progress:
            on_progress({"stage": "ok", "status": "ok", "job_id": job_id, "percent": 100,
                         "message": f"[دمو] job تمام شد — {len(rows)} ردیف خام"})
        return job_id, rows


# ─────────────────────────────────────────────────────────────────────────────
# انتخاب موتور
# ─────────────────────────────────────────────────────────────────────────────
def make_engine(mode: str | None = None, base_url: str | None = None) -> Engine:
    """`auto` → اگر موتور داکر بالا بود واقعی، وگرنه دمو (با پیام روشن)."""
    mode = (mode or config.ENGINE_MODE or "auto").lower()
    docker = DockerEngine(base_url=base_url)
    if mode == "demo":
        return DemoEngine()
    if mode == "docker":
        return docker
    return docker if docker.health().get("up") else DemoEngine()


def engine_status(mode: str | None = None) -> dict:
    """وضعیت کامل موتور برای داشبورد: کدام فعال است و چرا."""
    docker = DockerEngine()
    docker_health = docker.health()
    active_mode = (mode or config.ENGINE_MODE or "auto").lower()
    if active_mode == "demo":
        active = "demo"
        reason = "ENGINE_MODE=demo در تنظیمات"
    elif active_mode == "docker":
        active = "docker" if docker_health.get("up") else "unavailable"
        reason = "موتور واقعی" if docker_health.get("up") else docker_health.get("error", "در دسترس نیست")
    else:
        active = "docker" if docker_health.get("up") else "demo"
        reason = ("موتور داکر روی " + docker_health["base_url"]) if docker_health.get("up") \
            else "داکر/موتور در دسترس نبود → حالت دمو (داده نمونه، پایپ‌لاین کامل)"
    return {
        "mode": active_mode,
        "active": active,
        "live": active == "docker",
        "reason": reason,
        "docker": docker_health,
        "base_url": config.SCRAPER_BASE_URL,
        "proxies": len(config.SCRAPER_PROXIES),
    }
