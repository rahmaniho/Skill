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
from unittest import mock
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

    def test_21_tick_reserves_a_bounded_enrichment_budget(self):
        """تپش باید بخشی از بودجه را به غنی‌سازی (ایمیل/شبکه اجتماعی) بدهد، ولی محدود."""
        from leadgen import config as config_mod, worker as worker_mod

        captured: dict = {}

        def fake_process(job, quiet=True):
            captured.update(job.get("request") or {})
            return {"ok": True, "run_id": job.get("run_id"), "total": 0, "hot": 0,
                    "seconds": 0.1, "empty": True}

        original_queue, original_process = config_mod.QUEUE_MODE, worker_mod.process_job
        with storage._connect() as conn:
            conn.execute("delete from jobs")
        try:
            config_mod.QUEUE_MODE = "always"
            storage.enqueue_job("tick-budget-run", {
                "run_id": "tick-budget-run", "city_query": "تهران", "niche_query": "کافه",
                "engine_mode": "demo", "max_time": 60})
            worker_mod.process_job = fake_process
            status, data = self.get("/api/worker/tick?budget=50")
        finally:
            worker_mod.process_job = original_process
            config_mod.QUEUE_MODE = original_queue
            with storage._connect() as conn:
                conn.execute("delete from jobs")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("processed"))
        self.assertTrue(captured.get("enrich_live"))
        self.assertGreater(captured.get("enrich_budget", 0), 0)
        self.assertLessEqual(captured.get("enrich_budget", 0), 15)
        # موتور و غنی‌سازی با هم زیر سقف ۶۰ثانیه‌ای تابع می‌مانند
        self.assertLessEqual(captured["max_time"] + captured["enrich_budget"], 40)

    def test_21b_tick_keeps_full_scan_limits_proportional(self):
        """سقف ۶۰تایی فقط برای اجرای سریع است؛ اسکراب کامل همان limit کاربر را نگه می‌دارد."""
        from leadgen import config as config_mod, worker as worker_mod

        seen: list[dict] = []

        def fake_process(job, quiet=True):
            seen.append(dict(job.get("request") or {}))
            return {"ok": True, "run_id": job.get("run_id"), "total": 0, "hot": 0,
                    "seconds": 0.1, "empty": True}

        original_queue, original_process = config_mod.QUEUE_MODE, worker_mod.process_job
        try:
            config_mod.QUEUE_MODE = "always"
            worker_mod.process_job = fake_process
            with storage._connect() as conn:
                conn.execute("delete from jobs")
            for run_id, extra in (("tick-quick", {}), ("tick-scan", {"scan_full": True})):
                storage.enqueue_job(run_id, {"run_id": run_id, "city_query": "تهران",
                                             "niche_query": "کافه", "engine_mode": "demo",
                                             "limit": 300, "max_time": 60, **extra})
                self.get("/api/worker/tick?budget=50")
            with storage._connect() as conn:
                conn.execute("delete from jobs")
        finally:
            worker_mod.process_job = original_process
            config_mod.QUEUE_MODE = original_queue
            with storage._connect() as conn:
                conn.execute("delete from jobs")
        limits = {item.get("run_id") or "": item.get("limit") for item in seen}
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].get("limit"), 60)      # سریع: سقف محافظه‌کارانه
        self.assertEqual(seen[1].get("limit"), 300)     # اسکراب کامل: عدد کاربر

    def test_20b_enrich_run_fills_missing_emails_without_rescraping(self):
        """«غنی‌سازی مجدد»: لیدهای ذخیره‌شده ایمیل/شبکه اجتماعی می‌گیرند، بدون اسکراپ."""
        from leadgen import enrich as enrich_mod

        status, imported = self.post("/api/import", {
            "rows": [
                {"name": "کافه ساده", "site": "https://cafesaadeh.ir", "phone": "09121110000",
                 "city": "یزد"},
                {"name": "کلینیک پارسا", "website": "https://parsa-clinic.ir", "city": "تهران"},
            ],
            "niche_query": "کافه", "city": "یزد",
        })
        self.assertEqual(status, 200)
        run_id = imported["run_id"]

        html = ('<html><body><a href="mailto:info@cafesaadeh.ir">mail</a>'
                '<a href="https://instagram.com/cafesaadeh">ig</a>'
                '<a href="/contact">تماس</a></body></html>')
        with mock.patch.object(enrich_mod, "_fetch_cached", return_value=html), \
                mock.patch.object(enrich_mod, "_fetch_html", return_value=html):
            status, result = self.post(f"/api/runs/{run_id}/enrich", {"budget": 8})
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 2)
        self.assertGreaterEqual(result["email"]["after"], 1)
        self.assertGreaterEqual(result["social"]["after"], 1)
        # گزارش در بستهٔ آمار می‌ماند
        self.assertTrue(any("غنی‌سازی مجدد" in w for w in result["analytics"].get("warnings", [])))
        # اجرای دوباره بی‌خطر است (کاری برای انجام نمانده)
        with mock.patch.object(enrich_mod, "_fetch_cached", return_value=html), \
                mock.patch.object(enrich_mod, "_fetch_html", return_value=html):
            status, again = self.post(f"/api/runs/{run_id}/enrich", {"budget": 8})
        self.assertEqual(status, 200)
        self.assertEqual(again["email"]["after"], result["email"]["after"])

        storage.delete_run(run_id)

    def test_20c_enrich_can_be_queued_and_ticked_without_a_browser(self):
        """اجرای بزرگ: غنی‌سازی به صف می‌رود و همان تپش (کرون) تمامش می‌کند."""
        from leadgen import config as config_mod, enrich as enrich_mod, worker as worker_mod

        status, imported = self.post("/api/import", {
            "rows": [{"name": "کافه صف", "website": "https://queued-cafe.ir", "city": "یزد"}],
            "niche_query": "کافه", "city": "یزد"})
        self.assertEqual(status, 200)
        run_id = imported["run_id"]

        status, queued = self.post(f"/api/runs/{run_id}/enrich", {"queue": True, "budget": 20})
        self.assertEqual(status, 202, queued)
        self.assertTrue(queued["queued"])
        job_id = queued["job_id"]

        html = '<a href="mailto:hi@queued-cafe.ir">mail</a>'
        original_queue = config_mod.QUEUE_MODE
        try:
            config_mod.QUEUE_MODE = "always"
            with mock.patch.object(enrich_mod, "_fetch_cached", return_value=html), \
                    mock.patch.object(enrich_mod, "_fetch_html", return_value=html):
                status, ticked = self.get("/api/worker/tick?budget=45")
        finally:
            config_mod.QUEUE_MODE = original_queue
        self.assertEqual(status, 200)
        self.assertEqual(ticked.get("job_id"), job_id)
        self.assertEqual(ticked.get("task"), "enrich")
        self.assertTrue(ticked.get("ok"))
        lead = storage.all_leads_for_export(run_id)[0]
        self.assertEqual(lead["email_primary"], "hi@queued-cafe.ir")
        storage.delete_run(run_id)

    def test_20d_unknown_run_cannot_be_queued_for_enrich(self):
        status, data = self.post("/api/runs/does-not-exist/enrich", {"queue": True})
        self.assertEqual(status, 404)
        self.assertIn("پیدا نشد", data["error"])

    def test_20e_queue_reports_when_it_last_did_something(self):
        """«آخرین تپش صف» باید از دادهٔ پایگاه‌داده بیاید (نه حافظهٔ همین پروسه)."""
        from leadgen import config as config_mod

        original = config_mod.QUEUE_MODE
        try:
            config_mod.QUEUE_MODE = "always"
            with storage._connect() as conn:
                conn.execute("delete from jobs")
            storage.enqueue_job("activity-probe", {"run_id": "activity-probe",
                                                   "city_query": "تهران",
                                                   "niche_query": "کافه"})
            status, data = self.get("/api/queue")
        finally:
            config_mod.QUEUE_MODE = original
            with storage._connect() as conn:
                conn.execute("delete from jobs")
        self.assertEqual(status, 200)
        activity = data["activity"]
        self.assertIsNotNone(activity["last_activity_s"])
        self.assertLessEqual(activity["last_activity_s"], 60)
        self.assertTrue(activity["cron_ok"])
        self.assertIn("ثانیه پیش", activity["last_activity_fa"])

    def test_20e2_idle_queue_is_healthy_but_stuck_queue_is_not(self):
        """صف خالی سالم است؛ فقط صفِ با کارِ منتظر و سکوت طولانی «گیر کرده» است."""
        from leadgen import server as server_mod
        import time as time_mod

        now = time_mod.time()
        old_job = {"updated_at": now - 3600}
        idle = server_mod.queue_activity([old_job], {"pending": 0})
        self.assertFalse(idle["stalled"])
        self.assertIsNone(idle["cron_ok"])          # بدون کار، «کارکردن» بی‌معناست

        stuck = server_mod.queue_activity([old_job], {"pending": 2})
        self.assertTrue(stuck["stalled"])
        self.assertFalse(stuck["cron_ok"])

        busy = server_mod.queue_activity([{"updated_at": now - 20}], {"pending": 2})
        self.assertFalse(busy["stalled"])
        self.assertTrue(busy["cron_ok"])

    def test_20f_queue_activity_reads_supabase_timestamps(self):
        """Supabase مقدار updated_at را متنی برمی‌گرداند؛ باید درست خوانده شود."""
        from leadgen import server as server_mod

        stamp = server_mod._stamp_of({"updated_at": "2026-11-12T19:04:49.091265+00:00"})
        self.assertGreater(stamp, 1_700_000_000)
        self.assertEqual(server_mod._stamp_of({"updated_at": None, "created_at": 5.0}), 5.0)

    # ── تپش صف از بیرون (کرون) ──────────────────────────────────────────────
    def _queue_tick_module(self):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "scripts" / "queue_tick.py"
        spec = importlib.util.spec_from_file_location("leadgen_queue_tick", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_20_queue_tick_script_drains_a_queued_job(self):
        """کرون بیرونی باید بتواند کار در صف را تمام کند (بدون مرورگر باز)."""
        from leadgen import config as config_mod

        module = self._queue_tick_module()
        original = config_mod.QUEUE_MODE
        with storage._connect() as conn:
            conn.execute("delete from jobs")
        try:
            config_mod.QUEUE_MODE = "always"
            status, queued = self.post("/api/runs", {
                "niche_query": "کافه", "city_query": "تهران", "engine_mode": "demo",
                "limit": 4, "depth": 2})
            self.assertEqual(status, 202)
            self.assertEqual(queued["status"], "queued")

            payload = module.tick(self.base, budget=25)
            self.assertFalse(payload.get("idle"))
            self.assertEqual(payload.get("_http"), 200)
            self.assertTrue(payload.get("ok"))
            self.assertEqual(payload.get("run_id"), queued["run_id"])
            self.assertGreaterEqual(payload.get("total", 0), 1)
            self.assertIn("انجام شد", module.describe(payload))

            idle = module.tick(self.base, budget=20)
            self.assertTrue(idle.get("idle"))
            self.assertIn("بیکار", module.describe(idle))
        finally:
            config_mod.QUEUE_MODE = original
            with storage._connect() as conn:
                conn.execute("delete from jobs")

    # ── سنجش زندهٔ OSM ──────────────────────────────────────────────────────
    def test_health_lists_sources_and_tag_coverage(self):
        status, data = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertIn("sources", data)
        self.assertIn("overpass", data["sources"])
        self.assertEqual(data["sources"]["overpass"]["mode"], "hybrid")
        self.assertGreaterEqual(data["tag_map"]["niches"], 80)
        self.assertGreaterEqual(data["tag_map"]["filters"], 100)

    def test_diag_reports_real_shape_for_city_and_niche(self):
        from leadgen.providers import osm
        row = {"title": "کافه تست", "category": "کافه", "latitude": "35.70", "longitude": "51.40",
               "phone": "+982112345678", "website": "", "instagram": "cafetest",
               "address": "تهران، خیابان ولیعصر"}
        query = urllib.parse.urlencode({"city": "تهران", "niche": "کافه", "limit": 50})
        with mock.patch.object(osm, "overpass_search", return_value=[row, dict(row)]) as search:
            status, data = self.get(f"/api/diag/osm?{query}")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["city_key"], "tehran")
        self.assertGreaterEqual(data["filters"], 1)
        self.assertEqual(data["rows"], 2)
        self.assertEqual(data["with_phone"], 2)
        self.assertEqual(data["with_social"], 2)
        self.assertLessEqual(len(data["sample"]), 12)
        self.assertEqual(data["sample"][0]["title"], "کافه تست")
        self.assertIn("bbox", data)
        self.assertEqual(len(data["bbox"]), 4)
        self.assertTrue(search.called)

    def test_diag_bounds_overpass_timeout(self):
        """سنجش زنده باید داخل سقف تابع serverless بماند (پیش‌فرض ۲۲ ثانیه)."""
        from leadgen.providers import osm
        seen = {}

        def fake_search(filters, *, bbox, limit=None, timeout=None, input_id="", lang="fa",
                        cache=True):
            seen["timeout"] = timeout
            return []

        query = urllib.parse.urlencode({"city": "تهران", "niche": "کافه", "timeout": 12})
        with mock.patch.object(osm, "overpass_search", side_effect=fake_search):
            status, data = self.get(f"/api/diag/osm?{query}")
        self.assertEqual(status, 200)
        self.assertEqual(seen["timeout"], 12)
        self.assertEqual(data["timeout"], 12)

    def test_diag_reports_error_instead_of_crashing(self):
        from leadgen.providers import osm
        query = urllib.parse.urlencode({"city": "رامسر", "niche": "رستوران"})
        with mock.patch.object(osm, "overpass_search", side_effect=RuntimeError("overpass down")), \
                mock.patch.object(osm, "search", return_value=[]):
            status, data = self.get(f"/api/diag/osm?{query}")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("overpass down", data["error"])
        self.assertEqual(data["rows"], 0)
        # مسیر جایگزین هم واقعاً امتحان شده (نه اینکه ساکت رد شود)
        self.assertTrue(data["fallback"]["tried"])
        self.assertEqual(data["fallback"]["rows"], 0)

    def test_diag_falls_back_to_text_search_when_overpass_is_down(self):
        """پیام باید بین «سرویس پایین است» و «شهر داده ندارد» فرق بگذارد."""
        from leadgen.providers import osm
        row = {"title": "کافه یزد", "category": "کافه", "latitude": "31.89", "longitude": "54.36",
               "phone": "", "website": "", "instagram": "", "address": "یزد"}
        query = urllib.parse.urlencode({"city": "یزد", "niche": "کافه", "limit": 40})
        with mock.patch.object(osm, "overpass_search", side_effect=RuntimeError("502")), \
                mock.patch.object(osm, "search", return_value=[row]) as text_search:
            status, data = self.get(f"/api/diag/osm?{query}")
        self.assertEqual(status, 200)
        self.assertTrue(text_search.called)
        self.assertTrue(data["ok"])
        self.assertFalse(data["overpass_ok"])
        self.assertEqual(data["rows"], 1)
        self.assertEqual(data["fallback"]["via"], "nominatim")
        self.assertEqual(data["sample"][0]["title"], "کافه یزد")
        self.assertIn("502", data["error"])

    def test_diag_unknown_city_is_reported(self):
        query = urllib.parse.urlencode({"city": "ناکجاآباد", "niche": "کافه"})
        status, data = self.get(f"/api/diag/osm?{query}")
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("پیدا نشد", data["error"])

    # ── دیدبان (watchlist) ────────────────────────────────────────────────────
    def test_26_watchlist_crud_and_validation(self):
        status, data = self.post("/api/watchlist", {"city": "یزد", "niche_query": "کافه",
                                                    "interval_days": 7})
        self.assertEqual(status, 201)
        watch = data["watch"]
        self.assertTrue(data["ok"])
        self.assertEqual(watch["city"], "یزد")
        self.assertEqual(watch["interval_days"], 7)
        self.assertEqual(data["label"], "کافه · یزد")
        watch_id = watch["id"]

        status, listing = self.get("/api/watchlist")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(listing["total"], 1)
        self.assertIn("کافه · یزد", [w["label"] for w in listing["items"]])

        status, updated = self.post(f"/api/watchlist/{watch_id}",
                                    {"interval_days": 14, "active": False}, method="PATCH")
        self.assertEqual(status, 200)
        self.assertEqual(updated["watch"]["interval_days"], 14)
        self.assertFalse(updated["watch"]["active"])

        status, bad = self.post("/api/watchlist", {"city": "یزد"})
        self.assertEqual(status, 400)
        self.assertIn("صنف", bad["error"])

        status, deleted = self.post(f"/api/watchlist/{watch_id}", method="DELETE")
        self.assertEqual(status, 200)
        self.assertTrue(deleted["deleted"])
        self.assertIsNone(storage.get_watch(watch_id))

    def test_26b_watchlist_tick_runs_due_item_and_reports(self):
        status, data = self.post("/api/watchlist", {"city": "یزد", "niche_query": "کافه"})
        self.assertEqual(status, 201)
        watch_id = data["watch"]["id"]
        storage.update_watch(watch_id, next_run_at=time.time() - 30)
        status, tick = self.get("/api/watchlist/tick?limit=1")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(tick["due"], 1)
        self.assertTrue(tick["queued"])
        first = tick["queued"][0]
        self.assertNotIn("error", first)
        self.assertTrue(first.get("run_id"))
        moved = storage.get_watch(watch_id)
        self.assertGreater(moved["next_run_at"], time.time() + 6 * 86400)
        self.post(f"/api/watchlist/{watch_id}", method="DELETE")

    def test_27_run_leads_fresh_filter_is_server_side(self):
        for run_id, ids in (("fresh-old", ["osm:node/1", "osm:node/2"]),
                            ("fresh-new", ["osm:node/2", "osm:node/3"])):
            storage.create_run({"id": run_id, "created_at": time.time(), "status": "done",
                                "city": "یزد", "niche_fa": "کافه", "total": len(ids)})
            storage.save_leads(run_id, [
                {"place_id": pid, "title": f"کافه {pid}", "segment": "hot", "score": 70,
                 "city": "یزد", "status": "new", "latitude": "31.89",
                 "longitude": "54.36", "signals": {}, "gaps": [], "tags": []}
                for pid in ids])
        status, fresh = self.get("/api/runs/fresh-new/leads?fresh=new")
        self.assertEqual(status, 200)
        self.assertEqual([r["place_id"] for r in fresh["leads"]], ["osm:node/3"])
        self.assertEqual(fresh["fresh"]["new"], 1)
        self.assertEqual(fresh["fresh"]["repeat"], 1)
        self.assertFalse((fresh["leads"][0].get("signals") or {}).get("repeat"))
        status, repeats = self.get("/api/runs/fresh-new/leads?fresh=repeat")
        self.assertEqual([r["place_id"] for r in repeats["leads"]], ["osm:node/2"])
        self.assertTrue(repeats["leads"][0]["signals"]["repeat"])
        # همان فیلتر روی جست‌وجوی سراسری هم هست
        search_q = urllib.parse.urlencode({"fresh": "new", "q": "کافه", "limit": 50})
        status, allnew = self.get(f"/api/search?{search_q}")
        self.assertEqual(status, 200)
        self.assertTrue(all(not (r.get("signals") or {}).get("repeat")
                            for r in allnew["leads"]))

    def test_28_create_run_persists_analytics_on_sqlite(self):
        """باگ قبلی: `create_run` کلید analytics را دور می‌ریخت و «ادامه» کار نمی‌کرد."""
        storage.create_run({"id": "with-analytics", "created_at": time.time(),
                            "status": "done", "city": "تهران", "niche_fa": "کافه",
                            "analytics": {"scan": {"full": True, "total": 4,
                                                   "remaining": ["x"]}}})
        run = storage.get_run("with-analytics")
        self.assertEqual((run.get("analytics") or {}).get("scan", {}).get("remaining"), ["x"])

    def test_28_continue_scan_requeues_only_the_rest(self):
        """دکمهٔ «ادامهٔ اسکراب» بقیهٔ بخش‌ها را با همان run_id در صف می‌گذارد."""
        run_id = "scan-partial"
        storage.create_run({"id": run_id, "created_at": time.time(), "status": "done",
                            "city": "تهران", "niche_fa": "کافه"})
        storage.update_run(run_id, analytics={"scan": {
            "full": True, "chunk": 0, "total": 9, "done": 4,
            "labels_done": [f"تهران · کاشی {i}/۹" for i in range(1, 5)],
            "remaining": [f"تهران · کاشی {i}/۹" for i in range(5, 10)],
            "request": {"niche_query": "کافه", "city_query": "تهران", "city_key": "tehran",
                        "area_level": "city", "engine_mode": "demo", "lang": "fa",
                        "scan_full": True}}})
        before = {j["id"] for j in storage.list_jobs(limit=200)}
        status, data = self.post(f"/api/runs/{run_id}/continue")
        self.assertIn(status, (200, 202), data)
        self.assertEqual(data["run_id"], run_id)
        if data.get("queued"):
            self.assertEqual(data["remaining"], 5)
            jobs_now = {j["id"] for j in storage.list_jobs(limit=200)}
            self.assertIn(run_id, jobs_now - before)
        else:
            # اجرای درون‌فرایندی: همان run_id ادامه داده شد و بخش بعدی گزارش شد
            self.assertIn("scan", data)
            self.assertEqual(data["run_id"], run_id)
        # بخش ناتمام، در گزارشِ خودِ اجرا هم دیده می‌شود
        detail = storage.get_run(run_id)
        self.assertTrue((detail.get("analytics") or {}).get("scan", {}).get("full"))

    def test_28b_continue_on_a_finished_scan_says_nothing_left(self):
        run_id = "scan-done"
        storage.create_run({"id": run_id, "created_at": time.time(), "status": "done",
                            "city": "یزد", "niche_fa": "کافه"})
        storage.update_run(run_id, analytics={"scan": {
            "full": True, "chunk": 1, "total": 4, "done": 4,
            "labels_done": [], "remaining": [], "request": {}}})
        status, data = self.post(f"/api/runs/{run_id}/continue")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("done"))
        status, missing = self.post("/api/runs/nope-nope/continue")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
