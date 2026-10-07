"""Saved workouts and the training calendar (scheduled workouts, events, Garmin Coach)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .. import shape, units
from ..client import Ctx
from ..dates import trim_local
from ..errors import InputError
from ..shape import dig, dumps, first

CALENDAR_PARTS = ("scheduled", "events", "coach")
MAX_CALENDAR_DAYS = 366
DISTANCE_UNIT_M = {"meter": 1, "kilometer": 1000, "mile": 1609.344, "yard": 0.9144, "foot": 0.3048}


# ------------------------------------------------------------------ reading a workout


def _until(step: dict) -> str | None:
    """What ends a step, in words."""
    key = dig(step, "endCondition", "conditionTypeKey")
    value = step.get("endConditionValue")
    if key == "time" and value:
        return units.hms(value)
    if key == "distance" and value:
        return f"{units.km(value, 2)} km" if value >= 1000 else f"{units.whole(value)} m"
    if key == "lap.button":
        return "lap button"
    if key == "reps" and value:
        return f"{units.whole(value)} reps"
    if key == "calories" and value:
        return f"{units.whole(value)} kcal"
    if key == "heart.rate" and value:
        return f"heart rate {units.whole(value)} bpm"
    if key == "fixed.rest" and value:
        return f"rest {units.hms(value)}"
    return key


def _target(step: dict, prefix: str = "") -> str | None:
    key = dig(step, f"{prefix}targetType" if not prefix else "secondaryTargetType", "workoutTargetTypeKey")
    one = step.get(f"{prefix}targetValueOne") if not prefix else step.get("secondaryTargetValueOne")
    two = step.get(f"{prefix}targetValueTwo") if not prefix else step.get("secondaryTargetValueTwo")
    zone = step.get("zoneNumber") if not prefix else step.get("secondaryZoneNumber")
    unit = dig(step, "targetValueUnit", "unitKey") or step.get("targetValueUnit")
    if not key or key == "no.target":
        return None
    if key == "heart.rate.zone":
        return f"HR zone {zone}" if zone else f"HR {units.whole(one)}-{units.whole(two)} bpm"
    if key == "pace.zone" and one and two:
        # Speeds in m/s; the faster speed is the faster pace.
        fast, slow = max(one, two), min(one, two)
        return f"pace {units.pace_min_km(fast)}-{units.pace_min_km(slow)} /km"
    if key in ("power.zone", "power.between"):
        if zone:
            return f"power zone {zone}"
        suffix = f" {unit}" if unit and unit not in ("watt", "W") else " W"
        return f"power {units.whole(one)}-{units.whole(two)}{suffix}"
    if key.startswith("cadence"):
        return f"cadence {units.whole(one)}-{units.whole(two)}"
    if key.startswith("speed") and one and two:
        return f"speed {units.kmh(min(one, two))}-{units.kmh(max(one, two))} km/h"
    return f"{key} {one}-{two}" if one or two else key


def _step(step: dict) -> dict:
    if step.get("type") == "RepeatGroupDTO":
        return {
            "repeat": units.whole(first(step.get("numberOfIterations"), step.get("endConditionValue"))),
            "steps": [_step(s) for s in step.get("workoutSteps") or []],
        }
    weight = step.get("weightValue")
    return {
        "type": dig(step, "stepType", "stepTypeKey"),
        "until": _until(step),
        "target": _target(step),
        "also": _target(step, "secondary"),
        "exercise": step.get("exerciseName"),
        "category": step.get("category"),
        "weight": f"{weight} {dig(step, 'weightUnit', 'unitKey') or ''}".strip() if weight else None,
        "stroke": dig(step, "strokeType", "strokeTypeKey"),
        "note": step.get("description"),
    }


def workout_view(w: dict) -> dict:
    segments = w.get("workoutSegments") or []
    steps = [s for seg in segments for s in seg.get("workoutSteps") or []]
    return {
        "id": w.get("workoutId"),
        "uuid": w.get("workoutUuid"),
        "name": w.get("workoutName"),
        "sport": dig(w, "sportType", "sportTypeKey"),
        "description": w.get("description"),
        "est_duration": units.hms(w.get("estimatedDurationInSecs")),
        "est_distance_km": units.km(w.get("estimatedDistanceInMeters")),
        "pool_length": f"{w.get('poolLength')} {dig(w, 'poolLengthUnit', 'unitKey') or ''}".strip()
        if w.get("poolLength")
        else None,
        "steps": [_step(s) for s in steps],
    }


def _workout_row(w: dict) -> dict:
    return {
        "id": w.get("workoutId"),
        "name": w.get("workoutName"),
        "sport": dig(w, "sportType", "sportTypeKey"),
        "est_duration": units.hms(w.get("estimatedDurationInSecs")),
        "est_distance_km": units.km(w.get("estimatedDistanceInMeters")),
        "updated": trim_local(w.get("updatedDate") or w.get("updateDate")),
    }


def get_workout(ctx: Ctx, workout_id: str) -> dict:
    """A saved workout by number, or a Garmin Coach workout by UUID."""
    text = str(workout_id).strip()
    if "-" in text:
        return ctx.g.connectapi(f"/workout-service/fbt-adaptive/{text}") or {}
    if not text.isdigit():
        raise InputError("workout_id is a number, or a UUID for Garmin Coach workouts")
    return ctx.g.get_workout_by_id(int(text)) or {}


def workouts(ctx: Ctx, workout_id: str | None, raw: bool) -> str:
    if workout_id:
        w = get_workout(ctx, workout_id)
        return shape.raw(w) if raw else dumps(workout_view(w))
    rows = ctx.g.get_workouts(0, 100) or []
    return shape.raw(rows) if raw else dumps([_workout_row(w) for w in rows])


# ------------------------------------------------------------------ calendar


def _window(ctx: Ctx, start: str | None, end: str | None) -> tuple[date, date]:
    """The calendar looks forward: by default the next two weeks."""
    a = ctx.day(start, name="start_date") if start else ctx.today()
    b = ctx.day(end, name="end_date") if end else a + timedelta(days=13)
    if a > b:
        raise InputError(f"start_date {a} is after end_date {b}")
    if (b - a).days + 1 > MAX_CALENDAR_DAYS:
        raise InputError(f"At most {MAX_CALENDAR_DAYS} days per call.")
    return a, b


def scheduled(ctx: Ctx, a: date, b: date) -> list[dict]:
    query = {"query": f'query{{workoutScheduleSummariesScalar(startDate:"{a}", endDate:"{b}")}}'}
    rows = dig(ctx.g.query_garmin_graphql(query), "data", "workoutScheduleSummariesScalar") or []
    out = []
    for r in rows:
        out.append(
            {
                "date": first(r.get("scheduleDate"), r.get("calendarDate"), r.get("date")),
                # Live payload: scheduledWorkoutId (the schedule response calls it workoutScheduleId).
                "scheduled_id": first(r.get("scheduledWorkoutId"), r.get("workoutScheduleId"), r.get("id")),
                "workout_id": r.get("workoutId"),
                "name": first(r.get("workoutName"), r.get("name"), r.get("title")),
                "sport": first(r.get("workoutType"), r.get("sportTypeKey"), dig(r, "sportType", "sportTypeKey")),
                "est_duration": units.hms(r.get("estimatedDurationInSecs")),
                "completed": first(r.get("associatedActivityId"), r.get("completedActivityId")),
            }
        )
    return sorted(out, key=lambda r: r.get("date") or "")


def _months(a: date, b: date):
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def events(ctx: Ctx, a: date, b: date) -> list[dict]:
    """Races and events. The library's get_scheduled_workouts is really the whole
    calendar feed for a month, events included."""
    seen, out = set(), []
    for year, month in _months(a, b):
        feed = ctx.g.get_scheduled_workouts(year, month) or {}
        for item in feed.get("calendarItems") or []:
            day = (item.get("date") or "")[:10]
            if item.get("itemType") != "event" or not (a.isoformat() <= day <= b.isoformat()):
                continue
            key = (item.get("shareableEventUuid"), item.get("title"), day)
            if key in seen:
                continue
            seen.add(key)
            target = item.get("completionTarget") or {}
            meters = None
            if target.get("value") is not None:
                meters = float(target["value"]) * DISTANCE_UNIT_M.get(target.get("unit"), 1)
            out.append(
                {
                    "date": day,
                    "title": item.get("title"),
                    "race": bool(item.get("isRace")) or None,
                    "distance_km": units.km(meters),
                    "start_time": item.get("startTimestampLocal") or item.get("eventTimeLocal"),
                    "location": item.get("location"),
                    "goal_time": _goal_time(ctx, item),
                    "url": item.get("url"),
                }
            )
    return out


def _goal_time(ctx: Ctx, item: dict) -> str | None:
    event_id, uuid = item.get("id"), item.get("shareableEventUuid")
    try:
        if event_id:
            detail = ctx.g.connectapi(f"/calendar-service/event/{event_id}")
        elif uuid:
            detail = ctx.g.connectapi(f"/calendar-service/event/{uuid}/shareable")
        else:
            return None
    except Exception:  # noqa: BLE001 - the goal time is a nice-to-have
        return None
    seconds = dig(detail, "eventCustomization", "customGoal", "value")
    return units.hms(seconds) if seconds else None


def coach(ctx: Ctx, a: date, b: date) -> list[dict]:
    query = {
        "query": f'query{{trainingPlanScalar(calendarDate:"{a}", lang:"en-US", firstDayOfWeek:"monday")}}'
    }
    plans = dig(ctx.g.query_garmin_graphql(query), "data", "trainingPlanScalar", "trainingPlanWorkoutScheduleDTOS") or []
    out = []
    for plan in plans:
        for w in plan.get("workoutScheduleSummaries") or []:
            day = first(w.get("scheduleDate"), w.get("calendarDate"), w.get("date"))
            if day and not (a.isoformat() <= day[:10] <= b.isoformat()):
                continue
            out.append(
                {
                    "date": day,
                    "plan": first(plan.get("trainingPlanName"), plan.get("name")),
                    "name": first(w.get("workoutName"), w.get("name")),
                    "workout_uuid": first(w.get("workoutUuid"), w.get("uuid")),
                    "sport": first(w.get("workoutType"), w.get("sportTypeKey"), dig(w, "sportType", "sportTypeKey")),
                    "est_duration": units.hms(w.get("estimatedDurationInSecs")),
                }
            )
    return sorted(out, key=lambda r: r.get("date") or "")


# ------------------------------------------------------------------ writing


def _scheduled_keys(ctx: Ctx, a: date, b: date) -> set[tuple[str, int]]:
    return {
        ((r.get("date") or "")[:10], int(r["workout_id"]))
        for r in scheduled(ctx, a, b)
        if r.get("workout_id") is not None
    }


def _schedule(ctx: Ctx, workout_id: int, day: date, existing: set) -> str:
    """Schedule once: Garmin happily schedules the same workout twice on a day."""
    if (day.isoformat(), int(workout_id)) in existing:
        return f"workout {workout_id} was already on {day}"
    result = ctx.g.schedule_workout(int(workout_id), day.isoformat()) or {}
    sid = first(result.get("workoutScheduleId"), result.get("scheduleId"), result.get("id"))
    existing.add((day.isoformat(), int(workout_id)))
    return f"workout {workout_id} scheduled on {day}" + (f" (scheduled_id {sid})" if sid else "")


def create_workout(
    ctx: Ctx,
    name: str,
    sport: str,
    steps: list,
    description: str | None,
    schedule_date: str | None,
    pool_length_m: float | None,
) -> str:
    from .workout_dsl import build

    day = ctx.day(schedule_date, name="schedule_date") if schedule_date else None
    workout = build(name, sport, steps, description, pool_length_m)
    created = ctx.g.upload_workout(workout) or {}
    workout_id = created.get("workoutId")
    lines = [f"Created workout '{workout['workoutName']}' ({sport})" + (f", id {workout_id}" if workout_id else "") + "."]
    if day and workout_id:
        lines.append(_schedule(ctx, workout_id, day, _scheduled_keys(ctx, day, day)).capitalize() + ".")
    lines.append(dumps(workout_view(created if created.get("workoutSegments") else workout)))
    return "\n".join(lines)


def upload_workouts(ctx: Ctx, items: list[dict]) -> str:
    from .workout_dsl import normalize

    if not items:
        raise InputError("Give at least one workout.")
    # Check them all before uploading any.
    ready = [normalize(w) for w in items]
    results = []
    for w in ready:
        try:
            created = ctx.g.upload_workout(w) or {}
            results.append({"name": w.get("workoutName"), "workout_id": created.get("workoutId")})
        except Exception as exc:  # noqa: BLE001 - report each, keep going
            results.append({"name": w.get("workoutName"), "error": str(exc)})
    return dumps(results)


def schedule_workouts(ctx: Ctx, items: list[dict]) -> str:
    if not items:
        raise InputError("Give at least one {date, workout_id}.")
    plan = []
    for item in items:
        if not isinstance(item, dict) or "workout_id" not in item or "date" not in item:
            raise InputError("Each item is {\"date\": \"YYYY-MM-DD\", \"workout_id\": 123}.")
        workout_id = str(item["workout_id"]).strip()
        if not workout_id.isdigit():
            raise InputError(f"workout_id {item['workout_id']!r} must be a saved workout's number")
        plan.append((ctx.day(str(item["date"])), int(workout_id)))
    first_day, last_day = min(d for d, _ in plan), max(d for d, _ in plan)
    existing = _scheduled_keys(ctx, first_day, last_day)
    return "\n".join(_schedule(ctx, wid, d, existing) for d, wid in plan)


def calendar(ctx: Ctx, start: str | None, end: str | None, include: list[str]) -> str:
    include = include or list(CALENDAR_PARTS)
    bad = [p for p in include if p not in CALENDAR_PARTS]
    if bad:
        raise InputError(f"Unknown part(s) {bad}. Choose from: {', '.join(CALENDAR_PARTS)}")
    a, b = _window(ctx, start, end)
    out: dict[str, Any] = {"start": a.isoformat(), "end": b.isoformat()}
    if "scheduled" in include:
        out["scheduled"] = scheduled(ctx, a, b)
    if "events" in include:
        out["events"] = events(ctx, a, b)
    if "coach" in include:
        out["coach"] = coach(ctx, a, b)
    return dumps(out)
