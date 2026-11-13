"""ورکر اجراها — صف را برمی‌دارد و با موتور (داکر/دمو) اجرا می‌کند.

کجا اجرا می‌شود؟
  • داخل GitHub Codespace که موتور اسکراپر با `docker compose up -d` بالا است
  • در GitHub Actions (workflow `worker.yml`) — بدون هیچ سرور همیشه‌روشن
  • یا روی هر ماشین/VPS که داکر دارد

چرا جدا از وب‌اپ؟ اپ ابری (Vercel) نمی‌تواند یک اجرای چند دقیقه‌ای را در یک تابع
serverless نگه دارد؛ پس اجراها در پایگاه‌دادهٔ مشترک به صف می‌روند و ورکر آن‌ها را
برمی‌دارد. نتیجه‌ها در همان پایگاه‌داده (Supabase یا SQLite) ذخیره می‌شوند.

کاربرد:
    python3 -m leadgen.worker                 # حلقهٔ دائمی
    python3 -m leadgen.worker --once          # یک دور (مناسب CI/کرون)
    python3 -m leadgen.worker --max-jobs 3 --deliver
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback

from . import config, jobs, pipeline, storage

# فاصلهٔ حداقلی بین دو heartbeat ابری (تا درخواست‌های HTTP زیاد نشود)
HEARTBEAT_MIN_SECONDS = 2.0
HEARTBEAT_MIN_PERCENT = 10


def _log(message: str, *, quiet: bool = False) -> None:
    if quiet:
        return
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def _throttled_heartbeat(job_id: str, state: dict):
    """heartbeat را محدود می‌کند: تغییر مرحله، جهش درصد، یا گذشت زمان."""
    now = time.time()
    percent = int(state.get("percent") or 0)
    stage = state.get("stage") or ""
    terminal = state.get("status") in ("done", "failed", "partial")
    fresh = (now - state.get("_last_sent", 0) >= HEARTBEAT_MIN_SECONDS)
    jumped = abs(percent - state.get("_last_percent", -99)) >= HEARTBEAT_MIN_PERCENT
    changed = stage != state.get("_last_stage")
    if terminal or fresh and (jumped or changed or percent >= 100):
        payload = {k: v for k, v in state.items() if not k.startswith("_")}
        try:
            # «status» در payload پیشرفت یعنی مرحلهٔ اجرا («working»، «enrich»…) و
            # نباید در ستون jobs.status نوشته شود؛ وگرنه کار به وضعیت نامعتبر
            # «working» می‌رفت، از شمارش/بازپس‌گیری صف بیرون می‌افتاد و صف
            # برای همیشه «خالی» دیده می‌شد. وضعیت کار را فقط claim/finish می‌نویسند.
            storage.heartbeat_job(job_id, payload, None)
        except Exception:  # pragma: no cover - شبکه ناپایدار؛ اجرا نباید بمیرد
            pass
        state["_last_sent"] = now
        state["_last_percent"] = percent
        state["_last_stage"] = stage


def process_job(job: dict, *, engine_mode: str | None = None, quiet: bool = False,
                deliver: bool = False) -> dict:
    """یک کارِ برداشته‌شده را اجرا می‌کند و نتیجه را ثبت می‌کند."""
    job_id = job["id"]
    run_id = job.get("run_id") or job_id
    request_dict = dict(job.get("request") or {})
    if str(request_dict.get("task") or "").strip().lower() == "enrich":
        return _process_enrich_job(job, request_dict, quiet=quiet)
    request_dict["run_id"] = run_id
    if engine_mode:
        request_dict["engine_mode"] = engine_mode
    request = pipeline.ScrapeRequest.from_dict(request_dict)

    state: dict = {"_last_sent": 0.0, "_last_percent": -99, "_last_stage": ""}
    label = (request_dict.get("niche_query") or request_dict.get("area_label")
             or request_dict.get("city_query") or run_id)
    _log(f"▶ اجرای «{label}» (کار {job_id}، تلاش {job.get('attempts', 1)})", quiet=quiet)

    started = time.time()
    try:
        storage.update_run(run_id, status="running",
                           progress={"stage": "working", "status": "running", "percent": 2,
                                     "message": "ورکر کار را برمی‌داشت…", "run_id": run_id,
                                     "worker": config.WORKER_ID, "ts": started})

        def on_progress(payload: dict) -> None:
            state.update({k: v for k, v in (payload or {}).items() if not k.startswith("_")})
            _throttled_heartbeat(job_id, state)

        result = pipeline.run_scrape(request, on_progress=on_progress)
        stats = result.stats or {}
        summary = {
            "job_id": job_id, "run_id": result.run_id, "ok": True,
            "total": stats.get("total", 0), "hot": stats.get("hot", 0),
            "seconds": round(time.time() - started, 1),
            "engine": (result.run or {}).get("engine"),
            "empty": bool(stats.get("empty")),
            "warnings": result.warnings or [],
            "exports": result.export_paths or [],
        }
        storage.finish_job(job_id, "done")
        _log(f"✔ تمام شد — {summary['total']} لید ({summary['hot']} داغ) "
             f"در {summary['seconds']} ثانیه · موتور: {summary['engine']}", quiet=quiet)
        continued = _queue_continuation(result, quiet=quiet)
        if not continued:
            # اجرای دیدبانی: فقط وقتی همهٔ بخش‌های منطقه تمام شد، ایمیل «تازه‌ها»
            _deliver_watch(result, request_dict, quiet=quiet)
        if deliver:
            _deliver_after_run(summary, quiet=quiet)
        return summary
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        storage.finish_job(job_id, "failed", detail)
        _log(f"✖ شکست — {detail}", quiet=quiet)
        if config.env("WORKER_DEBUG") == "1":
            traceback.print_exc()
        return {"job_id": job_id, "run_id": run_id, "ok": False, "error": detail,
                "seconds": round(time.time() - started, 1)}


def _queue_continuation(result, *, quiet: bool = False) -> bool:
    """بخش بعدی اسکراب کامل را خودکار در صف می‌گذارد (بدون دخالت کاربر).

    چرا؟ یک اجرای استانی/شهری بزرگ در بودجهٔ زمانی یک تابع serverless جا
    نمی‌شود. اجرا بخشی را انجام می‌دهد، می‌گوید بقیه کجاست و بلافاصله کار بعدی
    با همان run_id در صف می‌نشیند؛ نتیجه: «هیچ داده‌ای جا نمی‌ماند».
    """
    continuation = (getattr(result, "stats", None) or {}).get("continuation")
    if not continuation:
        return False
    try:
        if not jobs.queue_mode_active():
            return False
        payload = dict(continuation.get("request") or {})
        run_id = continuation.get("run_id") or payload.get("run_id")
        payload["run_id"] = run_id
        payload["resume_of"] = run_id
        chunk = int(continuation.get("chunk") or 1)
        # شناسهٔ کار همان run_id است (هر بخش، همان ردیف را دوباره زنده می‌کند) تا
        # جدول صف یک اسکراب را یک ردیف نشان دهد، نه چند تکهٔ پراکنده.
        # اولویت ۰ = هم‌صف با اجراهای کاربر (FIFO). قبلاً ۳ بود و زنجیرهٔ
        # چندبخشی، اجرای تازه‌ای که کاربر همین حالا خواسته بود را پشت خود حبس
        # می‌کرد. الان در هر لحظه حداکثر یک بخش در صف است و کاربر حداکثر یک
        # تپش (~۱ دقیقه) منتظر می‌ماند، ولی اسکراب هم زمین نمی‌ماند.
        storage.enqueue_job(run_id, payload, priority=0)
        _log(f"↻ بخش {chunk} اسکراب در صف گذاشته شد "
             f"({continuation.get('remaining')} بخش باقی‌مانده)", quiet=quiet)
        storage.update_run(run_id, progress={
            "stage": "queued", "status": "queued", "percent": 2,
            "message": f"بخش {chunk} اسکراب در صف — "
                       f"{continuation.get('remaining')} بخش باقی‌مانده",
            "run_id": run_id})
        return True
    except Exception as exc:  # noqa: BLE001 — شکست صف نباید نتیجهٔ اجرا را خراب کند
        _log(f"⚠ صف کردن بخش بعدی نشد: {type(exc).__name__}: {exc}", quiet=quiet)
        return False


def _deliver_watch(result, request_dict: dict, *, quiet: bool = False) -> None:
    """اگر اجرا از «دیدبان» آمده، ایمیل «فقط تازه‌ها» را می‌فرستد (اگر تنظیم شده)."""
    if not (request_dict or {}).get("watch_id"):
        return
    try:
        from . import watchlist
        outcome = watchlist.maybe_deliver(
            getattr(result, "run_id", ""), request_dict, getattr(result, "analytics", None))
    except Exception as exc:  # noqa: BLE001 — ایمیل نباید نتیجهٔ اجرا را خراب کند
        _log(f"⚠ ایمیل دیدبان ناموفق: {type(exc).__name__}: {exc}", quiet=quiet)
        return
    if not outcome:
        return
    status = outcome.get("status")
    if status == "deferred":
        _log(f"👁 دیدبان: ایمیل تا تمام‌شدن بخش‌های اسکراب عقب افتاد "
             f"({outcome.get('remaining')} بخش)", quiet=quiet)
    elif status == "empty":
        _log("👁 دیدبان: چیز تازه‌ای نبود؛ ایمیلی فرستاده نشد", quiet=quiet)
    else:
        _log(f"👁 دیدبان: {outcome.get('shown', 0)} تازه → ایمیل {status}", quiet=quiet)


def _process_enrich_job(job: dict, request_dict: dict, *, quiet: bool = False) -> dict:
    """کارِ «غنی‌سازی مجدد»: لیدهای یک اجرای موجود را کامل‌تر می‌کند."""
    job_id = job["id"]
    target = str(request_dict.get("target_run") or "").strip()
    budget = float(request_dict.get("enrich_budget") or 25.0)
    limit = int(request_dict.get("limit") or 0)
    started = time.time()
    if not target:
        storage.finish_job(job_id, "failed", "شناسهٔ اجرای مقصد داده نشده")
        return {"job_id": job_id, "ok": False, "task": "enrich",
                "error": "شناسهٔ اجرای مقصد داده نشده"}
    _log(f"✨ غنی‌سازی مجدد اجرای {target} (کار {job_id}، بودجهٔ {budget:.0f} ثانیه)", quiet=quiet)
    state: dict = {"_last_sent": 0.0}

    def on_progress(payload: dict) -> None:
        state.update({k: v for k, v in (payload or {}).items() if not k.startswith("_")})
        _throttled_heartbeat(job_id, state)

    try:
        result = pipeline.enrich_run(target, budget_s=budget, limit=limit,
                                     on_progress=on_progress)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        storage.finish_job(job_id, "failed", detail)
        _log(f"✖ غنی‌سازی مجدد شکست خورد — {detail}", quiet=quiet)
        return {"job_id": job_id, "run_id": target, "ok": False, "task": "enrich",
                "error": detail, "seconds": round(time.time() - started, 1)}
    storage.finish_job(job_id, "done")
    summary = {
        "job_id": job_id, "run_id": target, "ok": True, "task": "enrich",
        "total": result.get("total", 0), "hot": 0,
        "seconds": round(time.time() - started, 1), "engine": "enrich",
        "empty": not result.get("total"),
        "warnings": [result.get("note", "")], "exports": [],
        "checked": result.get("checked", 0),
        "email": result.get("email", {}), "social": result.get("social", {}),
    }
    _log(f"✔ غنی‌سازی مجدد — {result.get('note', '')}", quiet=quiet)
    return summary


def _deliver_after_run(summary: dict, *, quiet: bool = False) -> None:
    """پس از اجرای موفق، گزارش ایمیلی لیدهای داغ را می‌فرستد (اگر پیکربندی شده باشد)."""
    try:
        from . import delivery
    except Exception as exc:  # pragma: no cover
        _log(f"⚠ تحویل ایمیلی در دسترس نیست: {exc}", quiet=quiet)
        return
    if config.email_transport() == "none" or not config.DIGEST_TO:
        _log("⚠ ایمیل پیکربندی نشده (DIGEST_TO / GMAIL_*) — گزارش ارسال نشد", quiet=quiet)
        return
    try:
        result = delivery.send_digest(run_id=summary.get("run_id"))
        _log(f"✉ گزارش ایمیلی: {result.get('status')} → {result.get('target')}", quiet=quiet)
    except Exception as exc:
        _log(f"⚠ ارسال گزارش ناموفق: {exc}", quiet=quiet)


def check_engine(engine_mode: str | None = None, *, quiet: bool = False) -> dict:
    """وضعیت موتور را پیش از برداشتن کار بررسی می‌کند (تشخیص سریع داکر خاموش)."""
    status = pipeline.engine_status(engine_mode) if hasattr(pipeline, "engine_status") else {}
    if not status:
        from . import engine as engine_module
        status = engine_module.engine_status(engine_mode)
    active = status.get("active")
    if active == "docker" and status.get("up"):
        _log("موتور واقعی در دسترس است (داکر)", quiet=quiet)
    elif active == "demo":
        _log("موتور دمو فعال است — دادهٔ مصنوعی تولید می‌شود "
             "(برای دادهٔ واقعی: docker compose up -d)", quiet=quiet)
    else:
        _log(f"⚠ موتور در دسترس نیست: {status.get('reason') or status.get('note') or 'نامشخص'}",
             quiet=quiet)
    return status


def run_worker(*, once: bool = False, max_jobs: int = 0, interval: int | None = None,
               engine_mode: str | None = None, stale_seconds: int = 1800,
               deliver: bool = False, quiet: bool = False) -> dict:
    """حلقهٔ اصلی ورکر: کارهای در صف را برمی‌دارد و اجرا می‌کند."""
    poll = int(interval or config.WORKER_POLL_SECONDS)
    cap = int(max_jobs or config.WORKER_MAX_JOBS or 0)
    processed = succeeded = failed = idle_rounds = 0
    started = time.time()

    storage.init_db()
    reclaimed = storage.requeue_stale_jobs(stale_seconds)
    if reclaimed:
        _log(f"♻ {reclaimed} کار نیمه‌تمامِ ورکرِ قبلی به صف برگشت", quiet=quiet)
    check_engine(engine_mode, quiet=quiet)
    _log(f"ورکر «{config.WORKER_ID}» شروع به کار کرد "
         f"(پایگاه‌داده: {config.DB_BACKEND} · حالت اجرا: {'یک‌دور' if once else 'دائمی'})",
         quiet=quiet)

    while True:
        job = storage.claim_job()
        if not job:
            idle_rounds += 1
            if once or (cap and processed >= cap):
                break
            stats = storage.queue_stats() if idle_rounds % 6 == 1 else {}
            if stats and not quiet:
                _log(f"… در انتظار کار (صف: {stats.get('pending', 0)} در انتظار، "
                     f"{stats.get('running', 0)} در حال اجرا)")
            if idle_rounds % 12 == 0:
                storage.requeue_stale_jobs(stale_seconds)
            time.sleep(poll)
            continue

        result = process_job(job, engine_mode=engine_mode, quiet=quiet, deliver=deliver)
        processed += 1
        if result.get("ok"):
            succeeded += 1
        else:
            failed += 1
        if cap and processed >= cap:
            break
        if once:
            break

    summary = {"worker": config.WORKER_ID, "processed": processed, "succeeded": succeeded,
               "failed": failed, "idle_rounds": idle_rounds, "reclaimed": reclaimed,
               "seconds": round(time.time() - started, 1), "db": config.DB_BACKEND,
               "queue": storage.queue_stats()}
    _log(f"پایان — {succeeded} موفق، {failed} ناموفق از {processed} کار "
         f"({summary['seconds']} ثانیه)", quiet=quiet)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="leadgen.worker",
        description="ورکر صف اجراها — کارها را برمی‌دارد و با موتور اجرا می‌کند")
    parser.add_argument("--once", action="store_true", help="فقط یک دور (مناسب cron/CI)")
    parser.add_argument("--max-jobs", type=int, default=0, help="سقف تعداد اجرا (۰ = نامحدود)")
    parser.add_argument("--interval", type=int, default=0, help="فاصلهٔ بررسی صف (ثانیه)")
    parser.add_argument("--engine", default="", help="docker | demo | auto (پیش‌فرض از .env)")
    parser.add_argument("--stale-seconds", type=int, default=1800,
                        help="کارهای بی‌سرپرست پس از این مدت به صف برمی‌گردند")
    parser.add_argument("--deliver", action="store_true",
                        help="پس از هر اجرای موفق، گزارش ایمیلی بفرست")
    parser.add_argument("--quiet", action="store_true", help="بدون چاپ لاگ")
    parser.add_argument("--status", action="store_true", help="فقط وضعیت صف و موتور را نشان بده")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.status:
        storage.init_db()
        status = {"worker": config.WORKER_ID, "db": config.DB_BACKEND,
                  "supabase_ready": config.supabase_ready(),
                  "queue": storage.queue_stats(), "engine": check_engine(args.engine or None,
                                                                         quiet=True),
                  "email": config.email_transport(), "digest_to": config.DIGEST_TO}
        import json
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0

    run_worker(once=args.once, max_jobs=args.max_jobs, interval=args.interval or None,
               engine_mode=args.engine or None, stale_seconds=args.stale_seconds,
               deliver=args.deliver, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
