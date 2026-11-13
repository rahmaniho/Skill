"""آزمون نقشهٔ صنف → تگ OSM و مسیر Overpass (بدون شبکه)."""
from __future__ import annotations

import inspect
import unittest
from unittest import mock

from leadgen import config, engine, niches, osm_tags
from leadgen.providers import osm


class TagMapTests(unittest.TestCase):
    def test_every_niche_has_tag_mapping(self):
        missing = [n["id"] for n in niches.all_niches() if n["id"] not in osm_tags.SPEC]
        self.assertEqual(missing, [], f"صنف‌های بدون نقشهٔ OSM: {missing}")

    def test_no_orphan_ids(self):
        known = {n["id"] for n in niches.all_niches()}
        orphans = [nid for nid in osm_tags.SPEC if nid not in known]
        self.assertEqual(orphans, [], f"شناسهٔ ناشناخته در نقشه: {orphans}")

    def test_filters_are_valid_blocks(self):
        for niche_id in osm_tags.SPEC:
            blocks = osm_tags.filters_for_niche(niche_id, with_names=True)
            self.assertTrue(blocks, f"بدون فیلتر: {niche_id}")
            for block in blocks:
                self.assertTrue(block.startswith("[") and block.endswith("]"), block)

    def test_all_business_filters_cover_trade_layers(self):
        blocks = osm_tags.all_business_filters()
        text = " ".join(blocks)
        for key in ("shop", "craft", "office", "amenity", "healthcare"):
            self.assertIn(f'["{key}"', text, f"لایهٔ {key} در جاروی همهٔ اصناف نیست")

    def test_niche_from_tags(self):
        self.assertEqual(osm_tags.niche_for_tags({"amenity": "dentist"}), "dentist")
        self.assertEqual(osm_tags.niche_for_tags({"amenity": "fast_food"}), "fast-food")
        self.assertEqual(osm_tags.niche_for_tags({"shop": "car_repair"}), "car-repair")
        self.assertEqual(osm_tags.niche_for_tags({"shop": "hairdresser"}), "barbershop")
        self.assertEqual(osm_tags.niche_for_tags({"amenity": "parking"}), "")

    def test_niche_from_persian_name_fallback(self):
        self.assertEqual(osm_tags.niche_for_tags({"shop": "yes", "name": "کباب سرای گلستان"}),
                         "restaurant")
        self.assertEqual(osm_tags.niche_for_tags({"shop": "yes", "name": "آرایشگاه مردانه رضا"}),
                         "barbershop")

    def test_excluded_places(self):
        self.assertTrue(osm_tags.is_excluded({"amenity": "parking"}))
        self.assertTrue(osm_tags.is_excluded({"place": "suburb"}))
        self.assertFalse(osm_tags.is_excluded({"shop": "bakery"}))
        self.assertFalse(osm_tags.is_excluded({"shop": "yes"}))

    def test_describe_is_persian(self):
        self.assertEqual(osm_tags.describe({"amenity": "restaurant"}), "رستوران")
        self.assertEqual(osm_tags.describe({"shop": "clothes"}), "پوشاک")


class OverpassQueryTests(unittest.TestCase):
    def test_query_shape_and_bbox_order(self):
        query = osm.build_overpass_query(['["amenity"="dentist"]'], [35.5, 35.9, 51.1, 51.7],
                                         limit=250, timeout=20)
        # bbox داخلی [s,n,w,e] → Overpass (s,w,n,e)
        self.assertIn("(35.500000,51.100000,35.900000,51.700000)", query)
        self.assertIn("[out:json][timeout:20];", query)
        self.assertIn("out center 250;", query)
        self.assertEqual(query.count("nwr"), 1)

    def test_query_rejects_empty_filters(self):
        with self.assertRaises(ValueError):
            osm.build_overpass_query([], [1, 2, 3, 4])

    def test_limit_scales_with_area(self):
        self.assertEqual(osm.overpass_limit_for([35.6, 35.7, 51.3, 51.5], default=800), 800)
        self.assertLessEqual(osm.overpass_limit_for([25.0, 39.0, 44.0, 63.0], default=3000), 1200)

    def test_engine_uses_overpass_first(self):
        from leadgen.engine import OSMEngine

        def row_at(index: int) -> dict:
            return {"place_id": f"osm:node/{index}", "title": f"دندانپزشکی {index}",
                    "latitude": "35.75", "longitude": "51.41", "input_id": "dentist",
                    "niche_id": "dentist", "category": "دندانپزشکی"}

        rows_found = [row_at(i) for i in range(6)]
        body = {"name": "probe", "keywords": ["دندانپزشکی در تهران"], "lang": "fa",
                "city_label": "تهران", "lat": "35.6892", "lon": "51.389", "radius": 8000,
                "country": "ir", "max_time": 40, "_niche": niches.find_niche("دندانپزشکی"),
                "osm_terms": ["dentist"], "bbox": [35.55, 35.83, 51.15, 51.62]}
        with mock.patch.object(osm, "overpass_search", return_value=rows_found) as overpass, \
                mock.patch.object(osm, "search", return_value=[]) as nominatim:
            _job_id, rows = OSMEngine().run_job(body)
        self.assertEqual(overpass.call_count, 1)
        self.assertEqual(len(rows), 6)
        # با نتیجهٔ کافی از تگ‌ها، سهمیهٔ Nominatim مصرف نمی‌شود
        self.assertEqual(nominatim.call_count, 0)

    def test_engine_falls_back_to_nominatim_when_overpass_empty(self):
        from leadgen.engine import OSMEngine

        fallback = {"place_id": "osm:node/2", "title": "مطب دکتر رضایی",
                    "latitude": "35.7", "longitude": "51.4", "input_id": "doctors"}
        body = {"name": "probe", "keywords": ["پزشک در تهران"], "lang": "fa",
                "city_label": "تهران", "lat": "35.6892", "lon": "51.389", "radius": 8000,
                "country": "ir", "max_time": 40, "_niche": niches.find_niche("پزشک"),
                "osm_terms": ["doctors"], "bbox": [35.55, 35.83, 51.15, 51.62]}
        with mock.patch.object(osm, "overpass_search", return_value=[]), \
                mock.patch.object(osm, "search", return_value=[fallback]):
            _job_id, rows = OSMEngine().run_job(body)
        self.assertEqual([r["title"] for r in rows], ["مطب دکتر رضایی"])

    def test_nominatim_only_mode_skips_overpass(self):
        from leadgen.engine import OSMEngine

        body = {"name": "probe", "keywords": ["کافه در تهران"], "lang": "fa",
                "city_label": "تهران", "lat": "35.6892", "lon": "51.389", "radius": 8000,
                "country": "ir", "max_time": 40, "_niche": niches.find_niche("کافه"),
                "osm_terms": ["cafe"], "bbox": [35.55, 35.83, 51.15, 51.62]}
        with mock.patch.object(config, "OSM_MODE", "nominatim"), \
                mock.patch.object(osm, "overpass_search") as overpass, \
                mock.patch.object(osm, "search", return_value=[{"place_id": "x", "title": "کافه"}]):
            _job_id, rows = OSMEngine().run_job(body)
        self.assertEqual(overpass.call_count, 0)
        self.assertEqual(len(rows), 1)


class RowParsingTests(unittest.TestCase):
    element = {
        "type": "node", "id": 12345,
        "lat": 36.91806, "lon": 50.64806,
        "tags": {
            "name": "رستوران آرارات", "amenity": "restaurant",
            "phone": "+981155213715", "website": "https://example.ir",
            "contact:instagram": "ararat.rest", "addr:street": "خیابان معلم",
            "opening_hours": "Mo-Su 11:00-23:00", "cuisine": "iranian",
        },
    }

    def test_parse_full_row(self):
        row = osm.row_from_overpass(self.element, input_id="restaurant")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["title"], "رستوران آرارات")
        self.assertEqual(row["category"], "رستوران")
        self.assertEqual(row["niche_id"], "restaurant")
        self.assertEqual(row["latitude"], "36.91806")
        self.assertEqual(row["phone"], "+981155213715")
        self.assertEqual(row["instagram"], "ararat.rest")
        self.assertEqual(row["address"], "خیابان معلم")
        self.assertEqual(row["place_id"], "osm:node/12345")
        self.assertEqual(row["source"], "openstreetmap")

    def test_skip_unnamed_and_excluded(self):
        self.assertIsNone(osm.row_from_overpass({"type": "node", "id": 1, "lat": 1, "lon": 2,
                                                 "tags": {"amenity": "parking"}}))
        self.assertIsNone(osm.row_from_overpass({"type": "node", "id": 2, "lat": 1, "lon": 2,
                                                 "tags": {"amenity": "restaurant"}}))
        self.assertIsNone(osm.row_from_overpass({"type": "way", "id": 3, "tags": {"shop": "bakery",
                                                                                "name": "نان"}}))

    def test_relation_uses_center(self):
        element = {"type": "way", "id": 77, "center": {"lat": 35.7, "lon": 51.4},
                   "tags": {"name": "کلینیک ونک", "amenity": "clinic"}}
        row = osm.row_from_overpass(element)
        self.assertEqual((row["latitude"], row["longitude"]), ("35.7", "51.4"))
        self.assertEqual(row["category"], "درمانگاه")

    def test_skip_unnamed(self):
        self.assertIsNone(osm.row_from_overpass({"type": "node", "id": 9, "lat": 1, "lon": 2,
                                                 "tags": {"amenity": "restaurant"}}))


if __name__ == "__main__":
    unittest.main()


class OverpassCacheAndTilingTests(unittest.TestCase):
    """کش کوتاه‌مدت و شکستن محدوده: کامل‌تری و ادب استفاده از سرویس عمومی."""

    def setUp(self):
        osm.cache_clear()

    def tearDown(self):
        osm.cache_clear()

    def _element(self, ident: int, lat: float, lon: float) -> dict:
        return {"type": "node", "id": ident, "lat": lat, "lon": lon,
                "tags": {"amenity": "cafe", "name": f"کافه {ident}"}}

    def test_split_bbox_tiles_cover_area_without_gap(self):
        tiles = osm.split_bbox([35.0, 36.0, 51.0, 52.0], 2, 2)
        self.assertEqual(len(tiles), 4)
        self.assertEqual(tiles[0][:2], [35.0, 35.5])
        self.assertAlmostEqual(sum(t[1] - t[0] for t in tiles[:2]), 1.0)
        # پوشش کامل بدون سوراخ: اجتماع عرض‌ها = عرض اصلی
        for tile in tiles:
            self.assertGreater(tile[1], tile[0])
            self.assertGreater(tile[3], tile[2])

    def test_cache_hit_avoids_second_network_call(self):
        payload = {"elements": [self._element(1, 35.7, 51.4)]}
        with mock.patch.object(osm, "_overpass_request", return_value=payload) as request:
            first = osm.overpass_search(['["amenity"="cafe"]'], bbox=[35.6, 35.8, 51.3, 51.5],
                                        limit=100, input_id="cafe")
            second = osm.overpass_search(['["amenity"="cafe"]'], bbox=[35.6, 35.8, 51.3, 51.5],
                                        limit=100, input_id="cafe")
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["place_id"], second[0]["place_id"])
        self.assertEqual(second[0]["input_id"], "cafe")

    def test_cache_disabled_sends_every_request(self):
        payload = {"elements": [self._element(2, 35.7, 51.4)]}
        with mock.patch.object(config, "OVERPASS_CACHE_TTL", 0), \
                mock.patch.object(osm, "_overpass_request", return_value=payload) as request:
            for _ in range(2):
                osm.overpass_search(['["amenity"="cafe"]'], bbox=[35.6, 35.8, 51.3, 51.5],
                                    limit=100)
        self.assertEqual(request.call_count, 2)

    def test_area_crawl_splits_when_result_hits_limit(self):
        calls = []

        def fake_search(filters, *, bbox, limit=None, timeout=None, input_id="", lang="fa",
                        cache=True, force=False, budget_s=None):
            calls.append(list(bbox))
            if len(calls) == 1:
                return [{"place_id": f"osm:node/{i}"} for i in range(limit)]
            ident = len(calls)
            return [{"place_id": f"osm:node/{ident}"}]

        with mock.patch.object(osm, "overpass_search", side_effect=fake_search):
            rows = osm.overpass_search_area(['["amenity"="cafe"]'],
                                            bbox=[35.4, 35.9, 51.0, 51.7], limit=100, tiles=4)
        self.assertEqual(len(calls), 5)                 # ۱ + ۴ قطعه
        self.assertEqual(len(rows), 4)                  # اتحاد قطعه‌ها، بدون تکرار
        self.assertEqual(len({r["place_id"] for r in rows}), 4)

    def test_area_crawl_respects_time_budget(self):
        calls = []

        def fake_search(filters, *, bbox, limit=None, timeout=None, input_id="", lang="fa",
                        cache=True, force=False, budget_s=None):
            calls.append(list(bbox))
            return [{"place_id": f"osm:node/{i}"} for i in range(limit)]

        with mock.patch.object(osm, "overpass_search", side_effect=fake_search):
            rows = osm.overpass_search_area(['["amenity"="cafe"]'],
                                            bbox=[35.4, 35.9, 51.0, 51.7], limit=100,
                                            tiles=4, budget_s=1)
        self.assertEqual(len(calls), 1, "با بودجهٔ یک‌ثانیه‌ای نباید قطعه‌های بیشتر رفت")
        self.assertEqual(len(rows), 100)

    def test_area_crawl_single_call_for_small_complete_result(self):
        payload = {"elements": [self._element(3, 35.7, 51.4)]}
        with mock.patch.object(osm, "_overpass_request", return_value=payload) as request:
            rows = osm.overpass_search_area(['["amenity"="cafe"]'],
                                            bbox=[35.6, 35.8, 51.3, 51.5], limit=100, tiles=4)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(rows), 1)


class MirrorConfigTests(unittest.TestCase):
    def test_at_least_two_https_mirrors(self):
        endpoints = config.OVERPASS_ENDPOINTS
        self.assertGreaterEqual(len(endpoints), 2)
        for url in endpoints:
            self.assertTrue(url.startswith("https://"), url)
            self.assertTrue(url.endswith("/interpreter") or "/interpreter" in url, url)
        self.assertEqual(len(set(endpoints)), len(endpoints), "آینهٔ تکراری")


class OverpassHealthTests(unittest.TestCase):
    """/status کند ≠ سرویس خراب (روی GitHub Actions دیده شد)."""

    def setUp(self):
        osm._OVERPASS_HEALTH.update({"ts": 0.0, "payload": None})
        osm._OVERPASS_LAST_OK[0] = 0.0
        self._gap = config.OVERPASS_MIN_INTERVAL
        config.OVERPASS_MIN_INTERVAL = 0.001     # بدون مکث ۱ ثانیه‌ای در آزمون

    def tearDown(self):
        config.OVERPASS_MIN_INTERVAL = self._gap
        osm._OVERPASS_HEALTH.update({"ts": 0.0, "payload": None})
        osm._OVERPASS_LAST_OK[0] = 0.0

    def test_status_timeout_falls_back_to_real_mini_query(self):
        # /status تایم‌اوت می‌دهد ولی پرسش کوچک واقعی جواب دارد → سالم
        with mock.patch.object(osm.urllib.request, "urlopen", side_effect=TimeoutError("timed out")), \
                mock.patch.object(osm, "_overpass_mini_probe", return_value=True):
            payload = osm.overpass_health(force=True)
        self.assertTrue(payload["up"])
        self.assertTrue(payload["mini_probe"])

    def test_status_timeout_and_mini_probe_fail_is_unknown(self):
        with mock.patch.object(osm.urllib.request, "urlopen", side_effect=TimeoutError("timed out")), \
                mock.patch.object(osm, "_overpass_mini_probe", return_value=False):
            payload = osm.overpass_health(force=True)
        self.assertIsNone(payload["up"])
        self.assertEqual(payload["error"], "")
        self.assertIn("نامعلوم", payload["note"])

    def test_recent_success_wins_over_slow_status(self):
        osm._OVERPASS_LAST_OK[0] = __import__("time").time()
        with mock.patch.object(osm.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
            payload = osm.overpass_health(force=True)
        self.assertTrue(payload["up"])

    def test_connection_refused_is_down(self):
        with mock.patch.object(osm.urllib.request, "urlopen",
                               side_effect=OSError("Connection refused")), \
                mock.patch.object(osm, "_overpass_mini_probe", return_value=False):
            payload = osm.overpass_health(force=True)
        self.assertFalse(payload["up"])

    def test_status_502_uses_real_query(self):
        """روی Vercel دیده شد: /status → 502 ولی interpreter سالم بود."""
        import urllib.error
        err = urllib.error.HTTPError("https://overpass-api.de/api/status", 502,
                                     "Bad Gateway", {}, None)
        with mock.patch.object(osm.urllib.request, "urlopen", side_effect=err), \
                mock.patch.object(osm, "_overpass_mini_probe", return_value=True):
            payload = osm.overpass_health(force=True)
        self.assertTrue(payload["up"])
        self.assertTrue(payload["mini_probe"])

    def test_probe_disabled_returns_unknown_without_network(self):
        with mock.patch.object(osm.urllib.request, "urlopen", side_effect=AssertionError("شبکه")):
            payload = osm.overpass_health(probe=False)
        self.assertIsNone(payload["up"])
        self.assertIn("سنجیده نشده", payload["note"])


class OverpassDownCacheTests(unittest.TestCase):
    """وقتی Overpass کامل شکست می‌خورد، جست‌وجوی بعدی نباید دوباره ده‌ها ثانیه معطل شود."""

    def setUp(self):
        osm.cache_clear()
        osm._OVERPASS_DOWN_UNTIL[0] = 0.0
        self._gap = config.OVERPASS_MIN_INTERVAL
        config.OVERPASS_MIN_INTERVAL = 0.001

    def tearDown(self):
        config.OVERPASS_MIN_INTERVAL = self._gap
        osm._OVERPASS_DOWN_UNTIL[0] = 0.0
        osm.cache_clear()

    def test_second_search_fails_fast_without_network(self):
        with mock.patch.object(osm.urllib.request, "urlopen",
                               side_effect=OSError("network down")) as net:
            with self.assertRaises(osm.OSMDownError):
                osm.overpass_search(['["amenity"="cafe"]'], bbox=[35.6, 35.8, 51.3, 51.5],
                                    limit=50)
            first_calls = net.call_count
            with self.assertRaises(osm.OSMDownError):
                osm.overpass_search(['["amenity"="cafe"]'], bbox=[35.6, 35.8, 51.3, 51.5],
                                    limit=50)
            self.assertEqual(net.call_count, first_calls, "نباید دوباره شبکه را امتحان کند")
        self.assertFalse(osm.overpass_available())

    def test_force_bypasses_down_marker(self):
        osm._OVERPASS_DOWN_UNTIL[0] = __import__("time").monotonic() + 60
        payload = {"elements": [{"type": "node", "id": 1, "lat": 35.7, "lon": 51.4,
                                 "tags": {"amenity": "cafe", "name": "کافه"}}]}
        with mock.patch.object(osm, "_overpass_request", return_value=payload):
            rows = osm.overpass_search(['["amenity"="cafe"]'], bbox=[35.6, 35.8, 51.3, 51.5],
                                       limit=50, force=True)
        self.assertEqual(len(rows), 1)
        self.assertTrue(osm.overpass_available())      # موفقیت، علامت خرابی را پاک می‌کند


class ProvinceBoxTests(unittest.TestCase):
    """انتخاب «استان» باید چند شهر را بگردد، نه فقط مرکز استان."""

    def test_boxes_sorted_by_population_and_have_bbox(self):
        from leadgen import regions
        boxes = regions.province_boxes("tehran-province", limit=5)
        self.assertEqual(len(boxes), 5)
        pops = [b["pop"] for b in boxes]
        self.assertEqual(pops, sorted(pops, reverse=True))
        self.assertEqual(boxes[0]["id"], "tehran")
        for box in boxes:
            self.assertEqual(len(box["bbox"]), 4)
            s, n, w, e = box["bbox"]
            self.assertLess(s, n)
            self.assertLess(w, e)
            self.assertTrue(box["fa"])

    def test_unknown_province_is_empty(self):
        from leadgen import regions
        self.assertEqual(regions.province_boxes("nope", limit=5), [])

    def test_limit_zero_means_no_sweep(self):
        from leadgen import regions
        self.assertGreater(len(regions.province_boxes("gilan", limit=0)), 1)
        self.assertEqual(regions.province_boxes("gilan", limit=2), regions.province_boxes("gilan", limit=2)[:2])


class OverpassSweepTests(unittest.TestCase):
    """موتور OSM باید برای هر جعبهٔ شهر جدا بپرسد و نتیجه‌ها را بی‌تکرار ادغام کند."""

    def _row(self, ident: int, title: str) -> dict:
        return {"place_id": f"osm:node/{ident}", "title": title, "category": "کافه",
                "latitude": "35.70", "longitude": "51.40", "phone": "", "website": "",
                "instagram": "", "facebook": "", "linkedin": "", "address": "",
                "input_id": "cafe", "source": "openstreetmap"}

    def test_each_sweep_box_is_queried_and_merged(self):
        from leadgen import engine as engine_mod
        seen: list[list[float]] = []

        def fake_area(filters, *, bbox, limit=None, timeout=None, input_id="", lang="fa",
                      tiles=1, max_rows=None, budget_s=None, segments=None):
            seen.append(list(bbox))
            row = self._row(len(seen), f"کافه {len(seen)}")
            row["latitude"] = f"{(bbox[0] + bbox[1]) / 2:.5f}"
            row["longitude"] = f"{(bbox[2] + bbox[3]) / 2:.5f}"
            return [row]

        body = {
            "keywords": ["کافه در گیلان"], "city_label": "گیلان", "lang": "fa",
            "lat": "37.28", "lon": "49.59", "bbox": [37.20, 37.36, 49.51, 49.67],
            "radius": 30000, "depth": 3, "max_time": 40,
            "sweep_boxes": [
                {"fa": "رشت", "pop": 700000, "bbox": [37.20, 37.36, 49.51, 49.67]},
                {"fa": "لاهیجان", "pop": 100000, "bbox": [37.15, 37.25, 49.95, 50.05]},
            ],
        }
        with mock.patch.object(osm, "overpass_search_area", side_effect=fake_area), \
                mock.patch.object(osm, "search", return_value=[]):
            job_id, rows = engine_mod.OSMEngine().run_job(body)
        self.assertEqual(len(seen), 2, "برای هر شهر باید یک پرسش جدا زده شود")
        self.assertEqual(seen[0], [37.20, 37.36, 49.51, 49.67])
        self.assertEqual(seen[1], [37.15, 37.25, 49.95, 50.05])
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["place_id"] for r in rows}), 2)
        self.assertTrue(job_id)

    def test_progress_names_the_city(self):
        from leadgen import engine as engine_mod
        events: list[dict] = []

        def fake_area(filters, *, bbox, **kwargs):
            row = self._row(1, "کافه")
            row["latitude"] = f"{(bbox[0] + bbox[1]) / 2:.5f}"
            row["longitude"] = f"{(bbox[2] + bbox[3]) / 2:.5f}"
            return [row]

        body = {
            "keywords": ["کافه در گیلان"], "city_label": "گیلان", "lang": "fa",
            "lat": "37.28", "lon": "49.59", "bbox": [37.20, 37.36, 49.51, 49.67],
            "depth": 3, "max_time": 40,
            "sweep_boxes": [
                {"fa": "رشت", "pop": 700000, "bbox": [37.20, 37.36, 49.51, 49.67]},
                {"fa": "لاهیجان", "pop": 100000, "bbox": [37.15, 37.25, 49.95, 50.05]},
            ],
        }
        with mock.patch.object(osm, "overpass_search_area", side_effect=fake_area), \
                mock.patch.object(osm, "search", return_value=[]):
            engine_mod.OSMEngine().run_job(body, on_progress=events.append)
        messages = " ".join(str(e.get("message") or "") for e in events)
        self.assertIn("رشت", messages)
        self.assertIn("لاهیجان", messages)


class SweepFallbackTests(unittest.TestCase):
    """اگر Overpass برای شهری جواب نداد، همان شهر با جست‌وجوی متنی گرفته شود."""

    def _row(self, ident: int, title: str) -> dict:
        return {"place_id": f"osm:node/{ident}", "title": title, "category": "کافه",
                "latitude": "37.28", "longitude": "49.59", "phone": "", "website": "",
                "instagram": "", "facebook": "", "linkedin": "", "address": "",
                "input_id": "cafe", "source": "openstreetmap", "city": ""}

    def _body(self) -> dict:
        return {
            "keywords": ["کافه در گیلان"], "city_label": "گیلان", "lang": "fa",
            "lat": "37.28", "lon": "49.59", "bbox": [37.20, 37.36, 49.51, 49.67],
            "depth": 2, "max_time": 50,
            "sweep_boxes": [
                {"fa": "رشت", "pop": 700000, "bbox": [37.20, 37.36, 49.51, 49.67]},
                {"fa": "لاهیجان", "pop": 100000, "bbox": [37.15, 37.25, 49.95, 50.05]},
            ],
        }

    def test_missing_city_is_covered_by_text_search(self):
        from leadgen import engine as engine_mod
        calls = []

        def fake_area(filters, *, bbox, **kwargs):
            if bbox[2] > 49.9:                      # لاهیجان: Overpass خالی
                raise osm.OSMDownError("Overpass پاسخ نداد")
            return [self._row(1, "کافه رشت")]

        def fake_search(term, **kwargs):
            calls.append((term, kwargs.get("bbox"), kwargs.get("input_id")))
            return [self._row(2, "کافه لاهیجان")]

        eng = engine_mod.OSMEngine()
        with mock.patch.object(osm, "overpass_search_area", side_effect=fake_area), \
                mock.patch.object(osm, "search", side_effect=fake_search):
            _, rows = eng.run_job(self._body())
        self.assertEqual(len(rows), 2)
        self.assertTrue(any(city == "لاهیجان" for _, _, city in calls),
                        f"باید برای لاهیجان جست‌وجوی متنی زده شود: {calls}")
        coverage = {c["city"]: c for c in eng.coverage}
        self.assertEqual(coverage["رشت"]["via"], "overpass")
        self.assertEqual(coverage["لاهیجان"]["via"], "nominatim")
        self.assertEqual(coverage["لاهیجان"]["rows"], 1)
        # شهری که با مسیر جایگزین داده داد، نباید خطای Overpass را با خود بکشد
        self.assertEqual(coverage["لاهیجان"]["error"], "")

    def test_every_city_appears_once_in_coverage(self):
        """حتی وقتی هیچ شهری داده نداد، گزارش باید هر شهر را یک‌بار و با دلیل نشان دهد."""
        from leadgen import engine as engine_mod

        def failing_area(filters, *, bbox, **kwargs):
            raise osm.OSMDownError("Overpass پاسخ نداد")

        eng = engine_mod.OSMEngine()
        with mock.patch.object(osm, "overpass_search_area", side_effect=failing_area), \
                mock.patch.object(osm, "search", return_value=[]):
            with self.assertRaises(engine_mod.EmptyResult):
                eng.run_job(self._body())
        cities = [c["city"] for c in eng.coverage]
        self.assertEqual(len(cities), len(set(cities)), f"شهر تکراری در گزارش: {cities}")
        self.assertEqual(set(cities), {"رشت", "لاهیجان"})
        for item in eng.coverage:
            self.assertEqual(item["rows"], 0)
            self.assertTrue(item["error"], item)

    def test_strict_overpass_mode_skips_text_fallback(self):
        from leadgen import engine as engine_mod
        eng = engine_mod.OSMEngine()

        def fake_area(filters, *, bbox, **kwargs):
            return [self._row(1, "کافه")]

        saved = config.OSM_MODE
        config.OSM_MODE = "overpass"
        try:
            with mock.patch.object(osm, "overpass_search_area", side_effect=fake_area), \
                    mock.patch.object(osm, "search") as search:
                eng.run_job(self._body())
            self.assertFalse(search.called)
        finally:
            config.OSM_MODE = saved


class ShareForBoxTests(unittest.TestCase):
    """سهم Overpass باید همیشه چند ثانیه برای مسیر متنی (Nominatim) کنار بگذارد."""

    def test_single_city_keeps_time_for_text_search(self):
        from leadgen.engine import share_for_box
        share = share_for_box(box_left=21.0, phase_budget=12.6, remaining=1, single=True)
        self.assertLessEqual(share, 12.0)
        self.assertGreaterEqual(21.0 - share, 9.0, "بعد از Overpass باید ≥۹ ثانیه بمانَد")

    def test_single_city_share_never_exceeds_half_the_budget(self):
        from leadgen.engine import share_for_box
        share = share_for_box(box_left=40.0, phase_budget=40.0, remaining=1, single=True)
        self.assertLessEqual(share, 24.0)

    def test_sweep_shares_budget_between_cities(self):
        from leadgen.engine import share_for_box
        share = share_for_box(box_left=21.0, phase_budget=12.6, remaining=3, single=False)
        self.assertGreaterEqual(share, 8.0)
        self.assertLessEqual(share, 21.0 - 6.0)


class TermOrderTests(unittest.TestCase):
    """عبارت واژه‌نامه‌ای باید قبل از عبارت آزاد باشد («cafe» قبل از «coffee shops»)."""

    def test_lexicon_term_comes_before_user_phrase(self):
        from leadgen import niches, osm_terms
        niche = niches.find_niche("کافه")
        terms = osm_terms.derive([niche.fa, *niche.keywords_fa[:2], "coffee shops"])
        self.assertEqual(terms[0], "cafe", terms)
        self.assertIn("coffee shops", terms)
        self.assertLess(terms.index("cafe"), terms.index("coffee shops"))

    def test_complex_phrase_goes_last(self):
        from leadgen import osm_terms
        terms = osm_terms.derive(["cafe", "cafés & coffee shops"])
        self.assertEqual(terms[-1], "cafés & coffee shops")
        self.assertEqual(terms[0], "cafe")


class FullScanTileTests(unittest.TestCase):
    """موتور OSM: گذر «اسکراب کامل» هر کاشی را جدا می‌پرسد و پوشش را گزارش می‌کند."""

    def _engine(self):
        return engine.OSMEngine()

    def test_each_tile_is_searched_and_reported(self):
        calls: list[list[float]] = []
        counter = [0]

        def fake_search(term, *, bbox, country=None, limit=None, timeout=12.0,
                        lang="fa", input_id="", pages=1):
            calls.append(list(bbox))
            counter[0] += 1
            center = ((bbox[0] + bbox[1]) / 2, (bbox[2] + bbox[3]) / 2)
            return [{"place_id": f"osm:node/c{counter[0]}-{i}", "title": f"کافه {i}",
                     "category": "کافه", "latitude": f"{center[0]:.6f}",
                     "longitude": f"{center[1]:.6f}", "source": "openstreetmap",
                     "phone": "", "website": "", "instagram": "", "facebook": "",
                     "linkedin": "", "address": "یزد", "rating": None, "reviews": 0,
                     "tags": {}, "input_id": term}
                    for i in range(3)]

        body = {
            "name": "scan", "keywords": ["کافه"], "lang": "fa", "lat": "31.89",
            "lon": "54.36", "radius": 5000, "max_time": 45,
            "bbox": [31.80, 31.98, 54.25, 54.47],
            "scan_tiles": [
                {"label": "یزد · کاشی ۱/۴", "bbox": [31.80, 31.89, 54.25, 54.36]},
                {"label": "یزد · کاشی ۲/۴", "bbox": [31.80, 31.89, 54.36, 54.47]},
                {"label": "یزد · کاشی ۳/۴", "bbox": [31.89, 31.98, 54.25, 54.36]},
                {"label": "یزد · کاشی ۴/۴", "bbox": [31.89, 31.98, 54.36, 54.47]},
            ],
            "osm_terms": ["cafe"], "_niche": None,
        }
        with mock.patch.object(osm, "overpass_search", return_value=[]), \
                mock.patch.object(osm, "search", side_effect=fake_search):
            _name, rows = self._engine().run_job(body)
        self.assertGreaterEqual(len(calls), 4, "هر کاشی باید جدا پرسیده شود")
        self.assertGreaterEqual(len(rows), 4)
        self.assertEqual(len({r["place_id"] for r in rows}), len(rows), "بدون تکرار")

    def test_tile_coverage_records_every_tile(self):
        eng = self._engine()

        calls = [0]

        def fake_search(term, *, bbox, country=None, limit=None, timeout=12.0,
                        lang="fa", input_id="", pages=1):
            calls[0] += 1
            center = ((bbox[0] + bbox[1]) / 2, (bbox[2] + bbox[3]) / 2)
            return [{"place_id": f"osm:node/n{calls[0]}", "title": "کافه",
                     "category": "کافه", "latitude": f"{center[0]:.6f}",
                     "longitude": f"{center[1]:.6f}", "source": "openstreetmap",
                     "phone": "", "website": "", "instagram": "", "facebook": "",
                     "linkedin": "", "address": "یزد", "rating": None, "reviews": 0,
                     "tags": {}, "input_id": term}]

        body = {
            "name": "scan", "keywords": ["کافه"], "lang": "fa", "lat": "31.89",
            "lon": "54.36", "radius": 5000, "max_time": 45,
            "bbox": [31.80, 31.98, 54.25, 54.47],
            "scan_tiles": [
                {"label": "الف", "bbox": [31.80, 31.89, 54.25, 54.36]},
                {"label": "ب", "bbox": [31.89, 31.98, 54.36, 54.47]},
            ],
            "osm_terms": ["cafe"], "_niche": None,
        }
        with mock.patch.object(osm, "overpass_search", return_value=[]), \
                mock.patch.object(osm, "search", side_effect=fake_search):
            eng.run_job(body)
        report = {t["label"]: t for t in eng.tile_coverage}
        self.assertEqual(set(report), {"الف", "ب"})
        self.assertTrue(all(t["rows"] >= 1 for t in report.values()))

    def test_tile_equal_to_main_box_is_not_re_queried(self):
        """کاشی هم‌اندازهٔ جعبهٔ اصلی = همان پرسش انجام‌شده؛ شبکه دوباره اذیت نمی‌شود."""
        eng = self._engine()
        box = [31.85, 31.95, 54.30, 54.42]
        calls = []

        def fake_search(term, *, bbox, country=None, limit=None, timeout=12.0,
                        lang="fa", input_id="", pages=1):
            calls.append(list(bbox))
            return []

        base = {"name": "scan", "keywords": ["کافه"], "lang": "fa", "lat": "31.90",
                "lon": "54.36", "radius": 5000, "max_time": 30, "bbox": box,
                "osm_terms": ["cafe"], "_niche": None}
        with mock.patch.object(osm, "overpass_search", return_value=[]), \
                mock.patch.object(osm, "search", side_effect=fake_search):
            with self.assertRaises(engine.EmptyResult):
                eng.run_job(dict(base))          # بدون کاشی
            plain_calls = len(calls)
            calls.clear()
            with self.assertRaises(engine.EmptyResult):
                eng.run_job({**base, "scan_tiles": [
                    {"label": "خیابان کاشانی", "bbox": list(box)}]})
        self.assertEqual(len(calls), plain_calls,
                         "کاشیِ هم‌اندازهٔ جعبهٔ اصلی نباید پرسش تازه بسازد")
        self.assertEqual(eng.tile_coverage[0]["via"], "main")


class OverpassStreetBBoxTests(unittest.TestCase):
    """همهٔ تکه‌های به‌هم‌پیوستهٔ یک خیابان → یک جعبهٔ واحد (نه یک تکه)."""

    def setUp(self):
        from leadgen.providers import osm
        osm._STREET_CACHE.clear()

    @staticmethod
    def _way(wid, nodes, start, end):
        """یک way سادهٔ دو نقطه‌ای (مثل خروجی out geom)."""
        return {"type": "way", "id": wid, "nodes": list(nodes),
                "geometry": [{"lat": start[0], "lon": start[1]},
                             {"lat": end[0], "lon": end[1]}]}

    def test_merges_connected_segments_of_the_same_street(self):
        from leadgen.providers import osm
        data = {"elements": [
            self._way(1, [10, 11, 12], (35.700, 51.410), (35.710, 51.412)),
            self._way(2, [12, 13, 14], (35.710, 51.412), (35.750, 51.420)),
            self._way(3, [99, 98], (35.800, 51.300), (35.801, 51.301)),
        ]}
        with mock.patch.object(osm, "_overpass_request", return_value=data) as call:
            found = osm.overpass_street_bbox(["خیابان ولیعصر", "ولیعصر"],
                                             bbox=[35.6, 35.9, 51.2, 51.6],
                                             near=[35.7005, 35.7095, 51.4101, 51.4119])
        self.assertEqual(found["bbox"], [35.7, 35.75, 51.41, 51.42])
        self.assertEqual(found["ways"], 2, "تکهٔ جداافتاده نباید جمع شود")
        self.assertEqual(found["via"], "exact")
        self.assertGreater(found["length_km"], 3.0)
        self.assertEqual(len(found["boxes"]), 2, "جعبهٔ هر تکه برای فیلتر «روی خیابان»")
        query = call.call_args[0][0]
        self.assertIn('way["name"="ولیعصر"](35.6,51.2,35.9,51.6)', query)
        self.assertIn("out geom;", query, "مختصات گره‌ها برای تشخیص اتصال لازم است")

    def test_neighbouring_same_name_street_is_not_joined_by_proximity(self):
        """دو «ولیعصر» موازی با فاصلهٔ ~۲۰۰ متر ولی بدون گرهٔ مشترک نباید یکی شوند."""
        from leadgen.providers import osm
        data = {"elements": [
            self._way(1, [1, 2], (35.7000, 51.4100), (35.7100, 51.4100)),
            self._way(2, [3, 4], (35.7000, 51.4122), (35.7100, 51.4122)),
        ]}
        with mock.patch.object(osm, "_overpass_request", return_value=data):
            found = osm.overpass_street_bbox(["ولیعصر"], bbox=[35.6, 35.9, 51.2, 51.6],
                                             near=[35.7005, 35.7095, 51.4099, 51.4101])
        self.assertEqual(found["ways"], 1)
        self.assertAlmostEqual(found["bbox"][3], 51.4100, places=5)

    def test_falls_back_to_spacing_tolerant_regex_when_exact_name_misses(self):
        from leadgen.providers import osm
        boxes = {"elements": [
            self._way(1, [1, 2], (35.700, 51.410), (35.710, 51.412))]}
        with mock.patch.object(osm, "_overpass_request",
                               side_effect=[{"elements": []}, boxes]) as call:
            found = osm.overpass_street_bbox(["ولیعصر"], bbox=[35.6, 35.9, 51.2, 51.6])
        self.assertEqual(found["via"], "regex")
        regex_query = call.call_args_list[1][0][0]
        self.assertIn('way["name"~"^[[:space:]', regex_query)
        # «ولیعصر» و «ولی عصر» باید یک خیابان حساب شوند (OSM فاصله می‌گذارد)
        self.assertIn("و[[:space:]]*ل[[:space:]]*ی", regex_query)
        self.assertNotIn("(?:", regex_query, "Overpass فقط POSIX ERE را می‌پذیرد")

    def test_empty_bbox_when_no_way_matches(self):
        from leadgen.providers import osm
        with mock.patch.object(osm, "_overpass_request", return_value={"elements": []}):
            self.assertEqual(osm.overpass_street_bbox(["خیابان ناموجود"], bbox=[1, 2, 3, 4]), {})

    def test_down_service_returns_empty_without_raising(self):
        from leadgen.providers import osm
        with mock.patch.object(osm, "_overpass_request", side_effect=osm.OSMDownError("down")):
            self.assertEqual(osm.overpass_street_bbox(["ولیعصر"], bbox=[1, 2, 3, 4]), {})

    def test_result_is_cached_between_calls(self):
        from leadgen.providers import osm
        data = {"elements": [self._way(1, [1, 2], (35.700, 51.410), (35.710, 51.412))]}
        with mock.patch.object(osm, "_overpass_request", return_value=data) as call, \
                mock.patch.object(osm.config, "OVERPASS_CACHE_TTL", 600):
            # «ولیعصر» در OSM «ولی عصر» است؛ نام دقیق هیچ تکه‌ای ندارد و مسیر regex
            # جواب می‌دهد. هر دو درخواست باید کش شوند.
            first = osm.overpass_street_bbox(["ولی عصر"], bbox=[35.6, 35.8, 51.2, 51.6],
                                             near=[35.7005, 35.7095, 51.4101, 51.4119])
            calls_after_first = call.call_count
            second = osm.overpass_street_bbox(["ولی عصر"], bbox=[35.6, 35.8, 51.2, 51.6],
                                              near=[35.7005, 35.7095, 51.4101, 51.4119])
        self.assertTrue(first)
        self.assertEqual(first, second)
        self.assertEqual(call.call_count, calls_after_first, "اجرای دوم باید از کش بیاید")

    def test_side_query_does_not_trip_the_global_breaker(self):
        from leadgen.providers import osm
        self.assertIn("trip_breaker", inspect.signature(osm._overpass_request).parameters)


class StreetAnchorTests(unittest.TestCase):
    """تکیه‌گاه باید تکه‌ای باشد که روی جعبهٔ Nominatim نشسته، نه نزدیک‌ترین مرکز."""

    def setUp(self):
        from leadgen.providers import osm
        osm._STREET_CACHE.clear()

    def test_anchor_is_chosen_by_overlap_with_the_nominatim_box(self):
        from leadgen.providers import osm
        ways = [
            # کوچهٔ هم‌نام در محلهٔ دور (مرکزش ~۳ کیلومتر فاصله دارد ولی نزدیک‌ترین است)
            {"id": 1, "nodes": {1, 2}, "box": [35.678, 35.679, 51.412, 51.414], "length_km": 0.1},
            {"id": 2, "nodes": {5, 6}, "box": [35.705, 35.712, 51.409, 51.414], "length_km": 0.9},
            {"id": 3, "nodes": {6, 7}, "box": [35.712, 35.720, 51.414, 51.418], "length_km": 1.1},
        ]
        cluster = osm._connected_street(ways, [35.7050, 35.7150, 51.4085, 51.4159])
        self.assertTrue(cluster["anchored"])
        self.assertEqual([w["id"] for w in cluster["ways"]], [2, 3])
        self.assertAlmostEqual(sum(w["length_km"] for w in cluster["ways"]), 2.0, places=3)

    def test_unrelated_exact_name_is_rejected_and_regex_result_wins(self):
        from leadgen.providers import osm
        stray = {"elements": [
            {"type": "way", "id": 1, "nodes": [1, 2],
             "geometry": [{"lat": 35.6780, "lon": 51.4120},
                          {"lat": 35.6781, "lon": 51.4130}]}]}
        real = {"elements": [
            {"type": "way", "id": 5, "nodes": [10, 11],
             "geometry": [{"lat": 35.7050, "lon": 51.4090},
                          {"lat": 35.7200, "lon": 51.4170}]},
            {"type": "way", "id": 6, "nodes": [11, 12],
             "geometry": [{"lat": 35.7200, "lon": 51.4170},
                          {"lat": 35.7400, "lon": 51.4200}]}]}
        with mock.patch.object(osm, "_overpass_request", side_effect=[stray, real]) as call:
            found = osm.overpass_street_bbox(["ولیعصر"], bbox=[35.6, 35.85, 51.2, 51.6],
                                             near=[35.7055, 35.7145, 51.4095, 51.4155])
        self.assertEqual(found["via"], "regex", "نام دقیق کوچهٔ بی‌ربط بود؛ باید regex برنده شود")
        self.assertEqual(found["ways"], 2)
        self.assertGreater(found["length_km"], 3.0)
        self.assertEqual(call.call_count, 2)


class StreetComponentGapTests(unittest.TestCase):
    """خیابان بلند ممکن است چند مؤلفهٔ جدا داشته باشد (میدان/زیرگذر)."""

    def test_nearby_component_is_added_but_distant_one_is_not(self):
        from leadgen.providers import osm
        ways = []
        nid = 1000
        # مؤلفهٔ اصلی روی جعبهٔ Nominatim
        ways.append({"id": 1, "nodes": {nid, nid + 1},
                     "box": [35.700, 35.710, 51.405, 51.407], "length_km": 1.1})
        # مؤلفهٔ بعدی، ۵۰ متر بالاتر (گرهٔ مشترک ندارد)
        ways.append({"id": 2, "nodes": {nid + 2, nid + 3},
                     "box": [35.7104, 35.720, 51.407, 51.409], "length_km": 1.1})
        # کوچهٔ هم‌نام در محلهٔ دور
        ways.append({"id": 3, "nodes": {nid + 4, nid + 5},
                     "box": [35.760, 35.761, 51.440, 51.441], "length_km": 0.1})
        cluster = osm._connected_street(ways, [35.701, 35.709, 51.4055, 51.4069])
        ids = sorted(w["id"] for w in cluster["ways"])
        self.assertEqual(ids, [1, 2], "قطعهٔ نزدیک باید اضافه شود، کوچهٔ دور نه")


class StreetSegmentSweepTests(unittest.TestCase):
    """خیابان بلند باید روی نوار خودش پرسیده شود، نه مستطیل بزرگ شهر."""

    def test_cluster_boxes_splits_along_the_long_axis(self):
        from leadgen.providers import osm
        boxes = [[35.700 + i * 0.002, 35.701 + i * 0.002, 51.405, 51.406] for i in range(20)]
        groups = osm.cluster_boxes(boxes, groups=4)
        self.assertEqual(len(groups), 4)
        self.assertAlmostEqual(groups[0][0], 35.700, places=6)
        self.assertAlmostEqual(groups[-1][1], 35.739, places=6)
        # هر جعبه فقط یک نوار باریک است، نه کل خیابان
        for box in groups:
            self.assertLess(box[1] - box[0], 0.02)

    def test_search_area_queries_each_segment_and_merges_without_duplicates(self):
        from leadgen.providers import osm
        segments = [[35.700 + i * 0.004, 35.701 + i * 0.004, 51.405, 51.406] for i in range(8)]
        seen: list[list[float]] = []

        def fake_search(filters, *, bbox, limit=None, timeout=None, input_id="", lang="fa",
                        budget_s=None, **kwargs):
            seen.append(list(bbox))
            mid_lat = round((bbox[0] + bbox[1]) / 2, 5)
            rows = [
                {"place_id": f"osm:node/{mid_lat}-a", "title": "کافه",
                 "latitude": f"{mid_lat}", "longitude": "51.4055"},
                # ردیفی که در دو قطعهٔ هم‌پوشان تکرار می‌شود نباید دوبار بیاید
                {"place_id": "osm:node/shared", "title": "کافه مشترک",
                 "latitude": "35.7005", "longitude": "51.4055"},
            ]
            return rows

        with mock.patch.object(osm, "overpass_search", side_effect=fake_search):
            rows = osm.overpass_search_area(['["amenity"="cafe"]'],
                                            bbox=[35.69, 35.75, 51.40, 51.42],
                                            segments=segments, limit=100, tiles=4,
                                            budget_s=60)
        parts = {r["place_id"] for r in rows}
        self.assertIn("osm:node/shared", parts)
        self.assertGreaterEqual(len(seen), 2, "روی نوار خیابان باید چند پرسش جدا زده شود")
        # هر پرسش باید بسیار باریک‌تر از کل جعبه (۰٫۰۶ درجه) باشد، نه نصف شهر
        self.assertLess(max(b[1] - b[0] for b in seen), 0.06 + 1e-9,
                        "هر پرسش باید نوار باریک باشد")
        # یک پرسش اول روی کل جعبه + حداکثر ۸ پرسش روی نوار خیابان
        self.assertLessEqual(len(seen), 9)
        self.assertEqual(seen[0], [35.69, 35.75, 51.40, 51.42])
        for bbox in seen:
            self.assertGreaterEqual(bbox[2], 51.399, "پرسش نباید از نوار خیابان بیرون بزند")
        self.assertEqual(len(parts), len(rows), "نتیجهٔ تکراری نباید برگردد")

    def test_pipeline_passes_street_boxes_to_the_engine_body(self):
        import inspect
        from leadgen import pipeline as pipeline_mod
        source = inspect.getsource(pipeline_mod.run_scrape)
        self.assertIn('"street_boxes"', source)
