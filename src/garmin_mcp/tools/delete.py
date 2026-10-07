"""The one destructive tool. It always previews first.

A call without confirm=true only describes what would be deleted. A call with
confirm=true deletes only if that exact request was previewed in this session in the
last 15 minutes, so nothing can be deleted that the user wasn't shown. Garmin has no
undo for any of these.
"""

from __future__ import annotations

import time

from .. import units
from ..client import Ctx
from ..errors import InputError
from ..shape import dig, dumps
from .body import blood_pressure_rows, weigh_entry

KINDS = ("weigh_ins", "blood_pressure", "workouts", "scheduled_workouts", "custom_food", "food_log", "course")
NEEDS_DATE = {"weigh_ins", "blood_pressure", "food_log"}
NEEDS_IDS = {"workouts", "scheduled_workouts", "custom_food", "food_log", "course"}
PREVIEW_TTL = 15 * 60


def _key(kind: str, ids: list[str], date_text: str | None) -> tuple:
    return (kind, tuple(sorted(ids)), date_text or "")


def _previews(ctx: Ctx) -> dict:
    holder = ctx.session if ctx.session is not None else ctx
    if not hasattr(holder, "delete_previews"):
        holder.delete_previews = {}
    return holder.delete_previews


def _plan(ctx: Ctx, kind: str, ids: list[str], day: str | None) -> list[dict]:
    """What would go, read back from Garmin so the preview shows real items."""
    g = ctx.g
    if kind == "weigh_ins":
        entries = [weigh_entry(w, ctx.tz) for w in (g.get_daily_weigh_ins(day) or {}).get("dateWeightList") or []]
        return [e for e in entries if not ids or str(e["id"]) in ids]
    if kind == "blood_pressure":
        rows = blood_pressure_rows(g.get_blood_pressure(day, day) or {})
        return [r for r in rows if not ids or str(r["id"]) in ids]
    if kind == "workouts":
        out = []
        for i in ids:
            w = g.get_workout_by_id(int(i)) or {}
            out.append({"id": i, "name": w.get("workoutName"), "sport": dig(w, "sportType", "sportTypeKey")})
        return out
    if kind == "scheduled_workouts":
        out = []
        for i in ids:
            s = g.get_scheduled_workout_by_id(int(i)) or {}
            out.append(
                {
                    "scheduled_id": i,
                    "date": s.get("date") or s.get("calendarDate"),
                    "workout": dig(s, "workout", "workoutName") or s.get("workoutName"),
                }
            )
        return out
    if kind == "custom_food":
        foods = g.connectapi(
            "/nutrition-service/customFood", params={"start": 0, "limit": 100, "includeContent": "true"}
        )
        foods = foods if isinstance(foods, list) else (foods or {}).get("customFoods") or []
        by_id = {str(dig(f, "foodMetaData", "foodId")): dig(f, "foodMetaData", "foodName") for f in foods}
        return [{"food_id": i, "name": by_id.get(i, "(not found among the first 100 custom foods)")} for i in ids]
    if kind == "food_log":
        log = g.get_nutrition_daily_food_log(day) or {}
        items = [
            {"log_id": str(f.get("logId")), "name": f.get("foodName") or dig(f, "foodMetaData", "foodName")}
            for meal in log.get("mealDetails") or []
            for f in meal.get("loggedFoods") or []
        ]
        return [i for i in items if i["log_id"] in ids] or [{"log_id": i} for i in ids]
    if kind == "course":
        out = []
        for i in ids:
            c = g.connectapi(f"/course-service/course/{int(i)}") or {}
            out.append({"id": i, "name": c.get("courseName"), "distance_km": units.km(c.get("distanceMeter"))})
        return out
    raise InputError(f"Unknown kind {kind!r}. Choose from: {', '.join(KINDS)}")


def _remove(ctx: Ctx, kind: str, items: list[dict], ids: list[str], day: str | None) -> list[str]:
    g, done = ctx.g, []
    for item in items:
        if kind == "weigh_ins":
            g.delete_weigh_in(str(item["id"]), day)
            done.append(f"weigh-in {item.get('weight_kg')} kg at {item.get('time')}")
        elif kind == "blood_pressure":
            g.delete_blood_pressure(str(item["id"]), day)
            done.append(f"blood pressure {item.get('systolic')}/{item.get('diastolic')} at {item.get('time')}")
        elif kind == "workouts":
            g.delete_workout(int(item["id"]))
            done.append(f"workout {item['id']} '{item.get('name')}'")
        elif kind == "scheduled_workouts":
            g.unschedule_workout(int(item["scheduled_id"]))
            done.append(f"scheduled workout {item['scheduled_id']} on {item.get('date')}")
        elif kind == "custom_food":
            g.client.delete("connectapi", f"/nutrition-service/customFood/{item['food_id']}", api=True)
            done.append(f"custom food {item['food_id']} '{item.get('name')}'")
        elif kind == "course":
            g.client.delete("connectapi", f"/course-service/course/{int(item['id'])}", api=True)
            done.append(f"course {item['id']} '{item.get('name')}'")
    if kind == "food_log":
        g.client.delete("connectapi", f"/nutrition-service/food/logs/{day}", json={"logIds": ids}, api=True)
        done.append(f"{len(ids)} food log item(s) on {day}")
    return done


def delete(ctx: Ctx, kind: str, ids: list[str] | None, date_text: str | None, confirm: bool) -> str:
    if kind not in KINDS:
        raise InputError(f"Unknown kind {kind!r}. Choose from: {', '.join(KINDS)}")
    ids = [str(i).strip() for i in ids or [] if str(i).strip()]
    if kind in NEEDS_DATE and not date_text:
        raise InputError(f"Deleting {kind} needs the date they are on.")
    day = ctx.day(date_text).isoformat() if kind in NEEDS_DATE else None
    if kind in NEEDS_IDS and not ids:
        raise InputError(f"Deleting {kind} needs ids (see the matching read tool).")

    items = _plan(ctx, kind, ids, day)
    if not items:
        return f"Nothing to delete: no {kind.replace('_', ' ')} match that request."
    key = _key(kind, ids, day)
    previews = _previews(ctx)
    previewed_at = previews.get(key)

    if not confirm or previewed_at is None or time.monotonic() - previewed_at > PREVIEW_TTL:
        previews[key] = time.monotonic()
        head = "PREVIEW - nothing has been deleted."
        if confirm:
            head = "Not deleted yet: this exact request has to be previewed first. " + head
        return (
            f"{head} This would permanently delete {len(items)} item(s) from Garmin Connect, "
            f"with no undo:\n{dumps(items)}\nShow the user this list. Only if they say yes, call "
            "garmin_delete again with the same kind, ids and date plus confirm=true."
        )

    done = _remove(ctx, kind, items, ids, day)
    previews.pop(key, None)
    ctx.cache.clear()
    return "Deleted: " + "; ".join(done) + "."
