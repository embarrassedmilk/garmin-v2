"""SQLite storage: one row per day, one row per activity, plus the tags logged on the dashboard."""
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv("GARMIN_DB", Path(__file__).resolve().parent.parent / "data" / "garmin.db"))

DAILY_COLS = [
    "steps", "step_goal", "distance_m", "calories_total", "calories_active",
    "rhr", "hr_min", "hr_max", "stress_avg", "bb_min", "bb_max", "bb_charged",
    "bb_drained", "bb_latest", "resp_avg", "sleep_score", "sleep_s", "deep_s",
    "light_s", "rem_s", "awake_s", "sleep_hr", "sleep_stress", "sleep_spo2",
    "hrv_last_night", "hrv_weekly",
]

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS daily (date TEXT PRIMARY KEY NOT NULL, "
    + ", ".join(f"{c} REAL" for c in DAILY_COLS)
    + ", summary_fetched_on TEXT);"  # day-totals are only final if fetched after the day ended
    "CREATE TABLE IF NOT EXISTS activities (id INTEGER PRIMARY KEY, start_local TEXT, type TEXT, "
    "name TEXT, duration_s REAL, distance_m REAL, avg_hr REAL, max_hr REAL, calories REAL, "
    "te_aerobic REAL, te_anaerobic REAL, training_load REAL);"
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"
    "CREATE TABLE IF NOT EXISTS body_battery (date TEXT PRIMARY KEY NOT NULL, curve TEXT);"  # JSON, kept out of daily to keep /api/daily small
    # JSON [[lat, lon], ...], "[]" if the file has no GPS. Its own table so location can't leak through SELECT * on activities.
    "CREATE TABLE IF NOT EXISTS run_routes (id INTEGER PRIMARY KEY, points TEXT);"
    # Logged by hand on the dashboard: Garmin's API doesn't expose the app's Lifestyle Logging.
    "CREATE TABLE IF NOT EXISTS tags (date TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (date, tag));"
)

# Added after the first release, so existing databases need them too.
ADDED_DAILY_COLS = {
    "stress_max": "REAL", "stress_rest_s": "REAL", "stress_low_s": "REAL", "stress_med_s": "REAL",
    "stress_high_s": "REAL", "stress_activity_s": "REAL", "stress_uncat_s": "REAL",
    "sleep_details": "TEXT",  # JSON: score factors, bed and wake times, respiration
}
ADDED_ACTIVITY_COLS = {
    "elevation_gain_m": "REAL", "elevation_loss_m": "REAL", "avg_gap_mps": "REAL",
    "avg_cadence_spm": "REAL", "avg_stride_cm": "REAL",
    "hr_z1_s": "REAL", "hr_z2_s": "REAL", "hr_z3_s": "REAL", "hr_z4_s": "REAL", "hr_z5_s": "REAL",
    "run_details": "TEXT",  # JSON from the activity file: laps and HR zone boundaries. "{}" means no file.
}

# Garmin activity type -> activity_subtype in the export. Only these get the detailed run fields.
RUN_SUBTYPES = {"running": "road", "trail_running": "trail", "treadmill_running": "treadmill"}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    for table, added in (("daily", ADDED_DAILY_COLS), ("activities", ADDED_ACTIVITY_COLS)):
        existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        for col, kind in added.items():
            if col not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
    return c


def upsert(c: sqlite3.Connection, table: str, key: str, row: dict) -> None:
    """Insert or update; None values never overwrite an existing value."""
    cols = list(row)
    updates = ", ".join(f"{k}=COALESCE(excluded.{k}, {table}.{k})" for k in cols if k != key)
    c.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT({key}) DO UPDATE SET {updates}",
        [row[k] for k in cols],
    )


def set_meta(c: sqlite3.Connection, key: str, value: str) -> None:
    c.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def get_meta(c: sqlite3.Connection, key: str):
    r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None

def all_tags(c: sqlite3.Connection) -> dict:
    """{"days": {date: [tags]}, "known": every tag used, most used first}."""
    days = {}
    for r in c.execute("SELECT date, tag FROM tags ORDER BY date, tag"):
        days.setdefault(r["date"], []).append(r["tag"])
    known = [r["tag"] for r in c.execute("SELECT tag FROM tags GROUP BY tag ORDER BY COUNT(*) DESC, tag")]
    return {"days": days, "known": known}
