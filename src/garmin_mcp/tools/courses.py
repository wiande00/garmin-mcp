"""Courses (routes) saved in Garmin Connect. Not wrapped by garminconnect; the endpoints
follow taxuspt's courses.py."""

from __future__ import annotations

from typing import Any

from .. import units
from ..client import Ctx
from ..dates import trim_local
from ..shape import dig, dumps, first

COURSE_URL = "https://connect.garmin.com/modern/course/{}"


def get_course(ctx: Ctx, course_id: int) -> dict:
    return ctx.g.connectapi(f"/course-service/course/{int(course_id)}") or {}


def _course_row(c: dict) -> dict:
    return {
        "id": c.get("courseId"),
        "name": c.get("courseName"),
        "activity": first(dig(c, "activityType", "typeKey"), c.get("activityTypeKey")),
        "distance_km": units.km(first(c.get("distanceInMeters"), c.get("distanceMeter"))),
        "elev_gain_m": units.whole(first(c.get("elevationGainInMeters"), c.get("elevationGainMeter"))),
        "elev_loss_m": units.whole(first(c.get("elevationLossInMeters"), c.get("elevationLossMeter"))),
        "created": trim_local(c.get("createdDate") or c.get("createDate")),
    }


def courses(ctx: Ctx, course_id: int | None) -> str:
    if course_id is None:
        rows = ctx.g.connectapi("/course-service/course") or []
        return dumps([_course_row(c) for c in rows if isinstance(c, dict)])
    c = get_course(ctx, course_id)
    out: dict[str, Any] = _course_row(c)
    # coursePoints can be an explicit null (taxuspt #377), so never iterate it blindly.
    points = c.get("coursePoints") or []
    out["waypoints"] = [
        {
            "name": p.get("name"),
            "type": p.get("pointType"),
            "km": units.km(p.get("distance")),
        }
        for p in points
        if isinstance(p, dict)
    ]
    out["track_points"] = len(c.get("geoPoints") or [])
    out["url"] = COURSE_URL.format(c.get("courseId") or course_id)
    return dumps(out)
