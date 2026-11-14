"""A run's per-record series (stored by sync in run_samples), resampled for the export, plus simple HR anomaly flags."""
from statistics import median

SAMPLE_INTERVAL_S = 10  # bucket size for the exported samples and for the anomaly rules
SAMPLE_DAYS = 14  # samples only for runs in the last N days, so long ranges don't blow up the file
MIN_SPEED_MPS = 0.5  # slower than this (33 min/km) is standing still: pace is null

SPIKE_RISE_BPM = 12  # spike: HR rises at least this much...
SPIKE_WINDOW_S = 30  # ...within this many seconds...
SPIKE_MAX_PACE_CHANGE = 0.05  # ...while pace changes by less than this fraction...
SPIKE_MAX_GAIN_M = 3  # ...and the climb over that window is less than this
SPIKE_IGNORE_START_S = 120  # no spike flags starting this early in the run (HR is still rising from rest)...
SPIKE_IGNORE_AFTER_GAP_S = 90  # ...or this soon after a gap (null HR buckets: a pause or no record)

DROP_FALL_BPM = 12  # sudden drop: HR falls at least this much...
DROP_WINDOW_S = 20  # ...within this many seconds, with no gap in between...
DROP_MAX_PACE_CHANGE = 0.05  # ...while pace changes by less than this fraction

EPISODE_MAX_S = 300  # a spike or cadence lock followed by a sudden drop this soon, with no gap, is one episode

LOCK_MAX_GAP_BPM = 3  # cadence lock: |HR - cadence| at most this...
LOCK_MIN_S = 30  # ...for at least this long...
LOCK_ABOVE_MEDIAN_BPM = 10  # ...with HR at least this far above...
LOCK_BASELINE_S = 120  # ...the median HR of this long before the segment


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def resample(series: dict, interval: int = SAMPLE_INTERVAL_S) -> dict:
    """Average each channel within fixed buckets of elapsed time. Buckets with no records (pauses) are None,
    so every channel lines up with t_s. Pace comes from the average speed, not an average of paces."""
    t = series.get("t") or []
    n = t[-1] // interval + 1 if t else 0
    buckets = {k: [[] for _ in range(n)] for k in ("hr", "cad", "speed", "alt")}
    for i, ts in enumerate(t):
        for k, b in buckets.items():
            b[ts // interval].append(series[k][i])
    speed = [_mean(b) for b in buckets["speed"]]
    return {
        "t_s": [i * interval for i in range(n)],
        "hr": [_mean(b) for b in buckets["hr"]],
        "cadence_spm": [_mean(b) for b in buckets["cad"]],
        "pace_min_per_km": [1000 / 60 / s if s and s >= MIN_SPEED_MPS else None for s in speed],
        "elev_m": [_mean(b) for b in buckets["alt"]],
    }


def export_samples(rs: dict, interval: int = SAMPLE_INTERVAL_S) -> dict:
    """Columnar and compact: whole numbers for HR, cadence and elevation, pace to 2 decimals."""
    def whole(vals):
        return [None if v is None else round(v) for v in vals]
    return {"interval_s": interval, "t_s": rs["t_s"], "hr": whole(rs["hr"]), "cadence_spm": whole(rs["cadence_spm"]),
            "pace_min_per_km": [None if v is None else round(v, 2) for v in rs["pace_min_per_km"]],
            "elev_m": whole(rs["elev_m"])}


def _resumed_at(hr: list, interval: int) -> list:
    """For each bucket, the time HR data last resumed: 0 at the start, else the first bucket after a gap."""
    out, since = [], 0
    for i, h in enumerate(hr):
        if h is None:
            since = (i + 1) * interval
        out.append(since)
    return out


def _spikes(rs: dict, interval: int) -> list:
    hr, pace, elev = rs["hr"], rs["pace_min_per_km"], rs["elev_m"]
    resumed = _resumed_at(hr, interval)
    out = []
    for i in range(len(hr)):
        if hr[i] is None or pace[i] is None:
            continue
        start = i * interval
        if start < SPIKE_IGNORE_START_S or (resumed[i] > 0 and start - resumed[i] < SPIKE_IGNORE_AFTER_GAP_S):
            continue
        for j in range(i + 1, min(i + SPIKE_WINDOW_S // interval, len(hr) - 1) + 1):
            if hr[j] is None or pace[j] is None or hr[j] - hr[i] < SPIKE_RISE_BPM:
                continue
            if abs(pace[j] - pace[i]) / pace[i] >= SPIKE_MAX_PACE_CHANGE:
                continue
            heights = [e for e in elev[i:j + 1] if e is not None]
            if sum(max(b - a, 0) for a, b in zip(heights, heights[1:])) >= SPIKE_MAX_GAIN_M:
                continue
            out.append({"start_s": i * interval, "end_s": (j + 1) * interval, "peak_hr": max(h for h in hr[i:j + 1] if h is not None),
                        "type": "spike"})
    return out


def _cadence_locks(rs: dict, interval: int) -> list:
    hr, cad = rs["hr"], rs["cadence_spm"]
    look = LOCK_BASELINE_S // interval
    out, i = [], 0
    while i < len(hr):
        before = [h for h in hr[max(i - look, 0):i] if h is not None]
        if not before:
            i += 1
            continue
        floor = median(before) + LOCK_ABOVE_MEDIAN_BPM
        j = i
        while j < len(hr) and hr[j] is not None and cad[j] is not None \
                and abs(hr[j] - cad[j]) <= LOCK_MAX_GAP_BPM and hr[j] >= floor:
            j += 1
        if (j - i) * interval >= LOCK_MIN_S:
            out.append({"start_s": i * interval, "end_s": j * interval, "peak_hr": max(hr[i:j]),
                        "type": "cadence_lock_suspect"})
            i = j
        else:
            i += 1
    return out


def _drops(rs: dict, interval: int) -> list:
    """HR falling faster than it really does at a steady pace. peak_hr is HR just before, low_hr just after."""
    hr, pace = rs["hr"], rs["pace_min_per_km"]
    out = []
    for i in range(len(hr)):
        if hr[i] is None or pace[i] is None:
            continue
        for j in range(i + 1, min(i + DROP_WINDOW_S // interval, len(hr) - 1) + 1):
            if hr[j] is None:
                break  # a gap in between: not a drop while running
            if pace[j] is None or hr[i] - hr[j] < DROP_FALL_BPM:
                continue
            if abs(pace[j] - pace[i]) / pace[i] >= DROP_MAX_PACE_CHANGE:
                continue
            out.append({"start_s": i * interval, "end_s": (j + 1) * interval, "peak_hr": hr[i], "low_hr": hr[j],
                        "type": "sudden_drop"})
    return out


def _merge(segments: list) -> list:
    """Merge overlapping segments of the same type."""
    out = []
    for s in sorted(segments, key=lambda s: (s["type"], s["start_s"])):
        last = out[-1] if out else None
        if last and last["type"] == s["type"] and s["start_s"] <= last["end_s"]:
            last["end_s"] = max(last["end_s"], s["end_s"])
            last["peak_hr"] = max(last["peak_hr"], s["peak_hr"])
            if "low_hr" in s:
                last["low_hr"] = min(last["low_hr"], s["low_hr"])
        else:
            out.append(dict(s))
    return [{**s, **{k: round(s[k]) for k in ("peak_hr", "low_hr") if k in s}}
            for s in sorted(out, key=lambda s: (s["start_s"], s["end_s"]))]


def _link_episodes(segments: list, hr: list, interval: int) -> list:
    """Give a spike or cadence lock and the sudden drops that follow it within EPISODE_MAX_S, with no gap in
    between, the same episode number. Unlinked segments get none."""
    group = list(range(len(segments)))

    def root(k):
        while group[k] != k:
            k = group[k]
        return k

    for a, rise in enumerate(segments):
        if rise["type"] == "sudden_drop":
            continue
        for b, drop in enumerate(segments):
            if drop["type"] != "sudden_drop" or not rise["start_s"] < drop["start_s"] <= rise["end_s"] + EPISODE_MAX_S:
                continue
            if None in hr[rise["start_s"] // interval:drop["end_s"] // interval]:
                continue
            group[root(b)] = root(a)
    roots = [root(k) for k in range(len(segments))]
    linked = sorted({r for r in roots if roots.count(r) > 1}, key=roots.index)
    return [{**s, "episode": linked.index(r) + 1} if r in linked else s for s, r in zip(segments, roots)]


def hr_anomalies(rs: dict, interval: int = SAMPLE_INTERVAL_S) -> list:
    """Segments flagged by simple rules on the resampled series. Heuristics, not diagnoses."""
    found = _merge(_spikes(rs, interval) + _cadence_locks(rs, interval) + _drops(rs, interval))
    return _link_episodes(found, rs["hr"], interval)
