"""Changes to the Garmin account: activity edits, manual activities, body measurements,
heart-rate zones, reprocessing, and course uploads.

Every input is checked before the first write, so a bad field never leaves an
activity half-updated. Timestamps are made timezone-aware in the account's time zone
before they reach garminconnect, which would otherwise read a naive time as the
computer's local time.
"""

from __future__ import annotations

from datetime import UTC
from itertools import pairwise
from pathlib import Path
from typing import Any

from .. import units
from ..client import Ctx
from ..dates import moment
from ..errors import InputError
from ..shape import dumps
from .account import _hr_zones

FEEL_BY_LABEL = {label: value for value, label in units.FEEL.items()}
ACTIVITY_PATH = "/activity-service/activity/{}"
HR_ZONES_PATH = "/biometric-service/heartRateZones"
HR_METHODS = {"max_hr": "HR_MAX", "hrr": "HR_RESERVE", "lthr": "LACTATE_THRESHOLD", "custom_bpm": "HR_MAX"}
# Garmin's default heart-rate zones are 50/60/70/80/90 % of max HR.
MAX_HR_SPLIT = (0.5, 0.6, 0.7, 0.8, 0.9)
COURSE_ACTIVITY_PK = {
    "running": 1,
    "cycling": 2,
    "hiking": 3,
    "walking": 4,
    "trail_running": 5,
    "mountain_biking": 6,
    "road_biking": 7,
    "gravel_cycling": 8,
}


def _activity_types(ctx: Ctx) -> list[dict]:
    return ctx.cached(("activity_types",), lambda: ctx.g.get_activity_types() or [])


def _event_types(ctx: Ctx) -> list[dict]:
    return ctx.cached(
        ("event_types",), lambda: ctx.g.connectapi("/activity-service/activity/eventTypes") or []
    )


def _put_activity(ctx: Ctx, activity_id: int, body: dict) -> None:
    ctx.g.client.put(
        "connectapi", ACTIVITY_PATH.format(activity_id), json={"activityId": activity_id, **body}, api=True
    )


def update_activity(
    ctx: Ctx,
    activity_id: int,
    name: str | None = None,
    type_key: str | None = None,
    description: str | None = None,
    event_type: str | None = None,
    rpe: float | None = None,
    feel: str | None = None,
    add_gear: str | None = None,
    remove_gear: str | None = None,
) -> str:
    g, aid = ctx.g, int(activity_id)

    # Check everything first.
    if name is not None and not name.strip():
        raise InputError("name can't be empty")
    type_match = event_match = None
    if type_key:
        type_match = next((t for t in _activity_types(ctx) if t.get("typeKey") == type_key), None)
        if not type_match:
            raise InputError(f"Unknown activity type {type_key!r}; see garmin_reference kind=activity_types")
    if event_type:
        event_match = next((t for t in _event_types(ctx) if t.get("typeKey") == event_type), None)
        if not event_match:
            keys = ", ".join(t.get("typeKey") for t in _event_types(ctx))
            raise InputError(f"Unknown event type {event_type!r}. Choose from: {keys}")
    if rpe is not None and not 0 <= rpe <= 10:
        raise InputError("rpe is 1-10 (0 clears it)")
    if feel is not None and feel not in FEEL_BY_LABEL:
        raise InputError(f"feel is one of: {', '.join(FEEL_BY_LABEL)}")

    done = []
    if name is not None:
        g.set_activity_name(str(aid), name.strip())
        done.append(f"name '{name.strip()}'")
    if type_match:
        g.set_activity_type(str(aid), type_match["typeId"], type_match["typeKey"], type_match.get("parentTypeId"))
        done.append(f"type {type_key}")
    if description is not None:
        _put_activity(ctx, aid, {"description": description})
        done.append("description cleared" if not description else "description")
    if event_match:
        dto = {k: event_match.get(k) for k in ("typeId", "typeKey", "sortOrder")}
        _put_activity(ctx, aid, {"eventTypeDTO": dto})
        done.append(f"event type {event_type}")
    if rpe is not None:
        _put_activity(ctx, aid, {"summaryDTO": {"directWorkoutRpe": units.rpe_to_garmin(rpe)}})
        done.append("RPE cleared" if rpe == 0 else f"RPE {rpe:g}")
    if feel is not None:
        _put_activity(ctx, aid, {"summaryDTO": {"directWorkoutFeel": FEEL_BY_LABEL[feel]}})
        done.append(f"feel {feel}")
    if add_gear:
        g.add_gear_to_activity(add_gear, str(aid))
        done.append(f"gear {add_gear} added")
    if remove_gear:
        g.remove_gear_from_activity(remove_gear, str(aid))
        done.append(f"gear {remove_gear} removed")
    if not done:
        raise InputError("Nothing to change: give at least one field.")
    ctx.cache.drop(("activity", str(aid)))
    return f"Activity {aid} updated: {', '.join(done)}."


def create_activity(
    ctx: Ctx,
    type_key: str,
    date_text: str,
    start_time: str,
    duration_minutes: int,
    distance_km: float,
    name: str | None,
) -> str:
    d = ctx.day(date_text)
    if d > ctx.today():
        raise InputError("A manual activity can't be in the future.")
    if duration_minutes <= 0:
        raise InputError("duration_minutes must be more than 0")
    if distance_km < 0:
        raise InputError("distance_km can't be negative")
    when, _ = moment(f"{d.isoformat()} {start_time}", ctx.tz)
    if not any(t.get("typeKey") == type_key for t in _activity_types(ctx)):
        raise InputError(f"Unknown activity type {type_key!r}; see garmin_reference kind=activity_types")
    title = (name or "").strip() or type_key.replace("_", " ").title()
    # The account's own time zone, not UTC (taxuspt defaulted to UTC). Garmin wants an
    # IANA name; with only a fixed offset known, send the start in UTC instead.
    zone = getattr(ctx.tz, "key", None)
    start = when if zone else when.astimezone(UTC)
    created = ctx.g.create_manual_activity(
        start.strftime("%Y-%m-%dT%H:%M:00.000"), zone or "UTC", type_key, float(distance_km), int(duration_minutes), title
    )
    activity_id = created.get("activityId") if isinstance(created, dict) else None
    return f"Created '{title}' ({type_key}) on {when:%Y-%m-%d %H:%M}, {duration_minutes} min" + (
        f", {distance_km:g} km" if distance_km else ""
    ) + (f". Activity id {activity_id}." if activity_id else ".")


def add_weigh_in(ctx: Ctx, weight: float, unit: str, when: str | None) -> str:
    if weight <= 0:
        raise InputError("weight must be positive")
    at, _ = moment(when, ctx.tz)
    ctx.g.add_weigh_in(weight, unit, timestamp=at.isoformat())
    return f"Weigh-in saved: {weight:g} {unit} at {at:%Y-%m-%d %H:%M}."


def add_body_composition(ctx: Ctx, weight_kg: float, when: str | None, **metrics: Any) -> str:
    if weight_kg <= 0:
        raise InputError("weight_kg must be positive")
    at, _ = moment(when, ctx.tz)
    given = {k: v for k, v in metrics.items() if v is not None}
    ctx.g.add_body_composition(at.isoformat(), weight_kg, **given)
    extra = ", ".join(f"{k} {v:g}" for k, v in given.items())
    return f"Body composition saved for {at:%Y-%m-%d %H:%M}: {weight_kg:g} kg" + (f", {extra}" if extra else "") + "."


def add_blood_pressure(ctx: Ctx, systolic: int, diastolic: int, pulse: int | None, notes: str | None, when: str | None) -> str:
    if not (70 <= systolic <= 260 and 40 <= diastolic <= 150):
        raise InputError("systolic is 70-260 and diastolic 40-150 mmHg")
    if pulse is not None and not 20 <= pulse <= 250:
        raise InputError("pulse is 20-250 bpm")
    at, _ = moment(when, ctx.tz)
    ctx.g.set_blood_pressure(systolic, diastolic, pulse, timestamp=at.isoformat(), notes=notes or "")
    return f"Blood pressure saved: {systolic}/{diastolic}" + (f", pulse {pulse}" if pulse else "") + f" at {at:%Y-%m-%d %H:%M}."


def add_hydration(ctx: Ctx, ml: float, when: str | None) -> str:
    if ml == 0 or abs(ml) > 10_000:
        raise InputError("ml must be non-zero and at most 10 000 (negative removes water)")
    at, has_time = moment(when, ctx.tz)
    if has_time:
        ctx.g.add_hydration_data(ml, timestamp=at.isoformat())
    else:
        ctx.g.add_hydration_data(ml, cdate=at.date().isoformat())
    verb = "Added" if ml > 0 else "Removed"
    return f"{verb} {abs(ml):g} ml of water on {at:%Y-%m-%d}."


def set_hr_zones(
    ctx: Ctx,
    sport: str,
    method: str | None,
    max_hr: int | None,
    resting_hr: int | None,
    lactate_threshold_hr: int | None,
    zone_floors: list[int] | None,
) -> str:
    g, sport_key = ctx.g, sport.upper()
    if method is not None and method not in HR_METHODS:
        raise InputError(f"method is one of: {', '.join(HR_METHODS)}")
    for label, v in (("max_hr", max_hr), ("resting_hr", resting_hr), ("lactate_threshold_hr", lactate_threshold_hr)):
        if v is not None and not 1 <= v <= 300:
            raise InputError(f"{label} must be 1-300 bpm")
    if zone_floors is not None and method != "custom_bpm":
        raise InputError("zone_floors only go with method='custom_bpm'")
    if method == "custom_bpm" and (not zone_floors or len(zone_floors) != 5):
        raise InputError("custom_bpm needs exactly five zone_floors (the lowest bpm of zones 1-5)")

    zones = g.connectapi(HR_ZONES_PATH) or []
    current = next((z for z in zones if z.get("sport") == sport_key), None)
    if current is None:
        base = next((z for z in zones if z.get("sport") == "DEFAULT"), {})
        current = {**base, "sport": sport_key}
    cfg = dict(current)
    if method:
        cfg["trainingMethod"] = HR_METHODS[method]
    if max_hr is not None:
        cfg["maxHeartRateUsed"] = max_hr
    if resting_hr is not None:
        cfg["restingHeartRateUsed"] = resting_hr
        cfg["restingHrAutoUpdateUsed"] = False
    if lactate_threshold_hr is not None:
        cfg["lactateThresholdHeartRateUsed"] = lactate_threshold_hr

    top = cfg.get("maxHeartRateUsed")
    note = ""
    if zone_floors:
        floors = list(zone_floors)
    elif max_hr is not None and cfg.get("trainingMethod") == "HR_MAX" and top:
        floors = [round(top * p) for p in MAX_HR_SPLIT]
        note = " Zone floors recalculated as 50/60/70/80/90 % of max HR."
    else:
        floors = None
        if max_hr is not None or resting_hr is not None or lactate_threshold_hr is not None:
            note = " Zone floors were left as they were; pass method='custom_bpm' with zone_floors to change them."
    if floors:
        if any(b <= a for a, b in pairwise(floors)):
            raise InputError("zone floors must strictly increase")
        if top and floors[-1] > top:
            raise InputError("zone 5 can't start above max HR")
        for i, floor in enumerate(floors, 1):
            cfg[f"zone{i}Floor"] = floor
    if cfg.get("restingHeartRateUsed") and top and cfg["restingHeartRateUsed"] >= top:
        raise InputError("resting HR must be below max HR")
    if cfg.get("lactateThresholdHeartRateUsed") and top and cfg["lactateThresholdHeartRateUsed"] > top:
        raise InputError("lactate threshold HR can't be above max HR")
    cfg["changeState"] = "CHANGED"

    g.client.request("PUT", "connectapi", HR_ZONES_PATH, json=[cfg], api=True)
    saved = next((z for z in g.connectapi(HR_ZONES_PATH) or [] if z.get("sport") == sport_key), cfg)
    return f"Heart-rate zones for {sport_key} saved.{note} Now: {dumps(_hr_zones(saved))}"


def request_reload(ctx: Ctx, date_text: str) -> str:
    d = ctx.day(date_text)
    ctx.g.request_reload(d.isoformat())
    ctx.cache.clear()
    return f"Asked Garmin to reprocess {d}. Give it a minute before reading that day again."


def upload_course(ctx: Ctx, gpx_path: str, name: str | None, activity_type: str, description: str | None) -> str:
    path = Path(gpx_path).expanduser()
    if path.suffix.lower() != ".gpx":
        raise InputError("Only .gpx files can be uploaded as courses.")
    if not path.is_file():
        raise InputError(f"No file at {path}")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise InputError("That GPX file is over 20 MB.")
    if activity_type not in COURSE_ACTIVITY_PK:
        raise InputError(f"activity_type is one of: {', '.join(COURSE_ACTIVITY_PK)}")
    g = ctx.g
    skeleton = g.client.post(
        "connectapi",
        "/course-service/course/import",
        files={"file": (path.name, path.read_bytes(), "application/gpx+xml")},
        api=True,
    ) or {}
    body = {
        **skeleton,
        "courseName": (name or "").strip() or skeleton.get("courseName") or path.stem,
        "description": description or skeleton.get("description"),
        "activityTypePk": COURSE_ACTIVITY_PK[activity_type],
        "rulePK": 2,  # private
        "sourceTypeId": 3,  # GPX
    }
    created = g.client.post("connectapi", "/course-service/course", json=body, api=True) or {}
    course_id = created.get("courseId")
    km = units.km(created.get("distanceMeter") or skeleton.get("distanceMeter"))
    return f"Course '{body['courseName']}' created" + (f" (id {course_id})" if course_id else "") + (
        f", {km} km" if km else ""
    ) + ". It is private and can be sent to the watch from Garmin Connect."
