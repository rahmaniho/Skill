"""تحویل خروجی — ایمیل.

دو محصول:
  1) **گزارش (digest)**: یک ایمیل فارسی RTL با KPIها، بینش‌ها، جدول لیدهای داغ
     (تلفن/ایمیل/سایت/شبکه‌ها/لوکیشن دقیق + لینک نقشه) و فایل اکسل پیوست.
  2) **پیش‌نویس تماس**: برای هر لید، متن آمادهٔ `campaign` در Gmail به‌صورت Draft
     ساخته می‌شود تا کاربر فقط بازبینی و ارسال کند (بدون ارسال انبوه خودکار).

دو مسیر ارسال (هر دو فقط کتابخانهٔ استاندارد):
  • `gmail_api` — با refresh token روی HTTPS؛ روی Vercel/ابر تنها راه ممکن است.
  • `smtp`      — با رمز برنامهٔ Gmail؛ روی ماشین خودتان/Codespace ساده‌ترین راه.
  • اگر هیچ‌کدام پیکربندی نشده باشد → حالت خشک: فایل `.eml` در `data/exports`
    نوشته می‌شود تا محتوا قابل بازبینی باشد (و تست‌ها بدون شبکه اجرا شوند).
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import uuid
import urllib.parse
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from . import analytics, campaign, config, exports, storage

TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/{user}/messages/{endpoint}"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class DeliveryError(RuntimeError):
    """خطای ارسال ایمیل — با جزئیات برای تشخیص سریع (توکن/رمز/گیرنده)."""


# ── ساخت محتوا ────────────────────────────────────────────────────────────────
def _insight_text(insight) -> str:
    """بینش‌های `analytics` dict هستند ({kind, fa}) — متن فارسی‌شان را برمی‌گردانیم."""
    if isinstance(insight, dict):
        return str(insight.get("fa") or insight.get("text") or insight.get("kind") or "")
    return str(insight or "")


def _gap_count(analysis: dict, key: str) -> int:
    """شمارش یک شکاف مشخص از `gaps_breakdown` (که فهرستی از dictها است)."""
    for item in (analysis or {}).get("gaps") or []:
        if isinstance(item, dict) and item.get("key") == key:
            try:
                return int(item.get("count") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _fa_number(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def collect(run_id: str | None = None, *, segment: str | None = "hot",
            min_score: int | None = None, limit: int | None = None,
            since: float | None = None) -> dict:
    """دادهٔ گزارش را از پایگاه‌داده (محلی یا ابری) جمع می‌کند."""
    limit = int(limit or config.DIGEST_MAX_LEADS)
    filters: dict = {"segment": segment} if segment else {}
    if min_score is not None:
        filters["min_score"] = int(min_score)
    if run_id:
        filters["run_id"] = run_id
    result = storage.search_leads(limit=max(limit * 4, limit), sort="score", order="desc",
                                  **filters)
    leads = result.get("leads") or []
    if since:
        leads = [l for l in leads if (l.get("created_at") or 0) >= float(since)]
    total_scope = result.get("total", len(leads))
    top = leads[:limit]

    run = storage.get_run(run_id) if run_id else None
    scope_rows = leads
    analysis = analytics.build(scope_rows, run or {}) if scope_rows else {}
    stats = storage.dashboard_stats() if hasattr(storage, "dashboard_stats") else {}
    facets = result.get("facets") or {}
    return {
        "run": run or {},
        "leads": top,
        "scope_total": total_scope,
        "shown": len(top),
        "facets": facets,
        "stats": stats,
        "analysis": analysis,
        "generated_at": time.time(),
        "app_url": config.app_base_url(),
    }


def subject_for(payload: dict, override: str = "") -> str:
    if override:
        return override
    if payload.get("subject"):
        return str(payload["subject"])[:150]
    run = payload.get("run") or {}
    area = run.get("area_label") or run.get("city") or ""
    niche = run.get("niche_fa") or ""
    hot = (payload.get("facets") or {}).get("segment", {}).get("hot") or len(payload["leads"])
    parts = ["گزارش لیدهای داغ"]
    if hot:
        parts.append(f"{_fa_number(hot)} لید")
    if niche:
        parts.append(niche)
    if area:
        parts.append(area)
    subject = " — ".join(parts[:1]) + (" | " + " · ".join(parts[1:]) if len(parts) > 1 else "")
    return subject[:150]


def render_text(payload: dict) -> str:
    lines = [subject_for(payload), ""]
    stats = payload.get("stats") or {}
    if stats:
        lines.append(f"پایگاه‌داده: {_fa_number(stats.get('leads'))} لید در "
                     f"{_fa_number(stats.get('runs'))} اجرا · "
                     f"داغ: {_fa_number(stats.get('hot'))} · "
                     f"ایمیل‌دار: {_fa_number(stats.get('with_email'))}")
        lines.append("")
    for insight in (payload.get("analysis") or {}).get("insights", [])[:5]:
        text = _insight_text(insight)
        if text:
            lines.append(f"◈ {text}")
    if (payload.get("analysis") or {}).get("insights"):
        lines.append("")
    lines.append(f"لیدهای پیشنهادی برای تماس امروز ({_fa_number(payload['shown'])} مورد):")
    lines.append("")
    for index, lead in enumerate(payload["leads"], 1):
        lines.append(f"{index}. {lead.get('title') or '—'}  [{lead.get('segment_fa') or ''}] "
                     f"امتیاز {lead.get('score')}")
        lines.append(f"   صنف: {lead.get('niche_fa') or lead.get('category') or '—'} · "
                     f"منطقه: {lead.get('run_area') or lead.get('city') or '—'}")
        contact = []
        if lead.get("phone"):
            contact.append(f"تلفن {lead['phone']}")
        if lead.get("email_primary"):
            contact.append(f"ایمیل {lead['email_primary']}")
        if lead.get("website"):
            contact.append(f"سایت {lead['website']}")
        socials = [k for k in ("instagram", "facebook", "linkedin") if lead.get(k)]
        if socials:
            contact.append("شبکه: " + "، ".join(socials))
        if contact:
            lines.append("   " + " · ".join(contact))
        if lead.get("lat") or lead.get("lon"):
            lines.append(f"   لوکیشن دقیق: {lead.get('lat')}, {lead.get('lon')}")
        gaps = lead.get("gaps_fa") or []
        if gaps:
            lines.append(f"   شکاف‌ها: {'، '.join(gaps[:3])}")
        if lead.get("pitch"):
            lines.append(f"   زاویهٔ فروش: {lead['pitch']}")
        lines.append("")
    if payload.get("app_url"):
        lines.append(f"ادامهٔ کار در اپ: {payload['app_url']}")
    lines.append("")
    lines.append("— ساخته‌شده با LeadGen (موتور: gosom/google-maps-scraper) · "
                 "داده‌ها را پیش از تماس راستی‌آزمایی کنید.")
    return "\n".join(lines)


def render_html(payload: dict) -> str:
    leads = payload["leads"]
    stats = payload.get("stats") or {}
    analysis = payload.get("analysis") or {}
    coverage = (analysis.get("coverage") or {})
    insights = analysis.get("insights") or []
    no_website = _gap_count(analysis, "no_website") or max(
        0, int(coverage.get("total") or 0) - int(coverage.get("website") or 0))
    area = (payload.get("run") or {}).get("area_label") or (payload.get("run") or {}).get("city")

    def cell(value: str, link: str = "", rtl: bool = True) -> str:
        if not value:
            return '<td style="padding:6px 8px;color:#9aa0a6;font-size:12px">—</td>'
        align = "right" if rtl else "left"
        if link:
            return (f'<td style="padding:6px 8px;font-size:12px;text-align:{align}">'
                    f'<a href="{link}" style="color:#1a73e8;text-decoration:none">{value}</a></td>')
        return f'<td style="padding:6px 8px;font-size:12px;text-align:{align}">{value}</td>'

    rows_html = []
    for index, lead in enumerate(leads, 1):
        score = lead.get("score") or 0
        color = "#d93025" if score >= 80 else "#f29900" if score >= 60 else "#5f6368"
        socials = " ".join(
            f'<a href="{lead[k]}" style="color:#1a73e8;text-decoration:none;font-size:11px">{label}</a>'
            for k, label in (("instagram", "IG"), ("facebook", "FB"), ("linkedin", "IN"))
            if lead.get(k))
        maps = ""
        if lead.get("maps_link"):
            maps = lead["maps_link"]
        elif lead.get("lat") and lead.get("lon"):
            maps = f"https://www.google.com/maps/search/?api=1&query={lead['lat']},{lead['lon']}"
        rows_html.append(
            "<tr style=\"border-bottom:1px solid #eceff1\">"
            + cell(f"{index}", rtl=False)
            + f'<td style="padding:6px 8px;font-size:12px"><b>{lead.get("title") or "—"}</b>'
              f'<div style="color:#5f6368;font-size:11px">{lead.get("niche_fa") or lead.get("category") or ""}'
              f' · {lead.get("run_area") or lead.get("city") or ""}</div></td>'
            + f'<td style="padding:6px 8px;text-align:center"><span style="background:{color};color:#fff;'
              f'border-radius:10px;padding:2px 8px;font-size:11px">{score}</span></td>'
            + cell(lead.get("phone") or "", f"tel:{lead.get('phone')}" if lead.get("phone") else "")
            + cell(lead.get("email_primary") or "",
                   f"mailto:{lead.get('email_primary')}" if lead.get("email_primary") else "")
            + cell((lead.get("domain") or lead.get("website") or "")[:34], lead.get("website") or "")
            + f'<td style="padding:6px 8px;font-size:11px">{socials or "—"}</td>'
            + cell(f"{lead.get('rating') or '—'} ★ / {_fa_number(lead.get('reviews'))}", rtl=False)
            + cell("📍 نقشه", maps)
            + "</tr>")

    kpis = [
        ("لید در این گزارش", _fa_number(payload["shown"])),
        ("کل لیدهای پایگاه‌داده", _fa_number(stats.get("leads"))),
        ("لید داغ", _fa_number(stats.get("hot"))),
        ("ایمیل‌دار", _fa_number(coverage.get("email") or stats.get("with_email"))),
        ("تلفن‌دار", _fa_number(coverage.get("phone") or stats.get("with_phone"))),
        ("بدون وب‌سایت", _fa_number(no_website)),
        ("لوکیشن دقیق", _fa_number(coverage.get("location") or len(leads))),
    ]
    kpi_html = "".join(
        f'<td style="padding:10px;text-align:center;border-left:1px solid #eceff1">'
        f'<div style="font-size:18px;font-weight:700;color:#202124">{value}</div>'
        f'<div style="font-size:11px;color:#5f6368">{label}</div></td>'
        for label, value in kpis)

    insight_html = "".join(
        f'<li style="margin:4px 0;font-size:13px;color:#3c4043">{_insight_text(item)}</li>'
        for item in insights[:6] if _insight_text(item))

    app_link = ""
    if payload.get("app_url"):
        app_link = (f'<p style="font-size:12px;color:#5f6368">ادامهٔ کار، فیلترها و خروجی اکسل: '
                    f'<a href="{payload["app_url"]}" style="color:#1a73e8">{payload["app_url"]}</a></p>')

    return f"""<!doctype html>
<html dir="rtl" lang="fa"><head><meta charset="utf-8">
<title>{subject_for(payload)}</title></head>
<body style="margin:0;padding:0;background:#f5f6f8;font-family:Tahoma,'Segoe UI',sans-serif">
<div style="max-width:900px;margin:0 auto;padding:16px">
  <div style="background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e3e6ea">
    <div style="background:#0b57d0;color:#fff;padding:16px 20px">
      <div style="font-size:17px;font-weight:700">{subject_for(payload)}</div>
      <div style="font-size:12px;opacity:.9">{'محدوده: ' + area if area else 'همهٔ اجراها'}
        · زمان گزارش: {time.strftime('%Y/%m/%d %H:%M', time.localtime(payload['generated_at']))}</div>
    </div>
    <table style="width:100%;border-collapse:collapse;border-bottom:1px solid #eceff1">
      <tr>{kpi_html}</tr>
    </table>
    <div style="padding:14px 20px">
      <div style="font-size:14px;font-weight:700;color:#202124;margin-bottom:6px">🧠 بینش‌ها</div>
      <ul style="margin:0;padding-right:18px">{insight_html or '<li style="font-size:13px">داده‌ای برای بینش نیست.</li>'}</ul>
    </div>
    <div style="padding:0 8px 8px">
      <table style="width:100%;border-collapse:collapse">
        <thead><tr style="background:#f1f3f4;color:#3c4043;font-size:11px">
          <th style="padding:6px 8px">#</th><th style="padding:6px 8px;text-align:right">کسب‌وکار</th>
          <th style="padding:6px 8px">امتیاز</th><th style="padding:6px 8px;text-align:right">تلفن</th>
          <th style="padding:6px 8px;text-align:right">ایمیل</th>
          <th style="padding:6px 8px;text-align:right">وب‌سایت</th>
          <th style="padding:6px 8px;text-align:right">شبکه‌ها</th>
          <th style="padding:6px 8px;text-align:right">★ / نظر</th>
          <th style="padding:6px 8px;text-align:right">لوکیشن</th>
        </tr></thead>
        <tbody>{''.join(rows_html) or '<tr><td style="padding:14px;font-size:13px">لیدی پیدا نشد.</td></tr>'}</tbody>
      </table>
    </div>
    <div style="padding:12px 20px;border-top:1px solid #eceff1">
      {app_link}
      <p style="font-size:11px;color:#9aa0a6">این فهرست از داده‌های عمومی نقشه جمع‌شده است؛
      پیش از تماس، اطلاعات را راستی‌آزمایی کنید و قوانین تبلیغات (GDPR/CAN-SPAM) را رعایت کنید.</p>
    </div>
  </div>
</div></body></html>"""


def build_attachment(payload: dict, *, with_xlsx: bool = True) -> dict | None:
    """فایل اکسل چندبرگی برای پیوست ایمیل (همان نویسندهٔ XLSX برنامه)."""
    if not with_xlsx or not payload["leads"]:
        return None
    rows = payload["leads"]
    run = payload.get("run") or None
    sheets = exports.build_workbook(rows, payload.get("analysis") or {}, run, preset="lead")
    data = exports.xlsx_bytes(sheets)
    stamp = time.strftime("%Y%m%d-%H%M")
    area = (run or {}).get("area_label") or (run or {}).get("city") or "leads"
    name = exports.safe_filename(f"leadgen-{area}-{stamp}") + ".xlsx"
    return {"name": name, "bytes": data, "mime": XLSX_MIME}


# ── MIME و ارسال ──────────────────────────────────────────────────────────────
def build_mime(to: list[str], subject: str, text: str, html: str,
               attachments: list[dict] | None = None, sender: str | None = None) -> EmailMessage:
    from_address = sender or config.digest_from() or "leadgen@localhost"
    message = EmailMessage()
    message["From"] = formataddr(("LeadGen", from_address)) if "@" in from_address else from_address
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=(from_address.split("@")[-1] if "@" in from_address
                                              else "localhost"))
    message.set_content(text or "این ایمیل نسخهٔ متنی ندارد.")
    if html:                     # نسخهٔ HTML خالی، بخش بی‌فایدهٔ multipart می‌سازد
        message.add_alternative(html, subtype="html")
    for item in attachments or []:
        data = item.get("bytes") or b""
        maintype, _, subtype = (item.get("mime") or "application/octet-stream").partition("/")
        message.add_attachment(data, maintype=maintype or "application",
                               subtype=subtype or "octet-stream",
                               filename=item.get("name") or "attachment.bin")
    return message


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 45) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json",
                                              **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise DeliveryError(f"خطای HTTP {exc.code} از {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise DeliveryError(f"اتصال به {url} برقرار نشد: {getattr(exc, 'reason', exc)}") from exc


def _post_form(url: str, fields: dict, timeout: int = 30) -> dict:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise DeliveryError(f"توکن Gmail گرفته نشد (HTTP {exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise DeliveryError(f"اتصال به سرویس توکن گوگل برقرار نشد: {getattr(exc, 'reason', exc)}") from exc


def gmail_access_token() -> str:
    creds = config.gmail_credentials()
    if not (creds["refresh_token"] and creds["client_id"] and creds["client_secret"]):
        raise DeliveryError("برای مسیر Gmail API به GMAIL_REFRESH_TOKEN و GMAIL_CLIENT_ID و "
                            "GMAIL_CLIENT_SECRET نیاز است")
    data = _post_form(TOKEN_URL, {"client_id": creds["client_id"],
                                  "client_secret": creds["client_secret"],
                                  "refresh_token": creds["refresh_token"],
                                  "grant_type": "refresh_token"})
    token = data.get("access_token")
    if not token:
        raise DeliveryError(f"پاسخ سرویس توکن، access_token نداشت: {data}")
    return token


def _gmail_call(endpoint: str, payload: dict, *, token: str | None = None,
                user: str = "me") -> dict:
    token = token or gmail_access_token()
    url = GMAIL_API.format(user=user, endpoint=endpoint)
    return _post_json(url, payload, headers={"Authorization": f"Bearer {token}"})


def _send_smtp(message: EmailMessage) -> dict:
    import smtplib
    creds = config.smtp_credentials()
    user, password = creds["user"], creds["password"]
    if not (user and password):
        raise DeliveryError("برای SMTP به SMTP_USER/SMTP_PASSWORD (یا GMAIL_APP_PASSWORD) نیاز است")
    with smtplib.SMTP(creds["host"], creds["port"], timeout=45) as smtp:
        smtp.ehlo()
        try:
            smtp.starttls()
            smtp.ehlo()
        except smtplib.SMTPNotSupportedError:  # pragma: no cover
            pass
        smtp.login(user, password)
        smtp.send_message(message)
    return {"transport": "smtp", "host": config.SMTP_HOST}


def _write_eml(message: EmailMessage, note: str = "", slug: str = "") -> Path:
    """ایمیل را به‌صورت `.eml` روی دیسک می‌گذارد (حالت خشک / پیش‌نویس محلی).

    نام فایل شامل برچسب و یک پسوند یکتا است تا چند پیش‌نویس پشت‌سرهم روی هم
    نوشته نشوند (پیش‌تر همه در یک ثانیه یک نام می‌گرفتند).
    """
    config.ensure_dirs()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    parts = ["mail", stamp]
    if note and note not in ("dry-run",):
        parts.append(str(note))
    if slug:
        parts.append(exports.safe_filename(slug)[:40])
    parts.append(uuid.uuid4().hex[:6])
    path = config.EXPORT_DIR / ("-".join(part for part in parts if part) + ".eml")
    path.write_bytes(bytes(message))
    return path


def send_email(to: list[str] | str, subject: str, html: str, text: str = "",
               attachments: list[dict] | None = None, *, transport: str | None = None,
               sender: str | None = None, dry_run: bool | None = None) -> dict:
    """یک ایمیل را می‌فرستد (یا در حالت خشک، فایل `.eml` می‌سازد)."""
    recipients = [x.strip() for x in (to if isinstance(to, (list, tuple)) else [to]) if x.strip()]
    if not recipients:
        raise DeliveryError("گیرنده‌ای مشخص نشده است (DIGEST_TO را تنظیم کنید)")
    transport = (transport or config.email_transport()).lower()
    if dry_run is None:
        dry_run = config.env("LEADGEN_DELIVERY_DRY") == "1" or transport == "none"
    message = build_mime(recipients, subject, text, html, attachments, sender)

    if dry_run:
        path = _write_eml(message, "dry-run")
        return {"status": "dry_run", "transport": transport, "target": ", ".join(recipients),
                "subject": subject, "path": str(path),
                "bytes": len(bytes(message)), "attachments": len(attachments or [])}

    if transport == "gmail_api":
        raw = _b64url(bytes(message))
        result = _gmail_call("send", {"raw": raw})
        return {"status": "sent", "transport": "gmail_api", "target": ", ".join(recipients),
                "subject": subject, "id": result.get("id"), "thread": result.get("threadId")}
    if transport == "smtp":
        info = _send_smtp(message)
        return {"status": "sent", "transport": "smtp", "target": ", ".join(recipients),
                "subject": subject, **info}
    raise DeliveryError(f"مسیر ارسال ناشناخته: {transport!r} (gmail_api | smtp | none)")


# ── محصول ۱: گزارش ────────────────────────────────────────────────────────────
def send_digest(run_id: str | None = None, *, to: list[str] | None = None,
                segment: str | None = "hot", min_score: int | None = None,
                limit: int | None = None, subject: str = "", dry_run: bool | None = None,
                with_xlsx: bool = True, transport: str | None = None) -> dict:
    payload = collect(run_id, segment=segment, min_score=min_score, limit=limit)
    recipients = [x.strip() for x in (to or config.digest_recipients()) if x.strip()]
    final_subject = subject or config.env("DIGEST_SUBJECT_OVERRIDE") or subject_for(payload)
    attachment = build_attachment(payload, with_xlsx=with_xlsx)
    text = render_text(payload)
    html = render_html(payload)

    if not recipients:
        result = {"status": "skipped", "reason": "no_recipient", "target": "",
                  "subject": final_subject, "shown": payload["shown"],
                  "scope_total": payload["scope_total"]}
        _log_delivery(result, payload)
        return result

    result = send_email(recipients, final_subject, html, text,
                        [attachment] if attachment else None,
                        transport=transport, dry_run=dry_run)
    result.update({"shown": payload["shown"], "scope_total": payload["scope_total"],
                   "segment": segment, "attachment": (attachment or {}).get("name")})
    _log_delivery(result, payload)
    return result


def _log_delivery(result: dict, payload: dict | None = None) -> None:
    try:
        storage.log_delivery(
            kind=result.get("kind") or "digest", channel="email",
            target=result.get("target", ""), subject=result.get("subject", ""),
            status=result.get("status", "sent"),
            detail={"shown": (payload or {}).get("shown", result.get("shown")),
                    "scope_total": (payload or {}).get("scope_total", result.get("scope_total")),
                    "transport": result.get("transport"), "path": result.get("path"),
                    "attachment": result.get("attachment"), "reason": result.get("reason")})
    except Exception:  # pragma: no cover - ثبت گزارش نباید ارسال را بشکند
        pass


# ── محصول ۲: پیش‌نویس تماس برای هر لید ────────────────────────────────────────
def build_lead_drafts(limit: int = 10, *, channel: str = "email", min_score: int | None = None,
                      run_id: str | None = None, sender: dict | None = None,
                      only_missing: bool = True) -> list[dict]:
    """برای لیدهای داغ، متن آمادهٔ تماس می‌سازد (Gmail Draft یا فایل `.eml`)."""
    person = campaign.Sender.from_dict(sender)
    result = storage.search_leads(segment="hot" if only_missing else None, run_id=run_id,
                                  min_score=min_score, has_email=True if channel == "email" else None,
                                  limit=max(int(limit), 1), sort="score", order="desc")
    drafts = []
    for lead in result.get("leads", [])[:int(limit)]:
        message = campaign.generate(lead, channel=channel, sender=person)
        # عنوان پیش‌نویس = همان نامی که در متن ایمیل خطاب می‌شود (نه عنوان خام لید)؛
        # عنوان کامل هم جدا نگه داشته می‌شود تا در رابط دیده شود.
        business = str((message.get("context") or {}).get("business") or lead.get("title") or "")
        drafts.append({"lead_id": lead.get("id"), "title": business,
                       "lead_title": lead.get("title"),
                       "to": message.get("to"), "subject": message.get("subject"),
                       "text": message.get("body") or message.get("text"),
                       "channel": channel, "score": lead.get("score")})
    return drafts


def create_drafts(limit: int = 10, *, channel: str = "email", min_score: int | None = None,
                  run_id: str | None = None, sender: dict | None = None,
                  dry_run: bool | None = None, transport: str | None = None) -> dict:
    drafts = build_lead_drafts(limit, channel=channel, min_score=min_score, run_id=run_id,
                               sender=sender)
    transport = (transport or config.email_transport()).lower()
    if dry_run is None:
        dry_run = config.env("LEADGEN_DELIVERY_DRY") == "1" or transport != "gmail_api"
    created = []
    for item in drafts:
        if not item.get("to"):
            continue
        message = build_mime([item["to"]], item["subject"] or "تماس", item["text"] or "", "")
        if dry_run:
            path = _write_eml(message, "draft", item.get("title") or "")
            created.append({"status": "dry_run", "to": item["to"], "path": str(path),
                            "title": item["title"]})
            continue
        result = _gmail_call("drafts", {"message": {"raw": _b64url(bytes(message))}})
        created.append({"status": "draft", "to": item["to"], "id": result.get("id"),
                        "title": item["title"]})
    summary = {"status": "dry_run" if dry_run else "created", "transport": transport,
               "count": len(created), "items": created}
    try:
        storage.log_delivery(kind="draft", channel=channel, target=f"{len(created)} پیش‌نویس",
                             subject="پیش‌نویس تماس لیدها",
                             status="sent" if not dry_run else "dry_run",
                             detail={"count": len(created), "transport": transport})
    except Exception:  # pragma: no cover
        pass
    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leadgen.delivery",
                                     description="گزارش ایمیلی لیدها و پیش‌نویس تماس")
    parser.add_argument("--digest", action="store_true", help="ساخت و ارسال گزارش لیدهای داغ")
    parser.add_argument("--drafts", action="store_true", help="ساخت پیش‌نویس تماس برای لیدها")
    parser.add_argument("--to", default="", help="گیرنده‌ها (با کاما جدا کنید)")
    parser.add_argument("--run-id", default="", help="محدود به یک اجرا")
    parser.add_argument("--segment", default="hot", help="hot|warm|cold|'' (همه)")
    parser.add_argument("--min-score", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0, help="تعداد لید در گزارش/پیش‌نویس‌ها")
    parser.add_argument("--subject", default="", help="موضوع دلخواه")
    parser.add_argument("--no-xlsx", action="store_true", help="بدون پیوست اکسل")
    parser.add_argument("--transport", default="", help="gmail_api|smtp|none")
    parser.add_argument("--dry-run", action="store_true",
                        help="فقط فایل .eml بساز (بدون ارسال) — برای بازبینی و تست")
    parser.add_argument("--preview", action="store_true", help="چاپ متن گزارش در ترمینال")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    storage.init_db()
    recipients = ([x.strip() for x in args.to.split(",") if x.strip()]
                  or config.digest_recipients())

    if args.preview or not (args.digest or args.drafts):
        payload = collect(args.run_id or None, segment=args.segment or None,
                          min_score=args.min_score or None, limit=args.limit or None)
        print(render_text(payload))
        if args.preview:
            config.ensure_dirs()
            path = config.EXPORT_DIR / "digest-preview.html"
            path.write_text(render_html(payload), encoding="utf-8")
            print(f"\n✓ نسخهٔ HTML در {path} نوشته شد "
                  f"({payload['shown']} لید از {payload['scope_total']} مورد)")
        return 0

    if args.digest:
        result = send_digest(args.run_id or None, to=recipients, segment=args.segment or None,
                             min_score=args.min_score or None, limit=args.limit or None,
                             subject=args.subject, with_xlsx=not args.no_xlsx,
                             dry_run=True if args.dry_run else None,
                             transport=args.transport or None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in ("sent", "dry_run", "skipped") else 1

    if args.drafts:
        result = create_drafts(args.limit or 10, min_score=args.min_score or None,
                               run_id=args.run_id or None, dry_run=True if args.dry_run else None,
                               transport=args.transport or None)
        print(json.dumps({"count": result["count"], "status": result["status"],
                          "items": result["items"][:5]}, ensure_ascii=False, indent=2))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
