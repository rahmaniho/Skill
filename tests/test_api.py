"""تست‌های سطح HTTP: سرور را روی پورت تصادفی بالا می‌آورد و API را می‌زند.

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
import zipfile
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = Path(tempfile.mkdtemp(prefix="leadgen-api-"))
os.environ["LEADGEN_DATA_DIR"] = str(_TMP)
os.environ["ENGINE_MODE"] = "demo"

from leadgen import server as server_mod  # noqa: E402
from leadgen import storage  # noqa: E402


def _find_free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ApiTestCase(unittest.TestCase):
    httpd = None
    thread = None
    base = ""

    @classmethod
    def setUpClass(cls):
        cls.port = _find_free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.httpd = server_mod.make_server("127.0.0.1", cls.port)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        for _ in range(60):                      # منتظر آماده شدن سرور
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

    # ── ابزارهای درخواست ─────────────────────────────────────────────────────
    @classmethod
    def request(cls, path: str, method: str = "GET", body=None, headers=None):
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json")
            elif isinstance(body, bytes):
                data = body
            else:
                data = str(body).encode("utf-8")
        req = urllib.request.Request(cls.base + path, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return resp.status, raw, resp.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    @classmethod
    def get(cls, path: str):
        status, raw, headers = cls.request(path)
        if "json" in (headers.get("Content-Type") or ""):
            return status, json.loads(raw.decode("utf-8"))
        return status, raw

    @classmethod
    def post(cls, path: str, body=None, method: str = "POST", headers=None):
        status, raw, resp_headers = cls.request(path, method=method, body=body, headers=headers)
        headers = resp_headers
        if "json" in (headers.get("Content-Type") or ""):
            return status, json.loads(raw.decode("utf-8"))
        return status, raw

    # ── تست‌ها ───────────────────────────────────────────────────────────────
    def test_01_health(self):
        status, data = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn(data["engine"]["active"], ("docker", "demo", "unavailable"))
        self.assertIn("db", data)

    def test_02_catalog(self):
        status, data = self.get("/api/catalog")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(data["niches"]), 60)
        self.assertGreaterEqual(len(data["cities"]), 40)
        self.assertEqual(len(data["profiles"]), 5)
        self.assertTrue(data["gap_labels"])

    def test_03_static_dashboard(self):
        status, raw = self.get("/")
        self.assertEqual(status, 200)
        html = raw.decode("utf-8")
        self.assertIn('dir="rtl"', html)
        self.assertIn('id="tabbar"', html)                   # رابط نسل بعد (نُوا) پیش‌فرض روی /
        self.assertIn('id="dock"', html)
        for asset in ("/styles.css", "/app.js", "/ui.js", "/manifest.webmanifest",
                      "/favicon.svg", "/icons/icon.svg"):
            status, _ = self.get(asset)
            self.assertEqual(status, 200, asset)
        status, raw = self.get("/studio/")
        self.assertEqual(status, 200)
        self.assertIn("LeadGen Studio", raw.decode("utf-8"))

    def test_04_static_path_traversal_blocked(self):
        status, _ = self.get("/../leadgen/config.py")
        self.assertIn(status, (400, 404))

    def test_05_unknown_route(self):
        status, data = self.get("/api/nope")
        self.assertEqual(status, 404)
        self.assertIn("error", data)

    def test_06_run_requires_input(self):
        status, data = self.post("/api/runs", {})
        self.assertEqual(status, 400)
        self.assertIn("niche_query", data["error"] + data.get("error", ""))

    def test_07_full_run_and_endpoints(self):
        status, started = self.post("/api/runs", {
            "niche_query": "دندانپزشکی", "city_query": "تهران", "profile": "web_design",
            "lang": "fa", "depth": 2, "limit": 30, "engine_mode": "demo",
            "socials": True, "export_fmt": "xlsx",
            "sender": {"name": "سارا", "company": "استودیو رشد", "phone": "+989120000000"},
        })
        self.assertEqual(status, 202)
        run_id = started["run_id"]
        self.assertTrue(run_id)

        # نظرسنجی تا پایان
        detail = None
        for _ in range(120):
            status, detail = self.get(f"/api/runs/{run_id}")
            self.assertEqual(status, 200)
            if detail["run"]["status"] in ("done", "failed"):
                break
            time.sleep(0.25)
        self.assertEqual(detail["run"]["status"], "done", detail["run"].get("error"))
        self.assertEqual(detail["lead_count"], 30)
        self.assertTrue(detail["analytics"]["coverage"]["total"])

        # فهرست اجراها
        status, runs = self.get("/api/runs?limit=10")
        self.assertTrue(any(r["id"] == run_id for r in runs["runs"]))

        # لیدها با فیلتر
        status, leads = self.get(f"/api/runs/{run_id}/leads?segment=hot&limit=5")
        self.assertEqual(status, 200)
        for lead in leads["leads"]:
            self.assertEqual(lead["segment"], "hot")
            self.assertTrue(lead["segment_fa"])
            self.assertTrue(lead["status_fa"])

        # جست‌وجو
        first_title = leads["leads"][0]["title"] if leads["leads"] else ""
        if first_title:
            status, found = self.get(f"/api/runs/{run_id}/leads?q=" +
                                     urllib.parse.quote(first_title[:6]))
            self.assertGreaterEqual(found["count"], 1)

        # جزئیات یک لید + breakdown امتیاز
        if leads["leads"]:
            lead_id = leads["leads"][0]["id"]
            status, one = self.get(f"/api/leads/{lead_id}")
            self.assertEqual(status, 200)
            self.assertEqual(len(one["lead"]["score_factors"]), 5)
            self.assertIn("email", one["lead"]["campaign"])
            # تغییر وضعیت (مینی‌CRM)
            status, changed = self.post(f"/api/leads/{lead_id}",
                                        {"status": "contacted", "note": "تماس اول"}, method="PATCH")
            self.assertEqual(status, 200)
            self.assertEqual(changed["lead"]["status_fa"], "تماس گرفته شد")
            # وضعیت نامعتبر
            status, bad = self.post(f"/api/leads/{lead_id}", {"status": "bogus"}, method="PATCH")
            self.assertEqual(status, 400)

        # خروجی‌ها
        status, raw = self.get(f"/api/runs/{run_id}/export?fmt=csv")
        self.assertEqual(status, 200)
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))          # BOM برای اکسل
        header_line = raw.decode("utf-8-sig", "ignore").splitlines()[0]
        self.assertIn("نام کسب‌وکار", header_line)
        self.assertIn("امتیاز لید", header_line)

        status, raw = self.get(f"/api/runs/{run_id}/export?fmt=xlsx")
        with zipfile.ZipFile(BytesIO(raw)) as zf:
            self.assertIsNone(zf.testzip())
            self.assertIn("xl/workbook.xml", zf.namelist())

        status, payload = self.get(f"/api/runs/{run_id}/export?fmt=json")
        if isinstance(payload, bytes):            # اگر Content-Type غیر json بود
            payload = json.loads(payload.decode("utf-8"))
        self.assertEqual(len(payload["leads"]), 30)

        # خروجی کمپین
        status, raw = self.get(f"/api/runs/{run_id}/export?what=campaign&channel=whatsapp")
        self.assertIn("واتس‌اپ", raw.decode("utf-8-sig"))

        # کمپین از API
        status, camp = self.get(f"/api/runs/{run_id}/campaign?channel=email&segment=hot&limit=2")
        self.assertEqual(status, 200)
        self.assertTrue(camp["items"])
        self.assertNotIn("{", camp["items"][0]["body"])
        self.assertTrue(camp["compliance"])

        # امتیازدهی مجدد
        status, rescored = self.post(f"/api/runs/{run_id}/rescore", {"profile": "b2b_supply"})
        self.assertEqual(rescored["profile"], "b2b_supply")

        # پاک کردن
        status, deleted = self.post(f"/api/runs/{run_id}", None, method="DELETE")
        self.assertTrue(deleted["deleted"])
        self.assertEqual(storage.count_leads(run_id), 0)

    def test_08_import_json_rows(self):
        status, data = self.post("/api/import", {
            "rows": [
                {"name": "کلینیک پارسا", "phone": "+989121110000", "email": "info@parsa.ir"},
                {"نام": "کافه ونک", "تلفن": "09122223344", "سایت": "https://vanak.cafe"},
            ],
            "niche_query": "دندانپزشکی", "city": "تهران", "profile": "web_design",
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 2)
        self.assertTrue(data["analytics"]["insights"])
        storage.delete_run(data["run_id"])

    def test_09_import_csv_text(self):
        csv_text = "نام,تلفن,ایمیل,سایت\nالف,+989120000001,a@x.ir,\nبتا,,b@x.ir,https://b.ir\n"
        status, data = self.post("/api/import", {"text": csv_text, "city": "تهران"})
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 2)
        storage.delete_run(data["run_id"])

    def test_10_import_multipart(self):
        boundary = "----leadgenboundary"
        csv_bytes = "name,phone,email\nGamma,+989120000002,g@x.ir\n".encode("utf-8")
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"l.csv\"\r\n"
            f"Content-Type: text/csv\r\n\r\n").encode("utf-8") + csv_bytes + (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"city\"\r\n\r\nتهران"
            f"\r\n--{boundary}--\r\n").encode("utf-8")
        status, data = self.post("/api/import", body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        storage.delete_run(data["run_id"])

    def test_11_import_empty_is_400(self):
        status, data = self.post("/api/import", {"text": ""})
        self.assertEqual(status, 400)

    def test_12_niche_find_and_geocode(self):
        status, found = self.post("/api/niches/find", {"query": "کارواش", "city": "تهران"})
        self.assertEqual(found["niche"]["id"], "car-wash")
        self.assertTrue(found["sample_keywords"])

        status, geo_data = self.post("/api/geocode", {"place": "فرانکفورت"})
        self.assertEqual(geo_data["city_key"], "frankfurt")
        self.assertEqual(len(geo_data["bbox"]), 4)

        status, missing = self.post("/api/geocode", {"place": "ناکجاآباد xyz", "allow_network": False})
        self.assertEqual(status, 404)

    def test_13_engine_status_endpoint(self):
        status, data = self.get("/api/health")
        self.assertIn("docker", data["engine"])
        self.assertIn("reason", data["engine"])

    # ── صف اجراها و تحویل ایمیلی (لایهٔ عملیاتی) ──────────────────────────────
    def test_14_queue_endpoints(self):
        status, data = self.get("/api/queue")
        self.assertEqual(status, 200)
        self.assertEqual(data["backend"], "sqlite")
        self.assertIn(data["mode"], ("auto", "always", "never"))
        self.assertFalse(data["active"])          # پایگاه‌داده محلی → صف خاموش
        for key in ("pending", "running", "done", "failed", "oldest_wait_s", "workers"):
            self.assertIn(key, data["stats"])
        self.assertIsInstance(data["jobs"], list)
        self.assertIn(data["engine"]["active"], ("docker", "demo", "unavailable"))
        self.assertTrue(data["worker"])

        status, data = self.get("/api/queue/jobs?status=done&limit=5")
        self.assertEqual(status, 200)
        self.assertTrue(data["statuses"])
        self.assertEqual(data["statuses"][0], {"id": "pending", "fa": "در صف"})
        self.assertTrue(all(job["status"] == "done" for job in data["jobs"]))

        status, data = self.get("/api/queue/jobs?status=nope")
        self.assertEqual(status, 400)
        self.assertIn("وضعیت نامعتبر", data["error"])

    def test_15_delivery_status(self):
        status, data = self.get("/api/delivery/status")
        self.assertEqual(status, 200)
        self.assertIn(data["transport"], ("none", "smtp", "gmail_api"))
        self.assertEqual(data["configured"], data["transport"] != "none")
        self.assertIsInstance(data["recipients"], list)
        if data["transport"] == "none":
            self.assertIn("GMAIL_REFRESH_TOKEN", data["hint"])

    def test_16_delivery_digest_dry_run(self):
        status, data = self.post("/api/delivery/digest",
                                 {"to": "ops@example.com", "dry_run": True, "limit": 3})
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "dry_run")
        self.assertEqual(data["target"], "ops@example.com")
        self.assertLessEqual(data["shown"], 3)
        self.assertTrue(Path(data["path"]).exists())
        if data.get("attachment"):
            self.assertTrue(data["attachment"].endswith(".xlsx"))

        # بدون گیرنده و بدون DIGEST_TO → خطای قابل‌فهم
        status, data = self.post("/api/delivery/digest", {"dry_run": True})
        self.assertEqual(status, 400)
        self.assertIn("گیرنده", data["error"])

    def test_17_delivery_drafts_dry_run(self):
        status, data = self.post("/api/delivery/drafts", {"limit": 2, "dry_run": True})
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "dry_run")
        self.assertLessEqual(data["count"], 2)
        self.assertEqual(len({item["path"] for item in data["items"]}), data["count"])

    def test_18_queue_mode_run_goes_to_worker(self):
        """در حالت صف، POST /api/runs کار را در صف می‌گذارد و ورکر تمامش می‌کند."""
        from leadgen import config as config_mod, jobs, worker
        with storage._connect() as conn:
            conn.execute("delete from jobs")
        original = config_mod.QUEUE_MODE
        try:
            config_mod.QUEUE_MODE = "always"
            status, data = self.post("/api/runs", {
                "niche_query": "کافه", "city_query": "تهران", "engine_mode": "demo",
                "limit": 6, "depth": 2, "cols": "contact", "export_fmt": "csv"})
            self.assertEqual(status, 202)
            self.assertEqual(data["status"], "queued")
            self.assertEqual(data["job_id"], data["run_id"])
            self.assertTrue(data["note"])
            self.assertGreaterEqual(data["queue"]["pending"], 1)
            self.assertEqual(data["position"]["position"], 1)

            # پیشرفت از پایگاه‌داده خوانده می‌شود (ورکر فرایند دیگری است)
            status, progress = self.get(f"/api/runs/{data['run_id']}/progress")
            self.assertEqual(status, 200)
            self.assertEqual(progress["progress"]["status"], "queued")
            self.assertIn("صف", progress["progress"]["message"])
            self.assertEqual(storage.get_run(data["run_id"])["status"], "queued")

            summary = worker.run_worker(once=True, engine_mode="demo", quiet=True)
            self.assertEqual(summary["succeeded"], 1)
            status, progress = self.get(f"/api/runs/{data['run_id']}/progress")
            self.assertEqual(progress["progress"]["percent"], 100)
            self.assertEqual(progress["progress"]["stage"], "done")
            status, detail = self.get(f"/api/runs/{data['run_id']}")
            self.assertEqual(status, 200)
            self.assertEqual(detail["run"]["status"], "done")
            self.assertGreater(detail["run"]["total"], 0)
            self.assertEqual(jobs.queue_position(data["run_id"]), None)
        finally:
            config_mod.QUEUE_MODE = original
            with storage._connect() as conn:
                conn.execute("delete from jobs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
