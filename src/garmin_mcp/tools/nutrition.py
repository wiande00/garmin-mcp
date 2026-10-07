"""Garmin Connect's food log: logged meals, goals, food search, and energy balance.

These cover Garmin Connect's own nutrition feature: whatever is logged in the Connect
app. None of these endpoints are wrapped by garminconnect, so the paths and payloads
follow Taxuspt/garmin_mcp's nutrition.py.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .. import shape, units
from ..client import Ctx
from ..dates import chunks
from ..errors import InputError
from ..shape import dig, dumps, first

KINDS = ("food_log", "meals", "settings", "summary", "energy_balance")
MAX_RANGE_DAYS = 61
KCAL_PER_KG = 7700  # energy in a kilogram of body-weight change, the usual rule of thumb


def _content(c: dict | None) -> dict:
    c = c or {}
    return {
        "kcal": units.whole(c.get("calories")),
        "carbs_g": units.num(c.get("carbs")),
        "protein_g": units.num(c.get("protein")),
        "fat_g": units.num(c.get("fat")),
    }


def _food_log(data: dict) -> dict:
    meals = []
    for meal in data.get("mealDetails") or []:
        items = []
        for f in meal.get("loggedFoods") or []:
            items.append(
                {
                    "log_id": f.get("logId"),
                    "name": first(f.get("foodName"), dig(f, "foodMetaData", "foodName"), f.get("name")),
                    "servings": f.get("servingQty"),
                    **_content(first(f.get("nutritionContent"), f.get("nutritionContents"), f)),
                }
            )
        meals.append(
            {
                "meal": first(dig(meal, "meal", "mealName"), meal.get("mealName")),
                "meal_id": first(dig(meal, "meal", "mealId"), meal.get("mealId")),
                "items": items,
            }
        )
    return {
        "date": data.get("mealDate"),
        "totals": _content(data.get("dailyNutritionContent")),
        "goals": _content(data.get("dailyNutritionGoals")) or None,
        "meals": meals,
    }


def _range(ctx: Ctx, a: date, b: date) -> list[dict]:
    rows = []
    for s, e in chunks(a, b, MAX_RANGE_DAYS):
        data = ctx.g.connectapi(
            "/nutrition-service/food/logs/range", params={"startDate": s.isoformat(), "endDate": e.isoformat()}
        ) or {}
        rows += data.get("dailyNutritionSummaries") or []
    return rows


def _summary(ctx: Ctx, a: date, b: date) -> list[dict]:
    out = []
    for day in _range(ctx, a, b):
        items = sum(len(m.get("loggedFoods") or []) for m in day.get("mealDetails") or [])
        out.append({"date": day.get("mealDate"), **_content(day.get("dailyNutritionContent")), "items": items})
    return sorted(out, key=lambda r: r.get("date") or "")


def _slope_per_day(points: list[tuple[int, float]]) -> float | None:
    """Least-squares slope of (day number, kg)."""
    if len(points) < 2:
        return None
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    var = sum((x - mx) ** 2 for x, _ in points)
    if var == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in points) / var


def _energy_balance(ctx: Ctx, a: date, b: date) -> dict:
    intake = {r["date"]: r for r in _summary(ctx, a, b) if r.get("items")}
    burned = {
        c.get("calendarDate"): c.get("total") for c in ctx.g.get_calories_daily(a.isoformat(), b.isoformat()) or []
    }
    days = []
    for day in sorted(set(intake) & set(burned)):
        if day >= ctx.today().isoformat():
            continue  # today is unfinished on both sides
        eaten, spent = intake[day]["kcal"], burned[day]
        if eaten is None or spent is None:
            continue
        days.append({"date": day, "kcal_in": eaten, "kcal_out": units.whole(spent), "balance": units.whole(eaten - spent)})

    weights = ctx.g.get_weigh_ins(a.isoformat(), b.isoformat()) or {}
    points = []
    for s in weights.get("dailyWeightSummaries") or []:
        kg = units.kg(dig(s, "latestWeight", "weight"))
        if kg is not None and s.get("summaryDate"):
            points.append(((date.fromisoformat(s["summaryDate"]) - a).days, kg))
    slope = _slope_per_day(points)

    out: dict[str, Any] = {"days_with_food_and_burn": len(days), "days": days}
    if days:
        out["avg_balance_kcal_per_day"] = round(sum(d["balance"] for d in days) / len(days))
    if slope is not None:
        out["weight_trend_kg_per_week"] = round(slope * 7, 2)
        out["balance_implied_by_weight_kcal_per_day"] = round(slope * KCAL_PER_KG)
        out["weigh_ins"] = len(points)
    if days and slope is not None and len(points) >= 3:
        # Logged intake minus the energy the weight trend says was really eaten:
        # positive means food goes unlogged or the burn estimate is high.
        out["gap_kcal_per_day"] = round(out["balance_implied_by_weight_kcal_per_day"] - out["avg_balance_kcal_per_day"])
    out["note"] = (
        f"Energy balance uses only finished days that have both a food log and a calorie burn. "
        f"The weight-implied balance assumes {KCAL_PER_KG} kcal per kg; it needs at least "
        "two weeks of regular weigh-ins to mean much."
    )
    return out


def nutrition(ctx: Ctx, kind: str, date_text: str | None, start: str | None, end: str | None) -> str:
    g = ctx.g
    if kind in ("food_log", "meals", "settings"):
        d = ctx.day(date_text).isoformat()
        if kind == "food_log":
            data = g.get_nutrition_daily_food_log(d) or {}
            return dumps(_food_log(data) if "mealDetails" in data else data)
        if kind == "meals":
            meals = (g.get_nutrition_daily_meals(d) or {}).get("meals") or []
            return dumps(
                [
                    {"meal_id": m.get("mealId"), "meal": m.get("mealName"), "from": m.get("startTime"), "to": m.get("endTime")}
                    for m in meals
                ]
            )
        settings = g.get_nutrition_daily_settings(d) or {}
        return dumps(
            {
                "date": d,
                "calorie_goal": settings.get("calorieGoal"),
                "macro_goals_g": settings.get("macroGoals"),
                "weight_goal_target_date": settings.get("targetDate"),
                "other": {k: v for k, v in settings.items() if k not in ("calorieGoal", "macroGoals", "targetDate")},
            }
        )
    if kind not in ("summary", "energy_balance"):
        raise InputError(f"Unknown kind {kind!r}. Choose from: {', '.join(KINDS)}")
    a, b = ctx.span(start or date_text, end, max_days=MAX_RANGE_DAYS, default_days=14)
    if kind == "summary":
        return dumps({"start": a.isoformat(), "end": b.isoformat(), "days": _summary(ctx, a, b)})
    return dumps({"start": a.isoformat(), "end": b.isoformat(), **_energy_balance(ctx, a, b)})


# ------------------------------------------------------------------ food search


def _food(f: dict) -> dict:
    meta = f.get("foodMetaData") or {}
    return {
        "food_id": meta.get("foodId"),
        "name": meta.get("foodName"),
        "brand": meta.get("brandName"),
        "source": meta.get("source"),
        "servings": [
            {
                "serving_id": s.get("servingId"),
                "unit": s.get("servingUnit"),
                "units": s.get("numberOfUnits"),
                **_content(s),
            }
            for s in f.get("nutritionContents") or []
        ],
    }


def food_search(ctx: Ctx, query: str, source: str, start: int, limit: int) -> str:
    if not 1 <= limit <= 50 or start < 0:
        raise InputError("limit is 1-50 and start counts from 0")
    params = {"searchExpression": query or "", "start": start, "limit": limit}
    if source == "catalog":
        if not query:
            raise InputError("Give a query to search Garmin's food catalog.")
        data = ctx.g.connectapi("/nutrition-service/food/search", params=params) or {}
        foods = data.get("results") or []
        more = data.get("moreDataAvailable")
    elif source == "custom":
        data = ctx.g.connectapi("/nutrition-service/customFood", params={**params, "includeContent": "true"})
        foods = data if isinstance(data, list) else first(dig(data, "customFoods"), dig(data, "results"), []) or []
        more = dig(data, "moreDataAvailable") if isinstance(data, dict) else None
    else:
        raise InputError("source is 'catalog' or 'custom'")
    return dumps({"foods": [_food(f) for f in foods], "more": more})


def serving_units(ctx: Ctx) -> Any:
    return shape.compact(ctx.g.connectapi("/nutrition-service/metadata/customFoodServingUnits"))


# ------------------------------------------------------------------ writing

NUTRIENTS = (
    "carbs",
    "protein",
    "fat",
    "fiber",
    "sugar",
    "saturatedFat",
    "sodium",
    "cholesterol",
    "potassium",
    "transFat",
    "calcium",
    "iron",
    "vitaminD",
)


def num_str(value: float) -> str:
    """Garmin wants "160", not "160.0"."""
    return str(int(value)) if float(value) == int(value) else str(value)


def set_goals(
    ctx: Ctx,
    date_text: str | None,
    calorie_goal: int | None,
    carbs_g: float | None,
    fat_g: float | None,
    protein_g: float | None,
    target_date: str | None,
) -> str:
    if all(v is None for v in (calorie_goal, carbs_g, fat_g, protein_g)):
        raise InputError("Give at least one goal to change.")
    d = ctx.day(date_text)
    current = ctx.g.get_nutrition_daily_settings(d.isoformat()) or {}
    body = dict(current)
    if calorie_goal is not None:
        body["calorieGoal"] = int(calorie_goal)
    macros = dict(body.get("macroGoals") or {})
    for key, value in (("carbs", carbs_g), ("fat", fat_g), ("protein", protein_g)):
        if value is not None:
            macros[key] = num_str(value)
    body["macroGoals"] = macros
    # Garmin refuses any update while the weight goal's targetDate is not after the date
    # (taxuspt #362), so check it here and say what to do.
    stored = current.get("targetDate")
    if target_date:
        if ctx.day(target_date, name="target_date") <= d:
            raise InputError("target_date must be after the date the goals apply from.")
        body["targetDate"] = target_date
    elif stored and str(stored)[:10] <= d.isoformat():
        raise InputError(
            f"Garmin's weight-goal target date ({str(stored)[:10]}) is not after {d}, and Garmin "
            "rejects goal changes until it is. Pass target_date (a later date) to move it."
        )
    ctx.g.client.put("connectapi", f"/nutrition-service/settings/{d.isoformat()}", json=body, api=True)
    return f"Nutrition goals from {d}: " + dumps({"calorie_goal": body.get("calorieGoal"), "macro_goals_g": macros})


def _custom_foods(ctx: Ctx, search: str) -> list[dict]:
    data = ctx.g.connectapi(
        "/nutrition-service/customFood",
        params={"searchExpression": search, "start": 0, "limit": 50, "includeContent": "true"},
    )
    return data if isinstance(data, list) else (data or {}).get("customFoods") or []


def save_custom_food(
    ctx: Ctx,
    name: str,
    calories: float,
    serving_unit: str,
    number_of_units: float,
    brand: str | None,
    food_id: str | None,
    serving_id: str | None,
    nutrients: dict[str, float | None],
) -> str:
    if not (name or "").strip():
        raise InputError("The food needs a name.")
    content: dict[str, Any] = {}
    meta: dict[str, Any] = {
        "foodName": name.strip(),
        "foodType": "GENERIC",
        "source": "GARMIN",
        "regionCode": "US",
        "languageCode": "en",
    }
    if food_id:
        # An update keeps whatever wasn't given.
        existing = next(
            (f for f in _custom_foods(ctx, name) if str(dig(f, "foodMetaData", "foodId")) == str(food_id)), None
        )
        if existing:
            content.update((existing.get("nutritionContents") or [{}])[0])
            meta.update(existing.get("foodMetaData") or {})
            meta["foodName"] = name.strip()
        meta["foodId"] = food_id
        if serving_id:
            content["servingId"] = serving_id
    if brand:
        meta["brandName"] = brand
    content.update(servingUnit=serving_unit, numberOfUnits=num_str(number_of_units), calories=num_str(calories))
    for key in NUTRIENTS:
        if nutrients.get(key) is not None:
            content[key] = num_str(nutrients[key])
    ctx.g.client.put(
        "connectapi", "/nutrition-service/customFood", json={"foodMetaData": meta, "nutritionContents": [content]}, api=True
    )
    verb = "Updated" if food_id else "Created"
    return f"{verb} custom food '{name.strip()}': {num_str(calories)} kcal per {num_str(number_of_units)} {serving_unit}."


def _meal_id(ctx: Ctx, meal_date: str, meal_time: str) -> tuple[Any, str]:
    meals = (ctx.g.get_nutrition_daily_meals(meal_date) or {}).get("meals") or []
    for m in meals:
        start, end = m.get("startTime"), m.get("endTime")
        if start and end and start <= meal_time <= end:
            return m["mealId"], m.get("mealName") or ""
    snacks = next((m for m in meals if m.get("mealName") == "SNACKS"), None)
    if snacks is None:
        raise InputError(f"No meal on {meal_date} covers {meal_time}, and there is no SNACKS meal to fall back on.")
    return snacks["mealId"], "SNACKS"


def log_food(
    ctx: Ctx,
    meal_date: str | None,
    meal_time: str,
    food_id: str | None,
    serving_id: str | None,
    serving_qty: float,
    source: str,
    name: str | None,
    calories: float | None,
    carbs_g: float | None,
    protein_g: float | None,
    fat_g: float | None,
    save_as_custom: bool,
) -> str:
    from datetime import UTC, datetime

    d = ctx.day(meal_date, name="meal_date").isoformat()
    clock = (meal_time or "").strip()
    if len(clock) == 5:
        clock += ":00"
    if len(clock) != 8 or clock[2] != ":" or clock[5] != ":":
        raise InputError("meal_time is HH:MM in the account's time zone")
    if serving_qty <= 0:
        raise InputError("serving_qty must be positive")
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    if save_as_custom:
        if not name or calories is None:
            raise InputError("save_as_custom needs a name and calories")
        match = next(
            (f for f in _custom_foods(ctx, name) if (dig(f, "foodMetaData", "foodName") or "").lower() == name.lower()),
            None,
        )
        if match is None:
            save_custom_food(
                ctx, name, calories, "SERVING", 1, None, None, None,
                {"carbs": carbs_g, "protein": protein_g, "fat": fat_g},
            )
            match = next(
                (f for f in _custom_foods(ctx, name) if (dig(f, "foodMetaData", "foodName") or "").lower() == name.lower()),
                None,
            )
        if match is None:
            raise InputError("The custom food was saved but could not be found again to log it.")
        food_id = str(dig(match, "foodMetaData", "foodId"))
        serving_id = str(((match.get("nutritionContents") or [{}])[0]).get("servingId", ""))
        source = "GARMIN"

    meal_id, meal_name = _meal_id(ctx, d, clock)
    if food_id:
        if not serving_id:
            raise InputError("Logging a saved food needs its serving_id (see garmin_food_search).")
        item = {
            "logTimestamp": stamp,
            "logSource": "GCW",
            "logCategory": "REGULAR_LOG",
            "mealTime": clock,
            "action": "ADD",
            "mealId": meal_id,
            "foodId": food_id,
            "servingId": serving_id,
            "source": source,
            "regionCode": "US",
            "languageCode": "en",
            "servingQty": serving_qty,
        }
        ctx.g.client.put("connectapi", "/nutrition-service/food/logs", json={"mealDate": d, "foodLogItems": [item]}, api=True)
        return f"Logged food {food_id} x{serving_qty:g} to {meal_name or 'meal'} on {d} at {clock[:5]}."

    if not name or calories is None:
        raise InputError("A quick add needs a name and calories (or give food_id and serving_id).")
    item = {
        "name": name,
        "logId": None,
        "logTimestamp": stamp,
        "logSource": "GCW",
        "logCategory": "QUICK_ADD",
        "mealTime": clock,
        "mealId": meal_id,
        "action": "ADD",
        "calories": num_str(calories),
        "carbs": num_str(carbs_g or 0),
        "protein": num_str(protein_g or 0),
        "fat": num_str(fat_g or 0),
    }
    ctx.g.client.put("connectapi", "/nutrition-service/food/logs/quickAdd", json={"mealDate": d, "quickAddItems": [item]}, api=True)
    return f"Quick-added '{name}' ({num_str(calories)} kcal) to {meal_name or 'meal'} on {d} at {clock[:5]}."
