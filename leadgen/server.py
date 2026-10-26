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
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import (analytics, campaign, config, engine as engine_mod, enrich, exports,
               geo, niches, pipeline, scoring, storage)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

# حافظه پیشرفت اجراها (در حال اجرا) — پایگاه‌داده هم وضعیت نهایی را نگه می‌دارد
_PROGRESS: dict[str, dict] = {}
_PROGRESS_LOCK = threading.Lock()
MAX_PROGRESS = 400


def record_progress(run_id: str, payload: dict) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS[run_id] = {**payload, "ts": time.time()}
        if len(_PROGRESS) > MAX_PROGRESS:
            for key in list(_PROGRESS)[:-MAX_PROGRESS]:
                _PROGRESS.pop(key, None)


def get_progress(run_id: str) -> dict:
    with _PROGRESS_LOCK:
        return dict(_PROGRESS.get(run_id) or {})


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
        storage.init_db()

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/assets/") or path in ("/styles.css", "/app.js", "/favicon.svg"):
            return self._static(path.lstrip("/"))

        if path == "/api/health":
            return self._json({
                "ok": True,
                "version": "1.0.0",
                "engine": engine_mod.engine_status(),
                "config": config.summary(),
                "db": storage.pipeline_stats(),
            })

        if path == "/api/catalog":
            return self._json({
                "niches": niches.all_niches(),
                "groups": niches.GROUPS,
                "cities": geo.known_cities(),
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
            return self._json({"cities": geo.known_cities()})
        if path == "/api/profiles":
            return self._json({"profiles": scoring.profile_list()})

        raise ApiError(f"مسیر پیدا نشد: {path}", 404)

    def _route_write(self, method: str = "POST") -> None:
        path, query = self._path, self._query
        storage.init_db()

        if path == "/api/runs" and method == "POST":
            return self._start_run(self._read_json())

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
    def _static(self, rel: str) -> None:
        target = (WEB_DIR / rel).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
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
        return self._json({
            "leads": rows,
            "count": len(rows),
            "total": storage.count_leads(run_id),
            "statuses": storage.LEAD_STATUS_FA,
        })

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
            sheets = exports.build_workbook(rows, pack, run, campaign_rows, full=full)
            body = exports.xlsx_bytes(sheets)
            name = f"leads-{run_id}.xlsx"
            return self._send(200, body,
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              {"Content-Disposition": f'attachment; filename="{name}"'})

        body = exports.csv_bytes(rows, full=full)
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

    def _start_run(self, body: dict) -> None:
        """اجرا را در یک رشته پس‌زمینه شروع می‌کند و فوراً شناسه را برمی‌گرداند."""
        request = pipeline.ScrapeRequest.from_dict(body)
        if not (request.niche_query or request.keywords):
            raise ApiError("«niche_query» (صنف) یا «keywords» لازم است.")
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
    args = parser.parse_args(argv or [])

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
