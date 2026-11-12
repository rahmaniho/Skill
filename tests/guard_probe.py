"""کاوشگر تور ایمنی: فقط با `python3 -m unittest tests.guard_probe` اجرا می‌شود.

این فایل عمداً با الگوی `test*.py` شروع نمی‌شود تا در جاروی عادی آزمون‌ها دوبار
اجرا نشود؛ اما وقتی با متغیرهای محیطی ابری صدا زده شود، باید همان‌جا هم SQLite
بماند (پایگاه‌دادهٔ زندهٔ مشتری آلوده نشود).
"""
from __future__ import annotations

import unittest

from leadgen import config


class GuardProbe(unittest.TestCase):
    def test_backend_is_local_even_with_cloud_env(self):
        self.assertEqual(config.DB_BACKEND, "sqlite",
                         "آزمون‌ها نباید به Supabase زنده وصل شوند")
        self.assertFalse(config.supabase_ready())


if __name__ == "__main__":
    unittest.main(verbosity=2)
