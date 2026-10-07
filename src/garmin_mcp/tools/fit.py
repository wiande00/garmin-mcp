"""The activity's original FIT file, decoded with Garmin's own FIT SDK, and what can be
worked out from it that Garmin Connect doesn't show directly: climbs and VAM, time on
each grade, aerobic decoupling, the power-duration curve, variability index, R-R
heart-rate variability, running and cycling dynamics, Di2 gear use.

Every analysis takes the decoded messages, so it can be tested without a real file.
"""

from __future__ import annotations

import gzip
import io
import math
import zipfile
from collections import OrderedDict, defaultdict
from datetime import datetime
from itertools import pairwise
from typing import Any

from .. import units
from ..client import Ctx
from ..errors import InputError
from ..shape import dumps, first

ANALYSES = (
    "summary",
    "laps",
    "climbs",
    "grades",
    "hr_drift",
    "power_curve",
    "variability",
    "hrv",
    "running_dynamics",
    "cycling_dynamics",
    "di2",
    "temperature",
)
CURVE_SECONDS = (5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600)
GRADE_BINS = ((-100, -8), (-8, -4), (-4, -1), (-1, 1), (1, 4), (4, 8), (8, 100))
_FIT_CACHE: OrderedDict[str, bytes] = OrderedDict()
_FIT_CACHE_SIZE = 6


# ------------------------------------------------------------------ getting the file


def unpack(data: bytes) -> bytes:
    """Garmin's 'original' download is a zip holding the .fit; sometimes gzip; sometimes bare."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".fit")] or z.namelist()
            if not names:
                raise InputError("The downloaded archive is empty.")
            return z.read(names[0])
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def fit_file(ctx: Ctx, activity_id: int) -> bytes:
    key = str(activity_id)
    if key in _FIT_CACHE:
        _FIT_CACHE.move_to_end(key)
        return _FIT_CACHE[key]
    from garminconnect import Garmin

    raw = ctx.g.download_activity(key, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
    data = unpack(raw)
    _FIT_CACHE[key] = data
    while len(_FIT_CACHE) > _FIT_CACHE_SIZE:
        _FIT_CACHE.popitem(last=False)
    return data


def decode(data: bytes) -> dict[str, list[dict]]:
    from garmin_fit_sdk import Decoder, Stream

    decoder = Decoder(Stream.from_byte_array(bytearray(data)))
    if not decoder.is_fit():
        raise InputError("That download is not a FIT file (manual activities have none).")
    messages, errors = decoder.read(convert_datetimes_to_dates=True, merge_heart_rates=True)
    if errors and not messages:
        raise InputError(f"The FIT file could not be read: {errors[0]}")
    return messages


def messages_for(ctx: Ctx, activity_id: int) -> dict[str, list[dict]]:
    return decode(fit_file(ctx, activity_id))


# ------------------------------------------------------------------ helpers


def _secs(t: Any) -> float | None:
    return t.timestamp() if isinstance(t, datetime) else None


def _records(msgs: dict) -> list[dict]:
    return [r for r in msgs.get("record_mesgs") or [] if isinstance(r.get("timestamp"), datetime)]


def _alt(r: dict):
    return first(r.get("enhanced_altitude"), r.get("altitude"))


def _speed(r: dict):
    return first(r.get("enhanced_speed"), r.get("speed"))


def _smooth(values: list[float], window: int) -> list[float]:
    """Centered moving average."""
    if window <= 1 or len(values) < 3:
        return values
    half, out = window // 2, []
    for i in range(len(values)):
        chunk = values[max(0, i - half) : i + half + 1]
        out.append(sum(chunk) / len(chunk))
    return out


def per_second(records: list[dict], field: str, max_gap: int = 5) -> list[float]:
    """Resample a field to one value per second; gaps longer than max_gap count as 0
    (paused), shorter gaps carry the last value forward."""
    points = [(int(_secs(r["timestamp"])), r.get(field)) for r in records if r.get(field) is not None]
    if not points:
        return []
    out: list[float] = []
    following = points[1:] + [(points[-1][0] + 1, None)]
    for (t0, v0), (t1, _) in zip(points, following, strict=True):
        gap = t1 - t0
        out.extend([float(v0)] * min(gap, max_gap))
        if gap > max_gap:
            out.extend([0.0] * (gap - max_gap))
    return out


def best_averages(series: list[float], durations=CURVE_SECONDS) -> dict[int, float]:
    if not series:
        return {}
    cum = [0.0]
    for v in series:
        cum.append(cum[-1] + v)
    best = {}
    for d in durations:
        if d <= len(series):
            best[d] = max(cum[i + d] - cum[i] for i in range(len(series) - d + 1)) / d
    return best


def _label(seconds: int) -> str:
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}min"


# ------------------------------------------------------------------ analyses


def summary(msgs: dict) -> dict:
    s = (msgs.get("session_mesgs") or [{}])[0]
    sport = str(s.get("sport") or "")
    speed = first(s.get("enhanced_avg_speed"), s.get("avg_speed"))
    out = {
        "sport": sport,
        "sub_sport": s.get("sub_sport"),
        "distance_km": units.km(s.get("total_distance")),
        "timer": units.hms(s.get("total_timer_time")),
        "elapsed": units.hms(s.get("total_elapsed_time")),
        "avg_hr_bpm": s.get("avg_heart_rate"),
        "max_hr_bpm": s.get("max_heart_rate"),
        "ascent_m": s.get("total_ascent"),
        "descent_m": s.get("total_descent"),
        "kcal": s.get("total_calories"),
        "avg_power_w": s.get("avg_power"),
        "norm_power_w": s.get("normalized_power"),
        "tss": units.num(s.get("training_stress_score")),
        "intensity_factor": units.num(s.get("intensity_factor"), 2),
        "aerobic_te": units.num(s.get("total_training_effect")),
        "anaerobic_te": units.num(s.get("total_anaerobic_training_effect")),
    }
    if "cycl" in sport or "bik" in sport:
        out["avg_speed_kmh"] = units.kmh(speed)
    elif speed:
        out["avg_pace_min_km"] = units.pace_min_km(speed)
    return out


def _on_foot(msgs: dict) -> bool:
    sport = str(((msgs.get("session_mesgs") or [{}])[0]).get("sport") or "")
    return any(w in sport for w in ("running", "walking", "hiking"))


def laps(msgs: dict) -> list[dict]:
    on_foot = _on_foot(msgs)
    rows = []
    for n, lap in enumerate(msgs.get("lap_mesgs") or [], 1):
        speed = first(lap.get("enhanced_avg_speed"), lap.get("avg_speed"))
        cadence = lap.get("avg_running_cadence") or lap.get("avg_cadence")
        row = {
            "lap": n,
            "distance_km": units.km(lap.get("total_distance")),
            "time": units.hms(lap.get("total_timer_time")),
            "avg_hr_bpm": lap.get("avg_heart_rate"),
            "max_hr_bpm": lap.get("max_heart_rate"),
            "ascent_m": lap.get("total_ascent"),
            "avg_power_w": lap.get("avg_power"),
        }
        if on_foot:
            # FIT counts running cadence per leg; steps per minute is twice that.
            row["pace_min_km"] = units.pace_min_km(speed)
            row["avg_cadence_spm"] = (
                round((cadence + (lap.get("avg_fractional_cadence") or 0)) * 2) if cadence else None
            )
        else:
            row["speed_kmh"] = units.kmh(speed)
            row["avg_cadence_rpm"] = cadence
        rows.append(row)
    return rows


def _track(msgs: dict):
    """(seconds, metres, smoothed altitude) for records that have all three."""
    pts = [
        (_secs(r["timestamp"]), float(r["distance"]), float(_alt(r)))
        for r in _records(msgs)
        if r.get("distance") is not None and _alt(r) is not None
    ]
    if len(pts) < 10:
        return None
    t, d, a = zip(*pts, strict=False)
    return list(t), list(d), _smooth(list(a), 15)


def climbs(msgs: dict, min_gain: float = 15, min_grade: float = 2.0, drop: float = 5.0) -> list[dict]:
    track = _track(msgs)
    if not track:
        return []
    t, d, a = track
    recs = _records(msgs)
    found = []

    def keep(s: int, p: int) -> None:
        # Start the climb where it really starts: skip a flat lead-in within 2 m of the
        # starting altitude, which would otherwise dilute the grade.
        base = a[s]
        s = max(k for k in range(s, p + 1) if a[k] <= base + 2.0) if p > s else s
        gain, length = a[p] - a[s], d[p] - d[s]
        if gain < min_gain or length <= 0 or 100 * gain / length < min_grade:
            return
        seconds = t[p] - t[s]
        hrs = [r.get("heart_rate") for r in recs[s : p + 1] if r.get("heart_rate")]
        pw = [r.get("power") for r in recs[s : p + 1] if r.get("power") is not None]
        found.append(
            {
                "start_km": units.km(d[s]),
                "length_km": units.km(length),
                "gain_m": round(gain),
                "avg_grade_pct": round(100 * gain / length, 1),
                "time": units.hms(seconds),
                "vam_m_per_h": round(gain / (seconds / 3600)) if seconds > 0 else None,
                "avg_hr_bpm": round(sum(hrs) / len(hrs)) if hrs else None,
                "avg_power_w": round(sum(pw) / len(pw)) if pw else None,
            }
        )

    s = p = 0
    for i in range(1, len(a)):
        if p == s and a[i] < a[s]:
            s = p = i
            continue
        if a[i] > a[p]:
            p = i
        elif a[p] - a[i] >= drop:
            keep(s, p)
            s = p = i
    keep(s, p)
    return found


def grades(msgs: dict, window_m: float = 50) -> list[dict]:
    track = _track(msgs)
    if not track:
        return []
    t, d, a = track
    dist = defaultdict(float)
    time = defaultdict(float)
    j = 0
    for i in range(1, len(d)):
        while j < i and d[i] - d[j] > window_m:
            j += 1
        run = d[i] - d[j]
        grade = 100 * (a[i] - a[j]) / run if run > 5 else 0.0
        for lo, hi in GRADE_BINS:
            if lo <= grade < hi:
                dist[(lo, hi)] += d[i] - d[i - 1]
                time[(lo, hi)] += t[i] - t[i - 1]
                break
    total = sum(dist.values()) or 1
    return [
        {
            "grade_pct": f"{lo}..{hi}" if -100 < lo and hi < 100 else (f"<{hi}" if lo == -100 else f">{lo}"),
            "distance_km": units.km(dist[(lo, hi)]),
            "share_pct": round(100 * dist[(lo, hi)] / total),
            "time": units.hms(time[(lo, hi)]),
        }
        for lo, hi in GRADE_BINS
        if dist[(lo, hi)] > 0
    ]


def hr_drift(msgs: dict) -> dict | None:
    """Aerobic decoupling: output per heartbeat in the first half against the second.
    Speed per beat for runs, power per beat when there is power."""
    recs = [r for r in _records(msgs) if r.get("heart_rate")]
    use_power = sum(1 for r in recs if r.get("power")) > len(recs) / 2
    field = "power" if use_power else None
    rows = [
        (r.get("power") if use_power else _speed(r), r["heart_rate"])
        for r in recs
        if (r.get("power") if use_power else _speed(r))
    ]
    if len(rows) < 600:  # under ten minutes of moving data says nothing
        return None
    half = len(rows) // 2

    def ef(part):
        return (sum(o for o, _ in part) / len(part)) / (sum(h for _, h in part) / len(part))

    first_ef, second_ef = ef(rows[:half]), ef(rows[half:])
    return {
        "based_on": field or "speed",
        "decoupling_pct": round(100 * (first_ef - second_ef) / first_ef, 1),
        "note": "Under 5% means heart rate stayed coupled to output; it is only meaningful for steady efforts.",
    }


def power_curve(msgs: dict) -> dict | None:
    best = best_averages(per_second(_records(msgs), "power"))
    if not best:
        return None
    return {_label(d): round(w) for d, w in best.items()}


def variability(msgs: dict, ftp: float | None = None) -> dict | None:
    series = per_second(_records(msgs), "power")
    moving = [p for p in series if p > 0]
    if len(series) < 60 or not moving:
        return None
    rolling, window = [], 30
    acc = sum(series[:window])
    for i in range(window, len(series) + 1):
        rolling.append(acc / window)
        if i < len(series):
            acc += series[i] - series[i - window]
    np_w = (sum(x**4 for x in rolling) / len(rolling)) ** 0.25
    avg = sum(series) / len(series)
    s = (msgs.get("session_mesgs") or [{}])[0]
    ftp = ftp or s.get("threshold_power")
    out = {
        "avg_power_w": round(avg),
        "norm_power_w": round(np_w),
        "variability_index": round(np_w / avg, 2) if avg else None,
    }
    if ftp:
        intensity = np_w / ftp
        out.update(
            ftp_w=ftp,
            intensity_factor=round(intensity, 2),
            tss=round(len(series) * np_w * intensity / (ftp * 3600) * 100),
        )
    return out


def hrv(msgs: dict) -> dict | None:
    rr = [x for m in msgs.get("hrv_mesgs") or [] for x in (m.get("time") or []) if x and 0.25 < x < 2.5]
    if len(rr) < 30:
        return None
    ms = [x * 1000 for x in rr]
    diffs = [b - a for a, b in pairwise(ms)]
    mean = sum(ms) / len(ms)
    return {
        "beats": len(ms),
        "mean_rr_ms": round(mean),
        "rmssd_ms": round(math.sqrt(sum(d * d for d in diffs) / len(diffs)), 1),
        "sdnn_ms": round(math.sqrt(sum((x - mean) ** 2 for x in ms) / len(ms)), 1),
        "note": "From R-R intervals recorded during the activity (HRV logging must be on).",
    }


def running_dynamics(msgs: dict) -> dict | None:
    s = (msgs.get("session_mesgs") or [{}])[0]
    if not _on_foot(msgs):
        return None
    # FIT stores running cadence per leg (strides/min); oscillation and step length in mm.
    cadence = first(s.get("avg_running_cadence"), s.get("avg_cadence"))
    oscillation, step = s.get("avg_vertical_oscillation"), s.get("avg_step_length")
    out = {
        "cadence_spm": round((cadence + (s.get("avg_fractional_cadence") or 0)) * 2) if cadence else None,
        "ground_contact_ms": units.num(s.get("avg_stance_time")),
        "ground_contact_balance_pct_left": units.num(s.get("avg_stance_time_balance")),
        "vertical_oscillation_cm": units.num(oscillation / 10) if oscillation is not None else None,
        "vertical_ratio_pct": units.num(s.get("avg_vertical_ratio")),
        "step_length_m": units.num(step / 1000, 2) if step else None,
    }
    return out if any(v is not None for v in out.values()) else None


def cycling_dynamics(msgs: dict) -> dict | None:
    s = (msgs.get("session_mesgs") or [{}])[0]

    def pair(left: str, right: str):
        a, b = s.get(left), s.get(right)
        return [a, b] if a is not None or b is not None else None

    out = {
        "left_right_balance": s.get("left_right_balance"),
        "torque_effectiveness_pct": pair("avg_left_torque_effectiveness", "avg_right_torque_effectiveness"),
        "pedal_smoothness_pct": pair("avg_left_pedal_smoothness", "avg_right_pedal_smoothness"),
        "platform_center_offset_mm": pair("avg_left_pco", "avg_right_pco"),
        "seated_time": units.hms(s.get("time_seated")),
        "standing_time": units.hms(s.get("time_standing")),
    }
    return out if any(v is not None for v in out.values()) else None


def di2(msgs: dict) -> dict | None:
    events = [
        e for e in msgs.get("event_mesgs") or [] if str(e.get("event")) in ("rear_gear_change", "front_gear_change")
    ]
    if not events:
        return None
    usage: dict[str, float] = defaultdict(float)
    for e0, e1 in pairwise(events):
        combo = f"{e0.get('front_gear') or e0.get('front_gear_num')}x{e0.get('rear_gear') or e0.get('rear_gear_num')}"
        usage[combo] += (_secs(e1["timestamp"]) or 0) - (_secs(e0["timestamp"]) or 0)
    top = sorted(usage.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return {
        "rear_shifts": sum(1 for e in events if str(e.get("event")) == "rear_gear_change"),
        "front_shifts": sum(1 for e in events if str(e.get("event")) == "front_gear_change"),
        "most_used_gears": [{"gear": k, "time": units.hms(v)} for k, v in top],
    }


def temperature(msgs: dict) -> dict | None:
    temps = [r["temperature"] for r in _records(msgs) if r.get("temperature") is not None]
    if not temps:
        return None
    return {"min_c": min(temps), "avg_c": round(sum(temps) / len(temps), 1), "max_c": max(temps)}


_ANALYSIS = {
    "summary": summary,
    "laps": laps,
    "climbs": climbs,
    "grades": grades,
    "hr_drift": hr_drift,
    "power_curve": power_curve,
    "variability": variability,
    "hrv": hrv,
    "running_dynamics": running_dynamics,
    "cycling_dynamics": cycling_dynamics,
    "di2": di2,
    "temperature": temperature,
}


def analyse(msgs: dict, include: list[str]) -> dict:
    bad = [i for i in include if i not in ANALYSES]
    if bad:
        raise InputError(f"Unknown analysis {bad}. Choose from: {', '.join(ANALYSES)}")
    out: dict[str, Any] = {}
    skipped = []
    for name in include or ANALYSES:
        result = _ANALYSIS[name](msgs)
        if result in (None, [], {}):
            skipped.append(name)
        else:
            out[name] = result
    if skipped and include:
        out["no_data_for"] = skipped
    return out


def activity_analysis(ctx: Ctx, activity_id: int, include: list[str]) -> str:
    return dumps(analyse(messages_for(ctx, activity_id), include))


# ------------------------------------------------------------------ raw messages


def _clean(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def fit_messages(
    ctx: Ctx,
    activity_id: int,
    message_types: list[str] | None,
    include_records: bool,
    offset: int,
    limit: int,
) -> str:
    if not 1 <= limit <= 5000 or offset < 0:
        raise InputError("limit is 1-5000 and offset counts from 0")
    msgs = messages_for(ctx, activity_id)
    counts = {k: len(v) for k, v in msgs.items() if isinstance(v, list)}
    if message_types:
        wanted = [t if t.endswith("_mesgs") else f"{t}_mesgs" for t in message_types]
        unknown = [t for t in wanted if t not in counts]
        if unknown:
            raise InputError(f"Not in this file: {unknown}. Available: {', '.join(sorted(counts))}")
    else:
        # By default leave out the per-second records and anything as bulky.
        wanted = [k for k, n in counts.items() if n <= 100 and (include_records or k != "record_mesgs")]
        if include_records and "record_mesgs" in counts:
            wanted.append("record_mesgs")
    flat = [(t, m) for t in wanted for m in msgs.get(t) or []]
    page = flat[offset : offset + limit]
    grouped: dict[str, list] = defaultdict(list)
    for t, m in page:
        grouped[t.removesuffix("_mesgs")].append(_clean(m))
    return dumps(
        {
            "available": {k.removesuffix("_mesgs"): v for k, v in sorted(counts.items())},
            "returned": len(page),
            "next_offset": offset + limit if offset + limit < len(flat) else None,
            "messages": grouped,
        },
        keep_empty=False,
    )


# ------------------------------------------------------------------ across activities


def season_power_curve(ctx: Ctx, activity_type: str, num_activities: int) -> str:
    if not 1 <= num_activities <= 50:
        raise InputError("num_activities is 1-50 (each one is a file download)")
    found = ctx.g.get_activities(0, min(100, num_activities * 3), activity_type) or []
    with_power = [a for a in found if a.get("avgPower") or a.get("maxPower")][:num_activities]
    if not with_power:
        return dumps({"note": f"No recent {activity_type} activities with power data."})
    best: dict[int, tuple[float, Any]] = {}
    used = 0
    for a in with_power:
        try:
            curve = best_averages(per_second(_records(messages_for(ctx, a["activityId"])), "power"))
        except InputError:
            continue
        used += 1
        for d, w in curve.items():
            if d not in best or w > best[d][0]:
                best[d] = (w, a)
    out: dict[str, Any] = {
        "activity_type": activity_type,
        "activities_used": used,
        "best": [
            {
                "duration": _label(d),
                "watts": round(w),
                "activity_id": a.get("activityId"),
                "date": (a.get("startTimeLocal") or "")[:10],
            }
            for d, (w, a) in sorted(best.items())
        ],
    }
    if 1200 in best:
        out["ftp_estimate_w"] = round(best[1200][0] * 0.95)
        out["ftp_note"] = "95% of the best 20-minute power in these activities."
    return dumps(out)
