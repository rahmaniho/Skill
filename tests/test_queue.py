"""آزمون صف کار، worker و تحویل ایمیلی (همه روی SQLite محلی، بدون شبکه).

اجرا:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import email
import glob
import io
import json
import os
import sys
import tempfile
import unittest
from email import policy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="leadgen-queue-"))
os.environ["LEADGEN_DATA_DIR"] = str(_TMP)
os.environ["ENGINE_MODE"] = "demo"

from leadgen import (campaign, config, delivery, exports, jobs,  # noqa: E402
                     pipeline, storage, worker)


def demo_request(**over) -> dict:
    base = {"niche_query": "کافه", "city_query": "تهران", "lang": "fa", "depth": 2,
            "socials": True, "engine_mode": "demo", "limit": 8, "keywords_per_job": 2,
            "cols": "contact", "export_fmt": "csv"}
    base.update(over)
    return base


def clear_queue() -> None:
    """صف را خالی می‌کند تا هر آزمون با وضعیت تمیز شروع شود (ورکر کهنه‌کار برندارد)."""
    with storage._connect() as conn:
        conn.execute("delete from jobs")


class QueueBasicsTests(unittest.TestCase):
    def setUp(self):
        storage.init_db()
        clear_queue()
        self.run_id = storage.create_run({"id": "q-run-1", "city": "تهران", "niche_fa": "کافه",
                                          "keywords": ["کافه در تهران"], "status": "queued",
                                          "progress": {"stage": "queued", "percent": 1}})

    def test_enqueue_then_claim_locks_the_job(self):
        job_id = storage.enqueue_job(self.run_id, demo_request())
        self.assertEqual(job_id, self.run_id)                 # شناسهٔ کار = شناسهٔ اجرا
        pending = [job for job in storage.list_jobs("pending") if job["id"] == job_id]
        self.assertEqual(pending[0]["status"], "pending")
        self.assertEqual(pending[0]["status_fa"], "در صف")
        self.assertEqual(pending[0]["attempts"], 0)
        self.assertEqual(pending[0]["request"]["niche_query"], "کافه")  # request به dict برمی‌گردد

        claimed = storage.claim_job("worker-a", 3)
        self.assertEqual(claimed["id"], job_id)
        self.assertEqual(claimed["status"], "running")
        self.assertEqual(claimed["status_fa"], "در حال اجرا")
        self.assertEqual(claimed["attempts"], 1)
        self.assertEqual(claimed["worker"], "worker-a")
        self.assertGreaterEqual(claimed["started_at"], claimed["created_at"])

        # کار دوم چیزی نمی‌گیرد (قفل صف)
        self.assertIsNone(storage.claim_job("worker-b", 3))
        self.assertEqual(storage.list_jobs("running")[0]["id"], job_id)

    def test_claim_ceiling_and_retry_policy(self):
        job_id = storage.enqueue_job(self.run_id, demo_request(), priority=1)
        claimed = storage.claim_job("worker-a", 1)          # سقف تلاش ۱
        self.assertIsNotNone(claimed)
        storage.finish_job(job_id, "failed", "خطای آزمایش")
        # سیاست تلاش مجدد از max_attempts ردیف می‌آید (پیش‌فرض ۳) → کار در صف می‌ماند
        with storage._connect() as conn:
            row = conn.execute("select status, attempts, max_attempts, last_error from jobs"
                               " where id = ?", (job_id,)).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["max_attempts"], config.WORKER_MAX_ATTEMPTS)
        self.assertIn("خطای آزمایش", row["last_error"])
        # ولی ورکری که سقف ۱ می‌خواهد دیگر برش نمی‌دارد؛ ورکر با سقف ۳ چرا
        self.assertIsNone(storage.claim_job("worker-a", 1))
        self.assertIsNotNone(storage.claim_job("worker-b", 3))

    def test_heartbeat_merges_progress_and_updates_run_status(self):
        job_id = storage.enqueue_job(self.run_id, demo_request())
        storage.claim_job("worker-a", 3)
        self.assertTrue(storage.heartbeat_job(job_id, {"stage": "resolve", "percent": 20,
                                                       "message": "در حال آماده‌سازی"}, "running"))
        run = storage.get_run(self.run_id)
        self.assertEqual(run["progress"]["percent"], 20)
        self.assertEqual(run["progress"]["stage"], "resolve")
        storage.heartbeat_job(job_id, {"stage": "scrape", "percent": 60,
                                          "message": "در حال استخراج"}, "running")
        run = storage.get_run(self.run_id)
        self.assertEqual(run["progress"]["stage"], "scrape")
        self.assertEqual(run["progress"]["percent"], 60)
        self.assertTrue(run["progress"]["message"])
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["status_fa"], "در حال اجرا")

    def test_finish_success_closes_the_run(self):
        job_id = storage.enqueue_job(self.run_id, demo_request())
        storage.claim_job("worker-a", 3)
        storage.heartbeat_job(job_id, {"stage": "scrape", "percent": 90}, "running")
        finished = storage.finish_job(job_id, "done", None)
        self.assertEqual(finished["status"], "done")
        run = storage.get_run(self.run_id)
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["status_fa"], "تکمیل شد")
        self.assertEqual(run["progress"]["message"], "پایان کار")

    def test_finish_failure_requeues_below_max_attempts(self):
        job_id = storage.enqueue_job(self.run_id, demo_request())
        storage.claim_job("worker-a", 3)
        result = storage.finish_job(job_id, "failed", "موتور در دسترس نیست")
        self.assertEqual(result["status"], "pending")        # دوباره در صف
        run = storage.get_run(self.run_id)
        self.assertEqual(run["status"], "queued")
        self.assertIn("دوباره در صف", run["progress"]["message"])
        self.assertIn("موتور در دسترس نیست", run["progress"]["message"])
        # کار دیگری می‌تواند بردارد
        self.assertIsNotNone(storage.claim_job("worker-b", 3))

    def test_stale_jobs_are_requeued(self):
        job_id = storage.enqueue_job(self.run_id, demo_request())
        claimed = storage.claim_job("worker-a", 3)
        with storage._connect() as conn:
            conn.execute("update jobs set started_at = ? where id = ?",
                         (claimed["started_at"] - 3600.0, job_id))
        self.assertEqual(storage.requeue_stale_jobs(1800), 1)
        with storage._connect() as conn:
            row = conn.execute("select status from jobs where id = ?", (job_id,)).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(storage.get_run(self.run_id)["status"], "queued")

    def test_queue_stats_and_priority_ordering(self):
        other = storage.create_run({"id": "q-run-2", "city": "شیراز", "niche_fa": "رستوران",
                                    "status": "queued"})
        storage.enqueue_job(self.run_id, demo_request(), priority=0)
        storage.enqueue_job(other, demo_request(engine="r"), priority=5)
        stats = storage.queue_stats()
        self.assertEqual(stats["pending"], 2)
        self.assertEqual(stats["running"], 0)
        self.assertIsInstance(stats["oldest_wait_s"], (int, float))
        # اولویت بالاتر زودتر برداشته می‌شود
        self.assertEqual(storage.claim_job("worker-a", 3)["run_id"], other)


class JobsModuleTests(unittest.TestCase):
    def setUp(self):
        storage.init_db()
        clear_queue()

    def test_enqueue_run_creates_queued_run_row(self):
        result = jobs.enqueue_run(demo_request(), run_id="j-run-1")
        self.assertEqual(result["status"], "queued")
        self.assertTrue(result["job_id"])
        run = storage.get_run(result["run_id"])
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["status_fa"], "در صف")
        self.assertEqual(run["progress"]["stage"], "queued")
        self.assertIn("در صف اجرا", run["progress"]["message"])
        self.assertEqual(run["city"], "تهران")
        self.assertEqual(run["engine"], "demo")
        self.assertEqual(run["keywords"], [])      # کلیدواژه‌ها در خودِ اجرا ساخته می‌شوند

    def test_queue_mode_auto_only_on_cloud_backend(self):
        original = (config.DB_BACKEND, config.QUEUE_MODE, config.SUPABASE_URL, config.SUPABASE_KEY)
        try:
            config.QUEUE_MODE = "auto"
            config.DB_BACKEND = "sqlite"
            self.assertFalse(jobs.queue_mode_active())
            config.DB_BACKEND = "supabase"
            config.SUPABASE_URL = "https://demo.supabase.co"
            config.SUPABASE_KEY = "service-key"
            self.assertTrue(jobs.queue_mode_active())
            config.QUEUE_MODE = "always"
            config.DB_BACKEND = "sqlite"
            self.assertTrue(jobs.queue_mode_active())
            config.QUEUE_MODE = "never"
            config.DB_BACKEND = "supabase"
            self.assertFalse(jobs.queue_mode_active())
        finally:
            (config.DB_BACKEND, config.QUEUE_MODE,
             config.SUPABASE_URL, config.SUPABASE_KEY) = original

    def test_queue_position_reports_place_in_line(self):
        first = jobs.enqueue_run(demo_request(), run_id="qp-run-1", priority=5)
        second = jobs.enqueue_run(demo_request(niche_query="رستوران"), run_id="qp-run-2")
        info = jobs.queue_position(second["run_id"])
        self.assertEqual(info["position"], 2)
        self.assertIn("نفر 2 در صف", info["message"])
        self.assertEqual(info["pending"], 2)
        self.assertEqual(jobs.queue_position(first["run_id"])["position"], 1)
        self.assertIsNone(jobs.queue_position("missing-run"))
        self.assertGreaterEqual(jobs.wait_time(info["wait_seconds"]), 1)

    def test_queue_position_shows_running_jobs_as_position_zero(self):
        result = jobs.enqueue_run(demo_request(niche_query="نانوایی"), run_id="qp-run-3")
        storage.claim_job("worker-a", 3)
        info = jobs.queue_position(result["run_id"])
        self.assertTrue(info["running"])
        self.assertEqual(info["position"], 0)


class WorkerTests(unittest.TestCase):
    """اجرای کامل worker با موتور دمو (بدون Docker، بدون شبکه)."""

    def setUp(self):
        storage.init_db()
        clear_queue()

    def test_worker_completes_a_job_end_to_end(self):
        result = jobs.enqueue_run(demo_request(limit=6), run_id="wk-run-1")
        summary = worker.run_worker(once=True, engine_mode="demo", quiet=True)
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["queue"]["done"], 1)
        self.assertEqual(summary["db"], config.DB_BACKEND)

        run = storage.get_run(result["run_id"])
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["engine"], "demo")
        self.assertFalse(run["live"])
        self.assertGreater(run["total"], 0)
        self.assertEqual(run["progress"]["percent"], 100)
        leads = storage.load_leads(result["run_id"])
        self.assertEqual(len(leads), run["total"])
        self.assertTrue(all(lead["run_id"] == result["run_id"] for lead in leads))
        # جست‌وجو هم کار می‌کند
        self.assertEqual(storage.search_leads(run_id=result["run_id"])["total"], len(leads))

    def test_worker_progress_is_persisted_step_by_step(self):
        result = jobs.enqueue_run(demo_request(limit=4), run_id="wk-run-2")
        seen = []
        original = storage.update_run

        def spy(run_id, **fields):
            if fields.get("progress") and fields["progress"].get("percent"):
                seen.append(fields["progress"]["percent"])
            return original(run_id, **fields)

        storage.update_run = spy
        try:
            worker.run_worker(once=True, engine_mode="demo", quiet=True)
        finally:
            storage.update_run = original
        self.assertTrue(seen)
        self.assertEqual(seen, sorted(seen))       # درصد فقط رو به جلو
        self.assertEqual(storage.get_run(result["run_id"])["progress"]["stage"], "done")

    def test_worker_requeues_a_job_when_engine_is_missing(self):
        result = jobs.enqueue_run(demo_request(limit=2), run_id="wk-run-3")
        summary = worker.run_worker(once=True, engine_mode="docker", quiet=True)
        self.assertEqual(summary["failed"], 1)
        with storage._connect() as conn:
            row = conn.execute("select status, attempts, last_error from jobs where run_id = ?",
                               (result["run_id"],)).fetchone()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["attempts"], 1)
        self.assertIn("Connection refused", row["last_error"])
        run = storage.get_run(result["run_id"])
        self.assertEqual(run["status"], "queued")
        self.assertIn("دوباره در صف", run["progress"]["message"])
        self.assertIn("تلاش 1", run["progress"]["message"])

    def test_worker_gives_up_after_max_attempts(self):
        result = jobs.enqueue_run(demo_request(limit=2), run_id="wk-run-4")
        for _ in range(config.WORKER_MAX_ATTEMPTS):
            worker.run_worker(once=True, engine_mode="docker", quiet=True, interval=0.0)
        run = storage.get_run(result["run_id"])
        self.assertEqual(run["status"], "failed")
        self.assertIn("ناموفق شد", run["progress"]["message"])
        self.assertIn(f"{config.WORKER_MAX_ATTEMPTS} تلاش", run["progress"]["message"])
        with storage._connect() as conn:
            row = conn.execute("select status, attempts from jobs where run_id = ?",
                               (result["run_id"],)).fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["attempts"], config.WORKER_MAX_ATTEMPTS)
        self.assertIsNone(storage.claim_job("worker-a", config.WORKER_MAX_ATTEMPTS))

    def test_check_engine_reports_active_mode_and_reason(self):
        demo = worker.check_engine("demo")
        self.assertEqual(demo["active"], "demo")
        self.assertFalse(demo["live"])
        self.assertTrue(demo["reason"])
        docker = worker.check_engine("docker")
        self.assertEqual(docker["active"], "unavailable")   # داکر در محیط آزمون نیست
        self.assertTrue(docker["reason"])


class DeliveryTests(unittest.TestCase):
    """گزارش ایمیلی و پیش‌نویس تماس — در حالت خشک (بدون ارسال واقعی)."""

    def setUp(self):
        storage.init_db()
        clear_queue()
        with storage._connect() as conn:
            conn.execute("delete from deliveries")
        self.run_id = "dl-run-1"
        if not storage.get_run(self.run_id):
            jobs.enqueue_run(demo_request(limit=6), run_id=self.run_id)
            worker.run_worker(once=True, engine_mode="demo", quiet=True)
        self._env = {key: os.environ.get(key) for key in
                     ("DIGEST_TO", "LEADGEN_DELIVERY_DRY", "APP_BASE_URL")}
        os.environ["LEADGEN_DELIVERY_DRY"] = "1"
        os.environ["DIGEST_TO"] = "ops@example.com, sales@example.com"
        os.environ["APP_BASE_URL"] = "https://app.example.com/"

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_collect_returns_hot_leads_with_exact_location(self):
        payload = delivery.collect(self.run_id, limit=5)
        self.assertEqual(payload["run"]["id"], self.run_id)
        self.assertTrue(payload["leads"])
        self.assertLessEqual(payload["shown"], 5)
        self.assertEqual(payload["app_url"], "https://app.example.com")
        for lead in payload["leads"]:
            self.assertIn("latitude", lead)
            self.assertIn("longitude", lead)
            self.assertTrue(lead["title"])

    def test_subject_mentions_count_and_niche(self):
        payload = delivery.collect(self.run_id, limit=5)
        subject = delivery.subject_for(payload)
        self.assertIn("گزارش لیدهای داغ", subject)
        self.assertIn("کافه", subject)

    def test_render_text_lists_leads_contacts_and_no_raw_dicts(self):
        payload = delivery.collect(self.run_id, limit=5)
        text = delivery.render_text(payload)
        self.assertIn("لیدهای پیشنهادی برای تماس امروز", text)
        self.assertIn("لوکیشن دقیق", text)
        self.assertNotIn("{'kind'", text)          # بینش‌ها باید متن باشند نه dict
        self.assertIn(payload["leads"][0]["title"], text)

    def test_render_html_is_rtl_with_table_and_kpis(self):
        payload = delivery.collect(self.run_id, limit=5)
        html = delivery.render_html(payload)
        self.assertIn('dir="rtl"', html)
        self.assertIn("<table", html)
        self.assertIn("لید در این گزارش", html)
        self.assertNotIn("{'kind'", html)
        self.assertGreaterEqual(html.count("<tr"), len(payload["leads"]))
        self.assertIn("google.com/maps", html)     # لینک نقشه برای لوکیشن دقیق

    def test_attachment_is_a_valid_xlsx_with_expected_sheets(self):
        payload = delivery.collect(self.run_id, limit=5)
        attachment = delivery.build_attachment(payload)
        self.assertTrue(attachment["name"].endswith(".xlsx"))
        self.assertEqual(attachment["mime"], delivery.XLSX_MIME)
        self.assertEqual(attachment["bytes"][:2], b"PK")
        import zipfile
        with zipfile.ZipFile(io.BytesIO(attachment["bytes"])) as book:
            names = book.namelist()
            workbook = book.read("xl/workbook.xml").decode("utf-8")
        self.assertIn("xl/worksheets/sheet1.xml", names)
        self.assertIn("لیدها", workbook)            # برگهٔ لیدها در اکسل پیوست است

    def test_digest_dry_run_writes_eml_and_logs_delivery(self):
        result = delivery.send_digest(self.run_id, limit=4)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["target"], "ops@example.com, sales@example.com")
        self.assertEqual(result["shown"], 4)
        path = Path(result["path"])
        self.assertTrue(path.exists())
        message = email.message_from_bytes(path.read_bytes(), policy=policy.default)
        self.assertEqual(message["Subject"], result["subject"])
        self.assertIn("sales@example.com", message["To"])
        types = [(part.get_content_type(), part.get_filename())
                 for part in message.walk() if not part.is_multipart()]
        self.assertIn(("text/plain", None), types)
        self.assertIn(("text/html", None), types)
        self.assertTrue(any(t == delivery.XLSX_MIME for t, _ in types))
        # حالت خشک «ارسال‌شده» حساب نمی‌شود تا dedupe اشتباه نکند
        self.assertIsNone(storage.last_delivery("digest"))
        with storage._connect() as conn:
            row = conn.execute("select kind, status, target, detail from deliveries"
                               " order by created_at desc limit 1").fetchone()
        self.assertEqual(row["kind"], "digest")
        self.assertEqual(row["status"], "dry_run")
        self.assertEqual(row["target"], "ops@example.com, sales@example.com")
        self.assertIn("attachment", storage._json_or(row["detail"], {}))
        # یک ارسال واقعی باید برای dedupe دیده شود
        storage.log_delivery("digest", "email", "ops@example.com", result["subject"], "sent",
                             {"shown": result["shown"]})
        logged = storage.last_delivery("digest")
        self.assertEqual(logged["status"], "sent")
        self.assertEqual(logged["detail"]["shown"], result["shown"])

    def test_digest_without_recipient_is_skipped_not_sent(self):
        os.environ["DIGEST_TO"] = ""
        result = delivery.send_digest(self.run_id, limit=2)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_recipient")
        self.assertIsNone(storage.last_delivery("digest"))

    def test_lead_drafts_use_campaign_copy_and_only_emailable_leads(self):
        drafts = delivery.build_lead_drafts(3, run_id=self.run_id)
        self.assertTrue(drafts)
        for draft in drafts:
            self.assertTrue(draft["to"])
            self.assertTrue(draft["subject"])
            self.assertIn(draft["title"], draft["text"])
            self.assertGreater(len(draft["text"]), 120)

    def test_create_drafts_writes_one_eml_per_lead(self):
        before = len(glob.glob(str(config.EXPORT_DIR / "*.eml")))
        result = delivery.create_drafts(2, run_id=self.run_id, dry_run=True)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["status"], "dry_run")
        paths = {item["path"] for item in result["items"]}
        self.assertEqual(len(paths), 2)            # نام یکتا برای هر پیش‌نویس
        after = len(glob.glob(str(config.EXPORT_DIR / "*.eml")))
        self.assertEqual(after - before, 2)
        message = email.message_from_bytes(Path(result["items"][0]["path"]).read_bytes(),
                                           policy=policy.default)
        self.assertEqual([p.get_content_type() for p in message.walk()
                          if not p.is_multipart()], ["text/plain"])
        with storage._connect() as conn:
            row = conn.execute("select kind, channel, status from deliveries"
                               " where kind='draft' order by created_at desc limit 1").fetchone()
        self.assertEqual(row["channel"], "email")
        self.assertEqual(row["status"], "dry_run")

    def test_email_transport_selection(self):
        keys = ("GMAIL_REFRESH_TOKEN", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET",
                "SMTP_PASSWORD", "GMAIL_APP_PASSWORD", "GMAIL_SENDER", "SMTP_USER")
        original = {key: os.environ.get(key) for key in keys}
        try:
            for key in keys:
                os.environ.pop(key, None)
            # هیچ اعتبارنامه‌ای → ارسال ممکن نیست (حالت خشک)
            self.assertEqual(config.email_transport(), "none")
            self.assertEqual(config.digest_from(), "")
            # رمز برنامهٔ Gmail + آدرس فرستنده → SMTP (مناسب Codespace/ماشین خودتان)
            os.environ["GMAIL_SENDER"] = "me@gmail.com"
            os.environ["GMAIL_APP_PASSWORD"] = "app-password"
            self.assertEqual(config.email_transport(), "smtp")
            self.assertEqual(config.smtp_credentials()["host"], "smtp.gmail.com")
            self.assertEqual(config.smtp_credentials()["port"], 587)
            self.assertEqual(config.smtp_credentials()["user"], "me@gmail.com")
            self.assertEqual(config.digest_from(), "me@gmail.com")
            # OAuth (refresh token + client) → gmail_api، تنها مسیر ممکن روی Vercel
            os.environ["GMAIL_REFRESH_TOKEN"] = "refresh"
            os.environ["GMAIL_CLIENT_ID"] = "client-id"
            os.environ["GMAIL_CLIENT_SECRET"] = "client-secret"
            self.assertEqual(config.email_transport(), "gmail_api")
            self.assertEqual(config.gmail_credentials()["refresh_token"], "refresh")
            # گیرنده‌ها زنده از DIGEST_TO خوانده می‌شوند
            os.environ["DIGEST_TO"] = "a@example.com, b@example.com"
            self.assertEqual(config.digest_recipients(), ["a@example.com", "b@example.com"])
            os.environ["APP_BASE_URL"] = "https://app.example.com/"
            self.assertEqual(config.app_base_url(), "https://app.example.com")
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_send_email_requires_a_recipient(self):
        with self.assertRaises(delivery.DeliveryError):
            delivery.send_email([], "موضوع", "<b>x</b>", "متن", dry_run=True)


class DashboardStatsTests(unittest.TestCase):
    def setUp(self):
        storage.init_db()

    def test_stats_summarize_runs_leads_and_queue(self):
        stats = storage.dashboard_stats()
        for key in ("runs", "leads", "hot", "with_email", "with_phone", "with_site",
                    "contacted", "won", "queue"):
            self.assertIn(key, stats)
        self.assertEqual(stats["leads"], storage.pipeline_stats()["leads"])
        self.assertIsInstance(stats["latest_runs"], list)
        self.assertEqual(stats["queue"]["failed"], storage.queue_stats()["failed"])


if __name__ == "__main__":
    unittest.main()
