"""تست‌های «رابط ساده» و مغز پشت آن (leadgen/smart.py) + مسیرهای API و فایل‌های ایستا.

این تست‌ها دو لایه را پوشش می‌دهند:
  ۱) لایهٔ تصمیم‌گیری: یک جملهٔ فارسی/انگلیسی → صنف، شهر، زبان، کلیدواژه، حجم، زمان تقریبی
  ۲) لایهٔ سرویس: /api/smart/{options,suggest,parse}، شروع اجرا از جملهٔ تجزیه‌شده،
     فایل‌های smart/ روی «/» و داشبورد حرفه‌ای روی «/studio/»

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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="leadgen-smart-"))
os.environ["LEADGEN_DATA_DIR"] = str(_TMP)
os.environ["ENGINE_MODE"] = "demo"

from leadgen import niches, smart  # noqa: E402
from leadgen import server as server_mod  # noqa: E402


def _find_free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ══════════════════════════════════════════════════════════════════════════
# ۱) مغز برنامه: تجزیهٔ جملهٔ کاربر
# ══════════════════════════════════════════════════════════════════════════
class SmartBrainTestCase(unittest.TestCase):
    def test_options_shape(self):
        opts = smart.options()
        self.assertEqual(len(opts["goals"]), 5)
        self.assertEqual(len(opts["sizes"]), 3)
        self.assertGreaterEqual(len(opts["examples"]), 3)
        self.assertEqual({p["id"] for p in opts["presets"]}, {"lead", "contact", "location", "all"})
        self.assertEqual({f["id"] for f in opts["formats"]}, {"xlsx", "csv", "json"})
        self.assertEqual(len(opts["profiles"]), 5)
        self.assertEqual(len(opts["groups"]), len(niches.GROUPS))
        for goal in opts["goals"]:
            self.assertTrue(goal["fa"] and goal["profile"] and goal["icon"])
        for size in opts["sizes"]:
            self.assertIn("depth", size)
            self.assertIn("limit", size)

    def test_suggest_finds_niche_and_city(self):
        data = smart.suggest("دندان")
        self.assertIn("دندانپزشکی", [n["fa"] for n in data["niches"]])
        self.assertFalse(data["all_trades"])

        data = smart.suggest("tehran")
        self.assertIn("تهران", [c["fa"] for c in data["cities"]])
        # «tehran» نباید صنوف بی‌ربط پیشنهاد بدهد (نویز مطابقت تقریبی حذف شده)
        self.assertNotIn("رستوران", [n["fa"] for n in data["niches"]])

    def test_suggest_empty_and_all_trades(self):
        self.assertEqual(smart.suggest("")["niches"], [])
        self.assertEqual(smart.suggest("")["cities"], [])
        self.assertTrue(smart.suggest("همه مشاغل")["all_trades"])

    def test_suggest_limit(self):
        self.assertLessEqual(len(smart.suggest("ا", limit=3)["niches"]), 3)

    # ── تک‌صنفی ────────────────────────────────────────────────────────────
    def test_parse_single_niche_persian(self):
        out = smart.parse("دندانپزشکی در تهران", goal="site", size="standard")
        self.assertEqual(out["mode"], "single")
        self.assertTrue(out["ready"])
        self.assertEqual(out["missing"], [])
        d = out["detected"]
        self.assertEqual(d["niche_fa"], "دندانپزشکی")
        self.assertEqual(d["city"], "تهران")
        self.assertEqual(d["lang"], "fa")
        self.assertFalse(d["all_trades"])
        self.assertEqual(d["goal"]["profile"], "web_design")
        self.assertGreater(d["keyword_count"], 0)
        self.assertEqual(len(out["keywords"]), d["keyword_count"])

    def test_keywords_bake_city_once(self):
        out = smart.parse("کافه در اصفهان")
        self.assertTrue(out["keywords"], "کلیدواژه‌ای ساخته نشد")
        for kw in out["keywords"]:
            self.assertIn("اصفهان", kw)
            self.assertNotIn("در اصفهان در اصفهان", kw)     # شهر دوبار تزریق نشود
            self.assertEqual(kw.count("اصفهان"), 1)

    def test_eta_is_a_sane_range(self):
        for text, size in [("دندانپزشکی در تهران", "quick"),
                           ("دندانپزشکی در تهران", "standard"),
                           ("همه مشاگل در تهران", "deep")]:
            out = smart.parse(text, size=size)
            low, high = out["eta_minutes"]
            self.assertLessEqual(low, high)
            self.assertGreaterEqual(low, 1)
            self.assertLessEqual(high, 240, f"{text}/{size} → {out['eta_minutes']}")

    def test_goal_and_size_drive_request(self):
        out = smart.parse("دندانپزشکی در تهران", goal="ads", size="quick")
        self.assertEqual(out["request"]["profile"], "marketing")
        self.assertEqual(out["request"]["depth"], 2)
        self.assertEqual(out["request"]["limit"], 60)

        out = smart.parse("دندانپزشکی در تهران", goal="supply", size="deep")
        self.assertEqual(out["request"]["profile"], "b2b_supply")
        self.assertEqual(out["request"]["grid"], 3)
        self.assertEqual(out["request"]["depth"], 5)

    def test_export_options_pass_through(self):
        out = smart.parse("دندانپزشکی در تهران", cols="location", export_fmt="csv",
                          socials=False, emails=False)
        self.assertEqual(out["request"]["cols"], "location")
        self.assertEqual(out["request"]["export_fmt"], "csv")
        self.assertFalse(out["request"]["socials"])
        self.assertFalse(out["request"]["emails"])

    # ── جاروی تمامی مشاگل ─────────────────────────────────────────────────
    def test_parse_all_trades_sweep(self):
        out = smart.parse("همه مشاغل در مشهد", size="standard")
        self.assertEqual(out["mode"], "sweep")
        self.assertTrue(out["ready"])
        self.assertTrue(out["detected"]["all_trades"])
        self.assertTrue(out["request"]["all_niches"])
        self.assertEqual(out["request"]["grid"], 0)          # شبکه در جارو خاموش است
        self.assertEqual(out["request"]["city_query"], "مشهد")
        self.assertTrue(out["keywords"])
        self.assertIn("صنف", out["summary"])

    def test_parse_group_only_becomes_scoped_sweep(self):
        out = smart.parse("خدمات فنی و ساختمان در شیراز")
        self.assertEqual(out["mode"], "sweep")
        self.assertIn("خدمات فنی و ساختمان", out["detected"]["groups"])
        self.assertEqual(out["request"]["city_query"], "شیراز")
        self.assertEqual(out["request"]["niche_groups"], ["خدمات فنی و ساختمان"])
        self.assertTrue(out["request"]["all_niches"])
        # کلیدواژه‌ها باید از صنوف همان گروه باشند (باگ قدیمی: نگاشت گروه → حسابداری)
        group_words = {w for n in niches.CATALOG if n.group == "خدمات فنی و ساختمان"
                       for w in [n.fa, *n.aliases, *n.keywords_fa]}
        self.assertTrue(any(any(w in kw for w in group_words) for kw in out["keywords"]),
                        out["keywords"][:4])
        self.assertNotIn("حسابداری", " ".join(out["keywords"]))

    def test_parse_two_niches_becomes_group_sweep(self):
        out = smart.parse("کافه و رستوران در اصفهان")
        self.assertEqual(out["mode"], "sweep")
        self.assertGreaterEqual(len(out["detected"]["named_niches"]), 2)
        self.assertIn("غذا و نوشیدنی", out["detected"]["groups"])

    # ── زبان انگلیسی و شهرهای خارجی ───────────────────────────────────────
    def test_parse_english_query(self):
        out = smart.parse("plumbers in Denver CO")
        self.assertEqual(out["mode"], "single")
        self.assertTrue(out["ready"])
        self.assertEqual(out["detected"]["lang"], "en")
        self.assertEqual(out["detected"]["niche_id"], "plumber")
        self.assertIn("denver", out["detected"]["city"].lower())
        self.assertTrue(any("plumb" in kw.lower() for kw in out["keywords"]))

    # ── جملهٔ ناقص ────────────────────────────────────────────────────────
    def test_parse_empty_query_is_not_ready(self):
        out = smart.parse("")
        self.assertEqual(out["mode"], "empty")
        self.assertFalse(out["ready"])
        self.assertIn("niche", out["missing"])
        self.assertEqual(out["keywords"], [])
        self.assertEqual(out["detected"]["niche_fa"], "")     # صنف پیش‌فرض القا نشود
        self.assertFalse(out["detected"]["all_trades"])

    def test_parse_whitespace_only(self):
        self.assertFalse(smart.parse("   \n\t ")["ready"])

    def test_missing_city_warns_and_defaults(self):
        out = smart.parse("آرایشگاه")
        self.assertTrue(out["ready"])
        self.assertTrue(out["detected"]["city"])             # شهر پیش‌فرض گذاشته می‌شود
        self.assertTrue(out["warnings"])                     # ولی کاربر آگاه می‌شود
        self.assertTrue(out["needs_confirmation"])

    def test_custom_niche_not_in_catalog(self):
        out = smart.parse("فروشگاه لوازم کوهنوردی در رشت")
        self.assertTrue(out["ready"])
        self.assertEqual(out["detected"]["city"], "رشت")
        self.assertTrue(out["keywords"])

    def test_parse_never_crashes_on_odd_input(self):
        for text in ["!!", "۱۲۳", "در", "و", "همه", "تهران", "a" * 400, "در در در تهران تهران"]:
            out = smart.parse(text)
            self.assertIn(out["mode"], ("empty", "single", "sweep"))
            self.assertIsInstance(out["ready"], bool)
            self.assertIsInstance(out["keywords"], list)

    def test_request_is_directly_postable(self):
        """خروجی parse باید بدون دست‌کاری به POST /api/runs بخورد."""
        out = smart.parse("دندانپزشکی در تهران", size="quick")
        request = out["request"]
        for key in ("city_query", "profile", "depth", "limit", "engine_mode", "cols"):
            self.assertIn(key, request)
        self.assertTrue(request.get("niche_query") or request["all_niches"] or request["keywords"])


# ══════════════════════════════════════════════════════════════════════════
# ۲) سرویس HTTP: مسیرهای جدید و فایل‌های ایستا
# ══════════════════════════════════════════════════════════════════════════
class SmartApiTestCase(unittest.TestCase):
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
        for _ in range(60):
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

    @classmethod
    def request(cls, path, method="GET", body=None):
        data, hdrs = None, {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(cls.base + path, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.status, resp.read(), resp.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    @classmethod
    def get(cls, path):
        status, raw, headers = cls.request(path)
        if "json" in (headers.get("Content-Type") or ""):
            return status, json.loads(raw.decode("utf-8"))
        return status, raw

    @classmethod
    def post(cls, path, body=None):
        status, raw, headers = cls.request(path, "POST", body)
        if "json" in (headers.get("Content-Type") or ""):
            return status, json.loads(raw.decode("utf-8"))
        return status, raw

    # ── فایل‌های ایستا ────────────────────────────────────────────────────
    def test_01_root_serves_simple_ui(self):
        status, raw = self.get("/")
        self.assertEqual(status, 200)
        html = raw.decode("utf-8")
        self.assertIn('dir="rtl"', html)
        self.assertIn('id="q"', html)                        # همان یک جعبهٔ جست‌وجو
        self.assertIn('id="brain"', html)                    # «مغز» چه فهمید
        self.assertIn('id="goals"', html)
        self.assertIn('id="advanced"', html)                 # تنظیمات فنی پشت بخش بازشو
        self.assertIn("campaign.js", html)

    def test_02_smart_assets_served(self):
        for asset in ("/styles.css", "/app.js", "/campaign.js", "/favicon.svg"):
            status, raw = self.get(asset)
            self.assertEqual(status, 200, asset)
            self.assertGreater(len(raw), 200, asset)

    def test_03_demo_data_available(self):
        status, data = self.get("/demo-data.json")
        self.assertEqual(status, 200)
        self.assertTrue(data["leads"])
        self.assertIn("analytics", data)

    def test_04_studio_still_serves_pro_dashboard(self):
        status, raw = self.get("/studio/")
        self.assertEqual(status, 200)
        html = raw.decode("utf-8")
        self.assertIn("LeadGen Studio", html)
        self.assertNotIn('id="q"', html)                     # رابط حرفه‌ای، نه ساده
        for asset in ("/studio/styles.css", "/studio/app.js", "/studio/favicon.svg"):
            self.assertEqual(self.get(asset)[0], 200, asset)

    def test_05_path_traversal_blocked(self):
        for bad in ("/../leadgen/config.py", "/%2e%2e/leadgen/server.py",
                    "/studio/../../leadgen/config.py"):
            status, _ = self.get(bad)
            self.assertIn(status, (400, 404), bad)

    # ── API مغز ───────────────────────────────────────────────────────────
    def test_06_api_options(self):
        status, data = self.get("/api/smart/options")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["goals"]), 5)
        self.assertEqual(len(data["sizes"]), 3)
        self.assertTrue(data["examples"])

    def test_07_api_suggest(self):
        q = urllib.parse.quote("دندان")
        status, data = self.get(f"/api/smart/suggest?q={q}&limit=4")
        self.assertEqual(status, 200)
        self.assertLessEqual(len(data["niches"]), 4)
        self.assertIn("دندانپزشکی", [n["fa"] for n in data["niches"]])

    def test_08_api_parse(self):
        status, data = self.post("/api/smart/parse", {"text": "همه مشاغل در مشهد", "size": "quick"})
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], "sweep")
        self.assertTrue(data["ready"])
        self.assertTrue(data["request"]["all_niches"])
        self.assertTrue(data["keywords"])

    def test_09_api_parse_empty(self):
        status, data = self.post("/api/smart/parse", {"text": ""})
        self.assertEqual(status, 200)
        self.assertEqual(data["mode"], "empty")
        self.assertFalse(data["ready"])

    # ── اجرای واقعی از جملهٔ کاربر (موتور دمو) ────────────────────────────
    def test_10_end_to_end_from_sentence(self):
        status, parsed = self.post("/api/smart/parse", {
            "text": "دندانپزشکی در تهران", "goal": "site", "size": "quick",
            "cols": "contact", "export_fmt": "csv", "engine_mode": "demo"})
        self.assertEqual(status, 200)
        request = dict(parsed["request"])
        request["limit"] = 12                                # تست سریع بماند

        status, data = self.post("/api/runs", request)
        self.assertEqual(status, 202)
        run_id = data["run_id"]

        for _ in range(120):
            status, prog = self.get(f"/api/runs/{run_id}/progress")
            self.assertEqual(status, 200)
            if (prog.get("progress") or {}).get("status") in ("done", "failed"):
                break
            time.sleep(0.5)
        self.assertEqual(prog["progress"]["status"], "done", prog["progress"].get("message"))

        status, detail = self.get(f"/api/runs/{run_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["run"]["status"], "done")
        self.assertGreater(detail["lead_count"], 0)
        self.assertIn("coverage", detail["analytics"])

        status, leads = self.get(f"/api/runs/{run_id}/leads?limit=5")
        self.assertEqual(status, 200)
        self.assertTrue(leads["leads"])
        lead = leads["leads"][0]
        for field in ("title", "phone", "address", "lat", "lon", "score", "segment"):
            self.assertIn(field, lead)

        status, one = self.get(f"/api/leads/{lead['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(one["lead"]["score_factors"])
        self.assertIn("campaign", one["lead"])

    def test_11_sweep_run_accepted_without_niche_query(self):
        """جاروی تمامی مشاگل صنف مشخصی ندارد؛ API نباید آن را رد کند."""
        status, data = self.post("/api/runs", {
            "all_niches": True, "city_query": "تهران", "max_niches": 2,
            "keywords_per_niche": 1, "keywords_per_job": 4, "limit": 10,
            "depth": 1, "engine_mode": "demo", "profile": "generic"})
        self.assertEqual(status, 202, data)
        run_id = data["run_id"]
        for _ in range(120):
            _, prog = self.get(f"/api/runs/{run_id}/progress")
            if (prog.get("progress") or {}).get("status") in ("done", "failed"):
                break
            time.sleep(0.5)
        self.assertEqual(prog["progress"]["status"], "done", prog["progress"].get("message"))
        _, detail = self.get(f"/api/runs/{run_id}")
        self.assertGreater(detail["lead_count"], 0)
        niches_seen = {lead["niche_fa"] for lead in detail.get("leads_preview", []) if lead.get("niche_fa")}
        self.assertGreaterEqual(len(niches_seen), 1)

    def test_12_run_without_any_niche_is_rejected(self):
        status, data = self.post("/api/runs", {"city_query": "تهران"})
        self.assertEqual(status, 400)
        self.assertIn("niche_query", json.dumps(data, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
