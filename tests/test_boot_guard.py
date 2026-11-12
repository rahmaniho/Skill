"""تور ایمنی پایگاه‌داده: آزمون‌ها هرگز به Supabase زنده وصل نمی‌شوند.

چرا این تست مهم است؟ در Codespaces و روی Vercel کلیدهای Supabase به‌صورت متغیر
محیطی حاضرند. یک آزمونِ سهل‌انگارانه می‌تواند اجرای واقعی بسازد، لید اضافه کند یا
کار در صف بگذارد — یعنی دادهٔ مشتری. این تست همان سناریو را با کلیدهای ساختگی
بازسازی می‌کند و مطمئن می‌شود پایگاه‌داده محلی می‌ماند.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from leadgen import config

ROOT = Path(__file__).resolve().parent.parent
CLOUD_ENV = {"SUPABASE_URL": "https://guard-probe.supabase.co",
             "SUPABASE_SERVICE_KEY": "service-role-key-for-guard-probe"}


class CloudIsolationTests(unittest.TestCase):
    def test_current_test_process_is_local(self):
        self.assertEqual(config.DB_BACKEND, "sqlite")
        self.assertFalse(config.supabase_ready())

    def test_detector_recognises_unittest(self):
        self.assertTrue(config._looks_like_unittest())

    def test_detector_has_an_explicit_escape_hatch(self):
        with mock.patch.dict(os.environ, {"LEADGEN_TEST_CLOUD": "1"}):
            self.assertFalse(config._looks_like_unittest())

    def test_cloud_credentials_in_env_do_not_reach_tests(self):
        env = {**os.environ, **CLOUD_ENV}
        env.pop("LEADGEN_TEST_CLOUD", None)
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "-q", "tests.guard_probe"],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0,
                         f"stdout={result.stdout}\nstderr={result.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
