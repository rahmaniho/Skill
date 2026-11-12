"""سرور وب: API + داشبورد فارسی (RTL) — فقط با کتابخانه استاندارد پایتون.

مسیرها:
  GET  /                     داشبورد (web/index.html)
  GET  /api/health           وضعیت موتور و تنظیمات
  GET  /api/catalog          اصناف، شهرها، پروفایل‌های امتیازدهی، کانال‌ها
  POST /api/runs             شروع یک اجرای اسکراپ (غیرهمزمان، در رشته جدا)
  GET  /api/runs             فهرست اجراها
  GET  /api/runs/{id}        جزئیات اجرا + آمار + پیشرفت
  GET  /api/runs/{id}/leads  لیدها با فیلتر/جست‌وجو/مرتب‌سازی
  GET  /api/runs/{id}/export خروجی فایل (csv|json|xlsx) با فیلتر بخش
  GET  /api/runs/{id}/campaign  متن کمپین برای یک لید یا همه
  POST /api/runs/{id}/rescore   امتیازدهی مجدد با پروفایل دیگر
  POST /api/import           وارد کردن CSV/JSON موجود و امتیازدهی آن
  POST/GET /api/worker/tick  یک کار از صف را همین‌جا اجرا می‌کند (سرورلس/کرون)
  GET  /api/worker           وضعیت اجرای زنده: صف، ورکر، موتور
  GET  /terms , /privacy     صفحه‌های حقوقی
  GET  /api/queue            وضعیت صف اجراها (آمار + کارها + موتور)
  GET  /api/queue/jobs       فهرست کارهای صف با فیلتر وضعیت
  GET  /api/delivery/status  وضعیت پیکربندی ایمیل و آخرین گزارش ارسال‌شده
  POST /api/delivery/digest  ارسال ایمیل گزارش لیدهای داغ (+ پیوست اکسل)
  POST /api/delivery/drafts  ساخت پیش‌نویس تماس در Gmail برای لیدها
  PATCH/POST /api/leads/{id}    تغییر وضعیت پیگیری (مینی‌CRM)
  DELETE /api/runs/{id}      حذف اجرا و لیدهایش

نکته امنیتی: موتور اسکراپر کیت روی 127.0.0.1 می‌ماند؛ این سرویس وب را اگر بیرون از
دستگاه خود منتشر می‌کنید، حتماً پشت پراکسی احراز هویت بگذارید (داده‌ها شخصی هستند).
"""
from __future__ import annotations

import json
import os
import re
import threading
from collections import Counter
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import (analytics, campaign, config, delivery, engine as engine_mod, enrich,
               exports, geo, jobs, niches, osm_tags, pipeline, regions, scoring, smart,
               storage)

ROOT_DIR = Path(__file__).resolve().parent.parent


def _num(value) -> float | None:
    """تبدیل امن به عدد اعشاری (برای lat/lon/حداقل امتیاز)."""
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value, default: int = 0) -> int:
    """تبدیل امن به عدد صحیح."""
    try:
        if value in (None, ""):
            return int(default)
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _flag(query: dict, key: str) -> bool | None:
    """پارامتر سه‌حالتهٔ کوئری: 1/true → True، 0/false → False، نبود → None."""
    if key not in query:
        return None
    raw = str(query.get(key, "")).strip().lower()
    if raw in ("1", "true", "yes", "on", "دارد"):
        return True
    if raw in ("0", "false", "no", "off", "ندارد"):
        return False
    return None
WEB_DIR = ROOT_DIR / "web"                 # داشبورد حرفه‌ای (کلاسیک)
SMART_DIR = ROOT_DIR / "smart"             # رابط سادهٔ کاربرپسند (پیش‌فرض روی /)
MINI_DIR = ROOT_DIR / "mini"               # نسخهٔ جمع‌وجور: جست‌وجو + استان/شهر/منطقه/خیابان
NOVA_DIR = ROOT_DIR / "nova"               # رابط نسل بعد (وب‌اپ + موبایل‌اپ، PWA)

# رابط پیش‌فرض روی «/»: nova (پیش‌فرض) یا smart (رابط ساده) یا mini (جمع‌وجور).
# با LEADGEN_ROOT_UI می‌توان یک نمونهٔ جدا فقط برای یکی از رابط‌ها بالا آورد.
ROOT_UI = os.environ.get("LEADGEN_ROOT_UI", "nova").strip().lower()
DEMO_DATA = ROOT_DIR / "site" / "data" / "sample.json"   # دادهٔ حالت دمو (مشترک با سایت Pages)

# حافظه پیشرفت اجراها (در حال اجرا) — پایگاه‌داده هم وضعیت نهایی را نگه می‌دارد
_PROGRESS: dict[str, dict] = {}
_PROGRESS_LOCK = threading.Lock()
MAX_PROGRESS = 400


# ── محافظ‌های تجاری ───────────────────────────────────────────────────────────
# مسیرهای عمومی که رابط برای بالا آمدن لازم دارد (حتی وقتی کد دسترسی گذاشته شده)
_PUBLIC_API = ("/api/health", "/api/meta", "/api/engine", "/api/worker", "/api/diag/",
               "/api/smart/", "/api/regions", "/api/cities", "/api/catalog")
_RUN_HITS: dict[str, list[float]] = {}
_DIAG_HITS: dict[str, list[float]] = {}
DIAG_PER_HOUR = 60                    # سقف آزمون زندهٔ موتور برای هر IP
_HITS_LOCK = threading.Lock()
WORKER_ONLINE_SECONDS = 180          # اگر کار در حال اجرا جدیدتر از این باشد، ورکر زنده است


def _client_ip(handler) -> str:
    forwarded = (handler.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    return forwarded or (handler.client_address[0] if handler.client_address else "?")


def _access_ok(handler, path: str, query: dict) -> bool:
    """اگر LEADGEN_ACCESS_CODE گذاشته شده باشد، همهٔ APIهای داده قفل می‌شوند."""
    if not config.ACCESS_CODE:
        return True
    if path.startswith(_PUBLIC_API):
        return True
    given = (handler.headers.get("X-Leadgen-Code") or query.get("code") or "").strip()
    return bool(given) and given == config.ACCESS_CODE


def _diag_allowed(ip: str, limit: int = DIAG_PER_HOUR) -> bool:
    """سقف سادهٔ آزمون زنده برای هر IP (ضد سوءاستفاده از سهمیهٔ Overpass)."""
    now = time.time()
    with _HITS_LOCK:
        hits = [t for t in _DIAG_HITS.get(ip, []) if now - t < 3600]
        if len(hits) >= limit:
            _DIAG_HITS[ip] = hits
            return False
        hits.append(now)
        _DIAG_HITS[ip] = hits
    return True


def _rate_hit(ip: str) -> bool:
    """سقف اجرا در ساعت برای هر IP (بهترین‌تلاش، در حافظهٔ همین نمونه)."""
    limit = config.RATE_RUNS_PER_HOUR
    if not limit:
        return True
    now = time.time()
    with _HITS_LOCK:
        hits = [t for t in _RUN_HITS.get(ip, []) if now - t < 3600]
        if len(hits) >= limit:
            _RUN_HITS[ip] = hits
            return False
        hits.append(now)
        _RUN_HITS[ip] = hits
        if len(_RUN_HITS) > 5000:                  # جلوگیری از رشد بی‌مرز حافظه
            for key in list(_RUN_HITS)[:2500]:
                _RUN_HITS.pop(key, None)
    return True


def worker_online() -> bool:
    """آیا ورکر بیرونی (Codespace/Actions) همین حالا روی کاری کار می‌کند؟"""
    try:
        running = storage.list_jobs("running", limit=5)
    except Exception:
        return False
    now = time.time()
    for job in running:
        stamp = job.get("updated_at") or job.get("started_at") or 0
        try:
            if now - float(stamp) < WORKER_ONLINE_SECONDS:
                return True
        except (TypeError, ValueError):
            continue
    return False


def self_drive_allowed() -> bool:
    """اجرای صف از سمت رابط مجاز است؟ (وقتی ورکر بیرونی نیست و SELF_DRIVE روشن است)"""
    return bool(config.SELF_DRIVE and jobs.queue_mode_active())


def record_progress(run_id: str, payload: dict) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS[run_id] = {**payload, "ts": time.time()}
        if len(_PROGRESS) > MAX_PROGRESS:
            for key in list(_PROGRESS)[:-MAX_PROGRESS]:
                _PROGRESS.pop(key, None)


def _persisted_progress(run_id: str) -> dict:
    """پیشرفت ذخیره‌شده در پایگاه‌داده (`runs.progress`) — منبع حقیقت در حالت صف."""
    try:
        run = storage.get_run(run_id) or {}
    except Exception:                       # پایگاه‌دادهٔ ابری موقتاً در دسترس نیست
        return {}
    persisted = run.get("progress") or {}
    if not isinstance(persisted, dict) or not persisted:
        return {}
    payload = dict(persisted)
    payload.setdefault("run_id", run_id)
    payload.setdefault("status", run.get("status"))
    if run.get("status") == "queued":
        position = jobs.queue_position(run_id) or {}
        payload.setdefault("message", position.get("message") or "در صف اجرا — منتظر موتور…")
        payload["queue"] = position
    return payload


def get_progress(run_id: str) -> dict:
    """پیشرفت اجرا — تازه‌ترین منبع برنده است.

    در حالت محلی، رشتهٔ پس‌زمینه پیشرفت را در حافظهٔ همین فرایند می‌نویسد. در حالت صف
    (ابر)، ورکر روی ماشین دیگری است و پیشرفت را در پایگاه‌داده می‌گذارد؛ بنابراین نسخهٔ
    حافظه (که فقط «در صف» اولیه را دارد) نباید روی دادهٔ تازه‌ترِ ورکر سایه بیندازد.
    """
    with _PROGRESS_LOCK:
        local = dict(_PROGRESS.get(run_id) or {})
    persisted = _persisted_progress(run_id)
    if not persisted:
        return _with_self_drive(local)
    if not local:
        return _with_self_drive(persisted)

    def stamp(payload: dict) -> float:
        try:
            return float(payload.get("ts") or 0)
        except (TypeError, ValueError):
            return 0.0

    return _with_self_drive(persisted if stamp(persisted) >= stamp(local) else local)


def _with_self_drive(payload: dict) -> dict:
    """اگر کاری در صف مانده و ورکر بیرونی نیست، به رابط بگو خودش موتور را بیدار کند."""
    if not payload:
        return payload
    if str(payload.get("status") or "") in ("queued", "pending") and \
            self_drive_allowed() and not worker_online():
        payload = dict(payload)
        payload["action"] = "tick"
    return payload


MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".csv": "text/csv; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".webmanifest": "application/manifest+json; charset=utf-8",
}


def parse_multipart(body: bytes, content_type: str) -> dict[str, dict]:
    """تجزیه ساده multipart/form-data → {name: {"value": str} | {"data": bytes, "filename": str}}."""
    match = re.search(r'boundary="?([^";]+)"?', content_type)
    if not match:
        return {}
    boundary = ("--" + match.group(1)).encode()
    fields: dict[str, dict] = {}
    for chunk in body.split(boundary):
        chunk = chunk.strip(b"\r\n-")
        if not chunk or b"\r\n\r\n" not in chunk:
            continue
        head, _, payload = chunk.partition(b"\r\n\r\n")
        headers = head.decode("utf-8", "replace")
        name_match = re.search(r'name="([^"]*)"', headers)
        if not name_match:
            continue
        name = name_match.group(1)
        file_match = re.search(r'filename="([^"]*)"', headers)
        if file_match:
            fields[name] = {"data": payload, "filename": file_match.group(1)}
        else:
            fields[name] = {"value": payload.decode("utf-8", "replace").strip()}
    return fields


def osm_tags_summary() -> dict:
    """پوشش نقشهٔ صنف → تگ (برای نمایش در سلامت و دیباگ)."""
    try:
        return osm_tags.spec_coverage()
    except Exception:                                    # noqa: BLE001
        return {}


def _cities_brief(cities: list[dict] | None = None) -> list[dict]:
    """فهرست سبک شهرها با نام فارسی استان (برای datalist و انتخاب‌گرها)."""
    rows = cities if cities is not None else geo.known_cities()
    out = []
    for item in rows:
        province = item.get("province", "")
        province_fa = (regions.PROVINCES.get(province) or {}).get("fa", "")
        if not province_fa and item.get("country") != "IR":
            province_fa = geo.COUNTRY_FA.get(item.get("country", ""), item.get("country", ""))
        out.append({"id": item.get("id", ""), "fa": item.get("fa") or item.get("label", ""),
                    "label": item.get("label", ""), "country": item.get("country", ""),
                    "province": province, "province_fa": province_fa,
                    "lat": item.get("lat"), "lon": item.get("lon")})
    return out


def _search_cities(text: str, limit: int = 8) -> list[dict]:
    """جست‌وجوی شهر در همهٔ ۱٬۳۰۰+ شهر کشور: دقیق > شروع‌شدن > زیررشته، پرجمعیت‌تر اول."""
    needle = geo._normalize(text)
    if len(needle) < 2:
        return []
    scored: list[tuple[float, int, dict]] = []
    for item in geo.known_cities():
        best = 0.0
        for name in (item.get("fa", ""), item.get("label", ""), *(item.get("aliases") or [])):
            norm = geo._normalize(name)
            if not norm:
                continue
            if norm == needle:
                best = max(best, 3.0)
            elif norm.startswith(needle):
                best = max(best, 2.0)
            elif needle in norm and len(needle) >= 3:
                best = max(best, 1.0)
        if best:
            scored.append((best, -(item.get("pop") or 0), item))
    scored.sort(key=lambda row: (-row[0], row[1], row[2].get("fa", "")))
    out = []
    for _score, _pop, item in scored[:limit]:
        province = item.get("province", "")
        out.append(dict(item, city=item.get("id", ""), kind="city",
                        province_fa=(regions.PROVINCES.get(province) or {}).get("fa", "")))
    return out


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server_version = "LeadGenStudio/1.0"
    protocol_version = "HTTP/1.1"

    # ── ابزارها ──────────────────────────────────────────────────────────────
    def log_message(self, fmt, *args):  # quieter logs
        if os.environ.get("LEADGEN_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _read_json(self) -> dict:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError(f"بدنه JSON نامعتبر: {exc}") from exc
        if not isinstance(data, dict):
            raise ApiError("بدنه درخواست باید یک JSON object باشد.")
        return data

    @property
    def _query(self) -> dict:
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "").items()}

    @property
    def _path(self) -> str:
        return urllib.parse.unquote(self.path.split("?", 1)[0])

    # ── مسیریابی ─────────────────────────────────────────────────────────────
    def do_GET(self):
        try:
            self._route_get()
        except ApiError as exc:
            self._json({"error": exc.message}, exc.status)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}",
                        "trace": traceback.format_exc().splitlines()[-6:]}, 500)

    def do_POST(self):
        try:
            self._route_write(method="POST")
        except ApiError as exc:
            self._json({"error": exc.message}, exc.status)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}",
                        "trace": traceback.format_exc().splitlines()[-6:]}, 500)

    def do_PATCH(self):
        self.do_POST()

    def do_PUT(self):
        self.do_POST()

    def do_DELETE(self):
        try:
            self._route_write(method="DELETE")
        except ApiError as exc:
            self._json({"error": exc.message}, exc.status)
        except Exception as exc:
            self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_HEAD(self):
        self.do_GET()

    def _route_get(self) -> None:
        path, query = self._path, self._query
        if path.startswith("/api/") and not _access_ok(self, path, query):
            raise ApiError("🔒 برای دیدن داده‌ها کد دسترسی لازم است.", 401)
        storage.init_db()

        # رابط نسل بعد (پیش‌فرض) روی / ؛ داشبورد حرفه‌ای روی /studio/
        root_dir = {"mini": MINI_DIR, "smart": SMART_DIR}.get(ROOT_UI, NOVA_DIR)
        if path in ("/", "/index.html"):
            return self._static("index.html", root_dir)
        if path in ("/styles.css", "/app.js", "/ui.js", "/sw.js", "/campaign.js",
                    "/manifest.webmanifest", "/favicon.svg", "/demo-data.json") \
                or path.startswith("/assets/") or path.startswith("/icons/"):
            return self._static(path.lstrip("/"), root_dir)
        # صفحه‌های حقوقی (برای عرضهٔ تجاری و نصب به‌عنوان اپ لازم‌اند)
        if path in ("/terms", "/terms/", "/terms.html"):
            return self._static("terms.html", NOVA_DIR)
        if path in ("/privacy", "/privacy/", "/privacy.html"):
            return self._static("privacy.html", NOVA_DIR)
        if path in ("/nova", "/nova/", "/nova/index.html"):
            return self._static("index.html", NOVA_DIR)
        if path.startswith("/nova/"):
            return self._static(path[len("/nova/"):], NOVA_DIR)
        if path in ("/smart", "/smart/", "/smart/index.html"):
            return self._static("index.html", SMART_DIR)
        if path.startswith("/smart/"):
            return self._static(path[len("/smart/"):], SMART_DIR)
        if path in ("/mini", "/mini/", "/mini/index.html"):
            return self._static("index.html", MINI_DIR)
        if path.startswith("/mini/"):
            return self._static(path[len("/mini/"):], MINI_DIR)
        if path in ("/studio", "/studio/", "/studio/index.html"):
            return self._static("index.html", WEB_DIR)
        if path.startswith("/studio/"):
            return self._static(path[len("/studio/"):], WEB_DIR)

        if path == "/api/health":
            fresh = _flag(query, "fresh") is True
            osm_health = engine_mod.engine_health("osm", force=fresh, probe=fresh)
            return self._json({
                "ok": True,
                "version": "1.0.0",
                "engine": engine_mod.engine_status(),
                "config": config.summary(),
                "db": storage.pipeline_stats(),
                "sources": {"osm": osm_health,
                            "overpass": {"mode": config.OSM_MODE,
                                         **engine_mod.engine_health("overpass", force=fresh,
                                                                   probe=fresh)}},
                "tag_map": osm_tags_summary(),
            })

        if path == "/api/smart/options":
            return self._json(smart.options())

        if path == "/api/smart/suggest":
            return self._json(smart.suggest(query.get("q", ""), limit=int(query.get("limit", 6))))

        # ── سلسله‌مراتب جغرافیایی: استان → شهر → منطقه → خیابان ─────────────
        if path == "/api/regions":
            return self._json(regions.hierarchy())

        if path == "/api/regions/districts":
            key = regions.city_key(query.get("city", ""))
            if not key:
                raise ApiError(f"شهر ناشناخته: {query.get('city', '')}", 404)
            return self._json({"city": key, "city_fa": regions.city_fa(key),
                               "districts": regions.districts(key)})

        if path == "/api/regions/streets":
            key = regions.city_key(query.get("city", ""))
            if not key:
                raise ApiError(f"شهر ناشناخته: {query.get('city', '')}", 404)
            return self._json({"city": key, "city_fa": regions.city_fa(key),
                               "district": query.get("district", ""),
                               "streets": regions.streets(key, query.get("district", ""))})

        if path == "/api/regions/search":
            text = query.get("q", "")
            limit = max(1, min(_int(query.get("limit", 8), 8), 30))
            cities = _search_cities(text.strip(), limit) if text.strip() else []
            return self._json({
                "q": text,
                "areas": regions.search_areas(text, limit=_int(query.get("limit", 8), 8)),
                "in_text": [{"fa": h["fa"], "kind": h["kind"], "city": h["city"],
                             "city_fa": h["city_fa"], "lat": h["lat"], "lon": h["lon"]}
                            for h in regions.detect_area(text)],
                "cities": cities,
            })

        if path == "/api/regions/resolve":
            return self._json(regions.resolve(province=query.get("province", ""),
                                              city=query.get("city", ""),
                                              district=query.get("district", ""),
                                              street=query.get("street", ""),
                                              lat=_num(query.get("lat")),
                                              lon=_num(query.get("lon"))))

        # ── آزمون زندهٔ موتور داده (بدون نوشتن در پایگاه‌داده) ─────────────
        if path == "/api/diag/osm":
            return self._json(self._osm_diag(query))

        # ── جست‌وجوی قوی در همهٔ داده‌های جمع‌شده (تمام اجراها) ──────────────
        if path == "/api/search":
            return self._json(storage.search_leads(**self._search_params(query)))

        if path == "/api/search/export":
            return self._search_export(query)

        if path == "/api/catalog":
            return self._json({
                "niches": niches.all_niches(),
                "groups": niches.GROUPS,
                "column_presets": [{"id": key, "fa": value["fa"], "note": value["note"],
                                    "columns": [fa for _, fa in value["columns"]]}
                                   for key, value in exports.COLUMN_PRESETS.items()],
                "cities": _cities_brief(),
                "profiles": scoring.profile_list(),
                "channels": [{"id": c, "fa": campaign.CHANNEL_FA[c]} for c in campaign.CHANNELS],
                "lead_statuses": [{"id": s, "fa": storage.LEAD_STATUS_FA[s]} for s in storage.LEAD_STATUSES],
                "gap_labels": scoring.GAP_FA,
            })

        if path == "/api/runs":
            limit = int(query.get("limit", 50))
            return self._json({"runs": storage.list_runs(limit=limit),
                               "stats": storage.pipeline_stats()})

        match = re.fullmatch(r"/api/runs/([\w\-]+)", path)
        if match:
            return self._run_detail(match.group(1))

        match = re.fullmatch(r"/api/runs/([\w\-]+)/leads", path)
        if match:
            return self._leads(match.group(1), query)

        match = re.fullmatch(r"/api/runs/([\w\-]+)/analytics", path)
        if match:
            return self._run_analytics(match.group(1))

        match = re.fullmatch(r"/api/runs/([\w\-]+)/campaign", path)
        if match:
            return self._run_campaign(match.group(1), query)

        match = re.fullmatch(r"/api/runs/([\w\-]+)/export", path)
        if match:
            return self._run_export(match.group(1), query)

        match = re.fullmatch(r"/api/runs/([\w\-]+)/progress", path)
        if match:
            return self._json({"progress": get_progress(match.group(1))})

        if path == "/api/worker/tick":
            # GET هم پشتیبانی می‌شود تا کرون/پینگر بیرونی (بدون JS) صف را جلو ببرد.
            return self._worker_tick(query)

        if path == "/api/worker":
            stats = storage.queue_stats()
            active_engine = pipeline.engine_status(None)
            return self._json({
                "queue_active": jobs.queue_mode_active(),
                "self_drive": self_drive_allowed(),
                "worker_online": worker_online(),
                "engine": active_engine.get("active"),
                "engine_reason": active_engine.get("reason"),
                "live_data": bool(active_engine.get("live")),
                "stats": stats,
                "note": ("همین رابط صف را اجرا می‌کند (بدون ورکر بیرونی)" if
                         self_drive_allowed() and not worker_online() else
                         "ورکر بیرونی فعال است" if worker_online() else "ورکر بیرونی تنظیم نشده"),
            })

        if path == "/api/queue":
            return self._json(self._queue_snapshot(query))

        if path == "/api/queue/jobs":
            status = query.get("status") or None
            if status and status not in storage.JOB_STATUSES:
                raise ApiError(f"وضعیت نامعتبر: {status}. مجاز: {', '.join(storage.JOB_STATUSES)}")
            return self._json({"jobs": storage.list_jobs(status, limit=_int(query.get("limit"), 50)),
                               "statuses": [{"id": k, "fa": storage.job_status_fa(k)}
                                            for k in storage.JOB_STATUSES]})

        if path == "/api/delivery/status":
            return self._json({
                "transport": config.email_transport(),
                "recipients": config.digest_recipients(),
                "app_url": config.app_base_url(),
                "configured": config.email_transport() != "none",
                "last_digest": storage.last_delivery("digest"),
                "hint": ("برای ارسال واقعی، GMAIL_REFRESH_TOKEN + GMAIL_CLIENT_ID + "
                         "GMAIL_CLIENT_SECRET (روی Vercel) یا GMAIL_APP_PASSWORD (محلی) "
                         "را در .env بگذارید.") if config.email_transport() == "none" else "",
            })

        match = re.fullmatch(r"/api/leads/([\w\-]+)", path)
        if match:
            lead = storage.get_lead(match.group(1))
            if not lead:
                raise ApiError("لید پیدا نشد.", 404)
            run = storage.get_run(lead["run_id"])
            niche = niches.find_niche((run or {}).get("niche_id") or "")
            # breakdown امتیاز ذخیره نمی‌شود؛ همین‌جا با همان پروفایل اجرا بازمحاسبه می‌شود
            # تا همیشه با پروفایل فعلی سازگار بماند.
            recomputed = scoring.score_lead(lead, profile=(run or {}).get("profile") or lead.get("profile")
                                            or "generic", niche=niche)
            lead["score_factors"] = recomputed.factors
            lead["gaps"] = recomputed.gaps
            lead["gaps_fa"] = [scoring.GAP_FA.get(g, g) for g in recomputed.gaps]
            lead["tags"] = recomputed.tags
            lead["pitch"] = recomputed.pitch
            lead["campaign"] = {
                channel: campaign.generate(lead, channel, campaign.Sender(), niche)
                for channel in campaign.CHANNELS
            }
            lead["campaign"]["phone"] = campaign.generate(lead, "phone", campaign.Sender(), niche)
            return self._json({"lead": lead})

        if path == "/api/niches":
            return self._json({"niches": niches.all_niches(), "groups": niches.GROUPS})
        if path == "/api/cities":
            return self._json({"cities": _cities_brief(), "count": len(geo.CITIES)})
        if path == "/api/profiles":
            return self._json({"profiles": scoring.profile_list()})

        raise ApiError(f"مسیر پیدا نشد: {path}", 404)

    def _route_write(self, method: str = "POST") -> None:
        path, query = self._path, self._query
        if path.startswith("/api/") and not _access_ok(self, path, query):
            raise ApiError("🔒 برای این کار کد دسترسی لازم است.", 401)
        storage.init_db()

        if path == "/api/runs" and method == "POST":
            if not _rate_hit(_client_ip(self)):
                raise ApiError(
                    f"سقف اجرا در ساعت پر شد ({config.RATE_RUNS_PER_HOUR} اجرا). "
                    "کمی بعد دوباره امتحان کن.", 429)
            return self._start_run(self._read_json())

        if path == "/api/worker/tick" and method == "POST":
            return self._worker_tick(query)

        match = re.fullmatch(r"/api/runs/([\w\-]+)/rescore", path)
        if match and method == "POST":
            body = self._read_json()
            profile = body.get("profile") or "generic"
            if profile not in scoring.PROFILES:
                raise ApiError(f"پروفایل ناشناخته: {profile}")
            return self._json(pipeline.rescore_run(match.group(1), profile))

        if path == "/api/runs" and method == "DELETE":
            run_id = query.get("id")
            if not run_id:
                raise ApiError("شناسه اجرا لازم است (?id=…).")
            ok = storage.delete_run(run_id)
            return self._json({"deleted": ok, "id": run_id})

        match = re.fullmatch(r"/api/runs/([\w\-]+)", path)
        if match and method == "DELETE":
            return self._json({"deleted": storage.delete_run(match.group(1)), "id": match.group(1)})

        match = re.fullmatch(r"/api/leads/([\w\-]+)", path)
        if match and method in ("POST", "PATCH", "PUT"):
            body = self._read_json()
            status = body.get("status", "new")
            if status not in storage.LEAD_STATUSES:
                raise ApiError(f"وضعیت نامعتبر: {status}. مجاز: {', '.join(storage.LEAD_STATUSES)}")
            ok = storage.set_lead_status(match.group(1), status, body.get("note"))
            if not ok:
                raise ApiError("لید پیدا نشد.", 404)
            return self._json({"ok": True, "lead": storage.get_lead(match.group(1))})

        match = re.fullmatch(r"/api/leads/([\w\-]+)/campaign", path)
        if match and method == "POST":
            lead = storage.get_lead(match.group(1))
            if not lead:
                raise ApiError("لید پیدا نشد.", 404)
            body = self._read_json()
            sender = campaign.Sender.from_dict(body.get("sender"))
            channel = body.get("channel") or lead.get("channel") or "email"
            niche = niches.find_niche((storage.get_run(lead["run_id"]) or {}).get("niche_id") or "")
            return self._json(campaign.generate(lead, channel, sender, niche,
                                                language=body.get("language", "fa")))

        if path == "/api/import" and method == "POST":
            return self._import_leads()

        if path == "/api/delivery/digest" and method == "POST":
            body = self._read_json()
            recipients = [x.strip() for x in str(body.get("to") or "").split(",") if x.strip()]
            try:
                result = delivery.send_digest(
                    body.get("run_id") or None, to=recipients or None,
                    segment=(body.get("segment") or "hot") or None,
                    min_score=_int(body.get("min_score")) or None,
                    limit=_int(body.get("limit")) or None, subject=body.get("subject") or "",
                    with_xlsx=bool(body.get("with_xlsx", True)),
                    dry_run=(None if body.get("dry_run") is None else bool(body["dry_run"])),
                    transport=body.get("transport") or None)
            except delivery.DeliveryError as exc:
                raise ApiError(str(exc)) from exc
            if result.get("status") == "skipped":
                raise ApiError("گیرنده‌ای مشخص نشده است — «to» را بفرستید یا DIGEST_TO را "
                               "در .env تنظیم کنید.")
            return self._json(result)

        if path == "/api/delivery/drafts" and method == "POST":
            body = self._read_json()
            try:
                return self._json(delivery.create_drafts(
                    _int(body.get("limit"), 10), channel=body.get("channel") or "email",
                    min_score=_int(body.get("min_score")) or None,
                    run_id=body.get("run_id") or None, sender=body.get("sender") or None,
                    dry_run=(None if body.get("dry_run") is None else bool(body["dry_run"])),
                    transport=body.get("transport") or None))
            except delivery.DeliveryError as exc:
                raise ApiError(str(exc)) from exc

        if path == "/api/smart/parse" and method == "POST":
            body = self._read_json()
            return self._json(smart.parse(
                text=body.get("text", ""), goal=body.get("goal", "site"),
                size=body.get("size", "standard"), city=body.get("city", ""),
                socials=bool(body.get("socials", True)), emails=bool(body.get("emails", True)),
                engine_mode=body.get("engine_mode") or config.ENGINE_MODE,
                cols=body.get("cols", "contact"), export_fmt=body.get("export_fmt", "xlsx"),
                allow_network=bool(body.get("allow_network", False)),
                lat=str(body.get("lat", "") or ""), lon=str(body.get("lon", "") or ""),
                province=str(body.get("province", "") or ""),
                district=str(body.get("district", "") or ""),
                street=str(body.get("street", "") or ""),
                radius=_int(body.get("radius")), niche_id=str(body.get("niche_id", "") or ""),
                all_trades=(None if body.get("all_trades") is None
                            else bool(body.get("all_trades"))),
                limit=_int(body.get("limit")),
                area_from_text=bool(body.get("area_from_text", True))))

        if path == "/api/niches/find" and method == "POST":
            body = self._read_json()
            found = niches.find_niche(body.get("query", ""))
            return self._json({"niche": found.to_dict(),
                               "sample_keywords": niches.expand_keywords(found, body.get("city", "Tehran"))})

        if path == "/api/geocode" and method == "POST":
            body = self._read_json()
            place = body.get("place", "")
            lat, lon, bbox, key = geo.geocode(place, with_bbox=True,
                                              allow_network=bool(body.get("allow_network", False)))
            if not lat:
                raise ApiError(f"مکان پیدا نشد: {place}", 404)
            return self._json({"place": place, "lat": lat, "lon": lon, "bbox": bbox,
                               "city_key": key, "label": geo.city_label(key, place)})

        raise ApiError(f"مسیر/متد پشتیبانی نمی‌شود: {method} {path}", 404)

    # ── هندلرها ──────────────────────────────────────────────────────────────
    def _static(self, rel: str, root: Path | None = None) -> None:
        root = (root or WEB_DIR).resolve()
        target = (root / rel).resolve()
        if not str(target).startswith(str(root)) or not target.is_file():
            # دادهٔ حالت دمو در مخزن یک نسخه بیشتر ندارد (site/data/sample.json)
            if rel == "demo-data.json" and DEMO_DATA.is_file():
                target = DEMO_DATA.resolve()
            else:
                raise ApiError("یافت نشد", 404)
        body = target.read_bytes()
        self._send(200, body, MIME.get(target.suffix, "application/octet-stream"))

    def _run_detail(self, run_id: str) -> None:
        run = storage.get_run(run_id)
        if not run:
            raise ApiError("اجرا پیدا نشد.", 404)
        progress = get_progress(run_id)
        leads_preview = storage.load_leads(run_id, limit=8)
        return self._json({
            "run": run,
            "progress": progress,
            "analytics": run.get("analytics") or analytics.build(storage.all_leads_for_export(run_id), run),
            "leads_preview": leads_preview,
            "lead_count": storage.count_leads(run_id),
        })

    def _run_analytics(self, run_id: str) -> None:
        run = storage.get_run(run_id)
        if not run:
            raise ApiError("اجرا پیدا نشد.", 404)
        pack = run.get("analytics")
        if not pack:
            rows = storage.all_leads_for_export(run_id)
            niche = niches.find_niche(run.get("niche_id") or "")
            pack = analytics.build(rows, run, niche)
        return self._json({"analytics": pack})

    def _leads(self, run_id: str, query: dict) -> None:
        if not storage.get_run(run_id):
            raise ApiError("اجرا پیدا نشد.", 404)

        def as_int(key, default=None):
            try:
                return int(query[key])
            except (KeyError, ValueError):
                return default

        rows = storage.load_leads(
            run_id,
            segment=query.get("segment") or None,
            status=query.get("status") or None,
            channel=query.get("channel") or None,
            gap=query.get("gap") or None,
            query=query.get("q") or None,
            min_score=as_int("min_score"),
            sort=query.get("sort", "score"),
            order=query.get("order", "desc"),
            limit=as_int("limit", 200) or 200,
            offset=as_int("offset", 0) or 0,
        )
        # برچسب‌های فارسی شکاف‌ها و وضعیت در پایگاه‌داده ذخیره نشده‌اند → همین‌جا ساخته می‌شوند
        for row in rows:
            row["gaps_fa"] = [scoring.GAP_FA.get(g, g) for g in (row.get("gaps") or [])]
            row["channel_fa"] = campaign.CHANNEL_FA.get(row.get("channel"), row.get("channel"))
            if not row.get("maps_link"):
                row["maps_link"] = enrich.maps_link(row)
        return self._json({
            "leads": rows,
            "count": len(rows),
            "total": storage.count_leads(run_id),
            "statuses": storage.LEAD_STATUS_FA,
        })

    def _osm_diag(self, query: dict) -> dict:
        """یک درخواست واقعی به Overpass برای یک شهر/صنف و برگرداندن آمار و نمونه.

        چرا؟ برای اینکه «کار می‌کند» با شاهد واقعی سنجیده شود، نه با حدس. بدون
        نوشتن در پایگاه‌داده و با همان محدودیت نرخ موتور.
        """
        from .providers import osm as osm_provider

        city_name = (query.get("city") or "").strip()
        niche_name = (query.get("niche") or "").strip()
        all_business = _flag(query, "all") is True or niche_name in ("", "همه", "all")
        if not _diag_allowed(_client_ip(self)):
            raise ApiError("تعداد آزمون‌ها زیاد بود؛ چند دقیقه دیگر امتحان کنید.", 429)
        area = regions.resolve(city=city_name) if city_name else {}
        if not area.get("lat"):
            return {"ok": False, "error": f"شهر «{city_name}» پیدا نشد", "city": city_name}
        key = area.get("city_key") or ""
        bbox = None
        if key and key in geo.CITIES:
            bbox = [float(x) for x in geo.CITIES[key][3]]
        bbox = bbox or geo.default_bbox(area["lat"], area["lon"], 0.12)
        span = max(bbox[1] - bbox[0], bbox[3] - bbox[2])
        niche_obj = None if all_business else niches.find_niche(niche_name)
        if all_business:
            filters = osm_tags.all_business_filters()
            label = "همهٔ کسب‌وکارها"
        else:
            filters = osm_tags.filters_for_niche(niche_obj.id, with_names=span < 0.6)
            label = niche_obj.fa
        limit = max(20, min(_int(query.get("limit", 300), 300), 800))
        deep = _flag(query, "deep") is True
        # تابع serverless سقف زمانی دارد؛ سنجش زنده هم باید داخل همان سقف بماند،
        # وگرنه به‌جای «جواب واقعی» یک 504 می‌بینیم. پیش‌فرض ۲۲ ثانیه، قابل تنظیم.
        probe_timeout = min(45, max(8, _int(query.get("timeout", 22), 22)))
        started = time.time()
        try:
            if deep:          # جاروی کامل: اگر نتیجه به سقف خورد، محدوده قطعه‌قطعه می‌شود
                rows = osm_provider.overpass_search_area(
                    filters, bbox=bbox, limit=osm_provider.overpass_limit_for(bbox, limit),
                    input_id="diag", tiles=4, max_rows=4000, timeout=probe_timeout,
                    budget_s=float(probe_timeout) + 12)
            else:
                rows = osm_provider.overpass_search(filters, bbox=bbox, limit=limit,
                                                   input_id="diag", timeout=probe_timeout)
            error = ""
        except Exception as exc:                          # noqa: BLE001 — گزارش روشن
            rows, error = [], f"{type(exc).__name__}: {exc}"[:300]
        elapsed = round(time.time() - started, 2)
        overpass_ok = not error and bool(rows)
        # مسیر جایگزین را هم واقعاً امتحان می‌کنیم؛ وگرنه «Overpass پایین» با
        # «این شهر داده ندارد» قاطی می‌شود و پاسخ سنجش گمراه‌کننده است.
        fallback: dict = {"tried": False, "via": "", "rows": 0, "seconds": 0.0,
                          "text": "", "error": ""}
        if not rows and config.OSM_MODE != "overpass":
            from . import osm_terms as lexicon

            if all_business:
                texts = ["restaurant", "shop", "cafe"]
            else:
                texts = list(dict.fromkeys(
                    lexicon.derive([niche_obj.fa, niche_obj.en])[:2]
                    + [niche_obj.fa, niche_obj.en]))
            fb_started = time.time()
            fallback["tried"] = True
            for text in texts[:3]:
                left = probe_timeout + 6 - (time.time() - fb_started)
                if left < 4:
                    break
                try:
                    got = osm_provider.search(text, bbox=bbox, limit=limit,
                                              input_id="diag", timeout=min(12.0, left))
                except Exception as exc:                  # noqa: BLE001
                    fallback["error"] = f"{type(exc).__name__}: {exc}"[:200]
                    continue
                if got:
                    rows = got
                    fallback.update(via="nominatim", rows=len(got), text=text, error="")
                    break
            fallback["seconds"] = round(time.time() - fb_started, 2)
        elapsed = round(time.time() - started, 2)
        sample = [{k: row.get(k) for k in ("title", "category", "latitude", "longitude",
                                           "phone", "website", "instagram", "address")}
                  for row in rows[:12]]
        return {
            "ok": bool(rows),
            "overpass_ok": overpass_ok,
            "fallback": fallback,
            "city": area.get("label") or city_name, "city_key": key,
            "province": area.get("province", ""), "niche": label,
            "mode": config.OSM_MODE, "bbox": [round(x, 4) for x in bbox],
            "filters": len(filters), "rows": len(rows), "seconds": elapsed, "deep": deep,
            "timeout": probe_timeout,
            "with_phone": sum(1 for r in rows if r.get("phone")),
            "with_website": sum(1 for r in rows if r.get("website")),
            "with_address": sum(1 for r in rows if r.get("address")),
            "with_social": sum(1 for r in rows if r.get("instagram") or r.get("facebook")),
            "categories": dict(sorted(
                Counter((r.get("category") or "—") for r in rows).items(),
                key=lambda kv: -kv[1])[:8]),
            "sample": sample,
            "error": error,
        }

    def _search_params(self, query: dict) -> dict:
        """پارامترهای جست‌وجوی قوی از کوئری‌استرینگ (یک تعریف، دو مصرف)."""
        return {
            "q": query.get("q", ""), "run_id": query.get("run_id") or None,
            "segment": query.get("segment") or None, "status": query.get("status") or None,
            "channel": query.get("channel") or None, "gap": query.get("gap") or None,
            "niche_id": query.get("niche_id") or None,
            "niche_fa": query.get("niche_fa") or None,
            "niche_group": query.get("niche_group") or None,
            "has_phone": _flag(query, "has_phone"), "has_email": _flag(query, "has_email"),
            "has_website": _flag(query, "has_website"), "has_social": _flag(query, "has_social"),
            "has_instagram": _flag(query, "has_instagram"),
            "min_rating": _num(query.get("min_rating")),
            "min_reviews": _int(query.get("min_reviews")),
            "min_score": _int(query.get("min_score")),
            "lat": _num(query.get("lat")), "lon": _num(query.get("lon")),
            "radius_m": _int(query.get("radius_m") or query.get("radius")),
            "sort": query.get("sort", "score"), "order": query.get("order", "desc"),
            "limit": _int(query.get("limit", 200), 200), "offset": _int(query.get("offset"), 0),
        }

    def _search_export(self, query: dict) -> None:
        """خروجی فایل از مجموعهٔ فیلترشدهٔ جست‌وجوی قوی (می‌تواند چند اجرا را شامل شود)."""
        params = self._search_params(query)
        params["limit"] = _int(query.get("max_rows", 20000), 20000)
        params["offset"] = 0
        rows = storage.search_leads(**params)["leads"]
        if not rows:
            raise ApiError("نتیجه‌ای برای خروجی گرفتن پیدا نشد؛ فیلترها را تغییر دهید.", 404)
        fmt = (query.get("fmt") or "csv").lower()
        full = query.get("full") in ("1", "true", "yes")
        preset = query.get("cols") or query.get("preset") or "contact"
        for row in rows:
            row["gaps_fa"] = [scoring.GAP_FA.get(g, g) for g in (row.get("gaps") or [])]

        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = f"search-{stamp}"
        meta_run = {
            "id": base, "niche_fa": query.get("q") or "جست‌وجوی ترکیبی",
            "city": rows[0].get("run_city") or rows[0].get("city") or "",
            "engine": "search", "keywords": [query["q"]] if query.get("q") else [],
            "area_label": (rows[0].get("run_area") or ""),
        }
        disposition = lambda name, mime: self._send(  # noqa: E731
            200, body, mime, {"Content-Disposition": f'attachment; filename="{name}"'})

        if fmt == "json":
            body = exports.json_bytes({"run": meta_run, "filters": params, "total": len(rows),
                                       "leads": rows})
            return disposition(f"{base}.json", "application/json; charset=utf-8")

        if fmt in ("xlsx", "excel"):
            pack = analytics.build(rows, meta_run)
            sheets = exports.build_workbook(rows, pack, meta_run, None, full=full, preset=preset)
            body = exports.xlsx_bytes(sheets)
            return disposition(
                f"{base}.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        body = exports.csv_bytes(rows, full=full, preset=preset)
        return disposition(f"{base}.csv", "text/csv; charset=utf-8")

    def _run_export(self, run_id: str, query: dict) -> None:
        run = storage.get_run(run_id)
        if not run:
            raise ApiError("اجرا پیدا نشد.", 404)
        rows = storage.all_leads_for_export(run_id)
        segment = query.get("segment")
        if segment:
            rows = [r for r in rows if r.get("segment") == segment]
        status = query.get("status")
        if status:
            rows = [r for r in rows if r.get("status") == status]
        fmt = (query.get("fmt") or "xlsx").lower()
        full = query.get("full") in ("1", "true", "yes")
        preset = query.get("cols") or query.get("preset") or "lead"
        for row in rows:
            row["gaps_fa"] = [scoring.GAP_FA.get(g, g) for g in (row.get("gaps") or [])]

        if query.get("what") == "campaign":
            sender = campaign.Sender.from_dict(json.loads(query.get("sender") or "{}"))
            channel = query.get("channel") or "email"
            campaign_rows = []
            for row in rows:
                message = campaign.generate(row, channel, sender,
                                            niches.find_niche(run.get("niche_id") or ""))
                campaign_rows.append({**row, "channel": channel,
                                      "channel_fa": campaign.CHANNEL_FA.get(channel, channel),
                                      "to": message.get("to", ""), "subject": message.get("subject", ""),
                                      "body": message.get("text") or message.get("body", "")})
            body = exports.csv_bytes_custom(campaign_rows, exports.CAMPAIGN_COLUMNS)
            name = f"campaign-{channel}-{run_id}.csv"
            return self._send(200, body, "text/csv; charset=utf-8", {
                "Content-Disposition": f'attachment; filename="{name}"'})

        if fmt == "json":
            pack = run.get("analytics") or analytics.build(rows, run)
            body = exports.json_bytes({"run": run, "analytics": pack, "leads": rows})
            name = f"leads-{run_id}.json"
            return self._send(200, body, "application/json; charset=utf-8", {
                "Content-Disposition": f'attachment; filename="{name}"'})

        if fmt in ("xlsx", "excel"):
            pack = run.get("analytics") or analytics.build(rows, run)
            sender = campaign.Sender.from_dict(json.loads(query.get("sender") or "{}"))
            campaign_rows = []
            for row in [r for r in rows if r.get("segment") in ("hot", "warm")][:400]:
                channel = row.get("channel") if row.get("channel") in campaign.CHANNELS else "email"
                if channel == "email" and not row.get("email_primary"):
                    channel = "whatsapp" if row.get("phone") else "email"
                message = campaign.generate(row, channel, sender, niches.find_niche(run.get("niche_id") or ""))
                campaign_rows.append({**row, "channel": channel,
                                      "channel_fa": campaign.CHANNEL_FA.get(channel, channel),
                                      "to": message.get("to", ""), "subject": message.get("subject", ""),
                                      "body": message.get("text") or message.get("body", "")})
            sheets = exports.build_workbook(rows, pack, run, campaign_rows, full=full, preset=preset)
            body = exports.xlsx_bytes(sheets)
            name = f"leads-{run_id}.xlsx"
            return self._send(200, body,
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              {"Content-Disposition": f'attachment; filename="{name}"'})

        body = exports.csv_bytes(rows, full=full, preset=preset)
        name = f"leads-{run_id}.csv"
        return self._send(200, body, "text/csv; charset=utf-8", {
            "Content-Disposition": f'attachment; filename="{name}"'})

    def _run_campaign(self, run_id: str, query: dict) -> None:
        run = storage.get_run(run_id)
        if not run:
            raise ApiError("اجرا پیدا نشد.", 404)
        channel = query.get("channel") or "email"
        if channel not in campaign.CHANNELS and channel != "phone":
            raise ApiError(f"کانال نامعتبر: {channel}")
        limit = int(query.get("limit", 25))
        rows = storage.load_leads(run_id, segment=query.get("segment") or None, limit=limit)
        sender = campaign.Sender.from_dict(json.loads(query.get("sender") or "{}"))
        niche = niches.find_niche(run.get("niche_id") or "")
        items = []
        for row in rows:
            row["gaps_fa"] = [scoring.GAP_FA.get(g, g) for g in (row.get("gaps") or [])]
            message = campaign.generate(row, channel, sender, niche,
                                        language=query.get("language", "fa"))
            items.append({
                "id": row["id"], "title": row.get("title"), "score": row.get("score"),
                "segment_fa": row.get("segment") and scoring.SEGMENTS.get(row["segment"], ""),
                "to": message.get("to", ""), "subject": message.get("subject", ""),
                "body": message.get("body", ""), "text": message.get("text", ""),
                "sequence": message.get("sequence", []),
                "mailto": campaign.mailto_link(row, channel, sender, niche) if channel == "email" else "",
                "wa": campaign.wa_link(row, sender, niche) if channel == "whatsapp" else "",
                "status": row.get("status"), "status_fa": storage.LEAD_STATUS_FA.get(row.get("status")),
            })
        return self._json({"channel": channel, "channel_fa": campaign.CHANNEL_FA.get(channel, channel),
                           "sender": {k: getattr(sender, k) for k in sender.__dataclass_fields__},
                           "items": items,
                           "compliance": (
                               "⚖ داده‌های اسکراپ‌شده شخصی‌اند: مبنای قانونی، هویت فرستنده و امکان "
                               "«لغو دریافت» را در هر پیام نگه دارید (GDPR / CCPA / CAN-SPAM). "
                               "از ارسال انبوه بدون شخصی‌سازی خودداری کنید.")})

    def _worker_tick(self, query: dict) -> None:
        """یک کار از صف را همین‌جا (در تابع serverless) اجرا می‌کند.

        چرا؟ روی Vercel نه داکری هست و نه پروسهٔ همیشه‌روشن؛ پس رابط کاربری،
        وقتی اجرایی در صف است، این نقطه را صدا می‌زند تا کار با موتور واقعیِ
        بدون داکر (OSM/Places) اجرا شود. با ورکر بیرونی (Codespace/Actions) هم
        تداخل ندارد چون کار با قفل اتمی claim می‌شود.
        """
        from . import worker as worker_mod

        if not jobs.queue_mode_active():
            return self._json({"processed": 0, "idle": True, "note": "حالت صف فعال نیست"}, 200)

        budget = max(12, min(52, _int(query.get("budget"), 42)))
        started = time.time()
        reclaimed = storage.requeue_stale_jobs(90)
        job = storage.claim_job(worker=f"tick-{config.WORKER_ID}")
        if not job:
            return self._json({
                "processed": 0, "idle": True, "reclaimed": reclaimed,
                "queue": storage.queue_stats(), "seconds": round(time.time() - started, 1),
                "note": "کاری در صف نبود (یا کار دیگری همین حالا برداشته شد).",
            }, 200)

        request = dict(job.get("request") or {})
        # بودجهٔ زمانی: بخشی برای غنی‌سازی/امتیاز/ذخیره نگه می‌داریم.
        # سهم غنی‌سازی، واکشی سایت‌ها برای پیدا کردن ایمیل و شبکه‌های اجتماعی است؛
        # بدون آن، لیدهای OSM معمولاً بدون ایمیل می‌مانند. با بودجهٔ محدود و
        # «توقف در مهلت» (enrich.py) داخل سقف ۶۰ثانیه‌ای تابع جا می‌شود.
        enrich_budget = 12.0 if budget >= 40 else 0.0
        engine_budget = max(12, int(budget - (enrich_budget + 12)))
        request["max_time"] = min(_int(request.get("max_time"), 60), engine_budget)
        request["enrich_live"] = bool(enrich_budget)
        if enrich_budget:
            request["enrich_budget"] = enrich_budget
        else:
            request.pop("enrich_budget", None)
        if _int(request.get("limit"), 0):
            request["limit"] = min(_int(request.get("limit"), 40), 60)
        job["request"] = request

        result = worker_mod.process_job(job, quiet=True)
        result.update({
            "processed": 1, "seconds": round(time.time() - started, 1),
            "reclaimed": reclaimed, "queue": storage.queue_stats(),
            "engine": pipeline.engine_status(None).get("active"),
        })
        record_progress(result.get("run_id") or job.get("run_id") or job["id"], {
            "stage": "done" if result.get("ok") else "failed",
            "status": "done" if result.get("ok") else "failed", "percent": 100,
            "message": self._tick_message(result),
            "stats": {"total": result.get("total", 0), "hot": result.get("hot", 0)},
            "error": "" if result.get("ok") else str(result.get("error") or ""),
            "run_id": result.get("run_id") or job.get("run_id") or job["id"],
        })
        return self._json(result, 200 if result.get("ok") else 200)

    @staticmethod
    def _tick_message(result: dict) -> str:
        """پیام پایان کار — برای اجرای خالی، پیشنهاد عملی به‌جای «۰ لید»."""
        if not result.get("ok"):
            return f"خطا: {result.get('error', 'نامشخص')}"
        if result.get("empty") or not result.get("total"):
            return ("در این محدوده چیزی پیدا نشد — محدودهٔ بزرگ‌تری (کل شهر/استان) یا صنف "
                    "دیگری را امتحان کن؛ برای پوشش بیشتر کلید Google Places را بگذار.")
        return f"تکمیل شد — {result.get('total', 0)} لید ({result.get('hot', 0)} داغ)"

    def _queue_snapshot(self, query: dict) -> dict:
        """وضعیت صف برای داشبورد: آمار + کارها + جای اجراها."""
        stats = storage.queue_stats()
        limit = _int(query.get("limit"), 20)
        payload = {
            "mode": config.QUEUE_MODE,
            "active": jobs.queue_mode_active(),
            "backend": config.DB_BACKEND,
            "worker": config.WORKER_ID,
            "worker_online": worker_online(),
            "self_drive": self_drive_allowed(),
            "stats": stats,
            "jobs": storage.list_jobs(None, limit=limit),
            "engine": pipeline.engine_status(None),
        }
        if stats.get("oldest_wait_s"):
            payload["wait_seconds"] = jobs.wait_time(stats["oldest_wait_s"])
        return payload

    def _start_run(self, body: dict) -> None:
        """اجرا را در یک رشته پس‌زمینه شروع می‌کند و فوراً شناسه را برمی‌گرداند."""
        request = pipeline.ScrapeRequest.from_dict(body)
        # جاروی «تمامی مشاغل» صنف مشخصی ندارد؛ خودش برنامهٔ صنف‌ها را می‌سازد
        if not (request.niche_query or request.keywords or request.all_niches):
            raise ApiError("«niche_query» (صنف) یا «keywords» لازم است — "
                           "یا «all_niches» برای جاروی تمامی مشاغل.")
        if not (request.city_query or (request.lat and request.lon)):
            raise ApiError("«city_query» یا مختصات lat/lon لازم است.")

        storage.init_db()
        preview_id = storage.new_run_id()
        request.run_id = preview_id          # همان شناسه تا پایان اجرا حفظ می‌شود
        request_dict = {k: getattr(request, k) for k in request.__dataclass_fields__}
        storage.create_run({
            "id": preview_id, "created_at": time.time(), "status": "pending",
            "city": request.city_query, "niche_fa": request.niche_query,
            "keywords": request.keywords, "depth": request.depth,
            "grid": request.grid or 1, "profile": request.profile,
            "socials": request.socials, "emails": request.emails,
            "engine": request.engine_mode or config.ENGINE_MODE, "live": None,
        })
        # ── حالت صف (ابر/serverless): ورکر جداگانه اجرا را برمی‌دارد ────────────
        if jobs.queue_mode_active():
            if config.MAX_PENDING_JOBS:
                pending = int((storage.queue_stats() or {}).get("pending", 0) or 0)
                if pending >= config.MAX_PENDING_JOBS:
                    raise ApiError(
                        f"الان {pending} کار در صف است (سقف {config.MAX_PENDING_JOBS}). "
                        "چند دقیقهٔ دیگر امتحان کن.", 429)
            queued = jobs.enqueue_run(request_dict, run_id=preview_id)
            position = jobs.queue_position(preview_id) or {}
            record_progress(preview_id, {
                "stage": "queued", "status": "queued", "percent": 1,
                "message": position.get("message") or "در صف اجرا — منتظر ورکر…",
                "run_id": preview_id, "job_id": queued["job_id"], "queue": position})
            return self._json({
                "run_id": preview_id, "status": "queued", "request": request_dict,
                "poll": f"/api/runs/{preview_id}", "job_id": queued["job_id"],
                "queue": queued.get("queue") or storage.queue_stats(),
                "position": position,
                "note": ("اجرا در صف است؛ ورکر (Codespace/داکر) آن را برمی‌دارد. "
                         "وضعیت زنده: " + f"/api/runs/{preview_id}/progress")}, 202)

        record_progress(preview_id, {"stage": "queued", "status": "pending", "percent": 1,
                                     "message": "در صف اجرا…", "run_id": preview_id})

        def worker() -> None:
            try:
                result = pipeline.run_scrape(request, on_progress=lambda p: record_progress(
                    p.get("run_id") or preview_id, p))
                record_progress(result.run_id, {
                    "stage": "done", "status": "done", "percent": 100,
                    "message": f"تکمیل شد — {result.stats['total']} لید "
                               f"({result.stats['hot']} داغ)",
                    "run_id": result.run_id, "stats": result.stats,
                    "warnings": result.warnings, "export_paths": result.export_paths,
                })
            except Exception as exc:
                record_progress(preview_id, {
                    "stage": "failed", "status": "failed", "percent": 100,
                    "message": f"خطا: {exc}", "error": str(exc),
                    "trace": traceback.format_exc().splitlines()[-8:], "run_id": preview_id,
                })

        threading.Thread(target=worker, name=f"scrape-{preview_id}", daemon=True).start()
        return self._json({"run_id": preview_id, "status": "pending",
                           "request": request_dict,
                           "poll": f"/api/runs/{preview_id}"}, 202)

    def _import_leads(self) -> None:
        """وارد کردن فایل (CSV/JSON) یا چسباندن متن → امتیازدهی فوری."""
        content_type = (self.headers.get("Content-Type") or "").lower()
        rows: list[dict] = []
        meta: dict = {}

        if content_type.startswith("multipart/form-data"):
            # پارسر دستی multipart — بدون ماژول منسوخ `cgi` (که در پایتون ۳٫۱۳ حذف شده)
            fields = parse_multipart(self._read_body(), content_type)
            file_part = fields.get("file")
            if file_part and file_part.get("data"):
                rows = self._parse_tabular(
                    file_part["data"].decode("utf-8-sig", "replace"),
                    file_part.get("filename", ""))
            for key in ("niche_query", "city", "profile"):
                if fields.get(key, {}).get("value"):
                    meta[key] = fields[key]["value"]
        elif content_type.startswith("application/json"):
            body = self._read_json()
            data = body.get("rows") or body.get("leads") or body.get("data")
            if isinstance(data, list):
                rows = [r for r in data if isinstance(r, dict)]
            elif isinstance(body.get("text"), str):
                rows = self._parse_tabular(body["text"], body.get("filename", ""))
            meta = {k: body.get(k) for k in ("niche_query", "city", "profile") if body.get(k)}
        else:
            raw = self._read_body()
            rows = self._parse_tabular(raw.decode("utf-8-sig", "replace"), "")

        if not rows:
            raise ApiError("هیچ ردیف قابل استفاده‌ای پیدا نشد. ستون‌های لازم: نام/تلفن/ایمیل/سایت.")
        result = pipeline.import_leads(rows, niche_query=str(meta.get("niche_query") or ""),
                                       city=str(meta.get("city") or ""),
                                       profile=str(meta.get("profile") or "generic"))
        return self._json({"ok": True, **result, "imported": len(rows)})

    @staticmethod
    def _parse_tabular(text: str, filename: str) -> list[dict]:
        import csv as _csv
        import io as _io
        text = (text or "").strip()
        if not text:
            return []
        if filename.lower().endswith(".json") or text.startswith(("[", "{")):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return []
            if isinstance(data, dict):
                data = data.get("leads") or data.get("rows") or data.get("data") or []
            return [r for r in data if isinstance(r, dict)]
        # تشخیص جداکننده: کاما، تب یا ;
        sample = text.splitlines()[0]
        delimiter = "\t" if "\t" in sample else (";" if sample.count(";") > sample.count(",") else ",")
        reader = _csv.DictReader(_io.StringIO(text), delimiter=delimiter)
        return [dict(row) for row in reader if any((v or "").strip() for v in row.values())]


def make_server(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    storage.init_db()
    config.ensure_dirs()
    httpd = ThreadingHTTPServer((host or config.HOST, port or config.PORT), Handler)
    httpd.daemon_threads = True
    return httpd


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="LeadGen Studio — داشبورد وب و API")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--print-engine", action="store_true", help="وضعیت موتور را چاپ کن و خارج شو")
    # argv=None یعنی همان sys.argv (وگرنه --host/--port بی‌اثر و همیشه پیش‌فرض می‌شد!)
    args = parser.parse_args(None if argv is None else list(argv))

    if args.print_engine:
        print(json.dumps(engine_mod.engine_status(), ensure_ascii=False, indent=2))
        return 0

    httpd = make_server(args.host, args.port)
    status = engine_mod.engine_status()
    print("━" * 72)
    print("  LeadGen Studio — پلتفرم لید کسب‌وکارهای محلی")
    print("  ساخته‌شده روی اسکیل google-maps-scraper (کیت Mahanaicoach)")
    print("━" * 72)
    print(f"  داشبورد      : http://{args.host}:{args.port}")
    print(f"  API          : http://{args.host}:{args.port}/api/health")
    print(f"  موتور فعال   : {status['active']} ({'واقعی' if status['live'] else 'دمو'})")
    print(f"  دلیل         : {status['reason']}")
    print(f"  موتور داکر   : {status['docker'].get('base_url')} → "
          f"{'UP' if status['docker'].get('up') else 'DOWN'}")
    if not status["live"]:
        print("  ⚠ برای داده واقعی: docker compose up -d  (سپس ENGINE_MODE=auto بماند)")
    print(f"  داده‌ها      : {config.DATA_DIR}")
    print("━" * 72)
    print("  برای توقف: Ctrl+C")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nدر حال خاموش شدن…")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
