"""Compact, identifier-free summary of the stored data, for attaching to an AI chat."""
from datetime import date, datetime
from statistics import mean

from . import db

CAVEATS = [
    "Single user, wrist-worn Garmin watch. Values are Garmin's estimates, not clinical measurements.",
    "Sleep rows are dated by wake-up day.",
    "The row for today has partial=true: it is incomplete and excluded from stats.",
    "HRV baseline/status may be unavailable while Garmin is still onboarding; only nightly averages are included.",
    "Some values (e.g. sleep_spo2) can be null.",
    "No context data (food, alcohol, caffeine, illness, mood, work). Do not guess causes.",
    "Only a few weeks of data: flag low confidence, avoid trend claims on short samples, no medical advice.",
]


def _r(v, n=1):
    return None if v is None else round(v, n)


def _m(s):
    return None if s is None else round(s / 60)


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return round(mean(vals), 1) if vals else None


def build_export(days: int = 28) -> dict:
    c = db.connect()
    rows = [dict(r) for r in reversed(c.execute("SELECT * FROM daily ORDER BY date DESC LIMIT ?", (days,)).fetchall())]
    first = rows[0]["date"] if rows else "9999-12-31"
    acts = [dict(r) for r in c.execute(
        "SELECT * FROM activities WHERE substr(start_local,1,10) >= ? ORDER BY start_local", (first,)).fetchall()]
    c.close()

    today = date.today().isoformat()
    daily = [{
        "date": r["date"], "partial": r["date"] == today,
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
    } for r in rows]

    workouts = [{
        "date": a["start_local"][:10], "type": a["type"], "duration_min": _m(a["duration_s"]),
        "distance_km": _r(a["distance_m"] and a["distance_m"] / 1000, 1), "avg_hr": a["avg_hr"], "max_hr": a["max_hr"],
        "aerobic_te": _r(a["te_aerobic"]), "anaerobic_te": _r(a["te_anaerobic"]), "training_load": _r(a["training_load"], 0),
    } for a in acts]  # names omitted on purpose: they can contain place names

    done = [d for d in daily if not d["partial"]]
    recent, earlier = done[-7:], done[:-7]
    keys = ["resting_hr", "hrv_last_night_ms", "sleep_score", "sleep_hours", "deep_min", "rem_min", "steps",
            "stress_avg", "body_battery_charged", "body_battery_drained", "sleep_avg_hr"]
    stats = {}
    for k in keys:
        vals = [d[k] for d in done if d[k] is not None]
        stats[k] = {
            "n": len(vals), "mean": _avg(vals), "min": min(vals, default=None), "max": max(vals, default=None),
            "last7_mean": _avg([d[k] for d in recent]), "earlier_mean": _avg([d[k] for d in earlier]),
        }
    dates = {d["date"] for d in recent}
    prior_dates = {d["date"] for d in earlier}
    for name, ds in (("last7", dates), ("earlier", prior_dates)):
        ws = [w for w in workouts if w["date"] in ds]
        stats[f"workouts_{name}"] = {
            "count": len(ws), "total_min": sum(w["duration_min"] or 0 for w in ws),
            "total_training_load": sum(w["training_load"] or 0 for w in ws),
        }

    return {
        "about": "Garmin watch export for AI analysis. Identifiers and activity names removed.",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "range": {"from": daily[0]["date"] if daily else None, "to": daily[-1]["date"] if daily else None, "days": len(daily)},
        "caveats": CAVEATS,
        "stats": stats,
        "daily": daily,
        "workouts": workouts,
    }