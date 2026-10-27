"""تست‌های «نسخهٔ جمع‌وجور»: جغرافیای منطقه‌ای، جست‌وجوی قوی و مسیرهای API.

پوشش:
  • سلسله‌مراتب استان ← شهر ← منطقه ← خیابان (leadgen.regions)
  • تشخیص منطقه/خیابان از داخل جملهٔ آزاد (فارسی و لاتین)
  • resolve: سطح، شعاع، زوم و برچسب (و اینکه استان فقط بدون شهر به برچسب می‌آید)
  • pipeline.apply_area: نقطه/شعاع/bbox و کلیدواژهٔ دقیق + بی‌اثر بودن فراخوانی دوم
  • storage.search_leads: AND بین توکن‌ها، فیلترهای تماس، شعاع دایره‌ای، facets، صفحه‌بندی
  • مسیرهای HTTP: /api/regions*، /api/search، /api/search/export و پارامترهای تازهٔ parse
  • smart.parse با انتخابگرها و با جملهٔ آزاد

اجرا:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = Path(tempfile.mkdtemp(prefix="leadgen-regions-"))
os.environ["LEADGEN_DATA_DIR"] = str(_TMP)
os.environ["ENGINE_MODE"] = "demo"

from leadgen import geo, pipeline, regions, smart, storage  # noqa: E402
from leadgen import server as server_mod  # noqa: E402


def _find_free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ─────────────────────────────────────────────────────────────────────────────
# ۱) دادهٔ جغرافیایی
# ─────────────────────────────────────────────────────────────────────────────
class RegionDataTestCase(unittest.TestCase):
    def test_every_province_has_capital_city(self):
        tree = regions.hierarchy()
        self.assertEqual(len(tree["provinces"]), 31)
        for province in tree["provinces"]:
            self.assertTrue(province["cities"], f"استان {province['fa']} شهری ندارد")
            self.assertEqual(province["cities"][0]["id"], province["capital"],
                             f"مرکز {province['fa']} باید اولین شهر باشد")
            self.assertEqual(province["cities"][0]["fa"], province["capital_fa"])

    def test_all_provincial_capitals_exist_in_geo(self):
        missing = [p["capital"] for p in regions.PROVINCES.values() if p["capital"] not in geo.CITIES]
        self.assertEqual(missing, [], f"مرکز استان‌ها در geo.CITIES نیست: {missing}")

    def test_cities_have_persian_names(self):
        iranian = [key for key, row in geo.CITIES.items() if row[2] == "IR"]
        self.assertGreaterEqual(len(iranian), 110)
        without_fa = [key for key in iranian
                      if not geo.ALIASES.get(key) or not any(
                          any("\u0600" <= ch <= "\u06FF" for ch in alias)
                          for alias in geo.ALIASES[key])]
        self.assertEqual(without_fa, [], f"شهرهای بدون نام فارسی: {without_fa[:8]}")

    def test_bboxes_are_well_formed(self):
        for key, (lat, lon, _country, bbox) in geo.CITIES.items():
            south, north, west, east = bbox
            self.assertTrue(-90 <= south <= north <= 90, f"bbox {key}: {bbox}")
            self.assertTrue(-180 <= west <= east <= 180, f"bbox {key}: {bbox}")
            self.assertTrue(south <= lat <= north and west <= lon <= east, f"مرکز {key} بیرون bbox")

    def test_districts_and_streets_have_points(self):
        self.assertGreaterEqual(len(regions.DISTRICTS), 10)
        self.assertGreaterEqual(len(regions.STREETS), 8)
        for city, rows in regions.DISTRICTS.items():
            self.assertIn(city, geo.CITIES, f"منطقه‌های شهر ناشناخته: {city}")
            for fa, lat, lon in rows:
                self.assertTrue(fa and -90 <= lat <= 90 and -180 <= lon <= 180, f"{city}/{fa}")
        for city, rows in regions.STREETS.items():
            self.assertIn(city, geo.CITIES, f"خیابان‌های شهر ناشناخته: {city}")
            for fa, lat, lon, district in rows:
                self.assertTrue(fa and -90 <= lat <= 90, f"{city}/{fa}")
                self.assertIsInstance(district, str)

    def test_tehran_is_rich(self):
        self.assertGreaterEqual(len(regions.districts("tehran")), 100)
        self.assertGreaterEqual(len(regions.streets("tehran")), 50)
        self.assertTrue(regions.streets("tehran", "منطقه ۳"))

    def test_city_key_accepts_persian_and_composite_labels(self):
        self.assertEqual(regions.city_key("تهران"), "tehran")
        self.assertEqual(regions.city_key("Tehran"), "tehran")
        self.assertEqual(regions.city_key("تهران، ونک"), "tehran")
        self.assertEqual(regions.city_key("shiraz"), "shiraz")
        self.assertEqual(regions.city_key("جایی که نیست"), "")
        self.assertEqual(regions.city_fa("tehran"), "تهران")


# ─────────────────────────────────────────────────────────────────────────────
# ۲) تشخیص از جمله و resolve
# ─────────────────────────────────────────────────────────────────────────────
class AreaResolutionTestCase(unittest.TestCase):
    def test_detect_area_in_persian_sentence(self):
        hits = regions.detect_area("کافه در چیتگر تهران")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["fa"], "چیتگر")
        self.assertEqual(hits[0]["kind"], "district")
        self.assertEqual(hits[0]["city"], "tehran")

    def test_detect_area_prefers_explicit_street(self):
        hits = {h["kind"]: h["fa"] for h in regions.detect_area("رستوران در خیابان چیتگر")}
        self.assertEqual(hits.get("street"), "خیابان چیتگر")

    def test_detect_area_neighborhood_beats_same_name_street(self):
        # «ونک» بدون پیشوند = منطقه؛ «خیابان ونک» = خیابان
        self.assertEqual(regions.best_area("کافه در ونک")["kind"], "district")
        self.assertEqual(regions.best_area("کافه در خیابان ونک")["kind"], "street")

    def test_detect_area_latin_names(self):
        hits = regions.detect_area("restaurants in Tehran Vanak")
        self.assertTrue(hits, "نام لاتین منطقه پیدا نشد")
        self.assertEqual(hits[0]["city"], "tehran")
        self.assertTrue(regions.detect_area("cafes in Saadat Abad"))
        self.assertTrue(regions.detect_area("office in Hemmat Highway"))

    def test_find_area_never_confuses_city_with_district(self):
        hit = regions.find_area("ونک", "tehran", level="district")
        self.assertEqual(hit["fa"], "ونک")
        self.assertNotEqual(hit["city"], "vancouver")
        self.assertAlmostEqual(hit["lat"], 35.755, places=2)

    def test_resolve_levels_radius_and_zoom(self):
        cases = [
            ({"city": "tehran"}, "city", 10000, 13),
            ({"city": "tehran", "district": "پونک"}, "district", 2500, 15),
            ({"city": "tehran", "district": "ونک", "street": "خیابان ملاصدرا"}, "street", 900, 16),
            ({"province": "razavi-khorasan"}, "province", 30000, 10),
        ]
        for kwargs, level, radius, zoom in cases:
            area = regions.resolve(**kwargs)
            self.assertEqual(area["level"], level, str(kwargs))
            self.assertEqual(area["radius"], radius, str(kwargs))
            self.assertEqual(area["zoom"], zoom, str(kwargs))
            self.assertTrue(area["lat"] and area["lon"], str(kwargs))

    def test_resolve_labels(self):
        self.assertEqual(regions.resolve(city="tehran", district="ونک",
                                        street="خیابان ملاصدرا")["label"],
                         "تهران، ونک، خیابان ملاصدرا")
        # کلیدواژه از جزء خاص به عام می‌رود
        self.assertEqual(regions.resolve(city="tehran", district="ونک",
                                        street="خیابان ملاصدرا")["keyword_label"],
                         "خیابان ملاصدرا، ونک، تهران")
        # استان فقط وقتی تنها انتخاب باشد در برچسب می‌آید
        self.assertEqual(regions.resolve(province="fars")["label"], "فارس")
        self.assertEqual(regions.resolve(province="fars", city="shiraz")["label"], "شیراز")
        self.assertEqual(regions.resolve(province="fars", city="shiraz",
                                        district="معالی‌آباد")["label"], "شیراز، معالی‌آباد")

    def test_resolve_foreign_city(self):
        area = regions.resolve(city="dubai", district="Deira")
        self.assertEqual(area["level"], "district")
        self.assertAlmostEqual(area["lat"], 25.26, places=1)
        self.assertEqual(regions.resolve(city="dubai", district="Deira", lang="en")["label"],
                         "Dubai, Deira")

    def test_resolve_explicit_coordinates_win(self):
        area = regions.resolve(city="tehran", district="ونک", lat=40.0, lon=-3.0)
        self.assertEqual((area["lat"], area["lon"]), (40.0, -3.0))
        self.assertEqual(area["level"], "district")

    def test_search_areas_suggests(self):
        results = regions.search_areas("Vanak", limit=5)
        self.assertTrue(results)
        self.assertIn("ونک", results[0]["label"])
        self.assertEqual(results[0]["city_fa"], "تهران")
        self.assertTrue(regions.search_areas("معالی"))
        self.assertEqual(regions.search_areas(""), [])


# ─────────────────────────────────────────────────────────────────────────────
# ۳) پایپ‌لاین: اعمال محدوده
# ─────────────────────────────────────────────────────────────────────────────
class ApplyAreaTestCase(unittest.TestCase):
    def _request(self, **kwargs):
        base = {"niche_query": "کافه", "city_query": "تهران", "province": "tehran-province",
                "lang": "fa", "engine_mode": "demo", "grid": 2}
        base.update(kwargs)
        return pipeline.ScrapeRequest.from_dict(base)

    def test_street_level_sets_point_radius_and_bbox(self):
        request = self._request(district="ونک", street="خیابان ملاصدرا")
        info = pipeline.apply_area(request)
        self.assertEqual(info["level"], "street")
        self.assertEqual(request.radius, 900)
        self.assertEqual(request.zoom, 16)
        self.assertEqual(request.grid, 0)          # در سطح خیابان کاشی‌بندی معنا ندارد
        self.assertEqual(len(request.bbox), 4)
        self.assertAlmostEqual(request.lat, "35.750000")
        self.assertEqual(request.area_label, "خیابان ملاصدرا، ونک، تهران")
        self.assertEqual(request.city_query, "تهران، ونک، خیابان ملاصدرا")

    def test_province_level_has_no_tight_bbox(self):
        request = pipeline.ScrapeRequest.from_dict({"niche_query": "کافه", "city_query": "",
                                                    "province": "razavi-khorasan",
                                                    "engine_mode": "demo", "lang": "fa"})
        info = pipeline.apply_area(request)
        self.assertEqual(info["level"], "province")
        self.assertEqual(request.bbox, [])         # وگرنه بقیهٔ شهرهای استان حذف می‌شدند
        self.assertEqual(request.radius, 30000)

    def test_apply_area_is_idempotent(self):
        request = self._request(district="چیتگر")
        first = pipeline.apply_area(request)
        second = pipeline.apply_area(request)
        self.assertEqual(first, second)
        self.assertEqual(second["label"], "چیتگر، تهران")

    def test_run_scrape_uses_area_label_in_keywords(self):
        request = self._request(district="ونک", street="خیابان ملاصدرا", depth=2, limit=6,
                               cols="contact", export_fmt="")
        result = pipeline.run_scrape(request)
        self.assertTrue(result.rows)
        run = storage.get_run(result.run_id)
        self.assertEqual(run["area_level"], "street")
        self.assertEqual(run["area_label"], "خیابان ملاصدرا، ونک، تهران")
        self.assertEqual(run["district"], "ونک")
        self.assertIn("خیابان ملاصدرا، ونک، تهران", run["keywords"][0])
        # نشانی ردیف‌ها باید نام منطقه را داشته باشد، نه تکرار آن
        self.assertIn("چیتگر" if False else "ونک", result.rows[0]["address"])
        self.assertNotIn("ونک، خیابان ملاصدرا، ونک", result.rows[0]["address"])
        storage.delete_run(result.run_id)

    def test_english_keywords_are_latin(self):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "cafe", "city_query": "Dubai", "district": "Deira", "lang": "en",
            "engine_mode": "demo", "depth": 2, "limit": 3, "cols": "contact", "export_fmt": ""})
        result = pipeline.run_scrape(request)
        run = storage.get_run(result.run_id)
        self.assertIn("Deira, Dubai", run["keywords"][0])
        self.assertNotIn("،", run["keywords"][0])
        storage.delete_run(result.run_id)


# ─────────────────────────────────────────────────────────────────────────────
# ۴) جست‌وجوی قوی در داده‌های ذخیره‌شده
# ─────────────────────────────────────────────────────────────────────────────
class SearchLeadsTestCase(unittest.TestCase):
    run_id = ""
    other_run_id = ""

    @classmethod
    def setUpClass(cls):
        storage.init_db()
        cls.run_id = cls._make_run("ونک", "خیابان ملاصدرا", "کافه", "tehran")
        cls.other_run_id = cls._make_run("", "", "دندانپزشکی", "mashhad")

    @classmethod
    def _make_run(cls, district, street, niche_query, city):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": niche_query, "city_query": city, "district": district, "street": street,
            "province": "tehran-province" if city == "tehran" else "",
            "lang": "fa", "engine_mode": "demo", "profile": "web_design", "depth": 2,
            "grid": 0, "socials": True, "emails": True, "cols": "contact", "export_fmt": ""})
        result = pipeline.run_scrape(request)
        return result.run_id

    @classmethod
    def tearDownClass(cls):
        for run_id in (cls.run_id, cls.other_run_id):
            storage.delete_run(run_id)

    def test_search_returns_facets_and_total(self):
        out = storage.search_leads(limit=5)
        self.assertGreater(out["total"], 0)
        self.assertEqual(out["returned"], len(out["leads"]))
        self.assertIn("segment", out["facets"])
        self.assertEqual(out["facets"]["total"], out["total"])
        self.assertGreater(len(out["facets"]["niche"]), 0)

    def test_filter_by_run(self):
        out = storage.search_leads(run_id=self.other_run_id, limit=500)
        self.assertTrue(out["leads"])
        self.assertTrue(all(lead["run_id"] == self.other_run_id for lead in out["leads"]))
        self.assertEqual(out["total"], len(out["leads"]))

    def test_multi_token_query_is_and(self):
        sample = storage.search_leads(limit=1)["leads"][0]
        word = (sample["title"] or "").split()[0]
        self.assertTrue(storage.search_leads(q=word, limit=5)["total"] > 0)
        self.assertEqual(storage.search_leads(q=f"{word} zzqqxx", limit=5)["total"], 0)

    def test_contact_and_quality_filters(self):
        with_email = storage.search_leads(has_email=True, limit=5)
        self.assertTrue(all(lead["email_primary"] or lead["emails"] for lead in with_email["leads"]))
        no_website = storage.search_leads(has_website=False, limit=5)
        self.assertTrue(all(not lead["website"] for lead in no_website["leads"]))
        rated = storage.search_leads(min_rating=4.5, limit=5)
        self.assertTrue(all((lead["rating"] or 0) >= 4.5 for lead in rated["leads"]))
        scored = storage.search_leads(min_score=200, limit=5)
        self.assertEqual(scored["total"], 0)

    def test_geo_radius_filter_is_a_circle(self):
        run = storage.get_run(self.run_id)
        lat, lon = float(run["lat"]), float(run["lon"])
        near = storage.search_leads(lat=lat, lon=lon, radius_m=1200, limit=500)
        far = storage.search_leads(lat=lat, lon=lon, radius_m=1, limit=500)
        self.assertGreater(near["total"], 0)
        self.assertEqual(far["total"], 0)
        self.assertTrue(all(lead["distance_m"] <= 1200 for lead in near["leads"]))
        self.assertEqual(near["facets"]["total"], near["total"])
        self.assertEqual(near["geo"]["radius_m"], 1200)

    def test_sort_and_pagination(self):
        first = storage.search_leads(sort="score", order="desc", limit=3)
        scores = [lead["score"] for lead in first["leads"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        page1 = storage.search_leads(limit=2, offset=0)
        page2 = storage.search_leads(limit=2, offset=2)
        ids1 = {lead["id"] for lead in page1["leads"]}
        ids2 = {lead["id"] for lead in page2["leads"]}
        self.assertFalse(ids1 & ids2)
        self.assertEqual(page1["total"], page2["total"])

    def test_filters_are_reported(self):
        out = storage.search_leads(segment="hot", has_email=True, min_score=10, limit=1)
        self.assertEqual(out["filters"]["segment"], "hot")
        self.assertTrue(out["filters"]["has_email"])
        self.assertEqual(out["filters"]["min_score"], 10)


# ─────────────────────────────────────────────────────────────────────────────
# ۵) مغز هوشمند با انتخابگرها
# ─────────────────────────────────────────────────────────────────────────────
class SmartAreaTestCase(unittest.TestCase):
    def test_explicit_selectors_drive_the_request(self):
        out = smart.parse("", province="tehran-province", city="tehran", district="ونک",
                          street="خیابان ملاصدرا", all_trades=True, size="quick", limit=80)
        self.assertEqual(out["mode"], "sweep")
        self.assertTrue(out["ready"])
        area = out["detected"]["area"]
        self.assertEqual(area["level"], "street")
        self.assertEqual(area["label"], "تهران، ونک، خیابان ملاصدرا")
        self.assertEqual(area["radius"], 900)
        request = out["request"]
        self.assertTrue(request["all_niches"])
        self.assertEqual(request["radius"], 900)
        self.assertEqual(request["grid"], 0)
        self.assertEqual(request["limit"], 80)
        self.assertEqual(request["province"], "tehran-province")
        self.assertEqual(request["district"], "ونک")
        self.assertEqual(request["street"], "خیابان ملاصدرا")
        self.assertEqual(len(request["bbox"]), 4)

    def test_area_detected_from_free_text(self):
        out = smart.parse("دندانپزشکی در چیتگر تهران")
        self.assertEqual(out["mode"], "single")
        self.assertEqual(out["detected"]["niche_fa"], "دندانپزشکی")
        self.assertEqual(out["detected"]["area"]["level"], "district")
        self.assertIn("چیتگر", out["request"]["city_query"])
        self.assertIn("چیتگر", out["keywords"][0])

    def test_niche_selector_beats_text(self):
        out = smart.parse("چیتگر", niche_id="dentist")
        self.assertEqual(out["detected"]["niche_id"], "dentist")
        self.assertEqual(out["detected"]["area"]["district"], "چیتگر")

    def test_all_trades_selector_with_group(self):
        out = smart.parse("همهٔ کسب‌وکارهای غذا و نوشیدنی", city="tehran",
                          all_trades=True, size="quick")
        self.assertEqual(out["mode"], "sweep")
        self.assertIn("غذا و نوشیدنی", out["request"]["niche_groups"])
        self.assertTrue(out["ready"])

    def test_english_query_keeps_latin_area_label(self):
        out = smart.parse("plumbers in Deira Dubai")
        self.assertEqual(out["detected"]["lang"], "en")
        # برچسب نمایشی «کلی ← جزئی» و کلیدواژهٔ جست‌وجو «جزئی ← کلی» است
        self.assertIn("Dubai", out["request"]["city_query"])
        self.assertIn("Deira", out["request"]["city_query"])
        self.assertIn("Deira, Dubai", out["keywords"][0])
        self.assertNotIn("،", out["keywords"][0])
        self.assertNotIn("دبی", out["keywords"][0])

    def test_city_only_query_has_no_district_warning(self):
        out = smart.parse("کافه در مشهد")
        self.assertEqual(out["detected"]["area"]["level"], "city")
        self.assertEqual(out["detected"]["area"]["radius"], 10000)
        self.assertFalse(any("خیابان" in w for w in out["warnings"]))

    def test_district_selector_counts_are_exposed(self):
        out = smart.parse("", city="tehran")
        area = out["detected"]["area"]
        self.assertGreater(area["districts"], 50)
        self.assertGreater(area["streets"], 20)
        self.assertIn("level_fa", area)
        self.assertIn("keyword_label", area)


# ─────────────────────────────────────────────────────────────────────────────
# ۶) مسیرهای HTTP
# ─────────────────────────────────────────────────────────────────────────────
class RegionsApiTestCase(unittest.TestCase):
    httpd = None
    base = ""

    @classmethod
    def setUpClass(cls):
        cls.port = _find_free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.httpd = server_mod.make_server("127.0.0.1", cls.port)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        for _ in range(60):
            try:
                cls.get("/api/health")
                return
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("سرور تست بالا نیامد")

    @classmethod
    def tearDownClass(cls):
        if cls.httpd:
            cls.httpd.shutdown()
            cls.httpd.server_close()

    @classmethod
    def get(cls, path):
        with urllib.request.urlopen(cls.base + path, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    @classmethod
    def get_bytes(cls, path):
        with urllib.request.urlopen(cls.base + path, timeout=120) as response:
            return response.read(), response.headers.get("Content-Type", "")

    @classmethod
    def post(cls, path, payload):
        request = urllib.request.Request(
            cls.base + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_mini_ui_is_served(self):
        body, content_type = self.get_bytes("/mini/")
        self.assertIn("text/html", content_type)
        self.assertIn(b'src="app.js"', body)
        self.assertIn("استان".encode(), body)
        for asset in ("/mini/app.js", "/mini/styles.css", "/mini/favicon.svg"):
            body, _ = self.get_bytes(asset)
            self.assertGreater(len(body), 400, asset)

    def test_regions_hierarchy(self):
        data = self.get("/api/regions")
        self.assertEqual(len(data["provinces"]), 31)
        self.assertEqual(data["radius"]["street"], 900)
        tehran = next(p for p in data["provinces"] if p["id"] == "tehran-province")
        self.assertEqual(tehran["capital"], "tehran")
        self.assertTrue(any(c["id"] == "tehran" and c["districts"] > 50 for c in tehran["cities"]))

    def test_regions_districts_and_streets(self):
        data = self.get("/api/regions/districts?city=" + urllib.parse.quote("تهران"))
        self.assertEqual(data["city"], "tehran")
        self.assertGreater(len(data["districts"]), 50)
        streets = self.get("/api/regions/streets?city=tehran&district=" + urllib.parse.quote("منطقه ۳"))
        self.assertTrue(streets["streets"])
        self.assertTrue(all(row["kind"] == "street" for row in streets["streets"]))
        with self.assertRaises(urllib.error.HTTPError):
            self.get("/api/regions/districts?city=nowhere")

    def test_regions_search_endpoint(self):
        data = self.get("/api/regions/search?q=" + urllib.parse.quote("vanak"))
        self.assertTrue(data["areas"])
        self.assertEqual(data["areas"][0]["city"], "tehran")
        sentence = self.get("/api/regions/search?q=" + urllib.parse.quote("کافه در چیتگر تهران"))
        self.assertTrue(sentence["in_text"])
        self.assertEqual(sentence["in_text"][0]["fa"], "چیتگر")

    def test_regions_resolve_endpoint(self):
        data = self.get("/api/regions/resolve?city=tehran&district=" +
                        urllib.parse.quote("پونک"))
        self.assertEqual(data["level"], "district")
        self.assertEqual(data["radius"], 2500)
        self.assertTrue(data["lat"] and data["lon"])

    def test_smart_parse_accepts_area_selectors(self):
        data = self.post("/api/smart/parse", {
            "text": "", "province": "tehran-province", "city": "tehran", "district": "پونک",
            "all_trades": True, "size": "quick", "limit": 60})
        self.assertTrue(data["ready"])
        self.assertEqual(data["detected"]["area"]["level"], "district")
        self.assertEqual(data["request"]["radius"], 2500)
        self.assertEqual(data["request"]["limit"], 60)

    def test_full_area_run_then_search_then_export(self):
        parsed = self.post("/api/smart/parse", {
            "text": "کافه", "city": "tehran", "district": "تجریش", "size": "quick"})
        self.assertTrue(parsed["ready"])
        started = self.post("/api/runs", parsed["request"])
        run_id = started["run_id"]
        for _ in range(300):
            progress = self.get(f"/api/runs/{run_id}/progress")["progress"]
            if progress.get("status") in ("done", "failed"):
                break
            time.sleep(0.1)
        self.assertEqual(progress.get("status"), "done", str(progress))

        detail = self.get(f"/api/runs/{run_id}")
        self.assertEqual(detail["run"]["area_level"], "district")
        self.assertIn("تجریش", detail["run"]["area_label"])
        self.assertGreater(detail["lead_count"], 0)

        found = self.get("/api/search?" + urllib.parse.urlencode({"run_id": run_id, "limit": 5}))
        self.assertEqual(found["total"], detail["lead_count"])
        self.assertTrue(all(lead["run_id"] == run_id for lead in found["leads"]))

        run = detail["run"]
        near = self.get("/api/search?" + urllib.parse.urlencode({
            "lat": float(run["lat"]), "lon": float(run["lon"]), "radius_m": 2500, "limit": 5}))
        self.assertGreater(near["total"], 0)
        self.assertTrue(all(lead["distance_m"] <= 2500 for lead in near["leads"]))

        filtered = self.get("/api/search?" + urllib.parse.urlencode({
            "run_id": run_id, "has_phone": 1, "segment": "hot", "sort": "rating", "limit": 3}))
        self.assertTrue(all(lead["phone"] for lead in filtered["leads"]))
        ratings = [lead["rating"] or 0 for lead in filtered["leads"]]
        self.assertEqual(ratings, sorted(ratings, reverse=True))

        body, content_type = self.get_bytes("/api/search/export?" + urllib.parse.urlencode({
            "run_id": run_id, "fmt": "csv", "cols": "contact"}))
        self.assertIn("text/csv", content_type)
        self.assertIn("نام کسب‌وکار".encode(), body)

        body, content_type = self.get_bytes("/api/search/export?" + urllib.parse.urlencode({
            "run_id": run_id, "fmt": "xlsx"}))
        self.assertIn("spreadsheetml", content_type)
        self.assertEqual(body[:2], b"PK")

        body, _ = self.get_bytes("/api/search/export?" + urllib.parse.urlencode({
            "run_id": run_id, "fmt": "json"}))
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(len(payload["leads"]), detail["lead_count"])
        storage.delete_run(run_id)

    def test_cli_flags_are_honoured(self):
        """`--port`/`--host`/`--print-engine` واقعاً باید از argv خوانده شوند."""
        # اگر argv نادیده گرفته شود، این فراخوانی به‌جای خروج، پورت پیش‌فرض را bind می‌کند
        self.assertEqual(server_mod.main(["--print-engine", "--port", "1"]), 0)
        with self.assertRaises(SystemExit):
            server_mod.main(["--port"])          # آرگومان ناقص → خطای argparse

    def test_search_export_without_results_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get_bytes("/api/search/export?" + urllib.parse.urlencode({"q": "zzqqxx"}))
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
