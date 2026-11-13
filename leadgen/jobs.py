"""صف اجراها — لایهٔ نازک روی `storage` (SQLite محلی یا Supabase ابری).

چرا صف؟ در حالت ابری، وب‌اپ روی Vercel یک تابع serverless است و نمی‌تواند یک اجرای
چند دقیقه‌ای گوگل‌مپ را نگه دارد. پس:
    رابط/CLI  →  enqueue_run()  →  جدول jobs در پایگاه‌دادهٔ مشترک
    ورکر (Codespace / GitHub Actions / هر ماشین دارای داکر) → claim_job() → اجرا
نتایج در همان پایگاه‌داده ذخیره می‌شود و رابط، وضعیت را زنده نشان می‌دهد.
"""
from __future__ import annotations

import time
import uuid

from . import config
from .storage import (  # noqa: F401  — بازصادرات برای استفادهٔ مستقیم
    JOB_STATUSES,
    claim_job,
    enqueue_job,
    finish_job,
    heartbeat_job,
    list_jobs,
    queue_stats,
    requeue_stale_jobs,
)


def enqueue_run(request_dict: dict, *, run_id: str | None = None, priority: int = 0,
                create_row: bool = True) -> dict:
    """یک اجرا را در صف می‌گذارد و (اختیاری) ردیف run را با وضعیت «در صف» می‌سازد."""
    from . import storage

    run_id = run_id or request_dict.get("run_id") or storage.new_run_id()
    payload = dict(request_dict or {})
    payload["run_id"] = run_id
    if create_row:
        existing = None
        try:
            existing = storage.get_run(run_id)
        except Exception:  # pragma: no cover - شبکه/پایگاه‌داده در دسترس نیست
            existing = None
        meta = {
            "id": run_id, "created_at": time.time(), "status": "queued",
            "city": payload.get("city_query") or payload.get("city"),
            "niche_fa": payload.get("niche_query") or payload.get("niche_fa"),
            "keywords": payload.get("keywords") or [],
            "depth": payload.get("depth"), "grid": payload.get("grid"),
            "profile": payload.get("profile"), "socials": payload.get("socials"),
            "emails": payload.get("emails"),
            "engine": payload.get("engine_mode") or config.ENGINE_MODE,
            "province": payload.get("province"), "district": payload.get("district"),
            "street": payload.get("street"), "area_label": payload.get("area_label"),
            "area_level": payload.get("area_level"),
            "city_key": payload.get("city_key"), "country": payload.get("country"),
            "lat": payload.get("lat"), "lon": payload.get("lon"),
            "progress": {"stage": "queued", "status": "queued", "percent": 1,
                         "message": "در صف اجرا — منتظر ورکر…", "run_id": run_id,
                         "ts": time.time()},
        }
        if existing:
            storage.update_run(run_id, **{k: v for k, v in meta.items()
                                          if k not in ("id", "created_at")})
        else:
            storage.create_run(meta)
    job_id = enqueue_job(run_id, payload, priority=priority)
    return {"run_id": run_id, "job_id": job_id, "status": "queued",
            "queue": queue_stats() if config.supabase_ready() else {}}


def enqueue_enrich(target_run_id: str, *, budget: float = 25.0, limit: int = 0,
                   priority: int = 5) -> dict:
    """«غنی‌سازی مجدد» را در صف می‌گذارد تا با کرون/ورکر (بدون مرورگر) اجرا شود.

    چرا صف؟ اجرای بزرگ (مثلاً ۲۰۰ لید) در یک تابع ۶۰ثانیه‌ای جا نمی‌شود؛ کار در
    صف می‌رود و هر تپش بخشی از آن را جلو می‌برد. کار، ردیف run برای خودش نمی‌سازد
    (اجرای مقصد از قبل هست) تا فهرست اجراها شلوغ نشود.
    """
    from . import storage

    job_id = f"enrich-{target_run_id}-{uuid.uuid4().hex[:6]}"
    payload = {"task": "enrich", "target_run": target_run_id, "run_id": job_id,
               "enrich_budget": float(budget), "limit": int(limit)}
    storage.enqueue_job(job_id, payload, priority=priority)
    return {"queued": True, "job_id": job_id, "target_run": target_run_id,
            "budget": float(budget), "status": "queued"}


def queue_mode_active() -> bool:
    """آیا اجراها باید به صف بروند (به‌جای اجرای همزمان در همان فرایند)؟"""
    return config.queue_enabled()


def queue_position(run_id: str) -> dict | None:
    """جای یک اجرا در صف — برای نمایش «نفر چندم هستید» در رابط کاربری.

    ترتیب صف همان ترتیب برداشتن کار است: اولویت بالاتر، سپس زمان ایجاد زودتر.
    """
    from . import storage

    try:
        pending = storage.list_jobs("pending", limit=500)
        running = storage.list_jobs("running", limit=200)
    except Exception:  # pragma: no cover - پایگاه‌داده در دسترس نیست
        return None
    if run_id in {job.get("run_id") for job in running}:
        return {"position": 0, "running": True, "pending": len(pending),
                "wait_seconds": 0, "message": "در حال اجرا"}
    ordered = sorted(pending, key=lambda job: (-int(job.get("priority") or 0),
                                               float(job.get("created_at") or 0)))
    for index, job in enumerate(ordered, 1):
        if job.get("run_id") == run_id:
            return {"position": index, "running": False, "pending": len(pending),
                    "wait_seconds": int(index * config.WORKER_POLL_SECONDS),
                    "message": f"نفر {index} در صف ({len(pending)} کار در انتظار)"}
    return None


def wait_time(oldest_wait_s: int = 0) -> int:
    """فاصلهٔ بعدی بررسی صف — وقتی کار در انتظار است کوتاه‌تر می‌شود."""
    base = max(1, config.WORKER_POLL_SECONDS)
    if oldest_wait_s:
        return max(1, min(base, 5))
    return base
