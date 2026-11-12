"""آزمون هم‌ارزی جست‌وجو: SQLite (محلی) در برابر انتظارهای commit‌شده.

فیکسچر (`tests/fixtures/cloud_parity/`) هشت لید قطعی از `site/data/sample.json` است با
شناسه‌های ثابت، و همان ردیف‌ها در Supabase هم بارگذاری می‌شوند. این تست سمتِ SQLite را
بدون شبکه می‌سنجد تا اگر منطق جست‌وجو/امتیازدهی عوض شد، فوراً دیده شود؛ سمتِ Postgres
را `scripts/cloud_parity.py --live` می‌سنجد (نیاز به SUPABASE_URL دارد).

اجرا:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="leadgen-parity-test-"))
os.environ["LEADGEN_DATA_DIR"] = str(_TMP)

FIXTURE = ROOT / "tests" / "fixtures" / "cloud_parity"
EXPECTED = FIXTURE / "expected.json"


def _load_build_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("parity_build", FIXTURE / "build.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["parity_build"] = module
    spec.loader.exec_module(module)
    return module


class ParityFixtureTests(unittest.TestCase):
    """فیکسچر باید قطعی و بازتولیدپذیر باشد."""

    @classmethod
    def setUpClass(cls):
        if not EXPECTED.exists():
            raise unittest.SkipTest("expected.json ساخته نشده است")
        cls.build = _load_build_module()
        cls.expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        cls.leads = cls.build.build_leads()
        cls.build.seed_sqlite(cls.leads, _TMP)
        cls.title_of = {lead["id"]: lead["title"] for lead in cls.leads}

    def test_fixture_is_deterministic(self):
        again = self.build.build_leads()
        self.assertEqual([lead["id"] for lead in again], [lead["id"] for lead in self.leads])
        self.assertEqual([lead["score"] for lead in again],
                         [lead["score"] for lead in self.leads])
        self.assertEqual(len(self.leads), 8)
        self.assertEqual({lead["segment"] for lead in self.leads}, {"hot", "warm", "cold"})

    def test_ids_match_committed_expectations(self):
        self.assertEqual(self.expected["_fixture"]["ids"], [lead["id"] for lead in self.leads])

    def test_contact_flags_are_computed(self):
        """پرچم‌های تماس باید با محتوای ردیف هم‌خوان باشند (نه صفرِ پیش‌فرض)."""
        for lead in self.leads:
            self.assertEqual(lead["has_phone"], 1 if (lead.get("phone") or "").strip() else 0)
            self.assertEqual(lead["has_email"],
                             1 if (lead.get("email_primary") or lead.get("emails") or "").strip()
                             else 0)
            self.assertEqual(lead["has_website"], 1 if (lead.get("website") or "").strip() else 0)
            self.assertEqual(lead["has_social"],
                             1 if any((lead.get(k) or "").strip()
                                      for k in ("instagram", "facebook", "linkedin")) else 0)

    def test_every_scenario_matches_expectation(self):
        from leadgen import storage
        checked = 0
        for name, want in self.expected.items():
            if name == "_fixture":
                continue
            with self.subTest(scenario=name):
                params = self.build.scenario_params(name)
                result = storage.search_leads(limit=params.pop("limit", 50),
                                              offset=params.pop("offset", 0), **params)
                facets = result["facets"]
                self.assertEqual(result["total"], want["total"])
                self.assertEqual(result["returned"], want["returned"])
                self.assertEqual([lead["id"] for lead in result["leads"]], want["ids"])
                self.assertEqual(facets["segment"], want["segments"])
                self.assertEqual(facets["contact"], want["contact"])
                self.assertEqual((facets["score"] or {}).get("min"), want["score_min"])
                self.assertEqual((facets["score"] or {}).get("max"), want["score_max"])
                distances = [lead.get("distance_m") for lead in result["leads"]][:6]
                self.assertEqual(distances, want["distances"])
                checked += 1
        self.assertEqual(checked, len(self.build.SCENARIOS))

    def test_geo_radius_returns_expected_distances(self):
        from leadgen import storage
        lat, lon = self.build.CENTER
        result = storage.search_leads(lat=lat, lon=lon, radius_m=2000)
        self.assertEqual([lead["id"] for lead in result["leads"]],
                         self.expected["geo_2km"]["ids"])
        for lead in result["leads"]:
            self.assertLessEqual(lead["distance_m"], 2000)
            self.assertTrue(lead["latitude"] and lead["longitude"])   # لوکیشن دقیق
        self.assertEqual(result["geo"], {"lat": lat, "lon": lon, "radius_m": 2000})

    def test_insert_sql_covers_every_lead_and_is_idempotent(self):
        sql = (FIXTURE / "insert.sql").read_text(encoding="utf-8")
        for lead in self.leads:
            self.assertIn(lead["id"], sql)
            self.assertIn(lead["title"].replace("'", "''"), sql)
        self.assertIn("on conflict (id) do update", sql)
        self.assertIn("insert into public.runs", sql)
        self.assertIn(f"'{self.build.RUN_ID}'", sql)

    def test_cloud_parity_script_is_available(self):
        script = ROOT / "scripts" / "cloud_parity.py"
        self.assertTrue(script.exists())
        text = script.read_text(encoding="utf-8")
        for flag in ("--live", "--apply", "--cleanup", "--rebuild"):
            self.assertIn(flag, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
