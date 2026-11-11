"""آزمون پل WSGI — همان API روی میزبان serverless (بدون سوکت، بدون شبکه).

این تست‌ها تضمین می‌کنند استقرار روی Vercel دقیقاً همان قراردادی را سرو کند که
`tests/test_api.py` روی سرور محلی می‌سنجد: کد وضعیت، هدرها، JSON فارسی و فایل‌های رابط.

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

_TMP = Path(tempfile.mkdtemp(prefix="leadgen-wsgi-"))
os.environ["LEADGEN_DATA_DIR"] = str(_TMP)
os.environ["ENGINE_MODE"] = "demo"

from leadgen import config, storage, wsgi  # noqa: E402


def json_body(body: bytes) -> dict:
    return json.loads(body.decode("utf-8"))


class WsgiContractTests(unittest.TestCase):
    """قرارداد HTTP باید روی هر دو میزبان یکی باشد."""

    def test_health_reports_engine_and_db(self):
        code, headers, body = wsgi.call("GET", "/api/health")
        self.assertEqual(code, 200)
        self.assertIn("application/json", headers["content-type"])
        data = json_body(body)
        self.assertTrue(data["ok"])
        self.assertIn(data["engine"]["active"], ("docker", "demo", "unavailable"))
        self.assertIn("db", data)

    def test_catalog_is_complete(self):
        code, _, body = wsgi.call("GET", "/api/catalog")
        self.assertEqual(code, 200)
        data = json_body(body)
        self.assertGreaterEqual(len(data["niches"]), 60)
        self.assertGreaterEqual(len(data["cities"]), 40)
        self.assertTrue(data["gap_labels"])

    def test_unknown_route_is_404_json(self):
        code, _, body = wsgi.call("GET", "/api/definitely-not-here")
        self.assertEqual(code, 404)
        self.assertIn("مسیر پیدا نشد", json_body(body)["error"])

    def test_root_serves_the_smart_ui(self):
        code, headers, body = wsgi.call("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("text/html", headers["content-type"])
        self.assertIn(b"<html", body.lower()[:400])

    def test_path_traversal_is_blocked(self):
        code, _, _ = wsgi.call("GET", "/../leadgen/config.py")
        self.assertIn(code, (400, 404))

    def test_query_string_is_parsed(self):
        code, _, body = wsgi.call("GET", "/api/niches", query="q=" + "%DA%A9%D8%A7%D9%81%D9%87")
        self.assertIn(code, (200, 404))          # مسیر /api/niches کوئری نمی‌خواهد
        code, _, body = wsgi.call("GET", "/api/queue/jobs", query="limit=3")
        self.assertEqual(code, 200)
        self.assertLessEqual(len(json_body(body)["jobs"]), 3)

    def test_invalid_query_is_400_not_500(self):
        code, _, body = wsgi.call("GET", "/api/queue/jobs", query="status=nope")
        self.assertEqual(code, 400)
        self.assertIn("وضعیت نامعتبر", json_body(body)["error"])

    def test_post_json_body_is_read(self):
        payload = json.dumps({"niche_query": "", "city_query": ""}).encode("utf-8")
        code, _, body = wsgi.call("POST", "/api/runs", body=payload,
                                  content_type="application/json")
        self.assertEqual(code, 400)
        self.assertIn("لازم است", json_body(body)["error"])

    def test_malformed_json_is_400(self):
        code, _, body = wsgi.call("POST", "/api/runs", body=b"{not json",
                                  content_type="application/json")
        self.assertEqual(code, 400)
        self.assertIn("JSON", json_body(body)["error"])

    def test_unsupported_method_is_405(self):
        code, _, _ = wsgi.call("OPTIONS", "/api/health")
        self.assertEqual(code, 405)


class WsgiQueueModeTests(unittest.TestCase):
    """در حالت صف (Vercel)، اجرا در صف می‌ماند و ورکرِ جای دیگری تمامش می‌کند."""

    def setUp(self):
        storage.init_db()
        with storage._connect() as conn:
            conn.execute("delete from jobs")
        self._mode = config.QUEUE_MODE
        config.QUEUE_MODE = "always"

    def tearDown(self):
        config.QUEUE_MODE = self._mode
        with storage._connect() as conn:
            conn.execute("delete from jobs")

    def test_run_is_queued_and_progress_comes_from_db(self):
        request = json.dumps({"niche_query": "کافه", "city_query": "تهران",
                              "engine_mode": "demo", "limit": 5, "depth": 2,
                              "cols": "contact", "export_fmt": "csv"}).encode("utf-8")
        code, _, body = wsgi.call("POST", "/api/runs", body=request,
                                  content_type="application/json")
        self.assertEqual(code, 202)
        data = json_body(body)
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["job_id"], data["run_id"])
        self.assertEqual(data["position"]["position"], 1)
        self.assertIn("/api/runs/", data["poll"])

        run_id = data["run_id"]
        code, _, body = wsgi.call("GET", f"/api/runs/{run_id}/progress")
        self.assertEqual(code, 200)
        progress = json_body(body)["progress"]
        self.assertEqual(progress["status"], "queued")
        self.assertIn("صف", progress["message"])
        self.assertEqual(progress["queue"]["position"], 1)

        # ورکر (فرایند دیگر) کار را تمام می‌کند
        from leadgen import worker
        summary = worker.run_worker(once=True, engine_mode="demo", quiet=True)
        self.assertEqual(summary["succeeded"], 1)

        code, _, body = wsgi.call("GET", f"/api/runs/{run_id}/progress")
        progress = json_body(body)["progress"]
        self.assertEqual(progress["percent"], 100)
        self.assertEqual(progress["stage"], "done")

        code, _, body = wsgi.call("GET", f"/api/runs/{run_id}/leads", query="limit=3")
        self.assertEqual(code, 200)
        self.assertTrue(json_body(body)["leads"])

    def test_queue_endpoint_shows_pending_work(self):
        request = json.dumps({"niche_query": "رستوران", "city_query": "شیراز",
                              "engine_mode": "demo", "limit": 3}).encode("utf-8")
        wsgi.call("POST", "/api/runs", body=request, content_type="application/json")
        code, _, body = wsgi.call("GET", "/api/queue")
        self.assertEqual(code, 200)
        data = json_body(body)
        self.assertTrue(data["active"])
        self.assertEqual(data["backend"], config.DB_BACKEND)
        self.assertGreaterEqual(data["stats"]["pending"], 1)
        self.assertTrue(data["jobs"])
        self.assertEqual(data["jobs"][0]["status_fa"], "در صف")


class WsgiDeliveryTests(unittest.TestCase):
    def setUp(self):
        storage.init_db()
        self._env = {key: os.environ.get(key) for key in ("DIGEST_TO", "LEADGEN_DELIVERY_DRY")}
        os.environ["LEADGEN_DELIVERY_DRY"] = "1"
        os.environ["DIGEST_TO"] = ""

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_delivery_status_explains_missing_credentials(self):
        code, _, body = wsgi.call("GET", "/api/delivery/status")
        self.assertEqual(code, 200)
        data = json_body(body)
        self.assertIn(data["transport"], ("none", "smtp", "gmail_api"))
        self.assertEqual(data["configured"], data["transport"] != "none")

    def test_digest_dry_run_returns_eml_path(self):
        payload = json.dumps({"to": "ops@example.com", "dry_run": True, "limit": 3,
                              "segment": "hot"}).encode("utf-8")
        code, _, body = wsgi.call("POST", "/api/delivery/digest", body=payload,
                                  content_type="application/json")
        self.assertEqual(code, 200)
        data = json_body(body)
        self.assertEqual(data["status"], "dry_run")
        self.assertEqual(data["target"], "ops@example.com")
        self.assertTrue(Path(data["path"]).exists())

    def test_digest_without_recipient_is_400(self):
        payload = json.dumps({"dry_run": True}).encode("utf-8")
        code, _, body = wsgi.call("POST", "/api/delivery/digest", body=payload,
                                  content_type="application/json")
        self.assertEqual(code, 400)
        self.assertIn("گیرنده", json_body(body)["error"])

    def test_drafts_dry_run_creates_unique_files(self):
        payload = json.dumps({"limit": 2, "dry_run": True}).encode("utf-8")
        code, _, body = wsgi.call("POST", "/api/delivery/drafts", body=payload,
                                  content_type="application/json")
        self.assertEqual(code, 200)
        data = json_body(body)
        self.assertEqual(data["status"], "dry_run")
        paths = {item["path"] for item in data["items"]}
        self.assertEqual(len(paths), data["count"])


class WsgiAdapterTests(unittest.TestCase):
    def test_environ_builder_sets_wsgi_keys(self):
        environ = wsgi.make_environ("POST", "/api/runs", query="a=1", body=b'{"x":1}',
                                    content_type="application/json",
                                    headers={"X-Test": "yes"})
        self.assertEqual(environ["REQUEST_METHOD"], "POST")
        self.assertEqual(environ["PATH_INFO"], "/api/runs")
        self.assertEqual(environ["QUERY_STRING"], "a=1")
        self.assertEqual(environ["CONTENT_LENGTH"], "7")
        self.assertEqual(environ["CONTENT_TYPE"], "application/json")
        self.assertEqual(environ["HTTP_X_TEST"], "yes")
        self.assertEqual(environ["wsgi.input"].read(), b'{"x":1}')

    def test_query_string_is_passed_through(self):
        code, _, body = wsgi.call("GET", "/api/search", query="q=%DA%A9%D8%A7%D9%81%D9%87&limit=2")
        self.assertEqual(code, 200)
        data = json_body(body)
        self.assertEqual(data["filters"]["q"], "کافه")
        self.assertLessEqual(data["returned"], 2)

    def test_head_request_has_no_body(self):
        code, headers, body = wsgi.call("HEAD", "/api/health")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"")
        self.assertIn("content-length", headers)


if __name__ == "__main__":
    unittest.main(verbosity=2)
