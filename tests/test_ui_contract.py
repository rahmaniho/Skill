"""قرارداد رابط کاربری: هر id که جاوااسکریپت می‌خواند باید در HTML وجود داشته باشد.

چرا؟ رابط ساده (`smart/`) و داشبورد حرفه‌ای (`web/`) بدون هیچ فریم‌ورکی نوشته شده‌اند و با
`$("id")` / `$("#id")` به عنصرها دسترسی دارند. اگر id وجود نداشته باشد، خطا فقط در مرورگر و
بی‌صدا ظاهر می‌شود (`TypeError: cannot read properties of null`). این تست همان را در CI می‌گیرد.

idهایی که خودِ جاوااسکریپت داخل قالب رشته می‌سازد (محتوای کشو/مودال/جدول‌های پویا) پذیرفته‌اند،
چون پیش از خوانده‌شدن به DOM اضافه می‌شوند.

اجرا:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SURFACES = {
    "smart": (ROOT / "smart" / "index.html", [ROOT / "smart" / "app.js",
                                              ROOT / "smart" / "campaign.js"]),
    "web": (ROOT / "web" / "index.html", [ROOT / "web" / "app.js"]),
    "mini": (ROOT / "mini" / "index.html", [ROOT / "mini" / "app.js"]),
}

# عنصرهایی که فقط در نسخهٔ ابری معنا دارند و نبودشان خطا نیست
OPTIONAL = {"queueLine", "queueText", "queueBtn", "deliveryNote"}

ID_IN_HTML = re.compile(r"""\bid\s*=\s*["']([A-Za-z][\w\-]*)["']""")
GET_ELEMENT = re.compile(r"""\$\(\s*["']#?([A-Za-z][\w\-]*)["']\s*(?:,\s*[\w.]+\s*)?\)""")
BY_ID = re.compile(r"""getElementById\(\s*["']([A-Za-z][\w\-]*)["']""")


class UiContractTests(unittest.TestCase):
    @staticmethod
    def read(relative: str) -> str:
        path = ROOT / relative
        if not path.exists():
            raise unittest.SkipTest(f"{relative} پیدا نشد")
        return path.read_text(encoding="utf-8")

    def has(self, text: str, label: str, needles: list[str]) -> None:
        """پیام خطای کوتاه (assertIn کل فایل را چاپ می‌کند و خروجی را می‌بلعد)."""
        missing = [needle for needle in needles if needle not in text]
        self.assertFalse(missing, f"{label}: این عبارت‌ها پیدا نشد → {missing}")

    def check_surface(self, name: str) -> str:
        html_path, scripts = SURFACES[name]
        self.assertTrue(html_path.exists(), f"{html_path} پیدا نشد")
        html = html_path.read_text(encoding="utf-8")
        html_ids = set(ID_IN_HTML.findall(html))

        js_ids: set = set()
        referenced: set = set()
        for script in scripts:
            if not script.exists():
                continue
            text = script.read_text(encoding="utf-8")
            referenced |= set(GET_ELEMENT.findall(text)) | set(BY_ID.findall(text))
            js_ids |= set(ID_IN_HTML.findall(text))

        missing = sorted(referenced - html_ids - js_ids - OPTIONAL)
        self.assertFalse(missing,
                         f"{name}: این idها در جاوااسکریپت خوانده می‌شوند ولی در HTML نیستند → "
                         f"{missing}")
        return html

    # ── وجود عنصرها ─────────────────────────────────────────────────────────
    def test_smart_ids_exist(self):
        html = self.check_surface("smart")
        self.has(html, "smart/index.html",
                 ['id="queueLine"', 'id="queueText"', 'id="queueBtn"', 'id="deliveryNote"',
                  'data-deliver="digest"', 'data-deliver="drafts"'])

    def test_web_ids_exist(self):
        html = self.check_surface("web")
        self.has(html, "web/index.html",
                 ['id="view-ops"', 'data-view="ops"', 'id="queueStats"', 'id="queueMode"',
                  'id="jobsTable"', 'id="workersBox"', 'id="queueRefresh"', 'id="queueAuto"',
                  'id="deliveryStatus"', 'id="dlvTo"', 'id="dlvLimit"', 'id="dlvSegment"',
                  'id="dlvRun"', 'id="dlvDry"', 'id="dlvDigest"', 'id="dlvDrafts"',
                  'id="deliveryLog"', 'id="lastDelivery"'])

    def test_mini_ids_exist(self):
        self.check_surface("mini")

    # ── سیم‌کشی صف و ارسال ──────────────────────────────────────────────────
    def test_smart_ui_is_wired_to_queue_and_delivery(self):
        smart = self.read("smart/app.js")
        self.has(smart, "smart/app.js",
                 ['/api/queue', '/api/queue/jobs', '/api/delivery/status', '/api/delivery/',
                  'renderQueueLine', 'hideQueueLine', 'refreshQueue', 'doDeliver',
                  'renderDeliveryNote', 'data-deliver'])
        # بنر صف باید جای کاربر و زمان انتظار را از پاسخ API بگیرد
        self.has(smart, "smart/app.js",
                 ['started.status === "queued"', 'position.message', 'position.wait_seconds',
                  'evt.queue'])

    def test_web_ui_is_wired_to_queue_and_delivery(self):
        web = self.read("web/app.js")
        self.has(web, "web/app.js",
                 ['/api/queue', '/api/queue/jobs', '/api/delivery/status', '/api/delivery/',
                  'loadOps', 'loadQueue', 'renderQueue', 'loadDelivery', 'sendDelivery',
                  'sendDelivery("digest")', 'sendDelivery("drafts")', 'stopOpsTimer',
                  'if (view === "ops") loadOps();'])

    def test_demo_mode_builds_delivery_locally(self):
        """در نسخهٔ ایستا (GitHub Pages) سروری نیست → گزارش/پیش‌نویس باید محلی ساخته شود."""
        smart = self.read("smart/app.js")
        self.has(smart, "smart/app.js",
                 ['function demoDeliver', 'leadgen-digest.txt', 'leadgen-drafts.txt',
                  'حالت دمو', 'downloadBlob', 'async deliver(kind) { return demoDeliver(kind); }'])

    def test_delivery_routes_exist_in_server(self):
        """مسیرهایی که رابط صدا می‌زند باید در سرور پیاده شده باشند."""
        server = self.read("leadgen/server.py")
        wsgi = self.read("leadgen/wsgi.py")
        self.has(server, "leadgen/server.py",
                 ['/api/queue', '/api/queue/jobs', '/api/delivery/status',
                  '/api/delivery/digest', '/api/delivery/drafts'])
        self.assertTrue(wsgi, "leadgen/wsgi.py خالی است")   # همان هندلر برای Vercel


if __name__ == "__main__":
    unittest.main(verbosity=2)
