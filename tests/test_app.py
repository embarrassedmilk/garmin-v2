"""Smoke and export tests. Uses a temporary database and never calls Garmin.

Run: uv run python -m unittest
"""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
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


def lap(dist=1000, time=360, hr=150, gain=1, loss=2, cadence=170, gap=2.8, trigger="distance"):
    return {"dist_m": dist, "time_s": time, "hr": hr, "gain_m": gain, "loss_m": loss,
            "cadence_spm": cadence, "gap_mps": gap, "trigger": trigger}


class RunExportTest(TempDbCase):
    BASE_WORKOUT_KEYS = {"date", "type", "duration_min", "distance_km", "avg_hr", "max_hr",
                         "aerobic_te", "anaerobic_te", "training_load"}

    def add(self, id=1, type="running", day=YESTERDAY, laps=None, **cols):
        c = db.connect()
        db.upsert(c, "daily", "date", {"date": day, "steps": 1, "summary_fetched_on": TODAY.isoformat()})
        details = {"run_details": json.dumps({"laps": laps, "hr_zone_bounds_bpm": [97, 116, 135, 154, 174, 193]})} if laps else {}
        db.upsert(c, "activities", "id", {"id": id, "start_local": f"{day} 07:00:00", "type": type,
                                          "duration_s": 3600, "distance_m": 10000, **details, **cols})
        c.commit()
        c.close()

    def workout(self):
        return build_export(28)["workouts"][0]

    def test_activities_api_adds_run_fields_only_to_runs(self):
        self.add(id=1, laps=[lap()] * 2, hr_z2_s=60)
        self.add(id=2, type="hiit", laps=[lap()] * 2, hr_z2_s=60)
        acts = {a["id"]: a for a in TestClient(app).get("/api/activities").json()}
        self.assertEqual(acts[1]["run"]["splits"][1]["km"], 2.0)
        self.assertEqual(acts[1]["run"]["hr_zone_minutes"]["z2"], 1.0)
        self.assertIsNone(acts[2]["run"])

    def test_other_workouts_keep_only_the_old_fields(self):
        self.add(type="hiit", laps=[lap()] * 10, elevation_gain_m=5, hr_z1_s=60)
        self.assertEqual(set(self.workout()), self.BASE_WORKOUT_KEYS)

    def test_subtypes(self):
        for i, (garmin_type, subtype) in enumerate(db.RUN_SUBTYPES.items()):
            self.add(id=i, type=garmin_type)
        self.assertEqual({w["activity_subtype"] for w in build_export(28)["workouts"]}, set(db.RUN_SUBTYPES.values()))

    def test_missing_fields_are_left_out_not_null(self):
        self.add()
        self.assertEqual(set(self.workout()), self.BASE_WORKOUT_KEYS | {"activity_subtype"})

    def test_run_fields_are_converted(self):
        self.add(laps=[lap()] * 10, elevation_gain_m=29.0, elevation_loss_m=30.0, avg_gap_mps=2.696,
                 avg_cadence_spm=171.17, avg_stride_cm=94.36, hr_z1_s=60, hr_z2_s=90, hr_z3_s=None, te_label="LACTATE_THRESHOLD", avg_vertical_osc_cm=8.28)
        w = self.workout()
        self.assertEqual(w["primary_benefit"], "lactate_threshold")
        self.assertEqual(w["avg_vertical_oscillation_cm"], 8.3)
        self.assertEqual((w["elevation_gain_m"], w["elevation_loss_m"]), (29, 30))
        self.assertEqual(w["avg_gap_min_per_km"], 6.18)
        self.assertEqual((w["avg_cadence_spm"], w["avg_stride_length_m"]), (171, 0.94))
        self.assertEqual(w["hr_zone_minutes"], {"z1": 1.0, "z2": 1.5, "z3": 0, "z4": 0, "z5": 0})
        self.assertEqual(w["hr_zone_bounds_bpm"], [97, 116, 135, 154, 174, 193])

    def test_km_splits_and_short_last_lap(self):
        self.add(laps=[lap(), lap(dist=1000, time=300, hr=160), lap(dist=18, time=11, trigger="session_end")])
        w = self.workout()
        self.assertEqual(w["split_type"], "km")
        self.assertEqual(w["splits"], [
            {"km": 1.0, "pace_min_per_km": 6.0, "avg_hr": 150, "elevation_gain_m": 1, "elevation_loss_m": 2, "cadence_spm": 170},
            {"km": 2.0, "pace_min_per_km": 5.0, "avg_hr": 160, "elevation_gain_m": 1, "elevation_loss_m": 2, "cadence_spm": 170},
        ])

    def test_single_lap_is_not_called_a_km_split(self):
        self.add(laps=[lap(dist=10000, time=3600, trigger="session_end")])
        self.assertEqual(self.workout()["split_type"], "lap")

    def test_manual_laps_are_labelled_laps(self):
        self.add(laps=[lap(), lap(dist=443, time=170, trigger="manual"), lap(trigger="session_end")])
        w = self.workout()
        self.assertEqual(w["split_type"], "lap")
        self.assertEqual([s["km"] for s in w["splits"]], [1.0, 1.44, 2.44])

    def test_drift_uses_grade_adjusted_pace(self):
        # Same grade-adjusted speed, HR up 10% in the second half: efficiency fell by 1/1.1.
        self.add(laps=[lap(hr=150)] * 5 + [lap(hr=165)] * 5)
        w = self.workout()
        self.assertEqual((w["hr_drift_pct"], w["hr_drift_basis"]), (10.0, "gap"))

    def test_drift_falls_back_to_pace_and_says_so(self):
        self.add(laps=[lap(hr=150, gap=None)] * 5 + [lap(hr=165, gap=None)] * 5)
        w = self.workout()
        self.assertEqual((w["hr_drift_pct"], w["hr_drift_basis"]), (10.0, "pace_not_grade_adjusted"))

    def test_lap_crossing_the_midpoint_is_shared(self):
        # 1080 s at 150, then 2160 s at 165. The first half (1620 s) averages 155 and the second is 165.
        self.add(laps=[lap(hr=150)] * 3 + [lap(dist=6000, time=2160, hr=165)])
        self.assertEqual(self.workout()["hr_drift_pct"], 6.5)

    def test_no_drift_under_40_minutes_or_without_enough_laps(self):
        self.add(laps=[lap()] * 10, duration_s=2399)
        self.assertNotIn("hr_drift_pct", self.workout())
        self.add(laps=[lap()] * 3, duration_s=2400)
        self.assertNotIn("hr_drift_pct", self.workout())
        self.add(laps=[lap()] * 4, duration_s=2400)
        self.assertEqual(self.workout()["hr_drift_pct"], 0)

    def test_run_stats(self):
        old = (TODAY - timedelta(days=12)).isoformat()
        for n in range(11, 0, -1):  # enough complete days for a last 7 and an earlier window
            self.add(id=100 + n, type="hiit", day=(TODAY - timedelta(days=n)).isoformat())
        self.add(id=1, day=old, distance_m=20000, duration_s=7200, elevation_gain_m=100)
        self.add(id=2, day=YESTERDAY, distance_m=10000, duration_s=3000, elevation_gain_m=20)
        self.add(id=3, day=TODAY.isoformat(), distance_m=99000, elevation_gain_m=999)  # partial day: not counted
        runs = build_export(28)["stats"]["runs"]
        self.assertEqual({k: runs[k] for k in ("count", "total_km", "total_min", "total_elevation_gain_m", "longest_km", "longest_min")},
                         {"count": 2, "total_km": 30.0, "total_min": 170, "total_elevation_gain_m": 120,
                          "longest_km": 20.0, "longest_min": 120})
        self.assertEqual((runs["last7"]["count"], runs["last7"]["total_km"]), (1, 10.0))
        self.assertEqual((runs["earlier"]["count"], runs["earlier"]["longest_km"]), (1, 20.0))

    def test_readiness_series_lists_only_days_with_a_value(self):
        earlier = (TODAY - timedelta(days=3)).isoformat()
        for id, day, details in ((1, earlier, {"vo2max": 50.04}), (2, YESTERDAY, {"laps": []})):
            self.add(id=id, day=day, run_details=json.dumps(details))
        self.add(id=3, type="hiit", day=YESTERDAY)
        self.assertEqual(build_export(28)["readiness"], [{"date": earlier, "vo2max_estimate": 50.0}])

    def test_no_runs_gives_empty_stats(self):
        self.add(type="hiit")
        self.assertEqual(build_export(28)["stats"]["runs"]["count"], 0)
        self.assertIsNone(build_export(28)["stats"]["runs"]["longest_km"])


FIT_MESSAGES = {
    "lap_mesgs": [{
        "total_distance": 1000.0, "total_timer_time": 364.1, "avg_heart_rate": 135, "total_ascent": 6, "total_descent": 3,
        "avg_running_cadence": 85, "avg_fractional_cadence": 0.5, "avg_grade_adjusted_speed": 2.862, "lap_trigger": "distance",
        "position_lat": 123, "position_long": 456,
    }],
    "time_in_zone_mesgs": [
        {"reference_mesg": "lap", "hr_zone_high_boundary": [1, 2, 3, 4, 5, 6]},
        {"reference_mesg": "session", "hr_zone_high_boundary": [97, 116, 135, 154, 174, 193]},
    ],
    "user_profile_mesgs": [{"friendly_name": PRIVATE_NAME}],
    "140": [{7: 965318, 9: 2304}],
}


class RunDetailsSyncTest(TempDbCase):
    def sync_once(self, client):
        client.__enter__.return_value = client
        client.get_daily_summaries.return_value = {}
        client.get_daily_sleep_summaries.return_value = []
        client.get_daily_hrv_summaries.return_value = {}
        with patch.object(sync, "PersonalApiAuthenticator", MagicMock()), \
                patch.object(sync, "GarminPersonalApiClient", return_value=client), \
                patch.object(sync, "read_fit", return_value=FIT_MESSAGES), \
                patch.dict(os.environ, {"GARMIN_API_KEY": "not-a-real-key"}):
            return sync.run_sync(days=2)

    @staticmethod
    def activity(id, type_key, **extra):
        return {"activityId": id, "startTimeLocal": f"{YESTERDAY} 07:00:00", "activityType": {"typeKey": type_key}, **extra}

    def test_only_keeps_laps_and_zones_from_the_file(self):
        details = sync.parse_run_details(FIT_MESSAGES)
        self.assertEqual(details["hr_zone_bounds_bpm"], [97, 116, 135, 154, 174, 193])
        self.assertEqual(details["laps"][0]["cadence_spm"], 171.0)
        self.assertNotIn("position_lat", details["laps"][0])
        self.assertNotIn(PRIVATE_NAME, json.dumps(details))

    def test_vo2max_comes_from_message_140(self):
        self.assertAlmostEqual(sync.parse_vo2max(FIT_MESSAGES), 51.55, places=2)

    def test_recovery_comes_from_message_140(self):
        self.assertEqual(sync.parse_run_details(FIT_MESSAGES)["recovery_min"], 2304)
        for messages in ({}, {"140": [{7: 1}]}, {"140": [{9: -1}]}, {"140": [{9: 65535}]}):
            self.assertIsNone(sync.parse_recovery_min(messages))

    def test_missing_or_implausible_vo2max_is_dropped(self):
        for messages in ({}, {"140": []}, {"140": [{9: 1}]}, {"140": [{7: 0}]}, {"140": [{7: 4294967295}]}):
            self.assertIsNone(sync.parse_vo2max(messages))

    def test_summary_fields_are_parsed(self):
        row = sync.parse_activity(self.activity(1, "running", elevationGain=29.0, avgGradeAdjustedSpeed=2.7,
                                                averageRunningCadenceInStepsPerMinute=171.2, avgStrideLength=94.4,
                                                hrTimeInZone_3=60.0, trainingEffectLabel="TEMPO",
                                                avgVerticalOscillation=8.28))
        self.assertEqual((row["elevation_gain_m"], row["avg_gap_mps"], row["avg_cadence_spm"], row["avg_stride_cm"], row["hr_z3_s"], row["te_label"],
                          row["avg_vertical_osc_cm"]), (29.0, 2.7, 171.2, 94.4, 60.0, "TEMPO", 8.28))

    def test_only_runs_are_downloaded_and_only_once(self):
        client = MagicMock()
        client.search_activities.return_value = [
            self.activity(1, "running"), self.activity(2, "hiit"), self.activity(3, "trail_running"),
            self.activity(4, "treadmill_running"), self.activity(5, "running", manualActivity=True),
        ]
        self.assertEqual(self.sync_once(client)["counts"]["run_details"], 3)
        self.assertEqual(sorted(call.args[0] for call in client.download_activity.call_args_list), [1, 3, 4])
        self.assertEqual(self.sync_once(client)["counts"]["run_details"], 0)
        self.assertEqual(client.download_activity.call_count, 3)

    def test_route_is_converted_and_thinned(self):
        half_circle = 2**31 // 2  # 90 degrees in semicircles
        records = [{"position_lat": half_circle, "position_long": -half_circle}, {"position_lat": None, "position_long": None}, {}]
        self.assertEqual(sync.parse_route({"record_mesgs": records}), [[90.0, -90.0]])
        self.assertEqual(sync.parse_route({}), [])
        many = [{"position_lat": i, "position_long": i} for i in range(6000)]
        route = sync.parse_route({"record_mesgs": many})
        self.assertLessEqual(len(route), sync.ROUTE_MAX_POINTS)
        self.assertEqual(route[0], [0.0, 0.0])
        self.assertEqual(route[-1], [round(5999 * sync.SEMICIRCLE_DEG, 5)] * 2)

    def test_route_is_stored_served_and_kept_out_of_activities_and_export(self):
        marker = 11.11111
        client = MagicMock()
        client.search_activities.return_value = [self.activity(1, "running")]
        with patch.object(sync, "parse_route", return_value=[[marker, marker], [marker, marker]]):
            self.sync_once(client)
        api = TestClient(app)
        self.assertEqual(api.get("/api/activities/1/route").json(), {"points": [[marker, marker], [marker, marker]]})
        self.assertEqual(api.get("/api/activities/999/route").json(), {"points": []})
        self.assertNotIn(str(marker), api.get("/api/activities").text)
        self.assertNotIn(str(marker), api.get("/api/export").text)

    def test_run_synced_before_routes_gets_one_more_download(self):
        c = db.connect()
        db.upsert(c, "activities", "id", {"id": 1, "start_local": f"{YESTERDAY} 07:00:00", "type": "running", "run_details": json.dumps({"laps": []})})
        db.upsert(c, "activities", "id", {"id": 2, "start_local": f"{YESTERDAY} 08:00:00", "type": "running", "run_details": "{}"})  # manual: no file
        c.commit()
        c.close()
        client = MagicMock()
        client.search_activities.return_value = []
        self.assertEqual(self.sync_once(client)["counts"]["run_details"], 1)
        client.download_activity.assert_called_once_with(1)
        self.assertEqual(self.sync_once(client)["counts"]["run_details"], 0)  # no GPS still counts as done

    def test_run_synced_before_recovery_gets_one_more_download(self):
        client = MagicMock()
        client.search_activities.return_value = [self.activity(1, "running")]
        with patch.object(sync, "read_fit", return_value={"140": [{}]}):
            self.sync_once(client)  # stores recovery_min as null: done, not retried
        c = db.connect()
        c.execute("UPDATE activities SET run_details = ? WHERE id = 1", (json.dumps({"laps": []}),))
        c.commit()
        c.close()
        self.assertEqual(self.sync_once(client)["counts"]["run_details"], 1)
        self.assertEqual(self.sync_once(client)["counts"]["run_details"], 0)
        self.assertEqual(client.download_activity.call_count, 2)

    def test_failed_download_is_recorded_and_retried(self):
        client = MagicMock()
        client.search_activities.return_value = [self.activity(1, "running")]
        client.download_activity.side_effect = RuntimeError("Garmin down")
        result = self.sync_once(client)
        self.assertEqual(result["counts"]["activities"], 1)
        self.assertIn("run_details", result["errors"][0])
        client.download_activity.side_effect = None
        self.assertEqual(self.sync_once(client)["counts"]["run_details"], 1)


def utc_ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


BODY_BATTERY_DAY = {
    "date": "2026-11-02", "startTimestampGMT": "2026-11-01T23:00:00.0", "startTimestampLocal": "2026-11-02T00:00:00.0",
    "bodyBatteryValuesArray": [
        [utc_ms("2026-11-01T23:00:00"), 40], [utc_ms("2026-11-02T00:00:00"), 50], [utc_ms("2026-11-02T00:00:00"), 50],
        [utc_ms("2026-11-02T00:15:00"), None], [utc_ms("2026-11-02T22:45:00"), 20],
    ],
    "bodyBatteryActivityEvent": [
        {"eventType": "SLEEP", "eventStartTimeGmt": "2026-11-01T22:00:00.0", "timezoneOffset": 3600000,
         "durationInMilliseconds": 8 * 3600000, "shortFeedback": PRIVATE_NAME},
        {"eventType": "ACTIVITY", "eventStartTimeGmt": "2026-11-02T17:00:00.0", "timezoneOffset": 3600000,
         "durationInMilliseconds": 30 * 60000},
        {"eventType": "SLEEP", "eventStartTimeGmt": "2026-11-02T22:00:00.0", "timezoneOffset": 3600000,
         "durationInMilliseconds": 8 * 3600000},
        {"eventType": "ACTIVITY", "eventStartTimeGmt": "2026-11-03T00:00:00.0", "timezoneOffset": 3600000,
         "durationInMilliseconds": 60000},
    ],
}


class StressDetailTest(TempDbCase):
    def test_stress_bands_are_stored_in_seconds(self):
        row = sync.parse_summary({"calendarDate": YESTERDAY, "stress": {
            "avgLevel": 30, "maxLevel": 88, "restDurationInMillis": 3600000, "lowStressDurationInMillis": 1800000,
            "mediumStressDurationInMillis": 60000, "highStressDurationInMillis": 0, "activityDurationInMillis": 120000,
            "uncategorizedDurationInMillis": 1000}})
        self.assertEqual((row["stress_avg"], row["stress_max"]), (30, 88))
        self.assertEqual((row["stress_rest_s"], row["stress_low_s"], row["stress_med_s"], row["stress_high_s"],
                          row["stress_activity_s"], row["stress_uncat_s"]), (3600, 1800, 60, 0, 120, 1))

    def test_missing_stress_stays_null(self):
        row = sync.parse_summary({"calendarDate": YESTERDAY})
        self.assertIsNone(row["stress_rest_s"])

    def test_curve_uses_local_minutes_and_drops_repeats_and_nulls(self):
        curve = json.loads(sync.parse_body_battery(BODY_BATTERY_DAY)["curve"])
        self.assertEqual(curve["points"], [[0, 40], [60, 50], [1425, 20]])

    def test_events_are_clipped_to_the_day_and_keep_no_feedback_text(self):
        curve = json.loads(sync.parse_body_battery(BODY_BATTERY_DAY)["curve"])
        self.assertEqual(curve["events"], [
            {"type": "SLEEP", "start_min": 0, "end_min": 420},
            {"type": "ACTIVITY", "start_min": 1080, "end_min": 1110},
            {"type": "SLEEP", "start_min": 1380, "end_min": 1440},
        ])
        self.assertNotIn(PRIVATE_NAME, json.dumps(curve))

    def test_day_without_timestamps_has_no_curve(self):
        self.assertIsNone(sync.parse_body_battery({"date": YESTERDAY})["curve"])

    def test_sync_stores_the_curve_and_api_serves_it(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.get_daily_summaries.return_value = {}
        client.get_daily_sleep_summaries.return_value = []
        client.get_daily_hrv_summaries.return_value = {}
        client.search_activities.return_value = []
        client.get_daily_body_battery.return_value = [BODY_BATTERY_DAY]
        with patch.object(sync, "PersonalApiAuthenticator", MagicMock()), \
                patch.object(sync, "GarminPersonalApiClient", return_value=client), \
                patch.dict(os.environ, {"GARMIN_API_KEY": "not-a-real-key"}):
            result = sync.run_sync(days=1)
        self.assertEqual((result["errors"], result["counts"]["body_battery"]), ([], 1))
        self.assertEqual(client.get_daily_body_battery.call_args.kwargs, {"chunks": 96})
        api = TestClient(app)
        self.assertEqual(api.get("/api/body-battery/2026-11-02").json()["points"][1], [60, 50])
        self.assertEqual(api.get("/api/body-battery/2020-01-01").json(), {"points": [], "events": []})

    def test_failing_body_battery_does_not_stop_the_sync(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.get_daily_summaries.return_value = {"data": [{"calendarDate": YESTERDAY}]}
        client.get_daily_sleep_summaries.return_value = []
        client.get_daily_hrv_summaries.return_value = {}
        client.search_activities.return_value = []
        client.get_daily_body_battery.side_effect = RuntimeError("Garmin down")
        with patch.object(sync, "PersonalApiAuthenticator", MagicMock()), \
                patch.object(sync, "GarminPersonalApiClient", return_value=client), \
                patch.dict(os.environ, {"GARMIN_API_KEY": "not-a-real-key"}):
            result = sync.run_sync(days=1)
        self.assertEqual(result["counts"]["daily_summaries"], 1)
        self.assertIn("body_battery", result["errors"][0])


SLEEP_NIGHT = {
    "calendarDate": "2026-10-31", "userProfilePK": 151415740,
    "sleepStartTimestampLocal": utc_ms("2026-10-30T23:49:49"), "sleepEndTimestampLocal": utc_ms("2026-10-31T06:56:12"),
    "awakeCount": 0, "averageRespirationValue": 14.0,
    "sleepScoreFeedback": "POSITIVE_LONG_AND_REFRESHING", "sleepScoreInsight": "NONE",
    "sleepScores": {
        "overall": {"value": 87, "qualifierKey": "GOOD"},
        "stress": {"qualifierKey": "FAIR", "optimalStart": 0.0, "optimalEnd": 15.0},
        "deepPercentage": {"value": 19, "qualifierKey": "EXCELLENT", "optimalStart": 16.0, "optimalEnd": 33.0, "idealStartInSeconds": 4093.28},
    },
    "sleepNeed": {"userProfilePk": 151415740, "deviceId": 3605279168, "actual": 490},
}


class SleepDetailTest(TempDbCase):
    def test_keeps_factors_and_times_but_no_identifiers(self):
        raw = sync.parse_sleep(SLEEP_NIGHT)["sleep_details"]
        d = json.loads(raw)
        self.assertEqual((d["start"], d["end"]), ("23:49", "06:56"))
        self.assertEqual(d["scores"]["overall"]["value"], 87)
        self.assertEqual(d["scores"]["stress"]["optimalEnd"], 15.0)
        self.assertNotIn("151415740", raw)
        self.assertNotIn("3605279168", raw)

    def test_night_without_scores_keeps_stored_details(self):
        c = db.connect()
        self.addCleanup(c.close)
        db.upsert(c, "daily", "date", sync.parse_sleep(SLEEP_NIGHT))
        db.upsert(c, "daily", "date", sync.parse_sleep({"calendarDate": "2026-10-31"}))
        stored = c.execute("SELECT sleep_details FROM daily").fetchone()["sleep_details"]
        self.assertEqual(json.loads(stored)["scores"]["overall"]["value"], 87)

    def test_details_reach_the_dashboard_but_not_the_export(self):
        c = db.connect()
        db.upsert(c, "daily", "date", {**sync.parse_sleep(SLEEP_NIGHT), "date": YESTERDAY})
        c.commit()
        c.close()
        rows = TestClient(app).get("/api/daily").json()
        self.assertEqual(json.loads(rows[-1]["sleep_details"])["end"], "06:56")
        self.assertNotIn("sleep_details", json.dumps(build_export(28)))


class TagTest(TempDbCase):
    def put(self, day, tags):
        return TestClient(app).put(f"/api/tags/{day}", json={"tags": tags})

    def test_tags_are_cleaned_saved_and_replaced(self):
        r = self.put(YESTERDAY, ["  Late   Meal ", "alcohol", "ALCOHOL", ""])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["days"], {YESTERDAY: ["alcohol", "late meal"]})
        self.put(TODAY.isoformat(), ["alcohol"])
        self.put(YESTERDAY, ["travel"])
        state = TestClient(app).get("/api/tags").json()
        self.assertEqual(state["days"], {YESTERDAY: ["travel"], TODAY.isoformat(): ["alcohol"]})
        self.assertEqual(set(state["known"]), {"travel", "alcohol"})

    def test_empty_list_clears_the_day(self):
        self.put(YESTERDAY, ["alcohol"])
        self.assertEqual(self.put(YESTERDAY, []).json()["days"], {})

    def test_known_tags_are_most_used_first(self):
        for d in range(1, 4):
            self.put((TODAY - timedelta(days=d)).isoformat(), ["alcohol"] if d == 1 else ["travel"])
        self.assertEqual(TestClient(app).get("/api/tags").json()["known"], ["travel", "alcohol"])

    def test_bad_input_is_rejected(self):
        tomorrow = (TODAY + timedelta(days=1)).isoformat()
        self.assertEqual(self.put("not-a-date", ["x"]).status_code, 422)
        self.assertEqual(self.put(tomorrow, ["x"]).status_code, 422)
        self.assertEqual(self.put(YESTERDAY, ["x" * 41]).status_code, 422)
        self.assertEqual(self.put(YESTERDAY, [f"t{n}" for n in range(21)]).status_code, 422)
        self.assertEqual(TestClient(app).get("/api/tags").json()["days"], {})

    def test_export_adds_tags_only_to_tagged_days(self):
        self.seed()
        self.put(YESTERDAY, ["alcohol"])
        export = build_export(28)
        by_date = {d["date"]: d for d in export["daily"]}
        self.assertEqual(by_date[YESTERDAY]["tags"], ["alcohol"])
        self.assertNotIn("tags", by_date[TODAY.isoformat()])
        self.assertEqual(export["tags_logged_since"], YESTERDAY)

    def test_export_without_tags_has_no_tag_fields(self):
        self.seed()
        export = build_export(28)
        self.assertNotIn("tags_logged_since", export)
        self.assertTrue(all("tags" not in d for d in export["daily"]))


class MigrationTest(TempDbCase):
    def test_old_daily_table_gets_stress_columns_and_keeps_rows(self):
        db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        old = sqlite3.connect(db.DB_PATH)
        old.execute("CREATE TABLE daily (date TEXT PRIMARY KEY NOT NULL, stress_avg REAL)")
        old.execute("INSERT INTO daily VALUES ('2026-11-01', 30)")
        old.commit()
        old.close()
        c = db.connect()
        self.addCleanup(c.close)
        row = dict(c.execute("SELECT * FROM daily").fetchone())
        self.assertEqual(row["stress_avg"], 30)
        self.assertIsNone(row["stress_rest_s"])

    def test_old_database_gets_new_columns_and_keeps_rows(self):
        db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        old = sqlite3.connect(db.DB_PATH)
        old.execute("CREATE TABLE activities (id INTEGER PRIMARY KEY, start_local TEXT, type TEXT, name TEXT, duration_s REAL, "
                    "distance_m REAL, avg_hr REAL, max_hr REAL, calories REAL, te_aerobic REAL, te_anaerobic REAL, training_load REAL)")
        old.execute("INSERT INTO activities (id, type) VALUES (1, 'running')")
        old.commit()
        old.close()
        c = db.connect()
        self.addCleanup(c.close)
        row = dict(c.execute("SELECT * FROM activities").fetchone())
        self.assertEqual(row["type"], "running")
        self.assertIn("run_details", row)
        db.connect().close()  # running it again is harmless


if __name__ == "__main__":
    unittest.main()
