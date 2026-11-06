"""Compact, identifier-free summary of the stored data, for attaching to an AI chat."""
import json
from datetime import date, datetime
from statistics import mean

from . import db

CAVEATS = [
    "Single user, wrist-worn Garmin watch. Values are Garmin's estimates, not clinical measurements.",
    "Sleep rows are dated by wake-up day.",
    "Rows with partial=true were last fetched before the day ended: steps, stress, body battery, resting HR "
    "and workouts are incomplete and excluded from stats. Sleep and HRV on those rows cover the night that "
    "ended that morning, and are included.",
    "HRV baseline/status may be unavailable while Garmin is still onboarding; only nightly averages are included.",
    "Some values (e.g. sleep_spo2) can be null.",
    "Apart from the optional daily tags, there is no context data (food, alcohol, caffeine, illness, mood, work). "
    "Do not guess causes.",
    "tags are short labels the user logged by hand on some days (e.g. alcohol, illness, travel), starting at "
    "tags_logged_since. A day without tags means nothing was logged, not that nothing happened. A tag is dated by "
    "the day it happened, while sleep, HRV and sleep_* fields are dated by wake-up day, so an evening tag's effect "
    "on sleep shows up in the next day's row. Tagged days are few: treat any link to the metrics as a hint to "
    "check, not a cause.",
    "Only a few weeks of data: flag low confidence, avoid trend claims on short samples, no medical advice.",
    "step_goal is Garmin's auto-adjusting goal, not one the user chose. Do not treat missed goals as meaningful.",
    "A null sleep_spo2 means Garmin returned no reading (Pulse Ox may be off on the watch), not a low value.",
    "Not available from Garmin's Personal API, so absent here: training status, training readiness, "
    "recovery time, acute and chronic load, run temperature. Do not estimate them.",
    "readiness.vo2max_estimate is Garmin's estimate, read from an undocumented field of each run's activity file. "
    "The user checked it against the watch. It only updates on days with a run. It is not a lab value, and "
    "changes of a few tenths are not meaningful.",
    "Only running workouts (road, trail, treadmill) have the detailed run fields. Fields Garmin did not return "
    "for a run are omitted. activity_subtype comes from the activity type: road means Garmin's generic running "
    "type, not a verified surface.",
    "Paces are decimal minutes per km (5.5 is 5:30). In splits, km is the cumulative distance at the end of the "
    "split. split_type km means 1 km auto-laps; lap means the laps are irregular (e.g. pressed manually). "
    "Laps under 50 m are left out of splits.",
    "Elevation comes from the watch's barometer and is not corrected, so it can be off, especially on flat routes "
    "and in changing weather. Grade-adjusted pace and HR zones are Garmin's estimates.",
    "hr_zone_minutes uses hr_zone_bounds_bpm: six values, the floors of zones 1-5 and then the zone 5 ceiling. "
    "Time below zone 1 is not counted, so the minutes can sum to less than the duration.",
    "hr_drift_pct is aerobic decoupling on runs of 40 min or more: pace-to-HR ratio of the first half of the time "
    "divided by the second half, minus 1, as a percentage. Positive means HR rose relative to pace. It is "
    "unreliable on hilly routes, and on intervals, progression runs or runs without a warm-up, where a change in "
    "effort looks like drift. hr_drift_basis gap uses grade-adjusted pace; pace_not_grade_adjusted means hills "
    "may distort it further.",
]
OVERNIGHT_KEYS = {"hrv_last_night_ms", "sleep_score", "sleep_hours", "deep_min", "rem_min", "sleep_avg_hr"}
DRIFT_MIN_S = 40 * 60
DRIFT_MIN_LAPS = 4  # fewer can't show a change between halves: splitting a lap just copies its average
SPLIT_MIN_M = 50  # shorter laps (e.g. the last seconds before stopping) only add a noisy pace
KM_LAP_M = 1000


def _is_partial(r: dict, today: str) -> bool:
    fetched = r["summary_fetched_on"]
    return r["date"] >= today or fetched is None or fetched <= r["date"]


def _r(v, n=1):
    return None if v is None else round(v, n)


def _m(s):
    return None if s is None else round(s / 60)


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(mean(vals), 1) if vals else None


def _pace(mps):
    return round(1000 / 60 / mps, 2) if mps else None


def _whole(v):
    return None if v is None else round(v)


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _split_type(laps: list) -> str:
    """km if every lap but the last is a 1 km auto-lap, otherwise lap."""
    auto = all(lap["trigger"] == "distance" and abs((lap["dist_m"] or 0) - KM_LAP_M) < 1 for lap in laps[:-1])
    return "km" if auto and len(laps) > 1 else "lap"


def _splits(laps: list) -> list:
    out, done_m = [], 0
    for lap in laps:
        dist, time = lap["dist_m"] or 0, lap["time_s"] or 0
        done_m += dist
        if dist < SPLIT_MIN_M or not time:
            continue
        out.append(_drop_none({
            "km": round(done_m / 1000, 2), "pace_min_per_km": _pace(dist / time), "avg_hr": lap["hr"],
            "elevation_gain_m": lap["gain_m"], "elevation_loss_m": lap["loss_m"],
            "cadence_spm": _whole(lap["cadence_spm"]),
        }))
    return out


def _hr_drift(laps: list, duration_s) -> tuple:
    """(drift %, basis), comparing the two halves of the timer time. A lap crossing the midpoint is shared."""
    laps = [lap for lap in laps if lap["dist_m"] and lap["time_s"] and lap["hr"]]
    if (duration_s or 0) < DRIFT_MIN_S or len(laps) < DRIFT_MIN_LAPS:
        return None, None
    use_gap = all(lap["gap_mps"] for lap in laps)
    middle, elapsed = sum(lap["time_s"] for lap in laps) / 2, 0
    halves = [{"time": 0, "speed": 0, "hr": 0}, {"time": 0, "speed": 0, "hr": 0}]
    for lap in laps:
        t = lap["time_s"]
        first_share = min(max(middle - elapsed, 0), t) / t
        speed = lap["gap_mps"] if use_gap else lap["dist_m"] / t
        for half, share in zip(halves, (first_share, 1 - first_share)):
            half["time"] += t * share
            half["speed"] += speed * t * share
            half["hr"] += lap["hr"] * t * share
        elapsed += t
    first, second = (h["speed"] / h["hr"] for h in halves)
    return round((first / second - 1) * 100, 1), "gap" if use_gap else "pace_not_grade_adjusted"


def run_fields(a: dict) -> dict:
    """Detailed fields for a running workout. Anything Garmin didn't return is left out."""
    details = json.loads(a["run_details"] or "{}")
    laps = details.get("laps") or []
    zones = [a[f"hr_z{n}_s"] for n in range(1, 6)]
    drift, basis = _hr_drift(laps, a["duration_s"])
    stride = a["avg_stride_cm"]
    return _drop_none({
        "activity_subtype": db.RUN_SUBTYPES[a["type"]],
        "primary_benefit": a["te_label"] and a["te_label"].lower(),
        "elevation_gain_m": _whole(a["elevation_gain_m"]),
        "elevation_loss_m": _whole(a["elevation_loss_m"]),
        "avg_gap_min_per_km": _pace(a["avg_gap_mps"]),
        "avg_cadence_spm": _whole(a["avg_cadence_spm"]),
        "avg_stride_length_m": None if stride is None else round(stride / 100, 2),
        "avg_vertical_oscillation_cm": _r(a["avg_vertical_osc_cm"]),
        "hr_zone_minutes": None if all(z is None for z in zones) else
        {f"z{n}": round((z or 0) / 60, 1) for n, z in enumerate(zones, 1)},
        "hr_zone_bounds_bpm": details.get("hr_zone_bounds_bpm"),
        "hr_drift_pct": drift,
        "hr_drift_basis": basis,
        "split_type": _split_type(laps) if laps else None,
        "splits": _splits(laps) or None,
    })


def _run_stats(runs: list) -> dict:
    longest = max(runs, key=lambda w: w["distance_km"] or 0, default=None)
    return {
        "count": len(runs),
        "total_km": round(sum(w["distance_km"] or 0 for w in runs), 1),
        "total_min": sum(w["duration_min"] or 0 for w in runs),
        "total_elevation_gain_m": sum(w.get("elevation_gain_m") or 0 for w in runs),
        "longest_km": longest and longest["distance_km"],
        "longest_min": longest and longest["duration_min"],
    }


def build_export(days: int = 28) -> dict:
    c = db.connect()
    rows = [dict(r) for r in reversed(c.execute("SELECT * FROM daily ORDER BY date DESC LIMIT ?", (days,)).fetchall())]
    first = rows[0]["date"] if rows else "9999-12-31"
    acts = [dict(r) for r in c.execute(
        "SELECT * FROM activities WHERE substr(start_local,1,10) >= ? ORDER BY start_local", (first,)).fetchall()]
    tags = db.all_tags(c)["days"]
    c.close()

    today = date.today().isoformat()
    daily = [{
        "date": r["date"], "partial": _is_partial(r, today),
        "steps": r["steps"], "step_goal": r["step_goal"], "distance_km": _r(r["distance_m"] and r["distance_m"] / 1000, 1),
        "calories_total": r["calories_total"], "calories_active": r["calories_active"],
        "resting_hr": r["rhr"], "hr_min": r["hr_min"], "hr_max": r["hr_max"],
        "hrv_last_night_ms": r["hrv_last_night"], "hrv_weekly_avg_ms": r["hrv_weekly"],
        "stress_avg": r["stress_avg"],
        "body_battery_min": r["bb_min"], "body_battery_max": r["bb_max"],
        "body_battery_charged": r["bb_charged"], "body_battery_drained": r["bb_drained"],
        "sleep_score": r["sleep_score"], "sleep_hours": _r(r["sleep_s"] and r["sleep_s"] / 3600, 2),
        "deep_min": _m(r["deep_s"]), "light_min": _m(r["light_s"]), "rem_min": _m(r["rem_s"]), "awake_min": _m(r["awake_s"]),
        "sleep_avg_hr": r["sleep_hr"], "sleep_stress": r["sleep_stress"], "sleep_spo2": r["sleep_spo2"],
        **({"tags": tags[r["date"]]} if r["date"] in tags else {}),
    } for r in rows]

    workouts = [{
        "date": a["start_local"][:10], "type": a["type"], "duration_min": _m(a["duration_s"]),
        "distance_km": _r(a["distance_m"] and a["distance_m"] / 1000, 1), "avg_hr": a["avg_hr"], "max_hr": a["max_hr"],
        "aerobic_te": _r(a["te_aerobic"]), "anaerobic_te": _r(a["te_anaerobic"]), "training_load": _r(a["training_load"], 0),
        **(run_fields(a) if a["type"] in db.RUN_SUBTYPES else {}),
    } for a in acts]  # names omitted on purpose: they can contain place names

    done = [d for d in daily if not d["partial"]]
    nights = [d for d in daily if not d["partial"] or any(d[k] is not None for k in OVERNIGHT_KEYS)]
    recent, earlier = done[-7:], done[:-7]
    keys = ["resting_hr", "hrv_last_night_ms", "sleep_score", "sleep_hours", "deep_min", "rem_min", "steps",
            "stress_avg", "body_battery_charged", "body_battery_drained", "sleep_avg_hr"]
    stats = {}
    for k in keys:
        source = nights if k in OVERNIGHT_KEYS else done
        vals = [d[k] for d in source if d[k] is not None]
        stats[k] = {
            "n": len(vals), "mean": _avg(vals), "min": min(vals, default=None), "max": max(vals, default=None),
            "last7_mean": _avg([d[k] for d in source[-7:]]), "earlier_mean": _avg([d[k] for d in source[:-7]]),
        }
    dates = {d["date"] for d in recent}
    prior_dates = {d["date"] for d in earlier}
    for name, ds in (("last7", dates), ("earlier", prior_dates)):
        ws = [w for w in workouts if w["date"] in ds]
        stats[f"workouts_{name}"] = {
            "count": len(ws), "total_min": sum(w["duration_min"] or 0 for w in ws),
            "total_training_load": sum(w["training_load"] or 0 for w in ws),
        }
    runs = [w for w in workouts if "activity_subtype" in w]
    vo2max = {a["start_local"][:10]: json.loads(a["run_details"] or "{}").get("vo2max") for a in acts}  # last run of a day wins
    stats["runs"] = {
        **_run_stats([w for w in runs if w["date"] in dates | prior_dates]),
        "last7": _run_stats([w for w in runs if w["date"] in dates]),
        "earlier": _run_stats([w for w in runs if w["date"] in prior_dates]),
    }

    return {
        "about": "Garmin watch export for AI analysis. Identifiers and activity names removed.",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "range": {"from": daily[0]["date"] if daily else None, "to": daily[-1]["date"] if daily else None, "days": len(daily)},
        **({"tags_logged_since": min(tags)} if tags else {}),
        "caveats": CAVEATS,
        "stats": stats,
        "daily": daily,
        "workouts": workouts,
        "readiness": [{"date": d, "vo2max_estimate": round(v, 1)} for d, v in vo2max.items() if v],
    }