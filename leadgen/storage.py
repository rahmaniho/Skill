"""ذخیره‌سازی: پایگاه‌داده SQLite برای «اجراها» و «لیدها» + وضعیت پیگیری (مینی‌CRM).

چرا SQLite؟ محلی، بدون سرویس، بدون کلید API و با کتابخانه استاندارد پایتون —
همان فلسفه کیت اصلی («همه‌چیز روی دستگاه خودتان»). داده‌های شخصی (ایمیل/تلفن)
هرگز commit نمی‌شوند: پوشه data/ در .gitignore است.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import config

_LOCK = threading.RLock()

LEAD_STATUSES = ("new", "contacted", "replied", "qualified", "won", "lost", "unsubscribed")
LEAD_STATUS_FA = {
    "new": "جدید",
    "contacted": "تماس گرفته شد",
    "replied": "پاسخ داد",
    "qualified": "واجد شرایط",
    "won": "مشتری شد 🎉",
    "lost": "از دست رفت",
    "unsubscribed": "لغو دریافت",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            TEXT PRIMARY KEY,
    created_at    REAL NOT NULL,
    niche_id      TEXT,
    niche_fa      TEXT,
    city          TEXT,
    city_key      TEXT,
    country       TEXT,
    lat           TEXT,
    lon           TEXT,
    keywords      TEXT,
    depth         INTEGER,
    radius        INTEGER,
    grid          INTEGER,
    lang          TEXT,
    profile       TEXT,
    socials       INTEGER,
    emails        INTEGER,
    engine        TEXT,
    live          INTEGER,
    status        TEXT,
    total         INTEGER DEFAULT 0,
    hot           INTEGER DEFAULT 0,
    warm          INTEGER DEFAULT 0,
    cold          INTEGER DEFAULT 0,
    avg_score     REAL DEFAULT 0,
    job_ids       TEXT,
    analytics     TEXT,
    error         TEXT,
    progress      TEXT
);
CREATE TABLE IF NOT EXISTS leads (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    place_id      TEXT,
    title         TEXT,
    category      TEXT,
    city          TEXT,
    address       TEXT,
    phone         TEXT,
    emails        TEXT,
    email_primary TEXT,
    website       TEXT,
    domain        TEXT,
    instagram     TEXT,
    facebook      TEXT,
    linkedin      TEXT,
    latitude      TEXT,
    longitude     TEXT,
    rating        REAL,
    reviews       INTEGER,
    price_range   TEXT,
    completeness  INTEGER,
    has_website   INTEGER,
    has_email     INTEGER,
    has_phone     INTEGER,
    has_social    INTEGER,
    score         INTEGER,
    segment       TEXT,
    gaps          TEXT,
    tags          TEXT,
    channel       TEXT,
    pitch         TEXT,
    signals       TEXT,
    input_id      TEXT,
    place_status  TEXT,
    status        TEXT DEFAULT 'new',
    note          TEXT DEFAULT '',
    created_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_leads_run ON leads(run_id);
CREATE INDEX IF NOT EXISTS idx_leads_segment ON leads(segment);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
"""

# ستون‌هایی که در جدول leads ذخیره می‌شوند
LEAD_COLUMNS = [
    "place_id", "title", "category", "city", "address", "phone", "emails", "email_primary",
    "website", "domain", "instagram", "facebook", "linkedin", "latitude", "longitude",
    "rating", "reviews", "price_range", "completeness", "has_website", "has_email",
    "has_phone", "has_social", "score", "segment", "gaps", "tags", "channel", "pitch",
    "signals", "input_id", "place_status", "status", "note",
]


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ستون‌هایی که در پایگاه‌داده ساخته‌شده با نسخه قدیمی وجود ندارند و باید اضافه شوند
_MIGRATIONS = [("city", "TEXT"), ("input_id", "TEXT"), ("place_status", "TEXT")]


def init_db() -> None:
    """جدول‌ها را می‌سازد و ستون‌های تازه را به پایگاه‌داده موجود اضافه می‌کند."""
    with _LOCK, _connect() as conn:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(leads)")}
        for column, kind in _MIGRATIONS:
            if column not in existing:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {kind}")


def new_run_id() -> str:
    return time.strftime("%Y%m%d-") + uuid.uuid4().hex[:6]


def create_run(meta: dict) -> str:
    run_id = meta.get("id") or new_run_id()
    row = {
        "id": run_id,
        "created_at": meta.get("created_at") or time.time(),
        "niche_id": meta.get("niche_id"),
        "niche_fa": meta.get("niche_fa"),
        "city": meta.get("city"),
        "city_key": meta.get("city_key"),
        "country": meta.get("country"),
        "lat": meta.get("lat"),
        "lon": meta.get("lon"),
        "keywords": json.dumps(meta.get("keywords") or [], ensure_ascii=False),
        "depth": meta.get("depth"),
        "radius": meta.get("radius"),
        "grid": meta.get("grid"),
        "lang": meta.get("lang"),
        "profile": meta.get("profile"),
        "socials": int(bool(meta.get("socials"))),
        "emails": int(bool(meta.get("emails"))),
        "engine": meta.get("engine"),
        "live": int(bool(meta.get("live"))),
        "status": meta.get("status") or "pending",
        "job_ids": json.dumps(meta.get("job_ids") or [], ensure_ascii=False),
        "progress": json.dumps(meta.get("progress") or {}, ensure_ascii=False),
    }
    cols = ", ".join(row.keys())
    marks = ", ".join("?" for _ in row)
    with _LOCK, _connect() as conn:
        conn.execute(f"INSERT OR REPLACE INTO runs ({cols}) VALUES ({marks})", list(row.values()))
    return run_id


def update_run(run_id: str, **fields) -> None:
    if not fields:
        return
    for key, value in list(fields.items()):
        if isinstance(value, (dict, list)):
            fields[key] = json.dumps(value, ensure_ascii=False)
    assignments = ", ".join(f"{k}=?" for k in fields)
    with _LOCK, _connect() as conn:
        conn.execute(f"UPDATE runs SET {assignments} WHERE id=?", [*fields.values(), run_id])


def get_run(run_id: str) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return _run_to_dict(row) if row else None


def list_runs(limit: int = 100) -> list[dict]:
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_run_to_dict(r) for r in rows]


def _run_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    for key in ("keywords", "job_ids"):
        try:
            data[key] = json.loads(data.get(key) or "[]")
        except json.JSONDecodeError:
            data[key] = []
    try:
        data["progress"] = json.loads(data.get("progress") or "{}")
    except json.JSONDecodeError:
        data["progress"] = {}
    try:
        data["analytics"] = json.loads(data.get("analytics") or "null")
    except json.JSONDecodeError:
        data["analytics"] = None
    data["live"] = bool(data.get("live"))
    data["socials"] = bool(data.get("socials"))
    data["emails"] = bool(data.get("emails"))
    data["status_fa"] = {
        "pending": "در صف", "running": "در حال اجرا", "enriching": "غنی‌سازی",
        "scoring": "امتیازدهی", "done": "تکمیل شد", "failed": "ناموفق",
        "partial": "نسبی",
    }.get(data.get("status"), data.get("status"))
    return data


def save_leads(run_id: str, rows: list[dict]) -> int:
    """لیدها را ذخیره می‌کند (upsert بر اساس run_id + place_id/title)."""
    now = time.time()
    payloads = []
    for row in rows:
        record = {"id": row.get("id") or f"{run_id}-{uuid.uuid4().hex[:10]}",
                  "run_id": run_id, "created_at": now, "note": row.get("note", "")}
        for col in LEAD_COLUMNS:
            value = row.get(col)
            if col == "status" and value not in LEAD_STATUSES:
                value = "new"
            if col in ("gaps", "tags") and isinstance(value, list):
                value = json.dumps(value, ensure_ascii=False)
            elif col == "signals" and isinstance(value, dict):
                value = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, bool):
                value = int(value)
            record[col] = value
        if not record.get("status"):
            record["status"] = "new"
        row["id"] = record["id"]
        payloads.append(record)

    cols = ["id", "run_id", *LEAD_COLUMNS, "created_at"]
    marks = ", ".join("?" for _ in cols)
    with _LOCK, _connect() as conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO leads ({', '.join(cols)}) VALUES ({marks})",
            [[p.get(c) for c in cols] for p in payloads],
        )
    return len(payloads)


def load_leads(run_id: str | None = None, *, segment: str | None = None,
               status: str | None = None, query: str | None = None,
               gap: str | None = None, channel: str | None = None,
               min_score: int | None = None, sort: str = "score",
               order: str = "desc", limit: int = 1000, offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM leads"
    where, params = [], []
    if run_id:
        where.append("run_id=?"); params.append(run_id)
    if segment:
        where.append("segment=?"); params.append(segment)
    if status:
        where.append("status=?"); params.append(status)
    if channel:
        where.append("channel=?"); params.append(channel)
    if min_score is not None:
        where.append("score>=?"); params.append(int(min_score))
    if gap:
        where.append("gaps LIKE ?"); params.append(f'%"{gap}"%')
    if query:
        like = f"%{query}%"
        where.append("(title LIKE ? OR address LIKE ? OR category LIKE ? OR domain LIKE ? OR emails LIKE ?)")
        params.extend([like, like, like, like, like])
    if where:
        sql += " WHERE " + " AND ".join(where)

    allowed_sort = {"score", "reviews", "rating", "title", "created_at", "completeness"}
    sql += f" ORDER BY {sort if sort in allowed_sort else 'score'} " \
           f"{'DESC' if order != 'asc' else 'ASC'}, title ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with _LOCK, _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_lead_to_dict(r) for r in rows]


def count_leads(run_id: str | None = None) -> int:
    with _LOCK, _connect() as conn:
        if run_id:
            return conn.execute("SELECT COUNT(*) FROM leads WHERE run_id=?", (run_id,)).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]


def _labels() -> tuple[dict, dict, dict]:
    """برچسب‌های فارسی از لایه امتیازدهی/کمپین (import تنبل تا وابستگی دایره‌ای نسازد)."""
    from . import campaign as _campaign
    from . import scoring as _scoring
    return _scoring.SEGMENTS, _scoring.GAP_FA, _campaign.CHANNEL_FA


def _lead_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    for key in ("gaps", "tags"):
        try:
            data[key] = json.loads(data.get(key) or "[]")
        except json.JSONDecodeError:
            data[key] = []
    try:
        data["signals"] = json.loads(data.get("signals") or "{}")
    except json.JSONDecodeError:
        data["signals"] = {}
    for key in ("has_website", "has_email", "has_phone", "has_social"):
        data[key] = bool(data.get(key))
    segments_fa, gaps_fa, channels_fa = _labels()
    data["status"] = data.get("status") or "new"
    data["status_fa"] = LEAD_STATUS_FA.get(data["status"], data["status"])
    data["segment_fa"] = segments_fa.get(data.get("segment"), data.get("segment") or "")
    data["gaps_fa"] = [gaps_fa.get(g, g) for g in data["gaps"]]
    data["channel_fa"] = channels_fa.get(data.get("channel"), data.get("channel") or "")
    return data


def set_lead_status(lead_id: str, status: str, note: str | None = None) -> bool:
    if status not in LEAD_STATUSES:
        return False
    with _LOCK, _connect() as conn:
        if note is None:
            cur = conn.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id))
        else:
            cur = conn.execute("UPDATE leads SET status=?, note=? WHERE id=?", (status, note, lead_id))
    return cur.rowcount > 0


def get_lead(lead_id: str) -> dict | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    return _lead_to_dict(row) if row else None


def delete_run(run_id: str) -> bool:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM leads WHERE run_id=?", (run_id,))
        cur = conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
    return cur.rowcount > 0


def all_leads_for_export(run_id: str) -> list[dict]:
    """همه لیدهای یک اجرا (بدون محدودیت صفحه‌بندی) برای خروجی فایل."""
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT * FROM leads WHERE run_id=? ORDER BY score DESC", (run_id,)).fetchall()
    return [_lead_to_dict(r) for r in rows]


def pipeline_stats() -> dict:
    with _LOCK, _connect() as conn:
        runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        contacted = conn.execute("SELECT COUNT(*) FROM leads WHERE status!='new'").fetchone()[0]
        won = conn.execute("SELECT COUNT(*) FROM leads WHERE status='won'").fetchone()[0]
        with_email = conn.execute("SELECT COUNT(*) FROM leads WHERE has_email=1").fetchone()[0]
    return {"runs": runs, "leads": leads, "contacted": contacted, "won": won,
            "with_email": with_email}


def data_dir() -> Path:
    config.ensure_dirs()
    return config.DATA_DIR
