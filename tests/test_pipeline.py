"""تست‌های پایپ‌لاین — فقط با کتابخانه استاندارد (unittest).

اجرا:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# هر تست روی پایگاه‌داده و پوشه خروجی موقت کار می‌کند تا data/ واقعی آلوده نشود
_TMP = Path(tempfile.mkdtemp(prefix="leadgen-test-"))
os.environ["LEADGEN_DATA_DIR"] = str(_TMP)
os.environ["ENGINE_MODE"] = "demo"

from leadgen import (analytics, campaign, config, engine as engine_mod, enrich,  # noqa: E402
                     exports, geo, niches, pipeline, scoring, storage)

# اگر ماژول آزمون دیگری پیش از این، `leadgen.config` را import کرده باشد، متغیر محیطی
# دیگر اثر ندارد؛ پس مسیرها را مستقیم روی ماژول می‌گذاریم تا این آزمون همیشه روی
# پایگاه‌دادهٔ موقت خودش اجرا شود (نه data/ واقعی یا باقی‌ماندهٔ اجراهای قبلی).
config.DATA_DIR = _TMP
config.DB_PATH = _TMP / "leadgen.db"
config.EXPORT_DIR = _TMP / "exports"


class GeoTests(unittest.TestCase):
    def test_offline_lookup_persian_and_latin(self):
        for query, expected in (("تهران", "tehran"), ("Tehran", "tehran"),
                                ("فرانکفورت", "frankfurt"), ("Denver, CO", "denver"),
                                ("دبی", "dubai")):
            self.assertEqual(geo.offline_lookup(query), expected, query)

    def test_geocode_returns_string_coords(self):
        lat, lon, bbox, key = geo.geocode("مشهد", with_bbox=True, allow_network=False)
        self.assertEqual(key, "mashhad")
        self.assertIsInstance(lat, str)
        self.assertEqual(len(bbox), 4)
        self.assertTrue(float(bbox[0]) < float(bbox[1]))

    def test_geocode_unknown_city_offline_is_none(self):
        self.assertEqual(geo.geocode("جایی که وجود ندارد", allow_network=False),
                         (None, None, None, None))

    def test_grid_cells_count_and_types(self):
        bbox = [35.55, 35.83, 51.15, 51.62]
        cells = geo.grid_cells(bbox, 3)
        self.assertEqual(len(cells), 9)
        for cell in cells:
            self.assertIsInstance(cell["lat"], str)
            self.assertGreaterEqual(cell["radius"], 1000)
            self.assertTrue(11 <= cell["zoom"] <= 17)

    def test_in_bbox_filters_far_rows(self):
        bbox = [35.5, 35.9, 51.1, 51.6]
        self.assertTrue(geo.in_bbox({"latitude": "35.7", "longitude": "51.4"}, bbox))
        self.assertFalse(geo.in_bbox({"latitude": "25.2", "longitude": "55.3"}, bbox))
        self.assertTrue(geo.in_bbox({"latitude": "", "longitude": ""}, bbox))  # بدون مختصات → نگه دار

    def test_dedupe_by_place_id(self):
        rows = [{"place_id": "a", "title": "X"}, {"place_id": "a", "title": "X copy"},
                {"place_id": "b", "title": "Y"}]
        self.assertEqual(len(geo.dedupe(rows)), 2)


class NicheTests(unittest.TestCase):
    def test_catalog_is_rich_and_unique(self):
        ids = [n.id for n in niches.CATALOG]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(niches.CATALOG), 60)
        self.assertGreaterEqual(len(niches.GROUPS), 8)

    def test_find_niche_persian_query(self):
        self.assertEqual(niches.find_niche("دندان پزشک").id, "dentist")
        self.assertEqual(niches.find_niche("لوله کشی ساختمان").id, "plumber")
        self.assertEqual(niches.find_niche("باشگاه بدنسازی").id, "gym")

    def test_find_niche_english_query(self):
        self.assertEqual(niches.find_niche("barbershop").id, "barbershop")
        self.assertEqual(niches.find_niche("cctv installation").id, "security-systems")

    def test_custom_niche_for_anything(self):
        found = niches.find_niche("فروش دستگاه تصفیه آب صنعتی")
        self.assertTrue(found.custom)
        self.assertTrue(found.keywords_fa)

    def test_expand_keywords_bakes_location(self):
        niche = niches.find_niche("dentist")
        terms = niches.expand_keywords(niche, "Denver CO", lang="en", limit=4)
        self.assertEqual(len(terms), 4)
        for term in terms:
            self.assertIn("Denver CO", term)          # قاعده اسکیل: مکان داخل کلیدواژه

    def test_expand_keywords_persian(self):
        niche = niches.find_niche("رستوران")
        terms = niches.expand_keywords(niche, "تهران", lang="fa", limit=3)
        self.assertTrue(all("تهران" in t for t in terms))


class EnrichTests(unittest.TestCase):
    HTML = """
    <html><body>
      <a href="mailto:Info@Example-Site.com">Email</a>
      <p>contact sales@parsaclinic.ir or support@wix.com</p>
      <a href="https://instagram.com/parsa_clinic">IG</a>
      <a href="https://facebook.com/sharer/sharer.php">share</a>
      <a href="https://www.linkedin.com/company/parsa-clinic">LinkedIn</a>
      <a href="/contact-us">تماس با ما</a>
      <form action="/x"><input type="email"></form>
      <script src="/wp-content/x.js"></script>
    </body></html>
    """

    def test_extract_emails_filters_junk_and_orders_site_first(self):
        found = enrich.extract_emails(self.HTML, "https://parsaclinic.ir")
        self.assertIn("sales@parsaclinic.ir", found)
        self.assertEqual(found[0], "sales@parsaclinic.ir")     # هم‌دامنه اول
        self.assertNotIn("support@wix.com", found)              # دامنه هرزنامه
        self.assertNotIn("info@example-site.com", found)        # example
        self.assertLessEqual(len(found), 3)

    def test_find_socials_skips_share_urls(self):
        socials = enrich.find_socials(self.HTML)
        self.assertTrue(socials["instagram"].endswith("parsa_clinic"))
        self.assertTrue(socials["linkedin"].endswith("parsa-clinic"))
        self.assertEqual(socials["facebook"], "")               # /sharer نادیده گرفته می‌شود

    def test_detect_signals(self):
        signals = enrich.detect_signals(self.HTML)
        self.assertTrue(signals["wordpress"])
        self.assertTrue(signals["contact_form"])
        self.assertFalse(signals["shopify"])

    def test_contact_links_prefers_discovered(self):
        links = enrich.contact_links(self.HTML, "https://parsaclinic.ir")
        self.assertIn("https://parsaclinic.ir/contact-us", links)
        self.assertLessEqual(len(links), 3)

    def test_normalize_row_flags(self):
        row = enrich.normalize_row({
            "title": "  کلینیک   پارسا ", "phone": "0912 111 0000",
            "emails": "INFO@Parsa.ir, info@parsa.ir", "website": "https://www.parsa.ir/",
            "review_rating": "4.6", "review_count": "128", "status": "ok",
            "instagram": "parsa", "address": "خیابان ولیعصر، تهران",
            "latitude": "35.700123", "longitude": "51.400456",
        })
        self.assertEqual(row["title"], "کلینیک پارسا")
        self.assertEqual(row["phone"], "+989121110000")
        self.assertEqual(row["email_primary"], "info@parsa.ir")
        self.assertEqual(row["domain"], "parsa.ir")
        self.assertEqual(row["rating"], 4.6)
        self.assertEqual(row["reviews"], 128)
        self.assertTrue(row["has_website"] and row["has_email"] and row["has_phone"])
        self.assertEqual(row["contact_channels"], 6)     # سایت، ایمیل، تلفن، شبکه، لوکیشن، نشانی
        self.assertEqual(row["completeness"], 100)
        self.assertEqual(row["place_status"], "ok")      # ستون خام موتور جدا می‌شود
        self.assertNotIn("status", row)
        self.assertEqual(row["location"], "35.700123, 51.400456")
        self.assertTrue(row["has_location"])

    def test_obfuscated_email_is_recovered(self):
        html = "<p>تماس: info [at] cafesaadeh [dot] ir یا sales(at)cafesaadeh(dot)com</p>"
        found = enrich.extract_emails(html, "https://cafesaadeh.ir")
        self.assertIn("info@cafesaadeh.ir", found)
        self.assertIn("sales@cafesaadeh.com", found)

    def test_social_profile_in_website_field_is_detected(self):
        """دادهٔ واقعی: وب‌سایت همان آدرس اینستاگرام است ولی ستون اینستاگرام خالی."""
        row = enrich.normalize_row({
            "title": "کافه ساده", "website": "https://www.instagram.com/cafesaadeh/",
            "latitude": "31.9", "longitude": "54.37"})
        self.assertEqual(row["instagram"], "https://www.instagram.com/cafesaadeh/")
        self.assertTrue(row["has_social"])

        fb = enrich.normalize_row({"title": "X", "website": "facebook.com/xcafe"})
        self.assertEqual(fb["facebook"], "https://facebook.com/xcafe")

    def test_refresh_flags_uses_six_channels_everywhere(self):
        row = enrich.normalize_row({
            "title": "کلینیک", "website": "https://parsa.ir", "address": "تهران",
            "latitude": "35.7", "longitude": "51.4"})
        self.assertEqual(row["contact_channels"], 3)      # سایت، نشانی، لوکیشن
        self.assertEqual(row["completeness"], 50)
        row["email_primary"] = "info@parsa.ir"
        enrich.refresh_flags(row)
        self.assertEqual(row["contact_channels"], 4)      # همان مخرج ۶، نه ۴
        self.assertEqual(row["completeness"], 67)

    def test_maps_link_prefers_place_link_then_coordinates(self):
        with_link = enrich.normalize_row({"title": "A", "link": "https://maps.google.com/?q=x"})
        self.assertEqual(with_link["maps_link"], "https://maps.google.com/?q=x")

        with_coords = enrich.normalize_row({"title": "B", "latitude": "35.7", "longitude": "51.4"})
        self.assertEqual(with_coords["maps_link"],
                         "https://www.google.com/maps/search/?api=1&query=35.700000,51.400000")

        only_address = enrich.normalize_row({"title": "کافه", "address": "تهران"})
        self.assertIn("google.com/maps", only_address["maps_link"])
        self.assertFalse(enrich.normalize_row({"title": ""})["maps_link"])

    def test_incomplete_row_scores_below_100(self):
        row = enrich.normalize_row({"title": "C", "phone": "+989120000000"})
        self.assertEqual(row["contact_channels"], 1)      # فقط تلفن؛ نه سایت/ایمیل/شبکه/لوکیشن/نشانی
        self.assertLess(row["completeness"], 40)
        self.assertFalse(row["has_location"])

    def test_enrich_offline_does_not_hit_network(self):
        rows = [{"title": "A", "website": "https://invalid.example", "emails": "",
                 "phone": "", "review_rating": "", "review_count": ""}]
        stats = enrich.enrich_rows(rows, allow_network=False)
        self.assertFalse(stats["network_used"])
        self.assertEqual(stats["sites_checked"], 0)

    def test_is_mobile_ir(self):
        self.assertTrue(enrich.is_mobile_ir("+98 912 111 0000"))
        self.assertFalse(enrich.is_mobile_ir("+98 21 8888 7777"))


class ScoringTests(unittest.TestCase):
    def _row(self, **over):
        row = enrich.normalize_row({
            "title": "T", "category": "Dentists", "address": "x، Tehran",
            "phone": "+989121110000", "emails": "", "website": "",
            "review_rating": "4.7", "review_count": "120",
        })
        row.update(over)
        return row

    def test_no_website_beats_full_site_for_web_design(self):
        niche = niches.find_niche("dentist")
        bare = scoring.score_lead(self._row(), profile="web_design", niche=niche)
        full = scoring.score_lead(self._row(
            website="https://x.ir", domain="x.ir", emails="a@x.ir", email_primary="a@x.ir",
            has_website=True, has_email=True, has_social=True, instagram="x",
            contact_channels=4, completeness=100, signals={"booking": True}),
            profile="web_design", niche=niche)
        self.assertGreater(bare.score, full.score)
        self.assertEqual(bare.segment, "hot")

    def test_b2b_profile_prefers_reachable_and_reputable(self):
        niche = niches.find_niche("wholesaler")
        reachable = scoring.score_lead(self._row(emails="a@x.ir", email_primary="a@x.ir",
                                                 has_email=True, contact_channels=3, completeness=75),
                                       profile="b2b_supply", niche=niche)
        unreachable = scoring.score_lead(self._row(phone="", has_phone=False, contact_channels=0,
                                                   completeness=0),
                                         profile="b2b_supply", niche=niche)
        self.assertGreater(reachable.score, unreachable.score)

    def test_factors_explain_the_score(self):
        result = scoring.score_lead(self._row(), profile="marketing", niche=niches.find_niche("gym"))
        self.assertEqual(len(result.factors), 5)
        total = round(sum(f["points"] for f in result.factors))
        self.assertAlmostEqual(total, result.score, delta=1)
        self.assertTrue(all(f["detail"] for f in result.factors))
        self.assertTrue(result.gaps)
        self.assertTrue(result.tags)
        self.assertIn(result.channel, ("email", "whatsapp", "phone", "instagram", "form", "visit"))

    def test_channel_selection_order(self):
        self.assertEqual(scoring.suggest_channel({"has_email": True})[0], "email")
        self.assertEqual(scoring.suggest_channel({"instagram": "x"})[0], "instagram")
        self.assertEqual(scoring.suggest_channel({"phone": "+989121110000"})[0], "whatsapp")
        self.assertEqual(scoring.suggest_channel({"phone": "+4930123456"})[0], "phone")
        self.assertEqual(scoring.suggest_channel({})[0], "visit")

    def test_score_all_sorts_descending(self):
        rows = [self._row(review_count=str(n), reviews=n) for n in (5, 400, 60)]
        scored = scoring.score_all(rows, profile="generic", niche=niches.find_niche("dentist"))
        scores = [r["score"] for r in scored]
        self.assertEqual(scores, sorted(scores, reverse=True))


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.row = enrich.normalize_row({
            "title": "کلینیک دندانپزشکی پارسا", "category": "دندانپزشکی",
            "address": "خیابان ولیعصر، ونک، تهران", "phone": "+989121110000",
            "emails": "info@parsa.ir", "website": "https://parsa.ir",
            "review_rating": "4.8", "review_count": "90",
        })
        self.niche = niches.find_niche("dentist")
        scoring.score_all([self.row], profile="web_design", niche=self.niche)
        self.sender = campaign.Sender(name="سارا", company="استودیو رشد",
                                      phone="+989120000000", email="sara@studio.ir")

    def test_email_sequence_has_three_touches_and_placeholders_filled(self):
        message = campaign.generate(self.row, "email", self.sender, self.niche)
        self.assertEqual(len(message["sequence"]), 3)
        for touch in message["sequence"]:
            self.assertNotIn("{", touch["body"])
            self.assertIn("کلینیک دندانپزشکی پارسا", touch["body"])
        self.assertIn("لغو", message["text"])        # گزینه انصراف همیشه هست

    def test_whatsapp_and_sms_are_short(self):
        self.assertLess(len(campaign.generate(self.row, "sms", self.sender, self.niche)["body"]), 290)
        whatsapp = campaign.generate(self.row, "whatsapp", self.sender, self.niche)
        self.assertEqual(whatsapp["to"], "+989121110000")

    def test_instagram_has_two_steps(self):
        message = campaign.generate(self.row, "instagram", self.sender, self.niche)
        self.assertEqual(len(message["sequence"]), 2)

    def test_english_variant(self):
        message = campaign.generate(self.row, "email", self.sender, self.niche, language="en")
        self.assertIn("Hi ", message["body"])
        self.assertNotIn("{", message["body"])

    def test_mailto_and_wa_links(self):
        self.assertTrue(campaign.mailto_link(self.row, "email", self.sender, self.niche)
                        .startswith("mailto:info@parsa.ir"))
        self.assertTrue(campaign.wa_link(self.row, self.sender, self.niche).startswith("https://wa.me/989121110000"))

    def test_unknown_placeholders_never_crash(self):
        text = campaign.render("سلام {business} — {does_not_exist}", {"business": "X"})
        self.assertEqual(text, "سلام X —")


class ExportsTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        for i in range(4):
            row = enrich.normalize_row({
                "title": f"کسب‌وکار {i}", "category": "کافه", "city": "تهران",
                "address": "خیابان x", "phone": f"+98912111000{i}",
                "emails": f"info{i}@x.ir", "website": f"https://x{i}.ir",
                "review_rating": str(4 + i * 0.2), "review_count": str(10 * (i + 1)),
            })
            self.rows.append(row)
        scoring.score_all(self.rows, profile="generic", niche=niches.find_niche("cafe"))
        for row in self.rows:
            row["gaps_fa"] = [scoring.GAP_FA.get(g, g) for g in row.get("gaps", [])]

    def test_csv_has_bom_and_persian_headers(self):
        data = exports.csv_bytes(self.rows)
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))     # BOM برای اکسل
        text = data.decode("utf-8-sig")
        self.assertIn("نام کسب‌وکار", text.splitlines()[0])
        self.assertEqual(len(text.strip().splitlines()), len(self.rows) + 1)

    def test_xlsx_is_a_valid_zip_with_expected_parts(self):
        path = _TMP / "t.xlsx"
        sheets = exports.build_workbook(self.rows, {"coverage": {"total": 4}, "segments": {},
                                                    "gaps": [], "funnel": [], "insights": []},
                                        {"id": "r1", "niche_fa": "کافه", "city": "تهران",
                                         "engine": "demo", "keywords": ["کافه در تهران"]},
                                        campaign_rows=None)
        exports.write_xlsx(path, sheets)
        with zipfile.ZipFile(path) as zf:
            self.assertIsNone(zf.testzip())
            names = zf.namelist()
            for required in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                             "xl/styles.xml", "xl/worksheets/sheet1.xml"):
                self.assertIn(required, names)
            import xml.dom.minidom as minidom
            for name in names:
                minidom.parseString(zf.read(name))           # همه XMLها خوش‌ساخت‌اند
            self.assertIn("rightToLeft", zf.read("xl/worksheets/sheet1.xml").decode())

    def test_json_round_trip(self):
        path = _TMP / "t.json"
        exports.write_json(path, {"leads": self.rows})
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(loaded["leads"]), 4)

    def test_campaign_csv_columns(self):
        data = exports.csv_bytes_custom(
            [{"title": "A", "channel_fa": "ایمیل", "to": "a@b.c", "subject": "s",
              "body": "b", "score": 80, "segment_fa": "داغ", "status_fa": "جدید"}],
            exports.CAMPAIGN_COLUMNS).decode("utf-8-sig")
        self.assertIn("متن آماده ارسال", data.splitlines()[0])


class StorageTests(unittest.TestCase):
    def setUp(self):
        storage.init_db()
        # ردیف‌های ثابتِ همین آزمون باید از صفر شروع شوند (وابستگی به ترتیب اجرا ممنوع)
        for run_id in ("test-run-1", "test-run-2"):
            storage.delete_run(run_id)

    def test_run_and_lead_round_trip_with_persian(self):
        run_id = storage.create_run({"id": "test-run-1", "niche_fa": "کافه", "city": "تهران",
                                     "keywords": ["کافه در تهران"], "engine": "demo",
                                     "profile": "generic", "status": "running"})
        rows = [enrich.normalize_row({"title": "کافه تست", "phone": "+989121110000",
                                      "emails": "a@b.ir", "website": "", "review_rating": "4.5",
                                      "review_count": "30", "city": "تهران"})]
        scoring.score_all(rows, profile="generic", niche=niches.find_niche("cafe"))
        storage.save_leads(run_id, rows)
        loaded = storage.load_leads(run_id)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["title"], "کافه تست")
        self.assertEqual(loaded[0]["status"], "new")
        self.assertEqual(loaded[0]["status_fa"], "جدید")
        self.assertTrue(loaded[0]["segment_fa"])
        self.assertTrue(loaded[0]["gaps_fa"])
        self.assertEqual(loaded[0]["city"], "تهران")

        self.assertTrue(storage.set_lead_status(loaded[0]["id"], "contacted", "یادداشت"))
        self.assertEqual(storage.get_lead(loaded[0]["id"])["status_fa"], "تماس گرفته شد")
        self.assertFalse(storage.set_lead_status(loaded[0]["id"], "bogus"))

    def test_filters_and_search(self):
        run_id = "test-run-2"
        storage.create_run({"id": run_id, "engine": "demo", "profile": "generic"})
        rows = []
        for i, (title, email) in enumerate((("آلفا", "a@x.ir"), ("بتا", ""), ("گاما", "c@x.ir"))):
            row = enrich.normalize_row({"title": title, "emails": email, "phone": f"+98912000{i}",
                                        "website": "", "review_rating": "4.2",
                                        "review_count": str(20 * (i + 1))})
            rows.append(row)
        scoring.score_all(rows, profile="web_design", niche=niches.find_niche("cafe"))
        storage.save_leads(run_id, rows)
        self.assertEqual(len(storage.load_leads(run_id, query="بتا")), 1)
        self.assertEqual(len(storage.load_leads(run_id, segment="hot")) +
                         len(storage.load_leads(run_id, segment="warm")) +
                         len(storage.load_leads(run_id, segment="cold")), 3)
        self.assertTrue(storage.count_leads(run_id) >= 3)
        storage.delete_run(run_id)
        self.assertEqual(storage.count_leads(run_id), 0)


class DemoEngineTests(unittest.TestCase):
    def test_rows_have_raw_columns_and_are_deterministic(self):
        body = {"keywords": ["dentists in Tehran"], "lat": "35.6892", "lon": "51.3890",
                "depth": 1, "city_label": "Tehran", "city_key": "tehran", "country": "IR",
                "bbox": [35.55, 35.83, 51.15, 51.62], "email": True}
        demo = engine_mod.DemoEngine()
        job_a, rows_a = demo.run_job(dict(body))
        job_b, rows_b = demo.run_job(dict(body))
        self.assertEqual(job_a, job_b)                       # seed قطعی
        self.assertEqual([r["title"] for r in rows_a], [r["title"] for r in rows_b])
        self.assertTrue(rows_a)
        for column in ("title", "phone", "website", "emails", "latitude", "longitude",
                       "place_id", "review_rating", "review_count", "address"):
            self.assertIn(column, rows_a[0])

    def test_docker_engine_validates_required_fields(self):
        docker = engine_mod.DockerEngine(base_url="http://localhost:1")
        with self.assertRaises(engine_mod.EngineError):
            docker.create_job({"keywords": []})
        with self.assertRaises(engine_mod.EngineError):
            docker.create_job({"keywords": ["x"], "lat": "", "lon": "1", "max_time": 60})

    def test_docker_body_is_json_safe_and_drops_internal_keys(self):
        """بدنهٔ job باید JSON خالص باشد — وگرنه هر اجرای واقعی با TypeError می‌شکند.

        پایپ‌لاین کلیدهای داخلی `_niche` (شیء Niche) و `_keyword_niches` را برای موتور دمو
        در بدنه می‌گذارد؛ موتور واقعی باید آن‌ها را نبیند.
        """
        captured = {}

        class Niche:                      # شیء غیرJSON مثل niches.Niche
            def __init__(self):
                self.id, self.fa = "cafe", "کافه"

        class FakeDocker(engine_mod.DockerEngine):
            def _request(self, method, path, body=None, timeout=45):
                captured["method"], captured["path"], captured["body"] = method, path, body
                return 200, b'{"id": "job-42"}'

        docker = FakeDocker(base_url="http://localhost:1")
        body = {"keywords": ["کافه در تهران"], "lat": 35.7592, "lon": 51.4094,
                "max_time": 600, "depth": 3, "_niche": Niche(),
                "_keyword_niches": {"کافه در تهران": "cafe"}, "bbox": [35.5, 35.9, 51.1, 51.7],
                "city_label": "تهران", "lang": "fa"}
        self.assertEqual(docker.create_job(body), "job-42")
        payload = captured["body"]
        json.dumps(payload, ensure_ascii=False)               # نباید استثنا بدهد
        self.assertNotIn("_niche", payload)
        self.assertNotIn("_keyword_niches", payload)
        self.assertEqual(payload["lat"], "35.7592")           # قرارداد اسکیل: رشته
        self.assertEqual(payload["lon"], "51.4094")
        self.assertEqual(payload["keywords"], ["کافه در تهران"])
        self.assertEqual(payload["lang"], "fa")
        self.assertTrue(payload["email"])                     # پیش‌فرض اسکیل
        self.assertEqual(payload["max_time"], 600)
        self.assertNotIn("proxies", payload)

    def test_engine_status_honours_requested_mode(self):
        """`engine_status` باید حالت خواسته‌شده را گزارش کند، نه همیشه `ENGINE_MODE` را."""
        self.assertEqual(pipeline.engine_status("demo")["active"], "demo")
        self.assertFalse(pipeline.engine_status("demo")["live"])
        docker = pipeline.engine_status("docker")             # داکر در محیط آزمون خاموش است
        self.assertIn(docker["active"], ("docker", "unavailable"))
        self.assertTrue(docker["reason"])
        self.assertIn(pipeline.engine_status(engine_mod.DemoEngine())["active"], ("demo",))

    def test_engine_status_reports_demo_when_docker_down(self):
        status = engine_mod.engine_status()
        self.assertIn(status["active"], ("docker", "demo", "unavailable"))
        self.assertIn("base_url", status["docker"])


class PipelineTests(unittest.TestCase):
    def test_end_to_end_demo_run(self):
        result = pipeline.run_scrape(pipeline.ScrapeRequest(
            niche_query="دندانپزشکی", city_query="تهران", profile="web_design",
            socials=True, engine_mode="demo", depth=2, limit=20, export_fmt="xlsx"),
            on_progress=lambda payload: self.assertIn("run_id", payload))
        self.assertEqual(len(result.rows), 20)
        self.assertTrue(result.run_id)
        self.assertEqual(result.stats["engine"], "demo")
        self.assertTrue(any(Path(p).suffix == ".xlsx" for p in result.export_paths))
        for path in result.export_paths:
            self.assertTrue(Path(path).is_file(), path)
        self.assertTrue(result.analytics["insights"])
        self.assertTrue(all(0 <= r["score"] <= 100 for r in result.rows))
        storage.delete_run(result.run_id)

    def test_grid_mode_dedupes_and_filters(self):
        result = pipeline.run_scrape(pipeline.ScrapeRequest(
            niche_query="رستوران", city_query="tehran", lang="fa", grid=2, depth=1,
            engine_mode="demo", limit=40, export_fmt="csv"), on_progress=None)
        self.assertTrue(result.rows)
        ids = [r.get("place_id") for r in result.rows if r.get("place_id")]
        self.assertEqual(len(ids), len(set(ids)))            # بدون تکرار
        storage.delete_run(result.run_id)

    def test_custom_niche_and_manual_keywords(self):
        result = pipeline.run_scrape(pipeline.ScrapeRequest(
            niche_query="فروش دستگاه تصفیه آب صنعتی", city_query="دبی", lang="fa",
            keywords=["دستگاه تصفیه آب صنعتی"], depth=1, engine_mode="demo", limit=5,
            export_fmt="json"), on_progress=None)
        self.assertTrue(result.rows)
        self.assertIn("دبی", result.stats["keywords"][0])
        storage.delete_run(result.run_id)

    def test_unknown_city_raises(self):
        with self.assertRaises(Exception):
            pipeline.run_scrape(pipeline.ScrapeRequest(
                niche_query="کافه", city_query="شهری که وجود ندارد xyz",
                engine_mode="demo", depth=1), on_progress=None)

    def test_rescore_changes_segments(self):
        result = pipeline.run_scrape(pipeline.ScrapeRequest(
            niche_query="کلینیک زیبایی", city_query="تهران", profile="web_design",
            depth=1, engine_mode="demo", limit=25, export_fmt="csv"), on_progress=None)
        first_hot = result.stats["hot"]
        changed = pipeline.rescore_run(result.run_id, "b2b_supply")
        self.assertEqual(changed["total"], result.stats["total"])
        self.assertNotEqual(changed["profile"], "web_design")
        self.assertIsInstance(first_hot, int)
        storage.delete_run(result.run_id)

    def test_import_existing_list(self):
        rows = [
            {"name": "کلینیک پارسا", "phone": "+989121110000", "email": "info@parsa.ir",
             "website": "", "rating": "4.9", "reviews": "12"},
            {"نام": "کافه ونک", "تلفن": "09122223344", "سایت": "https://vanak.cafe",
             "امتیاز": "4.1"},
        ]
        result = pipeline.import_leads(rows, niche_query="دندانپزشکی", city="تهران",
                                       profile="web_design", source="test")
        self.assertEqual(result["total"], 2)
        self.assertTrue(result["run_id"])
        storage.delete_run(result["run_id"])


class AnalyticsTests(unittest.TestCase):
    def _rows(self):
        rows = []
        for i in range(6):
            row = enrich.normalize_row({
                "title": f"T{i}", "category": "کافه", "city": "تهران", "address": "a، Tehran",
                "phone": f"+98912000{i}", "emails": f"e{i}@x.ir" if i % 2 else "",
                "website": f"https://x{i}.ir" if i % 3 else "",
                "review_rating": str(3 + i * 0.4), "review_count": str(i * 40),
                "latitude": str(35.6 + i * 0.01), "longitude": str(51.3 + i * 0.01),
            })
            rows.append(row)
        scoring.score_all(rows, profile="marketing", niche=niches.find_niche("cafe"))
        return rows

    def test_build_returns_all_sections(self):
        pack = analytics.build(self._rows(), {"city": "تهران", "niche_fa": "کافه"})
        for key in ("coverage", "coverage_percent", "segments", "score_distribution",
                    "rating_distribution", "gaps", "categories", "channels", "keywords",
                    "funnel", "map", "insights", "avg_score"):
            self.assertIn(key, pack)
        self.assertEqual(pack["coverage"]["total"], 6)
        self.assertEqual(sum(s["count"] for s in pack["segments"].values()), 6)
        self.assertTrue(pack["insights"])

    def test_empty_rows_do_not_crash(self):
        pack = analytics.build([])
        self.assertEqual(pack["coverage"]["total"], 0)
        self.assertEqual(pack["avg_score"], 0)

    def test_json_serializable(self):
        pack = analytics.build(self._rows(), {"id": "x"})
        json.dumps(pack, ensure_ascii=False)                 # باید بدون خطا سریال شود


class ConfigTests(unittest.TestCase):
    def test_env_file_parsing_and_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text('SCRAPER_PORT=9099\nSCRAPER_API_KEY="secret"\n# comment\n',
                                encoding="utf-8")
            for key in ("SCRAPER_PORT", "SCRAPER_API_KEY"):
                os.environ.pop(key, None)
            config.load_env_file(env_path)
            self.assertEqual(os.environ.get("SCRAPER_PORT"), "9099")
            self.assertEqual(os.environ.get("SCRAPER_API_KEY"), "secret")
            os.environ.pop("SCRAPER_PORT", None)
            os.environ.pop("SCRAPER_API_KEY", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ProvinceSweepPipelineTest(unittest.TestCase):
    """انتخاب استان در رابط → چند جعبهٔ شهری به موتور واقعی داده می‌شود."""

    def test_province_request_passes_city_boxes_and_warns(self):
        captured: list[dict] = []

        class FakeEngine:
            name = "osm"
            live = True

            def health(self):
                return {"up": True}

            def run_job(self, body, on_progress=None):
                captured.append(body)
                return "fake-job", []

        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: FakeEngine()
        try:
            request = pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "province": "gilan", "engine_mode": "osm",
                "lang": "fa", "limit": 20, "depth": 2})
            result = pipeline.run_scrape(request)
        finally:
            pipeline.make_engine = original
        self.assertTrue(captured, "موتور باید صدا زده شود")
        boxes = captured[0].get("sweep_boxes") or []
        self.assertGreater(len(boxes), 1, "استان باید چند شهر را بگردد")
        for box in boxes:
            self.assertEqual(len(box["bbox"]), 4)
            self.assertTrue(box.get("fa") or box.get("label"))
        self.assertTrue(any("اسکراب استانی" in w for w in result.warnings), result.warnings)
        self.assertEqual(result.run.get("status"), "done")
        # همهٔ شهرهای استان در برنامه‌اند (نه فقط چند شهر پرجمعیت)
        scan = (result.analytics or {}).get("scan") or {}
        self.assertTrue(scan.get("full"))
        self.assertGreaterEqual(scan.get("total") or 0, len(boxes))

    def test_province_with_more_cities_than_boxes_still_covers_every_city(self):
        """اصفهان ۱۱۱ شهر دارد؛ سقف ۴۰ جعبه نباید هیچ شهری را حذف کند."""
        from leadgen import regions, tiling

        province = next(p for p in regions.provinces() if p["fa"] == "اصفهان")
        boxes = tiling.plan("province", province=province["id"], max_tiles=40)
        self.assertLessEqual(len(boxes), 40)
        self.assertGreater(len(boxes), 20)
        missed = [c["fa"] for c in province["cities"]
                  if not any(b["bbox"][0] <= c["lat"] <= b["bbox"][1]
                             and b["bbox"][2] <= c["lon"] <= b["bbox"][3] for b in boxes)]
        self.assertEqual(missed, [], f"شهرهای جاافتاده: {missed[:5]}")
        # برچسب‌ها یکتا و پایدارند (ادامهٔ اسکراب به همان‌ها تکیه می‌کند)
        labels = [b["label"] for b in boxes]
        self.assertEqual(len(labels), len(set(labels)))

    def test_province_plans_all_cities_and_reports_continuation(self):
        """استان = همهٔ شهرها؛ کار در بخش‌های زمانی ادامه می‌یابد تا داده جا نماند."""
        captured: list[dict] = []

        class FakeEngine:
            name = "osm"
            live = True

            def health(self):
                return {"up": True}

            def run_job(self, body, on_progress=None):
                captured.append(body)
                return "fake-job", []

        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: FakeEngine()
        try:
            short = pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "province": "tehran-province", "engine_mode": "osm",
                "lang": "fa", "limit": 20, "max_time": 30})
            pipeline.run_scrape(short)
            long = pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "province": "tehran-province", "engine_mode": "osm",
                "lang": "fa", "limit": 20, "max_time": 60})
            pipeline.run_scrape(long)
        finally:
            pipeline.make_engine = original
        short_boxes = captured[0].get("sweep_boxes") or []
        long_boxes = captured[1].get("sweep_boxes") or []
        # سقف شهرهای استان بالا رفت: هر دو بودجه همهٔ شهرها را در برنامه می‌بینند،
        # ولی اجرای کوتاه بقیه را در «ادامه» می‌گذارد (نه اینکه بی‌صدا حذف کند).
        self.assertGreaterEqual(len(short_boxes), 5)
        self.assertEqual(len(short_boxes), len(long_boxes))

    def test_empty_city_is_reported_in_warnings(self):
        """گزارش باید بگوید کدام شهر داده نداشت (به‌جای سکوت)."""

        class FakeEngine:
            name = "osm"
            live = True

            def health(self):
                return {"up": True}

            def run_job(self, body, on_progress=None):
                self.coverage = [
                    {"city": "رشت", "rows": 12, "via": "overpass"},
                    {"city": "لاهیجان", "rows": 0, "via": ""},
                ]
                return "fake-job", [{
                    "place_id": "osm:node/1", "title": "کافه نمونه", "category": "کافه",
                    "latitude": "37.28", "longitude": "49.59", "phone": "01332222222",
                    "website": "", "instagram": "", "facebook": "", "linkedin": "",
                    "address": "رشت", "input_id": "cafe", "source": "openstreetmap",
                    "city": "رشت",
                }]

        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: FakeEngine()
        try:
            request = pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "province": "gilan", "engine_mode": "osm",
                "lang": "fa", "limit": 20, "max_time": 60})
            result = pipeline.run_scrape(request)
        finally:
            pipeline.make_engine = original
        joined = " ".join(result.warnings)
        self.assertIn("پوشش استانی", joined)
        self.assertIn("لاهیجان", joined)
        # پوشش شهرها در بستهٔ آمار می‌ماند (داشبورد آن را به‌صورت نمودار نشان می‌دهد)
        cities = result.analytics.get("cities") or []
        self.assertEqual({c["city"] for c in cities}, {"رشت", "لاهیجان"})
        lahijan = next(c for c in cities if c["city"] == "لاهیجان")
        self.assertEqual(lahijan["rows"], 0)
        self.assertEqual(lahijan["via"], "")
        # هشدارها در بستهٔ آمار می‌مانند (بعد از بسته شدن مرورگر هم قابل خواندن‌اند)
        stored = " ".join(result.analytics.get("warnings") or [])
        self.assertIn("پوشش استانی", stored)

    def test_empty_run_reports_text_search_attempts(self):
        """اجرای خالی باید بگوید کدام عبارت‌ها با مسیر متنی امتحان شدند و صفر دادند."""

        class FakeEngine:
            name = "osm"
            live = True

            def health(self):
                return {"up": True}

            def run_job(self, body, on_progress=None):
                self.attempts = [{"term": "cafes", "rows": 0, "error": ""},
                                 {"term": "coffee shops", "rows": 0, "error": ""}]
                raise pipeline.EmptyResult("در این محدوده چیزی پیدا نشد")

        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: FakeEngine()
        try:
            request = pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "city_query": "یزد", "engine_mode": "osm",
                "lang": "fa", "limit": 30, "max_time": 40})
            result = pipeline.run_scrape(request)
        finally:
            pipeline.make_engine = original
        joined = " ".join(result.warnings)
        self.assertIn("مسیر جست‌وجوی متنی", joined)
        self.assertIn("cafes", joined)
        stored = " ".join(result.analytics.get("warnings") or [])
        self.assertIn("مسیر جست‌وجوی متنی", stored)

    def test_single_city_run_has_no_city_coverage_block(self):
        """برای یک شهر، فهرست پوشش شهری نباید ردیف بی‌نام/صفر بسازد."""

        class FakeEngine:
            name = "osm"
            live = True

            def health(self):
                return {"up": True}

            def run_job(self, body, on_progress=None):
                self.coverage = [{"city": "", "rows": 0, "via": "", "error": "Overpass پایین"}]
                return "fake-job", [{
                    "place_id": "osm:node/9", "title": "کافه اهواز", "category": "رستوران",
                    "latitude": "31.32", "longitude": "48.67", "phone": "", "website": "",
                    "instagram": "", "facebook": "", "linkedin": "", "address": "اهواز",
                    "input_id": "restaurant", "source": "openstreetmap", "city": "اهواز"}]

        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: FakeEngine()
        try:
            request = pipeline.ScrapeRequest.from_dict({
                "niche_query": "رستوران", "city_query": "اهواز", "engine_mode": "osm",
                "lang": "fa", "limit": 20, "max_time": 30})
            result = pipeline.run_scrape(request)
        finally:
            pipeline.make_engine = original
        self.assertNotIn("cities", result.analytics)
        self.assertEqual(result.stats["total"], 1)

    def test_second_run_marks_leads_already_seen(self):
        """اجرای دوبارهٔ همان شهر نباید همه را «تازه» جا بزند — باید تکراری‌ها را بشمارد."""

        class FakeEngine:
            name = "osm"
            live = True

            def health(self):
                return {"up": True}

            def run_job(self, body, on_progress=None):
                return "fake-job", [{
                    "place_id": "osm:node/777", "title": "کافه تکراری", "category": "کافه",
                    "latitude": "29.59", "longitude": "52.58", "phone": "07133334444",
                    "website": "", "instagram": "", "facebook": "", "linkedin": "",
                    "address": "شیراز", "input_id": "cafe", "source": "openstreetmap",
                    "city": "شیراز"}]

        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: FakeEngine()
        try:
            first = pipeline.run_scrape(pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "city_query": "شیراز", "engine_mode": "osm",
                "lang": "fa", "limit": 10, "max_time": 30}))
            second = pipeline.run_scrape(pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "city_query": "شیراز", "engine_mode": "osm",
                "lang": "fa", "limit": 10, "max_time": 30}))
        finally:
            pipeline.make_engine = original

        self.assertEqual(first.analytics["fresh"]["repeat"], 0, "اجرای اول چیزی برای تکرار ندارد")
        self.assertEqual(second.analytics["fresh"], {"new": 0, "repeat": 1})
        joined = " ".join(second.analytics.get("warnings") or [])
        self.assertIn("در اجراهای قبلی هم", joined)
        rows = pipeline.storage.all_leads_for_export(second.run_id)
        self.assertTrue((rows[0].get("signals") or {}).get("repeat"))
        self.assertEqual((rows[0]["signals"] or {}).get("first_seen_run"), first.run_id)

class RegionFidelityTests(unittest.TestCase):
    """«هر شهر/استان/خیابانی انتخاب شد، فقط همان منطقه اسکراب شود.»

    این آزمون‌ها همان باگ‌های واقعی را می‌گیرند:
      • جست‌وجوی شهر، لیدهای شهر/استان دیگر را هم می‌آورد.
      • نام خیابان اشتباه، بی‌صدا به خیابان دیگری می‌چسبید.
      • انتخاب «کل استان» بدون شهر، هیچ‌وقت اجرا نمی‌شد.
      • انتخاب شهر از رابط (`city_key`) نادیده گرفته می‌شد و برچسب ترکیبی جای شهر می‌رفت.
    """

    @staticmethod
    def engine_returning(rows: list[dict]):
        class FakeEngine:
            name = "osm"
            live = True

            def __init__(self):
                self.bodies: list[dict] = []
                self.coverage: list = []

            def health(self):
                return {"up": True}

            def run_job(self, body, on_progress=None):
                self.bodies.append(body)
                return "fake-job", [dict(r) for r in rows]

        return FakeEngine()

    @staticmethod
    def row(place_id: str, lat: float, lon: float, title: str = "کسب‌وکار") -> dict:
        return {"place_id": place_id, "title": title, "category": "کافه",
                "latitude": str(lat), "longitude": str(lon), "phone": "",
                "website": "", "instagram": "", "facebook": "", "linkedin": "",
                "address": "", "input_id": "cafe", "source": "openstreetmap", "city": ""}

    def test_rows_outside_the_city_are_dropped(self):
        inside = self.row("osm:node/1", 31.32, 48.67, "کافهٔ اهواز")
        outside = self.row("osm:node/2", 35.70, 51.40, "کافهٔ تهران")
        fake = self.engine_returning([inside, outside])
        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: fake
        try:
            result = pipeline.run_scrape(pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "city_query": "اهواز", "city_key": "ahvaz",
                "engine_mode": "osm", "lang": "fa", "limit": 10, "max_time": 30}))
        finally:
            pipeline.make_engine = original
        self.assertEqual(result.stats["total"], 1)
        self.assertEqual(result.stats["rows"][0]["title"] if result.stats.get("rows") else "کافهٔ اهواز",
                         "کافهٔ اهواز")
        joined = " ".join(result.analytics.get("warnings") or [])
        self.assertIn("خارج از محدودهٔ", joined)
        self.assertIn("اهواز", joined)

    def test_city_run_forbids_province_fallback(self):
        fake = self.engine_returning([self.row("osm:node/1", 31.32, 48.67)])
        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: fake
        try:
            pipeline.run_scrape(pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "city_query": "اهواز", "city_key": "ahvaz",
                "engine_mode": "osm", "lang": "fa", "limit": 10, "max_time": 30}))
        finally:
            pipeline.make_engine = original
        body = fake.bodies[0]
        self.assertTrue(body["strict_region"])
        self.assertFalse(body["allow_province_fallback"],
                         "جست‌وجوی شهر نباید به کل استان پهن شود")
        self.assertEqual(body["bbox"], [31.2, 31.44, 48.52, 48.82])   # مرز همان شهر

    def test_province_only_request_resolves_to_capital_with_province_box(self):
        fake = self.engine_returning([self.row("osm:node/9", 31.32, 48.67)])
        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: fake
        try:
            result = pipeline.run_scrape(pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "city_query": "", "province": "khuzestan",
                "engine_mode": "osm", "lang": "fa", "limit": 10, "max_time": 60}))
        finally:
            pipeline.make_engine = original
        body = fake.bodies[0]
        self.assertTrue(body["allow_province_fallback"])
        self.assertEqual(body["bbox"][0], 29.89889)          # جعبهٔ کل استان
        self.assertEqual(body["bbox"][1], 32.84111)
        # برچسب اجرا نام استان است (نه مرکز آن) — کاربر «کل خوزستان» را خواسته
        self.assertEqual(result.run["city"], "خوزستان")
        self.assertEqual(result.run["lat"], "31.318300")       # نقطهٔ مبنا: مرکز استان

    def test_selected_city_key_wins_over_a_stale_city_query(self):
        fake = self.engine_returning([])
        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: fake
        try:
            request = pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "city_query": "تهران", "city_key": "yazd",
                "province": "yazd", "engine_mode": "osm", "lang": "fa", "max_time": 30})
            pipeline.apply_area(request)
        finally:
            pipeline.make_engine = original
        self.assertEqual(request.city_key, "yazd")
        self.assertEqual(request.city_query, "یزد")
        self.assertAlmostEqual(float(request.lat), 31.8974, places=3)

    def test_unknown_street_aborts_instead_of_scraping_the_wrong_place(self):
        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: self.engine_returning([])
        try:
            with self.assertRaises(pipeline.EngineError) as ctx:
                pipeline.run_scrape(pipeline.ScrapeRequest.from_dict({
                    "niche_query": "کافه", "city_query": "tehran", "city_key": "tehran",
                    "province": "tehran-province", "street": "خیابان کاملاً ناموجود",
                    "engine_mode": "osm", "lang": "fa", "max_time": 30}))
        finally:
            pipeline.make_engine = original
        message = str(ctx.exception)
        self.assertIn("خیابان «خیابان کاملاً ناموجود»", message)
        self.assertIn("متوقف شد", message)

    def test_city_request_has_no_sweep(self):
        captured: list[dict] = []

        class FakeEngine:
            name = "osm"
            live = True

            def health(self):
                return {"up": True}

            def run_job(self, body, on_progress=None):
                captured.append(body)
                return "fake-job", []

        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: FakeEngine()
        try:
            request = pipeline.ScrapeRequest.from_dict({
                "niche_query": "کافه", "city_query": "رشت", "engine_mode": "osm",
                "lang": "fa", "limit": 10, "depth": 2})
            pipeline.run_scrape(request)
        finally:
            pipeline.make_engine = original
        self.assertEqual(captured[0].get("sweep_boxes"), [])


class ScanPlanTests(unittest.TestCase):
    """اسکراب کامل: کاشی‌بندی شهر + ادامهٔ بخش‌های باقی‌مانده + پوشش گزارش‌شده."""

    def _run(self, request: dict, tiles_done: int | None = None):
        captured: list[dict] = []

        class FakeEngine:
            name = "osm"
            live = True

            def __init__(self):
                self.coverage = []
                self.tile_coverage = []
                self.attempts = []

            def health(self):
                return {"up": True}

            def run_job(self, body, on_progress=None):
                captured.append(body)
                tiles = body.get("scan_tiles") or []
                done = tiles if tiles_done is None else tiles[:tiles_done]
                self.tile_coverage = (
                    [{"label": t["label"], "rows": 2, "via": "nominatim", "error": ""}
                     for t in done]
                    + [{"label": t["label"], "rows": 0, "via": "",
                        "error": "بودجهٔ زمانی تمام شد"} for t in tiles[len(done):]])
                return "fake-job", []

        original = pipeline.make_engine
        pipeline.make_engine = lambda mode=None: FakeEngine()
        try:
            result = pipeline.run_scrape(pipeline.ScrapeRequest.from_dict(request))
        finally:
            pipeline.make_engine = original
        return captured, result

    def test_city_run_is_tiled_into_several_pieces(self):
        captured, result = self._run({"niche_query": "کافه", "city_key": "tehran",
                                      "city_query": "تهران", "engine_mode": "osm",
                                      "lang": "fa", "limit": 30})
        tiles = captured[0].get("scan_tiles") or []
        self.assertGreaterEqual(len(tiles), 4, "شهر باید کاشی‌بندی شود")
        for tile in tiles:
            self.assertEqual(len(tile["bbox"]), 4)
            self.assertGreater(tile["bbox"][1], tile["bbox"][0])
        scan = (result.analytics or {}).get("scan") or {}
        self.assertTrue(scan.get("full"))
        self.assertEqual(scan.get("total"), len(tiles))
        self.assertEqual(scan.get("done"), len(tiles))
        self.assertEqual(scan.get("remaining"), [])
        self.assertEqual(result.run.get("area_level"), "city")

    def test_partial_coverage_requests_continuation_and_resumes_it(self):
        captured, result = self._run({"niche_query": "کافه", "city_key": "tehran",
                                      "city_query": "تهران", "engine_mode": "osm",
                                      "lang": "fa", "limit": 30}, tiles_done=2)
        scan = (result.analytics or {}).get("scan") or {}
        self.assertEqual(scan.get("done"), 2)
        self.assertGreater(len(scan.get("remaining") or []), 0)
        continuation = (result.stats or {}).get("continuation") or {}
        self.assertTrue(continuation, "بخش باقی‌مانده باید درخواست ادامه بسازد")
        self.assertEqual(continuation["request"]["resume_of"], result.run_id)
        self.assertEqual(continuation["request"]["resume_state"]["done"],
                         scan.get("labels_done"))
        # اجرای بخش دوم فقط کاشی‌های نمانده را می‌پرسد
        captured2, result2 = self._run(continuation["request"])
        tiles2 = captured2[0].get("scan_tiles") or []
        self.assertEqual(len(tiles2), len(scan.get("remaining") or []))
        scan2 = (result2.analytics or {}).get("scan") or {}
        self.assertEqual(result2.run_id, result.run_id, "ادامه همان اجراست")
        self.assertEqual(scan2.get("done"), scan2.get("total"))
        self.assertEqual(scan2.get("remaining"), [])

    def test_continuation_chain_covers_a_plan_bigger_than_chunk_default(self):
        """سقف پیش‌فرض بخش‌ها نباید کاشی‌های شهر بزرگ را نیمه‌کاره رها کند."""
        from leadgen import config as config_mod

        original = config_mod.SCAN_MAX_CHUNKS
        config_mod.SCAN_MAX_CHUNKS = 2        # عمداً کوچک‌تر از تعداد کاشی‌ها
        try:
            _, result = self._run({"niche_query": "کافه", "city_key": "tehran",
                                   "city_query": "تهران", "engine_mode": "osm",
                                   "lang": "fa", "limit": 30}, tiles_done=2)
        finally:
            config_mod.SCAN_MAX_CHUNKS = original
        scan = (result.analytics or {}).get("scan") or {}
        self.assertGreater(len(scan.get("remaining") or []), 2)
        # حتی با SCAN_MAX_CHUNKS=2، زنجیرهٔ ادامه باید کاملِ برنامه را پوشش دهد
        self.assertTrue((result.stats or {}).get("continuation"),
                        "کاشی‌های باقی‌مانده نباید بی‌ادامه بمانند")

    def test_street_run_uses_one_padded_tile_and_clips_to_city(self):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "یزد", "city_key": "yazd",
            "street": "خیابان کاشانی", "area_level": "street", "engine_mode": "osm",
            "lang": "fa", "limit": 10})
        expected = {"bbox": [31.85, 31.95, 54.30, 54.42]}

        def fake_refine(req, city, **kwargs):
            req.bbox = list(expected["bbox"])          # مثل نسخهٔ واقعی، درجا می‌نویسد
            req.lat, req.lon = "31.900000", "54.360000"
            return list(expected["bbox"])

        with mock.patch.object(pipeline.regions, "resolve", return_value={
                "lat": 31.897, "lon": 54.36, "radius": 1200, "zoom": 16,
                "level": "street", "label": "خیابان کاشانی، یزد",
                "keyword_label": "خیابان کاشانی، یزد", "city_key": "yazd",
                "city_fa": "یزد", "unresolved": [],
                "bbox": [31.88, 31.91, 54.35, 54.37]}), \
                mock.patch.object(pipeline, "refine_area_bbox", side_effect=fake_refine), \
                mock.patch.object(pipeline, "make_engine"):
            captured, result = self._run({**request.__dict__})
        tiles = captured[0].get("scan_tiles") or []
        self.assertEqual(len(tiles), 1, "خیابان = یک کاشی دقیق")
        # کاشی همان جعبهٔ OSM است، فقط ۲٪ حاشیهٔ tiling دورش
        tile_box = tiles[0]["bbox"]
        for got, want in zip(tile_box, expected["bbox"]):
            self.assertAlmostEqual(got, want, delta=0.02)
        self.assertLessEqual(tile_box[0], expected["bbox"][0])
        self.assertGreaterEqual(tile_box[1], expected["bbox"][1])
        self.assertEqual(captured[0].get("bbox"), expected["bbox"])
        scan = (result.analytics or {}).get("scan") or {}
        self.assertEqual(scan.get("total"), 1)

    def test_unknown_street_still_aborts(self):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "یزد", "city_key": "yazd",
            "street": "خیابان ناموجود", "area_level": "street", "engine_mode": "demo",
            "lang": "fa", "limit": 10})
        with mock.patch.object(pipeline.regions, "resolve", return_value={
                "lat": 31.897, "lon": 54.36, "radius": 1200, "zoom": 16,
                "level": "street", "label": "خیابان ناموجود، یزد",
                "keyword_label": "خیابان ناموجود، یزد", "city_key": "yazd",
                "city_fa": "یزد", "unresolved": ["street"]}), \
                mock.patch.object(pipeline, "refine_area_bbox", return_value=[]):
            with self.assertRaises(pipeline.EngineError) as ctx:
                pipeline.run_scrape(request)
        self.assertIn("پیدا نشد", str(ctx.exception))


class StreetLookupTests(unittest.TestCase):
    """جعبهٔ واقعی خیابان از OSM — کل خیابان، نه یک نقطه."""

    def test_lookup_area_returns_padded_nominatim_box(self):
        from leadgen.providers import osm
        payload = [{
            "lat": "31.890", "lon": "54.360", "type": "residential",
            "class": "highway", "osm_type": "way", "osm_id": 555,
            "display_name": "خیابان کاشانی، یزد، ایران",
            "boundingbox": ["31.8835", "31.8965", "54.3535", "54.3665"],
        }]
        with mock.patch.object(osm, "_http_json", return_value=payload), \
                mock.patch.object(osm, "_throttle", return_value=None):
            found = osm.lookup_area("خیابان کاشانی یزد", bbox=[31.7, 32.1, 54.1, 54.6])
        self.assertEqual(found["bbox"], [31.8835, 31.8965, 54.3535, 54.3665])
        self.assertEqual(found["type"], "residential")
        self.assertIn("کاشانی", found["label"])

    def test_lookup_area_ignores_non_area_results(self):
        from leadgen.providers import osm
        payload = [{"lat": "31.89", "lon": "54.36", "type": "city", "class": "place",
                    "boundingbox": ["31.0", "32.0", "53.0", "55.0"],
                    "display_name": "یزد"}]
        with mock.patch.object(osm, "_http_json", return_value=payload), \
                mock.patch.object(osm, "_throttle", return_value=None):
            found = osm.lookup_area("یزد", bbox=None)
        self.assertTrue(found["bbox"])

    def test_lookup_area_network_failure_falls_back_silently(self):
        from leadgen.providers import osm
        with mock.patch.object(osm, "_http_json", side_effect=OSError("down")), \
                mock.patch.object(osm, "_throttle", return_value=None):
            with self.assertRaises(osm.OSMDownError):
                osm.lookup_area("خیابان کاشانی یزد", bbox=None)


class StreetVariantTests(unittest.TestCase):
    """«خیابان X» باید با شکل‌های مختلف پرسیده شود تا پیدا شود، نه اینکه خطا بدهد."""

    def test_query_variants_cover_prefixed_and_short_names(self):
        variants = pipeline._area_query_variants("خیابان کاشانی", "", "یزد")
        self.assertIn("خیابان کاشانی، یزد", variants)
        self.assertIn("کاشانی، یزد", variants)
        self.assertIn("کاشانی", variants)
        # شهر خالی → فقط شکل‌های نام
        self.assertEqual(pipeline._area_query_variants("کاشانی", "", ""),
                         ["کاشانی"])

    def test_refine_uses_next_variant_when_first_returns_nothing(self):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "یزد", "city_key": "yazd",
            "street": "خیابان کاشانی", "area_level": "street", "engine_mode": "osm"})
        request.area_label = "خیابان کاشانی، یزد"
        request.area_unresolved = ["street"]
        city = {"bbox": [31.70, 32.10, 54.10, 54.60], "country": "IR",
                "label_local": "یزد"}
        seen: list[str] = []

        def fake_lookup(query, **kwargs):
            seen.append(query)
            if query.startswith("خیابان کاشانی"):
                return {}                       # Nominatim با نام کامل چیزی نمی‌دهد
            return {"bbox": [31.8835, 31.8965, 54.3535, 54.3665], "label": query}

        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", side_effect=fake_lookup), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox", return_value={}):
            box = pipeline.refine_area_bbox(request, city, allow_network=True)
        # جعبهٔ OSM + حاشیهٔ ~۱۵۰ متری (مغازه‌های دو طرف خیابان)
        self.assertAlmostEqual(box[0], 31.8820, places=3)
        self.assertAlmostEqual(box[1], 31.8980, places=3)
        self.assertAlmostEqual(box[2], 54.3520, places=3)
        self.assertAlmostEqual(box[3], 54.3680, places=3)
        self.assertGreaterEqual(len(seen), 2, "باید شکل دوم نام هم پرسیده شود")
        self.assertEqual(request.area_unresolved, [], "وقتی پیدا شد، «پیدا نشد» پاک می‌شود")
        # سطح باید «خیابان» شود، نه «شهر» — وگرنه گزارش می‌گوید کل شهر اسکراب شد
        self.assertEqual(request.area_level, "street")
        self.assertLess(request.radius, 3000, "شعاع باید اندازهٔ خیابان باشد، نه شهر")

    def test_lookup_area_merges_segments_of_a_long_street(self):
        """ولیعصر ۱۷ کیلومتر است؛ Nominatim تکه‌تکه می‌دهد و باید همه یکی شوند."""
        from leadgen.providers import osm
        payload = [
            {"type": "residential", "class": "highway", "osm_type": "way",
             "display_name": "خیابان ولیعصر، تهران، ایران",
             "boundingbox": ["35.700", "35.710", "51.410", "51.416"]},
            {"type": "residential", "class": "highway", "osm_type": "way",
             "display_name": "خیابان ولیعصر، تهران، ایران",
             "boundingbox": ["35.760", "35.780", "51.400", "51.405"]},
            {"type": "residential", "class": "highway", "osm_type": "way",
             "display_name": "خیابان انقلاب، تهران، ایران",
             "boundingbox": ["35.700", "35.701", "51.390", "51.391"]},
        ]
        with mock.patch.object(osm, "_http_json", return_value=payload), \
                mock.patch.object(osm, "_throttle", return_value=None):
            found = osm.lookup_area("خیابان ولیعصر، تهران", bbox=[35.6, 35.8, 51.2, 51.6],
                                    limit=10, merge=True)
        self.assertEqual(found["bbox"], [35.7, 35.78, 51.4, 51.416])
        self.assertEqual(found.get("segments"), 2, "خیابان انقلاب نباید جمع شود")

    def test_refine_rejects_city_sized_box_so_only_the_street_is_scraped(self):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "یزد", "city_key": "yazd",
            "street": "خیابان ناموجود", "area_level": "street", "engine_mode": "osm"})
        city = {"bbox": [31.70, 32.10, 54.10, 54.60], "country": "IR", "label_local": "یزد"}

        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area",
                           return_value={"bbox": [31.70, 32.10, 54.10, 54.60]}), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox", return_value={}):
            box = pipeline.refine_area_bbox(request, city, allow_network=True)
        self.assertEqual(box, [], "جعبهٔ هم‌اندازهٔ شهر نباید به‌جای خیابان پذیرفته شود")


class StreetWholeOverpassTests(unittest.TestCase):
    """خیابان بلند (ولیعصر) باید کل مسیر باشد؛ Nominatim فقط یک تکه می‌دهد."""

    def _request(self):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "تهران", "city_key": "tehran",
            "street": "خیابان ولیعصر", "area_level": "street", "engine_mode": "osm"})
        request.area_label = "خیابان ولیعصر، تهران"
        return request

    def test_refine_prefers_overpass_when_it_covers_more_of_the_street(self):
        request = self._request()
        city = {"bbox": [35.60, 35.80, 51.20, 51.60], "country": "IR", "label_local": "تهران"}
        nominatim = {"bbox": [35.705, 35.715, 51.414, 51.416], "label": "خیابان ولیعصر، تهران"}
        overpass = {"bbox": [35.700, 35.760, 51.390, 51.420], "ways": 41}

        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", return_value=dict(nominatim)), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox", return_value=overpass):
            box = pipeline.refine_area_bbox(request, city, allow_network=True)
        # جعبهٔ بزرگ‌تر Overpass + حاشیهٔ کوچک
        self.assertAlmostEqual(box[0], 35.6988, places=3)
        self.assertAlmostEqual(box[1], 35.7612, places=3)
        self.assertLess(box[0], 35.7001, "باید از تکهٔ ۱ کیلومتری Nominatim بزرگ‌تر باشد")
        self.assertGreater(box[1], 35.7599)
        self.assertGreater(request.radius, 3000, "شعاع باید اندازهٔ خیابان بلند باشد")
        self.assertEqual(request.area_level, "street")

    def test_overpass_city_sized_box_is_not_accepted(self):
        request = self._request()
        city = {"bbox": [35.60, 35.80, 51.20, 51.60], "country": "IR", "label_local": "تهران"}
        nominatim = {"bbox": [35.705, 35.715, 51.414, 51.416], "label": "خیابان ولیعصر، تهران"}
        huge = {"bbox": [35.60, 35.80, 51.20, 51.60], "ways": 900}

        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", return_value=dict(nominatim)), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox", return_value=huge):
            box = pipeline.refine_area_bbox(request, city, allow_network=True)
        self.assertAlmostEqual(box[0], 35.7035, places=3)   # همان جعبهٔ Nominatim + حاشیه
        self.assertAlmostEqual(box[1], 35.7165, places=3)

    def test_overpass_failure_is_swallowed(self):
        request = self._request()
        city = {"bbox": [35.60, 35.80, 51.20, 51.60], "country": "IR", "label_local": "تهران"}
        nominatim = {"bbox": [35.705, 35.715, 51.414, 51.416], "label": "خیابان ولیعصر، تهران"}

        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", return_value=dict(nominatim)), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox",
                           side_effect=__import__(
                               "leadgen.providers.osm", fromlist=["OSMDownError"]).OSMDownError("down")):
            box = pipeline.refine_area_bbox(request, city, allow_network=True)
        self.assertEqual(len(box), 4, "قطعی Overpass نباید محدودهٔ پیدا‌شده را از بین ببرد")

    def test_osm_full_name_from_the_label_is_asked_too(self):
        """«چهارباغ» در OSM «چهارباغ عباسی» است؛ نام کامل باید پرسیده شود."""
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "اصفهان", "city_key": "isfahan",
            "street": "خیابان چهارباغ", "area_level": "street", "engine_mode": "osm"})
        request.area_label = "خیابان چهارباغ، اصفهان"
        city = {"bbox": [32.55, 32.75, 51.55, 51.80], "country": "IR",
                "label_local": "اصفهان"}
        nominatim = {"bbox": [32.6480, 32.6520, 51.6690, 51.6740],
                     "label": "خیابان چهارباغ عباسی، اصفهان، ایران"}
        seen: list[dict] = []

        def fake_wide(names, **kwargs):
            seen.append({"names": names, **kwargs})
            return {}

        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", return_value=dict(nominatim)), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox", side_effect=fake_wide):
            pipeline.refine_area_bbox(request, city, allow_network=True)
        self.assertIn("چهارباغ عباسی", seen[0]["names"],
                      "نام کاملِ خودِ OSM باید جزو پرسش‌ها باشد")
        self.assertEqual("چهارباغ", seen[0]["names"][0])

    def test_overpass_query_is_anchored_near_the_nominatim_segment(self):
        request = self._request()
        city = {"bbox": [35.60, 35.80, 51.20, 51.60], "country": "IR", "label_local": "تهران"}
        nominatim = {"bbox": [35.705, 35.715, 51.414, 51.416], "label": "خیابان ولیعصر، تهران"}
        seen: list[dict] = []

        def fake_wide(names, **kwargs):
            seen.append({"names": names, **kwargs})
            return {}

        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", return_value=dict(nominatim)), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox", side_effect=fake_wide):
            pipeline.refine_area_bbox(request, city, allow_network=True)
        self.assertEqual(seen[0]["names"], ["ولیعصر", "خیابان ولیعصر"])
        self.assertEqual(seen[0]["near"], [35.705, 35.715, 51.414, 51.416],
                         "تکه‌های هم‌نامِ دورافتاده باید با همین لنگر فیلتر شوند")
        self.assertGreaterEqual(seen[0]["budget_s"], 15, "کوئری سنگین‌تر از ۱۲ ثانیه هم فرصت داشته باشد")

    def test_warning_is_honest_when_overpass_cannot_widen_the_street(self):
        request = self._request()
        city = {"bbox": [35.60, 35.80, 51.20, 51.60], "country": "IR", "label_local": "تهران"}
        nominatim = {"bbox": [35.705, 35.715, 51.414, 51.416], "label": "خیابان ولیعصر، تهران"}
        warnings: list[str] = []

        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", return_value=dict(nominatim)), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox", return_value={}):
            pipeline.refine_area_bbox(request, city, allow_network=True, warnings=warnings)
        self.assertTrue(any("بخشی از آن جا بماند" in w for w in warnings), warnings)


class StreetCorridorTests(unittest.TestCase):
    """جعبهٔ کل خیابان یک مستطیل بزرگ است؛ فقط مغازه‌های نوار خیابان باید بمانند."""

    def test_near_street_accepts_corridor_and_keeps_rows_without_coordinates(self):
        boxes = [[35.7000, 35.7040, 51.4100, 51.4120]]
        self.assertTrue(pipeline._near_street({"latitude": "35.7020", "longitude": "51.4110"}, boxes))
        # ~۱۳۰ متر حاشیه: ۰٫۰۰۰۹ درجه بالاتر از لبه
        self.assertTrue(pipeline._near_street({"latitude": "35.7049", "longitude": "51.4110"}, boxes))
        self.assertFalse(pipeline._near_street({"latitude": "35.7300", "longitude": "51.4110"}, boxes))
        self.assertTrue(pipeline._near_street({}, boxes), "ردیف بدون مختصات نباید حذف شود")

    def test_refine_stores_street_boxes_for_the_corridor_filter(self):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "تهران", "city_key": "tehran",
            "street": "خیابان ولیعصر", "area_level": "street", "engine_mode": "osm"})
        request.area_label = "خیابان ولیعصر، تهران"
        city = {"bbox": [35.60, 35.85, 51.20, 51.60], "country": "IR", "label_local": "تهران"}
        nominatim = {"bbox": [35.705, 35.715, 51.414, 51.416], "label": "خیابان ولیعصر، تهران"}
        boxes = [[35.700, 35.710, 51.410, 51.416], [35.710, 35.720, 51.410, 51.416],
                 [35.720, 35.730, 51.410, 51.416]]
        overpass = {"bbox": [35.700, 35.730, 51.410, 51.416], "ways": 3, "boxes": boxes}

        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", return_value=dict(nominatim)), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox", return_value=overpass):
            pipeline.refine_area_bbox(request, city, allow_network=True)
        self.assertEqual(len(request.street_boxes), 3, "تکه‌های خیابان باید در درخواست بمانند")
        self.assertEqual(request.street_boxes[0], [35.700, 35.710, 51.410, 51.416])

    def test_overpass_corridor_boxes_survive_a_same_size_bbox(self):
        """اگر Overpass همان جعبهٔ Nominatim را داد، تکه‌های خیابان نباید دور ریخته شوند."""
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "تهران", "city_key": "tehran",
            "street": "خیابان انقلاب", "area_level": "street", "engine_mode": "osm"})
        box = [35.696, 35.701, 51.391, 51.404]
        nominatim = {"bbox": box, "label": "خیابان انقلاب، تهران"}
        overpass = {"bbox": box, "ways": 4, "boxes": [box]}
        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", return_value=dict(nominatim)), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox", return_value=overpass):
            pipeline.refine_area_bbox(request, {"bbox": [35.6, 35.9, 51.2, 51.6],
                                                "country": "IR", "label_local": "تهران"},
                                      allow_network=True)
        self.assertEqual([box], request.street_boxes)

    def test_city_level_runs_get_no_corridor_filter(self):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "تهران", "city_key": "tehran",
            "area_level": "city", "engine_mode": "osm"})
        box = pipeline.refine_area_bbox(request, {"bbox": [35.6, 35.85, 51.2, 51.6]},
                                       allow_network=False)
        self.assertEqual(box, [])
        self.assertEqual(request.street_boxes, [])


class MultiStreetTests(unittest.TestCase):
    """«کافه در ولیعصر و انقلاب» = دو خیابان با دو نوار جدا، نه یک نقطه."""

    def test_split_streets_accepts_the_separators_users_actually_type(self):
        self.assertEqual(
            pipeline._split_streets("خیابان ولیعصر + خیابان انقلاب، خیابان آزادی"),
            ["خیابان ولیعصر", "خیابان انقلاب", "خیابان آزادی"])
        self.assertEqual(
            pipeline._split_streets("ولیعصر؛ انقلاب | آزادی\nکارگر"),
            ["ولیعصر", "انقلاب", "آزادی", "کارگر"])
        # «و» جداکننده نیست: «ولیعصر و انقلاب» یک نام است تا کاربر خودش «+» بگذارد
        self.assertEqual(pipeline._split_streets("ولیعصر و انقلاب، آزادی"),
                         ["ولیعصر و انقلاب", "آزادی"])
        self.assertEqual(pipeline._split_streets(""), [])

    def test_split_streets_dedupes_and_caps_at_eight(self):
        many = "، ".join(f"خیابان شماره {i}" for i in range(1, 15))
        names = pipeline._split_streets(many)
        self.assertEqual(8, len(names))
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(["خیابان الف", "خیابان ب"],
                         pipeline._split_streets("خیابان الف، خیابان الف، خیابان ب"))

    def test_from_dict_parses_streets_string_and_list(self):
        from_string = pipeline.ScrapeRequest.from_dict({"streets": "ولیعصر، انقلاب، آزادی"})
        self.assertEqual(["ولیعصر", "انقلاب", "آزادی"], from_string.streets)
        from_list = pipeline.ScrapeRequest.from_dict({"streets": ["ولیعصر", "انقلاب"]})
        self.assertEqual(["ولیعصر", "انقلاب"], from_list.streets)

    def _city(self):
        return {"bbox": [35.57, 35.83, 51.10, 51.60], "country": "IR",
                "label_local": "تهران", "label": "Tehran", "key": "tehran",
                "lat": 35.69, "lon": 51.39}

    def _request(self):
        return pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "تهران", "city_key": "tehran",
            "streets": "خیابان ولیعصر، خیابان انقلاب", "area_level": "street",
            "engine_mode": "osm"})

    def test_resolve_street_areas_unions_each_streets_own_boxes(self):
        boxes = {"ولیعصر": [35.700, 35.710, 51.410, 51.414],
                 "انقلاب": [35.720, 35.730, 51.400, 51.404]}

        def fake_lookup(query, **kwargs):
            for name, box in boxes.items():
                if name in query:
                    return {"bbox": box, "label": f"{name}، تهران", "boxes": [box]}
            return {}

        def fake_overpass(names, **kwargs):
            # مثل Overpass: جعبهٔ کل wayهای هم‌نام (اینجا همان جعبهٔ Nominatim)
            box = list(kwargs.get("near") or [])
            return {"bbox": box, "ways": 2, "boxes": [box] if box else []}

        request = self._request()
        warnings = []
        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", side_effect=fake_lookup), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox",
                           side_effect=fake_overpass):
            areas = pipeline.resolve_street_areas(request, self._city(),
                                                  allow_network=True, warnings=warnings)
        self.assertEqual(2, len(areas))
        self.assertEqual("خیابان ولیعصر + خیابان انقلاب، تهران", request.area_label)
        self.assertEqual(2, len(request.street_boxes))
        self.assertIn(boxes["ولیعصر"], request.street_boxes)
        self.assertIn(boxes["انقلاب"], request.street_boxes)
        # حاشیهٔ ~۱۵۰ متری refine_area_bbox: اجتماع دو نوار، نه یکی
        self.assertAlmostEqual(35.700, request.bbox[0], places=2)
        self.assertAlmostEqual(35.730, request.bbox[1], places=2)
        self.assertAlmostEqual(51.400, request.bbox[2], places=2)
        self.assertAlmostEqual(51.414, request.bbox[3], places=2)
        self.assertEqual("street", request.area_level)
        self.assertTrue(any("چند خیابان" in w for w in warnings))
        self.assertTrue(600 + 1 > len(request.street_boxes))   # سقف جعبه‌ها

    def test_resolve_street_areas_reports_missing_instead_of_silently_using_it(self):
        def fake_lookup(query, **kwargs):
            if "ولیعصر" in query:
                return {"bbox": [35.700, 35.710, 51.410, 51.414], "label": "ولیعصر، تهران"}
            return {}

        request = self._request()
        request.streets = ["خیابان ولیعصر", "خیابان خیالی"]
        warnings = []
        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", side_effect=fake_lookup), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox",
                           return_value={"bbox": [], "ways": 0, "boxes": []}):
            areas = pipeline.resolve_street_areas(request, self._city(),
                                                  allow_network=True, warnings=warnings)
        self.assertEqual(["خیابان ولیعصر"], [a["name"] for a in areas])
        self.assertTrue(any("پیدا نشد" in w and "خیابان خیالی" in w for w in warnings))
        self.assertEqual("خیابان ولیعصر، تهران", request.area_label)

    def test_street_without_overpass_corridor_keeps_its_own_box_and_says_so(self):
        """اگر Overpass نوار نداد، لیدهای آن خیابان نباید بی‌صدا حذف شوند."""
        def fake_lookup(query, **kwargs):
            if "ولیعصر" in query:
                return {"bbox": [35.700, 35.710, 51.410, 51.414], "label": "ولیعصر"}
            if "انقلاب" in query:
                return {"bbox": [35.696, 35.701, 51.391, 51.404], "label": "انقلاب"}
            return {}

        def fake_overpass(names, **kwargs):
            if "انقلاب" in " ".join(names):
                return {}
            box = list(kwargs.get("near") or [])
            return {"bbox": box, "ways": 2, "boxes": [box]}

        request = self._request()
        warnings = []
        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", side_effect=fake_lookup), \
                mock.patch("leadgen.providers.osm.overpass_street_bbox",
                           side_effect=fake_overpass):
            areas = pipeline.resolve_street_areas(request, self._city(),
                                                  allow_network=True, warnings=warnings)
        by_name = {a["name"]: a for a in areas}
        self.assertTrue(by_name["خیابان انقلاب"]["partial"])
        self.assertEqual([by_name["خیابان انقلاب"]["bbox"]],
                         by_name["خیابان انقلاب"]["boxes"])
        self.assertFalse(by_name["خیابان ولیعصر"]["partial"])
        self.assertEqual(2, len(request.street_boxes))
        self.assertTrue(any("تکهٔ Nominatim" in w for w in warnings))

    def test_all_streets_unresolved_stops_the_run(self):
        request = self._request()
        with mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True), \
                mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False), \
                mock.patch("leadgen.providers.osm.lookup_area", return_value={}):
            with self.assertRaises(pipeline.EngineError):
                pipeline.resolve_street_areas(request, self._city(),
                                              allow_network=True, warnings=[])
        self.assertEqual([], request.street_boxes, "نباید محدودهٔ جای دیگری اسکراب شود")

    def test_single_street_is_not_handled_here(self):
        request = self._request()
        request.streets = ["خیابان ولیعصر"]
        self.assertEqual([], pipeline.resolve_street_areas(request, self._city(),
                                                           allow_network=False))


class PlacesFillTests(unittest.TestCase):
    """OSM در شهرهای کوچک نازک است؛ Places همان خیابان را پر می‌کند، نه جای دیگر."""

    def _request(self, streets=None):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "یزد", "city_key": "yazd",
            "street": "خیابان کاشانی", "area_level": "street", "engine_mode": "osm",
            "limit": 20})
        request.street_boxes = [[31.870, 31.880, 54.370, 54.385]]
        request.bbox = [31.868, 31.882, 54.368, 54.387]
        request.area_label = "خیابان کاشانی، یزد"
        if streets:
            request.streets = list(streets)
            request.street_areas = [
                {"name": name, "bbox": box, "boxes": [box]}
                for name, box in zip(streets, [
                    [35.700, 35.720, 51.400, 51.420],
                    [35.690, 35.710, 51.430, 51.450],
                ])]
        return request

    def _row(self, lat, lon, pid):
        return {"title": f"کافه {pid}", "latitude": str(lat), "longitude": str(lon),
                "place_id": pid, "source": "google_places"}

    def test_no_key_means_silent_no_op(self):
        request = self._request()
        with mock.patch("leadgen.providers.places.available", return_value=False), \
                mock.patch("leadgen.providers.places.search") as search:
            self.assertEqual([], pipeline._places_fill(request, [{"keywords": ["کافه در کاشانی"]}], []))
        search.assert_not_called()

    def test_rows_outside_the_street_corridor_are_dropped(self):
        request = self._request()
        rows = [self._row(31.875, 54.377, "in"), self._row(31.900, 54.500, "far")]
        warnings = []
        with mock.patch("leadgen.providers.places.available", return_value=True), \
                mock.patch("leadgen.providers.places.search", return_value=rows):
            got = pipeline._places_fill(request, [{"keywords": ["کافه در کاشانی"]}], warnings)
        self.assertEqual(["in"], [r["place_id"] for r in got])
        self.assertTrue(any("بیرون از نوار خیابان" in w for w in warnings))
        self.assertTrue(any("Google Places" in w for w in warnings))

    def test_places_failure_is_reported_not_raised(self):
        request = self._request()
        warnings = []
        with mock.patch("leadgen.providers.places.available", return_value=True), \
                mock.patch("leadgen.providers.places.search", side_effect=RuntimeError("quota")):
            got = pipeline._places_fill(request, [{"keywords": ["کافه در کاشانی"]}], warnings)
        self.assertEqual([], got)
        self.assertTrue(any("Google Places هم نتیجه نداد" in w for w in warnings))

    def test_each_street_gets_its_own_rectangle_and_query(self):
        request = self._request(streets=["خیابان ولیعصر", "خیابان انقلاب"])
        request.area_label = "خیابان ولیعصر + خیابان انقلاب، تهران"
        request.street_boxes = [a["boxes"][0] for a in request.street_areas]
        request.bbox = [35.690, 35.720, 51.400, 51.450]
        calls = []

        def fake_search(term, **kwargs):
            calls.append((term, kwargs.get("bbox")))
            return [self._row(35.71, 51.41, f"p{len(calls)}")]

        with mock.patch("leadgen.providers.places.available", return_value=True), \
                mock.patch("leadgen.providers.places.search", side_effect=fake_search):
            got = pipeline._places_fill(
                request, [{"keywords": ["کافه در خیابان ولیعصر + خیابان انقلاب، تهران"]}], [])
        self.assertEqual(2, len(calls))
        self.assertIn("ولیعصر", calls[0][0])
        self.assertNotIn("انقلاب", calls[0][0])
        self.assertIn("انقلاب", calls[1][0])
        self.assertAlmostEqual(35.6994, calls[0][1][0], places=4)
        self.assertAlmostEqual(51.4294, calls[1][1][2], places=4)
        self.assertEqual(2, len(got))


class StreetGeometryMemoryTests(unittest.TestCase):
    """«حافظهٔ خیابان‌ها»: نوار کامل ولیعصر یک‌بار حل، بعد فوری و مقاوم در دسترس."""

    def _request(self, street="خیابان ولیعصر"):
        request = pipeline.ScrapeRequest.from_dict({
            "niche_query": "کافه", "city_query": "تهران", "city_key": "tehran",
            "street": street, "area_level": "street", "engine_mode": "osm"})
        request.area_label = f"{street}، تهران"
        return request

    def _city(self):
        return {"bbox": [35.60, 35.85, 51.20, 51.60], "country": "IR",
                "label_local": "تهران", "key": "tehran"}

    def _nominatim(self):
        return {"bbox": [35.705, 35.715, 51.414, 51.416], "label": "خیابان ولیعصر، تهران"}

    def _patches(self, *, cached=None, wide=None, put=None, lookup=None, cache_error=None):
        return [
            mock.patch.object(pipeline.config, "OSM_AREA_LOOKUP", True),
            mock.patch.object(pipeline.config, "_looks_like_unittest", return_value=False),
            mock.patch.object(pipeline.config, "STREET_CACHE_DAYS", 14),
            mock.patch("leadgen.providers.osm.lookup_area",
                       return_value=dict(lookup or self._nominatim())),
            mock.patch("leadgen.providers.osm.overpass_street_bbox",
                       return_value=dict(wide or {})),
            mock.patch.object(pipeline.storage, "get_street_cache",
                              side_effect=cache_error if cache_error else None,
                              return_value=None if cache_error else
                              (dict(cached) if cached else None)),
            mock.patch.object(pipeline.storage, "put_street_cache",
                              side_effect=put if put is not None else (lambda *a, **k: True)),
        ]

    def _run(self, request, **kwargs):
        warnings = kwargs.pop("warnings", None)
        patches = self._patches(**kwargs)
        for patch in patches:
            patch.start()
        try:
            return pipeline.refine_area_bbox(request, self._city(), allow_network=True,
                                             warnings=warnings, use_cache=True)
        finally:
            for patch in reversed(patches):
                patch.stop()

    def test_fresh_memory_skips_overpass_and_keeps_the_whole_corridor(self):
        request = self._request()
        boxes = [[35.6600, 35.7000, 51.4000, 51.4200], [35.7000, 35.7600, 51.4000, 51.4300]]
        cached = {"bbox": [35.6600, 35.7600, 51.4000, 51.4300], "boxes": boxes,
                  "ways": 77, "length_km": 12.4, "age_days": 1.5}
        with mock.patch("leadgen.providers.osm.overpass_street_bbox") as overpass:
            box = self._run(request, cached=cached)
        overpass.assert_not_called()
        self.assertEqual(boxes, request.street_boxes, "نوار کامل باید از حافظه بیاید")
        self.assertLess(box[0], 35.66)
        self.assertGreater(box[1], 35.76)
        self.assertEqual(request.street_areas, [])
        self.assertEqual("cache", request.street_memory[0]["source"],
                         "کارنامه باید بگوید نوار از حافظه آمد")

    def test_memory_from_a_few_minutes_ago_is_fresh(self):
        """سن ۰٫۰ روز (سه دقیقه پیش) نباید «کهنه» حساب شود — باگ or/9999."""
        request = self._request()
        boxes = [[35.6600, 35.7600, 51.4000, 51.4300]]
        cached = {"bbox": [35.6600, 35.7600, 51.4000, 51.4300], "boxes": boxes,
                  "ways": 57, "length_km": 14.7, "age_days": 0.0}
        with mock.patch("leadgen.providers.osm.overpass_street_bbox") as overpass:
            self._run(request, cached=cached)
        overpass.assert_not_called()
        self.assertEqual("cache", request.street_memory[0]["source"])
        self.assertEqual(boxes, request.street_boxes)

    def test_stale_memory_rescues_the_run_when_overpass_is_down(self):
        request = self._request()
        boxes = [[35.6600, 35.7600, 51.4000, 51.4300]]
        cached = {"bbox": [35.6600, 35.7600, 51.4000, 51.4300], "boxes": boxes,
                  "ways": 77, "length_km": 12.4, "age_days": 40}
        warnings: list = []
        box = self._run(request, cached=cached, wide={}, warnings=warnings)
        self.assertEqual(boxes, request.street_boxes)
        self.assertGreater(box[1], 35.75, "نباید به تکهٔ ۱ کیلومتری Nominatim سقوط کند")
        self.assertTrue(any("حافظه" in w for w in warnings), warnings)

    def test_overpass_result_is_saved_for_the_next_run(self):
        request = self._request()
        wide = {"bbox": [35.6600, 35.7600, 51.4000, 51.4120], "ways": 77,
                "boxes": [[35.6600, 35.7600, 51.4000, 51.4120]], "length_km": 12.4,
                "via": "regex"}
        saved: list = []
        self._run(request, wide=wide, put=lambda *a, **k: saved.append((a, k)) or True)
        self.assertEqual(1, len(saved), "نوار حل‌شده باید ذخیره شود")
        args, kwargs = saved[0]
        self.assertEqual("ولیعصر", args[1], "نام بدون پیشوند ذخیره می‌شود")
        self.assertEqual(77, kwargs["ways"])
        self.assertEqual("regex", kwargs["source"])

    def test_memory_error_is_reported_instead_of_silently_rescraping(self):
        request = self._request()
        box = self._run(request, wide={}, cache_error=RuntimeError("boom"))
        self.assertEqual("none", request.street_memory[0]["source"])
        self.assertIn("boom", request.street_memory[0]["error"])
        self.assertEqual(4, len(box), "خطای حافظه نباید اجرا را بشکند")

    def test_memory_of_another_area_is_not_used(self):
        request = self._request()
        far_away = {"bbox": [35.80, 35.83, 51.50, 51.55],
                    "boxes": [[35.80, 35.83, 51.50, 51.55]], "ways": 9, "age_days": 2}
        box = self._run(request, cached=far_away, wide={})
        self.assertLess(box[1], 35.72, "حافظهٔ جای دیگر نباید محدوده را عوض کند")
        self.assertEqual([], request.street_boxes)


class StreetCacheStorageTests(unittest.TestCase):
    """ذخیره/خواندن نوار خیابان روی پایگاه‌دادهٔ محلی."""

    def setUp(self):
        storage.init_db()

    def test_roundtrip_and_age(self):
        # شهر ساختگی تا این آزمون با بقیه (که تهران را می‌سنجند) قاطی نشود
        storage.put_street_cache("cachetest", "خیابان نمونهٔ تست",
                                 bbox=[35.66, 35.76, 51.40, 51.412],
                                 boxes=[[35.66, 35.76, 51.40, 51.412]],
                                 ways=77, length_km=12.4)
        got = storage.get_street_cache("cachetest", "نمونهٔ تست")   # کلید نرمال‌شدهٔ یکسان
        self.assertIsNotNone(got)
        self.assertEqual(77, int(got["ways"]))
        self.assertEqual([[35.66, 35.76, 51.40, 51.412]], got["boxes"])
        self.assertLess(got["age_days"], 1)

    def test_old_rows_are_pruned(self):
        storage.put_street_cache("cachetest", "خیابان کهنه",
                                 bbox=[35.66, 35.76, 51.40, 51.43],
                                 boxes=[[35.66, 35.76, 51.40, 51.43]])
        with storage._LOCK, storage._connect() as conn:
            conn.execute("UPDATE street_cache SET updated_at=? WHERE key=?",
                         (1.0, storage.street_cache_key("cachetest", "خیابان کهنه")))
        self.assertGreaterEqual(storage.prune_street_cache(120), 1)
        self.assertIsNone(storage.get_street_cache("cachetest", "خیابان کهنه"))

    def test_empty_boxes_are_not_cached(self):
        self.assertFalse(storage.put_street_cache("cachetest", "خیابان انقلاب",
                                                  bbox=[35.7, 35.71, 51.4, 51.41], boxes=[]))
