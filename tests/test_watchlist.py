"""آزمون‌های «دیدبان شهر» — جریان هفتگی «فقط تازه‌ها».

اجرا:  python3 -m unittest tests.test_watchlist -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = Path(tempfile.mkdtemp(prefix="leadgen-watch-"))
os.environ.setdefault("LEADGEN_DATA_DIR", str(_TMP))

from leadgen import config, storage, watchlist  # noqa: E402


class WatchStoreTests(unittest.TestCase):
    def setUp(self):
        storage.init_db()

    def tearDown(self):
        for item in storage.list_watch(limit=500):
            storage.delete_watch(item["id"])

    def test_create_validates_and_normalizes(self):
        with self.assertRaises(ValueError):
            watchlist.create({"niche_query": "کافه"})          # نه شهر نه استان
        with self.assertRaises(ValueError):
            watchlist.create({"city": "یزد"})                   # نه صنف نه همه‌کسب‌وکارها
        item = watchlist.create({"city": "یزد", "province": "yazd-province",
                                 "niche_query": "کافه", "interval_days": -3})
        self.assertTrue(item["id"].startswith("watch-"))
        self.assertEqual(item["interval_days"], 1)             # عدد منفی → کمینه
        self.assertTrue(item["active"])
        self.assertEqual(watchlist.label_for(item), "کافه · یزد")
        again = storage.get_watch(item["id"])
        self.assertEqual(again["city"], "یزد")
        self.assertTrue(again["active"])
        self.assertGreater(again["next_run_at"], 0)

    def test_all_niches_has_no_keyword(self):
        item = watchlist.create({"city": "شیراز", "all_niches": True,
                                 "niche_query": "کافه"})
        self.assertTrue(item["all_niches"])
        self.assertEqual(item["niche_query"], "")
        self.assertEqual(watchlist.label_for(item), "همهٔ کسب‌وکارها · شیراز")

    def test_update_and_delete(self):
        item = storage.add_watch({"city": "قم", "niche_query": "نانوایی"})
        updated = watchlist.update(item["id"], {"interval_days": 14, "active": False,
                                                "city": "کاشان"})
        self.assertEqual(updated["interval_days"], 14)
        self.assertFalse(updated["active"])
        self.assertEqual(updated["city"], "کاشان")
        self.assertTrue(watchlist.delete(item["id"]))
        self.assertIsNone(storage.get_watch(item["id"]))

    def test_due_and_next_run(self):
        now = time.time()
        old = storage.add_watch({"city": "یزد", "niche_query": "کافه",
                                 "next_run_at": now - 10, "interval_days": 7})
        future = storage.add_watch({"city": "یزد", "niche_query": "رستوران",
                                    "next_run_at": now + 3600})
        items = watchlist.due(now)
        ids = {w["id"] for w in items}
        self.assertIn(old["id"], ids)
        self.assertNotIn(future["id"], ids)
        self.assertAlmostEqual(watchlist.next_run_after(old, now) - now, 7 * 86400, places=1)

    def test_inactive_never_due(self):
        item = storage.add_watch({"city": "یزد", "niche_query": "کافه",
                                  "next_run_at": time.time() - 5, "active": False})
        self.assertFalse(watchlist.is_due(storage.get_watch(item["id"])))

    def test_overview_counts(self):
        storage.add_watch({"city": "یزد", "niche_query": "کافه",
                           "next_run_at": time.time() - 5})
        storage.add_watch({"city": "قم", "niche_query": "نانوایی",
                           "next_run_at": time.time() + 3600})
        out = watchlist.overview()
        self.assertGreaterEqual(out["total"], 2)
        self.assertGreaterEqual(out["active"], 2)
        self.assertGreaterEqual(out["due_now"], 1)
        self.assertTrue(all("label" in w for w in out["items"]))


class WatchRequestTests(unittest.TestCase):
    def test_city_request_shape(self):
        request = watchlist.build_request({"id": "w1", "city": "یزد", "city_key": "yazd",
                                           "province": "yazd-province",
                                           "niche_query": "کافه"})
        self.assertEqual(request["city_query"], "یزد")
        self.assertEqual(request["city_key"], "yazd")
        self.assertEqual(request["area_level"], "city")
        self.assertTrue(request["scan_full"])
        self.assertEqual(request["watch_id"], "w1")
        self.assertFalse(request["all_niches"])

    def test_province_request_uses_province_level(self):
        request = watchlist.build_request({"id": "w2", "city": "", "province": "yazd-province",
                                           "all_niches": True})
        self.assertEqual(request["area_level"], "province")
        self.assertTrue(request["all_niches"])
        self.assertEqual(request["city_query"], "yazd-province")


class WatchTickTests(unittest.TestCase):
    def setUp(self):
        storage.init_db()
        self._email = config.DIGEST_TO
        config.DIGEST_TO = "owner@example.com"

    def tearDown(self):
        config.DIGEST_TO = self._email
        for item in storage.list_watch(limit=500):
            storage.delete_watch(item["id"])

    def test_tick_runs_only_due_items_and_moves_next_run(self):
        now = time.time()
        due_item = storage.add_watch({"city": "یزد", "niche_query": "کافه",
                                      "next_run_at": now - 60})
        later = storage.add_watch({"city": "یزد", "niche_query": "رستوران",
                                   "next_run_at": now + 86400})
        seen = []

        class Result:
            run_id = "run-inline"

        def runner(request):
            seen.append(request)
            return Result()

        out = watchlist.tick(now=now, runner=runner)
        self.assertEqual(out["due"], 1)
        self.assertEqual([r["watch"] for r in out["queued"]], [due_item["id"]])
        self.assertEqual(seen[0]["city_query"], "یزد")
        self.assertTrue(seen[0]["scan_full"])
        moved = storage.get_watch(due_item["id"])
        self.assertGreater(moved["next_run_at"], now + 6 * 86400)
        self.assertEqual(moved["last_run_id"], "run-inline")
        self.assertGreater(storage.get_watch(later["id"])["next_run_at"], now + 86000)

    def test_tick_survives_one_broken_item(self):
        now = time.time()
        storage.add_watch({"city": "یزد", "niche_query": "کافه", "next_run_at": now - 5})
        storage.add_watch({"city": "یزد", "niche_query": "نانوایی", "next_run_at": now - 4})

        def runner(request):
            if request["niche_query"] == "کافه":
                raise RuntimeError("boom")

            class Result:
                run_id = "run-ok"
            return Result()

        out = watchlist.tick(now=now, runner=runner, limit=5)
        statuses = [("error" in r) for r in out["queued"]]
        self.assertIn(True, statuses)
        self.assertIn(False, statuses)


class OnlyNewTests(unittest.TestCase):
    def setUp(self):
        storage.init_db()

    def tearDown(self):
        for run_id in ("run-old", "run-new", "run-old2", "run-new2"):
            storage.delete_run(run_id)

    def _run(self, run_id: str, place_ids: list[str]) -> None:
        storage.create_run({"id": run_id, "created_at": time.time(), "status": "done",
                            "city": "یزد", "niche_fa": "کافه"})
        rows = []
        for pid in place_ids:
            rows.append({
                "place_id": pid, "title": f"کافه {pid}", "segment": "hot",
                "score": 80, "city": "یزد", "status": "new",
                "latitude": "31.89", "longitude": "54.36",
                "signals": {}, "gaps": [], "tags": [],
            })
        storage.save_leads(run_id, rows)

    def test_only_new_leads_skips_places_seen_in_other_runs(self):
        self._run("run-old", ["osm:node/1", "osm:node/2"])
        self._run("run-new", ["osm:node/2", "osm:node/3"])
        fresh = watchlist.only_new_leads("run-new")
        self.assertEqual([r["place_id"] for r in fresh], ["osm:node/3"])

    def test_deliver_sends_only_new_in_dry_run(self):
        self._run("run-old2", ["osm:node/10"])
        self._run("run-new2", ["osm:node/10", "osm:node/11"])
        item = storage.add_watch({"city": "یزد", "niche_query": "کافه",
                                  "next_run_at": time.time(), "note": "boss@example.com"})
        result = watchlist.deliver("run-new2", item["id"], dry_run=True)
        self.assertEqual(result["scope_total"], 1)
        self.assertEqual(result["shown"], 1)
        self.assertEqual(result["kind"], "watch")
        self.assertIn(result["status"], ("dry", "dry_run", "sent", "skipped"))
        self.assertIn("دیدبان", result["subject"])
        self.assertEqual(storage.get_watch(item["id"])["last_new"], 1)

    def test_maybe_deliver_defers_until_scan_is_complete(self):
        self._run("run-old2", ["osm:node/20"])
        item = storage.add_watch({"city": "یزد", "niche_query": "کافه"})
        request = {"watch_id": item["id"]}
        deferred = watchlist.maybe_deliver(
            "run-old2", request, {"scan": {"remaining": ["یزد · کاشی ۲/۹"]}})
        self.assertEqual(deferred["status"], "deferred")
        # بدون بخش باقی‌مانده، ارسال انجام می‌شود (نتیجهٔ dry_run یا empty)
        done = watchlist.maybe_deliver("run-old2", request, {"scan": {"remaining": []}})
        self.assertIn(done["status"], ("dry", "dry_run", "sent", "empty", "skipped"))
        self.assertIsNone(watchlist.maybe_deliver("run-old2", {}, {}))


if __name__ == "__main__":
    unittest.main()
