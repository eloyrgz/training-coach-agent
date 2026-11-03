"""Compact, LLM-friendly analysis of one recorded activity from Intervals.icu streams."""
import math
import re

from intervals_icu_client import IntervalsClient

FOOT_SPORTS = {"Run", "TrailRun", "VirtualRun", "Walk", "Hike"}
STREAM_TYPES = ["time", "heartrate", "velocity_smooth", "distance", "altitude", "cadence"]
MAX_SPLITS = 25
MAX_INTERVALS = 20
# Matches Intervals.icu elevation totals on Suunto barometric data within a few metres.
CLIMB_THRESHOLD_M = 2.0
_ACTIVITY_ID_RE = re.compile(r"^i?\d+$")


def is_valid_activity_id(activity_id: str) -> bool:
    return bool(_ACTIVITY_ID_RE.match(activity_id or ""))


def fetch_activity_data(client: IntervalsClient, activity_id: str) -> tuple[dict, dict, dict]:
    """Return (detail, streams_by_type, intervals) for one activity."""
    detail = client.get_activity(activity_id)
    raw_streams = client.get_activity_streams(activity_id, STREAM_TYPES)
    intervals = client.get_activity_intervals(activity_id)
    streams = {s.get("type"): s.get("data") or [] for s in raw_streams or []}
    return detail or {}, streams, intervals or {}


def _round(value, digits=1):
    return round(value, digits) if isinstance(value, (int, float)) else None


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _pace(speed_mps):
    """m/s -> 'm:ss/km'."""
    if not speed_mps or speed_mps <= 0:
        return None
    seconds = round(1000 / speed_mps)
    return f"{seconds // 60}:{seconds % 60:02d}/km"


def _duration(seconds):
    if seconds is None:
        return None
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def _speed_fields(speed_mps, foot_sport):
    return {"pace": _pace(speed_mps)} if foot_sport else {"speed_kmh": _round(speed_mps * 3.6) if speed_mps else None}


def _cadence(value, foot_sport):
    # Intervals.icu reports running cadence per leg; steps per minute is double.
    if value is None:
        return None
    return round(value * 2) if foot_sport else round(value)


def _climb_increments(altitudes):
    """Per-sample (gain, loss) with hysteresis so altimeter noise is not counted as climbing."""
    increments = [(0.0, 0.0)] * len(altitudes)
    reference = None
    for i, altitude in enumerate(altitudes):
        if altitude is None:
            continue
        if reference is None:
            reference = altitude
            continue
        delta = altitude - reference
        if abs(delta) >= CLIMB_THRESHOLD_M:
            increments[i] = (max(delta, 0.0), max(-delta, 0.0))
            reference = altitude
    return increments


def _zone_distribution(boundaries, times, label):
    if not times:
        return None
    total = sum(times) or 1
    zones = []
    for index, seconds in enumerate(times):
        zone = {"zone": f"Z{index + 1}", "time": _duration(seconds), "pct": round(100 * seconds / total, 1)}
        if boundaries and index < len(boundaries):
            zone[f"upper_{label}"] = boundaries[index]
        zones.append(zone)
    return zones


def _segments(streams: dict, foot_sport: bool) -> tuple[str, list[dict]]:
    """Split the activity by distance when available, otherwise by time."""
    time_s = streams.get("time") or []
    distance = streams.get("distance") or []
    heartrate = streams.get("heartrate") or []
    cadence = streams.get("cadence") or []
    altitude = streams.get("altitude") or []
    n = len(time_s)
    if n < 2:
        return "none", []

    def at(series, i):
        return series[i] if i < len(series) else None

    total_distance = max((d for d in distance if d is not None), default=0)
    by_distance = total_distance >= 1000
    if by_distance:
        unit = max(1, math.ceil(total_distance / 1000 / MAX_SPLITS)) * 1000
        position = []
        last = 0.0
        for i in range(n):
            last = at(distance, i) if at(distance, i) is not None else last
            position.append(last)
    else:
        unit = max(300, math.ceil(time_s[-1] / MAX_SPLITS / 60) * 60)
        position = list(time_s)

    climb = _climb_increments(altitude) if altitude else []
    has_climb = any(g or l for g, l in climb)

    buckets: dict[int, list[int]] = {}
    for i in range(n):
        buckets.setdefault(int(position[i] // unit), []).append(i)

    rows = []
    for key in sorted(buckets):
        idx = buckets[key]
        first, last_i = idx[0], idx[-1]
        elapsed = (time_s[last_i] - time_s[first]) + 1
        row = {
            "segment": key + 1,
            "duration": _duration(elapsed),
            "avg_hr": round(hr) if (hr := _mean(at(heartrate, i) for i in idx)) is not None else None,
            "max_hr": max((at(heartrate, i) for i in idx if at(heartrate, i) is not None), default=None),
        }
        if by_distance:
            covered = position[last_i] - (position[first - 1] if first > 0 else 0)
            row["distance_m"] = round(covered)
            row.update(_speed_fields(covered / elapsed if elapsed else None, foot_sport))
        cadence_avg = _mean(c for c in (at(cadence, i) for i in idx) if c)
        if cadence_avg:
            row["cadence_spm" if foot_sport else "cadence_rpm"] = _cadence(cadence_avg, foot_sport)
        if has_climb:
            row["elev_gain_m"] = round(sum(climb[i][0] for i in idx if i < len(climb)))
            row["elev_loss_m"] = round(sum(climb[i][1] for i in idx if i < len(climb)))
        rows.append(row)
    return ("km" if unit == 1000 else f"{unit // 1000} km") if by_distance else f"{unit // 60} min", rows


def _halves(streams: dict, foot_sport: bool) -> dict | None:
    """Compare first and second half by time: HR drift and aerobic decoupling (Pa:HR)."""
    heartrate = streams.get("heartrate") or []
    speed = streams.get("velocity_smooth") or []
    if len(heartrate) < 120:
        return None
    middle = len(heartrate) // 2
    hr_first, hr_second = _mean(heartrate[:middle]), _mean(heartrate[middle:])
    if not hr_first or not hr_second:
        return None
    result = {
        "first_half_avg_hr": round(hr_first),
        "second_half_avg_hr": round(hr_second),
        "hr_drift_pct": round(100 * (hr_second - hr_first) / hr_first, 1),
    }
    if any(speed):
        speed_first, speed_second = _mean(speed[:middle]), _mean(speed[middle:])
        if speed_first and speed_second:
            result.update({
                f"first_half_{k}": v for k, v in _speed_fields(speed_first, foot_sport).items()
            })
            result.update({
                f"second_half_{k}": v for k, v in _speed_fields(speed_second, foot_sport).items()
            })
            efficiency_first = speed_first / hr_first
            efficiency_second = speed_second / hr_second
            result["aerobic_decoupling_pct"] = round(100 * (efficiency_first - efficiency_second) / efficiency_first, 1)
    return result


def _intervals(intervals: dict, foot_sport: bool) -> list[dict]:
    rows = []
    for item in (intervals.get("icu_intervals") or [])[:MAX_INTERVALS]:
        row = {
            "type": item.get("type"),
            "label": item.get("label"),
            "duration": _duration(item.get("moving_time") or item.get("elapsed_time")),
            "distance_m": _round(item.get("distance"), 0),
            **_speed_fields(item.get("average_speed"), foot_sport),
            "avg_hr": item.get("average_heartrate"),
            "max_hr": item.get("max_heartrate"),
            "zone": item.get("zone"),
            "elev_gain_m": _round(item.get("total_elevation_gain"), 0),
            "avg_gradient_pct": _round((item.get("average_gradient") or 0) * 100),
        }
        cadence = _cadence(item.get("average_cadence"), foot_sport)
        if cadence:
            row["cadence_spm" if foot_sport else "cadence_rpm"] = cadence
        rows.append({k: v for k, v in row.items() if v is not None})
    return rows


def summarize_activity(detail: dict, streams: dict, intervals: dict) -> dict:
    """Build a compact analysis from raw Intervals.icu responses."""
    sport = detail.get("type")
    foot_sport = sport in FOOT_SPORTS
    hrr = detail.get("icu_hrr") or {}
    split_unit, splits = _segments(streams, foot_sport)
    overview = {
        "activity_id": detail.get("id"),
        "date": detail.get("start_date_local"),
        "name": detail.get("name"),
        "type": sport,
        "device": detail.get("device_name"),
        "distance_km": _round((detail.get("distance") or 0) / 1000, 2) or None,
        "moving_time": _duration(detail.get("moving_time")),
        "elapsed_time": _duration(detail.get("elapsed_time")),
        **_speed_fields(detail.get("average_speed"), foot_sport),
        "avg_hr": detail.get("average_heartrate"),
        "max_hr": detail.get("max_heartrate"),
        "athlete_max_hr": detail.get("athlete_max_hr"),
        "lthr": detail.get("lthr"),
        "elev_gain_m": _round(detail.get("total_elevation_gain"), 0),
        "elev_loss_m": _round(detail.get("total_elevation_loss"), 0),
        "avg_temp_c": _round(detail.get("average_temp")),
        "training_load": detail.get("icu_training_load"),
        "intensity_pct": _round(detail.get("icu_intensity")),
        "trimp": _round(detail.get("trimp")),
        "hr_recovery_60s_bpm": hrr.get("hrr"),
        "rpe": detail.get("icu_rpe"),
        "feel": detail.get("feel"),
        "interval_summary": detail.get("interval_summary"),
    }
    cadence = _cadence(detail.get("average_cadence"), foot_sport)
    if cadence:
        overview["avg_cadence_spm" if foot_sport else "avg_cadence_rpm"] = cadence
    if foot_sport and detail.get("average_stride"):
        overview["avg_stride_m"] = _round(detail.get("average_stride"), 2)

    result = {
        "overview": {k: v for k, v in overview.items() if v is not None},
        "hr_zones": _zone_distribution(detail.get("icu_hr_zones"), detail.get("icu_hr_zone_times"), "bpm"),
        "pace_zones": _zone_distribution(
            detail.get("pace_zones"), detail.get("pace_zone_times"), "pct_threshold_pace"
        ) if foot_sport else None,
        "halves": _halves(streams, foot_sport),
        "split_unit": split_unit,
        "splits": splits,
        "intervals": _intervals(intervals, foot_sport),
        "available_streams": sorted(k for k, v in streams.items() if any(x is not None for x in v)),
    }
    if sport == "TrailRun":
        result["note"] = "Trail terrain distorts pace; judge effort with HR, gradient and elevation per split."
    return {k: v for k, v in result.items() if v not in (None, [], {})}
