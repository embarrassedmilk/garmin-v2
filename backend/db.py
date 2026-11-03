"""SQLite storage: one row per day, one row per activity."""
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
)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
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