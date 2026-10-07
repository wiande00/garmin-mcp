"""Metrics over a date range.

Garmin has single-call range endpoints for most of these (sleep, HRV, resting HR,
calories, steps, body battery, VO2max, weight, endurance and hill score), so a 90-day
view is one or a few requests, not 90. The few with no range endpoint (daily stress,
respiration, training load) go day by day, capped, with finished days cached.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from .. import shape, units
from ..client import Ctx
from ..dates import chunks
from ..errors import InputError
from ..shape import dig, dumps, first
from .training import status_of

# metric -> (max days, default days)
LIMITS = {
    "stats": (366, 28),
    "steps": (366, 28),
    "calories": (366, 28),
    "stress": (366, 28),
    "intensity_minutes": (366, 28),
    "sleep": (366, 28),
    "hrv": (366, 28),
    "respiration": (31, 14),
    "resting_hr": (366, 28),
    "body_battery": (31, 14),
    "training_load": (90, 28),
    "vo2max": (366, 90),
    "running_tolerance": (366, 90),
    "endurance_score": (366, 90),
    "hill_score": (366, 90),
    "lactate_threshold": (366, 180),
    "progress": (3660, 28),
    "weight": (366, 90),
}
METRICS = tuple(LIMITS)
WEEKLY = {"steps", "stress", "intensity_minutes", "running_tolerance"}
PER_DAY_CAP = 31  # metrics fetched one day at a time
PROGRESS_METRICS = ("distance", "duration", "movingDuration", "elevationGain", "calories")


def _days(first_day: date, last_day: date):
    d = first_day
    while d <= last_day:
        yield d
        d += timedelta(days=1)


def _summary(ctx: Ctx, d: date) -> dict:
    return ctx.cached(("summary", d), lambda: ctx.g.get_user_summary(d.isoformat()) or {}, past=d)


def _local_millis(ms) -> str | None:
    """Garmin's 'local' millis are local wall-clock time encoded as if it were UTC."""
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ each metric


def _stats(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    steps = {r["date"]: r for r in _steps(ctx, a, b, "daily")}
    out = []
    for c in ctx.g.get_calories_daily(a.isoformat(), b.isoformat()) or []:
        day = c.get("calendarDate")
        out.append(
            {
                **steps.pop(day, {"date": day}),
                "kcal_total": units.whole(c.get("total")),
                "kcal_active": units.whole(c.get("active")),
                "kcal_resting": units.whole(c.get("resting")),
            }
        )
    out.extend(steps.values())
    return sorted(out, key=lambda r: r["date"])


def _steps(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    if agg == "weekly":
        weeks = max(1, -(-((b - a).days + 1) // 7))
        rows = ctx.g.get_weekly_steps(b.isoformat(), weeks) or []
        return [
            {
                "week": r.get("calendarDate"),
                "steps": dig(r, "values", "totalSteps"),
                "avg_steps_per_day": units.whole(dig(r, "values", "averageSteps")),
                "distance_km": units.km(dig(r, "values", "totalDistance")),
                "days_with_data": dig(r, "values", "wellnessDataDaysCount"),
            }
            for r in rows
        ]
    rows = []
    for s, e in chunks(a, b, 28):
        rows += ctx.g.get_daily_steps(s.isoformat(), e.isoformat()) or []
    return [
        {
            "date": r.get("calendarDate"),
            "steps": r.get("totalSteps"),
            "step_goal": r.get("stepGoal"),
            "distance_km": units.km(r.get("totalDistance")),
        }
        for r in rows
    ]


def _calories(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    return [
        {
            "date": c.get("calendarDate"),
            "kcal_total": units.whole(c.get("total")),
            "kcal_active": units.whole(c.get("active")),
            "kcal_resting": units.whole(c.get("resting")),
        }
        for c in ctx.g.get_calories_daily(a.isoformat(), b.isoformat()) or []
    ]


def _stress(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    if agg == "weekly":
        weeks = max(1, -(-((b - a).days + 1) // 7))
        return [
            {"week": r.get("calendarDate"), "avg_stress": r.get("value")}
            for r in ctx.g.get_weekly_stress(b.isoformat(), weeks) or []
        ]
    _cap(a, b, "stress (daily)")
    return [
        {
            "date": d.isoformat(),
            "avg": (s := _summary(ctx, d)).get("averageStressLevel"),
            "max": s.get("maxStressLevel"),
            "qualifier": s.get("stressQualifier"),
        }
        for d in _days(a, b)
    ]


def _intensity_minutes(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    if agg == "weekly":
        return [
            {
                "week": r.get("calendarDate"),
                "moderate_min": r.get("moderateValue"),
                "vigorous_min": r.get("vigorousValue"),
                "goal_min": r.get("weeklyGoal"),
            }
            for r in ctx.g.get_weekly_intensity_minutes(a.isoformat(), b.isoformat()) or []
        ]
    _cap(a, b, "intensity_minutes (daily)")
    return [
        {
            "date": d.isoformat(),
            "moderate_min": (s := _summary(ctx, d)).get("moderateIntensityMinutes"),
            "vigorous_min": s.get("vigorousIntensityMinutes"),
        }
        for d in _days(a, b)
    ]


def _sleep(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    rows = []
    for r in ctx.g.get_sleep_daily(a.isoformat(), b.isoformat()) or []:
        v = r.get("values") or {}
        rows.append(
            {
                "date": r.get("calendarDate"),
                "bedtime": _local_millis(v.get("localSleepStartTimeInMillis")),
                "wake": _local_millis(v.get("localSleepEndTimeInMillis")),
                "sleep_h": units.hours(v.get("totalSleepTimeInSeconds")),
                "score": first(v.get("sleepScore"), v.get("overallScore")),
                "quality": v.get("sleepScoreQuality"),
                "deep_min": units.minutes(v.get("deepTime"), 0),
                "light_min": units.minutes(v.get("lightTime"), 0),
                "rem_min": units.minutes(v.get("remTime"), 0),
                "awake_min": units.minutes(v.get("awakeTime"), 0),
                "resting_hr_bpm": v.get("restingHeartRate"),
                "hrv_status": v.get("hrvStatus"),
                "respiration_brpm": units.num(v.get("respiration")),
                "spo2_pct": units.num(v.get("spO2")),
                "body_battery_change": v.get("bodyBatteryChange"),
            }
        )
    return rows


def _hrv(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    rows = []
    for s, e in chunks(a, b, 28):
        data = ctx.g.get_hrv_data_range(s.isoformat(), e.isoformat()) or {}
        rows += data.get("hrvSummaries") or []
    return [
        {
            "date": r.get("calendarDate"),
            "last_night_avg_ms": r.get("lastNightAvg"),
            "last_night_5min_high_ms": r.get("lastNight5MinHigh"),
            "weekly_avg_ms": r.get("weeklyAvg"),
            "status": r.get("status"),
            "baseline_balanced_ms": [dig(r, "baseline", "balancedLow"), dig(r, "baseline", "balancedUpper")]
            if dig(r, "baseline", "balancedLow") is not None
            else None,
        }
        for r in sorted(rows, key=lambda r: r.get("calendarDate") or "")
    ]


def _respiration(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    out = []
    for d in _days(a, b):
        r = ctx.cached(("respiration", d), lambda d=d: ctx.g.get_respiration_data(d.isoformat()) or {}, past=d)
        out.append(
            {
                "date": d.isoformat(),
                "waking_avg_brpm": r.get("avgWakingRespirationValue"),
                "sleep_avg_brpm": r.get("avgSleepRespirationValue"),
                "lowest_brpm": r.get("lowestRespirationValue"),
                "highest_brpm": r.get("highestRespirationValue"),
            }
        )
    return out


def _resting_hr(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    return [
        {"date": r.get("calendarDate"), "resting_hr_bpm": units.whole(r.get("value"))}
        for r in ctx.g.get_rhr_daily(a.isoformat(), b.isoformat()) or []
    ]


def _body_battery(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    out = []
    for day in ctx.g.get_body_battery(a.isoformat(), b.isoformat()) or []:
        levels = [v[1] for v in day.get("bodyBatteryValuesArray") or [] if v and v[1] is not None]
        out.append(
            {
                "date": day.get("date"),
                "charged": day.get("charged"),
                "drained": day.get("drained"),
                "highest": max(levels) if levels else None,
                "lowest": min(levels) if levels else None,
            }
        )
    return out


def _training_load(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    out = []
    for d in _days(a, b):
        data = ctx.cached(("training_status", d), lambda d=d: ctx.g.get_training_status(d.isoformat()) or {}, past=d)
        s = status_of(data)
        out.append(
            {
                "date": d.isoformat(),
                "acute": s.get("acute_load"),
                "chronic": s.get("chronic_load"),
                "form_tsb": s.get("form_tsb"),
                "acwr": s.get("acwr"),
                "status": s.get("status"),
            }
        )
    return out


def _vo2max(ctx: Ctx, a: date, b: date, agg: str) -> dict:
    # Look back further so the value in force on the first day is known: VO2max is only
    # recomputed after qualifying activities and carries forward in between.
    lookback = a - timedelta(days=60)
    data = ctx.g.get_max_metrics_range(lookback.isoformat(), b.isoformat()) or []
    points = []
    for entry in data if isinstance(data, list) else [data]:
        gen, cyc = entry.get("generic") or {}, entry.get("cycling") or {}
        day = gen.get("calendarDate") or cyc.get("calendarDate")
        run = first(gen.get("vo2MaxPreciseValue"), gen.get("vo2MaxValue"))
        bike = first(cyc.get("vo2MaxPreciseValue"), cyc.get("vo2MaxValue"))
        if day and (run is not None or bike is not None):
            points.append({"date": day, "running": units.num(run), "cycling": units.num(bike)})
    points.sort(key=lambda p: p["date"])
    before = [p for p in points if p["date"] < a.isoformat()]
    inside = [p for p in points if a.isoformat() <= p["date"] <= b.isoformat()]
    return {
        "in_force_at_start": before[-1] if before else None,
        "updates": inside,
        "note": "VO2max only changes on the listed dates; it carries forward in between.",
    }


def _running_tolerance(ctx: Ctx, a: date, b: date, agg: str) -> Any:
    return shape.compact(ctx.g.get_running_tolerance(a.isoformat(), b.isoformat(), agg))


def _endurance_score(ctx: Ctx, a: date, b: date, agg: str) -> dict:
    data = ctx.g.get_endurance_score(a.isoformat(), b.isoformat()) or {}
    latest = data.get("enduranceScoreDTO") or {}
    weeks = data.get("groupMap") or {}
    return {
        "latest": {
            "date": latest.get("calendarDate"),
            "score": latest.get("overallScore"),
            "classification": latest.get("classification"),
            "feedback": latest.get("feedbackPhrase"),
        },
        "avg": data.get("avg"),
        "max": data.get("max"),
        "weeks": [
            {"week": week, "avg": v.get("groupAverage"), "max": v.get("groupMax")}
            for week, v in sorted(weeks.items())
            if isinstance(v, dict) and v.get("groupAverage") is not None
        ],
    }


def _hill_score(ctx: Ctx, a: date, b: date, agg: str) -> dict:
    data = ctx.g.get_hill_score(a.isoformat(), b.isoformat()) or {}
    return {
        "max": data.get("maxScore"),
        "days": [
            {
                "date": r.get("calendarDate"),
                "overall": r.get("overallScore"),
                "strength": r.get("strengthScore"),
                "endurance": r.get("enduranceScore"),
            }
            for r in data.get("hillScoreDTOList") or []
        ],
    }


def _lactate_threshold(ctx: Ctx, a: date, b: date, agg: str) -> Any:
    data = ctx.g.get_lactate_threshold(
        latest=False, start_date=a.isoformat(), end_date=b.isoformat(), aggregation=agg
    )
    if not isinstance(data, dict):
        return shape.compact(data)
    by_day: dict[str, dict] = {}
    for series, key, convert in (
        ("speed", "pace_min_km", lambda v: units.pace_min_km(units.lactate_speed_mps(v))),
        ("heart_rate", "hr_bpm", units.whole),
        ("power", "running_power_w", units.whole),
    ):
        for row in data.get(series) or []:
            day = row.get("from") or row.get("updatedDate")
            by_day.setdefault(day, {"date": day})[key] = convert(row.get("value"))
    return [by_day[d] for d in sorted(by_day)]


# Garmin's progress totals: distance and elevation in centimetres, durations in
# milliseconds, calories as kcal times 4.19 (checked against a recorded run).
_PROGRESS_UNITS = {
    "distance": ("distance_km", lambda v: units.num(v / 100_000, 2)),
    "duration": ("time", lambda v: units.hms(v / 1000)),
    "movingDuration": ("moving", lambda v: units.hms(v / 1000)),
    "elevationGain": ("elev_gain_m", lambda v: units.whole(v / 100)),
    "calories": ("kcal", lambda v: units.whole(v / 4.19)),
}


def _progress(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    by_type: dict[str, dict] = {}
    for metric in PROGRESS_METRICS:
        data = ctx.g.get_progress_summary_between_dates(a.isoformat(), b.isoformat(), metric) or []
        for bucket in data if isinstance(data, list) else [data]:
            for sport, stats in (bucket.get("stats") or {}).items():
                total = (stats or {}).get(metric) or {}
                if total.get("sum") is None:
                    continue
                name, convert = _PROGRESS_UNITS[metric]
                row = by_type.setdefault(sport, {"type": sport})
                row["activities"] = total.get("count")
                row[name] = convert(total["sum"])
    return sorted(by_type.values(), key=lambda r: -(r.get("activities") or 0))


def _weight(ctx: Ctx, a: date, b: date, agg: str) -> list[dict]:
    data = ctx.g.get_weigh_ins(a.isoformat(), b.isoformat()) or {}
    out = []
    for day in data.get("dailyWeightSummaries") or []:
        w = day.get("latestWeight") or {}
        out.append(
            {
                "date": day.get("summaryDate"),
                "weight_kg": units.kg(w.get("weight")),
                "body_fat_pct": units.num(w.get("bodyFat")),
                "min_kg": units.kg(day.get("minWeight")) if day.get("numOfWeightEntries", 0) > 1 else None,
                "max_kg": units.kg(day.get("maxWeight")) if day.get("numOfWeightEntries", 0) > 1 else None,
            }
        )
    return sorted(out, key=lambda r: r.get("date") or "")


_FETCH = {
    "stats": _stats,
    "steps": _steps,
    "calories": _calories,
    "stress": _stress,
    "intensity_minutes": _intensity_minutes,
    "sleep": _sleep,
    "hrv": _hrv,
    "respiration": _respiration,
    "resting_hr": _resting_hr,
    "body_battery": _body_battery,
    "training_load": _training_load,
    "vo2max": _vo2max,
    "running_tolerance": _running_tolerance,
    "endurance_score": _endurance_score,
    "hill_score": _hill_score,
    "lactate_threshold": _lactate_threshold,
    "progress": _progress,
    "weight": _weight,
}
PER_DAY = {"respiration", "training_load"}


def _cap(a: date, b: date, what: str) -> None:
    if (b - a).days + 1 > PER_DAY_CAP:
        raise InputError(
            f"{what} is fetched one day at a time, so at most {PER_DAY_CAP} days per call. "
            "Use aggregation='weekly' for longer periods."
        )


def trend(ctx: Ctx, metric: str, start_date: str | None, end_date: str | None, aggregation: str) -> str:
    if metric not in LIMITS:
        raise InputError(f"Unknown metric {metric!r}. Choose from: {', '.join(METRICS)}")
    if aggregation == "weekly" and metric not in WEEKLY:
        raise InputError(f"Weekly aggregation is only available for: {', '.join(sorted(WEEKLY))}")
    max_days, default_days = LIMITS[metric]
    a, b = ctx.span(start_date, end_date, max_days=max_days, default_days=default_days)
    if metric in PER_DAY and (b - a).days + 1 > max_days:
        raise InputError(f"{metric} allows at most {max_days} days.")
    rows = _FETCH[metric](ctx, a, b, aggregation)
    out: dict[str, Any] = {"metric": metric, "start": a.isoformat(), "end": b.isoformat()}
    if b >= ctx.today():
        out["note_today"] = "Today is still in progress."
    if isinstance(rows, list):
        # A day Garmin has nothing for comes back as just its date: leave it out.
        rows = [r for r in rows if not isinstance(r, dict) or set(shape.compact(r)) - {"date", "week"}]
    if not shape.compact(rows):
        out["note"] = f"Garmin has no {metric.replace('_', ' ')} data for this period."
    else:
        out["data"] = rows
    return dumps(out)
