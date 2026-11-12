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
    "nova": (ROOT / "nova" / "index.html", [ROOT / "nova" / "app.js",
                                            ROOT / "nova" / "ui.js"]),
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

    def test_nova_ids_exist(self):
        html = self.check_surface("nova")
        self.has(html, "nova/index.html",
                 ['dir="rtl"', 'id="tabbar"', 'id="dock"', 'id="sheetRoot"',
                  'rel="manifest"', 'id="view-hunt"', 'id="view-leads"',
                  'id="view-runs"', 'id="view-more"', 'id="goalChips"'])

    def test_nova_is_wired_to_engine_queue_and_pwa(self):
        app = self.read("nova/app.js")
        self.has(app, "nova/app.js",
                 ['"api/runs"', 'api/search?', 'api/queue', 'api/health',
                  'api/smart/parse', 'api/smart/suggest', 'api/leads/',
                  'registerSW', 'beforeinstallprompt'])
        ui = self.read("nova/ui.js")
        self.has(ui, "nova/ui.js", ['serviceWorker', 'setTab', 'sheet(', 'ringHtml'])

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


class OpsCronSqlTests(unittest.TestCase):
    """فایل زمان‌بندی Supabase باید همان قراردادی را داشته باشد که اپ انتظار دارد."""

    def test_cron_sql_schedules_the_tick_endpoint(self):
        sql = (ROOT / "supabase" / "cron_tick.sql").read_text(encoding="utf-8")
        self.assertIn("cron.schedule", sql)
        self.assertIn("net.http_get", sql)
        self.assertIn("/api/worker/tick", sql)
        self.assertIn("budget=", sql)
        # دوباره‌اجرا باید بی‌خطر باشد (جایگزینی زمان‌بندی قبلی)
        self.assertIn("cron.unschedule", sql)
        self.assertIn("create extension if not exists pg_net", sql)
        self.assertIn("create extension if not exists pg_cron", sql)

    def test_readme_documents_the_database_driver(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("supabase/cron_tick.sql", readme)


class SocialLinkContractTests(unittest.TestCase):
    """لینک شبکه اجتماعی باید از «هندل» و «آدرس کامل» هر دو درست ساخته شود.

    دادهٔ واقعی هم هندل می‌دهد (`cafesaadeh`) و هم لینک کامل
    (`https://www.instagram.com/cafesaadeh/`). پیشوند زدنِ کورکورانه به لینک کامل،
    آدرس خراب می‌سازد؛ پس هر رابط باید از `socialHref` استفاده کند.
    """

    SURFACES = {
        "web": [ROOT / "web" / "app.js"],
        "smart": [ROOT / "smart" / "app.js"],
        "mini": [ROOT / "mini" / "app.js"],
        "nova": [ROOT / "nova" / "app.js", ROOT / "nova" / "ui.js"],
    }

    def test_each_surface_defines_the_helper(self):
        for name, files in self.SURFACES.items():
            blob = "\n".join(f.read_text(encoding="utf-8") for f in files)
            self.assertIn("function socialHref(", blob, name)
            self.assertIn("function socialName(", blob, name)

    def test_no_blind_prefix_on_social_links(self):
        for name, files in self.SURFACES.items():
            for f in files:
                text = f.read_text(encoding="utf-8")
                self.assertNotIn("instagram.com/${", text, f"{name}: {f.name}")
                self.assertNotIn("instagram.com/' +", text, f"{name}: {f.name}")
                self.assertNotIn("instagram.com/\" +", text, f"{name}: {f.name}")

    def test_helper_handles_handle_and_full_url(self):
        """منطق تابع باید هر سه ورودی را به یک آدرس مطلق برساند (اجرای واقعی در Node)."""
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("Node در دسترس نیست")
        script = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const body = src.match(/function socialHref\(platform, value\) \{[\s\S]*?\n\}/)[0];
eval(body);
const out = [
  socialHref("instagram", "cafesaadeh"),
  socialHref("instagram", "@cafesaadeh"),
  socialHref("instagram", "https://www.instagram.com/cafesaadeh/"),
  socialHref("facebook", "facebook.com/xcafe"),
  socialHref("linkedin", "parsa-clinic"),
  socialHref("instagram", ""),
];
process.stdout.write(JSON.stringify(out));
"""
        result = subprocess.run([node, "-e", script, str(ROOT / "web" / "app.js")],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        import json as json_mod
        values = json_mod.loads(result.stdout)
        self.assertEqual(values[0], "https://instagram.com/cafesaadeh")
        self.assertEqual(values[1], "https://instagram.com/cafesaadeh")
        self.assertEqual(values[2], "https://www.instagram.com/cafesaadeh/")
        self.assertEqual(values[3], "https://facebook.com/xcafe")
        self.assertEqual(values[4], "https://linkedin.com/company/parsa-clinic")
        self.assertEqual(values[5], "")
