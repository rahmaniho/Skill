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

from . import config, niches
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


class EmptyResult(EngineError):
    """موتور واقعی پاسخ داد ولی چیزی پیدا نشد (نه خطا؛ «خالی»).

    تفاوت مهم: خالی بودن نتیجه در یک محله یا شهر کوچک طبیعی است و نباید اجرا را
    «ناموفق» و سه بار تکرار کند. پایپ‌لاین این خطا را به اجرای موفق با صفر لید
    تبدیل می‌کند و پیشنهاد می‌دهد محدوده را بزرگ‌تر یا صنف را عوض کنند.
    """

    def __init__(self, message: str, tried: list[str] | None = None):
        super().__init__(message)
        self.tried = tried or []


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
def _api_body(body: dict) -> dict:
    """بدنهٔ ارسالی به API موتور را تمیز می‌کند.

    پایپ‌لاین چند کلید داخلی با پیشوند `_` (مثل `_niche` که یک شیء است، نه JSON) در
    بدنه می‌گذارد تا موتور دمو از آن استفاده کند. موتور واقعی باید آن‌ها را نبیند، وگرنه
    `json.dumps` با TypeError می‌شکند و **هر اجرای واقعی** شکست می‌خورد.
    """
    from .cloud import jsonable
    return {key: jsonable(value) for key, value in (body or {}).items()
            if not str(key).startswith("_")}


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
        payload = _api_body(body)
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
        # در jobهای چندصنفی (جاروی «تمامی مشاغل») هر کلیدواژه صنف خودش را دارد
        per_keyword = (body.get("_keyword_niches") or {}).get(keyword)
        if per_keyword:
            niche = niches.get(per_keyword) or niche
        category = getattr(niche, "fa" if lang_fa else "en", keyword.split(" in ")[0].title())

        # اگر کاربر منطقه/خیابان مشخصی انتخاب کرده، نشانی‌ها همان‌جا ساخته می‌شوند
        area_hood = str(body.get("area_hood") or "").strip()
        # city_label می‌تواند خودش شامل نام منطقه باشد («چیتگر، تهران») → حذف تکرار
        city_short = city_label
        if area_hood and city_short:
            trimmed = (city_short.replace(f"{area_hood}، ", "").replace(f"{area_hood}, ", "")
                       .replace(area_hood, "").strip(" ،,"))
            city_short = trimmed or city_label
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
                hood = area_hood if (area_hood and rnd.random() < 0.75) else rnd.choice(hoods)
                title = rnd.choice(templates).format(biz=biz, name=name, hood=hood).strip()
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
            hood = area_hood if (area_hood and rnd.random() < 0.75) else rnd.choice(hoods)
            place_label = city_short if area_hood else city_label
            if use_fa_streets or lang_fa:
                address = f"{street}، {hood}، {place_label}"
            else:
                address = f"{rnd.randint(1, 900)} {street}, {place_label}"
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
        # کلیدهای داخلیِ مخصوص موتورهای واقعی نباید خروجی دمو را عوض کنند؛
        # وگرنه با هر تغییر کوچک در پایپ‌لاین، دادهٔ شبیه‌سازی‌شده عوض می‌شود.
        seed_body = {k: v for k, v in body.items()
                     if k not in ("osm_terms", "province_label", "province_bbox",
                                  "all_business", "tag_filters", "sweep_boxes")}
        seed = self._seed(json.dumps(seed_body, sort_keys=True, ensure_ascii=False, default=str))
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
# موتورهای واقعیِ بدون داکر (OSM / Google Places) — برای اجرای زنده روی Vercel
# ─────────────────────────────────────────────────────────────────────────────
def share_for_box(box_left: float, phase_budget: float, remaining: int,
                  single: bool) -> float:
    """سهم زمانی Overpass برای یک شهر — با ذخیرهٔ زمان برای مسیر متنی.

    چرا؟ Overpass از شبکهٔ سرورلس اغلب پاسخ نمی‌دهد و اگر تمام بودجهٔ شهر را
    بخورد، جست‌وجوی متنی (که سریع و پایدار است) هرگز اجرا نمی‌شود؛ نتیجه: صفر لید
    در شهری که داده دارد. پس همیشه چند ثانیه برای مسیر جایگزین کنار می‌گذاریم.
    """
    reserve = 12.0 if single else 6.0
    ceiling = max(8.0, float(box_left) - reserve)
    if single:
        return max(8.0, min(ceiling, float(phase_budget), float(box_left) * 0.55))
    return max(8.0, min(ceiling, float(phase_budget) / max(1, remaining) * 1.15))


class OSMEngine:
    """دادهٔ واقعی OpenStreetMap (Nominatim) — بدون داکر و بدون کلید."""

    name = "osm"
    live = True

    def __init__(self) -> None:
        # گزارش پوشش: کدام شهر/محدوده است، هر کدام چند ردیف و از کدام مسیر.
        # (لایهٔ بالاتر با این گزارش صادقانه می‌گوید کدام شهر داده نداشت.)
        self.coverage: list[dict] = []
        self.attempts: list[dict] = []      # هر تلاش جست‌وجوی متنی: {term, rows, city}

    def health(self) -> dict:
        from .providers import osm
        return osm.health()

    def run_job(self, body: dict, on_progress: ProgressFn | None = None) -> tuple[str, list[dict]]:
        from .providers import osm
        from .providers.osm import OSMDownError

        started = time.time()
        budget = max(15.0, float(body.get("max_time") or 60))
        lang = str(body.get("lang") or "fa")
        place = str(body.get("city_label") or "")
        keywords = [k for k in (body.get("keywords") or []) if str(k).strip()]
        terms: list[str] = []
        for kw in keywords:
            term = osm.clean_term(str(kw), place)
            if term and term.lower() not in [t.lower() for t in terms]:
                terms.append(term)
        # اگر رابط انگلیسی بود، نام فارسی صنف هم اضافه می‌شود؛ دادهٔ OSM ایران
        # بیشتر فارسی است و همین یک نکته تفاوت «صفر نتیجه» و «نتیجهٔ واقعی» است.
        niche_obj = body.get("_niche")
        if not str(lang).startswith("fa") and niche_obj is not None:
            for extra in [getattr(niche_obj, "fa", "")] + list(getattr(niche_obj, "keywords_fa", [])[:2]):
                extra = osm.clean_term(str(extra or ""), place)
                if extra and extra.lower() not in [t.lower() for t in terms]:
                    terms.append(extra)
        # معادل‌های دسته‌ای انگلیسی (راز اصلی بازدهی): Nominatim با «dentist» در
        # تهران ۴۰+ نتیجه می‌دهد و با «دندانپزشکی» ۱ نتیجه.
        category_terms = [str(t).strip() for t in (body.get("osm_terms") or []) if str(t).strip()]
        if not category_terms:
            from . import osm_terms as lexicon

            seen_texts = [str(getattr(niche_obj, "fa", "") or "")] + list(keywords[:2])
            category_terms = lexicon.derive(seen_texts)
        for term in reversed(category_terms[:4]):
            if term.lower() not in [t.lower() for t in terms]:
                terms.insert(0, term)       # دسته‌ای‌ها اول امتحان می‌شوند
        if not terms:
            raise EngineError("برای موتور OSM هیچ کلیدواژه‌ای نبود.")
        terms = terms[:9]

        bbox = body.get("bbox")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            try:
                lat = float(body.get("lat"))
                lon = float(body.get("lon"))
                bbox = osm._bbox_from_radius(lat, lon, int(body.get("radius") or 10000))
            except (TypeError, ValueError):
                bbox = None
        country = str(body.get("country") or "").strip() or config.OSM_COUNTRY

        rows: list[dict] = []
        errors: list[str] = []
        tried: list[str] = []
        used_overpass = False

        self.coverage = []
        self.attempts = []
        # جعبه‌های جست‌وجو: یا یک جعبهٔ شهر، یا چند شهر برای جست‌وجوی سطح استان
        sweep_boxes: list[dict] = []
        for item in (body.get("sweep_boxes") or []):
            if isinstance(item, dict) and isinstance(item.get("bbox"), (list, tuple)) \
                    and len(item["bbox"]) == 4:
                try:
                    sweep_boxes.append({
                        "label": str(item.get("fa") or item.get("label") or ""),
                        "bbox": [float(x) for x in item["bbox"]],
                        "pop": int(item.get("pop") or 0),
                    })
                except (TypeError, ValueError):
                    continue
        sweep_boxes.sort(key=lambda box: -box["pop"])

        # ── گذر اول: Overpass (تگ‌محور) — صدها کسب‌وکار در یک درخواست ─────────
        want_all = bool(body.get("all_business"))
        if config.OSM_MODE in ("hybrid", "overpass") and bbox:
            from . import osm_tags

            niche_id = str(getattr(niche_obj, "id", "") or "")
            span = max(abs(float(bbox[1]) - float(bbox[0])), abs(float(bbox[3]) - float(bbox[2])))
            with_names = span < 0.6            # جست‌وجوی نام فقط در سطح شهر (نه استان)
            filters: list[str] = []
            if want_all:
                filters = osm_tags.all_business_filters()
            if not filters:
                filters = osm_tags.filters_for_terms(category_terms, with_names=with_names)
            if not filters:
                filters = osm_tags.filters_for_terms(terms, with_names=with_names)
            if not filters and niche_id:
                filters = osm_tags.filters_for_niche(niche_id, with_names=with_names)
            if filters:
                left = budget - (time.time() - started)
                if left >= 6:
                    if on_progress:
                        label = ("همهٔ کسب‌وکارها" if want_all
                                 else f"صنف «{getattr(niche_obj, 'fa', '') or terms[0]}»")
                        on_progress({"stage": "working", "status": "working", "percent": 6,
                                     "job_id": body.get("name") or "osm",
                                     "message": f"[OSM] جست‌وجوی تگ‌محور: {label}"})
                    # در جست‌وجوی «کامل» (depth بالا) یا محدودهٔ بزرگ (استان)، اگر
                    # نتیجه به سقف Overpass بخورد، محدوده به قطعه‌ها شکسته می‌شود.
                    deep = int(body.get("depth") or 0) >= 5 or want_all
                    area_span = max(abs(float(bbox[1]) - float(bbox[0])),
                                    abs(float(bbox[3]) - float(bbox[2])))
                    tiles = 4 if ((deep or area_span > 0.8) and left >= 35) else 1
                    # بودجهٔ این مرحله حداکثر ~۶۰٪ زمانِ باقی‌مانده است تا اگر Overpass
                    # کند بود، مسیر جایگزین (Nominatim) و ذخیره/خروجی هم وقت داشته باشند.
                    phase_budget = max(8.0, min(left - 6, left * 0.6))
                    # یک شهر (حالت عادی) یا چند شهر پرجمعیت (حالت استان)
                    targets: list[tuple[str, list[float]]] = (
                        [(box["label"], box["bbox"]) for box in sweep_boxes]
                        if sweep_boxes else [("", list(bbox))])
                    cap_rows = min(1500, int(config.MAX_LEADS_PER_RUN or 1500))
                    # سهم هر شهر از سقف: پاسخ کوچک‌تر = سریع‌تر و عادلانه‌تر بین شهرها
                    per_box_cap = max(150, cap_rows // max(1, len(targets)))
                    # هر شهر از همان ابتدا در گزارش هست؛ بعداً وضعیتش به‌روز می‌شود.
                    # (تا شهری که به‌خاطر بودجه نوبتش نشد هم در گزارش دیده شود)
                    cov_index: dict[str, dict] = {}
                    attempted: set[str] = set()

                    def _cover(city: str, bbox_value=None, **fields) -> dict:
                        key = city or "شهر"
                        row = cov_index.get(key)
                        if row is None:
                            row = {"city": city, "bbox": bbox_value, "rows": 0,
                                   "via": "", "error": ""}
                            cov_index[key] = row
                            self.coverage.append(row)
                        if bbox_value is not None and not row.get("bbox"):
                            row["bbox"] = bbox_value
                        for key_name, value in fields.items():
                            row[key_name] = value
                        return row

                    for box_label, box in targets:
                        _cover(box_label, box)
                    found_map: dict[str, dict] = {}
                    for box_index, (box_label, box) in enumerate(targets, start=1):
                        box_left = budget - (time.time() - started)
                        if box_left < 6:
                            break
                        remaining = len(targets) - box_index + 1
                        share = share_for_box(box_left, phase_budget, remaining, len(targets) == 1)
                        box_span = max(abs(box[1] - box[0]), abs(box[3] - box[2]))
                        box_tiles = 4 if (deep and share >= 25 and box_span > 0.15) else 1
                        if on_progress and len(targets) > 1:
                            on_progress({"stage": "working", "status": "working",
                                         "percent": 6 + int(16 * (box_index - 1) / len(targets)),
                                         "job_id": body.get("name") or "osm",
                                         "message": f"[OSM] {box_label or 'شهر'} "
                                                    f"({box_index}/{len(targets)}) — جست‌وجوی تگ‌محور"})
                        attempted.add(box_label)
                        try:
                            got = osm.overpass_search_area(
                                filters, bbox=box,
                                limit=min(osm.overpass_limit_for(box), per_box_cap),
                                timeout=min(float(config.OVERPASS_TIMEOUT),
                                            max(6.0, share - 2)),
                                input_id=(niche_id or (terms[0] if terms else "")),
                                lang=lang, tiles=box_tiles,
                                max_rows=per_box_cap, budget_s=share)
                        except OSMDownError as exc:
                            errors.append(f"overpass{' ' + box_label if box_label else ''}: {exc}")
                            _cover(box_label, box, rows=0, via="", error=str(exc)[:160])
                            # اگر سرویس کامل پایین است، امتحان بقیهٔ شهرها فقط وقت می‌گیرد.
                            if not osm.overpass_available():
                                break
                            continue
                        for row in got:
                            found_map.setdefault(row.get("place_id"), row)
                        _cover(box_label, box, rows=len(got), via="overpass", error="")
                        if len(found_map) >= cap_rows:
                            break
                    found = list(found_map.values())
                    if found:
                        used_overpass = True
                        tried.append("overpass")
                        rows.extend(found)
                        if on_progress:
                            on_progress({"stage": "working", "status": "working", "percent": 22,
                                         "job_id": body.get("name") or "osm",
                                         "message": f"[OSM] {len(found)} کسب‌وکار از تگ‌های OSM "
                                                    f"({len(filters)} فیلتر)"})
                    elif on_progress:
                        on_progress({"stage": "working", "status": "working", "percent": 14,
                                     "job_id": body.get("name") or "osm",
                                     "message": "[OSM] نتیجهٔ تگ‌محور خالی بود؛ جست‌وجوی متنی…"})

        # ── تکمیل شهرهایی که Overpass نتوانست بدهد (شبکهٔ ابری، محدودیت نرخ) ──
        # کاربر «کل استان» را خواسته؛ اگر سرویس تگ‌محور برای شهری جواب نداد،
        # همان شهر را با جست‌وجوی متنی Nominatim (محدود به جعبهٔ همان شهر) می‌گردیم.
        if sweep_boxes and config.OSM_MODE != "overpass":
            served = {item.get("city") for item in self.coverage if item.get("rows")}
            missing = [box for box in sweep_boxes if box["label"] not in served]
            for missing_box in missing:
                left = budget - (time.time() - started)
                if left < 8:
                    break
                box = missing_box["bbox"]
                city_name = missing_box["label"]
                if on_progress:
                    on_progress({"stage": "working", "status": "working", "percent": 26,
                                 "job_id": body.get("name") or "osm",
                                 "message": f"[OSM] جست‌وجوی متنی در {city_name} "
                                            f"(Overpass برای این شهر جواب نداد)"})
                found_here = False
                for term in (category_terms[:2] or terms[:2]):
                    left = budget - (time.time() - started)
                    if left < 6:
                        break
                    try:
                        got = osm.search(term, bbox=box, country=country,
                                         limit=config.OSM_LIMIT, timeout=min(12.0, left),
                                         lang=lang, input_id=city_name)
                    except OSMDownError as exc:
                        errors.append(f"nominatim {city_name} «{term}»: {exc}")
                        self.attempts.append({"term": term, "rows": 0, "city": city_name,
                                              "error": type(exc).__name__})
                        continue
                    self.attempts.append({"term": term, "rows": len(got), "city": city_name,
                                          "error": ""})
                    seen_ids = {r.get("place_id") for r in rows}
                    fresh = [row for row in got if row.get("place_id") not in seen_ids]
                    if fresh:
                        rows.extend(fresh)
                        tried.append(f"{term} @ {city_name}")
                        _cover(city_name, box, rows=len(fresh), via="nominatim")
                        found_here = True
                        break
                if not found_here:
                    row = _cover(city_name, box, rows=0, via="")
                    if not row.get("error"):
                        row["error"] = "Overpass و جست‌وجوی متنی برای این شهر نتیجه ندادند"

        # شهرهایی که نوبتشان نشد (بودجهٔ زمانی تمام شد) صریح در گزارش علامت می‌خورند
        for item in self.coverage:
            if not item.get("rows") and not item.get("error"):
                item["error"] = "بودجهٔ زمانی این اجرا به این شهر نرسید"

        strict_overpass = config.OSM_MODE == "overpass"
        # در حالت hybrid: اگر تگ‌ها نتیجهٔ کافی دادند سهمیهٔ Nominatim را نمی‌سوزانیم؛
        # اگر نتیجه کم بود، حداکثر دو عبارت را برای تکمیل امتحان می‌کنیم.
        fallback_left = (2 if 0 < len(rows) < 5 else (0 if rows else len(terms)))
        for index, term in enumerate(terms):
            if strict_overpass and used_overpass:
                break
            if used_overpass and (len(rows) >= 60 or fallback_left <= 0):
                break
            if used_overpass:
                fallback_left -= 1
            left = budget - (time.time() - started)
            if left < 6:
                break
            if on_progress:
                on_progress({"stage": "working", "status": "working", "percent": int(
                    100 * index / max(1, len(terms))),
                    "job_id": body.get("name") or "osm",
                    "message": f"[OSM] جست‌وجو: «{term}» ({index + 1}/{len(terms)})"})
            seen = {r.get("place_id") for r in rows}

            def add(found: list[dict], label: str) -> int:
                """ردیف‌های تازه را اضافه می‌کند و تعداد نتیجهٔ *غیرتکراریِ* این
                جست‌وجو را برمی‌گرداند؛ نتیجهٔ تکراری هم یعنی «این عبارت کار کرد»."""
                tried.append(label)
                fresh = [r for r in found if r.get("place_id") not in seen]
                seen.update(r.get("place_id") for r in fresh)
                rows.extend(fresh)
                return len(found)

            try:
                got = osm.search(term, bbox=bbox, country=country, limit=config.OSM_LIMIT,
                                 timeout=min(12.0, left), lang=lang, input_id=term)
            except OSMDownError as exc:
                errors.append(f"{term}: {exc}")
                got = []
                self.attempts.append({"term": term, "rows": 0, "error": type(exc).__name__})
            else:
                self.attempts.append({"term": term, "rows": len(got), "error": ""})
            if add(got, term):
                continue
            # راه دوم: کلمهٔ کلیدیِ تنهایی — عبارت کامل فارسی در OSM کم نتیجه می‌دهد
            variants: list[str] = []
            if any("\u0600" <= ch <= "\u06FF" for ch in term):
                from . import osm_terms as lexicon

                variants = lexicon.term_variants(term)
            hit = False
            for variant in variants:
                left = budget - (time.time() - started)
                if left < 6:
                    break
                if variant.lower() == term.lower():
                    continue
                try:
                    found = osm.search(variant, bbox=bbox, country=country,
                                       limit=config.OSM_LIMIT, timeout=min(12.0, left),
                                       lang=lang, input_id=variant)
                except OSMDownError as exc:
                    errors.append(f"{variant}: {exc}")
                    continue
                if add(found, variant):
                    hit = True
                    break
            if hit:
                continue
            # راه‌های پهن‌تر فقط وقتی هیچ نتیجه‌ای نداریم اجرا می‌شوند؛
            # وگرنه صرفاً سهمیهٔ Nominatim (۱ درخواست/ثانیه) هدر می‌رود.
            if rows:
                continue
            # راه دوم: جست‌وجوی متنی با نام شهر، بعد فیلتر محدوده (پوشش بیشتر، خطای کمتر)
            if place:
                left = budget - (time.time() - started)
                if left < 6:
                    break
                try:
                    wide = osm.search(f"{term} {place}", country=country,
                                      limit=config.OSM_LIMIT, timeout=min(12.0, left),
                                      lang=lang, input_id=term)
                except OSMDownError as exc:
                    errors.append(f"{term} + {place}: {exc}")
                    wide = []
                if wide and bbox:
                    inside = [r for r in wide if osm.within(r, bbox, margin=0.35)]
                    wide = inside or []
                if add(wide, f"{term} + {place}"):
                    continue
            # راه سوم: کل استان — شهرهای کوچک OSM پوشش کمی دارند
            prov_label = str(body.get("province_label") or "")
            prov_bbox = body.get("province_bbox")
            if prov_label and prov_bbox:
                left = budget - (time.time() - started)
                if left < 6:
                    break
                try:
                    broad = osm.search(f"{term} {prov_label}", bbox=list(prov_bbox),
                                       country=country, limit=config.OSM_LIMIT,
                                       timeout=min(14.0, left), lang=lang, input_id=term)
                except OSMDownError as exc:
                    errors.append(f"{term} + {prov_label}: {exc}")
                    broad = []
                if add(broad, f"{term} + {prov_label}"):
                    if on_progress:
                        on_progress({
                            "stage": "working", "status": "working",
                            "job_id": body.get("name") or "osm",
                            "message": f"[OSM] در شهر نبود؛ {len(broad)} مورد از کل "
                                       f"{prov_label} اضافه شد (خارج از محدودهٔ شهر)."})
                    continue

        if not rows:
            detail = f" ({'; '.join(errors[:2])})" if errors else ""
            raise EmptyResult(
                "در این محدوده چیزی پیدا نشد" + detail +
                " — OSM در شهرهای کوچک پوشش کمی دارد؛ محدودهٔ بزرگ‌تری را امتحان کن یا "
                "کلید Google Places را بگذار.", tried=tried)
        if on_progress:
            on_progress({"stage": "ok", "status": "ok", "job_id": body.get("name") or "osm",
                         "percent": 100, "message":
                         f"[OSM] {len(rows)} ردیف واقعی از {len(terms)} کلیدواژه"})
        return str(body.get("name") or "osm-job"), rows


class PlacesEngine:
    """Google Places API — مسیر تجاری (کلید لازم)."""

    name = "places"
    live = True

    def health(self) -> dict:
        from .providers import places
        return places.health()

    def run_job(self, body: dict, on_progress: ProgressFn | None = None) -> tuple[str, list[dict]]:
        from .providers import places
        from .providers.places import PlacesError

        started = time.time()
        budget = max(15.0, float(body.get("max_time") or 60))
        lang = str(body.get("lang") or "fa")
        try:
            lat = float(body.get("lat"))
            lon = float(body.get("lon"))
        except (TypeError, ValueError):
            lat = lon = None
        radius = int(body.get("radius") or 10000)
        place = str(body.get("city_label") or "")
        keywords = [str(k).strip() for k in (body.get("keywords") or []) if str(k).strip()]
        want = max(1, int(body.get("limit") or 20))

        rows: list[dict] = []
        errors: list[str] = []
        for index, keyword in enumerate(keywords[:3]):
            left = budget - (time.time() - started)
            if left < 6 or len(rows) >= want:
                break
            term = keyword if place and place.lower() in keyword.lower() else (
                f"{keyword} {place}".strip())
            if on_progress:
                on_progress({"stage": "working", "status": "working",
                             "job_id": body.get("name") or "places",
                             "percent": int(100 * index / max(1, min(3, len(keywords)))),
                             "message": f"[Places] {term} ({index + 1}/{min(3, len(keywords))})"})
            try:
                found = places.search(term, lat=lat, lon=lon, radius=radius,
                                      limit=min(20, want - len(rows)), lang=lang,
                                      timeout=min(15.0, left), input_id=keyword)
            except PlacesError as exc:
                errors.append(f"{keyword}: {exc}")
                continue
            seen = {r.get("place_id") for r in rows}
            rows.extend([r for r in found if r.get("place_id") not in seen])
        if not rows:
            raise EmptyResult("در این محدوده چیزی پیدا نشد" +
                              (f" ({'; '.join(errors[:2])})" if errors else ""),
                              tried=keywords[:3])
        if on_progress:
            on_progress({"stage": "ok", "status": "ok", "job_id": body.get("name") or "places",
                         "percent": 100, "message": f"[Places] {len(rows)} ردیف"})
        return str(body.get("name") or "places-job"), rows


# ─────────────────────────────────────────────────────────────────────────────
# انتخاب موتور
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_mode(mode: str | None) -> str:
    """درخواست «auto» رابط با «پین» سرور جمع می‌شود.

    اگر گردانندهٔ سرور `ENGINE_MODE` را روی یک موتور مشخص بگذارد (مثلاً `demo` یا
    `places`)، درخواست `auto`ِ رابط همان پین را می‌گیرد؛ وگرنه هر اجرا می‌توانست
    ناخواسته موتور دیگری (و هزینهٔ دیگری) را روشن کند. انتخاب صریح کاربر
    (`osm`، `demo`، …) همیشه برنده است.
    """
    wanted = (mode or "").strip().lower()
    pinned = (config.ENGINE_MODE or "").strip().lower()
    if wanted in ("", "auto") and pinned not in ("", "auto"):
        return pinned
    return wanted or pinned or "auto"


def make_engine(mode: str | None = None, base_url: str | None = None) -> Engine:
    """انتخاب موتور.

    `auto` به ترتیب: داکر (گوگل‌مپ واقعی) → Google Places (اگر کلید باشد) →
    OpenStreetMap (واقعی، بدون کلید) → دمو. یعنی همیشه یا دادهٔ واقعی می‌دهد یا
    با دلیل روشن به دمو می‌افتد — و روی Vercel هم کار می‌کند.
    """
    mode = _resolve_mode(mode)
    docker = DockerEngine(base_url=base_url)
    if mode == "demo":
        return DemoEngine()
    if mode in ("docker", "google", "scraper"):
        return docker
    if mode in ("osm", "openstreetmap"):
        return OSMEngine()
    if mode in ("places", "google_places", "api"):
        return PlacesEngine()
    # auto
    if docker.health().get("up"):
        return docker
    if config.PLACES_API_KEY:
        return PlacesEngine()
    osm_engine = OSMEngine()
    if osm_engine.health().get("up"):
        return osm_engine
    return DemoEngine()


def engine_health(name: str, *, force: bool = False, probe: bool = True) -> dict:
    """سلامت یک منبع دادهٔ مشخص: osm | nominatim | overpass | places | docker.

    `probe=False` فقط کش را برمی‌گرداند تا پاسخ /api/health همیشه سریع باشد.
    """
    key = (name or "").strip().lower()
    try:
        if key in ("osm", "nominatim"):
            from .providers import osm
            return osm.health(force=force) if probe else {"up": None, "endpoint": config.OSM_ENDPOINT,
                                                          "note": "سنجیده نشده"}
        if key == "overpass":
            from .providers import osm
            return osm.overpass_health(force=force, probe=probe)
        if key == "places":
            from .providers import places
            return places.health()
        if key == "docker":
            return DockerEngine().health()
    except Exception as exc:                              # noqa: BLE001
        return {"up": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    return {"up": False, "error": f"منبع ناشناخته: {name}"}


def engine_status(mode: str | None = None) -> dict:
    """وضعیت کامل موتور برای داشبورد: کدام فعال است و چرا.

    زنجیرهٔ `auto`: داکر (گوگل‌مپ) → Google Places (اگر کلید باشد) → OpenStreetMap
    (واقعی، بدون کلید) → دمو. `live` یعنی دادهٔ واقعی از اینترنت می‌آید.
    """
    docker = DockerEngine()
    docker_health = docker.health()
    active_mode = _resolve_mode(mode)
    osm_health: dict = {}
    places_health: dict = {}

    def _osm() -> dict:
        nonlocal osm_health
        if not osm_health:
            from .providers import osm
            osm_health = osm.health()
        return osm_health

    def _places() -> dict:
        nonlocal places_health
        if not places_health:
            from .providers import places
            places_health = places.health()
        return places_health

    if active_mode == "demo":
        active, reason = "demo", "ENGINE_MODE=demo در تنظیمات"
    elif active_mode in ("docker", "google", "scraper"):
        active = "docker" if docker_health.get("up") else "unavailable"
        reason = "موتور واقعی" if docker_health.get("up") else docker_health.get("error", "در دسترس نیست")
    elif active_mode in ("osm", "openstreetmap"):
        up = bool(_osm().get("up"))
        active = "osm" if up else "unavailable"
        reason = "OpenStreetMap (دادهٔ واقعی)" if up else (_osm().get("error") or "OSM در دسترس نیست")
    elif active_mode in ("places", "google_places", "api"):
        if not config.PLACES_API_KEY:
            active, reason = "unavailable", "PLACES_API_KEY تنظیم نشده است"
        else:
            up = bool(_places().get("up"))
            active = "places" if up else "unavailable"
            reason = "Google Places API (دادهٔ واقعی)" if up else (_places().get("error") or "در دسترس نیست")
    else:  # auto
        if docker_health.get("up"):
            active = "docker"
            reason = "موتور داکر روی " + docker_health.get("base_url", config.SCRAPER_BASE_URL)
        elif config.PLACES_API_KEY:
            active = "places"
            reason = "Google Places API (کلید تنظیم شده)"
        elif _osm().get("up"):
            active = "osm"
            reason = "OpenStreetMap — دادهٔ واقعی بدون داکر"
        else:
            active = "demo"
            reason = "نه داکر، نه کلید Places، نه دسترسی OSM → دادهٔ نمونه (پایپ‌لاین کامل)"

    return {
        "mode": active_mode,
        "active": active,
        "live": active in ("docker", "osm", "places"),
        "reason": reason,
        "docker": docker_health,
        "osm": osm_health or ({"up": None} if not config.SELF_DRIVE else {}),
        "places": places_health or {"key_set": bool(config.PLACES_API_KEY)},
        "chain": ["docker", "places", "osm", "demo"],
        "base_url": config.SCRAPER_BASE_URL,
        "proxies": len(config.SCRAPER_PROXIES),
    }
