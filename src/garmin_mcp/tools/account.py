"""The account itself: login status, profile, devices, gear, achievements, reference data."""

from __future__ import annotations

from datetime import datetime
from importlib import metadata
from typing import Any

from .. import auth, dates, shape, units
from ..client import Ctx, GarminSession, tz_name
from ..dates import local_time, trim_local
from ..errors import LOGIN_COMMAND, AuthExpired, InputError, NotLoggedIn, TokenUnreadable
from ..shape import dig, dumps, first


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "?"


def status(session: GarminSession) -> str:
    """Never raises: a status check has to work precisely when things are broken."""
    cfg = session.cfg
    state = auth.token_state(cfg)
    lines: list[str] = []

    if state.status == "missing":
        lines.append(str(NotLoggedIn(cfg.token_file)))
    elif state.status in ("unreadable", "corrupt"):
        lines.append(str(TokenUnreadable(cfg.token_file, state.detail)))
    elif state.status == "expired":
        lines.append(str(AuthExpired(state.detail)))
    else:
        try:
            g = session.ctx().g
            name = g.get_full_name() or getattr(g, "display_name", None) or "(no name)"
            lines.append(f"Logged in to Garmin Connect as {name}.")
            units = getattr(g, "unit_system", None)
            if units:
                lines.append(f"Account units: {units}. Answers are always metric.")
            last = g.get_device_last_used() or {}
            device = last.get("lastUsedDeviceName")
            synced = dates.local_time(last.get("lastUsedDeviceUploadTime"), session.tz)
            if device or synced:
                lines.append(f"Last watch sync: {device or 'device'} at {synced or 'unknown time'}.")
            session.last_ok_at = datetime.now(session.tz)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            lines.append(f"Garmin login check FAILED: {session.failure(exc)}")

    lines.append(state.describe(session.tz))
    source = "GARMIN_MCP_TIMEZONE" if cfg.timezone else (
        "the Garmin account" if session.logged_in else "this computer, until logged in"
    )
    lines.append(f"Dates are read in {tz_name(session.tz)} (from {source}).")

    if session.hybrid is not None:
        lines.append(f"HTTP: {session.hybrid.describe()}.")
    elif session.logged_in:
        lines.append(
            "HTTP: requests only. The curl_cffi 403 fallback is unavailable because "
            "garminconnect's internal session attribute changed."
        )

    wait = auth.cooldown_remaining(cfg)
    if wait:
        left, reason = wait
        lines.append(
            f"A password login failed recently ({reason}); the login command waits "
            f"{int(left.total_seconds() // 60) + 1} more minutes before trying again."
        )

    if session.last_error:
        when, text = session.last_error
        lines.append(f"Last error ({when:%H:%M}): {text}")

    if cfg.read_only:
        lines.append("Read-only mode: write and delete tools are switched off.")

    lines.append(
        f"Versions: garmin-mcp {_version('garmin-mcp')}, garminconnect "
        f"{_version('garminconnect')}, mcp {_version('mcp')}."
    )
    if state.status != "ok":
        lines.append(f"Login command: {LOGIN_COMMAND}")
    return "\n".join(lines)


# ------------------------------------------------------------------ profile

PROFILE_SECTIONS = ("profile", "settings", "hr_zones", "power_zones")


def _clock(seconds) -> str | None:
    """Seconds after midnight as HH:MM."""
    if seconds is None:
        return None
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def _hr_zones(zone: dict) -> dict:
    floors = [zone.get(f"zone{i}Floor") for i in range(1, 6)]
    top = zone.get("maxHeartRateUsed")
    ranges = {}
    for i, low in enumerate(floors):
        if low is None:
            continue
        high = floors[i + 1] - 1 if i < 4 and floors[i + 1] is not None else top
        ranges[f"z{i + 1}"] = [low, high]
    return {
        "sport": zone.get("sport"),
        "method": zone.get("trainingMethod"),
        "max_hr_bpm": top,
        "resting_hr_bpm": zone.get("restingHeartRateUsed"),
        "lactate_threshold_hr_bpm": zone.get("lactateThresholdHeartRateUsed"),
        "zones_bpm": ranges,
    }


def profile(ctx: Ctx, section: str, sport: str | None) -> str:
    g = ctx.g
    if section == "profile":
        p = g.get_user_profile() or {}
        u = p.get("userData") or {}
        sleep = p.get("userSleep") or {}
        lt_speed = units.lactate_speed_mps(u.get("lactateThresholdSpeed"))
        return dumps(
            {
                "name": g.get_full_name() or getattr(g, "display_name", None),
                "gender": u.get("gender"),
                "birth_date": u.get("birthDate"),
                "weight_kg": units.kg(u.get("weight")),
                "height_cm": units.num(u.get("height")),
                "units": u.get("measurementSystem"),
                "first_day_of_week": dig(u, "firstDayOfWeek", "dayName") or u.get("firstDayOfWeek"),
                "vo2max_running": u.get("vo2MaxRunning"),
                "vo2max_cycling": u.get("vo2MaxCycling"),
                "lactate_threshold_hr_bpm": units.whole(u.get("lactateThresholdHeartRate")),
                "lactate_threshold_pace_min_km": units.pace_min_km(lt_speed),
                "activity_level": u.get("activityLevel"),
                "intensity_minutes_hr_zones": {
                    "moderate": u.get("moderateIntensityMinutesHrZone"),
                    "vigorous": u.get("vigorousIntensityMinutesHrZone"),
                },
                "usual_bedtime": _clock(sleep.get("sleepTime")),
                "usual_wake": _clock(sleep.get("wakeTime")),
            }
        )
    if section == "settings":
        return dumps(g.get_userprofile_settings())
    if section == "hr_zones":
        zones = g.get_heart_rate_zones() or []
        if sport:
            zones = [z for z in zones if (z.get("sport") or "").lower() in (sport.lower(), "default")]
        return dumps([_hr_zones(z) for z in zones])
    if section == "power_zones":
        return dumps(g.get_power_zones_for_sport(sport.upper()) if sport else g.get_power_zones())
    raise InputError(f"Unknown section {section!r}. Choose from: {', '.join(PROFILE_SECTIONS)}")


# ------------------------------------------------------------------ devices

DEVICE_KINDS = ("list", "last_used", "settings", "primary", "solar", "alarms")


def _last_used_id(ctx: Ctx) -> str:
    last = ctx.g.get_device_last_used() or {}
    device = last.get("userDeviceId") or last.get("deviceId")
    if not device:
        raise InputError("No device has synced yet, so give device_id explicitly.")
    return str(device)


def devices(ctx: Ctx, kind: str, device_id: str | None, date_text: str | None) -> str:
    g = ctx.g
    if kind == "list":
        return dumps(
            [
                {
                    "id": d.get("deviceId"),
                    "name": d.get("displayName") or d.get("productDisplayName"),
                    "product": d.get("productDisplayName"),
                    "firmware": d.get("currentFirmwareVersion"),
                    "primary_tracker": d.get("primaryActivityTrackerIndicator") or None,
                    "status": d.get("deviceStatus"),
                }
                for d in g.get_devices() or []
            ]
        )
    if kind == "last_used":
        last = g.get_device_last_used() or {}
        return dumps(
            {
                "id": last.get("userDeviceId"),
                "name": last.get("lastUsedDeviceName"),
                "last_sync": local_time(last.get("lastUsedDeviceUploadTime"), ctx.tz),
            }
        )
    if kind == "settings":
        return dumps(g.get_device_settings(device_id or _last_used_id(ctx)))
    if kind == "primary":
        data = g.get_primary_training_device() or {}

        def names(group: str) -> list:
            return [
                {"id": d.get("deviceId"), "name": d.get("displayName")}
                for d in dig(data, group, "deviceWeights") or []
            ]

        primary_id = dig(data, "PrimaryTrainingDevice", "deviceId")
        known = {d["id"]: d["name"] for d in names("RegisteredDevices") + names("WearableDevices")}
        return dumps(
            {
                "primary_training_device": {"id": primary_id, "name": known.get(primary_id)},
                "wearables": names("WearableDevices"),
                "registered": names("RegisteredDevices"),
            }
        )
    if kind == "solar":
        d = ctx.day(date_text)
        return dumps(g.get_device_solar_data(device_id or _last_used_id(ctx), d.isoformat()))
    if kind == "alarms":
        return dumps(g.get_device_alarms())
    raise InputError(f"Unknown kind {kind!r}. Choose from: {', '.join(DEVICE_KINDS)}")


# ------------------------------------------------------------------ gear


def profile_number(ctx: Ctx) -> str:
    pid = getattr(ctx.g, "profile_id", None)
    if not pid:
        pid = (ctx.g.get_device_last_used() or {}).get("userProfileNumber")
    if not pid:
        raise InputError("Could not find the account's profile number for the gear list.")
    return str(pid)


def activity_type_keys(ctx: Ctx) -> dict[int, str]:
    types = ctx.cached(("activity_types",), lambda: ctx.g.get_activity_types() or [])
    return {t.get("typeId"): t.get("typeKey") for t in types if isinstance(t, dict)}


def gear(ctx: Ctx, include_stats: bool) -> str:
    g, pid = ctx.g, profile_number(ctx)
    items = g.get_gear(pid) or []
    defaults = g.get_gear_defaults(pid) or []
    keys = activity_type_keys(ctx) if defaults else {}
    default_for: dict[str, list] = {}
    for d in defaults if isinstance(defaults, list) else []:
        if d.get("defaultGear"):
            default_for.setdefault(d.get("uuid"), []).append(
                keys.get(d.get("activityTypePk"), d.get("activityTypePk"))
            )
    out = []
    for item in items if isinstance(items, list) else []:
        uuid = item.get("uuid")
        row: dict[str, Any] = {
            "uuid": uuid,
            "name": item.get("displayName") or item.get("customMakeModel"),
            "type": item.get("gearTypeName"),
            "status": item.get("gearStatusName"),
            "make": item.get("gearMakeName"),
            "model": item.get("gearModelName"),
            "since": trim_local(item.get("dateBegin"))[:10] if item.get("dateBegin") else None,
            "retired": trim_local(item.get("dateEnd"))[:10] if item.get("dateEnd") else None,
            "max_km": units.km(item.get("maximumMeters"), 0) or None,
            "default_for": default_for.get(uuid),
        }
        if include_stats and uuid:
            stats = g.get_gear_stats(uuid) or {}
            row["total_km"] = units.km(stats.get("totalDistance"), 1)
            row["activities"] = stats.get("totalActivities")
        out.append(row)
    return dumps(out)


# ------------------------------------------------------------------ achievements

ACHIEVEMENT_KINDS = (
    "goals",
    "personal_records",
    "badges_earned",
    "badges_available",
    "badges_in_progress",
    "challenges_adhoc",
    "challenges_badge",
    "challenges_available",
    "challenges_unfinished",
    "challenges_virtual",
)

# Garmin's personal-record type ids: (label, kind of value).
PR_TYPES = {
    1: ("run_1km", "time"),
    2: ("run_1mile", "time"),
    3: ("run_5km", "time"),
    4: ("run_10km", "time"),
    5: ("run_half_marathon", "time"),
    6: ("run_marathon", "time"),
    7: ("longest_run", "distance"),
    8: ("longest_ride", "distance"),
    9: ("most_ride_ascent", "elevation"),
    10: ("best_20min_ride_power", "power"),
    11: ("ride_40km", "time"),
    12: ("most_steps_day", "count"),
    13: ("most_steps_week", "count"),
    14: ("most_steps_month", "count"),
    15: ("longest_goal_streak", "count"),
}


def _pr(r: dict) -> dict:
    label, kind = PR_TYPES.get(r.get("typeId"), (f"type_{r.get('typeId')}", None))
    value = r.get("value")
    shown: Any = value
    if kind == "time":
        shown = units.hms(value)
    elif kind == "distance":
        shown = f"{units.km(value)} km"
    elif kind == "elevation":
        shown = f"{units.whole(value)} m"
    elif kind == "power":
        shown = f"{units.whole(value)} W"
    elif kind == "count":
        shown = units.whole(value)
    return {
        "record": label,
        "value": shown,
        "date": (r.get("prStartTimeLocalFormatted") or r.get("actStartDateTimeInGMTFormatted") or "")[:10]
        or None,
        "activity_id": r.get("activityId"),
        "activity": r.get("activityName"),
    }


def _badge(b: dict) -> dict:
    return {
        "name": b.get("badgeName"),
        "earned": trim_local(b.get("badgeEarnedDate"))[:10] if b.get("badgeEarnedDate") else None,
        "times": b.get("badgeEarnedNumber") if (b.get("badgeEarnedNumber") or 0) > 1 else None,
        "points": b.get("badgePoints"),
        "progress": b.get("badgeProgressValue"),
        "target": b.get("badgeTargetValue"),
    }


def achievements(ctx: Ctx, kind: str, status: str, start: int, limit: int) -> str:
    g = ctx.g
    if start < 1 or not 1 <= limit <= 100:
        raise InputError("start counts from 1, and limit is 1-100")
    window = slice(start - 1, start - 1 + limit)
    if kind == "goals":
        return dumps(g.get_goals(status=status, start=start, limit=limit))
    if kind == "personal_records":
        # Garmin also returns placeholder records with value 0 and no activity.
        records = [r for r in g.get_personal_record() or [] if r.get("value") or r.get("activityId")]
        return dumps([_pr(r) for r in records])
    if kind == "badges_earned":
        badges = sorted(g.get_earned_badges() or [], key=lambda b: b.get("badgeEarnedDate") or "", reverse=True)
        return dumps([_badge(b) for b in badges[window]])
    if kind == "badges_available":
        return dumps([_badge(b) for b in (g.get_available_badges() or [])[window]])
    if kind == "badges_in_progress":
        return dumps([_badge(b) for b in (g.get_in_progress_badges() or [])[window]])
    calls = {
        "challenges_adhoc": lambda: g.get_adhoc_challenges(start - 1, limit),
        "challenges_badge": lambda: g.get_badge_challenges(start, limit),
        "challenges_available": lambda: g.get_available_badge_challenges(start, limit),
        "challenges_unfinished": lambda: g.get_non_completed_badge_challenges(start, limit),
        "challenges_virtual": lambda: g.get_inprogress_virtual_challenges(start, limit),
    }
    if kind not in calls:
        raise InputError(f"Unknown kind {kind!r}. Choose from: {', '.join(ACHIEVEMENT_KINDS)}")
    data = calls[kind]()
    items = data if isinstance(data, list) else None
    if items is None and isinstance(data, dict):
        items = next((v for v in data.values() if isinstance(v, list)), None)
    if items is None:
        return dumps(data)
    return dumps([_challenge(c) for c in items if isinstance(c, dict)])


def _challenge(c: dict) -> dict:
    return {
        "name": first(c.get("badgeChallengeName"), c.get("adHocChallengeName"), c.get("challengeName"), c.get("name")),
        "start": (c.get("startDate") or "")[:10] or None,
        "end": (c.get("endDate") or "")[:10] or None,
        "joined": c.get("userJoined"),
        "progress": c.get("badgeProgressValue"),
        "target": c.get("badgeTargetValue"),
        "points": c.get("badgePoints"),
        "partner": c.get("partnerName"),
        "id": first(c.get("uuid"), c.get("badgeChallengeUuid"), c.get("adHocChallengePk")),
    }


# ------------------------------------------------------------------ reference

REFERENCE_KINDS = (
    "activity_types",
    "event_types",
    "strength_exercises",
    "food_serving_units",
    "workout_structure",
)


def reference(ctx: Ctx, kind: str, query: str | None) -> str:
    q = (query or "").strip().lower()
    if kind == "activity_types":
        types = ctx.cached(("activity_types",), lambda: ctx.g.get_activity_types() or [])
        by_id = {t.get("typeId"): t.get("typeKey") for t in types}
        grouped: dict[str, list[str]] = {}
        for t in types:
            key = t.get("typeKey") or ""
            if q and q not in key:
                continue
            parent = by_id.get(t.get("parentTypeId"), "top_level")
            grouped.setdefault(parent, []).append(key)
        return dumps(grouped)
    if kind == "event_types":
        rows = ctx.g.connectapi("/activity-service/activity/eventTypes") or []
        return dumps([r.get("typeKey") for r in rows])
    if kind == "strength_exercises":
        from garminconnect import exercises

        hits = exercises.find(q) if q else exercises.EXERCISES
        if not q:
            return dumps({"categories": list(exercises.CATEGORIES), "note": "Pass query to search names."})
        return dumps(hits[:60] + ([{"more": len(hits) - 60}] if len(hits) > 60 else []))
    if kind == "food_serving_units":
        from .nutrition import serving_units

        return dumps(serving_units(ctx))
    if kind == "workout_structure":
        from .resources import structure

        return dumps(structure())
    raise InputError(f"Unknown kind {kind!r}. Choose from: {', '.join(REFERENCE_KINDS)}")


# ------------------------------------------------------------------ passthrough


def api_get(ctx: Ctx, path: str, params: dict | None) -> str:
    path = (path or "").strip()
    if "://" in path or not path.startswith("/"):
        raise InputError("path must be a Garmin Connect API path such as /usersummary-service/...")
    if "?" in path:
        raise InputError("Put query values in params, not in the path.")
    return shape.raw(ctx.g.connectapi(path, params=params or None))
