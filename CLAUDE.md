# Garmin dashboard: project context

Personal, single-user, local-only web dashboard for my Garmin watch metrics. Data comes from Garmin's Personal API; AI insights (not built yet) come from Gemini and/or Claude. Read `PLAN.md` (phase checklist) and `docs/data-notes.md` (what the API returns, quirks, limits) before changing data code.

## Commands (uv only, never pip)
- Install / add deps: `uv sync`, `uv add <pkg>` (explain before adding a new dependency)
- Pull the last 28 days from Garmin into SQLite: `uv run python -m backend.sync [--days N]`
- Run the dashboard: `uv run uvicorn backend.main:app --reload` then open http://127.0.0.1:8000
- Run the tests (temporary database, no Garmin calls): `uv run python -m unittest`
- Dump raw Garmin responses for exploration: `uv run python scripts/explore.py [--days N] [--only sleep hrv] [--path /x --param k=v]` (writes `raw_dumps/`, gitignored)

## Layout
- `backend/db.py`: SQLite (`data/garmin.db`, gitignored). Tables `daily` (one row per date), `activities`, `meta`. `upsert()` never overwrites an existing value with NULL.
- `backend/sync.py`: pulls daily summaries, sleep summaries, HRV summaries and activities in 28-day chunks, newest first; a failing chunk is recorded in `errors` and does not stop the others.
- `backend/export.py`: builds the compact, identifier-free JSON summary (`GET /api/export`) that I attach to AI chats for analysis.
- `backend/main.py`: FastAPI. `GET /api/daily`, `/api/activities`, `/api/status`, `/api/export`; `POST /api/sync`; serves `frontend/`.
- `frontend/index.html`: single file, Chart.js from a CDN, light/dark via CSS variables. Sleep hero, stat row, charts, workouts table, range select (7/14/28 days), Sync and Export buttons.
- `scripts/explore.py`, `docs/data-notes.md`, `PLAN.md`, `.env.example`.

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
- Not available from the Personal API: VO2 max, training readiness, training status, recovery time, performance condition (checked via dumps, activity details, and probes). Substitutes: `activityTrainingLoad`, training effect, fitness age, resting HR, HRV, sleep score, body battery.
- Sleep SpO2 can be null. Garmin's scores (stress, body battery, sleep stages) are wrist-based estimates, not clinical values.

## AI insights design (not built yet)
- Compute the statistics in code (own baselines, 7-day vs earlier averages, sample sizes). The model only writes the narrative from that compact summary.
- Prompt rules: state confidence and sample size, say when data is too thin, no causal claims (no context data like food, alcohol, illness, mood), no medical advice.
- Put it in `backend/insights.py` with a provider switch (`gemini` | `anthropic`) using the same prompt so outputs can be compared. Cache results; add a "regenerate" action.

## Next steps
1. Find out how far back each endpoint's history goes (probe older 28-day windows) and decide how much to backfill.
2. Scheduled daily sync (the Sync button and CLI exist; nothing runs automatically yet).
3. `insights.py` plus an insights panel in the UI.
4. Tests using sanitized fixtures from `raw_dumps/` for the parsers in `sync.py` and the stats in `export.py`.
5. Error states in the UI (expired key, Garmin down), README.

## Decisions already made
- uv for the environment; Python + FastAPI + SQLite + plain HTML/JS.
- Garmin's official client, not the third-party `gcpapi-sync` (which would add automatic range-splitting and retries but is a single-maintainer package).
- v1 metrics: sleep, resting HR, HRV, steps, stress, body battery, training load, workouts.
- Start with 28 days of data; insights explored first by attaching the export to a chat, then automated.