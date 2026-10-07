"""MCP server for Garmin Connect.

Tool logic lives in `tools/*` as plain functions that take a `Ctx` and return text, so
it can be tested without MCP. This module only declares the tools: their names,
parameters and descriptions (what the model sees), and the annotations that tell a
client which ones change the Garmin account.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Callable
from typing import Annotated, Any, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import config, shape
from .client import Ctx, GarminSession
from .errors import GarminMcpError, GarminTimeout
from .tools import (
    account,
    activities,
    body,
    courses,
    delete,
    downloads,
    fit,
    nutrition,
    resources,
    training,
    trends,
    wellness,
    womens_health,
    workout_dsl,
    workouts,
    writes,
)

Detail = Literal["summary", "full"]
DateText = Annotated[str, Field(description="YYYY-MM-DD, 'today' or 'yesterday'")]
OptDate = Annotated[str | None, Field(description="YYYY-MM-DD, 'today' or 'yesterday'")]

INSTRUCTIONS = """\
The user's Garmin Connect account: what their watch records (activities, sleep, HRV,
body battery, stress, training readiness and more), read live from Garmin.

- Dates are calendar days in the account's time zone (garmin_status shows it). Today's
  numbers are partial until the day is over, and only as fresh as the last watch sync;
  say so when you use them.
- Start with the summary views. Ask for detail="full" only when the summary lacks what
  you need. Answers are compact JSON with the unit in each key name.
- If a call says the user is not logged in or the login has expired, give them the
  terminal command from the message. Never ask for a Garmin password or MFA code in chat.
- Writes change the real Garmin account and may sync to the watch: confirm with the user
  before any write.
- garmin_delete only previews unless confirm=true. Use confirm=true only after the user
  has said yes to that preview.
"""

# What each kind of tool may do, as MCP annotations. Garmin is a closed world: one
# account, not the open web.
KINDS: dict[str, ToolAnnotations] = {
    "read": ToolAnnotations(read_only_hint=True, open_world_hint=False),
    "local": ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
    ),
    "write": ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
    "delete": ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False),
}
CHANGES_GARMIN = {"write", "delete"}

TOOLS: list[tuple[Callable[..., Any], str]] = []
_session: GarminSession | None = None


def tool(kind: str):
    """Collect a tool; build_server decides whether to register it."""

    def collect(fn):
        TOOLS.append((fn, kind))
        return fn

    return collect


def session() -> GarminSession:
    if _session is None:
        raise RuntimeError("build_server() has not been called")
    return _session


async def _call(fn: Callable[[Ctx], str], *, scale: float = 1.0) -> str:
    return await _run(lambda s: s.run(fn), scale)


async def _call_raw(fn: Callable[[GarminSession], str]) -> str:
    return await _run(lambda s: s.run_raw(fn), 1.0)


async def _run(job: Callable[[GarminSession], str], scale: float) -> str:
    s = session()
    timeout = s.cfg.call_timeout * scale
    try:
        if timeout > 0:
            with anyio.fail_after(timeout):
                text = await anyio.to_thread.run_sync(lambda: job(s), abandon_on_cancel=True)
        else:
            text = await anyio.to_thread.run_sync(lambda: job(s))
    except TimeoutError:
        raise ToolError(str(GarminTimeout(timeout))) from None
    except GarminMcpError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        raise ToolError(f"Unexpected {type(exc).__name__}: {exc}") from exc
    return shape.cap(text, s.cfg.max_chars)


# ------------------------------------------------------------------- status


@tool("read")
async def garmin_status() -> str:
    """Login and connection check: who is logged in, how long the saved login lasts,
    when the watch last synced, and the last error. Call this first when another
    Garmin call fails, or to see how current the data is."""
    return await _call_raw(account.status)


@tool("read")
async def garmin_profile(
    section: Literal[account.PROFILE_SECTIONS] = "profile",
    sport: Annotated[
        str | None, Field(description="For hr_zones / power_zones: RUNNING, CYCLING, ...")
    ] = None,
) -> str:
    """The account owner. profile: body measurements, VO2max, lactate threshold, usual
    sleep window. settings: Garmin's raw user settings. hr_zones: heart-rate zones in
    bpm per sport. power_zones: power zones."""
    return await _call(lambda ctx: account.profile(ctx, section, sport))


# ------------------------------------------------------------------- activities


@tool("read")
async def garmin_activities(
    start_date: Annotated[
        str | None,
        Field(description="First day (YYYY-MM-DD/today/yesterday). Omit both dates for the latest."),
    ] = None,
    end_date: Annotated[str | None, Field(description="Last day, inclusive. Default today.")] = None,
    activity_type: Annotated[
        str | None,
        Field(description="Type key, e.g. running, cycling, walking, strength_training"),
    ] = None,
    limit: Annotated[int, Field(description="Per page, 1-100. 0 returns only the total count.")] = 20,
    page: Annotated[int, Field(description="Page number, from 0")] = 0,
) -> str:
    """List activities, newest first: id, type, start time, distance, time, pace or speed,
    heart rate, training load and effect. Pass an id to garmin_activity for detail."""
    return await _call(
        lambda ctx: activities.activities(ctx, start_date, end_date, activity_type, limit, page)
    )


@tool("read")
async def garmin_activity(
    activity_id: Annotated[int, Field(description="Activity id from garmin_activities")],
    sections: Annotated[
        list[Literal[activities.SECTIONS]] | None,
        Field(
            description=(
                "What to include (default summary). summary; splits (laps); typed_splits "
                "and split_summaries (run/walk/interval segments); weather; hr_zones and "
                "power_zones (time in zone); gear; exercise_sets (strength); training_effect; "
                "details (time series of HR, pace, elevation, cadence, power)"
            )
        ),
    ] = None,
    detail: Annotated[Detail, Field(description="full = Garmin's raw payloads")] = "summary",
    points: Annotated[int, Field(ge=10, le=2000, description="Samples kept in details")] = 100,
) -> str:
    """One activity in as much depth as needed, one or more sections per call."""
    chosen = sections or ["summary"]
    scale = 2.0 if "details" in chosen or len(chosen) > 4 else 1.0
    return await _call(
        lambda ctx: activities.activity(ctx, activity_id, chosen, detail, points), scale=scale
    )


# ------------------------------------------------------------------- health


@tool("read")
async def garmin_wellness(
    metric: Literal[wellness.METRICS],
    date: DateText = "today",
    detail: Annotated[Detail, Field(description="full = Garmin's raw payload (large)")] = "summary",
) -> str:
    """One day of wellness data.
    summary: steps, calories, intensity minutes, resting/min/max HR, stress, body battery,
      SpO2, respiration, active and sedentary time.
    sleep: the night that ENDED on `date` (date=today is last night): times, stages,
      score, HRV, resting HR, SpO2. hrv: the same night's HRV, baseline and status.
    body_battery: charged/drained, high/low and the events that moved it.
    stress, heart_rate, steps: day totals plus an hour-by-hour profile.
    Also resting_hr, spo2, respiration, floors, hydration, intensity_minutes,
    events (auto-detected movement), lifestyle (lifestyle logging)."""
    return await _call(lambda ctx: wellness.wellness(ctx, metric, date, detail))


@tool("read")
async def garmin_training(
    metric: Literal[training.METRICS],
    date: DateText = "today",
    detail: Annotated[Detail, Field(description="full = Garmin's raw payload")] = "summary",
) -> str:
    """Training state on a day (default today).
    readiness: every training-readiness reading that day, with its factors;
    morning_readiness: the one after waking. recovery_time: hours still needed.
    status: training status, weekly load, acute/chronic load, ACWR, form, VO2max.
    load_balance: four-week aerobic low/high and anaerobic load against targets.
    Also fitness_age, vo2max, acclimation (heat/altitude), running_tolerance,
    race_predictions (5k to marathon), lactate_threshold, cycling_ftp."""
    return await _call(lambda ctx: training.training(ctx, metric, date, detail))


@tool("read")
async def garmin_trend(
    metric: Literal[trends.METRICS],
    start_date: Annotated[
        str | None, Field(description="First day. Default depends on the metric (2-13 weeks back)")
    ] = None,
    end_date: Annotated[str | None, Field(description="Last day, inclusive. Default today")] = None,
    aggregation: Annotated[
        Literal["daily", "weekly"],
        Field(description="weekly works for steps, stress, intensity_minutes, running_tolerance"),
    ] = "daily",
) -> str:
    """A metric across a date range, one row per day (or week).
    stats (steps + calories), steps, calories, stress, intensity_minutes, sleep (per night),
    hrv (per night), respiration, resting_hr, body_battery, weight, training_load (acute,
    chronic, ACWR, form per day), vo2max (the dates it changed), running_tolerance,
    endurance_score, hill_score, lactate_threshold, progress (activity totals by type).
    Most allow up to a year; respiration and daily stress/intensity minutes 31 days,
    training_load 90 days."""
    return await _call(
        lambda ctx: trends.trend(ctx, metric, start_date, end_date, aggregation), scale=4.0
    )


@tool("read")
async def garmin_body(
    kind: Literal[body.KINDS],
    start_date: OptDate = None,
    end_date: Annotated[
        str | None, Field(description="Last day, inclusive. Omit for just start_date")
    ] = None,
) -> str:
    """Body measurements over a period (default today): weigh_ins (each one, with body
    composition when a smart scale sent it), body_composition (entries plus averages),
    blood_pressure. Ids in the answer are what garmin_delete needs."""
    return await _call(lambda ctx: body.body(ctx, kind, start_date, end_date))


# ------------------------------------------------------------------- account


@tool("read")
async def garmin_devices(
    kind: Literal[account.DEVICE_KINDS] = "list",
    device_id: Annotated[str | None, Field(description="Default: the last synced device")] = None,
    date: OptDate = None,
) -> str:
    """Watches and other devices: list, last_used (and when it last synced), settings,
    primary (training device), solar (charging intake on a date), alarms."""
    return await _call(lambda ctx: account.devices(ctx, kind, device_id, date))


@tool("read")
async def garmin_gear(
    include_stats: Annotated[bool, Field(description="Add distance and activity count per item")] = True,
) -> str:
    """Shoes, bikes and other gear: status, default activity types, total distance."""
    return await _call(lambda ctx: account.gear(ctx, include_stats), scale=2.0)


@tool("read")
async def garmin_achievements(
    kind: Literal[account.ACHIEVEMENT_KINDS],
    status: Annotated[Literal["active", "future", "past"], Field(description="For goals")] = "active",
    start: Annotated[int, Field(description="Position to start from, counting from 1")] = 1,
    limit: Annotated[int, Field(description="How many, 1-100")] = 20,
) -> str:
    """Goals, personal records, badges (earned, available, in progress) and challenges
    (adhoc, badge, available, unfinished, virtual)."""
    return await _call(lambda ctx: account.achievements(ctx, kind, status, start, limit))


@tool("read")
async def garmin_reference(
    kind: Literal[account.REFERENCE_KINDS],
    query: Annotated[str | None, Field(description="Filter / search text")] = None,
) -> str:
    """Lookup lists: activity_types (type keys, grouped), event_types (for activities),
    strength_exercises (Garmin's exercise catalog; search with query),
    food_serving_units (for custom foods)."""
    return await _call(lambda ctx: account.reference(ctx, kind, query))


@tool("read")
async def garmin_api_get(
    path: Annotated[
        str, Field(description="Garmin Connect API path, e.g. /usersummary-service/stats/stress/weekly/2026-10-01/4")
    ],
    params: Annotated[dict[str, Any] | None, Field(description="Query parameters")] = None,
) -> str:
    """Read-only escape hatch: GET any Garmin Connect API path and return the raw JSON.
    Use only when no other garmin_ tool covers what is needed."""
    return await _call(lambda ctx: account.api_get(ctx, path, params))


# ------------------------------------------------------------------- workouts and calendar


@tool("read")
async def garmin_workouts(
    workout_id: Annotated[
        str | None,
        Field(description="Omit to list saved workouts. A number, or a UUID for Garmin Coach workouts"),
    ] = None,
    raw: Annotated[bool, Field(description="Garmin's workout JSON instead of the readable steps")] = False,
) -> str:
    """Saved workouts: the list, or one workout's steps (duration or distance, targets,
    repeats, strength exercises)."""
    return await _call(lambda ctx: workouts.workouts(ctx, workout_id, raw))


@tool("read")
async def garmin_calendar(
    start_date: Annotated[str | None, Field(description="First day. Default today")] = None,
    end_date: Annotated[str | None, Field(description="Last day. Default two weeks after start")] = None,
    include: Annotated[
        list[Literal[workouts.CALENDAR_PARTS]] | None,
        Field(description="scheduled (workouts on the calendar), events (races, with goal time), coach (Garmin Coach plan). Default all"),
    ] = None,
) -> str:
    """The training calendar, looking forward by default. Scheduled entries carry the
    scheduled_id that garmin_delete kind=scheduled_workouts needs."""
    return await _call(lambda ctx: workouts.calendar(ctx, start_date, end_date, include or []), scale=2.0)


# ------------------------------------------------------------------- nutrition


@tool("read")
async def garmin_nutrition(
    kind: Literal[nutrition.KINDS],
    date: OptDate = None,
    start_date: Annotated[str | None, Field(description="For summary / energy_balance")] = None,
    end_date: Annotated[str | None, Field(description="For summary / energy_balance. Default today")] = None,
) -> str:
    """Garmin Connect's food log. food_log, meals and
    settings (calorie and macro goals) for one date; summary (per-day totals) and
    energy_balance (intake against burn, and what the weight trend implies) over up to
    61 days."""
    return await _call(lambda ctx: nutrition.nutrition(ctx, kind, date, start_date, end_date), scale=2.0)


@tool("read")
async def garmin_food_search(
    query: Annotated[str, Field(description="Food name to search for")] = "",
    source: Annotated[
        Literal["catalog", "custom"], Field(description="catalog = Garmin/FatSecret foods; custom = your own")
    ] = "catalog",
    start: Annotated[int, Field(description="Offset, from 0")] = 0,
    limit: Annotated[int, Field(description="1-50")] = 20,
) -> str:
    """Search foods, with each serving's id and macros (what garmin_log_food needs)."""
    return await _call(lambda ctx: nutrition.food_search(ctx, query, source, start, limit))


# ------------------------------------------------------------------- courses and more


@tool("read")
async def garmin_courses(
    course_id: Annotated[int | None, Field(description="Omit to list all courses")] = None,
) -> str:
    """Saved courses (routes): the list, or one course with its waypoints."""
    return await _call(lambda ctx: courses.courses(ctx, course_id))


@tool("read")
async def garmin_womens_health(
    kind: Literal[womens_health.KINDS],
    date: OptDate = None,
    start_date: OptDate = None,
    end_date: OptDate = None,
) -> str:
    """Pregnancy summary, menstrual data for one date, or the menstrual calendar for a range."""
    return await _call(lambda ctx: womens_health.womens_health(ctx, kind, date, start_date, end_date))


# ------------------------------------------------------------------- files and FIT analysis


@tool("read")
async def garmin_activity_analysis(
    activity_id: int,
    include: Annotated[
        list[Literal[fit.ANALYSES]] | None,
        Field(
            description=(
                "Default: everything with data. summary, laps, climbs (gain, grade, VAM), grades "
                "(time per grade band), hr_drift (aerobic decoupling), power_curve (best power 5 s "
                "to 60 min), variability (NP, VI, IF, TSS), hrv (R-R), running_dynamics, "
                "cycling_dynamics, di2 (gear use), temperature"
            )
        ),
    ] = None,
) -> str:
    """Deeper analysis from the activity's original FIT file, beyond what Garmin
    Connect shows. Manual activities have no FIT file."""
    return await _call(lambda ctx: fit.activity_analysis(ctx, activity_id, include or []), scale=2.0)


@tool("read")
async def garmin_fit_messages(
    activity_id: int,
    message_types: Annotated[
        list[str] | None,
        Field(description="e.g. session, lap, record, event, hrv, device_info. Default: all small ones"),
    ] = None,
    include_records: Annotated[bool, Field(description="Also the per-second records (large)")] = False,
    offset: int = 0,
    limit: Annotated[int, Field(description="Messages per page, 1-5000")] = 1000,
) -> str:
    """Every field of the activity's FIT file, decoded with Garmin's FIT SDK, paged.
    For questions no other tool answers."""
    return await _call(
        lambda ctx: fit.fit_messages(ctx, activity_id, message_types, include_records, offset, limit), scale=2.0
    )


@tool("read")
async def garmin_power_curve(
    activity_type: Annotated[str, Field(description="cycling, running, virtual_ride, ...")] = "cycling",
    num_activities: Annotated[int, Field(description="Recent activities with power to include, 1-50")] = 20,
) -> str:
    """Best power for 5 s to 60 min across recent activities, with an FTP estimate
    (95 % of the best 20 minutes). Downloads one file per activity, so it is slow."""
    return await _call(lambda ctx: fit.season_power_curve(ctx, activity_type, num_activities), scale=6.0)


@tool("local")
async def garmin_download(
    kind: Literal[downloads.KINDS],
    id: Annotated[int, Field(description="Activity, workout or course id")],
    format: Annotated[
        Literal["fit", "original", "gpx", "tcx", "kml", "csv"],
        Field(description="Activities: any. Workouts: fit. Courses: gpx"),
    ] = "fit",
    output_dir: Annotated[
        str | None, Field(description="Full folder path. Default ~/Downloads/garmin-mcp")
    ] = None,
    remember_dir: Annotated[bool, Field(description="Make output_dir the default from now on")] = False,
) -> str:
    """Save an activity, workout or course as a file on this computer."""
    return await _call(
        lambda ctx: downloads.download(ctx, kind, id, format, output_dir, remember_dir), scale=2.0
    )


# ------------------------------------------------------------------- writes: activities

Feel = Literal["very weak", "weak", "normal", "strong", "very strong"]
When = Annotated[
    str | None, Field(description="'YYYY-MM-DD HH:MM', 'HH:MM' for today, or omit for now")
]


@tool("write")
async def garmin_update_activity(
    activity_id: int,
    name: str | None = None,
    type_key: Annotated[str | None, Field(description="New activity type key")] = None,
    description: Annotated[str | None, Field(description="New description; '' clears it")] = None,
    event_type: Annotated[
        str | None, Field(description="race, training, recreation, transportation, ... ")
    ] = None,
    rpe: Annotated[float | None, Field(description="Perceived effort 1-10; 0 clears it")] = None,
    feel: Annotated[Feel | None, Field(description="How the effort felt")] = None,
    add_gear: Annotated[str | None, Field(description="Gear uuid to attach (garmin_gear)")] = None,
    remove_gear: Annotated[str | None, Field(description="Gear uuid to detach")] = None,
) -> str:
    """Edit an activity: rename, retype, describe, set event type, RPE, feel, or gear.
    Only the fields given change. Confirm with the user first."""
    return await _call(
        lambda ctx: writes.update_activity(
            ctx, activity_id, name, type_key, description, event_type, rpe, feel, add_gear, remove_gear
        )
    )


@tool("write")
async def garmin_create_activity(
    type_key: Annotated[str, Field(description="e.g. running, walking, strength_training, yoga")],
    duration_minutes: int,
    date: DateText = "today",
    start_time: Annotated[str, Field(description="HH:MM local")] = "09:00",
    distance_km: float = 0.0,
    name: str | None = None,
) -> str:
    """Add a manual activity (one the watch didn't record), in the account's time zone."""
    return await _call(
        lambda ctx: writes.create_activity(ctx, type_key, date, start_time, duration_minutes, distance_km, name)
    )


# ------------------------------------------------------------------- writes: body


@tool("write")
async def garmin_add_weigh_in(
    weight: float,
    unit: Literal["kg", "lbs"] = "kg",
    when: When = None,
) -> str:
    """Record a weigh-in in Garmin Connect."""
    return await _call(lambda ctx: writes.add_weigh_in(ctx, weight, unit, when))


@tool("write")
async def garmin_add_body_composition(
    weight_kg: float,
    when: When = None,
    percent_fat: float | None = None,
    percent_hydration: float | None = None,
    muscle_mass: Annotated[float | None, Field(description="kg")] = None,
    bone_mass: Annotated[float | None, Field(description="kg")] = None,
    visceral_fat_mass: Annotated[float | None, Field(description="kg")] = None,
    visceral_fat_rating: float | None = None,
    basal_met: Annotated[float | None, Field(description="kcal/day")] = None,
    active_met: Annotated[float | None, Field(description="kcal/day")] = None,
    metabolic_age: float | None = None,
    physique_rating: float | None = None,
    bmi: float | None = None,
) -> str:
    """Record a full body-composition measurement (as a smart scale would)."""
    return await _call(
        lambda ctx: writes.add_body_composition(
            ctx,
            weight_kg,
            when,
            percent_fat=percent_fat,
            percent_hydration=percent_hydration,
            muscle_mass=muscle_mass,
            bone_mass=bone_mass,
            visceral_fat_mass=visceral_fat_mass,
            visceral_fat_rating=visceral_fat_rating,
            basal_met=basal_met,
            active_met=active_met,
            metabolic_age=metabolic_age,
            physique_rating=physique_rating,
            bmi=bmi,
        )
    )


@tool("write")
async def garmin_add_blood_pressure(
    systolic: int,
    diastolic: int,
    pulse: int | None = None,
    notes: str | None = None,
    when: When = None,
) -> str:
    """Record a blood-pressure reading (mmHg)."""
    return await _call(lambda ctx: writes.add_blood_pressure(ctx, systolic, diastolic, pulse, notes, when))


@tool("write")
async def garmin_add_hydration(
    ml: Annotated[float, Field(description="Millilitres; negative removes")],
    when: Annotated[
        str | None, Field(description="'YYYY-MM-DD HH:MM', 'HH:MM', a date, or omit for now")
    ] = None,
) -> str:
    """Log water intake in Garmin Connect's hydration tracker."""
    return await _call(lambda ctx: writes.add_hydration(ctx, ml, when))


@tool("write")
async def garmin_set_hr_zones(
    sport: Annotated[str, Field(description="DEFAULT, RUNNING, CYCLING, ...")] = "DEFAULT",
    method: Annotated[
        Literal["max_hr", "hrr", "lthr", "custom_bpm"] | None,
        Field(description="% of max HR, % of HR reserve, % of lactate threshold, or exact bpm floors"),
    ] = None,
    max_hr: int | None = None,
    resting_hr: int | None = None,
    lactate_threshold_hr: int | None = None,
    zone_floors: Annotated[
        list[int] | None, Field(description="custom_bpm only: lowest bpm of zones 1-5, increasing")
    ] = None,
) -> str:
    """Change heart-rate zone settings for a sport. With method max_hr and a new max_hr
    the floors are recalculated at 50/60/70/80/90 %. Confirm with the user first."""
    return await _call(
        lambda ctx: writes.set_hr_zones(ctx, sport, method, max_hr, resting_hr, lactate_threshold_hr, zone_floors)
    )


@tool("write")
async def garmin_request_reload(date: DateText) -> str:
    """Ask Garmin to reprocess a day's data (when a day's numbers look stuck or wrong)."""
    return await _call(lambda ctx: writes.request_reload(ctx, date))


# ------------------------------------------------------------------- writes: workouts


@tool("write")
async def garmin_create_workout(
    name: str,
    sport: workout_dsl.Sport,
    steps: Annotated[
        list[workout_dsl.Step | workout_dsl.Repeat],
        Field(description="In order. A step, or {repeat: n, steps: [...]}"),
    ],
    description: str | None = None,
    schedule_date: Annotated[str | None, Field(description="Also put it on the calendar this day")] = None,
    pool_length_m: Annotated[float | None, Field(description="Swimming only")] = None,
) -> str:
    """Build a structured workout and save it to Garmin Connect (it syncs to the watch).
    Each step ends one way (duration, distance_km, reps or lap_button) and has at most
    one target (hr_zone, hr_bpm, pace_min_km, speed_kmh, power_w, power_zone, cadence).
    Strength steps name a catalog exercise. See the workout://templates resources.
    Confirm the plan with the user first."""
    return await _call(
        lambda ctx: workouts.create_workout(ctx, name, sport, steps, description, schedule_date, pool_length_m)
    )


@tool("write")
async def garmin_upload_workouts(
    workouts_json: Annotated[
        list[dict[str, Any]],
        Field(description="Garmin workout JSON objects (garmin_workouts raw=true shows the format)"),
    ],
) -> str:
    """Upload workouts written directly in Garmin's JSON. All are checked before any is
    uploaded (ids vs keys, nested targets, power targets, repeat groups). Prefer
    garmin_create_workout unless the exact JSON matters."""
    return await _call(lambda ctx: workouts.upload_workouts(ctx, workouts_json), scale=2.0)


@tool("write")
async def garmin_schedule_workouts(
    items: Annotated[
        list[dict[str, Any]],
        Field(description='[{"date": "YYYY-MM-DD", "workout_id": 123}, ...]'),
    ],
) -> str:
    """Put saved workouts on the calendar (a week plan at once if needed). A workout
    already on that day is not added twice."""
    return await _call(lambda ctx: workouts.schedule_workouts(ctx, items), scale=2.0)


# ------------------------------------------------------------------- writes: nutrition


@tool("write")
async def garmin_set_nutrition_goals(
    date: DateText = "today",
    calorie_goal: int | None = None,
    carbs_g: float | None = None,
    fat_g: float | None = None,
    protein_g: float | None = None,
    target_date: Annotated[
        str | None, Field(description="Weight-goal date; needed when Garmin's stored one has passed")
    ] = None,
) -> str:
    """Set Garmin Connect's daily calorie and macro goals from a date."""
    return await _call(
        lambda ctx: nutrition.set_goals(ctx, date, calorie_goal, carbs_g, fat_g, protein_g, target_date)
    )


@tool("write")
async def garmin_save_custom_food(
    name: str,
    calories: float,
    serving_unit: Annotated[str, Field(description="G, ML, SERVING, ... (garmin_reference)")] = "G",
    number_of_units: Annotated[float, Field(description="Serving size in that unit")] = 100,
    brand: str | None = None,
    food_id: Annotated[str | None, Field(description="Give to update an existing custom food")] = None,
    serving_id: str | None = None,
    carbs: float | None = None,
    protein: float | None = None,
    fat: float | None = None,
    fiber: float | None = None,
    sugar: float | None = None,
    saturated_fat: float | None = None,
    sodium: float | None = None,
) -> str:
    """Create or update a custom food (grams per serving, sodium in mg)."""
    nutrients = {
        "carbs": carbs,
        "protein": protein,
        "fat": fat,
        "fiber": fiber,
        "sugar": sugar,
        "saturatedFat": saturated_fat,
        "sodium": sodium,
    }
    return await _call(
        lambda ctx: nutrition.save_custom_food(
            ctx, name, calories, serving_unit, number_of_units, brand, food_id, serving_id, nutrients
        )
    )


@tool("write")
async def garmin_log_food(
    meal_time: Annotated[str, Field(description="HH:MM local; picks the meal it falls in")],
    meal_date: DateText = "today",
    food_id: Annotated[str | None, Field(description="A saved food (garmin_food_search)")] = None,
    serving_id: str | None = None,
    serving_qty: float = 1,
    source: Literal["GARMIN", "FATSECRET"] = "GARMIN",
    name: Annotated[str | None, Field(description="Quick add: what it was")] = None,
    calories: float | None = None,
    carbs_g: float | None = None,
    protein_g: float | None = None,
    fat_g: float | None = None,
    save_as_custom: Annotated[
        bool, Field(description="Quick add, but also keep it as a custom food")
    ] = False,
) -> str:
    """Log food to Garmin Connect's food log: a saved food by id and serving, or a quick
    add by name and calories, to Garmin Connect's food log."""
    return await _call(
        lambda ctx: nutrition.log_food(
            ctx, meal_date, meal_time, food_id, serving_id, serving_qty, source, name, calories,
            carbs_g, protein_g, fat_g, save_as_custom,
        )
    )


# ------------------------------------------------------------------- writes: courses


@tool("write")
async def garmin_upload_course(
    gpx_path: Annotated[str, Field(description="Full path to a .gpx file on this computer")],
    name: str | None = None,
    activity_type: Literal[tuple(writes.COURSE_ACTIVITY_PK)] = "running",
    description: str | None = None,
) -> str:
    """Create a private course in Garmin Connect from a GPX file."""
    return await _call(lambda ctx: writes.upload_course(ctx, gpx_path, name, activity_type, description), scale=2.0)


# ------------------------------------------------------------------- delete


@tool("delete")
async def garmin_delete(
    kind: Literal[delete.KINDS],
    ids: Annotated[
        list[str] | None,
        Field(description="Ids from the read tools (weigh-in id, workout id, scheduled_id, ...)"),
    ] = None,
    date: Annotated[
        str | None, Field(description="Needed for weigh_ins, blood_pressure and food_log")
    ] = None,
    confirm: Annotated[
        bool, Field(description="true only after the user agreed to the preview")
    ] = False,
) -> str:
    """Permanently delete from Garmin Connect: weigh_ins (all on the date, or the ids
    given), blood_pressure, workouts, scheduled_workouts (unschedule), custom_food,
    food_log, course. Without confirm it only previews. With confirm=true it deletes
    only what was previewed in the last 15 minutes. There is no undo."""
    return await _call(lambda ctx: delete.delete(ctx, kind, ids, date, confirm))


# ------------------------------------------------------------------- build


def allowed(name: str, kind: str, cfg: config.Config) -> bool:
    if cfg.read_only and kind in CHANGES_GARMIN:
        return False
    if cfg.enabled_tools is not None:
        return name in cfg.enabled_tools
    return name not in cfg.disabled_tools


def build_server(
    cfg: config.Config | None = None,
    opener: Callable[[], tuple[Any, Any]] | None = None,
) -> MCPServer:
    global _session
    cfg = cfg or config.load()
    _session = GarminSession(cfg, opener)

    names = {fn.__name__ for fn, _ in TOOLS}
    for listed in sorted((cfg.enabled_tools or frozenset()) | cfg.disabled_tools):
        if listed not in names:
            print(f"garmin-mcp: unknown tool {listed!r} in GARMIN_MCP_*_TOOLS", file=sys.stderr)
    if cfg.enabled_tools is not None and not cfg.enabled_tools & names:
        raise SystemExit("garmin-mcp: GARMIN_MCP_ENABLED_TOOLS names no known tool")

    server = MCPServer(
        "garmin-connect",
        instructions=INSTRUCTIONS,
    )
    for fn, kind in TOOLS:
        if allowed(fn.__name__, kind, cfg):
            # structured_output=False: the text is the answer. Auto-detection would also
            # send it a second time as structuredContent (taxuspt #331).
            server.add_tool(fn, annotations=KINDS[kind], structured_output=False)
    resources.register(server)
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="garmin-mcp", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=os.environ.get("GARMIN_MCP_TRANSPORT", "stdio"),
    )
    parser.add_argument("--port", type=int, default=int(os.environ.get("GARMIN_MCP_PORT", "8765")))
    args = parser.parse_args(argv)

    server = build_server()
    if args.transport == "stdio":
        server.run("stdio")
    else:
        # No authentication on this transport, so it only ever listens on loopback.
        server.run("streamable-http", host="127.0.0.1", port=args.port, stateless_http=True)


if __name__ == "__main__":
    main()
