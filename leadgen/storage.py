"""ذخیره‌سازی: پایگاه‌داده SQLite برای «اجراها» و «لیدها» + وضعیت پیگیری (مینی‌CRM).

چرا SQLite؟ محلی، بدون سرویس، بدون کلید API و با کتابخانه استاندارد پایتون —
همان فلسفه کیت اصلی («همه‌چیز روی دستگاه خودتان»). داده‌های شخصی (ایمیل/تلفن)
هرگز commit نمی‌شوند: پوشه data/ در .gitignore است.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import config

_LOCK = threading.RLock()


def _cloud_backend():
    """اگر پایگاه‌دادهٔ ابری فعال باشد، همان API را از `pgstore` برمی‌گرداند.

    این تنها نقطهٔ تصمیم است: بقیهٔ برنامه (server/pipeline/cli/worker) بدون تغییر
    روی SQLite محلی یا Supabase ابری کار می‌کند.
    """
    if config.DB_BACKEND != "supabase":
        return None
    from . import pgstore
    return pgstore


def _dumps(value) -> str:
    """`json.dumps` با تبدیل امن شیء‌های غیرJSON (مثل `Niche` در payload پیشرفت)."""
    from .cloud import jsonable
    return json.dumps(jsonable(value), ensure_ascii=False)


def _json_or(value, default):
    """رشتهٔ JSON (SQLite) یا مقدار بومی jsonb (Postgres) → مقدار پایتونی."""
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
    if parsed is None:
        return default
    return parsed

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
    progress      TEXT,
    province      TEXT,
    district      TEXT,
    street        TEXT,
    area_label    TEXT,
    area_level    TEXT
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
    maps_link     TEXT,
    plus_code     TEXT,
    location      TEXT,
    niche_id      TEXT,
    niche_fa      TEXT,
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
    score_factors TEXT,
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
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    run_id        TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    priority      INTEGER NOT NULL DEFAULT 0,
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL DEFAULT 3,
    worker        TEXT,
    request       TEXT NOT NULL DEFAULT '{}',
    last_error    TEXT,
    created_at    REAL NOT NULL,
    started_at    REAL,
    finished_at   REAL,
    updated_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, priority DESC, created_at);
CREATE TABLE IF NOT EXISTS deliveries (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL DEFAULT 'digest',
    channel       TEXT NOT NULL DEFAULT 'email',
    target        TEXT,
    subject       TEXT,
    status        TEXT NOT NULL DEFAULT 'sent',
    detail        TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL
);
"""

# ستون‌هایی که در جدول leads ذخیره می‌شوند
LEAD_COLUMNS = [
    "place_id", "title", "category", "city", "address", "phone", "emails", "email_primary",
    "website", "domain", "maps_link", "plus_code", "location", "niche_id", "niche_fa",
    "instagram", "facebook", "linkedin", "latitude", "longitude",
    "rating", "reviews", "price_range", "completeness", "has_website", "has_email",
    "has_phone", "has_social", "score", "segment", "gaps", "tags", "channel", "pitch",
    "score_factors",
    "signals", "input_id", "place_status", "status", "note",
]


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ستون‌هایی که در پایگاه‌داده ساخته‌شده با نسخه قدیمی وجود ندارند و باید اضافه شوند
_MIGRATIONS = [("city", "TEXT"), ("input_id", "TEXT"), ("place_status", "TEXT"),
               ("maps_link", "TEXT"), ("plus_code", "TEXT"), ("location", "TEXT"),
               ("niche_id", "TEXT"), ("niche_fa", "TEXT"), ("score_factors", "TEXT")]

# ستون‌های منطقه‌ای جدول runs (برای رابط جمع‌وجور: استان/شهر/منطقه/خیابان)
_RUN_MIGRATIONS = [("province", "TEXT"), ("district", "TEXT"), ("street", "TEXT"),
                   ("area_label", "TEXT"), ("area_level", "TEXT")]


def init_db() -> None:
    """جدول‌ها را می‌سازد و ستون‌های تازه را به پایگاه‌داده موجود اضافه می‌کند."""
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.init_db()
    with _LOCK, _connect() as conn:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(leads)")}
        for column, kind in _MIGRATIONS:
            if column not in existing:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {column} {kind}")
        existing_runs = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        for column, kind in _RUN_MIGRATIONS:
            if column not in existing_runs:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {kind}")


def new_run_id() -> str:
    return time.strftime("%Y%m%d-") + uuid.uuid4().hex[:6]


def create_run(meta: dict) -> str:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.create_run(meta)
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
        "keywords": _dumps(meta.get("keywords") or []),
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
        "job_ids": _dumps(meta.get("job_ids") or []),
        "progress": _dumps(meta.get("progress") or {}),
        "province": meta.get("province"),
        "district": meta.get("district"),
        "street": meta.get("street"),
        "area_label": meta.get("area_label"),
        "area_level": meta.get("area_level"),
    }
    cols = ", ".join(row.keys())
    marks = ", ".join("?" for _ in row)
    with _LOCK, _connect() as conn:
        conn.execute(f"INSERT OR REPLACE INTO runs ({cols}) VALUES ({marks})", list(row.values()))
    return run_id


def update_run(run_id: str, **fields) -> None:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.update_run(run_id, **fields)
    if not fields:
        return
    for key, value in list(fields.items()):
        if isinstance(value, (dict, list)):
            fields[key] = _dumps(value)
    assignments = ", ".join(f"{k}=?" for k in fields)
    with _LOCK, _connect() as conn:
        conn.execute(f"UPDATE runs SET {assignments} WHERE id=?", [*fields.values(), run_id])


def get_run(run_id: str) -> dict | None:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.get_run(run_id)
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    return _run_to_dict(row) if row else None


def list_runs(limit: int = 100) -> list[dict]:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.list_runs(limit)
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_run_to_dict(r) for r in rows]


def _run_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    for key in ("keywords", "job_ids"):
        value = _json_or(data.get(key), [])
        data[key] = value if isinstance(value, list) else []
    value = _json_or(data.get("progress"), {})
    data["progress"] = value if isinstance(value, dict) else {}
    data["analytics"] = _json_or(data.get("analytics"), None)
    data["live"] = bool(data.get("live"))
    data["socials"] = bool(data.get("socials"))
    data["emails"] = bool(data.get("emails"))
    data["status_fa"] = {
        "pending": "در صف", "queued": "در صف", "running": "در حال اجرا", "enriching": "غنی‌سازی",
        "scoring": "امتیازدهی", "done": "تکمیل شد", "failed": "ناموفق",
        "partial": "نسبی",
    }.get(data.get("status"), data.get("status"))
    return data


def save_leads(run_id: str, rows: list[dict]) -> int:
    """لیدها را ذخیره می‌کند (upsert بر اساس run_id + place_id/title)."""
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.save_leads(run_id, rows)
    now = time.time()
    payloads = []
    for row in rows:
        record = {"id": row.get("id") or f"{run_id}-{uuid.uuid4().hex[:10]}",
                  "run_id": run_id, "created_at": now, "note": row.get("note", "")}
        for col in LEAD_COLUMNS:
            value = row.get(col)
            if col == "status" and value not in LEAD_STATUSES:
                value = "new"
            if col in ("gaps", "tags", "score_factors") and isinstance(value, list):
                value = _dumps(value)
            elif col == "signals" and isinstance(value, dict):
                value = _dumps(value)
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
    cloud = _cloud_backend()
    if cloud is not None:
        scope = {key: value for key, value in locals().items() if key not in ("cloud", "scope")}
        return cloud.load_leads(**scope)
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


# ستون‌هایی که «جست‌وجوی قوی» رویشان می‌گردد (هر توکن باید در یکی از این‌ها پیدا شود)
_SEARCH_FIELDS = ("title", "category", "niche_fa", "address", "city", "phone", "emails",
                  "email_primary", "website", "domain", "tags", "pitch", "gaps", "plus_code",
                  "input_id", "note")


def _tokens(text: str) -> list[str]:
    """شکستن متن جست‌وجو به توکن‌ها (برای AND کردن چند کلمه)."""
    import re as _re
    parts = _re.split(r"[\s،,;]+", (text or "").strip())
    return [p for p in (x.strip() for x in parts) if len(p) >= 2][:8]


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """فاصلهٔ دو نقطه بر حسب متر (برای فیلتر «شعاع منطقه»)."""
    import math
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def search_leads(*, q: str = "", run_id: str | None = None, segment: str | None = None,
                 status: str | None = None, channel: str | None = None, gap: str | None = None,
                 niche_id: str | None = None, niche_group: str | None = None,
                 niche_fa: str | None = None,
                 has_phone: bool | None = None, has_email: bool | None = None,
                 has_website: bool | None = None, has_social: bool | None = None,
                 has_instagram: bool | None = None, min_rating: float | None = None,
                 min_reviews: int | None = None, min_score: int | None = None,
                 lat: float | None = None, lon: float | None = None, radius_m: int = 0,
                 sort: str = "score", order: str = "desc",
                 limit: int = 200, offset: int = 0) -> dict:
    """جست‌وجوی قوی در همهٔ لیدهای ذخیره‌شده (تمام اجراها) با فیلترهای ترکیبی.

    ویژگی‌ها:
      • هر توکنِ متن باید در یکی از فیلدهای کلیدی باشد (AND بین توکن‌ها، OR بین فیلدها)
      • فیلترهای تماس: تلفن/ایمیل/وب‌سایت/شبکه اجتماعی/اینستاگرام
      • فیلتر کیفیت: امتیاز، ستاره، تعداد نظر، بخش داغ/گرم/سرد، شکاف
      • فیلتر جغرافیایی: شعاع متر حول یک نقطه (منطقه/خیابان انتخاب‌شده در رابط)
      • `facets`: شمارش‌های زنده برای نوار فیلتر (بدون توجه به limit)
    """
    cloud = _cloud_backend()
    if cloud is not None:
        scope = {key: value for key, value in locals().items() if key not in ("cloud", "scope")}
        return cloud.search_leads(**scope)
    where: list[str] = []
    params: list = []

    def add(clause: str, *values) -> None:
        where.append(clause)
        params.extend(values)

    if run_id:
        add("l.run_id=?", run_id)
    if segment:
        add("l.segment=?", segment)
    if status:
        add("l.status=?", status)
    if channel:
        add("l.channel=?", channel)
    if gap:
        add("l.gaps LIKE ?", f'%"{gap}"%')
    if niche_id:
        add("(l.niche_id=? OR l.category LIKE ? OR l.title LIKE ?)", niche_id,
            f"%{niche_id}%", f"%{niche_id}%")
    if niche_fa:
        add("(l.niche_fa=? OR l.category=? OR l.niche_fa LIKE ?)", niche_fa, niche_fa,
            f"%{niche_fa}%")
    if niche_group:
        ids = [n["id"] for n in _group_niches(niche_group)]
        if ids:
            add(f"l.niche_id IN ({','.join('?' * len(ids))})", *ids)
    for flag, column in ((has_phone, "has_phone"), (has_email, "has_email"),
                         (has_website, "has_website"), (has_social, "has_social")):
        if flag is not None:
            add(f"COALESCE(l.{column},0)={1 if flag else 0}")
    if has_instagram is not None:
        add(f"COALESCE(TRIM(l.instagram),'')<>''" if has_instagram
            else f"COALESCE(TRIM(l.instagram),'')=''")
    if min_rating is not None:
        add("COALESCE(l.rating,0)>=?", float(min_rating))
    if min_reviews is not None:
        add("COALESCE(l.reviews,0)>=?", int(min_reviews))
    if min_score is not None:
        add("COALESCE(l.score,0)>=?", int(min_score))
    if lat is not None and lon is not None and radius_m:
        # پیش‌فیلتر مربعی در SQL (سریع)، سپس فاصلهٔ دقیق در پایتون
        dlat = radius_m / 111000.0
        dlon = radius_m / (111000.0 * max(0.05, abs(math.cos(math.radians(float(lat))))))
        add("CAST(l.latitude AS REAL) BETWEEN ? AND ?", float(lat) - dlat, float(lat) + dlat)
        add("CAST(l.longitude AS REAL) BETWEEN ? AND ?", float(lon) - dlon, float(lon) + dlon)

    for token in _tokens(q):
        like = f"%{token}%"
        clause = " OR ".join(f"COALESCE(l.{field},'') LIKE ?" for field in _SEARCH_FIELDS)
        add(f"({clause})", *([like] * len(_SEARCH_FIELDS)))

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    allowed_sort = {"score", "reviews", "rating", "title", "created_at", "completeness"}
    sort_col = sort if sort in allowed_sort else "score"
    direction = "DESC" if order != "asc" else "ASC"
    geo_filter = lat is not None and lon is not None and bool(radius_m)
    select_sql = ("SELECT l.*, r.city AS run_city, r.niche_fa AS run_niche, "
                  "r.area_label AS run_area, r.created_at AS run_created "
                  "FROM leads l LEFT JOIN runs r ON r.id=l.run_id" + where_sql)

    with _LOCK, _connect() as conn:
        if geo_filter:
            # شعاع دایره‌ای در SQL دقیق نیست → همهٔ نامزدهای مربعی را می‌گیریم و در
            # پایتون فاصلهٔ واقعی را حساب می‌کنیم تا total و facets هم درست باشند.
            rows = conn.execute(
                f"{select_sql} ORDER BY {sort_col} {direction}, l.title ASC", params).fetchall()
            facet_rows = rows
        else:
            facet_rows = conn.execute(
                "SELECT l.segment, l.status, l.niche_id, l.niche_fa, l.score, l.run_id, "
                "l.has_phone, l.has_email, l.has_website, l.has_social "
                "FROM leads l LEFT JOIN runs r ON r.id=l.run_id" + where_sql, params).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM leads l LEFT JOIN runs r ON r.id=l.run_id" + where_sql,
                params).fetchone()[0]
            rows = conn.execute(
                f"{select_sql} ORDER BY {sort_col} {direction}, l.title ASC LIMIT ? OFFSET ?",
                [*params, int(limit), int(offset)]).fetchall()

    matched = []
    for row in rows:
        data = dict(row)
        item = _lead_to_dict(row)
        item["run_city"] = data.get("run_city") or ""
        item["run_niche"] = data.get("run_niche") or ""
        item["run_area"] = data.get("run_area") or item["run_city"]
        item["run_created"] = data.get("run_created")
        if geo_filter:
            try:
                item["distance_m"] = round(_haversine(
                    float(lat), float(lon),
                    float(item.get("lat") or 0), float(item.get("lon") or 0)))
            except (TypeError, ValueError):
                item["distance_m"] = None
            if item.get("distance_m") is None or item["distance_m"] > float(radius_m):
                continue                    # بیرون از دایرهٔ شعاع → حذف
        matched.append(item)

    if geo_filter:
        total = len(matched)
        page = matched[int(offset):int(offset) + int(limit)]
    else:
        page = matched

    facets_source = matched if geo_filter else [dict(r) for r in facet_rows]
    return {"leads": page, "total": total, "returned": len(page),
            "facets": _facets(facets_source, total),
            "geo": ({"lat": lat, "lon": lon, "radius_m": radius_m} if geo_filter else {}),
            "filters": _active_filters(locals())}


def _group_niches(group: str) -> list[dict]:
    from . import niches as _niches
    return [n for n in _niches.all_niches() if n.get("group") == group]


def _facets(rows, total: int = 0, partial: bool = False) -> dict:
    """شمارش‌های زنده برای نوار فیلتر رابط جمع‌وجور.

    در حالت بدون فیلتر شعاعی، `rows` فقط صفحهٔ جاری است → `partial=True` و شمارش‌ها
    «از میان نتیجه‌های نمایش‌داده‌شده» هستند؛ `total` همیشه عدد درست کل است.
    """
    facets = {"segment": {}, "status": {}, "niche": {}, "contact": {}, "runs": {},
              "total": total or len(rows), "counted": len(rows), "partial": bool(partial)}
    scores = []
    for row in rows:
        data = dict(row) if not isinstance(row, dict) else row
        seg = data.get("segment") or "na"
        facets["segment"][seg] = facets["segment"].get(seg, 0) + 1
        st = data.get("status") or "new"
        facets["status"][st] = facets["status"].get(st, 0) + 1
        niche_fa = data.get("niche_fa") or data.get("niche_id") or "—"
        facets["niche"][niche_fa] = facets["niche"].get(niche_fa, 0) + 1
        run = data.get("run_id") or "—"
        facets["runs"][run] = facets["runs"].get(run, 0) + 1
        for key in ("has_phone", "has_email", "has_website", "has_social"):
            if data.get(key):
                facets["contact"][key] = facets["contact"].get(key, 0) + 1
        if data.get("score") is not None:
            scores.append(float(data["score"]))
    facets["niche"] = dict(sorted(facets["niche"].items(), key=lambda kv: -kv[1])[:24])
    facets["score"] = {"min": int(min(scores)) if scores else 0,
                       "max": int(max(scores)) if scores else 0,
                       "avg": round(sum(scores) / len(scores), 1) if scores else 0}
    return facets


def _active_filters(scope: dict) -> dict:
    keys = ("q", "run_id", "segment", "status", "channel", "gap", "niche_id", "niche_fa",
            "niche_group",
            "has_phone", "has_email", "has_website", "has_social", "has_instagram",
            "min_rating", "min_reviews", "min_score", "lat", "lon", "radius_m")
    return {k: scope.get(k) for k in keys if scope.get(k) not in (None, "", 0, False)}


def count_leads(run_id: str | None = None) -> int:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.count_leads(run_id)
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
    for key in ("gaps", "tags", "score_factors"):
        value = _json_or(data.get(key), [])
        data[key] = value if isinstance(value, list) else []
    value = _json_or(data.get("signals"), {})
    data["signals"] = value if isinstance(value, dict) else {}
    for key in ("has_website", "has_email", "has_phone", "has_social"):
        data[key] = bool(data.get(key))
    # مختصات عددی (برای نقشه/لوکیشن دقیق) از ستون‌های متنی latitude/longitude
    for src, dst in (("latitude", "lat"), ("longitude", "lon")):
        try:
            data[dst] = round(float(data.get(src)), 6)
        except (TypeError, ValueError):
            data[dst] = 0.0
    data["has_location"] = bool(data["lat"] or data["lon"])
    if not data.get("location") and data["has_location"]:
        data["location"] = f"{data['lat']}, {data['lon']}"
    segments_fa, gaps_fa, channels_fa = _labels()
    data["status"] = data.get("status") or "new"
    data["status_fa"] = LEAD_STATUS_FA.get(data["status"], data["status"])
    data["segment_fa"] = segments_fa.get(data.get("segment"), data.get("segment") or "")
    data["gaps_fa"] = [gaps_fa.get(g, g) for g in data["gaps"]]
    data["channel_fa"] = channels_fa.get(data.get("channel"), data.get("channel") or "")
    return data


def set_lead_status(lead_id: str, status: str, note: str | None = None) -> bool:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.set_lead_status(lead_id, status, note)
    if status not in LEAD_STATUSES:
        return False
    with _LOCK, _connect() as conn:
        if note is None:
            cur = conn.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id))
        else:
            cur = conn.execute("UPDATE leads SET status=?, note=? WHERE id=?", (status, note, lead_id))
    return cur.rowcount > 0


def get_lead(lead_id: str) -> dict | None:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.get_lead(lead_id)
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    return _lead_to_dict(row) if row else None


def delete_run(run_id: str) -> bool:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.delete_run(run_id)
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM leads WHERE run_id=?", (run_id,))
        cur = conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
    return cur.rowcount > 0


def all_leads_for_export(run_id: str) -> list[dict]:
    """همه لیدهای یک اجرا (بدون محدودیت صفحه‌بندی) برای خروجی فایل."""
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.all_leads_for_export(run_id)
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT * FROM leads WHERE run_id=? ORDER BY score DESC", (run_id,)).fetchall()
    return [_lead_to_dict(r) for r in rows]


def pipeline_stats() -> dict:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.pipeline_stats()
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


# ── صف اجراها (پشتیبان SQLite — آینهٔ توابع SQL در supabase/schema.sql) ───────
JOB_STATUSES = ("pending", "running", "done", "failed")
JOB_STATUS_FA = {"pending": "در صف", "queued": "در صف", "running": "در حال اجرا",
                 "done": "تکمیل شد", "failed": "ناموفق"}
_STALE_MESSAGE = "ورکر پاسخ نداد — دوباره به صف برگشت"


def job_status_fa(status: str | None) -> str:
    """برچسب فارسی وضعیت کار (برای رابط و API)."""
    return JOB_STATUS_FA.get(status or "", status or "")


def _job_to_dict(row) -> dict:
    data = dict(row)
    data["request"] = _json_or(data.get("request"), {})
    if not isinstance(data["request"], dict):
        data["request"] = {}
    data["status_fa"] = job_status_fa(data.get("status"))
    return data


def enqueue_job(run_id: str, request: dict, priority: int = 0) -> str:
    """یک اجرا را در صف می‌گذارد تا ورکر (Codespace/Actions) برش دارد."""
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.enqueue_job(run_id, request, priority)
    job_id = run_id or ("job-" + uuid.uuid4().hex[:12])
    now = time.time()
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO jobs (id, run_id, status, priority, attempts, max_attempts,"
            " worker, request, last_error, created_at, started_at, finished_at, updated_at)"
            " VALUES (?,?, 'pending', ?, 0, ?, NULL, ?, NULL, ?, NULL, NULL, ?)",
            (job_id, run_id, int(priority), config.WORKER_MAX_ATTEMPTS,
             _dumps(request or {}), now, now))
    return job_id


def claim_job(worker: str | None = None, max_attempts: int | None = None) -> dict | None:
    """یک کار در انتظار را برمی‌دارد (زیر قفل؛ دو ورکر یک کار را نمی‌گیرند)."""
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.claim_job(worker, max_attempts)
    worker = worker or config.WORKER_ID
    ceiling = int(max_attempts or config.WORKER_MAX_ATTEMPTS)
    now = time.time()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='pending' AND attempts<? "
            "ORDER BY priority DESC, created_at ASC LIMIT 1", (ceiling,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE jobs SET status='running', worker=?, attempts=attempts+1,"
                     " started_at=?, updated_at=? WHERE id=?", (worker, now, now, row["id"]))
        job = _job_to_dict(row)
    job.update({"status": "running", "worker": worker, "attempts": job["attempts"] + 1,
                "started_at": now})
    job["status_fa"] = _job_to_dict({"status": "running"})["status_fa"]   # برچسب وضعیت تازه
    return job


def heartbeat_job(job_id: str, progress: dict | None = None, status: str | None = None) -> bool:
    """پیشرفت را روی job و run می‌نویسد تا رابط وضعیت زنده را نشان دهد."""
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.heartbeat_job(job_id, progress, status)
    now = time.time()
    with _LOCK, _connect() as conn:
        if status:
            conn.execute("UPDATE jobs SET updated_at=?, status=? WHERE id=?", (now, status, job_id))
        else:
            conn.execute("UPDATE jobs SET updated_at=? WHERE id=?", (now, job_id))
        row = conn.execute("SELECT run_id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or not row["run_id"] or not (progress or status):
            return True
        run = conn.execute("SELECT progress, status, error FROM runs WHERE id=?",
                           (row["run_id"],)).fetchone()
        if not run:
            return True
        merged = _json_or(run["progress"], {}) or {}
        merged.update(progress or {})
        merged["ts"] = now
        new_status = (progress or {}).get("status") or status or run["status"]
        new_error = (progress or {}).get("error") or run["error"]
        conn.execute("UPDATE runs SET progress=?, status=?, error=? WHERE id=?",
                     (_dumps(merged), new_status, new_error, row["run_id"]))
    return True


def _job_message(final: str, job: dict, error: str | None = None) -> str:
    """پیام فارسی وضعیت کار برای نمایش در رابط (نه متن خام خطا)."""
    attempts = int(job.get("attempts") or 0)
    ceiling = int(job.get("max_attempts") or config.WORKER_MAX_ATTEMPTS)
    detail = (error or "").strip()
    if final == "done":
        return "پایان کار" + (f" — {detail}" if detail else "")
    if final == "pending":
        return f"تلاش {attempts} ناموفق بود — دوباره در صف" + (f": {detail}" if detail else "")
    return (f"اجرا ناموفق شد ({attempts} از {ceiling} تلاش)"
            + (f": {detail}" if detail else ""))


def finish_job(job_id: str, status: str = "done", error: str | None = None) -> dict | None:
    """پایان کار؛ اگر شکست خورده و تلاش باقی است، برای تلاش مجدد به صف برمی‌گردد."""
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.finish_job(job_id, status, error)
    now = time.time()
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        job = _job_to_dict(row)
        if status == "failed" and job["attempts"] < job["max_attempts"]:
            conn.execute("UPDATE jobs SET status='pending', last_error=?, worker=NULL,"
                         " updated_at=? WHERE id=?", (error, now, job_id))
            final = "pending"
        else:
            conn.execute("UPDATE jobs SET status=?, last_error=?, finished_at=?, updated_at=?"
                         " WHERE id=?", (status, error, now, now, job_id))
            final = status
        if job.get("run_id"):
            run_status = "queued" if final == "pending" else final
            run = conn.execute("SELECT progress FROM runs WHERE id=?", (job["run_id"],)).fetchone()
            merged = _json_or(run["progress"] if run else {}, {}) or {}
            merged.update({"status": run_status, "stage": final,
                           "message": _job_message(final, job, error), "ts": now})
            if error:
                merged["error"] = error
            conn.execute("UPDATE runs SET status=?, error=?, progress=? WHERE id=?",
                         (run_status, error, _dumps(merged), job["run_id"]))
    job["status"] = final
    job["last_error"] = error
    return job


def requeue_stale_jobs(seconds: int = 1800) -> int:
    """کارهای نیمه‌تمامِ ورکرِ مرده را به صف برمی‌گرداند (Codespace خاموش شد)."""
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.requeue_stale_jobs(seconds)
    now = time.time()
    cutoff = now - max(int(seconds), 30)
    count = 0
    with _LOCK, _connect() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE status='running'"
                            " AND COALESCE(started_at, ?) < ?", (now, cutoff)).fetchall()
        for row in rows:
            job = _job_to_dict(row)
            final = "failed" if job["attempts"] >= job["max_attempts"] else "pending"
            conn.execute("UPDATE jobs SET status=?, worker=NULL,"
                         " last_error=COALESCE(last_error, ?), updated_at=? WHERE id=?",
                         (final, _STALE_MESSAGE, now, job["id"]))
            count += 1
        conn.execute("UPDATE runs SET status='queued' WHERE status='running' AND id IN"
                     " (SELECT run_id FROM jobs WHERE status='pending')")
    return count


def queue_stats() -> dict:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.queue_stats()
    now = time.time()
    with _LOCK, _connect() as conn:
        counts = {r["status"]: r["c"] for r in
                  conn.execute("SELECT status, COUNT(*) AS c FROM jobs GROUP BY status")}
        oldest = conn.execute("SELECT MIN(created_at) AS oldest FROM jobs"
                              " WHERE status='pending'").fetchone()["oldest"]
        workers = [r["worker"] for r in conn.execute(
            "SELECT DISTINCT worker FROM jobs WHERE status='running' AND worker IS NOT NULL")]
    return {"pending": counts.get("pending", 0), "running": counts.get("running", 0),
            "done": counts.get("done", 0), "failed": counts.get("failed", 0),
            "oldest_wait_s": int(now - oldest) if oldest else 0, "workers": workers}


def list_jobs(status: str | None = None, limit: int = 50) -> list[dict]:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.list_jobs(status, limit)
    with _LOCK, _connect() as conn:
        if status:
            rows = conn.execute("SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC"
                                " LIMIT ?", (status, int(limit))).fetchall()
        else:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                                (int(limit),)).fetchall()
    return [_job_to_dict(r) for r in rows]


# ── تحویل خروجی (گزارش ایمیل‌ها) ──────────────────────────────────────────────
def log_delivery(kind: str, channel: str, target: str, subject: str, status: str,
                 detail: dict | None = None) -> str:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.log_delivery(kind, channel, target, subject, status, detail)
    delivery_id = "dlv-" + uuid.uuid4().hex[:12]
    with _LOCK, _connect() as conn:
        conn.execute("INSERT OR REPLACE INTO deliveries (id, kind, channel, target, subject,"
                     " status, detail, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (delivery_id, kind or "digest", channel or "email", target, subject,
                      status or "sent", _dumps(detail or {}), time.time()))
    return delivery_id


def last_delivery(kind: str = "digest") -> dict | None:
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.last_delivery(kind)
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM deliveries WHERE kind=? AND status='sent'"
                           " ORDER BY created_at DESC LIMIT 1", (kind,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["detail"] = _json_or(data.get("detail"), {})
    return data


def dashboard_stats() -> dict:
    """آمار یک‌نگاه برای داشبورد/ایمیل (در هر دو پشتیبان هم‌شکل)."""
    cloud = _cloud_backend()
    if cloud is not None:
        return cloud.dashboard_stats()
    with _LOCK, _connect() as conn:
        def scalar(sql: str) -> int:
            return conn.execute(sql).fetchone()[0]
        stats = {
            "runs": scalar("SELECT COUNT(*) FROM runs"),
            "leads": scalar("SELECT COUNT(*) FROM leads"),
            "contacted": scalar("SELECT COUNT(*) FROM leads WHERE status!='new'"),
            "won": scalar("SELECT COUNT(*) FROM leads WHERE status='won'"),
            "with_email": scalar("SELECT COUNT(*) FROM leads WHERE has_email=1"),
            "with_phone": scalar("SELECT COUNT(*) FROM leads WHERE has_phone=1"),
            "with_site": scalar("SELECT COUNT(*) FROM leads WHERE has_website=1"),
            "hot": scalar("SELECT COUNT(*) FROM leads WHERE segment='hot'"),
        }
        latest = conn.execute("SELECT id, niche_fa, city, area_label, status, total, hot,"
                              " created_at FROM runs ORDER BY created_at DESC LIMIT 5").fetchall()
    stats["queue"] = queue_stats()
    stats["latest_runs"] = [dict(r) for r in latest]
    return stats
