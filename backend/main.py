"""Dashboard API + static frontend.  Run: uv run uvicorn backend.main:app --reload"""
import json
import threading
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .export import build_export
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
    return [dict(r) for r in rows]


@app.get("/api/status")
def status():
    with db.connect() as c:
        raw = db.get_meta(c, "last_sync")
    return json.loads(raw) if raw else None


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