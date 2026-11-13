"""دیدبان شهر — «هر هفته همین شهر/صنف را بگرد و فقط تازه‌ها را بفرست».

چرا این ماژول؟ خروجی یک‌بارهٔ اسکراب، برای تیم فروش کافی نیست؛ چیزی که واقعاً
کار می‌کند «جریان» است: یک منطقهٔ مشخص، یک صنف مشخص، هر N روز یک اجرای کامل، و
یک ایمیل کوتاه که فقط کسب‌وکارهای **تازه** (که در هیچ اجرای قبلی نبوده‌اند) را
نشان می‌دهد. تکراری‌ها هیچ‌وقت دوباره ایمیل نمی‌شوند.

اجزای کار:
  • ردیف دیدبان در جدول `watchlist` (SQLite محلی یا جدول Supabase) ذخیره می‌شود.
  • `tick()` دیدبان‌های «رسیده» را پیدا می‌کند و برایشان اجرای واقعی در صف
    می‌گذارد (یا در حالت محلی، همان‌جا اجرا می‌کند).
  • `deliver()` بعد از تمام‌شدن اسکراب، فقط تازه‌ها را ایمیل می‌کند.
  • کرون Supabase هر روز `GET /api/watchlist/tick` را صدا می‌زند؛ خودِ تیک
    تصمیم می‌گیرد کدام دیدبان امروز نوبتش است.
"""
from __future__ import annotations

import time

from . import analytics, config, delivery, jobs, storage

DEFAULT_INTERVAL_DAYS = 7
MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 60
MAX_WATCHES_PER_TICK = 5


# ── ساخت/ویرایش ───────────────────────────────────────────────────────────────
def _interval(value) -> int:
    try:
        days = int(value or DEFAULT_INTERVAL_DAYS)
    except (TypeError, ValueError):
        days = DEFAULT_INTERVAL_DAYS
    return max(MIN_INTERVAL_DAYS, min(MAX_INTERVAL_DAYS, days))


def label_for(item: dict) -> str:
    """برچسب خوانای یک دیدبان: «کافه · یزد»."""
    niche = (item or {}).get("niche_query") or "همهٔ کسب‌وکارها"
    area = (item or {}).get("city") or (item or {}).get("province") or "همه‌جا"
    return f"{niche} · {area}"


def create(data: dict) -> dict:
    """دیدبان تازه می‌سازد. ورودی ناقص → ValueError با پیام فارسی."""
    city = str((data or {}).get("city") or "").strip()
    province = str((data or {}).get("province") or "").strip()
    niche = str((data or {}).get("niche_query") or "").strip()
    all_niches = bool((data or {}).get("all_niches"))
    if not city and not province:
        raise ValueError("برای دیدبان، شهر یا استان لازم است.")
    if not niche and not all_niches:
        raise ValueError("برای دیدبان، صنف (یا «همهٔ کسب‌وکارها») لازم است.")
    if all_niches:
        niche = ""
    item = {
        "city": city,
        "province": province,
        "city_key": str((data or {}).get("city_key") or "").strip(),
        "niche_query": niche,
        "all_niches": all_niches,
        "interval_days": _interval((data or {}).get("interval_days")),
        "active": bool((data or {}).get("active", True)),
        "next_run_at": float((data or {}).get("next_run_at") or time.time()),
        "note": str((data or {}).get("note") or ""),
    }
    if not item["city_key"] and city:
        try:
            from . import regions
            item["city_key"] = regions.city_key(city) or ""
        except Exception:  # pragma: no cover - فهرست شهرها همیشه در دسترس است
            item["city_key"] = ""
    return storage.add_watch(item)


def update(watch_id: str, data: dict) -> dict | None:
    item = storage.get_watch(watch_id)
    if not item:
        return None
    fields: dict = {}
    for key in ("city", "province", "city_key", "niche_query", "note"):
        if key in (data or {}):
            fields[key] = str(data.get(key) or "").strip()
    if "all_niches" in (data or {}):
        fields["all_niches"] = bool(data["all_niches"])
        if fields["all_niches"]:
            fields["niche_query"] = ""
    if "interval_days" in (data or {}):
        fields["interval_days"] = _interval(data.get("interval_days"))
    if "active" in (data or {}):
        fields["active"] = bool(data["active"])
    if "next_run_at" in (data or {}):
        fields["next_run_at"] = float(data["next_run_at"] or time.time())
    storage.update_watch(watch_id, **fields)
    return storage.get_watch(watch_id)


def delete(watch_id: str) -> bool:
    return storage.delete_watch(watch_id)


# ── زمان‌بندی ─────────────────────────────────────────────────────────────────
def is_due(item: dict, now: float | None = None) -> bool:
    """آیا نوبت اجرای این دیدبان رسیده است؟ (غیرفعال‌ها هرگز)"""
    if not item or not item.get("active", True):
        return False
    now = time.time() if now is None else float(now)
    try:
        nxt = float(item.get("next_run_at") or 0)
    except (TypeError, ValueError):
        nxt = 0.0
    return nxt <= now


def next_run_after(item: dict, now: float | None = None) -> float:
    """زمان اجرای بعدی (حفظ ساعت؛ نه «۷ روز از الان دقیقاً» — ساده و قابل پیش‌بینی)."""
    now = time.time() if now is None else float(now)
    days = _interval((item or {}).get("interval_days"))
    return now + days * 86400.0


def due(now: float | None = None, limit: int = MAX_WATCHES_PER_TICK) -> list[dict]:
    now = time.time() if now is None else float(now)
    items = [w for w in storage.list_watch(active_only=True) if is_due(w, now)]
    items.sort(key=lambda w: float(w.get("next_run_at") or 0))
    return items[:max(1, int(limit))]


def build_request(item: dict) -> dict:
    """درخواست اسکراب معادل یک دیدبان (همان شکل POST /api/runs)."""
    province = str((item or {}).get("province") or "")
    city = str((item or {}).get("city") or "")
    request = {
        "niche_query": (item or {}).get("niche_query") or "",
        "all_niches": bool((item or {}).get("all_niches")),
        "city_query": city,
        "city_key": (item or {}).get("city_key") or "",
        "province": province,
        "area_level": "city" if city else "province",
        "lang": "fa",
        "scan_full": True,
        "watch_id": (item or {}).get("id") or "",
    }
    if province and not city:
        request["city_query"] = province          # مرکز استان از خودِ منطقه حل می‌شود
    return request


def run_now(item: dict, *, priority: int = 4) -> dict:
    """یک دیدبان را همین حالا در صف می‌گذارد و زمان اجرای بعدی را جلو می‌برد."""
    if not item:
        raise ValueError("دیدبان پیدا نشد.")
    request = build_request(item)
    queued = jobs.enqueue_run(request, priority=priority)
    now = time.time()
    storage.update_watch(item["id"], last_run_at=now, last_run_id=queued["run_id"],
                         next_run_at=next_run_after(item, now))
    return {"watch": item["id"], "run_id": queued["run_id"], "job_id": queued["job_id"],
            "label": label_for(item), "next_run_at": next_run_after(item, now)}


def tick(limit: int = MAX_WATCHES_PER_TICK, *, now: float | None = None,
         runner=None) -> dict:
    """دیدبان‌های رسیده را اجرا می‌کند.

    `runner` اختیاری: اگر داده شود (حالت محلی/بدون صف)، به‌جای صف‌گذاشتن همان‌جا
    با آن تابع اجرا می‌شود. خروجی برای نمایش در رابط/کرون است.
    """
    now = time.time() if now is None else float(now)
    items = due(now, limit=limit)
    queued: list[dict] = []
    for item in items:
        try:
            if runner is not None:
                request = build_request(item)
                result = runner(request)
                run_id = getattr(result, "run_id", "") or request.get("run_id") or ""
                storage.update_watch(item["id"], last_run_at=now, last_run_id=run_id,
                                     next_run_at=next_run_after(item, now))
                queued.append({"watch": item["id"], "run_id": run_id,
                               "label": label_for(item), "inline": True})
            else:
                queued.append(run_now(item))
        except Exception as exc:  # noqa: BLE001 — یک دیدبان خراب نباید بقیه را بخواباند
            queued.append({"watch": item.get("id"), "label": label_for(item),
                           "error": f"{type(exc).__name__}: {exc}"})
    return {"checked_at": now, "due": len(items), "queued": queued}


# ── «فقط تازه‌ها» + ایمیل ─────────────────────────────────────────────────────
def only_new_leads(run_id: str, limit: int = 800) -> list[dict]:
    """لیدهای یک اجرا که در هیچ اجرای دیگری دیده نشده‌اند (تازه‌های واقعی)."""
    if not run_id:
        return []
    rows = storage.load_leads(run_id, limit=max(1, int(limit)), sort="score", order="desc")
    place_ids = [str(r.get("place_id") or "") for r in rows if r.get("place_id")]
    seen = storage.seen_places(place_ids, exclude_run=run_id) if place_ids else {}
    fresh = []
    for row in rows:
        if str(row.get("place_id") or "") in seen:
            continue
        fresh.append(row)
    return fresh


def _recipients(item: dict) -> list[str]:
    target = str((item or {}).get("note") or "").strip()
    if "@" in target and " " not in target:
        return [target]
    return [x.strip() for x in config.digest_recipients() if x.strip()]


def _subject(item: dict | None, run_id: str, count: int | None = None) -> str:
    """موضوع ایمیل دیدبان — یک‌جا، تا ایمیل و صفحهٔ پیش‌نمایش یکی باشند."""
    niche = (item or {}).get("niche_query") or "همهٔ کسب‌وکارها"
    area = ((item or {}).get("city") or (item or {}).get("province") or "منطقه")
    head = "👁 دیدبان هفتگی" if count is None else f"👁 دیدبان هفتگی — {_fa(count)} تازه"
    return f"{head} | {niche} · {area}"


def _fa(value) -> str:
    """عدد فارسی (فقط برای متن‌های انسانی؛ منطق با ارقام لاتین کار می‌کند)."""
    digits = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    return str(value).translate(digits)


def _compose(item: dict | None, run_id: str, *, limit: int = 200) -> dict:
    """ایمیل «فقط تازه‌ها» را می‌سازد — بدون ارسال و بدون ثبت.

    جدا کردن «ساختن» از «فرستادن» یک فایدهٔ عملی دارد: وقتی مسیر ایمیل تنظیم
    نشده است (روی Vercel بدون GMAIL_REFRESH_TOKEN)، همان ایمیل با
    `GET /api/watchlist/<id>/new?format=html` در مرورگر باز و خوانده می‌شود؛
    پس «فقط تازه‌ها» بی‌ایمیل هم قابل استفاده است و کاربر دست‌خالی نمی‌ماند.
    """
    fresh = only_new_leads(run_id, limit=max(int(limit), 1) * 4)
    top = fresh[:max(1, int(limit))]
    payload = delivery.collect(run_id, segment=None, limit=max(1, min(len(fresh), int(limit))))
    payload["leads"] = top
    payload["shown"] = len(top)
    payload["scope_total"] = len(fresh)
    payload["analysis"] = analytics.build(top, payload.get("run") or {})
    payload["watch"] = item or {}
    # عنوان ایمیل/صفحه هم همان «دیدبان …» باشد، نه «گزارش لیدهای داغ».
    payload["subject"] = _subject(item, run_id)
    area = (
        (item or {}).get("city")
        or ((payload.get("run") or {}).get("area_label"))
        or ((payload.get("run") or {}).get("city"))
        or "منطقه"
    )
    niche = (item or {}).get("niche_query") or "همهٔ کسب‌وکارها"
    return {
        "fresh": fresh, "top": top, "payload": payload, "area": area, "niche": niche,
        "subject": f"👁 دیدبان هفتگی — {_fa(len(fresh))} تازه | {niche} · {area}",
        "text": delivery.render_text(payload),
        "html": delivery.render_html(payload),
        "recipients": _recipients(item or {}),
        "attachment": delivery.build_attachment(payload, with_xlsx=bool(top)),
    }


def email_status() -> dict:
    """وضعیت مسیر ایمیل: «آماده» یا «فقط پیش‌نمایش در برنامه»."""
    transport = str(config.email_transport() or "none").lower()
    recipients = [x for x in config.digest_recipients() if x]
    ready = transport != "none"
    return {
        "transport": transport,
        "ready": ready,
        "recipients": recipients,
        "hint": "" if ready else (
            "ارسال ایمیل تنظیم نشده (GMAIL_REFRESH_TOKEN یا GMAIL_APP_PASSWORD). "
            "تا آن موقع، ایمیل «فقط تازه‌ها» در برنامه ساخته و قابل مشاهده/دانلود است."),
    }


def deliver(run_id: str, watch_id: str, *, limit: int = 200,
            dry_run: bool | None = None) -> dict:
    """فقط تازه‌های یک اجرای دیدبانی را ایمیل می‌کند (بدون تکرار)."""
    item = storage.get_watch(watch_id) if watch_id else None
    composed = _compose(item, run_id, limit=limit)
    fresh, top = composed["fresh"], composed["top"]
    subject = composed["subject"]
    if item:
        storage.update_watch(watch_id, last_new=len(fresh))
    if not fresh:
        result = {"status": "empty", "kind": "watch", "run_id": run_id,
                  "watch": watch_id, "shown": 0, "scope_total": 0,
                  "reason": "no_new_leads", "email": email_status()}
        storage.log_delivery(kind="watch", channel="email", target="", status="skipped",
                             subject="", detail={**result})
        return result

    attachment = composed["attachment"]
    recipients = composed["recipients"]
    if not recipients:
        result = {"status": "skipped", "kind": "watch", "reason": "no_recipient",
                  "run_id": run_id, "watch": watch_id, "subject": subject,
                  "shown": len(top), "scope_total": len(fresh), "email": email_status()}
        storage.log_delivery(kind="watch", channel="email", target="", status="skipped",
                             subject=subject, detail={**result})
        return result
    result = delivery.send_email(
        recipients, subject, composed["html"], composed["text"],
        [attachment] if attachment else None, dry_run=dry_run)
    result.update({"kind": "watch", "run_id": run_id, "watch": watch_id,
                   "subject": subject, "shown": len(top), "scope_total": len(fresh),
                   "email": email_status()})
    storage.log_delivery(kind="watch", channel="email", target=result.get("target", ""),
                         subject=subject, status=result.get("status", "sent"),
                         detail={"shown": len(top), "scope_total": len(fresh),
                                 "transport": result.get("transport"),
                                 "path": result.get("path"), "run_id": run_id,
                                 "watch": watch_id})
    return result


def preview(watch_id: str, *, limit: int = 200) -> dict:
    """ایمیل «فقط تازه‌های آخرین اجرا» را برای نمایش/دانلود می‌سازد (بدون ارسال)."""
    item = storage.get_watch(watch_id)
    if not item:
        raise ValueError("دیدبان پیدا نشد.")
    run_id = str(item.get("last_run_id") or "")
    composed = _compose(item, run_id, limit=limit)
    status = email_status()
    text = (f"{composed['subject']}\n\n{composed['text']}"
            if composed["fresh"] else "")
    return {
        "watch": item,
        "run_id": run_id,
        "count": len(composed["fresh"]),
        "subject": composed["subject"] if composed["fresh"] else "",
        "html": composed["html"] if composed["fresh"] else "",
        "text": text,
        "recipients": composed["recipients"],
        "email": status,
        "reason": "" if composed["fresh"] else (
            "هیچ اجرایی برای این دیدبان ثبت نشده." if not run_id
            else "آخرین اجرا هیچ لید تازه‌ای نداشت."),
    }


def deliver_last(watch_id: str, *, limit: int = 200,
                 dry_run: bool | None = None) -> dict:
    """ارسال (یا ساخت) ایمیل «فقط تازه‌های آخرین اجرا» — دکمهٔ «✉️ ایمیل تازه‌ها»."""
    item = storage.get_watch(watch_id)
    if not item:
        raise ValueError("دیدبان پیدا نشد.")
    run_id = str(item.get("last_run_id") or "")
    if not run_id:
        raise ValueError("این دیدبان هنوز اجرایی ندارد؛ اول «همین حالا بگرد» را بزن.")
    return deliver(run_id, watch_id, limit=limit, dry_run=dry_run)


def maybe_deliver(run_id: str, request: dict, analytics_pack: dict | None = None,
                  *, dry_run: bool | None = None) -> dict | None:
    """اگر اجرا از یک دیدبان آمده باشد، در پایانش ایمیل «فقط تازه‌ها» می‌فرستد.

    تا وقتی بخش‌های اسکراب کامل باقی مانده‌اند، ارسال عقب می‌افتد؛ وگرنه کاربر
    چند ایمیل ناقص برای یک اسکراب می‌گیرد.
    """
    watch_id = str((request or {}).get("watch_id") or "")
    if not watch_id:
        return None
    scan = ((analytics_pack or {}).get("scan") or {}) if isinstance(analytics_pack, dict) else {}
    if scan.get("remaining"):
        return {"status": "deferred", "reason": "scan_incomplete", "watch": watch_id,
                "remaining": len(scan.get("remaining") or [])}
    return deliver(run_id, watch_id, dry_run=dry_run)


# ── گزارش وضعیت برای رابط ─────────────────────────────────────────────────────
def overview(now: float | None = None) -> dict:
    now = time.time() if now is None else float(now)
    items = storage.list_watch(limit=200)
    for item in items:
        item["label"] = label_for(item)
        item["due"] = is_due(item, now)
        nxt = item.get("next_run_at")
        item["days_left"] = (round(max(0.0, float(nxt) - now) / 86400.0, 1)
                             if nxt else None)
    last = storage.last_delivery("watch") or {}
    return {
        "items": items,
        "total": len(items),
        "active": sum(1 for w in items if w.get("active")),
        "due_now": sum(1 for w in items if w.get("due")),
        "interval_default": DEFAULT_INTERVAL_DAYS,
        "last_delivery": last,
        "next_due_at": min([float(w["next_run_at"]) for w in items
                            if w.get("active") and w.get("next_run_at")], default=None),
        # آیا ایمیل واقعاً ارسال می‌شود یا فقط ساخته می‌شود؟ رابط با همین تصمیم
        # می‌گیرد که «✉️ ایمیل تازه‌ها» بفرستد یا «👁 پیش‌نمایش» باز کند.
        "email": email_status(),
    }
