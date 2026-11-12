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
