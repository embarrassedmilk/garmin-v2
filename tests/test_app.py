"""Smoke and export tests. Uses a temporary database and never calls Garmin.

Run: uv run python -m unittest
"""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend import db, sync
from backend.export import build_export
from backend.main import app

TODAY = date.today()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
PRIVATE_NAME = "Morning run past Private Street"


class TempDbCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(db, "DB_PATH", Path(tmp.name) / "test.db")
        patcher.start()
        self.addCleanup(patcher.stop)

    def seed(self):
        c = db.connect()
        db.upsert(c, "daily", "date", {
            "date": YESTERDAY, "steps": 1000, "sleep_s": 25000, "summary_fetched_on": TODAY.isoformat(),
        })
        db.upsert(c, "daily", "date", {"date": TODAY.isoformat(), "steps": 50, "summary_fetched_on": TODAY.isoformat()})
        db.upsert(c, "activities", "id", {
            "id": 1, "start_local": f"{YESTERDAY} 07:00:00", "type": "running",
            "name": PRIVATE_NAME, "duration_s": 1800, "training_load": 40,
        })
        c.commit()
        c.close()


class SmokeTest(TempDbCase):
    def test_app_serves_api_and_frontend(self):
        self.seed()
        client = TestClient(app)
        self.assertEqual(len(client.get("/api/daily").json()), 2)
        self.assertEqual(len(client.get("/api/activities").json()), 1)
        self.assertEqual(client.get("/api/status").status_code, 200)
        self.assertEqual(client.get("/api/export").status_code, 200)
        self.assertIn("Garmin dashboard", client.get("/").text)


class SchemaTest(TempDbCase):
    def test_daily_rejects_null_date(self):
        c = db.connect()
        self.addCleanup(c.close)
        with self.assertRaises(sqlite3.IntegrityError):
            db.upsert(c, "daily", "date", {"date": None, "steps": 1})


class SyncTest(TempDbCase):
    def test_null_date_is_skipped_and_the_rest_are_stored(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.get_daily_summaries.return_value = {
            "data": [{"calendarDate": None}, {"calendarDate": YESTERDAY, "movement": {"steps": {"value": 123}}}],
        }
        client.get_daily_sleep_summaries.return_value = []
        client.get_daily_hrv_summaries.return_value = {}
        client.search_activities.return_value = []

        with patch.object(sync, "PersonalApiAuthenticator", MagicMock()), \
                patch.object(sync, "GarminPersonalApiClient", return_value=client), \
                patch.dict(os.environ, {"GARMIN_API_KEY": "not-a-real-key"}):
            result = sync.run_sync(days=1)

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["counts"]["daily_summaries"], 1)
        c = db.connect()
        self.addCleanup(c.close)
        rows = c.execute("SELECT date, steps, summary_fetched_on FROM daily").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(YESTERDAY, 123, TODAY.isoformat())])

    def test_failing_old_window_does_not_block_newer_data(self):
        def summaries(start, end):
            if end < TODAY:
                raise RuntimeError("old window failed")
            return {"data": [{"calendarDate": YESTERDAY}]}

        client = MagicMock()
        client.__enter__.return_value = client
        client.get_daily_summaries.side_effect = summaries
        client.get_daily_sleep_summaries.return_value = []
        client.get_daily_hrv_summaries.return_value = {}
        client.search_activities.return_value = []

        with patch.object(sync, "PersonalApiAuthenticator", MagicMock()), \
                patch.object(sync, "GarminPersonalApiClient", return_value=client), \
                patch.dict(os.environ, {"GARMIN_API_KEY": "not-a-real-key"}):
            result = sync.run_sync(days=60)

        first_call_end = client.get_daily_summaries.call_args_list[0].args[1]
        self.assertEqual(first_call_end, TODAY)
        self.assertEqual(result["counts"]["daily_summaries"], 1)
        self.assertEqual(len(result["errors"]), 2)  # 60 days is 3 windows; both older ones failed
        self.assertIn("daily_summaries", result["errors"][0])

    def meta(self, key):
        c = db.connect()
        self.addCleanup(c.close)
        return db.get_meta(c, key)

    def test_failed_login_is_recorded_and_raised(self):
        auth = MagicMock()
        auth.return_value.__enter__.return_value.obtain_token.side_effect = RuntimeError("key expired")
        with patch.object(sync, "PersonalApiAuthenticator", auth), \
                patch.dict(os.environ, {"GARMIN_API_KEY": "not-a-real-key"}):
            with self.assertRaises(RuntimeError):
                sync.run_sync(days=1)

        status = TestClient(app).get("/api/status").json()
        self.assertIn("key expired", status["errors"][0])
        self.assertIsNone(status["last_ok_at"])

    def test_missing_key_is_recorded_and_raised(self):
        with patch.object(sync.os, "getenv", return_value=None):
            with self.assertRaises(RuntimeError):
                sync.run_sync(days=1)
        self.assertIn("GARMIN_API_KEY", self.meta("last_sync"))

    def test_only_a_run_that_stores_data_counts_as_success(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.get_daily_summaries.side_effect = RuntimeError("Garmin down")
        client.get_daily_sleep_summaries.side_effect = RuntimeError("Garmin down")
        client.get_daily_hrv_summaries.side_effect = RuntimeError("Garmin down")
        client.search_activities.side_effect = RuntimeError("Garmin down")

        with patch.object(sync, "PersonalApiAuthenticator", MagicMock()), \
                patch.object(sync, "GarminPersonalApiClient", return_value=client), \
                patch.dict(os.environ, {"GARMIN_API_KEY": "not-a-real-key"}):
            sync.run_sync(days=1)
            self.assertIsNone(self.meta("last_ok_at"))

            client.get_daily_summaries.side_effect = None
            client.get_daily_summaries.return_value = {"data": [{"calendarDate": YESTERDAY}]}
            sync.run_sync(days=1)
            self.assertIsNotNone(self.meta("last_ok_at"))


class ExportTest(TempDbCase):
    def test_workouts_are_included(self):
        self.seed()
        export = build_export(28)
        self.assertEqual(len(export["workouts"]), 1)
        self.assertEqual(export["stats"]["workouts_last7"]["count"], 1)
        self.assertEqual(export["range"]["from"], YESTERDAY)

    def test_today_is_partial_and_excluded_from_stats(self):
        self.seed()
        export = build_export(28)
        partial = {d["date"]: d["partial"] for d in export["daily"]}
        self.assertEqual(partial, {YESTERDAY: False, TODAY.isoformat(): True})
        self.assertEqual(export["stats"]["steps"]["n"], 1)
        self.assertEqual(export["stats"]["steps"]["mean"], 1000)

    def test_day_fetched_before_it_ended_stays_partial(self):
        c = db.connect()
        db.upsert(c, "daily", "date", {"date": YESTERDAY, "steps": 300, "summary_fetched_on": YESTERDAY})
        c.commit()
        c.close()
        export = build_export(28)
        self.assertTrue(export["daily"][0]["partial"])
        self.assertEqual(export["stats"]["steps"]["n"], 0)

    def test_last_nights_sleep_and_hrv_count_but_todays_steps_do_not(self):
        c = db.connect()
        db.upsert(c, "daily", "date", {"date": YESTERDAY, "steps": 1000, "sleep_s": 3600, "hrv_last_night": 40,
                                       "summary_fetched_on": TODAY.isoformat()})
        db.upsert(c, "daily", "date", {"date": TODAY.isoformat(), "steps": 50, "sleep_s": 7200, "hrv_last_night": 60,
                                       "summary_fetched_on": TODAY.isoformat()})
        c.commit()
        c.close()
        stats = build_export(28)["stats"]
        self.assertEqual(stats["sleep_hours"]["n"], 2)
        self.assertEqual(stats["sleep_hours"]["last7_mean"], 1.5)
        self.assertEqual(stats["hrv_last_night_ms"]["last7_mean"], 50)
        self.assertEqual(stats["steps"]["n"], 1)
        self.assertEqual(stats["steps"]["last7_mean"], 1000)

    def test_activity_names_never_leave(self):
        self.seed()
        export = build_export(28)
        self.assertNotIn(PRIVATE_NAME, json.dumps(export))
        self.assertNotIn("name", export["workouts"][0])


if __name__ == "__main__":
    unittest.main()
