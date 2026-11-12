"""آزمون نقشهٔ صنف → تگ OSM و مسیر Overpass (بدون شبکه)."""
from __future__ import annotations

import unittest
from unittest import mock

from leadgen import config, niches, osm_tags
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
