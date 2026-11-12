"""آزمون سرتاسری موتور OSM با یک Nominatim جعلی روی HTTP.

چرا: در سندباکس، TLS به nominatim.openstreetmap.org بسته است؛ پس نمی‌توان اجرای
واقعی را اینجا دید. این آزمون دقیقاً همان مسیری را اجرا می‌کند که در تولید اجرا
می‌شود (ساخت URL، viewbox، پارس پاسخ، راهبردهای چندمرحله‌ای، امتیازدهی، خروجی).

پاسخ‌های جعلی، پاسخ‌های **واقعی** Nominatim برای همان پرسش‌ها هستند (با همان
شکل داده)، که در ۲۰۲۶-۱۰-۲۶ گرفته شدند:

    «آموزشگاه موسیقی و هنر» در مهاباد → []
    «music school»           در مهاباد → []
    «موسیقی»                 در مهاباد → یک آموزشگاه موسیقی (Kazhaw)
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from leadgen import config, engine, pipeline
from leadgen.providers import osm

KAZHAW = {
    "place_id": 208555156, "osm_type": "node", "osm_id": 7688799085,
    "lat": "36.7617440", "lon": "45.7170445", "category": "amenity",
    "type": "music_school", "name": "Kazhaw Music Academy",
    "display_name": "Kazhaw Music Academy, وفایی, Mahabad, Iran",
    "address": {"road": "وفایی", "city": "Mahabad", "state": "West Azerbaijan Province",
                "country_code": "ir"},
    "extratags": {
        "contact:instagram": "https://www.instagram.com/kazhaw_music_academy/",
        "contact:facebook": "https://www.facebook.com/Kazhaw_music_academy/",
    },
    "boundingbox": ["36.7616940", "36.7617940", "45.7169945", "45.7170945"],
}

# پاسخ‌های واقعی: عبارت کامل فارسی یا انگلیسی → خالی؛ کلمهٔ کلیدی «موسیقی» → یک لید
ANSWERS = {
    "آموزشگاه موسیقی و هنر": [],
    "آموزشگاه موسیقی": [],
    "music school": [],
    "musical instrument": [],
    "موسیقی": [KAZHAW],
}

SEEN: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):                                  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        SEEN.append(params)
        if parsed.path == "/status":
            body = {"status": 0, "message": "OK", "data_updated": "2026-10-20T00:00:00Z"}
        elif parsed.path == "/search":
            body = ANSWERS.get((params.get("q") or "").strip(), [])
        else:
            body = {}
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


class FakeNominatimTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        SEEN.clear()
        self.saved = (config.OSM_ENDPOINT, config.OSM_MIN_INTERVAL, config.DATA_DIR,
                      config.DB_PATH, config.EXPORT_DIR)
        config.OSM_ENDPOINT = f"http://127.0.0.1:{self.port}"
        config.OSM_MIN_INTERVAL = 0.001   # 0.0 به ۱.۱ ثانیهٔ پیش‌فرض برمی‌گردد
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp(prefix="leadgen-nominatim-"))
        config.DATA_DIR, config.DB_PATH = tmp, tmp / "leadgen.db"
        config.EXPORT_DIR = tmp / "exports"

    def tearDown(self):
        (config.OSM_ENDPOINT, config.OSM_MIN_INTERVAL, config.DATA_DIR,
         config.DB_PATH, config.EXPORT_DIR) = self.saved

    def test_live_pipeline_finds_lead_via_single_word_fallback(self):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "آموزشگاه موسیقی و هنر", "city_query": "مهاباد",
            "province": "west-azarbaijan", "engine_mode": "osm",
            "depth": 4, "limit": 40, "lang": "fa", "emails": False, "socials": False,
        })
        result = pipeline.run_scrape(request)

        self.assertEqual(1, result.stats["total"], result.warnings)
        lead = result.rows[0]
        self.assertEqual("Kazhaw Music Academy", lead["title"])
        self.assertEqual("مهاباد", result.stats["city"])
        self.assertAlmostEqual(36.761744, lead["lat"], places=4)
        # اینستاگرام و فیسبوک از تگ‌های contact:* خوانده می‌شوند
        self.assertIn("instagram.com/kazhaw_music_academy", str(lead.get("instagram", "")))

        queries = [p.get("q") for p in SEEN]
        self.assertIn("موسیقی", queries)
        self.assertTrue(any(p.get("viewbox") for p in SEEN), "جست‌وجوی محدود به شهر لازم است")
        self.assertTrue(any(p.get("bounded") == "1" for p in SEEN), SEEN[:2])

    def test_health_reports_up_against_live_endpoint(self):
        osm.HEALTH_TTL = 0
        status = osm.health(force=True)
        self.assertTrue(status.get("up"), status)

    def test_no_result_returns_clear_message_not_crash(self):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "آموزشگاه رقص باله", "city_query": "مهاباد",
            "engine_mode": "osm", "depth": 3, "limit": 20, "lang": "fa",
        })
        result = pipeline.run_scrape(request)
        self.assertEqual(0, result.stats["total"])
        self.assertTrue(result.stats.get("empty"))
        self.assertTrue(any("چیزی پیدا نشد" in w for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
