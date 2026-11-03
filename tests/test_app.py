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
        db.upsert(c, "daily", "date", {"date": YESTERDAY, "steps": 1000, "sleep_s": 25000})
        db.upsert(c, "daily", "date", {"date": TODAY.isoformat(), "steps": 50})
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
        rows = c.execute("SELECT date, steps FROM daily").fetchall()
        self.assertEqual([tuple(r) for r in rows], [(YESTERDAY, 123)])


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

    def test_activity_names_never_leave(self):
        self.seed()
        export = build_export(28)
        self.assertNotIn(PRIVATE_NAME, json.dumps(export))
        self.assertNotIn("name", export["workouts"][0])


if __name__ == "__main__":
    unittest.main()
