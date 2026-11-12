"""پشتیبان پایگاه‌دادهٔ ابری: همان API ماژول `storage` ولی روی Supabase/Postgres.

چرا این ماژول؟
  • اپ روی Vercel (serverless) و ورکر داخل Codespace باید **یک دادهٔ مشترک** ببینند؛
    SQLite محلی برای آن کافی نیست.
  • جست‌وجوی قوی، facets، شعاع دایره‌ای و صف اجراها داخل توابع SQL پیاده شده‌اند
    (`supabase/schema.sql`)، پس هر فراخوانی یک round-trip است.
  • رفتار دقیقاً آینهٔ نسخهٔ SQLite است: همان کلیدها، همان facets، همان مرتب‌سازی
    (آزمون هم‌ارزی در `tests/test_cloud_db.py`).

هیچ وابستگی بیرونی ندارد — فقط `urllib` (از راه `leadgen.cloud`).
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from . import cloud, config
from .cloud import CloudError

SCHEMA_HINT = (
    "جدول‌های Supabase پیدا نشد. فایل supabase/schema.sql را یک‌بار روی پروژه اجرا کنید "
    "(داشبورد Supabase → SQL Editor → Run) و دوباره تلاش کنید."
)

# ستون‌های jsonb که باید بومی (list/dict) فرستاده شوند، نه رشتهٔ JSON
_JSON_COLUMNS = ("gaps", "tags", "score_factors", "signals")
_INT_COLUMNS = ("has_website", "has_email", "has_phone", "has_social", "score",
                "completeness", "reviews")
_FLOAT_COLUMNS = ("rating", "created_at")


def _storage():
    """دسترسی تنبل به ماژول SQLite برای بازاستفاده از نرمال‌سازها (بدون import دایره‌ای)."""
    from . import storage
    return storage


def _client() -> cloud.SupabaseClient:
    return cloud.client()


# ── راه‌اندازی ────────────────────────────────────────────────────────────────
def init_db() -> None:
    """وجود schema را بررسی می‌کند؛ در Supabase ساخت جدول با migration انجام می‌شود."""
    try:
        _client().request("GET", "runs", params={"select": "id", "limit": 1})
    except CloudError as exc:
        detail = str(exc.detail or "")
        if exc.status in (404, 400) and ("does not exist" in detail or "42P01" in detail):
            raise CloudError(SCHEMA_HINT, exc.status, detail) from exc
        raise


def backend_info() -> dict:
    health = _client().health()
    health.update({"queue": queue_stats() if health.get("ok") else {}})
    return health


def new_run_id() -> str:
    return time.strftime("%Y%m%d-") + uuid.uuid4().hex[:6]


# ── اجراها ────────────────────────────────────────────────────────────────────
def _run_payload(meta: dict) -> dict:
    storage = _storage()
    payload: dict[str, Any] = {}
    for key in ("id", "niche_id", "niche_fa", "city", "city_key", "country", "lat", "lon",
                "depth", "radius", "grid", "lang", "profile", "engine", "status", "total",
                "hot", "warm", "cold", "avg_score", "error", "province", "district",
                "street", "area_label", "area_level"):
        if key in meta and meta[key] is not None:
            payload[key] = meta[key]
    for key in ("keywords", "job_ids", "progress", "analytics"):
        if key in meta and meta[key] is not None:
            value = meta[key]
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    value = [] if key != "analytics" and key != "progress" else {}
            payload[key] = value
    for key in ("socials", "emails"):
        if key in meta and meta[key] is not None:
            payload[key] = bool(meta[key])
    if "live" in meta and meta["live"] is not None:
        payload["live"] = bool(meta["live"])
    created = meta.get("created_at")
    if created:                       # در patch_run نباید زمان ساخت را بازنویسی کرد
        payload["created_at"] = float(created)
    # وضعیت‌های صف برای رابط فارسی‌شده هم معنی داشته باشند
    if payload.get("status") == "queued":
        payload.setdefault("progress", {})
    _ = storage  # (برچسب‌ها سمت پایتون اضافه می‌شوند)
    # هر شیء غیرJSON (مثل Niche در payload پیشرفت) پیش از ارسال به PostgREST تبدیل شود
    return cloud.jsonable(payload)


def create_run(meta: dict) -> str:
    payload = _run_payload(meta)
    payload["id"] = meta.get("id") or new_run_id()
    payload.setdefault("created_at", time.time())
    _client().rpc("save_run", {"payload": payload})
    return payload["id"]


def update_run(run_id: str, **fields) -> None:
    if not fields:
        return
    _client().rpc("patch_run", {"p_id": run_id, "payload": _run_payload(fields)})


def get_run(run_id: str) -> dict | None:
    rows, _ = _client().rows("runs", select="*", id=f"eq.{run_id}", limit=1)
    return _run_dict(rows[0]) if rows else None


def list_runs(limit: int = 100) -> list[dict]:
    rows, _ = _client().rows("runs", select="*", order="created_at.desc", limit=int(limit))
    return [_run_dict(r) for r in rows]


def _run_dict(data: dict) -> dict:
    storage = _storage()
    row = dict(data)
    for key in ("keywords", "job_ids"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value or "[]")
            except json.JSONDecodeError:
                value = []
        row[key] = value or []
    for key, empty in (("progress", {}), ("analytics", None)):
        value = row.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value or "null")
            except json.JSONDecodeError:
                value = empty
        row[key] = value if value is not None else empty
    for key in ("live", "socials", "emails"):
        if row.get(key) is not None:
            row[key] = bool(row[key])
    row["status_fa"] = {
        "pending": "در صف", "queued": "در صف", "running": "در حال اجرا", "enriching": "غنی‌سازی",
        "scoring": "امتیازدهی", "done": "تکمیل شد", "failed": "ناموفق", "partial": "نسبی",
    }.get(row.get("status"), row.get("status"))
    _ = storage
    return row


def delete_run(run_id: str) -> bool:
    result = _client().rpc("delete_run", {"p_run_id": run_id})
    return bool(result)


# ── لیدها ─────────────────────────────────────────────────────────────────────
def _lead_record(run_id: str, row: dict) -> dict:
    storage = _storage()
    record: dict[str, Any] = {
        "id": row.get("id") or f"{run_id}-{uuid.uuid4().hex[:10]}",
        "run_id": run_id,
        "created_at": float(row.get("created_at") or time.time()),
        "note": row.get("note", "") or "",
    }
    for column in storage.LEAD_COLUMNS:
        value = row.get(column)
        if column == "status" and value not in storage.LEAD_STATUSES:
            value = "new"
        if column in _JSON_COLUMNS:
            if isinstance(value, str):
                try:
                    value = json.loads(value or ("{}" if column == "signals" else "[]"))
                except json.JSONDecodeError:
                    value = {} if column == "signals" else []
            record[column] = value if value is not None else ({} if column == "signals" else [])
        elif column in _INT_COLUMNS:
            try:
                record[column] = int(bool(value)) if column.startswith("has_") else int(value or 0)
            except (TypeError, ValueError):
                record[column] = 0
        elif column in _FLOAT_COLUMNS:
            try:
                record[column] = float(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                record[column] = None
        elif isinstance(value, bool):
            record[column] = int(value)
        elif isinstance(value, (list, dict)):
            record[column] = json.dumps(value, ensure_ascii=False)
        else:
            record[column] = value
    if not record.get("status"):
        record["status"] = "new"
    row["id"] = record["id"]
    return record


def save_leads(run_id: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    records = [_lead_record(run_id, row) for row in rows]
    return _client().insert("leads", records, upsert=True, on_conflict="id")


def get_lead(lead_id: str) -> dict | None:
    rows, _ = _client().rows("leads", select="*", id=f"eq.{lead_id}", limit=1)
    return _storage()._lead_to_dict(rows[0]) if rows else None


def set_lead_status(lead_id: str, status: str, note: str | None = None) -> bool:
    storage = _storage()
    if status not in storage.LEAD_STATUSES:
        return False
    values: dict[str, Any] = {"status": status}
    if note is not None:
        values["note"] = note
    _client().update("leads", values, id=f"eq.{lead_id}")
    return True


def all_leads_for_export(run_id: str) -> list[dict]:
    result = _client().rpc("search_leads", {"params": {
        "run_id": run_id, "sort": "score", "order": "desc", "limit": 1000}})
    return [_normalize_lead(row) for row in (result or {}).get("leads", [])]


def count_leads(run_id: str | None = None) -> int:
    filters = {"run_id": f"eq.{run_id}"} if run_id else {}
    return _client().count("leads", **filters)


# ── جست‌وجوی قوی ──────────────────────────────────────────────────────────────
def _normalize_lead(row: dict) -> dict:
    storage = _storage()
    item = storage._lead_to_dict(row)
    item["run_city"] = row.get("run_city") or ""
    item["run_niche"] = row.get("run_niche") or ""
    item["run_area"] = row.get("run_area") or item["run_city"]
    item["run_created"] = row.get("run_created")
    distance = row.get("distance_m")
    if distance is not None:
        try:
            item["distance_m"] = round(float(distance))
        except (TypeError, ValueError):
            item["distance_m"] = None
    return item


def _search_params(*, q: str = "", run_id: str | None = None, segment: str | None = None,
                   status: str | None = None, channel: str | None = None,
                   gap: str | None = None, niche_id: str | None = None,
                   niche_group: str | None = None, niche_fa: str | None = None,
                   has_phone: bool | None = None, has_email: bool | None = None,
                   has_website: bool | None = None, has_social: bool | None = None,
                   has_instagram: bool | None = None, min_rating: float | None = None,
                   min_reviews: int | None = None, min_score: int | None = None,
                   lat: float | None = None, lon: float | None = None, radius_m: int = 0,
                   sort: str = "score", order: str = "desc",
                   limit: int = 200, offset: int = 0) -> dict:
    params: dict[str, Any] = {"sort": sort, "order": order,
                              "limit": int(limit), "offset": int(offset)}
    simple = {"q": q, "run_id": run_id, "segment": segment, "status": status,
              "channel": channel, "gap": gap, "niche_id": niche_id, "niche_fa": niche_fa}
    for key, value in simple.items():
        if value not in (None, ""):
            params[key] = value
    for key, value in (("has_phone", has_phone), ("has_email", has_email),
                       ("has_website", has_website), ("has_social", has_social),
                       ("has_instagram", has_instagram)):
        if value is not None:
            params[key] = bool(value)
    for key, value in (("min_rating", min_rating), ("min_reviews", min_reviews),
                       ("min_score", min_score)):
        if value is not None:
            params[key] = value
    if lat is not None and lon is not None and radius_m:
        params.update({"lat": float(lat), "lon": float(lon), "radius_m": int(radius_m)})
    if niche_group:
        ids = [n["id"] for n in _storage()._group_niches(niche_group)]
        if ids:
            params["niche_ids"] = ids
    return params


_FACET_GROUPS = ("segment", "status", "niche", "contact", "runs")


def _normalize_facets(facets: dict | None, leads: list[dict], total: int) -> dict:
    """facets ابری را هم‌شکل نسخهٔ SQLite می‌کند.

    تابع SQL همهٔ کلیدها را حتی با شمارش صفر برمی‌گرداند (`{"has_email": 0}`)، ولی نسخهٔ
    محلی فقط شمارش‌های غیرصفر را می‌گذارد. برای هم‌ارزی کامل، صفرها را حذف می‌کنیم و
    کلیدهای ثابت (`total`, `counted`, `partial`, `score`) را پر می‌کنیم.
    """
    base = _storage()._facets([], total or 0)
    data = dict(facets or {})
    out = {}
    for group in _FACET_GROUPS:
        values = data.get(group) or {}
        if isinstance(values, dict):
            out[group] = {str(key): int(value) for key, value in values.items()
                          if value not in (None, 0, "0")}
        else:
            out[group] = {}
    score = data.get("score") or {}
    out["score"] = {"min": score.get("min", 0), "max": score.get("max", 0),
                    "avg": score.get("avg", 0)} if isinstance(score, dict) else base["score"]
    out["total"] = int(data.get("total") or total or 0)
    out["counted"] = int(data.get("counted") if data.get("counted") is not None else len(leads))
    out["partial"] = bool(data.get("partial", False))
    return out


def search_leads(**kwargs) -> dict:
    storage = _storage()
    params = _search_params(**kwargs)
    result = _client().rpc("search_leads", {"params": params}) or {}
    leads = [_normalize_lead(row) for row in result.get("leads") or []]
    total = int(result.get("total") or 0)
    facets = _normalize_facets(result.get("facets"), leads, total)
    geo = {}
    if "radius_m" in params:
        geo = {"lat": params.get("lat"), "lon": params.get("lon"),
               "radius_m": params.get("radius_m")}
    scope = dict(kwargs)
    scope.setdefault("q", "")
    return {"leads": leads, "total": total, "returned": len(leads),
            "facets": facets, "geo": geo, "filters": storage._active_filters(scope)}


def load_leads(run_id: str | None = None, *, segment: str | None = None,
               status: str | None = None, query: str | None = None,
               gap: str | None = None, channel: str | None = None,
               min_score: int | None = None, sort: str = "score",
               order: str = "desc", limit: int = 1000, offset: int = 0) -> list[dict]:
    result = search_leads(q=query or "", run_id=run_id, segment=segment, status=status,
                          gap=gap, channel=channel, min_score=min_score, sort=sort,
                          order=order, limit=limit, offset=offset)
    return result["leads"]


# ── آمار ──────────────────────────────────────────────────────────────────────
def pipeline_stats() -> dict:
    data = _client().rpc("dashboard_stats", {}) or {}
    return {"runs": int(data.get("runs") or 0), "leads": int(data.get("leads") or 0),
            "contacted": int(data.get("contacted") or 0), "won": int(data.get("won") or 0),
            "with_email": int(data.get("with_email") or 0)}


def dashboard_stats() -> dict:
    return _client().rpc("dashboard_stats", {}) or {}


# ── صف اجراها ────────────────────────────────────────────────────────────────
def _job_dict(job: dict) -> dict:
    """ردیف job ابری را هم‌شکل نسخهٔ SQLite می‌کند (request به dict + status_fa)."""
    data = dict(job)
    request = data.get("request")
    if isinstance(request, str):
        try:
            request = json.loads(request)
        except json.JSONDecodeError:
            request = {}
    data["request"] = request if isinstance(request, dict) else {}
    data["status_fa"] = _storage().job_status_fa(data.get("status"))
    return data


def enqueue_job(run_id: str, request: dict, priority: int = 0) -> str:
    """هم‌ارز نسخهٔ SQLite: شناسهٔ کار را برمی‌گرداند (نه کل ردیف)."""
    result = _client().rpc("enqueue_job", {
        "p_run_id": run_id, "p_request": cloud.jsonable(request or {}),
        "p_priority": int(priority or 0)})
    if isinstance(result, dict):
        return str(result.get("id") or run_id)
    return str(result or run_id)


def claim_job(worker: str | None = None, max_attempts: int | None = None) -> dict | None:
    payload = {"p_worker": worker or config.WORKER_ID,
               "p_max_attempts": int(max_attempts or config.WORKER_MAX_ATTEMPTS)}
    job = _client().rpc("claim_job", payload)
    if not isinstance(job, dict) or not job:
        return None
    return _job_dict(job)


def list_jobs(status: str | None = None, limit: int = 50) -> list[dict]:
    """فهرست کارها (هم‌شکل SQLite) — برای داشبورد صف و محاسبهٔ جای کاربر در صف."""
    filters = {"status": f"eq.{status}"} if status else {}
    rows, _ = _client().rows("jobs", select="*", limit=int(limit),
                             order="created_at.desc", **filters)
    return [_job_dict(row) for row in rows]


def heartbeat_job(job_id: str, progress: dict | None = None, status: str | None = None) -> bool:
    return bool(_client().rpc("heartbeat_job", {
        "p_id": job_id, "p_progress": cloud.jsonable(progress) if progress else progress,
        "p_status": status}))


def finish_job(job_id: str, status: str = "done", error: str | None = None) -> dict | None:
    job = _client().rpc("finish_job", {"p_id": job_id, "p_status": status, "p_error": error})
    return _job_dict(job) if isinstance(job, dict) and job else (job or None)


def requeue_stale_jobs(seconds: int = 1800) -> int:
    result = _client().rpc("requeue_stale_jobs", {"p_seconds": float(seconds)})
    try:
        return int(result or 0)
    except (TypeError, ValueError):
        return 0


def queue_stats() -> dict:
    return _client().rpc("queue_stats", {}) or {}


def log_delivery(kind: str, channel: str, target: str, subject: str, status: str,
                 detail: dict | None = None) -> str:
    return _client().rpc("log_delivery", {
        "p_kind": kind, "p_channel": channel, "p_target": target, "p_subject": subject,
        "p_status": status, "p_detail": cloud.jsonable(detail or {})}) or ""


def last_delivery(kind: str = "digest") -> dict | None:
    return _client().rpc("last_delivery", {"p_kind": kind})


def data_dir():
    config.ensure_dirs()
    return config.DATA_DIR
