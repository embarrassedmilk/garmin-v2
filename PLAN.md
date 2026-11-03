# Garmin Dashboard: Plan

Local web dashboard for my Garmin watch metrics, with AI insights (Gemini and/or Claude).
Status as of 2026-11-02: phases 1 to 4 work locally with 28 days of data. Next: history depth, scheduled sync, insights.

## Stack (confirmed)
- Backend: Python (managed with uv) + FastAPI
- Storage: SQLite (`data/garmin.db`, gitignored)
- Frontend: plain HTML/JS + Chart.js (CDN), served by the backend
- Garmin access: Garmin's official Python client (`garmin_personal_api`)
- Secrets: `.env` (never committed)

## Architecture
```
Garmin Personal API -> sync.py -> SQLite -> FastAPI -> local dashboard
                                      |
                                      +-> /api/export (identifier-free JSON, for attaching to an AI chat)
                                      +-> insights.py (planned): stats computed in code -> Gemini or Claude -> narrative
```

## Known facts about the Garmin Personal API
- Self-service key from Garmin Connect; shown once; expires (30/90/180/365 days). Set a renewal reminder.
- Key is exchanged for an access token valid 24h (the official client caches it). No refresh tokens.
- Read-only. Base URL: https://personal.connectapi.garmin.com
- Per-request limits: 28 days (steps, stress), 31 days (daily summaries, sleep summaries, body battery), 365 days (HRV), ~45 activities per page. Daily summaries over ~90 days fail with HTTP 500. Chunk at 28 days.
- Not available: VO2 max, training readiness, training status, recovery time, performance condition.
- Details and quirks: `docs/data-notes.md`
- Docs: https://developer.garmin.com/gc-developer-program/personal/personal-api
- Swagger (needs a browser, JavaScript): https://developer.garmin.com/gc-developer-program/personal/swagger-docs/

## Phases (tick as we go)
### 1. Setup
- [x] `uv init --bare`, `uv add fastapi uvicorn python-dotenv garmin-connect-personal-api-python`
- [x] `.env` with `GARMIN_API_KEY` (and `GEMINI_API_KEY`)
- [x] `git init`, confirm `.env`, `data/` and `raw_dumps/` are ignored
- [x] FastAPI server runs

### 2. Garmin exploration
- [x] `scripts/explore.py` using the official client; raw dumps in `raw_dumps/`
- [x] Findings in `docs/data-notes.md`
- [x] v1 metrics chosen
- [ ] How far back does each endpoint's history go? (probe older 28-day windows; decide backfill size)

### 3. Data layer
- [x] `backend/db.py`: SQLite schema (daily, activities, meta)
- [x] `backend/sync.py`: 28-day chunking, per-endpoint error isolation
- [x] Manual sync: button, `POST /api/sync`, `python -m backend.sync`
- [ ] Scheduled daily sync (re-sync today and yesterday; today's row is partial)
- [ ] Longer backfill once history depth is known

### 4. Dashboard v1
- [x] Sleep hero, stat row (resting HR, HRV, steps, stress, body battery)
- [x] Charts: sleep stages + score, resting HR + HRV, steps, stress + body battery, training load
- [x] Workouts table
- [x] Range select: 7 / 14 / 28 days (extend to 90 after backfill)
- [x] Export for Claude button (`GET /api/export`)

### 5. AI insights
- [x] Manual route: export JSON attached to a Claude chat (works today)
- [ ] `backend/insights.py`: code computes stats, model writes narrative; provider switch `gemini` | `anthropic`, same prompt
- [ ] Prompt rules: state confidence and sample size, flag thin data, no causal claims, no medical advice
- [ ] Cache result; "regenerate" button; insights panel in the UI
- [ ] Decide which insights are worth automating (from the manual route)

### 6. Polish
- [x] Smoke and export tests (`tests/test_app.py`, temporary database, no Garmin calls)
- [ ] Tests with sanitized fixtures (parsers in `sync.py`, stats in `export.py`)
- [ ] Error states (expired key, Garmin down, no data)
- [ ] Loading states
- [ ] README

## Open decisions
- Which Gemini / Claude model for insights, and what each costs (API credits are separate from a Claude subscription).
- How much history to backfill.
- Which insights are worth showing every week.

## Notes / log
- 2026-10-31: first explore run; all 15 endpoints worked. Chose Garmin's official client over third-party `gcpapi-sync`.
- 2026-10-31: dashboard v1 and export button built in chat.
- 2026-11-02: moving to Claude Code; `CLAUDE.md` rewritten.
- 2026-11-03: fixed the `sync.py` import; removed an all-NULL `daily` row that emptied workouts in the 28-day export; `daily.date` is now NOT NULL; added `.gitignore` and tests.