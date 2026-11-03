"""Explore the Garmin Connect Personal API with Garmin's official Python client
(https://github.com/garmin/connect-personal-api-python) and dump raw responses to raw_dumps/.

Usage:
    uv run python scripts/explore.py                  # all presets, last 7 days
    uv run python scripts/explore.py --days 28
    uv run python scripts/explore.py --only sleep hrv # presets whose name contains any of these
    uv run python scripts/explore.py --path /some/path --param key=value

Notes:
- Reads GARMIN_API_KEY from .env. The key and token are never printed or saved by this script.
  (The library itself caches the bearer token at ~/.config/garmin/connect-personal-api-bearer-token.json.)
- raw_dumps/ holds your health data and is gitignored.
"""

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
from garmin_personal_api import GarminPersonalApiClient, PersonalApiAuthenticator
from garmin_personal_api.errors import GarminPersonalApiError, HttpError

load_dotenv()

OUT_DIR = Path(__file__).resolve().parent.parent / "raw_dumps"


def build_presets(days: int):
    """(name, callable(client)) pairs."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    start = today - timedelta(days=days)
    return [
        ("user_settings", lambda c: c.get_user_profile_settings()),
        ("daily_summaries", lambda c: c.get_daily_summaries(start, today)),
        ("steps", lambda c: c.get_daily_steps(start, today)),
        ("stress", lambda c: c.get_daily_stress(start, today)),
        ("sleep", lambda c: c.get_daily_sleep(today)),
        ("sleep_summaries", lambda c: c.get_daily_sleep_summaries(start, today)),
        ("hrv", lambda c: c.get_daily_hrv(yesterday)),
        ("hrv_summaries", lambda c: c.get_daily_hrv_summaries(start, today)),
        ("heart_rate", lambda c: c.get_daily_heart_rate(yesterday)),
        ("spo2", lambda c: c.get_daily_spo2(yesterday)),
        ("body_battery", lambda c: c.get_daily_body_battery(start, today)),
        ("weight", lambda c: c.get_weight_measurements(start, today)),
        ("fitness_age", lambda c: c.get_fitness_age(yesterday)),
        ("heart_rate_zones", lambda c: c.get_heart_rate_zones()),
        ("activities", lambda c: c.search_activities(start=0, limit=5)),
    ]


def describe(data) -> str:
    if data is None:
        return "empty response"
    if isinstance(data, dict):
        keys = list(data.keys())
        return f"object, {len(keys)} keys: {keys[:12]}{' ...' if len(keys) > 12 else ''}"
    if isinstance(data, list):
        first = f", first item keys: {list(data[0].keys())[:10]}" if data and isinstance(data[0], dict) else ""
        return f"list, {len(data)} items{first}"
    return type(data).__name__


def run_one(name: str, call) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    try:
        data = call()
    except HttpError as e:
        (OUT_DIR / f"{name}.error.txt").write_text(f"HTTP {e.status_code}\n{e.response_text}")
        print(f"[ERR] {name}: HTTP {e.status_code} {str(e.response_text)[:150]!r}")
        return
    except Exception as e:  # network errors, bad JSON, etc.
        print(f"[ERR] {name}: {type(e).__name__}: {e}")
        return
    (OUT_DIR / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"[ok ] {name}: {describe(data)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=7, help="range length for range endpoints (default 7)")
    parser.add_argument("--only", nargs="+", help="only presets whose name contains one of these")
    parser.add_argument("--path", help="call a custom GET path instead of the presets")
    parser.add_argument("--param", action="append", default=[], help="query param k=v (repeatable)")
    args = parser.parse_args()

    api_key = os.getenv("GARMIN_API_KEY")
    if not api_key:
        sys.exit("GARMIN_API_KEY is missing. Put it in .env (see .env.example).")

    try:
        with PersonalApiAuthenticator() as authenticator:
            token = authenticator.obtain_token(api_key)
    except GarminPersonalApiError as e:
        sys.exit(f"Could not get a token: {type(e).__name__}: {e}")
    print("Token obtained.")

    with GarminPersonalApiClient(token.access_token) as client:
        if args.path:
            params = dict(p.split("=", 1) for p in args.param)
            run_one("custom", lambda: client.get(args.path, params=params))
            return
        for name, fn in build_presets(args.days):
            if args.only and not any(o in name for o in args.only):
                continue
            run_one(name, lambda fn=fn: fn(client))


if __name__ == "__main__":
    main()