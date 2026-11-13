"""تست موتورهای دادهٔ واقعیِ بدون داکر: OpenStreetMap (Nominatim) و Google Places.

هیچ درخواست شبکه‌ای زده نمی‌شود: پاسخ واقعیِ ضبط‌شده به‌عنوان نمونه استفاده می‌شود و
لایهٔ شبکه monkeypatch می‌شود. هدف، تضمین «نقشهٔ درست داده» است، نه تست سرویس بیرونی.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leadgen import config, engine  # noqa: E402
from leadgen.providers import osm, places  # noqa: E402
from unittest import mock  # noqa: E402


class NoOverpassMixin:
    """Overpass را در این آزمون‌ها خاموش می‌کند.

    چرا؟ روی رانر CI اینترنت هست و Overpass واقعاً جواب می‌دهد؛ در سندباکس توسعه
    نه. بدون این خاموشی، نتیجهٔ آزمون به شبکهٔ محیط وابسته می‌شد (یک بار سبز، یک
    بار قرمز). مسیر Overpass آزمون‌های قطعی خودش را در tests/test_osm_tags.py دارد.
    """

    def setUp(self):
        self._overpass_off = [
            mock.patch.object(osm, "overpass_search_area", return_value=[]),
            mock.patch.object(osm, "overpass_search", return_value=[]),
        ]
        for patcher in self._overpass_off:
            patcher.start()

    def tearDown(self):
        for patcher in self._overpass_off:
            patcher.stop()

# نمونهٔ واقعی پاسخ Nominatim برای «دندانپزشکی در تهران» (فیلدها کوتاه شده‌اند)
NOMINATIM_ITEM = {
    "place_id": 421357302, "osm_type": "node", "osm_id": 13983362079,
    "lat": "35.6889866", "lon": "50.9685245",
    "category": "amenity", "type": "dentist",
    "name": "مطب دندانپزشکی دکتر وحید جلایی",
    "display_name": "مطب دندانپزشکی دکتر وحید جلایی, Dr Hesabi Boulevard, Malard, Iran",
    "address": {"road": "Dr Hesabi Boulevard", "city": "Malard", "state": "Tehran Province",
                "country_code": "ir"},
    "extratags": {
        "phone": "02182808343", "mobile": "+989198725308",
        "website": "https://jala-dental.com/",
        "contact:instagram": "https://www.instagram.com/doctorvahidjalayi",
        "opening_hours": "24/7", "healthcare": "dentist",
        "description": "دندانپزشکی تخصصی",
    },
}

PLACES_ITEM = {
    "id": "ChIJ_test_123", "displayName": {"text": "کلینیک دندانپزشکی نمونه"},
    "formattedAddress": "تهران، خیابان ولیعصر", "nationalPhoneNumber": "02112345678",
    "websiteUri": "https://example-dental.ir/", "rating": 4.6, "userRatingCount": 212,
    "location": {"latitude": 35.7448, "longitude": 51.4105},
    "types": ["dentist", "health"], "googleMapsUri": "https://maps.google.com/?cid=1",
    "businessStatus": "OPERATIONAL", "regularOpeningHours": {"weekdayDescriptions": ["دوشنبه ۹–۱۸"]},
}


class OSMProviderTest(NoOverpassMixin, unittest.TestCase):
    def test_row_from_item_maps_every_field(self):
        row = osm.row_from_item(NOMINATIM_ITEM, input_id="دندانپزشکی")
        assert row is not None
        self.assertEqual("مطب دندانپزشکی دکتر وحید جلایی", row["title"])
        self.assertEqual("02182808343", row["phone"])           # phone بر mobile مقدم است
        self.assertEqual("https://jala-dental.com/", row["website"])
        self.assertIn("instagram.com", row["instagram"])
        self.assertEqual("35.688987", row["latitude"])          # گرد شده
        self.assertEqual("50.968525", row["longitude"])
        self.assertEqual("osm:node/13983362079", row["place_id"])
        self.assertEqual("دندانپزشکی", row["category"])
        self.assertIn("Dr Hesabi", row["address"])
        self.assertEqual("24/7", row["open_hours"])
        self.assertEqual("openstreetmap", row["source"])

    def test_row_without_name_is_dropped(self):
        bare = dict(NOMINATIM_ITEM, name="")
        self.assertIsNone(osm.row_from_item(bare))

    def test_clean_term_strips_place_but_keeps_trade(self):
        self.assertEqual("دندانپزشکی", osm.clean_term("دندانپزشکی در تهران"))
        self.assertEqual("plumber", osm.clean_term("plumber in Denver CO"))
        self.assertEqual("کافه", osm.clean_term("کافه"))
        self.assertEqual("", osm.clean_term(""))

    def test_viewbox_order_is_w_n_e_s(self):
        # Nominatim: viewbox=<چپ>,<بالا>,<راست>,<پایین>
        self.assertEqual("51.300000,35.800000,51.500000,35.650000",
                         osm.viewbox_param([35.65, 35.80, 51.30, 51.50]))

    def test_bbox_from_radius_is_sane(self):
        s, n, w, e = osm._bbox_from_radius(35.7, 51.4, 10000)
        self.assertLess(s, 35.7)
        self.assertGreater(n, 35.7)
        self.assertLess(w, 51.4)
        self.assertGreater(e, 51.4)

    def test_search_builds_bounded_query(self):
        seen: dict = {}

        def fake_http(url, *, timeout):
            seen["url"] = url
            return [NOMINATIM_ITEM]

        original = osm._http_json
        osm._http_json = fake_http
        osm._LAST_CALL[0] = 0.0          # بدون تأخیر در تست
        try:
            rows = osm.search("دندانپزشکی", bbox=[35.6, 35.8, 51.3, 51.5], country="ir", limit=10)
        finally:
            osm._http_json = original
        self.assertEqual(1, len(rows))
        self.assertIn("bounded=1", seen["url"])
        self.assertIn("viewbox=", seen["url"])
        self.assertIn("countrycodes=ir", seen["url"])
        self.assertIn("limit=10", seen["url"])

    def test_search_can_take_a_second_page_when_the_service_caps_at_40(self):
        """شهر شلوغ: صفحهٔ دوم با exclude_place_ids گرفته می‌شود (سقف ۴۰ در هر درخواست)."""
        calls: list[str] = []

        def fake_http(url, *, timeout):
            calls.append(url)
            if len(calls) == 1:
                return [dict(NOMINATIM_ITEM, osm_id=1000 + i) for i in range(40)]
            return [dict(NOMINATIM_ITEM, osm_id=2000 + i) for i in range(10)]

        original = osm._http_json
        osm._http_json = fake_http
        osm._LAST_CALL[0] = 0.0
        try:
            rows = osm.search("cafe", bbox=[35.6, 35.8, 51.3, 51.5], country="ir",
                              limit=80, pages=2)
        finally:
            osm._http_json = original
        self.assertEqual(len(calls), 2)
        self.assertNotIn("exclude_place_ids", calls[0])
        self.assertIn("exclude_place_ids=", calls[1])
        self.assertIn("limit=40", calls[1])          # هر صفحه سقف ۴۰، نه ۸۰
        self.assertEqual(len(rows), 50)

    def test_search_does_not_ask_for_a_second_page_when_the_first_is_short(self):
        calls: list[str] = []

        def fake_http(url, *, timeout):
            calls.append(url)
            return [dict(NOMINATIM_ITEM, osm_id=3000 + i) for i in range(7)]

        original = osm._http_json
        osm._http_json = fake_http
        osm._LAST_CALL[0] = 0.0
        try:
            rows = osm.search("cafe", bbox=[35.6, 35.8, 51.3, 51.5], country="ir",
                              limit=80, pages=3)
        finally:
            osm._http_json = original
        self.assertEqual(len(calls), 1, "صفحهٔ کوتاه یعنی داده تمام شده")
        self.assertEqual(len(rows), 7)

    def test_search_row_cap_is_page_size_times_pages(self):
        calls: list[int] = []

        def fake_http(url, *, timeout):
            calls.append(1)
            base = 4000 + 100 * len(calls)
            return [dict(NOMINATIM_ITEM, osm_id=base + i) for i in range(40)]

        original = osm._http_json
        osm._http_json = fake_http
        osm._LAST_CALL[0] = 0.0
        try:
            rows = osm.search("cafe", bbox=[35.6, 35.8, 51.3, 51.5], country="ir",
                              limit=40, pages=3)
        finally:
            osm._http_json = original
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(rows), 120)          # ۳ صفحه × ۴۰

    def test_health_reports_down_on_network_error(self):
        original = osm._http_json
        osm._http_json = lambda url, timeout: (_ for _ in ()).throw(osm.OSMDownError("boom"))
        osm._HEALTH_CACHE.update({"ts": 0.0, "payload": None})
        try:
            payload = osm.health(force=True)
        finally:
            osm._http_json = original
        self.assertFalse(payload["up"])
        self.assertIn("boom", payload["error"])

    def test_osm_engine_produces_rows_without_docker(self):
        original = osm.search
        osm.search = lambda term, **kw: [osm.row_from_item(NOMINATIM_ITEM, input_id=term)]
        try:
            eng = engine.OSMEngine()
            events: list[dict] = []
            job_id, rows = eng.run_job({
                "name": "leadgen-c1", "keywords": ["دندانپزشکی در تهران", "کلینیک دندان"],
                "lat": "35.7", "lon": "51.4", "radius": 8000, "max_time": 40, "lang": "fa",
                # نتیجهٔ نمونه در ملارد است؛ محدوده هم همان‌جاست تا سد «فقط همان منطقه» عادی کار کند
                "bbox": [35.62, 35.75, 50.90, 51.03],
            }, on_progress=events.append)
        finally:
            osm.search = original
        self.assertEqual(1, len(rows))
        self.assertEqual("leadgen-c1", job_id)
        self.assertTrue(any(e.get("percent") == 100 for e in events))

    def test_osm_engine_raises_with_clear_message_when_down(self):
        original = osm.search

        def boom(term, **kw):
            raise osm.OSMDownError("شبکه قطع است")

        osm.search = boom
        try:
            with self.assertRaises(engine.EngineError) as ctx:
                engine.OSMEngine().run_job({"keywords": ["کافه در تهران"], "max_time": 20})
        finally:
            osm.search = original
        self.assertIn("OSM", str(ctx.exception))


class OSMRobustnessTest(NoOverpassMixin, unittest.TestCase):
    """سناریوهایی که در تولید واقعاً پیش آمد: شهر کوچک و رابط انگلیسی."""

    def test_empty_result_is_not_engine_error_but_empty_result(self):
        original = osm.search
        osm.search = lambda term, **kw: []
        try:
            with self.assertRaises(engine.EmptyResult) as ctx:
                engine.OSMEngine().run_job({
                    "keywords": ["آموزشگاه موسیقی و هنر در مهاباد"],
                    "city_label": "مهاباد", "max_time": 30, "lang": "en",
                })
        finally:
            osm.search = original
        self.assertIn("چیزی پیدا نشد", str(ctx.exception))
        self.assertTrue(ctx.exception.tried)          # می‌گوید چه چیزهایی را امتحان کرد

    def test_falls_back_to_text_query_with_place_name(self):
        calls: list[str] = []

        def fake_search(term, **kw):
            calls.append(term)
            if "ملارد" in term:                        # فقط «term + place» نتیجه بده
                return [osm.row_from_item(NOMINATIM_ITEM, input_id=term)]
            return []

        original = osm.search
        osm.search = fake_search
        try:
            _, rows = engine.OSMEngine().run_job({
                "keywords": ["کلینیک دندان در ملارد"], "city_label": "ملارد",
                "bbox": [35.60, 35.80, 50.90, 51.05], "max_time": 40, "lang": "fa",
            })
        finally:
            osm.search = original
        self.assertEqual(1, len(rows))
        self.assertTrue(any("ملارد" in c for c in calls), calls)

    def test_english_request_adds_persian_niche_terms(self):
        seen: list[str] = []

        def fake_search(term, **kw):
            seen.append(term)
            return []

        from leadgen import niches
        niche = niches.find_niche("موسیقی")
        original = osm.search
        osm.search = fake_search
        try:
            with self.assertRaises(engine.EmptyResult):
                engine.OSMEngine().run_job({
                    "keywords": ["music school in Mahabad"], "city_label": "Mahabad",
                    "max_time": 25, "lang": "en", "_niche": niche,
                })
        finally:
            osm.search = original
        self.assertTrue(any("\u0600" <= ch <= "\u06FF" for term in seen for ch in term),
                        f"هیچ کلیدواژهٔ فارسی امتحان نشد: {seen}")

    def test_within_rejects_far_rows(self):
        row = {"latitude": "35.70", "longitude": "51.40"}
        self.assertTrue(osm.within(row, [35.60, 35.80, 51.30, 51.50]))
        self.assertFalse(osm.within({"latitude": "36.30", "longitude": "45.70"},
                                    [35.60, 35.80, 51.30, 51.50]))


class EnginePinTest(unittest.TestCase):
    """پین سرور (ENGINE_MODE) بر درخواست «auto» رابط اولویت دارد."""

    def setUp(self):
        self.saved = config.ENGINE_MODE

    def tearDown(self):
        config.ENGINE_MODE = self.saved

    def test_auto_request_honours_server_pin(self):
        config.ENGINE_MODE = "demo"
        self.assertEqual("demo", engine.make_engine("auto").name)
        self.assertEqual("demo", engine.make_engine("").name)
        self.assertEqual("demo", engine._resolve_mode(None))

    def test_explicit_request_beats_pin(self):
        config.ENGINE_MODE = "demo"
        self.assertEqual("osm", engine.make_engine("osm").name)

    def test_auto_pin_keeps_chain(self):
        config.ENGINE_MODE = "auto"
        self.assertEqual("auto", engine._resolve_mode("auto"))


class OSMTermsTest(NoOverpassMixin, unittest.TestCase):
    """ترجمهٔ صنف فارسی به اصطلاح دسته‌ای انگلیسی + کلمهٔ کلیدی تنها."""

    def test_music_niche_maps_to_osm_categories(self):
        from leadgen import osm_terms
        terms = osm_terms.derive(["آموزشگاه موسیقی و هنر", "کلاس گیتار"])
        self.assertIn("music school", terms)
        self.assertIn("musical instrument", terms)

    def test_common_trades_map(self):
        from leadgen import osm_terms
        self.assertIn("dentist", osm_terms.derive(["دندانپزشکی"]))
        self.assertIn("cafe", osm_terms.derive(["کافه"]))
        self.assertTrue(any(t.startswith("car") for t in osm_terms.derive(["تعمیرگاه خودرو"])))

    def test_term_variants_drop_generic_words(self):
        from leadgen import osm_terms
        # «هنر» به‌تنهایی فقط گالری/پارک می‌آورد، پس کنار گذاشته می‌شود
        self.assertEqual(["موسیقی"], osm_terms.term_variants("آموزشگاه موسیقی و هنر"))
        self.assertEqual([], osm_terms.term_variants("موسیقی"))

    def test_non_business_osm_types_are_not_leads(self):
        """پارک/اثر هنری/بنای تاریخی نباید به‌عنوان کسب‌وکار ذخیره شود."""
        for category, kind in (("leisure", "park"), ("tourism", "artwork"),
                               ("historic", "memorial"), ("place", "suburb")):
            item = {"place_id": 7, "osm_type": "way", "osm_id": 7, "lat": "35.7", "lon": "51.4",
                    "category": category, "type": kind, "name": "پارک هنر",
                    "display_name": "پارک هنر, تهران", "address": {}, "extratags": {}}
            self.assertIsNone(osm.row_from_item(item, input_id="هنر"), f"{category}/{kind}")
        shop = {"place_id": 8, "osm_type": "node", "osm_id": 8, "lat": "35.7", "lon": "51.4",
                "category": "shop", "type": "musical_instrument", "name": "فروشگاه ساز",
                "display_name": "فروشگاه ساز, تهران", "address": {}, "extratags": {}}
        self.assertIsNotNone(osm.row_from_item(shop, input_id="ساز"))

    def test_engine_tries_category_and_single_word_when_phrase_is_thin(self):
        """سناریوی واقعی مهاباد: عبارت کامل صفر، «موسیقی» یک لید واقعی."""
        seen: list[str] = []
        kazhaw = {
            "place_id": 208555156, "osm_type": "node", "osm_id": 7688799085,
            "lat": "36.7617440", "lon": "45.7170445", "category": "amenity",
            "type": "music_school", "name": "Kazhaw Music Academy",
            "display_name": "Kazhaw Music Academy, وفایی, Mahabad, Iran",
            "address": {"city": "Mahabad"}, "extratags": {
                "contact:instagram": "https://www.instagram.com/kazhaw_music_academy/",
                "contact:facebook": "https://www.facebook.com/Kazhaw_music_academy/",
            },
        }

        def fake_search(term, **kw):
            seen.append(term)
            return [osm.row_from_item(kazhaw, input_id=term)] if term == "موسیقی" else []

        original = osm.search
        osm.search = fake_search
        try:
            _, rows = engine.OSMEngine().run_job({
                "keywords": ["آموزشگاه موسیقی و هنر در مهاباد"], "city_label": "مهاباد",
                "bbox": [36.66, 36.86, 45.60, 45.84], "max_time": 40, "lang": "fa",
                "osm_terms": ["music school", "musical instrument"],
                "province_label": "آذربایجان غربی",
                "province_bbox": [36.66, 38.65, 44.64, 46.22],
            })
        finally:
            osm.search = original
        self.assertEqual(1, len(rows))
        self.assertEqual("Kazhaw Music Academy", rows[0]["title"])
        self.assertIn("music school", seen)      # اصطلاح دسته‌ای امتحان شد
        self.assertIn("موسیقی", seen)            # و کلمهٔ کلیدی تنها


class EmptyRunPipelineTest(NoOverpassMixin, unittest.TestCase):
    """اجرای خالی باید «تمام‌شده با صفر لید» ثبت شود، نه «ناموفق»."""

    def test_empty_provider_completes_run_gracefully(self):
        import tempfile
        from pathlib import Path

        from leadgen import config, pipeline

        # مسیرها را روی خودِ ماژول عوض می‌کنیم (env بعد از import اثر ندارد و
        # باعث می‌شد اجرای تست، پایگاه‌دادهٔ واقعیِ data/ را آلوده کند)
        tmp = Path(tempfile.mkdtemp(prefix="leadgen-empty-"))
        saved = (config.DATA_DIR, config.DB_PATH, config.EXPORT_DIR)
        config.DATA_DIR, config.DB_PATH = tmp, tmp / "leadgen.db"
        config.EXPORT_DIR = tmp / "exports"
        original_search, original_health = osm.search, engine.OSMEngine.health
        osm.search = lambda term, **kw: []
        engine.OSMEngine.health = lambda self: {"up": True}
        try:
            request = pipeline.ScrapeRequest.from_dict({
                "niche_query": "آموزشگاه موسیقی و هنر", "city_query": "مهاباد",
                "engine_mode": "osm", "depth": 3, "limit": 50, "lang": "fa",
            })
            events: list[dict] = []
            result = pipeline.run_scrape(request, on_progress=events.append)
            self.assertEqual(0, result.stats["total"])
            self.assertTrue(result.stats.get("empty"))
            self.assertEqual("done", result.run.get("status"))
            self.assertTrue(any("چیزی پیدا نشد" in w for w in result.warnings))
            self.assertEqual("done", events[-1].get("stage"))
        finally:
            osm.search, engine.OSMEngine.health = original_search, original_health
            config.DATA_DIR, config.DB_PATH, config.EXPORT_DIR = saved


class PlacesProviderTest(unittest.TestCase):
    def test_row_from_place(self):
        row = places.row_from_place(PLACES_ITEM, input_id="دندانپزشکی")
        assert row is not None
        self.assertEqual("کلینیک دندانپزشکی نمونه", row["title"])
        self.assertEqual("02112345678", row["phone"])
        self.assertEqual(4.6, row["review_rating"])
        self.assertEqual(212, row["review_count"])
        self.assertEqual("35.7448", row["latitude"])
        self.assertEqual("gp:ChIJ_test_123", row["place_id"])
        self.assertEqual("google_places", row["source"])
        self.assertEqual("operational", row["status"])

    def test_available_follows_config(self):
        original = config.PLACES_API_KEY
        try:
            config.PLACES_API_KEY = ""
            self.assertFalse(places.available())
            config.PLACES_API_KEY = "key"
            self.assertTrue(places.available())
        finally:
            config.PLACES_API_KEY = original

    def test_places_engine_with_stubbed_search(self):
        original = places.search
        places.search = lambda term, **kw: [places.row_from_place(PLACES_ITEM, input_id=term)]
        try:
            _, rows = engine.PlacesEngine().run_job({
                "keywords": ["دندانپزشکی"], "lat": "35.7", "lon": "51.4",
                "radius": 5000, "lang": "fa", "max_time": 40,
            })
        finally:
            places.search = original
        self.assertEqual(1, len(rows))
        self.assertEqual("کلینیک دندانپزشکی نمونه", rows[0]["title"])


class PlacesSearchShapeTest(unittest.TestCase):
    """Places باید محدودهٔ سخت (مستطیل) بگیرد، نه دایرهٔ ۱۰ کیلومتری."""

    def _run(self, body):
        captured = []

        def fake_post(payload, *, timeout=0.0):
            captured.append(dict(payload))
            if "pageToken" not in payload:
                return {"places": [PLACES_ITEM], "nextPageToken": "tok"}
            second = dict(PLACES_ITEM, id="ChIJ_second", displayName={"text": "کلینیک دوم"})
            return {"places": [second]}

        original = places._post
        places._post = fake_post
        try:
            rows = places.search(**body)
        finally:
            places._post = original
        return rows, captured

    def test_bbox_becomes_rectangle_restriction(self):
        _, sent = self._run({"term": "کافه در ولیعصر", "bbox": [35.70, 35.72, 51.40, 51.42]})
        rect = sent[0]["locationRestriction"]["rectangle"]
        self.assertAlmostEqual(35.70, rect["low"]["latitude"])
        self.assertAlmostEqual(35.72, rect["high"]["latitude"])
        self.assertAlmostEqual(51.40, rect["low"]["longitude"])
        self.assertAlmostEqual(51.42, rect["high"]["longitude"])
        self.assertNotIn("locationBias", sent[0], "مستطیل سخت نباید با دایره قاطی شود")

    def test_no_bbox_keeps_soft_circle_bias(self):
        _, sent = self._run({"term": "کافه", "lat": 35.7, "lon": 51.4, "radius": 3000})
        self.assertNotIn("locationRestriction", sent[0])
        circle = sent[0]["locationBias"]["circle"]
        self.assertEqual(3000, circle["radius"])

    def test_pagination_follows_next_page_token(self):
        rows, sent = self._run({"term": "کافه", "bbox": [35.70, 35.72, 51.40, 51.42],
                                "limit": 20, "pages": 2, "page_wait": 0})
        self.assertEqual(2, len(sent))
        self.assertEqual("tok", sent[1]["pageToken"])
        self.assertEqual(2, len(rows), "صفحهٔ دوم نباید ردیف تکراری بسازد")

    def test_pages_respect_limit(self):
        rows, _ = self._run({"term": "کافه", "bbox": [35.70, 35.72, 51.40, 51.42],
                             "limit": 1, "pages": 3, "page_wait": 0})
        self.assertEqual(1, len(rows))

    def test_engine_searches_each_street_in_its_own_rectangle(self):
        calls = []

        def fake_search(term, **kwargs):
            calls.append((term, kwargs.get("bbox")))
            row = places.row_from_place(PLACES_ITEM, input_id=term)
            row["place_id"] = f"gp:{len(calls)}"
            return [row]

        original = places.search
        places.search = fake_search
        try:
            _, rows = engine.PlacesEngine().run_job({
                "keywords": ["کافه در ولیعصر + انقلاب، تهران"],
                "area_label": "ولیعصر + انقلاب، تهران",
                "street_areas": [
                    {"name": "خیابان ولیعصر", "bbox": [35.70, 35.72, 51.40, 51.42],
                     "boxes": [[35.70, 35.72, 51.40, 51.42]]},
                    {"name": "خیابان انقلاب", "bbox": [35.69, 35.71, 51.43, 51.45],
                     "boxes": [[35.69, 35.71, 51.43, 51.45]]},
                ],
                "lat": "35.7", "lon": "51.4", "radius": 5000, "lang": "fa",
                "max_time": 60, "limit": 20, "name": "multi",
            })
        finally:
            places.search = original
        self.assertEqual(2, len(calls))
        self.assertIn("ولیعصر", calls[0][0])
        self.assertNotIn("انقلاب", calls[0][0])
        self.assertEqual([35.6994, 35.7206, 51.3994, 51.4206], [round(x, 4) for x in calls[0][1]])
        self.assertIn("انقلاب", calls[1][0])
        self.assertAlmostEqual(51.43, calls[1][1][2], places=2)
        self.assertEqual(2, len(rows))


class EngineChainTest(unittest.TestCase):
    def test_explicit_modes(self):
        self.assertEqual("demo", engine.make_engine("demo").name)
        self.assertEqual("docker", engine.make_engine("docker").name)
        self.assertEqual("osm", engine.make_engine("osm").name)
        self.assertEqual("places", engine.make_engine("places").name)

    def test_auto_prefers_free_osm_and_uses_places_only_as_reserve(self):
        original_key, original_mode = config.PLACES_API_KEY, config.ENGINE_MODE
        try:
            config.PLACES_API_KEY = "test-key"
            config.ENGINE_MODE = "auto"     # بدون پین، «auto» یعنی زنجیرهٔ خودکار
            with mock.patch.object(engine.OSMEngine, "health", return_value={"up": True}), \
                    mock.patch.object(engine.DockerEngine, "health", return_value={"up": False}):
                self.assertEqual("osm", engine.make_engine("auto").name,
                                 "OSM رایگان است؛ Places نباید هر اجرا هزینه بسازد")
                self.assertEqual("places", engine.make_engine("places").name,
                                 "انتخاب صریح Places همیشه برنده است")
        finally:
            config.PLACES_API_KEY, config.ENGINE_MODE = original_key, original_mode

    def test_auto_falls_back_to_places_when_osm_is_down(self):
        original_key, original_mode = config.PLACES_API_KEY, config.ENGINE_MODE
        try:
            config.PLACES_API_KEY = "test-key"
            config.ENGINE_MODE = "auto"
            with mock.patch.object(engine.OSMEngine, "health", return_value={"up": False}), \
                    mock.patch.object(engine.DockerEngine, "health", return_value={"up": False}):
                self.assertEqual("places", engine.make_engine("auto").name)
        finally:
            config.PLACES_API_KEY, config.ENGINE_MODE = original_key, original_mode

    def test_status_has_honest_chain(self):
        status = engine.engine_status("demo")
        self.assertEqual("demo", status["active"])
        self.assertFalse(status["live"])
        self.assertEqual(["docker", "osm", "places", "demo"], status["chain"])
        self.assertIn("reason", status)


if __name__ == "__main__":
    unittest.main()
