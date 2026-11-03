# Garmin Dashboard: Plan

Local web dashboard for my Garmin watch metrics, with AI insights (Gemini and/or Claude).
Status as of 2026-11-03: dashboard v1 works locally, with data from 2026-10-15 onwards. Syncing is manual. Next: confirm history depth, then the daily tag log and baselines. Insights wait until there are 6 to 8 weeks of data.

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
- VO2 max is only in activity files (undocumented field, matches the watch).
- Not available: training readiness, training status, recovery time, acute and chronic load, run temperature.
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
- [ ] Findings in `docs/data-notes.md` (still a stub)
- [x] v1 metrics chosen
- [ ] How far back does each endpoint's history go? A 28-day sync on 2026-11-01 returned nothing before 2026-10-15, so one older-window probe should settle it

### 3. Data layer
- [x] `backend/db.py`: SQLite schema (daily, activities, meta)
- [x] `backend/sync.py`: 28-day chunking, per-endpoint error isolation
- [x] Manual sync: button, `POST /api/sync`, `python -m backend.sync` (no scheduled job, by choice)
- [x] Stale-data banner and failed logins shown in the dashboard
- [ ] Longer backfill once history depth is known

### 4. Dashboard v1
- [x] Sleep hero, stat row (resting HR, HRV, steps, stress, body battery)
- [x] Charts: sleep stages + score, resting HR + HRV, steps, stress + body battery, training load
- [x] Workouts table
- [x] Run pop-up: summary stats, time in each zone, km splits
- [x] Stress pop-up: time in each band, body battery curve (the API has no stress readings through the day)
- [x] Range select: 7 / 14 / 28 days (extend to 90 after backfill)
- [x] Export for Claude button (`GET /api/export`)
- [ ] Stat row: show each value against my own baseline
- [ ] Daily tag log (e.g. alcohol, illness, travel, late meal), included in the export. Start early: tags can't be collected reliably after the fact

### 5. AI insights
Automate only once there are 6 to 8 weeks of data (around 2026-11-26 to 2026-12-10). Until then, most output would be "too thin to say".
- [x] Manual route: export JSON attached to a Claude chat (works today)
- [x] Export: detailed running fields (elevation, GAP, splits, HR zones, form, drift), `stats.runs`, `readiness` (VO2 max), new caveats
- [ ] Check whether activity file message 140 field 9 is recovery minutes, against the watch
- [ ] `backend/insights.py`: code computes stats, model writes narrative; provider switch `gemini` | `anthropic`, same prompt
- [ ] Prompt rules: state confidence and sample size, flag thin data, no causal claims, no medical advice
- [ ] Cache result; "regenerate" button; insights panel in the UI
- [ ] Decide which insights are worth automating (from the manual route)

### 6. Polish
- [x] Smoke and export tests (`tests/test_app.py`, temporary database, no Garmin calls)
- [ ] Tests with sanitized fixtures (parsers in `sync.py`, stats in `export.py`)
- [x] Sync failures shown in the UI (expired key, Garmin down)
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
- 2026-11-03: sync runs chunks newest first and isolates errors per chunk; a day stays partial until fetched after it ended; last night's sleep and HRV now count in stats.
- 2026-11-03: dropped the scheduled sync in favour of syncing manually; kept the out-of-date banner (over 36 hours since the last successful sync).
- 2026-11-03: added the daily tag log and baselines to the plan; insights wait for 6 to 8 weeks of data.
- 2026-11-03: added detailed running fields to the export, using the activity file for laps (new dependency: `garmin-fit-sdk`). Sleep SpO2 is null since 2026-10-28 because Garmin returns none, not because of the parser. Added VO2 max from an undocumented activity file field, after matching it to the watch. Export went from 15,025 to 27,100 bytes on the 20-day range.
