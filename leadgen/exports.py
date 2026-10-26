"""خروجی فایل: CSV (سازگار با اکسل و گوگل‌شیت)، JSON و XLSX چندبرگی.

نکته مهم برای فارسی: CSV با BOM نوشته می‌شود (utf-8-sig) تا اکسل فارسی را
درست نشان دهد. فایل XLSX هم بدون هیچ کتابخانه بیرونی ساخته می‌شود — یک نویسنده
مینی‌مال OOXML با zipfile از کتابخانه استاندارد (چون فلسفه کیت «بدون نصب» است).

⚠ این فایل‌ها داده شخصی (ایمیل/تلفن) دارند: پوشه data/ در .gitignore است و
هرگز نباید commit شوند.
"""
from __future__ import annotations

import csv
import io
import json
import time
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import config

# ستون‌های خروجی «لید» — ۸ فیلد اصلی اسکیل + غنی‌سازی + امتیاز + وضعیت
EXPORT_COLUMNS: list[tuple[str, str]] = [
    ("title", "نام کسب‌وکار"),
    ("category", "صنف"),
    ("city", "شهر"),
    ("address", "نشانی"),
    ("phone", "تلفن"),
    ("email_primary", "ایمیل"),
    ("emails", "همه ایمیل‌ها"),
    ("website", "وب‌سایت"),
    ("instagram", "اینستاگرام"),
    ("facebook", "فیسبوک"),
    ("linkedin", "لینکدین"),
    ("rating", "امتیاز"),
    ("reviews", "تعداد نظر"),
    ("score", "امتیاز لید (۰-۱۰۰)"),
    ("segment_fa", "بخش"),
    ("channel_fa", "کانال پیشنهادی"),
    ("pitch", "زاویه پیشنهاد"),
    ("gaps_fa", "شکاف‌های دیجیتال"),
    ("tags", "برچسب‌ها"),
    ("status_fa", "وضعیت پیگیری"),
    ("note", "یادداشت"),
]

FULL_COLUMNS = EXPORT_COLUMNS + [
    ("domain", "دامنه"),
    ("completeness", "کامل بودن اطلاعات ٪"),
    ("latitude", "عرض جغرافیایی"),
    ("longitude", "طول جغرافیایی"),
    ("place_id", "شناسه مکان"),
    ("profile", "پروفایل امتیازدهی"),
    ("input_id", "کلیدواژه منبع"),
]

SUMMARY_COLUMNS: list[tuple[str, str]] = [
    ("id", "شناسه اجرا"),
    ("created_fa", "تاریخ"),
    ("niche_fa", "صنف"),
    ("city", "شهر"),
    ("engine", "موتور"),
    ("keywords", "کلیدواژه‌ها"),
    ("depth", "عمق"),
    ("grid", "شبکه"),
    ("profile", "پروفایل"),
    ("total", "تعداد لید"),
    ("hot", "داغ"),
    ("warm", "گرم"),
    ("cold", "سرد"),
    ("avg_score", "میانگین امتیاز"),
    ("with_email", "با ایمیل"),
    ("with_website", "با وب‌سایت"),
    ("with_social", "با شبکه اجتماعی"),
    ("reachable", "قابل تماس"),
]

CAMPAIGN_COLUMNS: list[tuple[str, str]] = [
    ("title", "نام کسب‌وکار"),
    ("channel_fa", "کانال"),
    ("to", "گیرنده"),
    ("subject", "موضوع"),
    ("body", "متن آماده ارسال"),
    ("score", "امتیاز"),
    ("segment_fa", "بخش"),
    ("status_fa", "وضعیت"),
]


def _value(row: dict, key: str) -> str:
    value = row.get(key, "")
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "، ".join(str(v) for v in value if v)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def rows_for_export(rows: list[dict], full: bool = False) -> list[dict]:
    cols = FULL_COLUMNS if full else EXPORT_COLUMNS
    return [{fa: _value(r, key) for key, fa in cols} for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# CSV
# ─────────────────────────────────────────────────────────────────────────────
def write_csv(path: Path, rows: list[dict], full: bool = False) -> Path:
    data = rows_for_export(rows, full=full)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        if not data:
            fh.write("")
            return path
        writer = csv.DictWriter(fh, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)
    return path


def csv_bytes(rows: list[dict], full: bool = False) -> bytes:
    buffer = io.StringIO()
    data = rows_for_export(rows, full=full)
    if data:
        writer = csv.DictWriter(buffer, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def csv_bytes_custom(rows: list[dict], columns: list[tuple[str, str]]) -> bytes:
    """CSV با ستون‌های دلخواه (مثلاً ستون‌های کمپین)."""
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=[fa for _, fa in columns])
        writer.writeheader()
        for row in rows:
            writer.writerow({fa: _value(row, key) for key, fa in columns})
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def read_csv(path: Path) -> list[dict]:
    """خواندن CSV ورودی (برای وارد کردن فهرست موجود و امتیازدهی مجدد)."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# ─────────────────────────────────────────────────────────────────────────────
# JSON
# ─────────────────────────────────────────────────────────────────────────────
def write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)
    return path


def json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# XLSX — نویسنده مینی‌مال بدون وابستگی
# ─────────────────────────────────────────────────────────────────────────────
def _col_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _sheet_xml(headers: list[str], rows: list[list[str]], rtl: bool = True) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>']
    sheet_view = '<sheetView workbookViewId="0"' + (' rightToLeft="1"' if rtl else '') + '/>'
    parts.append(f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{sheet_view}')
    # سبک: ستون سرتیتر بولد + عرض ستون‌ها
    if rows:
        widths = [max(len(str(h)), max(len(str(r[i])) if i < len(r) else 0 for r in rows[:200]))
                  for i, h in enumerate(headers)]
        cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{min(60, max(10, w + 2))}"/>'
                       for i, w in enumerate(widths))
        parts.append(f"<cols>{cols}</cols>")
    parts.append("<sheetData>")

    def cell(ref: str, value: str, style: int) -> str:
        if value == "":
            return ""
        # عددی بودن → سلول عددی، در غیر این صورت رشته inline (بدون نیاز به sharedStrings)
        try:
            number = float(value.replace(",", ""))
            if value.strip() and not value.startswith(("+", "0")) or value == "0":
                return f'<c r="{ref}" s="{style}"><v>{number:g}</v></c>'
        except ValueError:
            pass
        return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'

    parts.append("<row r=\"1\">" + "".join(
        cell(f"{_col_letter(i + 1)}1", str(h), 1) for i, h in enumerate(headers)) + "</row>")
    for r_index, row in enumerate(rows, start=2):
        cells = "".join(cell(f"{_col_letter(i + 1)}{r_index}", str(value), 0)
                        for i, value in enumerate(row))
        parts.append(f'<row r="{r_index}">{cells}</row>')
    parts.append("</sheetData>")
    parts.append('<autoFilter ref="A1:' + _col_letter(max(1, len(headers))) + str(len(rows) + 1) + '"/>')
    parts.append("</worksheet>")
    return "".join(parts)


_STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>
<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF1F6FEB"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
</cellXfs>
</styleSheet>"""

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
{overrides}
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _workbook_xml(sheets: list[str]) -> str:
    entries = "".join(
        f'<sheet name="{escape(name[:31])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, name in enumerate(sheets))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{entries}</sheets></workbook>")


def _workbook_rels(count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{i + 1}" '
        f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i + 1}.xml"/>' for i in range(count))
    rels += (f'<Relationship Id="rId{count + 1}" '
             f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
             f'Target="styles.xml"/>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{rels}</Relationships>")


def write_xlsx(path: Path, sheets: dict[str, tuple[list[str], list[list[str]]]]) -> Path:
    """sheets = {"نام برگه": (headers, rows)} — فایل xlsx واقعی می‌سازد."""
    names = list(sheets.keys()) or ["Sheet1"]
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(names)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES.format(overrides=overrides))
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("xl/workbook.xml", _workbook_xml(names))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(names)))
        zf.writestr("xl/styles.xml", _STYLES_XML)
        for i, name in enumerate(names, start=1):
            headers, rows = sheets[name]
            zf.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(headers, rows))
    return path


def xlsx_bytes(sheets: dict[str, tuple[list[str], list[list[str]]]]) -> bytes:
    buffer = io.BytesIO()
    names = list(sheets.keys()) or ["Sheet1"]
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(len(names)))
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES.format(overrides=overrides))
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("xl/workbook.xml", _workbook_xml(names))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(names)))
        zf.writestr("xl/styles.xml", _STYLES_XML)
        for i, name in enumerate(names, start=1):
            headers, rows = sheets[name]
            zf.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(headers, rows))
    return buffer.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# بسته خروجی کامل یک اجرا
# ─────────────────────────────────────────────────────────────────────────────
def build_workbook(rows: list[dict], analytics: dict | None, run: dict | None,
                   campaign_rows: list[dict] | None = None, full: bool = False) -> dict:
    """برگه‌های یک فایل xlsx کامل: لیدها، داغ‌ها، خلاصه/تحلیل، کمپین."""
    cols = FULL_COLUMNS if full else EXPORT_COLUMNS
    headers = [fa for _, fa in cols]
    body = [[_value(r, key) for key, _ in cols] for r in rows]
    sheets: dict[str, tuple[list[str], list[list[str]]]] = {"لیدها": (headers, body)}

    hot = [r for r in rows if r.get("segment") == "hot"]
    sheets["لیدهای داغ"] = (headers, [[_value(r, key) for key, _ in cols] for r in hot])

    if analytics:
        summary_headers = ["شاخص", "مقدار"]
        cov = analytics.get("coverage", {})
        seg = analytics.get("segments", {})
        summary_rows = [
            ["شناسه اجرا", _value(run or {}, "id")],
            ["صنف", _value(run or {}, "niche_fa")],
            ["شهر", _value(run or {}, "city")],
            ["موتور", _value(run or {}, "engine")],
            ["کلیدواژه‌ها", "، ".join(_value(run or {}, "keywords") or [])],
            ["تعداد لید", str(cov.get("total", 0))],
            ["میانگین امتیاز", str(analytics.get("avg_score", 0))],
            ["لید داغ", str(seg.get("hot", {}).get("count", 0))],
            ["لید گرم", str(seg.get("warm", {}).get("count", 0))],
            ["لید سرد", str(seg.get("cold", {}).get("count", 0))],
            ["دارای وب‌سایت", str(cov.get("website", 0))],
            ["دارای ایمیل", str(cov.get("email", 0))],
            ["دارای تلفن", str(cov.get("phone", 0))],
            ["دارای اینستاگرام", str(cov.get("instagram", 0))],
            ["قابل تماس", str(cov.get("reachable", 0))],
            ["میانگین کامل بودن ٪", str(cov.get("avg_completeness", 0))],
        ]
        sheets["خلاصه"] = (summary_headers, summary_rows)

        gap_rows = [[g["fa"], str(g["count"]), f"{g['percent']}٪"]
                    for g in analytics.get("gaps", [])]
        if gap_rows:
            sheets["شکاف‌های دیجیتال"] = (["شکاف", "تعداد", "درصد"], gap_rows)

        funnel_rows = [[step["fa"], str(step["count"]), f"{step['percent']}٪"]
                       for step in analytics.get("funnel", [])]
        if funnel_rows:
            sheets["قیف"] = (["مرحله", "تعداد", "درصد"], funnel_rows)

        insight_rows = [[ins["fa"]] for ins in analytics.get("insights", [])]
        if insight_rows:
            sheets["بینش‌ها"] = (["بینش اجرایی"], insight_rows)

    if campaign_rows:
        sheets["کمپین"] = ([fa for _, fa in CAMPAIGN_COLUMNS],
                           [[_value(r, key) for key, _ in CAMPAIGN_COLUMNS] for r in campaign_rows])
    return sheets


def export_run(path: Path, rows: list[dict], analytics: dict | None = None,
               run: dict | None = None, fmt: str = "csv", full: bool = False,
               campaign_rows: list[dict] | None = None) -> Path:
    fmt = (fmt or "csv").lower().lstrip(".")
    if fmt in ("xlsx", "excel"):
        return write_xlsx(path.with_suffix(".xlsx"),
                          build_workbook(rows, analytics, run, campaign_rows, full=full))
    if fmt == "json":
        return write_json(path.with_suffix(".json"),
                          {"run": run, "analytics": analytics, "leads": rows})
    return write_csv(path.with_suffix(".csv"), rows, full=full)


def safe_filename(text: str) -> str:
    """نام فایل امن (فارسی مجاز است، کاراکترهای مشکل‌ساز حذف می‌شوند)."""
    cleaned = "".join(ch for ch in (text or "").strip() if ch.isalnum() or ch in " -_.،")
    cleaned = cleaned.replace(" ", "-").strip("-_.") or "run"
    return cleaned[:60]


def default_path(prefix: str, fmt: str) -> Path:
    config.ensure_dirs()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return config.EXPORT_DIR / f"{safe_filename(prefix)}-{stamp}.{fmt}"
