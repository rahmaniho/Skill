"""آزمون لایهٔ ابری: کلاینت Supabase (PostgREST)، `pgstore` و dispatch در `storage`.

هیچ شبکهٔ واقعی مصرف نمی‌شود: `opener` کلاینت با یک حمل‌کنندهٔ ساختگی عوض می‌شود که
درخواست‌ها را ضبط و پاسخ از پیش‌تعیین‌شده برمی‌گرداند. (رفتار سمت SQL به‌صورت جدا
روی یک پروژهٔ واقعی Supabase اعتبارسنجی شده است — `supabase/schema.sql`.)

اجرا:  python3 -m unittest discover -t . -s tests -v
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="leadgen-cloud-"))
os.environ["LEADGEN_DATA_DIR"] = str(_TMP)

from leadgen import cloud, config, pgstore, storage  # noqa: E402


class FakeResponse:
    def __init__(self, payload=None, status: int = 200, headers=None, raw: bytes | None = None):
        self.status = status
        self.headers = headers or {}
        self._body = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._body


class FakeTransport:
    """حمل‌کنندهٔ ساختگی: هر درخواست را ضبط می‌کند و پاسخِ داده‌شده را برمی‌گرداند."""

    def __init__(self, responder=None):
        self.calls: list[dict] = []
        self.responder = responder or (lambda request, record: FakeResponse([]))

    def __call__(self, request, timeout):
        body = request.data
        record = {
            "method": request.get_method(),
            "url": request.full_url,
            "headers": {k.lower(): v for k, v in request.header_items()},
            "body": json.loads(body.decode("utf-8")) if body else None,
            "raw_body": body,
            "timeout": timeout,
        }
        self.calls.append(record)
        result = self.responder(request, record)
        if isinstance(result, Exception):
            raise result
        return result

    @property
    def last(self) -> dict:
        return self.calls[-1]

    def urls(self) -> list[str]:
        return [c["url"] for c in self.calls]


def make_client(transport=None, responder=None):
    transport = transport or FakeTransport(responder)
    client = cloud.SupabaseClient(url="https://demo.supabase.co", key="service-key",
                                  schema="public", opener=transport)
    return client, transport


def http_error(code: int = 404, detail: str = '{"message":"relation does not exist"}'):
    return urllib.error.HTTPError("https://demo.supabase.co", code, "Error", {},
                                  io.BytesIO(detail.encode("utf-8")))


class CloudClientTests(unittest.TestCase):
    def test_rows_builds_postgrest_query_and_auth_headers(self):
        client, transport = make_client()
        rows, _ = client.rows("runs", select="id,status", id="eq.run-1", limit=5,
                              order="created_at.desc")
        call = transport.last
        self.assertEqual(call["method"], "GET")
        self.assertIn("/rest/v1/runs?", call["url"])
        self.assertIn("select=id%2Cstatus", call["url"])
        self.assertIn("id=eq.run-1", call["url"])
        self.assertIn("order=created_at.desc", call["url"])
        self.assertIn("limit=5", call["url"])
        self.assertEqual(call["headers"]["apikey"], "service-key")
        self.assertEqual(call["headers"]["authorization"], "Bearer service-key")
        self.assertEqual(call["headers"]["accept-profile"], "public")
        self.assertEqual(rows, [])

    def test_rpc_posts_named_arguments(self):
        client, transport = make_client(responder=lambda r, rec: FakeResponse({"total": 3}))
        result = client.rpc("search_leads", {"params": {"q": "کافه"}})
        self.assertEqual(result, {"total": 3})
        self.assertTrue(transport.last["url"].endswith("/rest/v1/rpc/search_leads"))
        self.assertEqual(transport.last["body"], {"params": {"q": "کافه"}})
        self.assertEqual(transport.last["headers"]["content-type"], "application/json")

    def test_insert_batches_and_asks_for_upsert(self):
        client, transport = make_client()
        rows = [{"id": f"l{i}"} for i in range(450)]
        written = client.insert("leads", rows, upsert=True, on_conflict="id")
        self.assertEqual(written, 450)
        self.assertEqual(len(transport.calls), 3)          # ۲۰۰ + ۲۰۰ + ۵۰
        self.assertIn("resolution=merge-duplicates", transport.calls[0]["headers"]["prefer"])
        self.assertIn("on_conflict=id", transport.calls[0]["url"])
        self.assertEqual(len(transport.calls[0]["body"]), 200)
        self.assertEqual(len(transport.calls[2]["body"]), 50)

    def test_http_error_becomes_cloud_error_with_detail(self):
        client, _ = make_client(responder=lambda r, rec: http_error(401, '{"msg":"bad key"}'))
        with self.assertRaises(cloud.CloudError) as ctx:
            client.rows("leads")
        self.assertEqual(ctx.exception.status, 401)
        self.assertIn("bad key", ctx.exception.detail)
        self.assertIn("401", str(ctx.exception))

    def test_connection_error_is_reported_in_persian(self):
        def boom(request, record):
            raise urllib.error.URLError("connection refused")
        client, _ = make_client(responder=boom)
        with self.assertRaises(cloud.CloudError) as ctx:
            client.rpc("queue_stats", {})
        self.assertIn("Supabase", str(ctx.exception))

    def test_count_reads_content_range_header(self):
        client, transport = make_client(
            responder=lambda r, rec: FakeResponse(raw=b"", status=200,
                                                  headers={"Content-Range": "0-0/42"}))
        self.assertEqual(client.count("leads", segment="eq.hot"), 42)
        self.assertEqual(transport.last["method"], "HEAD")
        self.assertIn("count=exact", transport.last["headers"]["prefer"])

    def test_update_and_delete_pass_filters(self):
        client, transport = make_client()
        client.update("leads", {"status": "won"}, id="eq.lead-1")
        self.assertEqual(transport.last["method"], "PATCH")
        self.assertIn("id=eq.lead-1", transport.last["url"])
        self.assertEqual(transport.last["body"], {"status": "won"})
        client.delete("leads", run_id="eq.run-1")
        self.assertEqual(transport.last["method"], "DELETE")
        self.assertIn("run_id=eq.run-1", transport.last["url"])

    def test_missing_schema_gives_actionable_hint(self):
        client, _ = make_client(responder=lambda r, rec: http_error(
            404, '{"message":"relation \'public.runs\' does not exist"}'))
        cloud._CLIENT = client
        try:
            with self.assertRaises(cloud.CloudError) as ctx:
                pgstore.init_db()
            self.assertIn("schema.sql", str(ctx.exception))
        finally:
            cloud._CLIENT = None


class PgStoreTests(unittest.TestCase):
    def setUp(self):
        self.client, self.transport = make_client()
        cloud._CLIENT = self.client

    def tearDown(self):
        cloud._CLIENT = None

    def test_create_run_sends_native_json_and_booleans(self):
        self.transport.responder = lambda r, rec: FakeResponse("run-9")
        run_id = pgstore.create_run({"id": "run-9", "niche_fa": "کافه", "city": "تهران",
                                     "keywords": ["کافه در تهران"], "socials": True,
                                     "emails": 1, "status": "queued",
                                     "progress": {"stage": "queued"}, "created_at": 1700000000.5})
        self.assertEqual(run_id, "run-9")
        body = self.transport.last["body"]
        self.assertTrue(self.transport.last["url"].endswith("/rpc/save_run"))
        payload = body["payload"]
        self.assertEqual(payload["keywords"], ["کافه در تهران"])   # نه رشتهٔ JSON
        self.assertIs(payload["socials"], True)
        self.assertIs(payload["emails"], True)
        self.assertEqual(payload["progress"], {"stage": "queued"})
        self.assertEqual(payload["created_at"], 1700000000.5)

    def test_update_run_uses_patch_run(self):
        self.transport.responder = lambda r, rec: FakeResponse(True)
        pgstore.update_run("run-9", status="done", total=12, analytics={"avg_score": 80})
        self.assertTrue(self.transport.last["url"].endswith("/rpc/patch_run"))
        self.assertEqual(self.transport.last["body"],
                         {"p_id": "run-9", "payload": {"status": "done", "total": 12,
                                                      "analytics": {"avg_score": 80}}})

    def test_save_leads_converts_flags_and_json_columns(self):
        self.transport.responder = lambda r, rec: FakeResponse(None, status=201)
        row = {"id": "run-9-abc", "title": "کافه نارون", "gaps": ["no_website"],
               "signals": {"no_website": True}, "has_email": True, "has_phone": 1,
               "score": 88, "rating": "4.6", "reviews": 120, "status": "weird",
               "latitude": "35.75", "longitude": "51.41"}
        count = pgstore.save_leads("run-9", [dict(row)])
        self.assertEqual(count, 1)
        record = self.transport.last["body"][0]
        self.assertEqual(record["run_id"], "run-9")
        self.assertEqual(record["gaps"], ["no_website"])          # jsonb بومی
        self.assertEqual(record["signals"], {"no_website": True})
        self.assertEqual(record["has_email"], 1)                  # بول → عدد
        self.assertEqual(record["has_phone"], 1)
        self.assertEqual(record["status"], "new")                 # وضعیت نامعتبر → new
        self.assertEqual(record["rating"], 4.6)
        self.assertEqual(record["score"], 88)

    def test_search_leads_maps_params_and_normalizes_rows(self):
        payload = {"total": 2, "leads": [
            {"id": "a", "title": "کافه", "gaps": ["no_website"], "tags": [],
             "signals": {}, "has_phone": 1, "has_email": 0, "has_website": 0,
             "has_social": 0, "latitude": "35.75", "longitude": "51.41", "score": 90,
             "segment": "hot", "status": "new", "run_city": "تهران", "run_niche": "کافه",
             "run_area": "تهران، ونک", "run_created": 1700000000, "distance_m": 812.4},
            {"id": "b", "title": "رستوران", "gaps": [], "tags": [], "signals": {},
             "has_phone": 1, "has_email": 1, "has_website": 1, "has_social": 1,
             "latitude": "35.8", "longitude": "51.5", "score": 70, "segment": "warm",
             "status": "contacted"}],
            "facets": {"segment": {"hot": 1, "warm": 1}, "status": {}, "niche": {},
                       "contact": {"has_phone": 2}, "runs": {}, "total": 2, "counted": 2,
                       "partial": False, "score": {"min": 70, "max": 90, "avg": 80.0}}}
        self.transport.responder = lambda r, rec: FakeResponse(payload)
        result = pgstore.search_leads(q="کافه تهران", segment="hot", has_email=True,
                                      min_score=70, lat=35.7592, lon=51.4094, radius_m=2500,
                                      sort="score", order="desc", limit=5, offset=2)
        params = self.transport.last["body"]["params"]
        self.assertEqual(params["q"], "کافه تهران")
        self.assertEqual(params["segment"], "hot")
        self.assertIs(params["has_email"], True)
        self.assertEqual(params["min_score"], 70)
        self.assertEqual(params["radius_m"], 2500)
        self.assertEqual(params["limit"], 5)
        self.assertEqual(params["offset"], 2)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["returned"], 2)
        lead = result["leads"][0]
        self.assertEqual(lead["gaps"], ["no_website"])
        self.assertIs(lead["has_phone"], True)                    # نرمال‌سازی بول
        self.assertEqual(lead["lat"], 35.75)
        self.assertEqual(lead["distance_m"], 812)                 # گرد شده
        self.assertEqual(lead["run_area"], "تهران، ونک")
        self.assertEqual(lead["segment_fa"], storage._labels()[0].get("hot"))
        self.assertEqual(result["geo"], {"lat": 35.7592, "lon": 51.4094, "radius_m": 2500})
        self.assertEqual(result["filters"]["q"], "کافه تهران")
        self.assertEqual(result["facets"]["contact"], {"has_phone": 2})

    def test_facets_drop_zero_counts_like_sqlite(self):
        """تابع SQL صفرها را هم برمی‌گرداند؛ نسخهٔ محلی نه → باید یکی شوند."""
        payload = {"total": 2, "leads": [],
                   "facets": {"segment": {"hot": 1, "warm": 1, "cold": 0},
                              "status": {"new": 2, "contacted": 0},
                              "niche": {"کافه": 2},
                              "contact": {"has_phone": 2, "has_email": 0, "has_website": 1,
                                          "has_social": 0},
                              "runs": {"eq-run-1": 2},
                              "total": 2, "counted": 0, "partial": False,
                              "score": {"min": 53, "max": 75, "avg": 64.0}}}
        self.transport.responder = lambda r, rec: FakeResponse(payload)
        facets = pgstore.search_leads(lat=35.7592, lon=51.4094, radius_m=2000)["facets"]
        self.assertEqual(facets["segment"], {"hot": 1, "warm": 1})
        self.assertEqual(facets["status"], {"new": 2})
        self.assertEqual(facets["contact"], {"has_phone": 2, "has_website": 1})
        self.assertEqual(facets["runs"], {"eq-run-1": 2})
        self.assertEqual(facets["score"], {"min": 53, "max": 75, "avg": 64.0})
        self.assertEqual(facets["total"], 2)
        self.assertFalse(facets["partial"])
        # شکل کلیدها دقیقاً همان است که SQLite می‌سازد
        self.assertEqual(set(facets), {"segment", "status", "niche", "contact", "runs",
                                       "score", "total", "counted", "partial"})

    def test_niche_group_is_translated_to_ids(self):
        self.transport.responder = lambda r, rec: FakeResponse({"total": 0, "leads": [], "facets": {}})
        pgstore.search_leads(niche_group="خودرو")
        params = self.transport.last["body"]["params"]
        self.assertIn("niche_ids", params)
        self.assertTrue(len(params["niche_ids"]) >= 1)
        self.assertNotIn("niche_group", params)

    def test_set_lead_status_rejects_unknown_status_without_http(self):
        self.assertFalse(pgstore.set_lead_status("x", "not-a-status"))
        self.assertEqual(self.transport.calls, [])

    def test_queue_functions_call_the_right_rpc(self):
        self.transport.responder = lambda r, rec: FakeResponse({"id": "job-1"})
        pgstore.enqueue_job("run-9", {"niche_query": "کافه"}, priority=2)
        self.assertTrue(self.transport.last["url"].endswith("/rpc/enqueue_job"))
        self.assertEqual(self.transport.last["body"],
                         {"p_run_id": "run-9", "p_request": {"niche_query": "کافه"},
                          "p_priority": 2})
        pgstore.claim_job("w-1", 3)
        self.assertTrue(self.transport.last["url"].endswith("/rpc/claim_job"))
        pgstore.heartbeat_job("job-1", {"percent": 40}, "running")
        self.assertEqual(self.transport.last["body"],
                         {"p_id": "job-1", "p_progress": {"percent": 40}, "p_status": "running"})
        pgstore.finish_job("job-1", "failed", "boom")
        self.assertEqual(self.transport.last["body"],
                         {"p_id": "job-1", "p_status": "failed", "p_error": "boom"})
        pgstore.requeue_stale_jobs(600)
        self.assertEqual(self.transport.last["body"], {"p_seconds": 600.0})
        pgstore.queue_stats()
        self.assertTrue(self.transport.last["url"].endswith("/rpc/queue_stats"))
        pgstore.log_delivery("digest", "email", "a@b.c", "موضوع", "sent", {"shown": 3})
        self.assertTrue(self.transport.last["url"].endswith("/rpc/log_delivery"))

    def test_delete_run_and_stats(self):
        self.transport.responder = lambda r, rec: FakeResponse(True)
        self.assertTrue(pgstore.delete_run("run-9"))
        self.assertTrue(self.transport.last["url"].endswith("/rpc/delete_run"))
        self.transport.responder = lambda r, rec: FakeResponse(
            {"runs": 2, "leads": 30, "contacted": 4, "won": 1, "with_email": 12})
        stats = pgstore.pipeline_stats()
        self.assertEqual(stats["leads"], 30)
        self.assertEqual(stats["with_email"], 12)


class StorageDispatchTests(unittest.TestCase):
    """`storage` باید بدون تغییر در کد فراخوان، به پشتیبان ابری برود."""

    def setUp(self):
        self.client, self.transport = make_client()
        cloud._CLIENT = self.client
        self._backend = config.DB_BACKEND
        config.DB_BACKEND = "supabase"

    def tearDown(self):
        config.DB_BACKEND = self._backend
        cloud._CLIENT = None

    def test_search_leads_routes_to_rpc(self):
        self.transport.responder = lambda r, rec: FakeResponse(
            {"total": 1, "leads": [{"id": "a", "title": "کافه", "score": 90, "segment": "hot",
                                    "status": "new", "gaps": [], "tags": [], "signals": {},
                                    "latitude": "35.7", "longitude": "51.4"}],
             "facets": {"segment": {"hot": 1}, "total": 1}})
        result = storage.search_leads(q="کافه", limit=10)
        self.assertTrue(self.transport.last["url"].endswith("/rpc/search_leads"))
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["leads"][0]["title"], "کافه")

    def test_get_run_normalizes_jsonb_and_labels(self):
        self.transport.responder = lambda r, rec: FakeResponse([
            {"id": "run-9", "keywords": ["کافه در تهران"], "job_ids": [], "progress": {"percent": 100},
             "analytics": {"avg_score": 80}, "status": "queued", "live": True, "socials": True,
             "emails": False, "created_at": 1700000000}])
        run = storage.get_run("run-9")
        self.assertEqual(run["keywords"], ["کافه در تهران"])
        self.assertEqual(run["progress"], {"percent": 100})
        self.assertEqual(run["analytics"], {"avg_score": 80})
        self.assertIs(run["live"], True)
        self.assertEqual(run["status_fa"], "در صف")

    def test_sqlite_backend_is_untouched(self):
        config.DB_BACKEND = "sqlite"
        self.transport.responder = lambda r, rec: FakeResponse([])
        storage.init_db()
        run_id = storage.create_run({"id": "local-1", "city": "تهران", "niche_fa": "کافه",
                                     "status": "done"})
        self.assertEqual(run_id, "local-1")
        self.assertEqual(self.transport.calls, [])               # هیچ درخواست ابری نبود
        self.assertEqual(storage.get_run("local-1")["city"], "تهران")


if __name__ == "__main__":
    unittest.main()
