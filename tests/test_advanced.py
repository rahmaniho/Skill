"""تست قابلیت‌های پیشرفته: جاروی تمامی مشاغل، لوکیشن دقیق، پیش‌تنظیم ستون‌ها و سایت Pages.

اجرا:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="leadgen-adv-"))
os.environ["LEADGEN_DATA_DIR"] = str(_TMP)
os.environ["ENGINE_MODE"] = "demo"

from leadgen import (campaign, cli, exports, niches, pipeline, scoring,  # noqa: E402
                     storage)

SITE = ROOT / "site"


def fast_sweep(**over) -> pipeline.ScrapeResult:
    """یک جاروی کوچک و سریع با موتور دمو (برای تست‌های پایان‌به‌پایان)."""
    base = dict(all_niches=True, city_query="تهران", lang="fa", depth=2,
                profile="web_design", socials=True, engine_mode="demo",
                max_niches=4, keywords_per_niche=1, keywords_per_job=2,
                limit=24, cols="contact", export_fmt="csv")
    base.update(over)
    return pipeline.run_scrape(pipeline.ScrapeRequest(**base))


# ─────────────────────────────────────────────────────────────────────────────
class SweepPlanTests(unittest.TestCase):
    """«جست‌وجوی تمامی مشاغل» باید واقعاً چندصنفی و چنددسته‌ای باشد."""

    def test_plan_rotates_across_groups(self):
        plan = niches.sweep_plan(limit=6)
        self.assertEqual(len(plan), 6)
        self.assertEqual(len({n.group for n in plan}), 6, "هر صنف باید از یک دسته متفاوت بیاید")
        self.assertEqual(len({n.id for n in plan}), 6)

    def test_group_filter_limits_the_pool(self):
        plan = niches.sweep_plan(groups=["سلامت و پزشکی"], limit=8)
        self.assertTrue(plan)
        self.assertTrue(all(n.group == "سلامت و پزشکی" for n in plan))

    def test_multiple_groups_stay_balanced(self):
        plan = niches.sweep_plan(groups=["سلامت و پزشکی", "غذا و نوشیدنی"], limit=6)
        counts = {g: sum(1 for n in plan if n.group == g) for g in ("سلامت و پزشکی", "غذا و نوشیدنی")}
        self.assertEqual(sum(counts.values()), 6)
        self.assertLessEqual(abs(counts["سلامت و پزشکی"] - counts["غذا و نوشیدنی"]), 1)

    def test_exclude_ids_are_skipped(self):
        first = niches.sweep_plan(limit=3)
        plan = niches.sweep_plan(limit=3, exclude_ids=[n.id for n in first])
        self.assertFalse({n.id for n in plan} & {n.id for n in first})

    def test_zero_limit_returns_whole_catalog(self):
        self.assertEqual(len(niches.sweep_plan(limit=0)), len(niches.CATALOG))

    def test_unknown_group_falls_back_to_catalog(self):
        plan = niches.sweep_plan(groups=["دسته‌ای که وجود ندارد"], limit=3)
        self.assertEqual(len(plan), 3)


class ColumnPresetTests(unittest.TestCase):
    """چهار پیش‌تنظیم ستون: lead | contact | location | all."""

    def test_presets_exist_with_persian_labels(self):
        self.assertEqual(list(exports.COLUMN_PRESETS), ["lead", "contact", "location", "all"])
        for cfg in exports.COLUMN_PRESETS.values():
            self.assertTrue(cfg["fa"])
            self.assertTrue(all(fa for _, fa in cfg["columns"]))

    def test_contact_preset_has_everything_the_user_asked_for(self):
        keys = [k for k, _ in exports.COLUMN_PRESETS["contact"]["columns"]]
        for needed in ("title", "address", "phone", "email_primary", "website",
                       "instagram", "facebook", "linkedin",
                       "location", "latitude", "longitude", "maps_link", "plus_code"):
            self.assertIn(needed, keys, needed)

    def test_location_preset_is_light_and_place_first(self):
        cols = exports.COLUMN_PRESETS["location"]["columns"]
        self.assertLessEqual(len(cols), 15)
        self.assertIn("location", [k for k, _ in cols])
        self.assertIn("plus_code", [k for k, _ in cols])
        self.assertIn("maps_link", [k for k, _ in cols])

    def test_columns_for_resolution(self):
        self.assertEqual(exports.columns_for(None), exports.EXPORT_COLUMNS)
        self.assertEqual(exports.columns_for("lead"), exports.COLUMN_PRESETS["lead"]["columns"])
        self.assertEqual(exports.columns_for("all"), exports.FULL_COLUMNS)
        self.assertEqual(exports.columns_for("all"), exports.columns_for("lead", full=True))
        self.assertEqual(exports.columns_for("نامعتبر"), exports.EXPORT_COLUMNS)  # بازگشت امن

    def test_csv_uses_the_selected_preset(self):
        rows = [scoring.score_all([_row()], profile="web_design")[0]]
        raw = exports.csv_bytes(rows, preset="location").decode("utf-8-sig")
        parsed = list(csv.reader(io.StringIO(raw)))
        header = parsed[0]
        self.assertIn("لوکیشن", " ".join(header))
        self.assertEqual(len(header), len(exports.COLUMN_PRESETS["location"]["columns"]))
        self.assertEqual(len(parsed[1]), len(header))

        contact = exports.csv_bytes(rows, preset="contact").decode("utf-8-sig").splitlines()[0]
        self.assertIn("لینک نقشه گوگل", contact)
        self.assertIn("اینستاگرام", contact)

    def test_xlsx_workbook_honours_preset(self):
        rows = scoring.score_all([_row()], profile="web_design")
        sheets = exports.build_workbook(rows, None, None, None, preset="location")
        path = exports.write_xlsx(_TMP / "preset.xlsx", sheets)
        with zipfile.ZipFile(path) as zf:
            self.assertIsNone(zf.testzip())
            sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("لوکیشن", sheet)          # رشته‌ها inline نوشته می‌شوند
        self.assertIn("لینک نقشه", sheet)

    def test_export_run_writes_the_preset_file(self):
        rows = scoring.score_all([_row()], profile="web_design")
        path = exports.export_run(_TMP / "run.csv", rows, fmt="csv", preset="contact")
        self.assertTrue(path.exists())
        self.assertIn("لینک نقشه", path.read_text(encoding="utf-8-sig").splitlines()[0])


def _row(**over) -> dict:
    """یک ردیف خام شبیه خروجی موتور (۳۴ ستون کلیدی)."""
    base = {
        "input_id": "دندانپزشکی در تهران",
        "link": "https://www.google.com/maps/place/?q=place_id:ChIJTEST",
        "title": "کلینیک دندانپزشکی نگین", "category": "دندانپزشکی",
        "address": "خیابان ولیعصر، زعفرانیه، تهران",
        "phone": "+98 21 2222 3333", "emails": "info@negin-dental.ir",
        "website": "https://negin-dental.ir", "plus_code": "8Q4F+22",
        "review_count": "180", "review_rating": "4.4",
        "latitude": "35.700123", "longitude": "51.400456",
        "place_id": "ChIJTEST", "cid": "12345", "status": "ok",
        "instagram": "negindental", "facebook": "", "linkedin": "",
    }
    base.update(over)
    return base


# ─────────────────────────────────────────────────────────────────────────────
class SweepPipelineTests(unittest.TestCase):
    """پایپ‌لاین کامل حالت «تمامی مشاغل»."""

    @classmethod
    def setUpClass(cls):
        cls.res = fast_sweep()

    def test_run_returns_requested_limit_after_scoring(self):
        self.assertEqual(len(self.res.rows), 24)
        scores = [int(r["score"]) for r in self.res.rows]
        self.assertEqual(scores, sorted(scores, reverse=True), "خروجی باید بر اساس امتیاز مرتب باشد")

    def test_rows_come_from_multiple_niches_and_are_tagged(self):
        tags = {r.get("niche_fa") for r in self.res.rows}
        self.assertGreaterEqual(len(tags), 3, f"تنوع صنفی کم است: {tags}")
        for row in self.res.rows:
            self.assertTrue(row.get("niche_fa"))
            self.assertTrue(row.get("niche_id"))
        # تگ هر ردیف باید با دستهٔ گوگل یا نام کسب‌وکارش هم‌خوان باشد
        mismatched = [r for r in self.res.rows
                      if r["category"] and r["category"] not in r["niche_fa"]
                      and r["niche_fa"] not in r["category"]]
        self.assertEqual(mismatched, [], f"صنف منبع با دستهٔ ردیف نمی‌خواند: {mismatched[:2]}")

    def test_sweep_runs_multiple_jobs_sequentially(self):
        self.assertGreaterEqual(self.res.stats["jobs"], 2)
        self.assertEqual(len(self.res.stats["job_ids"]), self.res.stats["jobs"])
        self.assertEqual(len(self.res.stats["sweep_niches"]), 4)

    def test_sweep_warns_but_does_not_block(self):
        joined = " ".join(self.res.warnings)
        self.assertTrue(joined, "اجرای جارو باید هشدار اندازه/پروکسی بدهد")
        self.assertTrue(any("پروکسی" in w or "job" in w or "کنار" in w for w in self.res.warnings))

    def test_grid_is_disabled_in_sweep_mode(self):
        res = fast_sweep(grid=3, limit=10)
        self.assertTrue(any("شبکه" in w for w in res.warnings))

    def test_export_uses_all_trades_slug_and_contact_sidecar(self):
        names = [Path(p).name for p in self.res.export_paths]
        self.assertTrue(any(n.startswith("all-trades-tehran") for n in names), names)
        sidecar = [p for p in self.res.export_paths if p.endswith("-leads.csv") or "-leads-" in p]
        self.assertTrue(self.res.export_paths)
        main_csv = next(Path(p) for p in self.res.export_paths if p.endswith(".csv") and "-leads-" not in p)
        header = main_csv.read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertIn("لینک نقشه گوگل", header)     # پیش‌تنظیم contact
        self.assertIn("صنف منبع", header)
        self.assertTrue(sidecar or "-campaign-" in " ".join(names))

    def test_leads_are_persisted_with_location_and_niche(self):
        saved = storage.load_leads(self.res.run_id, limit=5)
        self.assertTrue(saved)
        for lead in saved:
            self.assertIsInstance(lead["lat"], float)
            self.assertIsInstance(lead["lon"], float)
            self.assertTrue(lead["has_location"])
            self.assertTrue(lead["location"])
            self.assertTrue(lead["maps_link"].startswith("https://"))
            self.assertTrue(lead["niche_fa"])
            self.assertTrue(lead["score_factors"], "breakdown امتیاز باید ذخیره شود")

    def test_group_filter_narrows_the_sweep(self):
        res = fast_sweep(niche_groups=["سلامت و پزشکی"], max_niches=3, limit=12)
        groups = {niches.get(r["niche_id"]).group for r in res.rows}
        self.assertEqual(groups, {"سلامت و پزشکی"})


# ─────────────────────────────────────────────────────────────────────────────
class CampaignAndCliTests(unittest.TestCase):
    def test_campaign_uses_exact_location_and_business_name(self):
        res = fast_sweep(limit=8, max_niches=2)
        lead = res.rows[0]
        msg = campaign.generate(lead, "email", campaign.Sender(name="تست"), None)
        self.assertIn(lead["title"], msg["context"]["business"])
        self.assertTrue(msg["text"])
        self.assertEqual(len(msg["sequence"]), 3)

    def test_cli_sweep_run_exits_zero(self):
        code = cli.main(["run", "--all-niches", "--city", "تهران", "--lang", "fa",
                         "--engine", "demo", "--max-niches", "2", "--keywords-per-niche", "1",
                         "--keywords-per-job", "2", "--limit", "6", "--cols", "contact",
                         "--export", "csv", "--quiet", "--depth", "2"])
        self.assertEqual(code, 0)

    def test_cli_rejects_run_without_niche_or_sweep(self):
        code = cli.main(["run", "--city", "تهران", "--engine", "demo", "--quiet"])
        self.assertNotEqual(code, 0)

    def test_cli_export_honours_cols(self):
        res = fast_sweep(limit=6, max_niches=2, export_fmt="json")
        out = _TMP / "cli-location.csv"
        code = cli.main(["export", res.run_id, "--fmt", "csv", "--cols", "location",
                         "--out", str(out)])
        self.assertEqual(code, 0)
        header = out.read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertIn("لوکیشن", header)
        self.assertNotIn("شکاف", header)


# ─────────────────────────────────────────────────────────────────────────────
class SiteSampleTests(unittest.TestCase):
    """سایت GitHub Pages باید همیشه دادهٔ سالم و هم‌اندازهٔ کد داشته باشد."""

    @classmethod
    def setUpClass(cls):
        cls.path = SITE / "data" / "sample.json"
        cls.data = json.loads(cls.path.read_text(encoding="utf-8")) if cls.path.exists() else {}

    def test_sample_file_exists_and_is_synthetic(self):
        self.assertTrue(self.path.exists(), "site/data/sample.json ساخته نشده — scripts/build_site_sample.py را اجرا کنید")
        self.assertIn("مصنوعی", self.data["disclaimer"])
        self.assertLess(self.path.stat().st_size, 1_500_000, "فایل نمونه برای یک صفحه ثابت زیادی سنگین است")

    def test_sample_leads_carry_every_required_field(self):
        leads = self.data["leads"]
        self.assertGreaterEqual(len(leads), 60)
        for lead in leads:
            self.assertTrue(lead["title"])
            self.assertTrue(lead["niche_fa"])
            self.assertTrue(lead["maps_link"].startswith("https://"))
            self.assertTrue(lead["location"])
            self.assertIsInstance(lead["lat"], float)
            self.assertIsInstance(lead["lon"], float)
            self.assertIn(lead["segment"], ("hot", "warm", "cold"))
            self.assertTrue(0 <= int(lead["score"]) <= 100)

    def test_sample_distribution_is_not_only_hot_leads(self):
        segments = self.data["analytics"]["segments"]
        self.assertLess(segments["hot"]["percent"], 80,
                        "نمونه باید توزیع واقعی داشته باشد، نه فقط بهترین لیدها")

    def test_sample_columns_match_the_backend_presets(self):
        for preset, cols in self.data["columns"].items():
            backend = [[k, fa] for k, fa in exports.COLUMN_PRESETS[preset]["columns"]]
            self.assertEqual(cols, backend, preset)

    def test_sample_niches_are_multi_group(self):
        groups = {niches.find_niche(n).group for n in self.data["meta"]["niches"]}
        self.assertGreaterEqual(len(groups), 3)

    def test_site_assets_and_workflow_exist(self):
        for rel in ("index.html", "styles.css", "app.js", "favicon.svg"):
            self.assertTrue((SITE / rel).exists(), rel)
        self.assertTrue((ROOT / ".github" / "workflows" / "pages.yml").exists())
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        self.assertIn("path: site", workflow)
        self.assertIn("deploy-pages", workflow)

    def test_site_html_references_only_local_assets(self):
        """سایت روی Pages با مسیر نسبی کار می‌کند؛ هیچ منبعی به سرور محلی گره نخورده است."""
        html = (SITE / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="styles.css"', html)
        self.assertIn('src="app.js"', html)
        allowed = ("github.com", "rahmaniho.github.io")
        for url in re.findall(r'(?:href|src)="([^"#][^"]*)"', html):
            if url.startswith(("http://", "https://")):
                self.assertTrue(any(h in url for h in allowed), f"منبع بیرونی غیرمجاز: {url}")
            else:
                self.assertFalse(url.startswith("/"), f"مسیر مطلق روی Pages می‌شکند: {url}")
        self.assertIn('fetch("data/sample.json"', (SITE / "app.js").read_text(encoding="utf-8"))

    def test_csv_shape_matches_preset_contract(self):
        """همان قراردادی که سایت در مرورگر می‌سازد، اینجا با پایتون بررسی می‌شود."""
        leads = self.data["leads"][:5]
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([fa for _, fa in exports.COLUMN_PRESETS["contact"]["columns"]])
        for lead in leads:
            writer.writerow([lead.get(key, "") for key, _ in exports.COLUMN_PRESETS["contact"]["columns"]])
        rows = list(csv.reader(io.StringIO(buf.getvalue())))
        self.assertEqual(len(rows[0]), 28)
        self.assertEqual(len(rows), 6)


if __name__ == "__main__":
    unittest.main()
