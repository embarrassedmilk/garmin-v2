"""Pull data from Garmin's Personal API into SQLite.

Run:  uv run python -m backend.sync [--days 28]
"""
import argparse
import io
import json
import os
import zipfile
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from garmin_fit_sdk import Decoder, Stream
from garmin_personal_api import GarminPersonalApiClient, PersonalApiAuthenticator

from . import db

load_dotenv()
CHUNK_DAYS = 28  # Garmin caps most range endpoints at 28-31 days per request
BODY_BATTERY_CHUNKS = 96  # readings per day: one every 15 minutes
DAY_MIN = 24 * 60
ROUTE_MAX_POINTS = 500  # plenty for a map; a run file has one point per second or so
SEMICIRCLE_DEG = 180 / 2**31  # activity files store positions in semicircles


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


def seconds(ms):
    return None if ms is None else ms / 1000


def parse_summary(d: dict) -> dict:
    stress = d.get("stress")
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
        "stress_max": g(d, "stress", "maxLevel"),
        "stress_rest_s": seconds(g(stress, "restDurationInMillis")),
        "stress_low_s": seconds(g(stress, "lowStressDurationInMillis")),
        "stress_med_s": seconds(g(stress, "mediumStressDurationInMillis")),
        "stress_high_s": seconds(g(stress, "highStressDurationInMillis")),
        "stress_activity_s": seconds(g(stress, "activityDurationInMillis")),
        "stress_uncat_s": seconds(g(stress, "uncategorizedDurationInMillis")),
        "bb_min": g(d, "bodyBattery", "minValue"),
        "bb_max": g(d, "bodyBattery", "maxValue"),
        "bb_charged": g(d, "bodyBattery", "chargedValue"),
        "bb_drained": g(d, "bodyBattery", "drainedValue"),
        "bb_latest": g(d, "bodyBattery", "latestValue"),
        "resp_avg": g(d, "respiration", "avgValue"),
    }


def local_clock(ms):
    """Garmin's "local" timestamps are the local clock written as if it were UTC."""
    return None if ms is None else datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%H:%M")


def parse_sleep_details(d: dict):
    """Only named fields: the summary's sleepNeed and sleepAlignment hold profile and device IDs."""
    scores = d.get("sleepScores")
    if not scores:
        return None
    fields = ("qualifierKey", "value", "optimalStart", "optimalEnd")
    return json.dumps({
        "start": local_clock(d.get("sleepStartTimestampLocal")),
        "end": local_clock(d.get("sleepEndTimestampLocal")),
        "awake_count": d.get("awakeCount"),
        "resp_avg": d.get("averageRespirationValue"),
        "feedback": d.get("sleepScoreFeedback"),
        "insight": d.get("sleepScoreInsight"),
        "scores": {k: {f: v.get(f) for f in fields} for k, v in scores.items() if isinstance(v, dict)},
    })


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
        "sleep_details": parse_sleep_details(d),
    }


def parse_hrv(d: dict) -> dict:
    return {"date": d["calendarDate"], "hrv_last_night": d.get("lastNightAvg"), "hrv_weekly": d.get("weeklyAvg")}


def parse_body_battery(d: dict) -> dict:
    """The day's curve, in minutes after local midnight. Keeps event types and times, not Garmin's feedback text."""
    if not (d.get("startTimestampGMT") and d.get("startTimestampLocal")):
        return {"date": d.get("date"), "curve": None}
    parse = lambda s: datetime.fromisoformat(s[:19])
    start_gmt, start_local = parse(d["startTimestampGMT"]), parse(d["startTimestampLocal"])
    first_min = start_local.hour * 60 + start_local.minute
    epoch_ms = start_gmt.replace(tzinfo=timezone.utc).timestamp() * 1000
    # Garmin repeats some timestamps, always with the same level.
    levels = {ts: level for ts, level in d.get("bodyBatteryValuesArray") or [] if level is not None}
    points = [[round((ts - epoch_ms) / 60000) + first_min, level] for ts, level in sorted(levels.items())]
    events = []
    for e in d.get("bodyBatteryActivityEvent") or []:
        local = parse(e["eventStartTimeGmt"]) + timedelta(milliseconds=e.get("timezoneOffset") or 0)
        begin = (local - start_local).total_seconds() / 60 + first_min
        end = begin + (e.get("durationInMilliseconds") or 0) / 60000
        if end > 0 and begin < DAY_MIN:  # sleep can start the evening before: keep only the part on this day
            events.append({"type": e.get("eventType"), "start_min": round(max(begin, 0)), "end_min": round(min(end, DAY_MIN))})
    events.sort(key=lambda e: e["start_min"])
    return {"date": d.get("date"), "curve": json.dumps({"points": points, "events": events})}


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
        "te_label": a.get("trainingEffectLabel"),
        "elevation_gain_m": a.get("elevationGain"),
        "elevation_loss_m": a.get("elevationLoss"),
        "avg_gap_mps": a.get("avgGradeAdjustedSpeed"),
        "avg_cadence_spm": a.get("averageRunningCadenceInStepsPerMinute"),
        "avg_stride_cm": a.get("avgStrideLength"),
        "avg_vertical_osc_cm": a.get("avgVerticalOscillation"),
        **{f"hr_z{n}_s": a.get(f"hrTimeInZone_{n}") for n in range(1, 6)},
        "run_details": "{}" if a.get("manualActivity") else None,  # manual entries have no file to fetch
    }


def read_fit(zip_bytes: bytes) -> dict:
    """Decode an activity file in memory. It also holds profile data, so the file itself is never saved."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        fit = z.read(next(n for n in z.namelist() if n.lower().endswith(".fit")))
    messages, _ = Decoder(Stream.from_byte_array(bytearray(fit))).read(convert_datetimes_to_dates=False, merge_heart_rates=False)
    return messages


def parse_vo2max(messages: dict):
    """VO2 max from undocumented message 140, field 7 (METs as 16.16 fixed point). Matches the value on the watch."""
    raw = next(iter(messages.get("140", [])), {}).get(7)
    value = None if raw is None else raw / 65536 * 3.5
    return value if value and 10 <= value <= 100 else None  # undocumented, so reject anything implausible


def parse_run_details(messages: dict) -> dict:
    """Keep only laps, HR zone boundaries and VO2 max from a decoded activity file."""
    laps = []
    for lap in messages.get("lap_mesgs", []):
        cadence = lap.get("avg_running_cadence")
        if cadence is not None:  # the file stores strides per minute: one foot, whole and fractional parts
            cadence = (cadence + (lap.get("avg_fractional_cadence") or 0)) * 2
        laps.append({
            "dist_m": lap.get("total_distance"),
            "time_s": lap.get("total_timer_time"),
            "hr": lap.get("avg_heart_rate"),
            "gain_m": lap.get("total_ascent"),
            "loss_m": lap.get("total_descent"),
            "cadence_spm": cadence,
            "gap_mps": lap.get("avg_grade_adjusted_speed"),
            "trigger": lap.get("lap_trigger"),
        })
    zones = next((z for z in messages.get("time_in_zone_mesgs", []) if z.get("reference_mesg") == "session"), {})
    return {"laps": laps, "hr_zone_bounds_bpm": zones.get("hr_zone_high_boundary"), "vo2max": parse_vo2max(messages)}


def parse_route(messages: dict) -> list:
    """The GPS track as [lat, lon] pairs, thinned to at most ROUTE_MAX_POINTS. Local only: never exported."""
    points = [[round(r["position_lat"] * SEMICIRCLE_DEG, 5), round(r["position_long"] * SEMICIRCLE_DEG, 5)]
              for r in messages.get("record_mesgs", [])
              if r.get("position_lat") is not None and r.get("position_long") is not None]
    if len(points) <= ROUTE_MAX_POINTS:
        return points
    kept = points[:-1][::(len(points) - 1) // (ROUTE_MAX_POINTS - 1) + 1]
    return kept + [points[-1]]  # always end where the run ended


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
        pull("body_battery", lambda s, e: client.get_daily_body_battery(s, e, chunks=BODY_BATTERY_CHUNKS) or [],
             parse_body_battery, "body_battery", "date")

        def activities(s, e):
            page, out = 0, []
            while True:
                batch = client.search_activities(start=page, limit=40, start_date=s, end_date=e) or []
                out += batch
                if len(batch) < 40:
                    return out
                page += 40

        pull("activities", activities, parse_activity, "activities", "id")

        # One download per run, once: a run's laps and route don't change after upload.
        # Runs synced before routes were stored have details but no route, so they get one more download.
        run_types = list(db.RUN_SUBTYPES)
        pending = c.execute(
            f"SELECT id, start_local FROM activities "
            f"WHERE (run_details IS NULL OR (run_details != '{{}}' AND id NOT IN (SELECT id FROM run_routes))) "
            f"AND substr(start_local,1,10) >= ? "
            f"AND type IN ({', '.join('?' * len(run_types))})", [start.isoformat(), *run_types]).fetchall()
        counts["run_details"] = 0
        for run in pending:
            try:
                messages = read_fit(client.download_activity(run["id"]))
                db.upsert(c, "activities", "id", {"id": run["id"], "run_details": json.dumps(parse_run_details(messages))})
                db.upsert(c, "run_routes", "id", {"id": run["id"], "points": json.dumps(parse_route(messages))})
                counts["run_details"] += 1
            except Exception as ex:
                errors.append(f"run_details {run['start_local'][:10]}: {type(ex).__name__}: {str(ex)[:200]}")
            c.commit()

    return _record(c, days, counts, errors)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=28)
    print(json.dumps(run_sync(ap.parse_args().days), indent=2))