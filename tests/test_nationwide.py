"""پوشش سراسری: همهٔ استان‌ها و ۱٬۳۰۰+ شهر ایران باید قابل انتخاب و جست‌وجو باشند."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from leadgen import geo, regions, server

DATA = Path(geo.__file__).with_name("data") / "iran_cities.json"

# شهرهای کوچک/کم‌شناخته‌شده‌ای که پیش‌تر در جدول دستی نبودند
SMALL_TOWNS = {
    "evaz": "اوز",           # فارس، ~۲۰ هزار نفر
    "siahkal": "سیاهکل",     # گیلان
    "ramsar": "رامسر",       # مازندران
    "qaimshahr": "قائم‌شهر",  # مازندران (نام ویکی‌داده خراب بود)
    "kangan": "کنگان",       # بوشهر
}


class NationwideDataTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(DATA.read_text(encoding="utf-8"))
        cls.rows = cls.payload["cities"]

    def test_dataset_is_complete_and_licensed(self):
        self.assertGreaterEqual(len(self.rows), 1200)
        meta = self.payload["meta"]
        self.assertEqual(meta["count"], len(self.rows))
        self.assertEqual(meta["provinces"], 31)
        self.assertEqual(meta["license"], "CC0-1.0")
        self.assertEqual(meta["curated_missing"], [])

    def test_every_iranian_city_is_in_geo_and_one_province(self):
        iranian = [key for key, row in geo.CITIES.items() if row[2] == "IR"]
        self.assertGreaterEqual(len(iranian), 1200)
        owners: dict[str, list[str]] = {}
        for pid, province in regions.PROVINCES.items():
            for key in province["cities"]:
                owners.setdefault(key, []).append(pid)
        doubled = {key: pids for key, pids in owners.items() if len(pids) > 1}
        self.assertEqual(doubled, {}, f"شهر در چند استان: {doubled}")
        self.assertEqual([key for key in iranian if key not in owners], [],
                         "شهرهای ایرانیِ بدون استان")

    def test_all_provinces_have_many_cities(self):
        for pid, province in regions.PROVINCES.items():
            self.assertGreaterEqual(len(province["cities"]), 5, f"استان کم‌شهر: {pid}")

    def test_cities_have_persian_names(self):
        for key in (row["k"] for row in self.rows):
            aliases = geo.ALIASES.get(key) or []
            self.assertTrue(any(any("\u0600" <= ch <= "\u06FF" for ch in alias) for alias in aliases),
                            f"بدون نام فارسی: {key}")

    def test_small_towns_are_searchable(self):
        for key, fa in SMALL_TOWNS.items():
            self.assertEqual(geo.offline_lookup(fa), key)
            lat, lon, country, bbox = geo.CITIES[key]
            self.assertEqual(country, "IR")
            self.assertTrue(bbox[0] <= lat <= bbox[1] and bbox[2] <= lon <= bbox[3])
            result = regions.resolve(city=fa)
            self.assertIsNotNone(result["lat"], f"resolve نشد: {fa}")
            self.assertEqual(result["city_key"], key)
            self.assertTrue(result["province"], f"استان برنگشت: {fa}")

    def test_geocode_works_offline_for_unknown_small_town(self):
        lat, lon, bbox, key = geo.geocode("اوز", with_bbox=True, allow_network=False)
        self.assertEqual(key, "evaz")
        self.assertAlmostEqual(float(lat), 27.76, places=1)
        self.assertEqual(len(bbox), 4)

    def test_unknown_place_still_fails_offline(self):
        self.assertEqual(geo.geocode("جایی که وجود ندارد", allow_network=False),
                         (None, None, None, None))

    def test_hierarchy_payload_covers_country(self):
        tree = regions.hierarchy()
        self.assertEqual(len(tree["provinces"]), 31)
        total = sum(len(p["cities"]) for p in tree["provinces"])
        self.assertGreaterEqual(total, 1200)
        for province in tree["provinces"]:
            self.assertEqual(province["cities"][0]["fa"], province["capital_fa"])

    def test_city_search_ranks_exact_match_first(self):
        hits = server._search_cities("اوز", 5)
        self.assertTrue(hits, "جست‌وجوی «اوز» نتیجه نداد")
        self.assertEqual(hits[0]["id"], "evaz")
        self.assertEqual(hits[0]["province"], "fars")
        self.assertEqual(hits[0]["province_fa"], "فارس")

    def test_city_search_disambiguates_duplicate_names(self):
        hits = server._search_cities("مهاباد", 5)
        self.assertGreaterEqual(len(hits), 2)
        self.assertEqual(hits[0]["province"], "west-azarbaijan")   # پرجمعیت‌تر اول

    def test_cities_brief_is_light_and_labelled(self):
        brief = server._cities_brief(geo.known_cities()[:5])
        self.assertEqual(len(brief), 5)
        for row in brief:
            self.assertIn("province_fa", row)
            self.assertNotIn("neighborhoods", row)


if __name__ == "__main__":
    unittest.main()
