"""Dashboard API + static frontend.  Run: uv run uvicorn backend.main:app --reload"""
import json
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .export import build_export, run_fields
from .sync import run_sync

app = FastAPI(title="Garmin dashboard")
_lock = threading.Lock()


@app.get("/api/daily")
def daily(days: int = 28):
    with db.connect() as c:
        rows = c.execute("SELECT * FROM daily ORDER BY date DESC LIMIT ?", (days,)).fetchall()
    return [dict(r) for r in reversed(rows)]


@app.get("/api/activities")
def activities(limit: int = 100):
    with db.connect() as c:
        rows = c.execute("SELECT * FROM activities ORDER BY start_local DESC LIMIT ?", (limit,)).fetchall()
    return [{**r, "run": run_fields(r) if r["type"] in db.RUN_SUBTYPES else None} for r in map(dict, rows)]


@app.get("/api/activities/{activity_id}/route")
def route(activity_id: int):
    """A run's GPS track. Empty if the file had no GPS or no sync has stored it yet."""
    with db.connect() as c:
        row = c.execute("SELECT points FROM run_routes WHERE id=?", (activity_id,)).fetchone()
    return {"points": json.loads(row["points"]) if row else []}


@app.get("/api/body-battery/{day}")
def body_battery(day: str):
    """One day's curve. Empty until a sync has stored it."""
    with db.connect() as c:
        row = c.execute("SELECT curve FROM body_battery WHERE date=?", (day,)).fetchone()
    return json.loads(row["curve"]) if row and row["curve"] else {"points": [], "events": []}


@app.get("/api/status")
def status():
    with db.connect() as c:
        raw = db.get_meta(c, "last_sync")
        last_ok_at = db.get_meta(c, "last_ok_at")
    return {**json.loads(raw), "last_ok_at": last_ok_at} if raw else None


@app.post("/api/sync")
def sync(days: int = 28):
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "A sync is already running.")
    try:
        return run_sync(days)
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")
    finally:
        _lock.release()


@app.get("/api/export")
def export(days: int = 28):
    """Download a compact, identifier-free JSON summary to attach to an AI chat."""
    name = f"garmin-export-{date.today().isoformat()}.json"
    return JSONResponse(build_export(days), headers={"Content-Disposition": f'attachment; filename="{name}"'})


app.mount("/", StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend", html=True), name="ui")