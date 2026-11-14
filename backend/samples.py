"""A run's per-record series (stored by sync in run_samples), resampled for the export, plus simple HR anomaly flags."""
from statistics import median

SAMPLE_INTERVAL_S = 10  # bucket size for the exported samples and for the anomaly rules
SAMPLE_DAYS = 14  # samples only for runs in the last N days, so long ranges don't blow up the file
MIN_SPEED_MPS = 0.5  # slower than this (33 min/km) is standing still: pace is null

SPIKE_RISE_BPM = 12  # spike: HR rises at least this much...
SPIKE_WINDOW_S = 30  # ...within this many seconds...
SPIKE_MAX_PACE_CHANGE = 0.05  # ...while pace changes by less than this fraction...
SPIKE_MAX_GAIN_M = 3  # ...and the climb over that window is less than this

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


def _spikes(rs: dict, interval: int) -> list:
    hr, pace, elev = rs["hr"], rs["pace_min_per_km"], rs["elev_m"]
    out = []
    for i in range(len(hr)):
        if hr[i] is None or pace[i] is None:
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


def _merge(segments: list) -> list:
    """Merge overlapping segments of the same type."""
    out = []
    for s in sorted(segments, key=lambda s: (s["type"], s["start_s"])):
        last = out[-1] if out else None
        if last and last["type"] == s["type"] and s["start_s"] <= last["end_s"]:
            last["end_s"] = max(last["end_s"], s["end_s"])
            last["peak_hr"] = max(last["peak_hr"], s["peak_hr"])
        else:
            out.append(dict(s))
    return [{**s, "peak_hr": round(s["peak_hr"])} for s in sorted(out, key=lambda s: s["start_s"])]


def hr_anomalies(rs: dict, interval: int = SAMPLE_INTERVAL_S) -> list:
    """Segments flagged by simple rules on the resampled series. Heuristics, not diagnoses."""
    return _merge(_spikes(rs, interval) + _cadence_locks(rs, interval))
