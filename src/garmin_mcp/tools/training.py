"""Training state on a given day: readiness, status, load balance, VO2max, predictions."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from .. import shape, units
from ..client import Ctx
from ..dates import trim_local
from ..errors import InputError
from ..shape import dig, dumps, first

METRICS = (
    "readiness",
    "morning_readiness",
    "recovery_time",
    "status",
    "load_balance",
    "fitness_age",
    "vo2max",
    "acclimation",
    "running_tolerance",
    "race_predictions",
    "lactate_threshold",
    "cycling_ftp",
)


def primary(device_map: Any) -> dict:
    """Garmin keys some data by device. Use the primary training device's entry, not
    whichever comes first (taxuspt #379: a second device made training load read 0)."""
    if not isinstance(device_map, dict):
        return {}
    entries = [v for v in device_map.values() if isinstance(v, dict)]
    return next((e for e in entries if e.get("primaryTrainingDevice")), entries[0] if entries else {})


def readiness_entry(e: dict) -> dict:
    return {
        "time": trim_local(e.get("timestampLocal")),
        "context": e.get("inputContext"),
        "score": e.get("score"),
        "level": e.get("level"),
        "feedback": e.get("feedbackShort") or e.get("feedbackLong"),
        "recovery_h": units.recovery_hours(e.get("recoveryTime")),
        "sleep_score": e.get("sleepScore"),
        # While HRV is still onboarding Garmin puts 511 here, which is no HRV in ms.
        "hrv_weekly_avg_ms": e.get("hrvWeeklyAverage") if 1 <= (e.get("hrvWeeklyAverage") or 0) <= 250 else None,
        "acute_load": e.get("acuteLoad"),
        "factors_pct": {
            "sleep": e.get("sleepScoreFactorPercent"),
            "recovery_time": e.get("recoveryTimeFactorPercent"),
            "acute_load": e.get("acwrFactorPercent"),
            "hrv": e.get("hrvFactorPercent"),
            "stress_history": e.get("stressHistoryFactorPercent"),
            "sleep_history": e.get("sleepHistoryFactorPercent"),
        },
        "factors_feedback": {
            "sleep": e.get("sleepScoreFactorFeedback"),
            "recovery_time": e.get("recoveryTimeFactorFeedback"),
            "acute_load": e.get("acwrFactorFeedback"),
            "hrv": e.get("hrvFactorFeedback"),
            "stress_history": e.get("stressHistoryFactorFeedback"),
            "sleep_history": e.get("sleepHistoryFactorFeedback"),
        },
    }


def _gmt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace(" ", "T")[:19]).replace(tzinfo=UTC)
    except ValueError:
        return None


def _band(value, low, high) -> dict:
    status = None
    if None not in (value, low, high):
        status = "below" if value < low else "above" if value > high else "within"
    return {"load": units.whole(value), "target": [units.whole(low), units.whole(high)], "status": status}


def load_balance(lb: dict) -> dict:
    return {
        "aerobic_low": _band(
            lb.get("monthlyLoadAerobicLow"),
            lb.get("monthlyLoadAerobicLowTargetMin"),
            lb.get("monthlyLoadAerobicLowTargetMax"),
        ),
        "aerobic_high": _band(
            lb.get("monthlyLoadAerobicHigh"),
            lb.get("monthlyLoadAerobicHighTargetMin"),
            lb.get("monthlyLoadAerobicHighTargetMax"),
        ),
        "anaerobic": _band(
            lb.get("monthlyLoadAnaerobic"),
            lb.get("monthlyLoadAnaerobicTargetMin"),
            lb.get("monthlyLoadAnaerobicTargetMax"),
        ),
        "feedback": lb.get("trainingBalanceFeedbackPhrase"),
    }


def status_of(data: dict) -> dict:
    ts = primary(dig(data, "mostRecentTrainingStatus", "latestTrainingStatusData"))
    acute = ts.get("acuteTrainingLoadDTO") or {}
    vo2 = dig(data, "mostRecentVO2Max", "generic") or {}
    devices = dig(data, "mostRecentTrainingStatus", "recordedDevices") or []
    device = next((d.get("deviceName") for d in devices if d.get("deviceId") == ts.get("deviceId")), None)
    chronic, acute_load = acute.get("dailyTrainingLoadChronic"), acute.get("dailyTrainingLoadAcute")
    return {
        "date": ts.get("calendarDate"),
        "status": ts.get("trainingStatusFeedbackPhrase"),
        "since": ts.get("sinceDate"),
        "paused": ts.get("trainingPaused") or None,
        "weekly_load": units.whole(ts.get("weeklyTrainingLoad")),
        "weekly_load_optimal": [units.whole(ts.get("loadTunnelMin")), units.whole(ts.get("loadTunnelMax"))]
        if ts.get("loadTunnelMin") is not None
        else None,
        "fitness_trend": ts.get("fitnessTrend"),
        "acute_load": units.whole(acute_load),
        "chronic_load": units.whole(chronic),
        "acwr": units.num(acute.get("dailyAcuteChronicWorkloadRatio"), 2),
        "acwr_status": acute.get("acwrStatus"),
        "form_tsb": units.whole(chronic - acute_load) if None not in (chronic, acute_load) else None,
        "vo2max": units.num(first(vo2.get("vo2MaxPreciseValue"), vo2.get("vo2MaxValue"))),
        "device": device,
    }


# ------------------------------------------------------------------ metrics


def _readiness(ctx: Ctx, d: date) -> dict:
    entries = ctx.g.get_training_readiness(d.isoformat()) or []
    entries = entries if isinstance(entries, list) else [entries]
    ordered = sorted(entries, key=lambda e: e.get("timestamp") or "", reverse=True)
    return {"date": d.isoformat(), "readings": [readiness_entry(e) for e in ordered]}


def _morning(ctx: Ctx, d: date) -> dict:
    e = ctx.g.get_morning_training_readiness(d.isoformat())
    if not e:
        return {"date": d.isoformat(), "note": "No morning readiness reading for this day."}
    return {"date": d.isoformat(), **readiness_entry(e)}


def _recovery_time(ctx: Ctx, d: date) -> dict:
    entries = ctx.g.get_training_readiness(d.isoformat()) or []
    entries = [e for e in (entries if isinstance(entries, list) else [entries]) if e.get("recoveryTime") is not None]
    if not entries:
        return {"date": d.isoformat(), "note": "No recovery time recorded for this day."}
    latest = max(entries, key=lambda e: e.get("timestamp") or "")
    minutes = float(latest["recoveryTime"])
    taken = _gmt(latest.get("timestamp"))
    out = {"as_of": trim_local(latest.get("timestampLocal")), "recovery_h_then": units.recovery_hours(minutes)}
    if taken and d >= ctx.today():
        elapsed = (datetime.now(UTC) - taken).total_seconds() / 60
        left = max(0.0, minutes - elapsed)
        out["remaining_h"] = units.recovery_hours(left)
        out["recovered"] = left == 0
    return out


NOT_YET = (
    "Garmin has no {what} yet. It appears after a few weeks of activities with heart "
    "rate on the watch (and, for some values, outdoor runs with GPS)."
)


def _status(ctx: Ctx, d: date) -> dict:
    out = status_of(ctx.g.get_training_status(d.isoformat()) or {})
    if not any(v is not None for v in out.values()):
        return {"date": d.isoformat(), "note": NOT_YET.format(what="training status")}
    return out


def _load_balance(ctx: Ctx, d: date) -> dict:
    data = ctx.g.get_training_status(d.isoformat()) or {}
    lb = primary(dig(data, "mostRecentTrainingLoadBalance", "metricsTrainingLoadBalanceDTOMap"))
    if not lb:
        return {"date": d.isoformat(), "note": "No four-week load balance yet."}
    return {"date": lb.get("calendarDate") or d.isoformat(), **load_balance(lb)}


def _fitness_age(ctx: Ctx, d: date) -> dict:
    f = ctx.g.get_fitnessage_data(d.isoformat()) or {}
    comps = f.get("components") or {}
    return {
        "date": d.isoformat(),
        "age": f.get("chronologicalAge"),
        "fitness_age": units.num(f.get("fitnessAge")),
        "achievable_fitness_age": units.num(f.get("achievableFitnessAge")),
        "previous_fitness_age": units.num(f.get("previousFitnessAge")),
        "components": {
            name: {
                "value": units.num(c.get("value")),
                "target": units.num(c.get("targetValue")),
                "improvement": units.num(c.get("improvementValue")),
            }
            for name, c in comps.items()
            if isinstance(c, dict)
        },
    }


def _max_metrics(ctx: Ctx, d: date) -> dict:
    data = ctx.g.get_max_metrics(d.isoformat())
    return (data[0] if data else {}) if isinstance(data, list) else (data or {})


def _vo2_entry(entry: dict) -> dict:
    gen, cyc = entry.get("generic") or {}, entry.get("cycling") or {}
    return {
        "date": gen.get("calendarDate") or cyc.get("calendarDate"),
        "running": units.num(first(gen.get("vo2MaxPreciseValue"), gen.get("vo2MaxValue"))),
        "cycling": units.num(first(cyc.get("vo2MaxPreciseValue"), cyc.get("vo2MaxValue"))),
        "fitness_age": gen.get("fitnessAge"),
    }


def _vo2max(ctx: Ctx, d: date) -> dict:
    """VO2max is only recomputed after qualifying activities, so look for the latest
    value up to this date rather than a value on it."""
    from datetime import timedelta

    data = ctx.g.get_max_metrics_range((d - timedelta(days=180)).isoformat(), d.isoformat()) or []
    found = [
        e
        for e in (_vo2_entry(x) for x in (data if isinstance(data, list) else [data]) if isinstance(x, dict))
        if e["running"] is not None or e["cycling"] is not None
    ]
    if not found:
        status = _vo2_entry(dig(ctx.g.get_training_status(d.isoformat()), "mostRecentVO2Max") or {})
        found = [status] if status["running"] is not None or status["cycling"] is not None else []
    if found:
        latest = max(found, key=lambda e: e["date"] or "")
        if latest["date"] and latest["date"] < d.isoformat():
            latest["note"] = f"Last updated {latest['date']}; VO2max is carried forward between updates."
        return latest
    profile = (ctx.g.get_user_profile() or {}).get("userData") or {}
    if profile.get("vo2MaxRunning") or profile.get("vo2MaxCycling"):
        return {
            "running": profile.get("vo2MaxRunning"),
            "cycling": profile.get("vo2MaxCycling"),
            "note": "From the user profile; Garmin has no dated VO2max yet.",
        }
    return {"note": NOT_YET.format(what="VO2max")}


def _acclimation(ctx: Ctx, d: date) -> dict:
    a = _max_metrics(ctx, d).get("heatAltitudeAcclimation") or dig(
        ctx.g.get_training_status(d.isoformat()), "mostRecentVO2Max", "heatAltitudeAcclimation"
    ) or {}
    out = {
        "date": a.get("calendarDate") or d.isoformat(),
        "heat_pct": a.get("heatAcclimationPercentage"),
        "heat_trend": a.get("heatTrend"),
        "altitude_acclimation_m": a.get("altitudeAcclimation"),
        "altitude_trend": a.get("altitudeTrend"),
        "current_altitude_m": a.get("currentAltitude"),
    }
    if all(v is None for k, v in out.items() if k != "date"):
        return {"date": d.isoformat(), "note": NOT_YET.format(what="heat or altitude acclimation")}
    return out


def _running_tolerance(ctx: Ctx, d: date) -> Any:
    rows = shape.compact(ctx.g.get_running_tolerance(d.isoformat(), d.isoformat(), "daily"))
    if not rows:
        return {"date": d.isoformat(), "note": NOT_YET.format(what="running tolerance")}
    return {"date": d.isoformat(), "tolerance": rows}


_RACES = (("5k", "time5K", 5.0), ("10k", "time10K", 10.0), ("half", "timeHalfMarathon", 21.0975),
          ("marathon", "timeMarathon", 42.195))


def _race_predictions(ctx: Ctx, d: date) -> dict:
    p = ctx.g.get_race_predictions() or {}
    out: dict[str, Any] = {"date": p.get("calendarDate")}
    for name, key, km in _RACES:
        seconds = p.get(key)
        if seconds:
            out[name] = {"time": units.hms(seconds), "pace_min_km": units.pace_min_km(km * 1000 / seconds)}
    if len(out) == 1:
        return {"note": NOT_YET.format(what="race predictions")}
    return out


def _lactate_threshold(ctx: Ctx, d: date) -> dict:
    data = ctx.g.get_lactate_threshold(latest=True) or {}
    lt = data.get("speed_and_heart_rate") or {}
    power = data.get("power") or {}
    speed = units.lactate_speed_mps(lt.get("speed"))
    return {
        "date": lt.get("calendarDate"),
        "hr_bpm": units.whole(lt.get("heartRate")),
        "cycling_hr_bpm": units.whole(lt.get("heartRateCycling")),
        "pace_min_km": units.pace_min_km(speed),
        "speed_kmh": units.kmh(speed),
        "running_power_w": units.whole(power.get("functionalThresholdPower")),
        "running_power_w_per_kg": units.num(power.get("powerToWeight"), 2),
    }


def _cycling_ftp(ctx: Ctx, d: date) -> dict:
    data = ctx.g.get_cycling_ftp()
    f = (data[0] if data else {}) if isinstance(data, list) else (data or {})
    return {
        "date": f.get("calendarDate"),
        "ftp_w": units.whole(f.get("functionalThresholdPower")),
        "sport": f.get("sport"),
    }


_CURATED = {
    "readiness": _readiness,
    "morning_readiness": _morning,
    "recovery_time": _recovery_time,
    "status": _status,
    "load_balance": _load_balance,
    "fitness_age": _fitness_age,
    "vo2max": _vo2max,
    "acclimation": _acclimation,
    "running_tolerance": _running_tolerance,
    "race_predictions": _race_predictions,
    "lactate_threshold": _lactate_threshold,
    "cycling_ftp": _cycling_ftp,
}


def _full(ctx: Ctx, metric: str, d: date) -> Any:
    g, ds = ctx.g, d.isoformat()
    return {
        "readiness": lambda: g.get_training_readiness(ds),
        "morning_readiness": lambda: g.get_morning_training_readiness(ds),
        "recovery_time": lambda: g.get_training_readiness(ds),
        "status": lambda: g.get_training_status(ds),
        "load_balance": lambda: g.get_training_status(ds),
        "fitness_age": lambda: g.get_fitnessage_data(ds),
        "vo2max": lambda: g.get_max_metrics(ds),
        "acclimation": lambda: g.get_max_metrics(ds),
        "running_tolerance": lambda: g.get_running_tolerance(ds, ds, "daily"),
        "race_predictions": lambda: g.get_race_predictions(),
        "lactate_threshold": lambda: g.get_lactate_threshold(latest=True),
        "cycling_ftp": lambda: g.get_cycling_ftp(),
    }[metric]()


def training(ctx: Ctx, metric: str, date_text: str | None, detail: str) -> str:
    if metric not in METRICS:
        raise InputError(f"Unknown metric {metric!r}. Choose from: {', '.join(METRICS)}")
    d = ctx.day(date_text)
    if d > ctx.today():
        raise InputError(f"{d} is in the future.")
    if detail == "full":
        return shape.raw(_full(ctx, metric, d))
    return dumps(ctx.cached(("training", metric, d), lambda: _CURATED[metric](ctx, d), past=d))
