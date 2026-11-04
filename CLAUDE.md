# Garmin dashboard: project context

Personal, single-user, local-only web dashboard for my Garmin watch metrics. Data comes from Garmin's Personal API; AI insights (not built yet) come from Gemini and/or Claude. Read `PLAN.md` (phase checklist) and `docs/data-notes.md` (what the API returns, quirks, limits) before changing data code.

## Commands (uv only, never pip)
- Install / add deps: `uv sync`, `uv add <pkg>` (explain before adding a new dependency)
- Pull the last 28 days from Garmin into SQLite: `uv run python -m backend.sync [--days N]`
- Run the dashboard: `uv run uvicorn backend.main:app --reload` then open http://127.0.0.1:8000
- Run the tests (temporary database, no Garmin calls): `uv run python -m unittest`
- Dump raw Garmin responses for exploration: `uv run python scripts/explore.py [--days N] [--only sleep hrv] [--path /x --param k=v]` (writes `raw_dumps/`, gitignored)

## Layout
- `backend/db.py`: SQLite (`data/garmin.db`, gitignored). Tables `daily` (one row per date), `activities`, `body_battery` (one JSON curve per date), `meta` (`last_sync` is the latest attempt; `last_ok_at` is the last run that stored data). `upsert()` never overwrites an existing value with NULL. New `daily` and `activities` columns go in `ADDED_DAILY_COLS` and `ADDED_ACTIVITY_COLS`, which `connect()` adds to existing databases.
- `backend/sync.py`: pulls daily summaries, sleep summaries, HRV summaries and activities in 28-day chunks, newest first; a failing chunk is recorded in `errors` and does not stop the others. Then downloads the activity file once per run (`run_details`), decodes it in memory with `garmin-fit-sdk`, and keeps only laps, HR zone boundaries and VO2 max: the file also holds GPS and profile data, so it is never saved.
- `backend/export.py`: builds the compact, identifier-free JSON summary (`GET /api/export`) that I attach to AI chats for analysis.
- `backend/main.py`: FastAPI. `GET /api/daily`, `/api/activities` (runs also get `run`: the same fields as the export, from `run_fields()`), `/api/body-battery/{day}`, `/api/status`, `/api/export`; `POST /api/sync`; serves `frontend/`.
- `frontend/index.html`: single file, Chart.js from a CDN, light/dark via CSS variables. Sleep hero, stat row, charts, workouts table with a tab per workout type (clicking a run opens a pop-up with summary stats, time in each zone, and pace, heart rate and splits per km), stress pop-up from the Average stress tile (time in each band, body battery curve), sleep pop-up from the sleep score (rating per score factor, bed and wake times), range select (7/14/28 days), Sync and Export buttons, banner for sync errors and out-of-date data.
- `tests/test_app.py`: smoke, sync and export tests.
- `scripts/explore.py`, `docs/data-notes.md` (still a stub), `PLAN.md`, `.env.example`.

## Rules
- Use Garmin's official client (`garmin_personal_api`) for every Garmin call. No hand-rolled HTTP. It handles the key-to-token exchange (tokens last 24h) and caches the token at `~/.config/garmin/`.
- Secrets live in `.env` (`GARMIN_API_KEY`, `GEMINI_API_KEY`, later maybe `ANTHROPIC_API_KEY`). Never print, log or commit them. All Garmin and AI calls happen in the backend, never in the browser.
- Garmin API is read-only. Backfill must respect per-request limits: 28 days for steps/stress, 31 for daily summaries, sleep summaries and body battery, 365 for HRV, about 45 activities per page. Daily summaries over about 90 days fail with HTTP 500. Chunk at 28 days.
- Health data stays local. Strip identifiers (name, profile ID, profile image URLs, activity names) before sending anything to an AI service, as `export.py` does. `raw_dumps/` and `data/` are gitignored.
- Keep changes small and simple. Update the checkboxes in `PLAN.md` as work completes.

## Data facts that matter
- Sleep rows are dated by the wake-up day. A row is partial until it is fetched after its day has ended (`summary_fetched_on`). Partial rows' steps, stress, body battery, resting HR and workouts are excluded from statistics; their sleep and HRV cover the night that just ended and are included.
- Units from Garmin: seconds (durations), metres (distance), grams (weight), epoch milliseconds (most timestamps).
- HRV baseline and status are null while Garmin is onboarding the account; only nightly and weekly averages are reliable.
- Not available from the Personal API: training readiness, training status, recovery time, acute and chronic load, run temperature (checked 2026-11-03 via dumps, activity details, FIT files and probes). Substitutes: `activityTrainingLoad`, training effect, fitness age, resting HR, HRV, sleep score, body battery.
- VO2 max is only in each run's activity file, in undocumented message 140: field 7 / 65536 x 3.5. I confirmed the value matches my watch. Field 9 of the same message may be recovery minutes: unverified, not exported. `/activity-service/activity/{id}/details` has a `directPerformanceCondition` series, also not exported.
- No stress readings through the day: the client only has daily statistics, and `/wellness-service/wellness/dailyStress/{date}` returns 404 (checked 2026-11-03). Daily summaries do give time per band and max level. Closest substitute: `get_daily_body_battery(..., chunks=96)`, 15-minute readings (repeated timestamps have the same level; 28 and 31-day ranges work) plus `ACTIVITY`, `SLEEP`, `STRESS` and `RECOVERY` periods. Curves are stored in minutes after local midnight using the day's start offset, so a day with a clock change can run past 1440 and its times are an hour off after the change.
- Per-km data exists only in the activity file (`lap_mesgs`); `get_activity` has only aggregate `splitSummaries`. Run summary fields (elevation, GAP, cadence, stride, HR zone seconds) are already in `search_activities`.
- Sleep score factors (`daily.sleep_details`, JSON, not exported) have Garmin's rating and optimal range but no points. Only the overall score and the deep, light and REM percentages have a `value`; restlessness has no value anywhere in the summary.
- Sleep SpO2 can be null. It has been null since 2026-10-28 because Garmin returns no SpO2 at all, sleep or all-day (last reading 2026-10-27 06:29), not because of the parser; Pulse Ox is probably off on the watch. Garmin's scores (stress, body battery, sleep stages) are wrist-based estimates, not clinical values.

## Export fields (`backend/export.py`)
Only add fields; existing ones stay as they are. Anything Garmin didn't return is omitted rather than null. Paces are decimal minutes per km.
- Added to `workouts` entries for `running`, `trail_running` and `treadmill_running` only (other types keep the summary fields): `activity_subtype` (road/trail/treadmill), `elevation_gain_m`, `elevation_loss_m`, `avg_gap_min_per_km`, `avg_cadence_spm`, `avg_stride_length_m`, `hr_zone_minutes` (`z1` to `z5`), `hr_zone_bounds_bpm` (zone floors, then the zone 5 ceiling), `hr_drift_pct`, `hr_drift_basis`, `split_type` (km/lap), `splits`.
- `splits` entries: `km` (cumulative at the end of the split), `pace_min_per_km`, `avg_hr`, `elevation_gain_m`, `elevation_loss_m`, `cadence_spm`. Laps under 50 m are left out.
- `hr_drift_pct`: first-half speed:HR divided by second-half, minus 1, by timer time. Only for runs of 40 min or more with at least 4 laps. Uses grade-adjusted pace when every lap has it (`hr_drift_basis: gap`), otherwise `pace_not_grade_adjusted`.
- `stats.runs`: `count`, `total_km`, `total_min`, `total_elevation_gain_m`, `longest_km`, `longest_min`, repeated under `last7` and `earlier`. Partial days are excluded, as in the other stats.
- `readiness`: top-level series of `date` and `vo2max_estimate`, only for days with a run that has a value.
- `caveats` also covers the auto-adjusting `step_goal`, null SpO2, the unavailable metrics, and how far to trust elevation, GAP, zones and drift.
- The `trail_running` and `treadmill_running` type keys are not yet confirmed against real data: I only have `running` so far. A run that shows no `activity_subtype` means a key is wrong (`RUN_SUBTYPES` in `db.py`).

## AI insights design (not built yet)
- Compute the statistics in code (own baselines, 7-day vs earlier averages, sample sizes). The model only writes the narrative from that compact summary.
- Prompt rules: state confidence and sample size, say when data is too thin, no causal claims (no context data like food, alcohol, illness, mood), no medical advice.
- Put it in `backend/insights.py` with a provider switch (`gemini` | `anthropic`) using the same prompt so outputs can be compared. Cache results; add a "regenerate" action.

## Next steps
1. Confirm how far back each endpoint's history goes. Data so far starts at 2026-10-15, so one older-window probe should settle it, then decide on any backfill.
2. Daily tag log (e.g. alcohol, illness, travel, late meal), included in the export.
3. Stat row: show each value against my own baseline.
4. Tests using sanitized fixtures from `raw_dumps/` for the parsers in `sync.py` and the stats in `export.py`.
5. Fill in `docs/data-notes.md`; README.
6. Check whether message 140 field 9 in the activity file is recovery minutes, by comparing it with the watch right after a run. Only export it if it matches.
7. `insights.py` plus an insights panel in the UI, once there are 6 to 8 weeks of data (around 2026-11-26 to 2026-12-10).

## Decisions already made
- uv for the environment; Python + FastAPI + SQLite + plain HTML/JS.
- Garmin's official client, not the third-party `gcpapi-sync` (which would add automatic range-splitting and retries but is a single-maintainer package).
- v1 metrics: sleep, resting HR, HRV, steps, stress, body battery, training load, workouts.
- Run splits come from the activity file via Garmin's `garmin-fit-sdk`, not `/details`, which is downsampled and has no laps.
- Sync is manual (Sync button or CLI). No scheduled job: one was written and deliberately dropped, so don't re-add it unless asked.
- Start with 28 days of data; insights explored first by attaching the export to a chat, then automated.