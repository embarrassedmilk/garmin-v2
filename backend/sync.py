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


def _record(c, days: int, counts: dict, errors: list) -> dict:
    """Save the outcome of a run. last_ok_at only moves when data actually arrived."""
    now = datetime.now().isoformat(timespec="seconds")
    result = {"synced_at": now, "days": days, "counts": counts, "errors": errors}
    db.set_meta(c, "last_sync", json.dumps(result))
    if any(counts.values()):
        db.set_meta(c, "last_ok_at", now)
    c.commit()
    c.close()
    return result


def run_sync(days: int = 28) -> dict:
    end = date.today()
    start = end - timedelta(days=days - 1)
    counts, errors = {}, []
    c = db.connect()

    try:
        key = os.getenv("GARMIN_API_KEY")
        if not key:
            raise RuntimeError("GARMIN_API_KEY is missing. Put it in .env.")
        with PersonalApiAuthenticator() as auth:
            token = auth.obtain_token(key)
    except Exception as ex:  # e.g. an expired key: record it so the dashboard can show it
        _record(c, days, counts, [f"login: {type(ex).__name__}: {str(ex)[:200]}"])
        raise

    with GarminPersonalApiClient(token.access_token) as client:

        def pull(name, fetch, parse, table, key_col):
            n = 0
            for s, e in reversed(list(chunks(start, end))):  # newest first: recent data matters most
                try:
                    for item in fetch(s, e):
                        row = parse(item)
                        if row[key_col] is None:  # nothing to key it on, e.g. a null calendarDate
                            continue
                        db.upsert(c, table, key_col, row)
                        n += 1
                except Exception as ex:  # keep going: one failing window shouldn't block the rest
                    errors.append(f"{name} {s} to {e}: {type(ex).__name__}: {str(ex)[:200]}")
                c.commit()  # don't hold the write lock across network calls
            counts[name] = n

        pull("daily_summaries", lambda s, e: (client.get_daily_summaries(s, e) or {}).get("data") or [],
             lambda d: {**parse_summary(d), "summary_fetched_on": end.isoformat()}, "daily", "date")
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

    return _record(c, days, counts, errors)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=28)
    print(json.dumps(run_sync(ap.parse_args().days), indent=2))