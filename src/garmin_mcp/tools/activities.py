"""Activities: the list, one activity in as much depth as asked, and edits to it."""

from __future__ import annotations

from typing import Any

from .. import shape, units
from ..client import Ctx
from ..dates import gmt_string_to_local, trim_local
from ..errors import InputError
from ..shape import dig, dumps, first

SEARCH = "/activitylist-service/activities/search/activities"
SECTIONS = (
    "summary",
    "splits",
    "typed_splits",
    "split_summaries",
    "weather",
    "hr_zones",
    "power_zones",
    "gear",
    "exercise_sets",
    "training_effect",
    "details",
)


# ------------------------------------------------------------------ sport family


def family(type_key: str | None) -> str:
    """run | bike | swim | other: decides pace vs speed."""
    key = (type_key or "").lower()
    if "swim" in key:
        return "swim"
    if any(w in key for w in ("cycl", "bik", "ride", "spinning")):
        return "bike"
    if any(w in key for w in ("run", "walk", "hik", "treadmill", "track", "trail", "mountaineering")):
        return "run"
    return "other"


def steady_speed(average, moving):
    """Moving speed when it agrees with the overall average. Garmin's run/walk interval
    summaries carry a broken movingDuration (528 s for a 42-minute run) and a moving
    speed of 10 m/s to match, so a moving speed far above the average is ignored."""
    if moving and (not average or float(moving) <= 1.3 * float(average)):
        return moving
    return average


def speed_fields(sport: str, avg_mps, max_mps=None, prefix: str = "") -> dict:
    """Pace for runs and walks, km/h for rides, pace per 100 m for swims."""
    if sport == "run":
        return {
            f"{prefix}pace_min_km": units.pace_min_km(avg_mps),
            f"{prefix}best_pace_min_km": units.pace_min_km(max_mps) if max_mps else None,
        }
    if sport == "bike":
        return {
            f"{prefix}speed_kmh": units.kmh(avg_mps),
            f"{prefix}max_speed_kmh": units.kmh(max_mps) if max_mps else None,
        }
    if sport == "swim" and avg_mps:
        return {f"{prefix}pace_min_100m": units.hms(100 / float(avg_mps))}
    return {}


# ------------------------------------------------------------------ list


def row(a: dict) -> dict:
    """One activity from a list, in the fields that matter for scanning a list."""
    type_key = dig(a, "activityType", "typeKey")
    sport = family(type_key)
    out = {
        "id": a.get("activityId"),
        "name": a.get("activityName"),
        "type": type_key,
        "start": trim_local(a.get("startTimeLocal")),
        "distance_km": units.km(a.get("distance")),
        "duration": units.hms(a.get("duration")),
        "moving": units.hms(a.get("movingDuration")),
    }
    out.update(speed_fields(sport, a.get("averageSpeed")))
    out.pop("best_pace_min_km", None)
    out.pop("max_speed_kmh", None)
    out.update(
        {
            "avg_hr_bpm": units.whole(a.get("averageHR")),
            "max_hr_bpm": units.whole(a.get("maxHR")),
            "elev_gain_m": units.whole(a.get("elevationGain")),
            "kcal": units.whole(a.get("calories")),
            "training_load": units.num(a.get("activityTrainingLoad"), 0),
            "aerobic_te": units.num(a.get("aerobicTrainingEffect")),
            "anaerobic_te": units.num(a.get("anaerobicTrainingEffect")),
            "te_label": a.get("trainingEffectLabel"),
            "event_type": dig(a, "eventType", "typeKey"),
            "location": a.get("locationName"),
            "sets": a.get("totalSets") or a.get("activeSets"),
        }
    )
    return out


def activities(
    ctx: Ctx,
    start_date: str | None,
    end_date: str | None,
    activity_type: str | None,
    limit: int,
    page: int,
) -> str:
    if limit == 0:
        return dumps({"total_activities": ctx.g.count_activities()})
    if not 1 <= limit <= 100:
        raise InputError("limit must be between 1 and 100 (or 0 for just the count)")
    if page < 0:
        raise InputError("page starts at 0")

    params: dict[str, Any] = {"start": page * limit, "limit": limit}
    if start_date or end_date:
        first_day, last_day = ctx.span(start_date or end_date, end_date, max_days=3660)
        params["startDate"] = first_day.isoformat()
        params["endDate"] = last_day.isoformat()
    if activity_type:
        params["activityType"] = activity_type
    # Straight to the search endpoint, one page at a time: the library's
    # get_activities_by_date fetches every page and can return far too much.
    found = ctx.g.connectapi(SEARCH, params=params) or []
    return dumps(
        {
            "page": page,
            "count": len(found),
            "has_more": len(found) == limit,
            "next_page": page + 1 if len(found) == limit else None,
            "activities": [row(a) for a in found],
        }
    )


# ------------------------------------------------------------------ one activity


def _summary(a: dict) -> dict:
    s = a.get("summaryDTO") or {}
    type_key = dig(a, "activityTypeDTO", "typeKey")
    sport = family(type_key)
    out = {
        "id": a.get("activityId"),
        "name": a.get("activityName"),
        "description": a.get("description"),
        "type": type_key,
        "event_type": dig(a, "eventTypeDTO", "typeKey"),
        "start": trim_local(s.get("startTimeLocal")),
        "location": a.get("locationName"),
        "distance_km": units.km(s.get("distance")),
        "duration": units.hms(s.get("duration")),
        "moving": units.hms(s.get("movingDuration")),
        "elapsed": units.hms(s.get("elapsedDuration")),
    }
    out.update(speed_fields(sport, steady_speed(s.get("averageSpeed"), s.get("averageMovingSpeed")), s.get("maxSpeed")))
    out.update(
        {
            "elev_gain_m": units.whole(s.get("elevationGain")),
            "elev_loss_m": units.whole(s.get("elevationLoss")),
            "elev_min_m": units.whole(s.get("minElevation")),
            "elev_max_m": units.whole(s.get("maxElevation")),
            "avg_hr_bpm": units.whole(s.get("averageHR")),
            "max_hr_bpm": units.whole(s.get("maxHR")),
            "min_hr_bpm": units.whole(s.get("minHR")),
            "avg_cadence_spm": units.whole(first(s.get("averageRunCadence"), s.get("averageBikeCadence"))),
            "max_cadence_spm": units.whole(first(s.get("maxRunCadence"), s.get("maxBikeCadence"))),
            "avg_power_w": units.whole(s.get("averagePower")),
            "max_power_w": units.whole(s.get("maxPower")),
            "norm_power_w": units.whole(s.get("normalizedPower")),
            "ground_contact_ms": units.whole(s.get("groundContactTime")),
            "stride_m": units.num(s.get("strideLength") / 100, 2) if s.get("strideLength") else None,
            "vertical_osc_cm": units.num(s.get("verticalOscillation")),
            "vertical_ratio_pct": units.num(s.get("verticalRatio")),
            "kcal": units.whole(s.get("calories")),
            "steps": s.get("steps"),
            "aerobic_te": units.num(s.get("trainingEffect")),
            "anaerobic_te": units.num(s.get("anaerobicTrainingEffect")),
            "te_label": s.get("trainingEffectLabel"),
            "training_load": units.num(s.get("activityTrainingLoad"), 0),
            "rpe": units.rpe_from_garmin(s.get("directWorkoutRpe")),
            "feel": units.feel_label(s.get("directWorkoutFeel")),
            "laps": dig(a, "metadataDTO", "lapCount"),
            "intensity_min_moderate": s.get("moderateIntensityMinutes"),
            "intensity_min_vigorous": s.get("vigorousIntensityMinutes"),
            "body_battery_change": s.get("differenceBodyBattery"),
            "recovery_hr_bpm": units.whole(s.get("recoveryHeartRate")),
            "temp_min_c": units.num(s.get("minTemperature")),
            "temp_max_c": units.num(s.get("maxTemperature")),
            "sweat_loss_ml": units.whole(s.get("waterEstimated")),
        }
    )
    return out


def _lap(lap: dict, sport: str, n: int) -> dict:
    out = {
        "lap": n,
        "intensity": lap.get("intensityType"),
        "distance_km": units.km(lap.get("distance")),
        "duration": units.hms(lap.get("duration")),
    }
    out.update(speed_fields(sport, steady_speed(lap.get("averageSpeed"), lap.get("averageMovingSpeed"))))
    out.pop("best_pace_min_km", None)
    out.pop("max_speed_kmh", None)
    out.update(
        {
            "avg_hr_bpm": units.whole(lap.get("averageHR")),
            "max_hr_bpm": units.whole(lap.get("maxHR")),
            "elev_gain_m": units.whole(lap.get("elevationGain")),
            "elev_loss_m": units.whole(lap.get("elevationLoss")),
            "avg_cadence_spm": units.whole(first(lap.get("averageRunCadence"), lap.get("averageBikeCadence"))),
            "avg_power_w": units.whole(lap.get("averagePower")),
            "lengths": len(lap.get("lengthDTOs") or []) or None,
            "strokes": lap.get("totalNumberOfStrokes"),
        }
    )
    return out


def _segment(seg: dict, sport: str, tz) -> dict:
    """One typed split (a run or walk segment, an interval) or one split-type summary."""
    out = {
        "type": first(seg.get("type"), seg.get("splitType")),
        "count": seg.get("noOfSplits"),
        "start": gmt_string_to_local(seg.get("startTimeGMT"), tz),
        "distance_km": units.km(seg.get("distance"), 2),
        "duration": units.hms(seg.get("duration")),
    }
    speed = speed_fields(sport, steady_speed(seg.get("averageSpeed"), seg.get("averageMovingSpeed")))
    out.update({k: v for k, v in speed.items() if "best" not in k and "max" not in k})
    out.update(
        {
            "avg_hr_bpm": units.whole(seg.get("averageHR")),
            "max_hr_bpm": units.whole(seg.get("maxHR")),
            "elev_gain_m": units.whole(seg.get("elevationGain")),
            "elev_loss_m": units.whole(seg.get("elevationLoss")),
            "avg_cadence_spm": units.whole(seg.get("averageRunCadence")),
            "avg_power_w": units.whole(seg.get("averagePower")),
        }
    )
    return out


def _segments(items: Any, sport: str, tz) -> list[dict]:
    """Skip the one-second, zero-distance splits Garmin records at transitions."""
    return [
        _segment(s, sport, tz)
        for s in (items if isinstance(items, list) else [])
        if isinstance(s, dict) and ((s.get("distance") or 0) >= 1 or (s.get("duration") or 0) >= 3)
    ]


def _zones(rows: Any, unit: str) -> list[dict]:
    rows = rows if isinstance(rows, list) else []
    total = sum(r.get("secsInZone") or 0 for r in rows) or 1
    return [
        {
            "zone": r.get("zoneNumber"),
            f"from_{unit}": units.whole(r.get("zoneLowBoundary")),
            "time": units.hms(r.get("secsInZone")),
            "pct": units.num(100 * (r.get("secsInZone") or 0) / total, 0),
        }
        for r in rows
    ]


def _weather(w: dict, tz) -> dict:
    return {
        "time": gmt_string_to_local(w.get("issueDate"), tz),  # issued in GMT
        "conditions": dig(w, "weatherTypeDTO", "desc"),
        # Garmin sends activity weather in Fahrenheit, with no unit attached.
        "temp_c": units.celsius_from_f(w.get("temp")),
        "feels_like_c": units.celsius_from_f(w.get("apparentTemp")),
        "dew_point_c": units.celsius_from_f(w.get("dewPoint")),
        "humidity_pct": w.get("relativeHumidity"),
        "wind_direction": w.get("windDirectionCompassPoint"),
        "wind_kmh": units.kmh_from_knots(w.get("windSpeed")),
        "wind_gust_kmh": units.kmh_from_knots(w.get("windGust")),
        "station": dig(w, "weatherStationDTO", "name"),
    }


def _gear(items: Any) -> list[dict]:
    return [
        {
            "uuid": g.get("uuid"),
            "name": g.get("displayName") or g.get("customMakeModel"),
            "type": g.get("gearTypeName"),
            "status": g.get("gearStatusName"),
        }
        for g in (items if isinstance(items, list) else [])
    ]


def _exercise_sets(data: dict) -> dict:
    sets = data.get("exerciseSets") or []
    active = [s for s in sets if s.get("setType") == "ACTIVE"]
    rows = []
    for n, s in enumerate(active, 1):
        best = max(s.get("exercises") or [{}], key=lambda e: e.get("probability") or 0)
        rows.append(
            {
                "set": n,
                "exercise": best.get("name") or best.get("category"),
                "category": best.get("category"),
                "reps": s.get("repetitionCount"),
                # Exercise-set weight arrives in grams.
                "weight_kg": units.kg(s.get("weight")) if s.get("weight") else None,
                "duration": units.hms(s.get("duration")),
            }
        )
    rest = sum(s.get("duration") or 0 for s in sets if s.get("setType") == "REST")
    return {"sets": rows, "total_rest": units.hms(rest) if rest else None}


def _training_effect(a: dict) -> dict:
    s = a.get("summaryDTO") or {}
    return {
        "aerobic_te": units.num(s.get("trainingEffect")),
        "aerobic_message": s.get("aerobicTrainingEffectMessage"),
        "anaerobic_te": units.num(s.get("anaerobicTrainingEffect")),
        "anaerobic_message": s.get("anaerobicTrainingEffectMessage"),
        "primary_benefit": s.get("trainingEffectLabel"),
        "training_load": units.num(s.get("activityTrainingLoad"), 0),
    }


def _detail_columns(sport: str) -> tuple:
    speed = {
        "run": ("directSpeed", "pace_min_km", units.pace_min_km),
        "bike": ("directSpeed", "speed_kmh", units.kmh),
    }.get(sport, ("directSpeed", "speed_mps", lambda v: units.num(v, 2)))
    return (
        ("sumDuration", "time", units.hms),
        ("sumDistance", "km", lambda v: units.km(v, 2)),
        ("directHeartRate", "hr_bpm", units.whole),
        speed,
        ("directElevation", "elev_m", units.whole),
        # directRunCadence is labelled steps/min but counts one leg (73 for 146 spm);
        # directDoubleCadence is the real steps per minute.
        ("directDoubleCadence", "cadence_spm", units.whole),
        ("directBikeCadence", "cadence_rpm", units.whole),
        ("directPower", "power_w", units.whole),
        ("directAirTemperature", "temp_c", units.num),
    )


def _details(ctx: Ctx, activity_id: int, points: int, sport: str) -> dict:
    from garminconnect.activity_details import parse_activity_detail_metrics

    raw = ctx.g.get_activity_details(str(activity_id), maxchart=2000, maxpoly=0) or {}
    parsed = parse_activity_detail_metrics(raw)
    if parsed and not any("directDoubleCadence" in s for s in parsed):
        for s in parsed:  # older devices only send the one-leg value
            if s.get("directRunCadence") is not None:
                s["directDoubleCadence"] = s["directRunCadence"] * 2
    samples = shape.downsample(parsed, points)
    present = [c for c in _detail_columns(sport) if any(c[0] in s for s in samples)]
    return {
        "samples_total": len(parsed),
        "columns": [c[1] for c in present],
        "rows": [[conv(s.get(key)) for key, _, conv in present] for s in samples],
    }


def activity(ctx: Ctx, activity_id: int, sections: list[str], detail: str, points: int) -> str:
    bad = [s for s in sections if s not in SECTIONS]
    if bad:
        raise InputError(f"Unknown section(s) {bad}. Choose from: {', '.join(SECTIONS)}")
    sections = sections or ["summary"]
    g, aid = ctx.g, str(activity_id)

    if detail == "full":
        raw_calls = {
            "summary": lambda: g.get_activity(aid),
            "splits": lambda: g.get_activity_splits(aid),
            "typed_splits": lambda: g.get_activity_typed_splits(aid),
            "split_summaries": lambda: g.get_activity_split_summaries(aid),
            "weather": lambda: g.get_activity_weather(aid),
            "hr_zones": lambda: g.get_activity_hr_in_timezones(aid),
            "power_zones": lambda: g.get_activity_power_in_timezones(aid),
            "gear": lambda: g.get_activity_gear(aid),
            "exercise_sets": lambda: g.get_activity_exercise_sets(aid),
            "training_effect": lambda: g.get_activity(aid),
            "details": lambda: g.get_activity_details(aid, maxchart=min(points, 2000), maxpoly=0),
        }
        return shape.raw({s: raw_calls[s]() for s in sections})

    base = None
    needs_base = {"summary", "training_effect", "splits", "typed_splits", "split_summaries", "details"}
    if needs_base & set(sections):
        base = ctx.cached(("activity", aid), lambda: g.get_activity(aid))
    sport = family(dig(base, "activityTypeDTO", "typeKey")) if base else "other"

    out: dict[str, Any] = {}
    for section in sections:
        if section == "summary":
            out["summary"] = _summary(base)
        elif section == "training_effect":
            out["training_effect"] = _training_effect(base)
        elif section == "splits":
            laps = (g.get_activity_splits(aid) or {}).get("lapDTOs") or []
            out["splits"] = [_lap(lap, sport, n) for n, lap in enumerate(laps, 1)]
        elif section == "typed_splits":
            data = g.get_activity_typed_splits(aid) or {}
            out["typed_splits"] = _segments(data.get("splits"), sport, ctx.tz)
        elif section == "split_summaries":
            data = g.get_activity_split_summaries(aid) or {}
            out["split_summaries"] = _segments(data.get("splitSummaries"), sport, ctx.tz)
        elif section == "weather":
            out["weather"] = _weather(g.get_activity_weather(aid) or {}, ctx.tz)
        elif section == "hr_zones":
            out["hr_zones"] = _zones(g.get_activity_hr_in_timezones(aid), "bpm")
        elif section == "power_zones":
            out["power_zones"] = _zones(g.get_activity_power_in_timezones(aid), "w")
        elif section == "gear":
            out["gear"] = _gear(g.get_activity_gear(aid))
        elif section == "exercise_sets":
            out["exercise_sets"] = _exercise_sets(g.get_activity_exercise_sets(aid) or {})
        elif section == "details":
            out["details"] = _details(ctx, activity_id, points, sport)
    return dumps(out)
