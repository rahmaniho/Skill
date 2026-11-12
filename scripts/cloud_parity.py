#!/usr/bin/env python3
"""سنجش هم‌ارزی جست‌وجو بین SQLite (محلی) و Postgres/Supabase (ابر).

چرا؟ اپِ روی Vercel داده را از Supabase می‌خواند و داشبورد محلی از SQLite. اگر
`search_leads` در این دو فرق کند، کاربر دو نتیجهٔ متفاوت می‌بیند. این اسکریپت هر دو را
با **یک فیکسچر یکسان** می‌سنجد و تفاوت‌ها را گزارش می‌کند.

حالت‌ها:
    python3 scripts/cloud_parity.py               # آفلاین: SQLite در برابر انتظارهای
                                                  # commit‌شده (مناسب CI، بدون شبکه)
    python3 scripts/cloud_parity.py --live        # زنده: SQLite در برابر Supabase واقعی
    python3 scripts/cloud_parity.py --live --apply    # اول فیکسچر را در ابر بنویس، بعد بسنج
    python3 scripts/cloud_parity.py --live --cleanup  # فیکسچر را از ابر پاک کن
    python3 scripts/cloud_parity.py --rebuild     # انتظارها را از نو بساز و commit کن

پیش‌نیاز حالت زنده: `SUPABASE_URL` و `SUPABASE_SERVICE_KEY` (در `.env` یا محیط).
توجه: `--apply` از راه همان RPCهایی می‌نویسد که اپ می‌نویسد (`save_run`, `save_leads`)،
پس مسیر نوشتن ابری را هم آزمون می‌کند.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "cloud_parity"
BUILD_PY = FIXTURE_DIR / "build.py"
EXPECTED_JSON = FIXTURE_DIR / "expected.json"
DATA_DIR = Path(os.environ.get("PARITY_DATA_DIR", "/tmp/leadgen-parity"))

GREEN, RED, YELLOW, DIM, RESET = "\033[1;32m", "\033[1;31m", "\033[1;33m", "\033[2m", "\033[0m"


def _load_build_module():
    spec = importlib.util.spec_from_file_location("parity_build", BUILD_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_sqlite_side(build_mod) -> tuple[dict, list[dict]]:
    """فیکسچر را در SQLite موقت می‌سازد و نتایج همهٔ سناریوها را می‌گیرد."""
    leads = build_mod.build_leads()
    build_mod.seed_sqlite(leads, DATA_DIR)
    return build_mod.build_expectations(leads), leads


def summarize(result: dict) -> dict:
    """چکیدهٔ قابل‌مقایسه (همان کلیدهایی که فیکسچر ذخیره می‌کند)."""
    facets = result.get("facets") or {}
    return {"total": result.get("total"), "returned": result.get("returned"),
            "ids": [lead["id"] for lead in result.get("leads", [])],
            "segments": facets.get("segment") or {},
            "contact": facets.get("contact") or {},
            "score_min": (facets.get("score") or {}).get("min"),
            "score_max": (facets.get("score") or {}).get("max"),
            "distances": [lead.get("distance_m") for lead in result.get("leads", [])][:6]}


def cloud_side(build_mod) -> dict:
    """همان سناریوها را از Supabase می‌گیرد (از راه `pgstore.search_leads`)."""
    from leadgen import cloud, config, pgstore
    if not (config.env("SUPABASE_URL") and config.env("SUPABASE_SERVICE_KEY")
            and config.env("SUPABASE_ANON_KEY")):
        raise SystemExit(
            "❌ برای حالت زنده، SUPABASE_URL و SUPABASE_SERVICE_KEY لازم است\n"
            "   (Project Settings → API). یا بدون --live اجرا کنید.")
    if not cloud.available():
        raise SystemExit("❌ کلاینت Supabase ساخته نشد — تنظیمات را بررسی کنید.")
    pgstore.init_db()
    out = {}
    for name in build_mod.SCENARIOS:
        params = build_mod.scenario_params(name)
        result = pgstore.search_leads(limit=params.pop("limit", 50),
                                      offset=params.pop("offset", 0), **params)
        out[name] = summarize(result)
    return out


def apply_fixture(build_mod, leads: list[dict]) -> None:
    """فیکسچر را در ابر می‌نویسد (همان مسیر نوشتن اپ)."""
    from leadgen import pgstore
    pgstore.init_db()
    pgstore.create_run(dict(build_mod.RUN))
    written = pgstore.save_leads(build_mod.RUN_ID, [dict(lead) for lead in leads])
    print(f"✔ فیکسچر در ابر نوشته شد: run «{build_mod.RUN_ID}» + {written} لید")


def cleanup_fixture(build_mod) -> None:
    from leadgen import pgstore
    pgstore.init_db()
    ok = pgstore.delete_run(build_mod.RUN_ID)
    print(f"✔ فیکسچر از ابر پاک شد ({build_mod.RUN_ID}): {'موفق' if ok else 'ناموفق/پیدا نشد'}")


def diff(name: str, left: dict, right: dict, *, left_label: str, right_label: str,
         ignore_title_order: bool = False) -> list[str]:
    problems = []
    for key in ("total", "returned", "ids", "segments", "contact", "score_min", "score_max",
                "distances"):
        a, b = left.get(key), right.get(key)
        if a == b:
            continue
        if key == "ids" and ignore_title_order and sorted(a or []) == sorted(b or []):
            problems.append(f"{DIM}{name}: ترتیب «title» متفاوت است (مجموعه یکی است) — "
                            f"collation فارسی در Postgres ≠ ترتیب بایتی SQLite{RESET}")
            continue
        problems.append(f"{RED}{name}.{key}: {left_label}={a!r} ≠ {right_label}={b!r}{RESET}")
    return problems


def run_offline(build_mod) -> int:
    print(f"▸ حالت آفلاین: SQLite در برابر {EXPECTED_JSON.relative_to(ROOT)}")
    if not EXPECTED_JSON.exists():
        raise SystemExit("❌ expected.json پیدا نشد — اول build.py را اجرا کنید.")
    expected = json.loads(EXPECTED_JSON.read_text(encoding="utf-8"))
    actual, _ = build_sqlite_side(build_mod)
    problems = []
    for name, values in expected.items():
        if name == "_fixture":
            continue
        problems += diff(name, values, actual.get(name, {}),
                         left_label="انتظار", right_label="SQLite")
    fixture_meta = expected.get("_fixture") or {}
    if fixture_meta.get("ids") != actual.get("_fixture", {}).get("ids"):
        problems.append(f"{RED}_fixture.ids: شناسه‌ها تغییر کرده‌اند — build.py را دوباره "
                        f"اجرا و نتیجه را commit کنید{RESET}")
    if problems:
        print("\n".join(problems))
        print(f"\n{RED}✖ {len(problems)} اختلاف{RESET}")
        return 1
    print(f"{GREEN}✔ همهٔ {len(expected) - 1} سناریو مطابق انتظارِ commit‌شده است{RESET}")
    return 0


def run_live(build_mod, apply_first: bool) -> int:
    from leadgen import pgstore
    print("▸ حالت زنده: SQLite محلی در برابر Supabase")
    sqlite_side, leads = build_sqlite_side(build_mod)
    if apply_first:
        apply_fixture(build_mod, leads)
    cloud = cloud_side(build_mod)
    problems = []
    for name in build_mod.SCENARIOS:
        problems += diff(name, sqlite_side.get(name, {}), cloud.get(name, {}),
                         left_label="SQLite", right_label="Postgres",
                         ignore_title_order=(name == "sort_title"))
    print(f"  {DIM}پایگاه ابری: {len(cloud)} سناریو سنجیده شد{RESET}")
    if problems:
        print("\n".join(problems))
        hard = [p for p in problems if p.startswith(RED)]
        if hard:
            print(f"\n{RED}✖ {len(hard)} اختلاف واقعی{RESET}")
            return 1
    print(f"{GREEN}✔ هم‌ارزی تأیید شد (Postgres == SQLite){RESET}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="سنجش هم‌ارزی SQLite ↔ Supabase")
    parser.add_argument("--live", action="store_true", help="مقایسه با Supabase واقعی")
    parser.add_argument("--apply", action="store_true", help="اول فیکسچر را در ابر بنویس")
    parser.add_argument("--cleanup", action="store_true", help="فیکسچر را از ابر پاک کن")
    parser.add_argument("--rebuild", action="store_true",
                        help="انتظارها را از نو بساز (وقتی عمدی تغییر کرده‌اند)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    build_mod = _load_build_module()

    if args.cleanup:
        cleanup_fixture(build_mod)
        return 0

    if args.rebuild:
        leads = build_mod.build_leads()
        build_mod.seed_sqlite(leads, DATA_DIR)
        expectations = build_mod.build_expectations(leads)
        EXPECTED_JSON.write_text(json.dumps(expectations, ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")
        (FIXTURE_DIR / "insert.sql").write_text(build_mod.build_insert_sql(leads),
                                                encoding="utf-8")
        print(f"{GREEN}✔ انتظارها و insert.sql از نو ساخته و در {FIXTURE_DIR} نوشته شدند{RESET}")
        print(f"{YELLOW}  فراموش نکنید: git add tests/fixtures/cloud_parity && git commit{RESET}")
        return 0

    if args.live:
        return run_live(build_mod, args.apply)
    return run_offline(build_mod)


if __name__ == "__main__":
    raise SystemExit(main())
