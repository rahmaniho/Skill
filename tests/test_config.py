"""آزمون لایهٔ تنظیمات — همان چیزی که استقرار ابری روی آن سوار است.

چرا مهم است؟ چون یک اپ در سه محیط اجرا می‌شود:
  ۱) محلی (`.env`)، ۲) Vercel (متغیرهای محیطی پروژه)، ۳) Codespaces (Secrets).
در هر سه، **متغیر محیطی واقعی باید بر `.env` قالب باشد** وگرنه مقادیر خالیِ
`.env.example` (که خودکار کپی می‌شود) اتصال به Supabase را می‌شکنند.

اجرا:  python3 -m unittest tests.test_config -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leadgen import config  # noqa: E402

# کلیدهایی که یک استقرار واقعی به آن‌ها نیاز دارد (Vercel env + Codespaces secrets)
REQUIRED_KEYS = (
    "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY", "LEADGEN_DB",
    "LEADGEN_QUEUE", "LEADGEN_DATA_DIR", "LEADGEN_ROOT_UI", "WORKER_ID",
    "DIGEST_TO", "DIGEST_FROM", "APP_BASE_URL",
    "GMAIL_SENDER", "GMAIL_APP_PASSWORD", "GMAIL_REFRESH_TOKEN",
    "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "SMTP_HOST", "SMTP_PORT",
    "SCRAPER_BASE_URL", "SCRAPER_API_KEY", "ENGINE_MODE",
)


class EnvFileTests(unittest.TestCase):
    """`.env` نباید هرگز متغیر محیطی واقعی را بازنویسی کند."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="leadgen-env-"))
        self.saved = {k: os.environ.get(k) for k in REQUIRED_KEYS}

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def write_env(self, text: str) -> Path:
        path = self.tmp / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_real_environment_wins_over_env_file(self):
        os.environ["SUPABASE_URL"] = "https://real.supabase.co"
        config.load_env_file(self.write_env("SUPABASE_URL=https://placeholder.supabase.co\n"))
        self.assertEqual("https://real.supabase.co", os.environ["SUPABASE_URL"])

    def test_missing_key_is_filled_from_env_file(self):
        os.environ.pop("SUPABASE_URL", None)
        config.load_env_file(self.write_env("SUPABASE_URL=https://from-file.supabase.co\n"))
        self.assertEqual("https://from-file.supabase.co", os.environ["SUPABASE_URL"])

    def test_quotes_comments_and_blank_lines(self):
        os.environ.pop("DIGEST_TO", None)
        config.load_env_file(self.write_env(
            "\n# توضیح فارسی\nDIGEST_TO=\"a@x.com, b@x.com\"\n\nNOT_A_KEY=1\n"))
        self.assertEqual("a@x.com, b@x.com", os.environ["DIGEST_TO"])
        self.assertNotIn("NOT_A_KEY", os.environ)

    def test_missing_file_is_silent(self):
        config.load_env_file(self.tmp / "nope.env")     # نباید استثنا بدهد

    def test_all_required_keys_are_supported(self):
        missing = [k for k in REQUIRED_KEYS if k not in config.ENV_KEYS]
        self.assertEqual([], missing, f"این کلیدها از .env خوانده نمی‌شوند: {missing}")


class LiveGetterTests(unittest.TestCase):
    """گیرنده‌ها و اعتبارنامه‌ها باید زنده خوانده شوند (بدون ری‌استارت)."""

    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in REQUIRED_KEYS}

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def clear_email(self):
        for key in ("GMAIL_SENDER", "GMAIL_APP_PASSWORD", "GMAIL_REFRESH_TOKEN",
                    "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "SMTP_USER", "SMTP_PASSWORD"):
            os.environ.pop(key, None)

    def test_digest_recipients_are_live_and_trimmed(self):
        os.environ["DIGEST_TO"] = " ops@example.com ,team@example.com "
        self.assertEqual(["ops@example.com", "team@example.com"], config.digest_recipients())
        os.environ["DIGEST_TO"] = ""
        self.assertEqual([], config.digest_recipients())

    def test_email_transport_prefers_gmail_api_then_smtp(self):
        self.clear_email()
        self.assertEqual("none", config.email_transport())
        os.environ["SMTP_USER"] = "me@gmail.com"
        os.environ["SMTP_PASSWORD"] = "app-password"
        self.assertEqual("smtp", config.email_transport())
        os.environ["GMAIL_REFRESH_TOKEN"] = "1//token"
        os.environ["GMAIL_CLIENT_ID"] = "id.apps.googleusercontent.com"
        os.environ["GMAIL_CLIENT_SECRET"] = "secret"
        self.assertEqual("gmail_api", config.email_transport())

    def test_gmail_api_needs_all_three_pieces(self):
        self.clear_email()
        os.environ["GMAIL_REFRESH_TOKEN"] = "1//token"
        os.environ["GMAIL_CLIENT_ID"] = "id"
        self.assertNotEqual("gmail_api", config.email_transport())

    def test_supabase_ready_needs_url_and_key(self):
        os.environ["SUPABASE_URL"] = "https://ref.supabase.co"
        os.environ.pop("SUPABASE_SERVICE_KEY", None)
        os.environ.pop("SUPABASE_ANON_KEY", None)
        self.assertFalse(config.supabase_ready())
        os.environ["SUPABASE_SERVICE_KEY"] = "service-key"
        # DB_BACKEND در import ثابت می‌شود؛ پس فقط شرط کلید/نشانی را می‌سنجیم
        self.assertTrue(bool(config.env("SUPABASE_URL") and config.env("SUPABASE_SERVICE_KEY")))

    def test_queue_modes(self):
        saved = config.QUEUE_MODE
        try:
            for mode, expected in (("always", True), ("never", False)):
                config.QUEUE_MODE = mode
                self.assertEqual(expected, config.queue_enabled(), mode)
            config.QUEUE_MODE = "auto"
            os.environ.pop("LEADGEN_WORKER", None)
            self.assertIsInstance(config.queue_enabled(), bool)
            os.environ["LEADGEN_WORKER"] = "1"
            self.assertTrue(config.queue_enabled())
        finally:
            config.QUEUE_MODE = saved
            os.environ.pop("LEADGEN_WORKER", None)

    def test_app_base_url_has_no_trailing_slash(self):
        os.environ["APP_BASE_URL"] = "https://leadgen-studio-umber.vercel.app/"
        self.assertEqual("https://leadgen-studio-umber.vercel.app", config.app_base_url())

    def test_summary_reports_live_values(self):
        os.environ["DIGEST_TO"] = "ops@example.com"
        os.environ["APP_BASE_URL"] = "https://app.example.com"
        summary = config.summary()
        self.assertEqual(["ops@example.com"], summary["digest_to"])
        self.assertEqual("https://app.example.com", summary["app_base_url"])
        for key in ("db_backend", "supabase_ready", "queue_mode", "queue_enabled",
                    "email_transport", "worker_id", "lead_fields"):
            self.assertIn(key, summary)


class LiveDbSafetyTests(unittest.TestCase):
    """اجرای آزمون نباید هرگز به پایگاه‌دادهٔ زنده برود — ولی اپ واقعی باید برود.

    در Codespaces/Vercel کلیدهای Supabase به‌صورت متغیر محیطی حاضرند. اگر `storage`
    حین تست به Postgres زنده وصل شود، تست‌های صف/ذخیره‌سازی دادهٔ واقعی را آلوده می‌کنند.
    """

    CLOUD_ENV = {"SUPABASE_URL": "https://live-ref.supabase.co",
                 "SUPABASE_SERVICE_KEY": "live-service-key",
                 "LEADGEN_DB": "supabase"}

    def child_env(self, extra=None):
        env = {k: v for k, v in os.environ.items()
               if k not in ("LEADGEN_DB", "SUPABASE_URL", "SUPABASE_SERVICE_KEY",
                            "SUPABASE_ANON_KEY", "LEADGEN_TEST_CLOUD")}
        env.update(self.CLOUD_ENV)
        env["PYTHONPATH"] = str(ROOT)
        env.update(extra or {})
        return env

    def run_python(self, code, extra=None):
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                              env=self.child_env(extra), capture_output=True, text=True,
                              timeout=120)
        return proc

    def test_plain_app_process_still_uses_cloud(self):
        """غیر از آزمون، کلیدهای ابری باید به supabase منجر شوند (ورسل/Codespaces)."""
        proc = self.run_python(
            "from leadgen import config;"
            "print(config.DB_BACKEND, config.supabase_ready(), config.TESTING)")
        self.assertEqual(0, proc.returncode, proc.stderr[-800:])
        self.assertEqual("supabase True False", proc.stdout.strip())

    def test_unittest_process_is_pinned_to_sqlite(self):
        """حتی با شکل قدیمی `discover -s tests` (که tests/__init__.py اجرا نمی‌شود)."""
        tmp = Path(tempfile.mkdtemp(prefix="leadgen-guard-"))
        (tmp / "test_probe.py").write_text(
            "import unittest\n"
            "from leadgen import config\n\n"
            "class Probe(unittest.TestCase):\n"
            "    def test_backend_is_sqlite(self):\n"
            "        self.assertEqual('sqlite', config.DB_BACKEND)\n"
            "        self.assertFalse(config.supabase_ready())\n"
            "        self.assertTrue(config.TESTING)\n",
            encoding="utf-8")
        proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(tmp)],
                              cwd=str(tmp), env=self.child_env(), capture_output=True,
                              text=True, timeout=180)
        self.assertEqual(0, proc.returncode, proc.stderr[-1500:])
        self.assertIn("OK", proc.stderr + proc.stdout)

    def test_direct_test_file_run_is_also_guarded(self):
        tmp = Path(tempfile.mkdtemp(prefix="leadgen-guard-")) / "tests"
        tmp.mkdir(parents=True)
        (tmp / "test_probe.py").write_text(
            "from leadgen import config\n\n"
            "assert config.DB_BACKEND == 'sqlite', config.DB_BACKEND\n"
            "assert config.TESTING is True\n"
            "print('GUARDED')\n", encoding="utf-8")
        proc = subprocess.run([sys.executable, str(tmp / "test_probe.py")], cwd=str(ROOT),
                              env=self.child_env(), capture_output=True, text=True, timeout=120)
        self.assertEqual(0, proc.returncode, proc.stderr[-800:])
        self.assertIn("GUARDED", proc.stdout)

    def test_explicit_opt_in_reenables_cloud_for_manual_checks(self):
        proc = self.run_python("from leadgen import config; print(config.TESTING)",
                               extra={"LEADGEN_TEST_CLOUD": "1"})
        self.assertEqual("False", proc.stdout.strip())


class DevContainerContractTests(unittest.TestCase):
    """Codespaces: تعریف کانتینر باید همان کلیدهایی را بخواهد که کد می‌خواند."""

    def setUp(self):
        self.cfg = json.loads((ROOT / ".devcontainer" / "devcontainer.json")
                              .read_text(encoding="utf-8"))

    def test_docker_feature_is_present(self):
        features = self.cfg.get("features") or {}
        self.assertTrue(any("docker-in-docker" in name for name in features),
                        f"موتور واقعی به داکر نیاز دارد؛ features: {list(features)}")

    def test_secret_keys_are_supported_by_config(self):
        secrets = self.cfg.get("secrets") or {}
        self.assertTrue(secrets, "devcontainer.json باید secretهای لازم را اعلام کند")
        unknown = [name for name in secrets if name not in config.ENV_KEYS]
        self.assertEqual([], unknown, f"این secretها در ENV_KEYS نیستند: {unknown}")

    def test_core_secrets_are_declared(self):
        secrets = set(self.cfg.get("secrets") or {})
        for name in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "DIGEST_TO"):
            self.assertIn(name, secrets)

    def test_lifecycle_scripts_exist_and_are_executable(self):
        self.assertEqual("bash .devcontainer/setup.sh", self.cfg.get("postCreateCommand"))
        self.assertEqual("bash .devcontainer/start-worker.sh", self.cfg.get("postStartCommand"))
        for script in ("setup.sh", "start-worker.sh"):
            path = ROOT / ".devcontainer" / script
            self.assertTrue(path.exists(), script)
            self.assertTrue(os.access(path, os.X_OK), f"{script} باید اجرایی باشد")

    def test_scraper_port_is_not_public(self):
        attrs = self.cfg.get("portsAttributes") or {}
        self.assertEqual("private", (attrs.get("8080") or {}).get("visibility"),
                         "موتور اسکراپر نباید عمومی شود (بدون احراز هویت است)")


class EnvExampleTests(unittest.TestCase):
    def test_example_documents_every_required_key(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        missing = [key for key in REQUIRED_KEYS if f"{key}=" not in text]
        self.assertEqual([], missing, f".env.example این کلیدها را ندارد: {missing}")


if __name__ == "__main__":
    unittest.main()
