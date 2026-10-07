"""One day of wellness data: the daily summary, sleep, HRV, stress, body battery and so on."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from .. import shape, units
from ..client import Ctx
from ..dates import gmt_string_to_local, local_time, trim_local
from ..errors import InputError
from ..shape import dig, dumps

METRICS = (
    "summary",
    "steps",
    "heart_rate",
    "resting_hr",
    "stress",
    "body_battery",
    "sleep",
    "hrv",
    "spo2",
    "respiration",
    "floors",
    "hydration",
    "intensity_minutes",
    "events",
    "lifestyle",
)


def _hourly(values: list, tz, index: int = 1) -> list[dict]:
    """[[epoch_ms, value, ...], ...] as per-hour min/avg/max, skipping invalid readings."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for item in values or []:
        if not isinstance(item, (list, tuple)) or len(item) <= index:
            continue
        ts, value = item[0], item[index]
        if ts is None or value is None or value < 0:
            continue
        hour = local_time(ts, tz)[11:13]
        buckets[hour].append(float(value))
    return [
        {"hour": h, "min": round(min(v)), "avg": round(sum(v) / len(v)), "max": round(max(v))}
        for h, v in sorted(buckets.items())
    ]


def _partial(ctx: Ctx, d: date) -> dict:
    return {"partial_day": True} if d >= ctx.today() else {}


# ------------------------------------------------------------------ each metric


def _summary(ctx: Ctx, d: date) -> dict:
    s = ctx.g.get_user_summary(d.isoformat()) or {}
    return {
        "date": d.isoformat(),
        **_partial(ctx, d),
        "steps": s.get("totalSteps"),
        "step_goal": s.get("dailyStepGoal"),
        "distance_km": units.km(s.get("totalDistanceMeters")),
        "kcal_total": units.whole(s.get("totalKilocalories")),
        "kcal_active": units.whole(s.get("activeKilocalories")),
        "kcal_resting": units.whole(s.get("bmrKilocalories")),
        "floors_up": units.num(s.get("floorsAscended"), 0),
        "intensity_min_moderate": s.get("moderateIntensityMinutes"),
        "intensity_min_vigorous": s.get("vigorousIntensityMinutes"),
        "resting_hr_bpm": s.get("restingHeartRate"),
        "resting_hr_7d_avg_bpm": s.get("lastSevenDaysAvgRestingHeartRate"),
        "min_hr_bpm": s.get("minHeartRate"),
        "max_hr_bpm": s.get("maxHeartRate"),
        "stress_avg": s.get("averageStressLevel"),
        "stress_max": s.get("maxStressLevel"),
        "stress_qualifier": s.get("stressQualifier"),
        "body_battery": {
            "highest": s.get("bodyBatteryHighestValue"),
            "lowest": s.get("bodyBatteryLowestValue"),
            "latest": s.get("bodyBatteryMostRecentValue"),
            "at_wake": s.get("bodyBatteryAtWakeTime"),
            "charged": s.get("bodyBatteryChargedValue"),
            "drained": s.get("bodyBatteryDrainedValue"),
        },
        "spo2_avg_pct": s.get("averageSpo2"),
        "spo2_lowest_pct": s.get("lowestSpo2"),
        "respiration_waking_avg": s.get("avgWakingRespirationValue"),
        "active_h": units.hours((s.get("activeSeconds") or 0) + (s.get("highlyActiveSeconds") or 0))
        if s.get("activeSeconds") is not None
        else None,
        "sedentary_h": units.hours(s.get("sedentarySeconds")),
        "sleeping_h": units.hours(s.get("sleepingSeconds")),
    }


def _steps(ctx: Ctx, d: date) -> dict:
    intervals = ctx.g.get_steps_data(d.isoformat()) or []
    per_hour: dict[str, int] = defaultdict(int)
    for i in intervals:
        start = gmt_string_to_local(i.get("startGMT"), ctx.tz)
        if start and i.get("steps"):
            per_hour[start[11:13]] += int(i["steps"])
    total = sum(per_hour.values())
    busiest = max(per_hour.items(), key=lambda kv: kv[1]) if per_hour else None
    return {
        "date": d.isoformat(),
        **_partial(ctx, d),
        "steps": total,
        "busiest_hour": {"hour": busiest[0], "steps": busiest[1]} if busiest else None,
        "by_hour": [{"hour": h, "steps": n} for h, n in sorted(per_hour.items())],
    }


def _heart_rate(ctx: Ctx, d: date) -> dict:
    hr = ctx.g.get_heart_rates(d.isoformat()) or {}
    values = [v[1] for v in hr.get("heartRateValues") or [] if v and v[1]]
    return {
        "date": d.isoformat(),
        **_partial(ctx, d),
        "resting_hr_bpm": hr.get("restingHeartRate"),
        "resting_hr_7d_avg_bpm": hr.get("lastSevenDaysAvgRestingHeartRate"),
        "min_hr_bpm": hr.get("minHeartRate"),
        "max_hr_bpm": hr.get("maxHeartRate"),
        "avg_hr_bpm": round(sum(values) / len(values)) if values else None,
        "readings": len(values),
        "by_hour": _hourly(hr.get("heartRateValues"), ctx.tz),
    }


def _resting_hr(ctx: Ctx, d: date) -> dict:
    data = ctx.g.get_rhr_day(d.isoformat()) or {}
    rows = dig(data, "allMetrics", "metricsMap", "WELLNESS_RESTING_HEART_RATE") or []
    value = next((r.get("value") for r in rows if r.get("value") is not None), None)
    return {"date": d.isoformat(), "resting_hr_bpm": units.whole(value)}


# Garmin's stress bands; -1 and -2 mean "no reading" and "activity".
_STRESS_BANDS = (("rest", 0, 25), ("low", 26, 50), ("medium", 51, 75), ("high", 76, 100))


def _stress(ctx: Ctx, d: date) -> dict:
    data = ctx.g.get_stress_data(d.isoformat()) or {}
    values = [v for v in data.get("stressValuesArray") or [] if v and v[1] is not None]
    valid = [v[1] for v in values if v[1] >= 0]
    counts = {
        name: sum(1 for x in valid if lo <= x <= hi) for name, lo, hi in _STRESS_BANDS
    }
    total = len(valid) or 1
    # Readings come every three minutes.
    return {
        "date": d.isoformat(),
        **_partial(ctx, d),
        "avg": data.get("avgStressLevel"),
        "max": data.get("maxStressLevel"),
        "minutes": {name: n * 3 for name, n in counts.items()},
        "pct": {name: round(100 * n / total) for name, n in counts.items()},
        "activity_minutes": sum(3 for v in values if v[1] == -2) or None,
        "by_hour": _hourly(data.get("stressValuesArray"), ctx.tz),
    }


def _body_battery(ctx: Ctx, d: date) -> dict:
    days = ctx.g.get_body_battery(d.isoformat(), d.isoformat()) or []
    day = days[0] if days else {}
    levels = [v[1] for v in day.get("bodyBatteryValuesArray") or [] if v and v[1] is not None]
    events = ctx.g.get_body_battery_events(d.isoformat()) or []
    return {
        "date": d.isoformat(),
        **_partial(ctx, d),
        "charged": day.get("charged"),
        "drained": day.get("drained"),
        "highest": max(levels) if levels else None,
        "lowest": min(levels) if levels else None,
        "latest": levels[-1] if levels else None,
        "feedback": dig(day, "bodyBatteryDynamicFeedbackEvent", "feedbackLongType")
        or dig(day, "endOfDayBodyBatteryDynamicFeedbackEvent", "feedbackLongType"),
        "events": [_bb_event(e.get("event") or e, ctx) for e in events]
        or [_bb_event(e, ctx) for e in day.get("bodyBatteryActivityEvent") or []],
    }


def _bb_event(e: dict, ctx: Ctx) -> dict:
    return {
        "type": e.get("eventType"),
        "start": gmt_string_to_local(e.get("eventStartTimeGmt"), ctx.tz),
        "duration": units.hms((e.get("durationInMilliseconds") or 0) / 1000),
        "impact": e.get("bodyBatteryImpact"),
        "feedback": e.get("shortFeedback"),
    }


def _sleep(ctx: Ctx, d: date) -> dict:
    data = ctx.g.get_sleep_data(d.isoformat()) or {}
    s = data.get("dailySleepDTO") or {}
    if not s.get("sleepTimeSeconds") and not s.get("sleepStartTimestampGMT"):
        return {"date": d.isoformat(), "note": "No sleep recorded for this night."}
    asleep = s.get("sleepTimeSeconds") or 0
    scores = s.get("sleepScores") or {}

    def share(seconds):
        return round(100 * seconds / asleep) if asleep and seconds is not None else None

    return {
        "date": d.isoformat(),
        "bedtime": local_time(s.get("sleepStartTimestampGMT"), ctx.tz),
        "wake": local_time(s.get("sleepEndTimestampGMT"), ctx.tz),
        "sleep_h": units.hours(asleep),
        "score": dig(scores, "overall", "value"),
        "quality": dig(scores, "overall", "qualifierKey"),
        "feedback": s.get("sleepScoreFeedback"),
        "insight": s.get("sleepScoreInsight"),
        "stages_min": {
            "deep": units.minutes(s.get("deepSleepSeconds"), 0),
            "light": units.minutes(s.get("lightSleepSeconds"), 0),
            "rem": units.minutes(s.get("remSleepSeconds"), 0),
            "awake": units.minutes(s.get("awakeSleepSeconds"), 0),
        },
        "stages_pct": {
            "deep": share(s.get("deepSleepSeconds")),
            "light": share(s.get("lightSleepSeconds")),
            "rem": share(s.get("remSleepSeconds")),
        },
        "awake_count": s.get("awakeCount"),
        "restless_moments": data.get("restlessMomentsCount"),
        "resting_hr_bpm": data.get("restingHeartRate"),
        "avg_overnight_hrv_ms": data.get("avgOvernightHrv"),
        "hrv_status": data.get("hrvStatus"),
        "body_battery_change": data.get("bodyBatteryChange"),
        "avg_sleep_stress": s.get("avgSleepStress"),
        "spo2_avg_pct": s.get("averageSpO2Value"),
        "spo2_lowest_pct": s.get("lowestSpO2Value"),
        "respiration_avg": s.get("averageRespirationValue"),
        "nap_min": units.minutes(s.get("napTimeSeconds"), 0) or None,
        "sleep_need_min": dig(s, "sleepNeed", "actual"),
    }


def _hrv(ctx: Ctx, d: date) -> dict:
    data = ctx.g.get_hrv_data(d.isoformat()) or {}
    s = data.get("hrvSummary") or {}
    if not s:
        return {"date": d.isoformat(), "note": "No HRV reading for this night."}
    base = s.get("baseline") or {}
    return {
        "date": s.get("calendarDate") or d.isoformat(),
        "last_night_avg_ms": s.get("lastNightAvg"),
        "last_night_5min_high_ms": s.get("lastNight5MinHigh"),
        "weekly_avg_ms": s.get("weeklyAvg"),
        "status": s.get("status"),
        "feedback": s.get("feedbackPhrase"),
        "baseline_balanced_ms": [base.get("balancedLow"), base.get("balancedUpper")]
        if base.get("balancedLow") is not None
        else None,
        "baseline_low_upper_ms": base.get("lowUpper"),
    }


def _spo2(ctx: Ctx, d: date) -> dict:
    s = ctx.g.get_spo2_data(d.isoformat()) or {}
    return {
        "date": d.isoformat(),
        **_partial(ctx, d),
        "avg_pct": s.get("averageSpO2"),
        "lowest_pct": s.get("lowestSpO2"),
        "latest_pct": s.get("latestSpO2"),
        "sleep_avg_pct": s.get("avgSleepSpO2"),
        "avg_7d_pct": s.get("lastSevenDaysAvgSpO2"),
    }


def _respiration(ctx: Ctx, d: date) -> dict:
    s = ctx.g.get_respiration_data(d.isoformat()) or {}
    return {
        "date": d.isoformat(),
        **_partial(ctx, d),
        "waking_avg_brpm": s.get("avgWakingRespirationValue"),
        "sleep_avg_brpm": s.get("avgSleepRespirationValue"),
        "highest_brpm": s.get("highestRespirationValue"),
        "lowest_brpm": s.get("lowestRespirationValue"),
    }


def _floors(ctx: Ctx, d: date) -> dict:
    data = ctx.g.get_floors(d.isoformat()) or {}
    rows = data.get("floorValuesArray") or []
    up = sum(r[2] or 0 for r in rows if isinstance(r, list) and len(r) > 3)
    down = sum(r[3] or 0 for r in rows if isinstance(r, list) and len(r) > 3)
    return {"date": d.isoformat(), **_partial(ctx, d), "floors_up": up, "floors_down": down}


def _hydration(ctx: Ctx, d: date) -> dict:
    h = ctx.g.get_hydration_data(d.isoformat()) or {}
    return {
        "date": d.isoformat(),
        "intake_ml": units.whole(h.get("valueInML")),
        "goal_ml": units.whole(h.get("goalInML")),
        "sweat_loss_ml": units.whole(h.get("sweatLossInML")),
        "from_activities_ml": units.whole(h.get("activityIntakeInML")),
        "last_entry": trim_local(h.get("lastEntryTimestampLocal")),
    }


def _intensity_minutes(ctx: Ctx, d: date) -> dict:
    m = ctx.g.get_intensity_minutes_data(d.isoformat()) or {}
    return {
        "date": d.isoformat(),
        **_partial(ctx, d),
        "moderate_min": m.get("moderateMinutes"),
        "vigorous_min": m.get("vigorousMinutes"),
        "week_moderate_min": m.get("weeklyModerate"),
        "week_vigorous_min": m.get("weeklyVigorous"),
        "week_total_min": m.get("weeklyTotal"),
        "week_goal_min": m.get("weekGoal"),
        "note": "Garmin counts vigorous minutes double toward the weekly total.",
    }


def _events(ctx: Ctx, d: date) -> Any:
    """Movement the watch picked up on its own (Move IQ) and recorded activities."""
    data = ctx.g.get_all_day_events(d.isoformat())
    rows = data if isinstance(data, list) else (data or {}).get("events") or []
    return {
        "date": d.isoformat(),
        "events": [
            {
                "type": e.get("activityType"),
                "start": trim_local(e.get("startTimestampLocal")),
                "end": trim_local(e.get("endTimestampLocal")),
                "duration_min": e.get("duration"),
                "intensity_min_moderate": e.get("moderateIntensityMinutes"),
                "intensity_min_vigorous": e.get("vigorousIntensityMinutes"),
            }
            for e in rows
            if isinstance(e, dict)
        ],
    }


def _lifestyle(ctx: Ctx, d: date) -> Any:
    return {"date": d.isoformat(), "log": shape.compact(ctx.g.get_lifestyle_logging_data(d.isoformat()))}


_CURATED = {
    "summary": _summary,
    "steps": _steps,
    "heart_rate": _heart_rate,
    "resting_hr": _resting_hr,
    "stress": _stress,
    "body_battery": _body_battery,
    "sleep": _sleep,
    "hrv": _hrv,
    "spo2": _spo2,
    "respiration": _respiration,
    "floors": _floors,
    "hydration": _hydration,
    "intensity_minutes": _intensity_minutes,
    "events": _events,
    "lifestyle": _lifestyle,
}


def _full(ctx: Ctx, metric: str, d: date) -> Any:
    g, ds = ctx.g, d.isoformat()
    calls = {
        "summary": lambda: g.get_stats_and_body(ds),
        "steps": lambda: g.get_steps_data(ds),
        "heart_rate": lambda: g.get_heart_rates(ds),
        "resting_hr": lambda: g.get_rhr_day(ds),
        "stress": lambda: g.get_stress_data(ds),
        "body_battery": lambda: {
            "days": g.get_body_battery(ds, ds),
            "events": g.get_body_battery_events(ds),
        },
        "sleep": lambda: g.get_sleep_data(ds),
        "hrv": lambda: g.get_hrv_data(ds),
        "spo2": lambda: g.get_spo2_data(ds),
        "respiration": lambda: g.get_respiration_data(ds),
        "floors": lambda: g.get_floors(ds),
        "hydration": lambda: g.get_hydration_data(ds),
        "intensity_minutes": lambda: g.get_intensity_minutes_data(ds),
        "events": lambda: g.get_all_day_events(ds),
        "lifestyle": lambda: g.get_lifestyle_logging_data(ds),
    }
    return calls[metric]()


def wellness(ctx: Ctx, metric: str, date_text: str | None, detail: str) -> str:
    if metric not in METRICS:
        raise InputError(f"Unknown metric {metric!r}. Choose from: {', '.join(METRICS)}")
    d = ctx.day(date_text)
    if d > ctx.today():
        raise InputError(f"{d} is in the future.")
    if detail == "full":
        return shape.raw(_full(ctx, metric, d))
    key = ("wellness", metric, d)
    return dumps(ctx.cached(key, lambda: _CURATED[metric](ctx, d), past=d))
