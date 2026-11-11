"""آزمون پراکسی احراز هویت موتور (`scripts/engine_proxy.py`).

چرا؟ موتور `gosom/google-maps-scraper` هیچ احراز هویتی ندارد و در Codespaces باید از بیرون
قابل صدا زدن باشد. این پراکسی لایهٔ `X-API-Key` است؛ اگر خراب شود یا کلید را نادیده بگیرد،
یا موتور بی‌دفاع می‌ماند یا ورکر ابری کار نمی‌کند.

اینجا یک «موتور ساختگی» (همان مسیرهای واقعی: `/api/v1/jobs` و `/api/v1/jobs/{id}`) بالا
می‌آید و پراکسی جلوی آن می‌نشیند؛ هر دو روی پورت تصادفی و در رشتهٔ پس‌زمینه.

اجرا:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location("engine_proxy", ROOT / "scripts" / "engine_proxy.py")
proxy = importlib.util.module_from_spec(SPEC)
sys.modules["engine_proxy"] = proxy
SPEC.loader.exec_module(proxy)

API_KEY = "test-key-1234"
JOBS = [{"id": "job-1", "Status": "ok", "keywords": ["دندانپزشکی در تهران"]}]


class FakeEngine(BaseHTTPRequestHandler):
    """موتور ساختگی با همان شکل API کیت."""

    protocol_version = "HTTP/1.1"
    seen: list[dict] = []

    def log_message(self, *_a) -> None:
        pass

    def _send(self, status: int, payload, content_type: str = "application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Engine", "fake")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        length = int(self.headers.get("Content-Length") or 0)
        FakeEngine.seen.append({"method": "GET", "path": self.path, "body": None,
                                "headers": dict(self.headers)})
        if self.path == "/api/v1/jobs":
            self._send(200, JOBS)
        elif self.path.endswith("/download"):        # پیش از وضعیت job بررسی می‌شود
            self._send(200, "title,phone\nکلینیک,+9821000\n".encode("utf-8"), "text/csv")
        elif self.path.startswith("/api/v1/jobs/"):
            self._send(200, {"id": self.path.rsplit("/", 1)[-1], "Status": "working"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        body = self.rfile.read(length) if (length := int(self.headers.get("Content-Length") or 0)) else b""
        FakeEngine.seen.append({"method": "POST", "path": self.path, "body": body.decode("utf-8"),
                                "headers": dict(self.headers)})
        if self.path == "/api/v1/jobs":
            self._send(201, {"id": "job-new"})
        else:
            self._send(422, {"error": "keywords[] required"})

    def do_DELETE(self):
        FakeEngine.seen.append({"method": "DELETE", "path": self.path, "body": None,
                                "headers": dict(self.headers)})
        self._send(204, b"")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Server:
    def __init__(self, handler) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class EngineProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = _Server(FakeEngine)
        cls.proxy = _Server(proxy.make_handler(cls.engine.url, API_KEY))

    @classmethod
    def tearDownClass(cls):
        cls.proxy.close()
        cls.engine.close()

    def setUp(self):
        FakeEngine.seen.clear()

    def request(self, path, *, key=API_KEY, method="GET", body=None, bearer=False,
                base=None, raw=False):
        url = (base or self.proxy.url) + path
        headers = {}
        if key and not bearer:
            headers["X-API-Key"] = key
        if bearer:
            headers["Authorization"] = f"Bearer {key}"
        data = body.encode("utf-8") if isinstance(body, str) else body
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                payload = res.read()
                return res.status, payload, dict(res.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers or {})

    # ── احراز هویت ───────────────────────────────────────────────────────────
    def test_missing_key_is_rejected_and_never_reaches_engine(self):
        status, payload, _ = self.request("/api/v1/jobs", key=None)
        self.assertEqual(401, status)
        self.assertEqual("unauthorized", json.loads(payload)["error"])
        self.assertEqual([], FakeEngine.seen, "درخواست بی‌کلید نباید به موتور برسد")

    def test_wrong_key_is_rejected(self):
        status, payload, _ = self.request("/api/v1/jobs", key="wrong")
        self.assertEqual(401, status)
        self.assertIn("X-API-Key", json.loads(payload)["hint"])
        self.assertEqual([], FakeEngine.seen)

    def test_correct_key_passes_through(self):
        status, payload, _ = self.request("/api/v1/jobs")
        self.assertEqual(200, status)
        self.assertEqual(JOBS, json.loads(payload))

    def test_bearer_token_is_accepted(self):
        status, payload, _ = self.request("/api/v1/jobs", bearer=True)
        self.assertEqual(200, status)
        self.assertEqual(JOBS, json.loads(payload))

    def test_api_key_is_not_forwarded_to_engine(self):
        """کلید فقط برای پراکسی است؛ موتور نباید آن را ببیند (در logs نشت نکند)."""
        self.request("/api/v1/jobs")
        forwarded = FakeEngine.seen[-1]["headers"]
        self.assertNotIn("X-API-Key", {k for k in forwarded})
        self.assertNotIn(API_KEY, json.dumps(forwarded, ensure_ascii=False))

    def test_health_endpoint_needs_no_key(self):
        for path in ("/healthz", "/", "/_proxy"):
            with self.subTest(path=path):
                status, payload, _ = self.request(path, key=None)
                self.assertEqual(200, status)
                data = json.loads(payload)
                self.assertEqual("leadgen-engine-proxy", data["proxy"])
                self.assertTrue(data["auth"])
                self.assertIn("/api/v1/jobs", data["hint"])

    # ── هدایت درخواست‌ها ─────────────────────────────────────────────────────
    def test_post_job_is_forwarded_with_body(self):
        body = json.dumps({"keywords": ["دندانپزشکی در تهران"], "lat": "35.6892",
                           "lon": "51.3890", "depth": 5}, ensure_ascii=False)
        status, payload, _ = self.request("/api/v1/jobs", method="POST", body=body)
        self.assertEqual(201, status)
        self.assertEqual({"id": "job-new"}, json.loads(payload))
        seen = FakeEngine.seen[-1]
        self.assertEqual("POST", seen["method"])
        self.assertEqual(body, seen["body"])          # بدنه دست‌نخورده می‌رسد
        self.assertEqual("35.6892", json.loads(seen["body"])["lat"])   # lat/lon رشته‌ای

    def test_engine_validation_error_is_passed_through(self):
        status, payload, _ = self.request("/api/v1/other", method="POST", body="{}")
        self.assertEqual(422, status)
        self.assertEqual("keywords[] required", json.loads(payload)["error"])

    def test_job_status_and_csv_download_are_forwarded(self):
        status, payload, _ = self.request("/api/v1/jobs/job-1")
        self.assertEqual(200, status)
        self.assertEqual("working", json.loads(payload)["Status"])

        status, payload, headers = self.request("/api/v1/jobs/job-1/download")
        self.assertEqual(200, status)
        self.assertIn("text/csv", headers.get("Content-Type", ""))
        self.assertIn("کلینیک", payload.decode("utf-8"))
        self.assertEqual("fake", headers.get("X-Engine"))     # هدرهای موتور هم برمی‌گردد

    def test_delete_job_is_forwarded(self):
        status, _payload, _ = self.request("/api/v1/jobs/job-1", method="DELETE")
        self.assertEqual(204, status)
        self.assertEqual("DELETE", FakeEngine.seen[-1]["method"])

    def test_query_string_is_preserved(self):
        self.request("/api/v1/jobs?limit=5&offset=10")
        self.assertEqual("/api/v1/jobs?limit=5&offset=10", FakeEngine.seen[-1]["path"])

    # ── خطاها ────────────────────────────────────────────────────────────────
    def test_unreachable_engine_returns_persian_hint_not_crash(self):
        dead = _Server(proxy.make_handler("http://127.0.0.1:1", API_KEY))
        try:
            status, payload, _ = self.request("/api/v1/jobs", base=dead.url)
            self.assertEqual(502, status)
            data = json.loads(payload)
            self.assertEqual("upstream unreachable", data["error"])
            self.assertIn("docker compose up -d", data["hint"])
        finally:
            dead.close()

    def test_proxy_without_key_allows_all_but_reports_auth_off(self):
        open_proxy = _Server(proxy.make_handler(self.engine.url, ""))
        try:
            status, payload, _ = self.request("/healthz", key=None, base=open_proxy.url)
            self.assertEqual(200, status)
            self.assertFalse(json.loads(payload)["auth"])
            status, _payload, _ = self.request("/api/v1/jobs", key=None, base=open_proxy.url)
            self.assertEqual(200, status)          # مثل خود موتور: فقط localhost
        finally:
            open_proxy.close()

    # ── ابزارها ──────────────────────────────────────────────────────────────
    def test_generate_key_prints_a_usable_secret(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(0, proxy.main(["--generate-key"]))
        key = buf.getvalue().strip()
        self.assertGreaterEqual(len(key), 32)
        self.assertNotIn(" ", key)
        # همان کلید را می‌توان در پراکسی استفاده کرد
        gated = _Server(proxy.make_handler(self.engine.url, key))
        try:
            self.assertEqual(200, self.request("/api/v1/jobs", key=key, base=gated.url)[0])
            self.assertEqual(401, self.request("/api/v1/jobs", key="nope", base=gated.url)[0])
        finally:
            gated.close()

    def test_handler_strips_hop_by_hop_headers(self):
        self.assertIn("x-api-key", proxy.HOP_BY_HOP)
        self.assertIn("host", proxy.HOP_BY_HOP)
        self.assertIn("content-length", proxy.HOP_BY_HOP)
        self.assertEqual(32 * 1024 * 1024, proxy.MAX_BODY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
