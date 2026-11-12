"""تست «اجرای زنده»: صف بدون ورکر بیرونی + کد دسترسی + سهمیه.

این تست‌ها همان مسیری را می‌زنند که روی Vercel اجرا می‌شود:
    POST /api/runs → queued → GET progress (action=tick) → POST /api/worker/tick → done

سرور روی یک رشتهٔ پس‌زمینه بالا می‌آید و موتور دمو است (بدون شبکه/داکر).
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
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = Path(tempfile.mkdtemp(prefix="leadgen-live-"))
os.environ.setdefault("LEADGEN_DATA_DIR", str(_TMP))

from leadgen import config, jobs, server as server_mod  # noqa: E402


def _free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LiveQueueTest(unittest.TestCase):
    httpd = None
    thread = None
    base = ""
    saved: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.saved = {k: getattr(config, k) for k in
                     ("QUEUE_MODE", "ACCESS_CODE", "MAX_PENDING_JOBS", "RATE_RUNS_PER_HOUR",
                      "SELF_DRIVE", "ENGINE_MODE")}
        config.QUEUE_MODE = "always"        # مثل Vercel
        config.ENGINE_MODE = "demo"
        config.ACCESS_CODE = ""
        config.MAX_PENDING_JOBS = 0
        config.RATE_RUNS_PER_HOUR = 0
        config.SELF_DRIVE = True
        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.httpd = server_mod.make_server("127.0.0.1", cls.port)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        for _ in range(80):
            try:
                cls.request("/api/health")
                return
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("سرور تست بالا نیامد")

    @classmethod
    def tearDownClass(cls):
        if cls.httpd:
            cls.httpd.shutdown()
            cls.httpd.server_close()
        for key, value in cls.saved.items():
            setattr(config, key, value)

    @classmethod
    def request(cls, path: str, body: dict | None = None, headers: dict | None = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(cls.base + path, data=data,
                                     method="POST" if body is not None else "GET",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def setUp(self):
        config.ACCESS_CODE = ""
        config.MAX_PENDING_JOBS = 0
        config.RATE_RUNS_PER_HOUR = 0
        server_mod._RUN_HITS.clear()
        self.drain()

    def drain(self, rounds: int = 6) -> None:
        """صف را خالی کن تا هر تست روی کار خودش تمرکز کند."""
        for _ in range(rounds):
            _, result = self.request("/api/worker/tick?budget=30", {})
            if not result.get("processed"):
                return

    # ── صف و اجرای خودکار ────────────────────────────────────────────────────
    def test_01_demo_run_goes_to_queue_and_hints_tick(self):
        code, run = self.request("/api/runs", {"niche_query": "کافه", "city_query": "تهران",
                                               "engine_mode": "demo", "depth": 2, "limit": 12})
        self.assertEqual(202, code)
        self.assertEqual("queued", run["status"])
        self.assertTrue(jobs.queue_mode_active())

        code, progress = self.request(f"/api/runs/{run['run_id']}/progress")
        self.assertEqual(200, code)
        self.assertEqual("tick", progress["progress"].get("action"))

        code, worker = self.request("/api/worker")
        self.assertEqual(200, code)
        self.assertTrue(worker["self_drive"])
        self.assertFalse(worker["worker_online"])

    def test_02_tick_executes_the_queued_run(self):
        self.drain()
        _, run = self.request("/api/runs", {"niche_query": "نانوایی", "city_query": "تهران",
                                            "engine_mode": "demo", "depth": 2, "limit": 10})
        run_id = run["run_id"]
        code, result = self.request("/api/worker/tick?budget=40", {})
        self.assertEqual(200, code)
        self.assertEqual(1, result["processed"])
        self.assertTrue(result["ok"], result.get("error"))
        self.assertGreater(result["total"], 0)
        self.assertEqual(run_id, result["run_id"])

        code, detail = self.request(f"/api/runs/{run_id}")
        self.assertEqual(200, code)
        self.assertEqual("done", detail["run"]["status"])
        self.assertGreater(detail["run"].get("total") or 0, 0)

    def test_03_tick_is_idle_when_queue_is_empty(self):
        self.drain()
        code, result = self.request("/api/worker/tick", {})
        self.assertEqual(200, code)
        self.assertEqual(0, result["processed"])
        self.assertTrue(result["idle"])

    # ── محافظ‌ها ─────────────────────────────────────────────────────────────
    def test_04_access_code_locks_data_routes(self):
        config.ACCESS_CODE = "secret-123"
        code, payload = self.request("/api/search")
        self.assertEqual(401, code)
        self.assertIn("کد دسترسی", payload["error"])

        code, _ = self.request("/api/search", headers={"X-Leadgen-Code": "secret-123"})
        self.assertEqual(200, code)
        code, _ = self.request("/api/search?code=secret-123")
        self.assertEqual(200, code)
        code, _ = self.request("/api/search", headers={"X-Leadgen-Code": "wrong"})
        self.assertEqual(401, code)

        # health عمومی می‌ماند تا رابط بفهمد قفل است
        code, health = self.request("/api/health")
        self.assertEqual(200, code)
        self.assertTrue(health["config"]["access_code_set"])

        code, _ = self.request("/api/runs", {"niche_query": "کافه", "city_query": "تهران"})
        self.assertEqual(401, code)

    def test_05_rate_limit_returns_429(self):
        config.RATE_RUNS_PER_HOUR = 1
        server_mod._RUN_HITS.clear()
        first, _ = self.request("/api/runs", {"niche_query": "کافه", "city_query": "تهران",
                                              "engine_mode": "demo", "limit": 5})
        second, payload = self.request("/api/runs", {"niche_query": "کافه", "city_query": "تهران",
                                                     "engine_mode": "demo", "limit": 5})
        self.assertIn(first, (202, 429))
        self.assertEqual(429, second)
        self.assertIn("سقف", payload["error"])

    def test_06_pending_cap_returns_429(self):
        config.RATE_RUNS_PER_HOUR = 0
        config.MAX_PENDING_JOBS = 1
        server_mod._RUN_HITS.clear()
        first, _ = self.request("/api/runs", {"niche_query": "کافه", "city_query": "تهران",
                                              "engine_mode": "demo", "limit": 5})
        self.assertEqual(202, first)
        second, payload = self.request("/api/runs", {"niche_query": "کافه", "city_query": "تهران",
                                                     "engine_mode": "demo", "limit": 5})
        self.assertEqual(429, second)
        self.assertIn("صف", payload["error"])
        config.MAX_PENDING_JOBS = 0
        self.drain()


if __name__ == "__main__":
    unittest.main()
