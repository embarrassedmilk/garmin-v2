"""Pull data from Garmin's Personal API into SQLite.

Run:  uv run python -m backend.sync [--days 28]
"""
import argparse
import json
import os
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from garmin_personal_api import GarminPersonalApiClient, PersonalApiAuthenticator

from . import db

load_dotenv()
CHUNK_DAYS = 28  # Garmin caps most range endpoints at 28-31 days per request


def chunks(start: date, end: date, n: int = CHUNK_DAYS):
    while start <= end:
        stop = min(start + timedelta(days=n - 1), end)
        yield start, stop
        start = stop + timedelta(days=1)


def g(d, *path):
    """Safe nested get."""
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def parse_summary(d: dict) -> dict:
    return {
        "date": d["calendarDate"],
        "steps": g(d, "movement", "steps", "value"),
        "step_goal": g(d, "movement", "steps", "goal"),
        "distance_m": g(d, "movement", "steps", "distanceInMeters"),
        "calories_total": g(d, "calories", "burnedTotal"),
        "calories_active": g(d, "calories", "burnedActive"),
        "rhr": g(d, "heartRate", "restingValue"),
        "hr_min": g(d, "heartRate", "minValue"),
        "hr_max": g(d, "heartRate", "maxValue"),
        "stress_avg": g(d, "stress", "avgLevel"),
        "bb_min": g(d, "bodyBattery", "minValue"),
        "bb_max": g(d, "bodyBattery", "maxValue"),
        "bb_charged": g(d, "bodyBattery", "chargedValue"),
        "bb_drained": g(d, "bodyBattery", "drainedValue"),
        "bb_latest": g(d, "bodyBattery", "latestValue"),
        "resp_avg": g(d, "respiration", "avgValue"),
    }


def parse_sleep(d: dict) -> dict:
    return {
        "date": d["calendarDate"],  # wake-up date
        "sleep_score": g(d, "sleepScores", "overall", "value"),
        "sleep_s": d.get("sleepTimeSeconds"),
        "deep_s": d.get("deepSleepSeconds"),
        "light_s": d.get("lightSleepSeconds"),
        "rem_s": d.get("remSleepSeconds"),
        "awake_s": d.get("awakeSleepSeconds"),
        "sleep_hr": d.get("avgHeartRate"),
        "sleep_stress": d.get("avgSleepStress"),
        "sleep_spo2": d.get("averageSpO2Value"),
    }


def parse_hrv(d: dict) -> dict:
    return {"date": d["calendarDate"], "hrv_last_night": d.get("lastNightAvg"), "hrv_weekly": d.get("weeklyAvg")}


def parse_activity(a: dict) -> dict:
    return {
        "id": a["activityId"],
        "start_local": a.get("startTimeLocal"),
        "type": g(a, "activityType", "typeKey"),
        "name": a.get("activityName"),
        "duration_s": a.get("duration"),
        "distance_m": a.get("distance"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "calories": a.get("calories"),
        "te_aerobic": a.get("aerobicTrainingEffect"),
        "te_anaerobic": a.get("anaerobicTrainingEffect"),
        "training_load": a.get("activityTrainingLoad"),
    }


def run_sync(days: int = 28) -> dict:
    key = os.getenv("GARMIN_API_KEY")
    if not key:
        raise RuntimeError("GARMIN_API_KEY is missing. Put it in .env.")
    end = date.today()
    start = end - timedelta(days=days - 1)
    counts, errors = {}, []

    with PersonalApiAuthenticator() as auth:
        token = auth.obtain_token(key)
    c = db.connect()
    with GarminPersonalApiClient(token.access_token) as client:

        def pull(name, fetch, parse, table, key_col):
            n = 0
            try:
                for s, e in chunks(start, end):
                    for item in fetch(s, e):
                        row = parse(item)
                        if row[key_col] is None:  # nothing to key it on, e.g. a null calendarDate
                            continue
                        db.upsert(c, table, key_col, row)
                        n += 1
            except Exception as ex:  # keep going: one failing endpoint shouldn't block the rest
                errors.append(f"{name}: {type(ex).__name__}: {str(ex)[:200]}")
            counts[name] = n

        pull("daily_summaries", lambda s, e: (client.get_daily_summaries(s, e) or {}).get("data") or [],
             parse_summary, "daily", "date")
        pull("sleep", lambda s, e: client.get_daily_sleep_summaries(s, e) or [], parse_sleep, "daily", "date")
        pull("hrv", lambda s, e: (client.get_daily_hrv_summaries(s, e) or {}).get("hrvSummaries") or [],
             parse_hrv, "daily", "date")

        def activities(s, e):
            page, out = 0, []
            while True:
                batch = client.search_activities(start=page, limit=40, start_date=s, end_date=e) or []
                out += batch
                if len(batch) < 40:
                    return out
                page += 40

        pull("activities", activities, parse_activity, "activities", "id")

    result = {"synced_at": datetime.now().isoformat(timespec="seconds"), "days": days, "counts": counts, "errors": errors}
    db.set_meta(c, "last_sync", json.dumps(result))
    c.commit()
    c.close()
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=28)
    print(json.dumps(run_sync(ap.parse_args().days), indent=2))