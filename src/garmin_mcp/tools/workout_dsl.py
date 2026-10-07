"""A small, validated step language for building Garmin workouts, and the checks that
make raw workout JSON safe to upload.

Garmin's workout JSON is easy to get subtly wrong, and taxuspt's issues are a catalogue
of the ways: numeric ids override keys (#123, #129), targets nested in `targetType` are
silently dropped (#210), watt ranges sent as type 6 reach the watch as a speed target
of 864 km/h (#245), repeat groups need `conditionTypeId` 7 (#112), strength steps need
a real category (#196, #273). The ids here come from garminconnect.workout, which reads
them from Garmin's /workout-service/workout/types.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import units
from ..errors import InputError

# key -> (id, displayOrder)
SPORTS = {
    "running": (1, 1),
    "cycling": (2, 2),
    "other": (3, 3),
    "swimming": (4, 3),
    "strength_training": (5, 5),
    "cardio_training": (6, 6),
    "yoga": (7, 7),
    "pilates": (8, 8),
    "hiit": (9, 9),
    "multi_sport": (10, 10),
    "mobility": (11, 11),
    "walking": (17, 17),
    "hiking": (18, 18),
}
STEP_TYPES = {
    "warmup": (1, 1),
    "cooldown": (2, 2),
    "interval": (3, 3),
    "recovery": (4, 4),
    "rest": (5, 5),
    "repeat": (6, 6),
    "other": (7, 7),
    "main": (8, 8),
}
CONDITIONS = {
    "lap.button": 1,
    "time": 2,
    "distance": 3,
    "calories": 4,
    "power": 5,
    "heart.rate": 6,
    "iterations": 7,
    "fixed.rest": 8,
    "fixed.repetition": 9,
    "reps": 10,
    "training.peaks.tss": 11,
}
TARGETS = {
    "no.target": 1,
    "power.zone": 2,
    "cadence": 3,
    "heart.rate.zone": 4,
    "speed.zone": 5,
    "pace.zone": 6,
    "grade": 7,
    "heart.rate.lap": 8,
    "power.lap": 9,
    "resistance": 15,
}
WEIGHT_UNIT_KILOGRAM = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
MAX_NAME = 256

Sport = Literal[tuple(SPORTS)]  # type: ignore[valid-type]


class Step(BaseModel):
    """One step. End it one way (duration, distance_km, reps or lap_button) and give at
    most one target."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["warmup", "interval", "recovery", "rest", "cooldown", "other"] = "interval"
    duration: str | None = Field(None, description="Time: 'm:ss', 'h:mm:ss' or seconds, e.g. '10:00'")
    distance_km: float | None = Field(None, gt=0, description="Distance, e.g. 0.4 for 400 m")
    reps: int | None = Field(None, gt=0, description="Strength: repetitions")
    lap_button: bool = Field(False, description="Ends when the lap button is pressed")
    hr_zone: int | None = Field(None, ge=1, le=5, description="Heart-rate zone 1-5")
    hr_bpm: tuple[int, int] | None = Field(None, description="Heart-rate range [low, high] bpm")
    pace_min_km: tuple[str, str] | None = Field(None, description="Pace range, e.g. ['4:30', '4:45']")
    speed_kmh: tuple[float, float] | None = Field(None, description="Speed range [low, high] km/h")
    power_w: tuple[int, int] | None = Field(None, description="Power range [low, high] watts")
    power_zone: int | None = Field(None, ge=1, le=7, description="Power zone")
    cadence: tuple[int, int] | None = Field(None, description="Cadence range [low, high] spm or rpm")
    exercise: str | None = Field(
        None, description="Strength: Garmin catalog name, e.g. 'Barbell Bench Press' (see garmin_reference)"
    )
    weight_kg: float | None = Field(None, gt=0, description="Strength: load in kg")
    note: str | None = Field(None, description="Text shown on the watch for this step")


class Repeat(BaseModel):
    """Repeat the steps inside it."""

    model_config = ConfigDict(extra="forbid")

    repeat: int = Field(ge=1, le=99, description="How many times")
    steps: list[Step] = Field(min_length=1)


Item = Step | Repeat


# ------------------------------------------------------------------ building


def _seconds(text: str) -> float:
    text = str(text).strip()
    if text.isdigit():
        return float(text)
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        raise InputError(f"duration {text!r} must be 'm:ss', 'h:mm:ss' or seconds") from None
    if len(nums) == 2:
        return float(nums[0] * 60 + nums[1])
    if len(nums) == 3:
        return float(nums[0] * 3600 + nums[1] * 60 + nums[2])
    raise InputError(f"duration {text!r} must be 'm:ss', 'h:mm:ss' or seconds")


def _target(key: str, order: int = 1) -> dict:
    return {"workoutTargetTypeId": TARGETS[key], "workoutTargetTypeKey": key, "displayOrder": order}


def _end(key: str) -> dict:
    return {
        "conditionTypeId": CONDITIONS[key],
        "conditionTypeKey": key,
        "displayOrder": CONDITIONS[key],
        "displayable": key != "iterations",
    }


def _step_type(key: str) -> dict:
    sid, order = STEP_TYPES[key]
    return {"stepTypeId": sid, "stepTypeKey": key, "displayOrder": order}


def _plain(text: str) -> str:
    """Compare names without case, spaces or hyphens: 'Pull Up' is 'Pull-up'."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def resolve_exercise(name: str) -> tuple[str, str]:
    """(category, exerciseName), trying in order: the exact display name, a category key
    (a generic exercise), a unique exercise key, a unique part of a display name."""
    from garminconnect import exercises

    wanted = _plain(name)
    for e in exercises.EXERCISES:
        if _plain(e["name"]) == wanted:
            return e["category"], e["exercise"]
    key = "_".join(name.strip().upper().replace("-", " ").split())
    if key in exercises.CATEGORIES:
        return key, ""
    by_key = [e for e in exercises.EXERCISES if e["exercise"] == key]
    if len(by_key) == 1:
        return by_key[0]["category"], by_key[0]["exercise"]
    hits = by_key or [e for e in exercises.EXERCISES if wanted and wanted in _plain(e["name"])]
    if len(hits) == 1:
        return hits[0]["category"], hits[0]["exercise"]
    suggestions = ", ".join(h["name"] for h in hits[:8]) or "none; try a shorter search"
    raise InputError(f"Exercise {name!r} is not in Garmin's catalog. Closest: {suggestions}")


def _executable(step: Step, order: int, sport: str) -> dict:
    endings = [
        n
        for n, given in (
            ("duration", step.duration is not None),
            ("distance_km", step.distance_km is not None),
            ("reps", step.reps is not None),
            ("lap_button", step.lap_button),
        )
        if given
    ]
    if len(endings) > 1:
        raise InputError(f"Step {order} ends {' and '.join(endings)}; a step ends one way only.")
    if step.duration is not None:
        end, value = "time", _seconds(step.duration)
    elif step.distance_km is not None:
        end, value = "distance", float(step.distance_km) * 1000
    elif step.reps is not None:
        end, value = "reps", float(step.reps)
    else:
        end, value = "lap.button", None

    targets = {
        "hr_zone": step.hr_zone,
        "hr_bpm": step.hr_bpm,
        "pace_min_km": step.pace_min_km,
        "speed_kmh": step.speed_kmh,
        "power_w": step.power_w,
        "power_zone": step.power_zone,
        "cadence": step.cadence,
    }
    given = [k for k, v in targets.items() if v is not None]
    if len(given) > 1:
        raise InputError(f"Step {order} has {' and '.join(given)}; give one target (a zone or a range).")

    out: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": _step_type(step.type),
        "endCondition": _end(end),
        "endConditionValue": value,
        "targetType": _target("no.target"),
    }
    if step.hr_zone is not None:
        out.update(targetType=_target("heart.rate.zone"), zoneNumber=step.hr_zone)
    elif step.hr_bpm is not None:
        low, high = sorted(step.hr_bpm)
        out.update(targetType=_target("heart.rate.zone"), targetValueOne=float(low), targetValueTwo=float(high))
    elif step.pace_min_km is not None:
        speeds = sorted(1000 / _seconds(p) for p in step.pace_min_km)  # slower pace first
        out.update(targetType=_target("pace.zone"), targetValueOne=speeds[0], targetValueTwo=speeds[1])
    elif step.speed_kmh is not None:
        low, high = sorted(step.speed_kmh)
        out.update(targetType=_target("speed.zone"), targetValueOne=low / 3.6, targetValueTwo=high / 3.6)
    elif step.power_w is not None:
        # Watt ranges are power.zone (2) with values, never type 6 (taxuspt #245).
        low, high = sorted(step.power_w)
        out.update(targetType=_target("power.zone"), targetValueOne=float(low), targetValueTwo=float(high))
    elif step.power_zone is not None:
        out.update(targetType=_target("power.zone"), zoneNumber=step.power_zone)
    elif step.cadence is not None:
        low, high = sorted(step.cadence)
        out.update(targetType=_target("cadence"), targetValueOne=float(low), targetValueTwo=float(high))

    if step.exercise:
        category, exercise_name = resolve_exercise(step.exercise)
        out.update(category=category, exerciseName=exercise_name)
    elif sport == "strength_training" and end == "reps":
        raise InputError(f"Step {order}: a strength step with reps needs an exercise.")
    if step.weight_kg is not None:
        out.update(weightValue=float(step.weight_kg) * 1000.0, weightUnit=dict(WEIGHT_UNIT_KILOGRAM))
    if step.note:
        out["description"] = step.note
    return out


def build(name: str, sport: str, items: list[Item], description: str | None = None, pool_length_m: float | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise InputError("The workout needs a name.")
    if len(name) > MAX_NAME:
        raise InputError(f"Workout names are limited to {MAX_NAME} characters (taxuspt #352).")
    if sport not in SPORTS:
        raise InputError(f"sport is one of: {', '.join(SPORTS)}")
    if not items:
        raise InputError("The workout needs at least one step.")

    sid, sorder = SPORTS[sport]
    sport_type = {"sportTypeId": sid, "sportTypeKey": sport, "displayOrder": sorder}
    order = 0
    steps: list[dict] = []
    estimate = 0.0
    for item in items:
        if isinstance(item, Repeat):
            order += 1
            group_order = order
            children = []
            for child in item.steps:
                order += 1
                children.append(_executable(child, order, sport))
            steps.append(
                {
                    "type": "RepeatGroupDTO",
                    "stepOrder": group_order,
                    "stepType": _step_type("repeat"),
                    "numberOfIterations": item.repeat,
                    "smartRepeat": False,
                    "endCondition": _end("iterations"),
                    "endConditionValue": float(item.repeat),
                    "workoutSteps": children,
                }
            )
            estimate += item.repeat * sum(
                c["endConditionValue"] or 0 for c in children if c["endCondition"]["conditionTypeKey"] == "time"
            )
        else:
            order += 1
            step = _executable(item, order, sport)
            steps.append(step)
            if step["endCondition"]["conditionTypeKey"] == "time":
                estimate += step["endConditionValue"]

    workout: dict[str, Any] = {
        "workoutName": name,
        "sportType": sport_type,
        "estimatedDurationInSecs": int(estimate),
        "workoutSegments": [{"segmentOrder": 1, "sportType": sport_type, "workoutSteps": steps}],
    }
    if description:
        workout["description"] = description
    if sport == "swimming":
        if not pool_length_m:
            raise InputError("A swimming workout needs pool_length_m (e.g. 25).")
        workout["poolLength"] = pool_length_m
        workout["poolLengthUnit"] = {"unitId": 1, "unitKey": "meter", "factor": 100.0}
    return workout


# ------------------------------------------------------------------ raw JSON checks

_NESTED = ("targetValueOne", "targetValueTwo", "zoneNumber")


def _check_step(step: dict, path: str, problems: list[str]) -> None:
    kind = step.get("type")
    if kind == "RepeatGroupDTO":
        end = step.setdefault("endCondition", {})
        if end.get("conditionTypeKey", "iterations") == "iterations":
            end.setdefault("conditionTypeKey", "iterations")
            end["conditionTypeId"] = CONDITIONS["iterations"]
        if step.get("numberOfIterations") is None:
            if step.get("endConditionValue") is None:
                problems.append(f"{path}: repeat group without numberOfIterations")
            else:
                step["numberOfIterations"] = int(step["endConditionValue"])
        for i, child in enumerate(step.get("workoutSteps") or [], 1):
            _check_step(child, f"{path}.{i}", problems)
        return
    if kind != "ExecutableStepDTO":
        problems.append(f"{path}: type must be ExecutableStepDTO or RepeatGroupDTO, not {kind!r}")
        return

    target = step.get("targetType") or {}
    for field in _NESTED:  # values nested in targetType are dropped by Garmin (taxuspt #210)
        if step.get(field) is None and target.get(field) is not None:
            step[field] = target.pop(field)
    key, tid = target.get("workoutTargetTypeKey"), target.get("workoutTargetTypeId")
    if key == "power.between" or (tid == 6 and key not in (None, "pace.zone")):
        problems.append(
            f"{path}: power ranges must use workoutTargetTypeId 2 'power.zone' with targetValueOne/Two "
            "(type 6 reaches the watch as a pace target, taxuspt #245)"
        )
    elif key is not None and key in TARGETS and tid is not None and TARGETS[key] != tid:
        problems.append(f"{path}: workoutTargetTypeId {tid} does not match {key!r} ({TARGETS[key]})")
    elif key in TARGETS and tid is None:
        target["workoutTargetTypeId"] = TARGETS[key]
    if key == "heart.rate.zone" and step.get("zoneNumber") is None and step.get("targetValueTwo") is None:
        one = step.get("targetValueOne")
        if one is not None and 1 <= float(one) <= 5:
            step["zoneNumber"], step["targetValueOne"] = int(one), None
    if step.get("zoneNumber") is not None and step.get("targetValueTwo") is not None:
        problems.append(f"{path}: give a zoneNumber or a targetValueOne/Two range, not both")

    end = step.get("endCondition") or {}
    ekey, eid = end.get("conditionTypeKey"), end.get("conditionTypeId")
    if ekey in CONDITIONS and eid is not None and CONDITIONS[ekey] != eid:
        problems.append(f"{path}: conditionTypeId {eid} does not match {ekey!r} ({CONDITIONS[ekey]})")
    elif ekey in CONDITIONS and eid is None:
        end["conditionTypeId"] = CONDITIONS[ekey]


def normalize(workout: dict) -> dict:
    """Fix what can be fixed safely, refuse what can't."""
    if not isinstance(workout, dict):
        raise InputError("Each workout must be a JSON object.")
    w = copy.deepcopy(workout)
    problems: list[str] = []
    name = (w.get("workoutName") or "").strip()
    if not name:
        problems.append("workoutName is missing")
    elif len(name) > MAX_NAME:
        problems.append(f"workoutName is over {MAX_NAME} characters")
    sport = w.get("sportType") or {}
    skey, sid = sport.get("sportTypeKey"), sport.get("sportTypeId")
    if skey in SPORTS and sid is not None and SPORTS[skey][0] != sid:
        problems.append(f"sportTypeId {sid} does not match {skey!r} ({SPORTS[skey][0]})")
    elif skey in SPORTS and sid is None:
        sport["sportTypeId"] = SPORTS[skey][0]
    segments = w.get("workoutSegments") or []
    if not segments:
        problems.append("workoutSegments is empty")
    for s, seg in enumerate(segments, 1):
        for i, step in enumerate(seg.get("workoutSteps") or [], 1):
            _check_step(step, f"segment {s} step {i}", problems)
    if problems:
        raise InputError("Workout not uploaded: " + "; ".join(problems))
    return w


def estimate_text(workout: dict) -> str | None:
    return units.hms(workout.get("estimatedDurationInSecs")) if workout.get("estimatedDurationInSecs") else None
