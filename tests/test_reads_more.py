"""Workouts, the calendar, nutrition, courses and women's health."""

import json
from datetime import date

import pytest

from garmin_mcp.errors import InputError
from garmin_mcp.tools import courses, nutrition, womens_health, workouts

from .fakes import FakeGarmin, FixedCtx, make_cfg

TODAY = date(2026, 10, 7)


def ctx_with(tmp_path, responses):
    return FixedCtx(FakeGarmin(responses), make_cfg(tmp_path), TODAY)


def j(text):
    return json.loads(text)


INTERVALS = {
    "workoutId": 77,
    "workoutName": "6x400",
    "sportType": {"sportTypeKey": "running"},
    "workoutSegments": [
        {
            "workoutSteps": [
                {
                    "type": "ExecutableStepDTO",
                    "stepType": {"stepTypeKey": "warmup"},
                    "endCondition": {"conditionTypeKey": "time"},
                    "endConditionValue": 600.0,
                    "targetType": {"workoutTargetTypeKey": "heart.rate.zone"},
                    "zoneNumber": 2,
                },
                {
                    "type": "RepeatGroupDTO",
                    "numberOfIterations": 6,
                    "workoutSteps": [
                        {
                            "type": "ExecutableStepDTO",
                            "stepType": {"stepTypeKey": "interval"},
                            "endCondition": {"conditionTypeKey": "distance"},
                            "endConditionValue": 400.0,
                            "targetType": {"workoutTargetTypeKey": "pace.zone"},
                            "targetValueOne": 1000 / 240,
                            "targetValueTwo": 1000 / 225,
                        },
                        {
                            "type": "ExecutableStepDTO",
                            "stepType": {"stepTypeKey": "recovery"},
                            "endCondition": {"conditionTypeKey": "lap.button"},
                        },
                    ],
                },
            ]
        }
    ],
}


class TestWorkouts:
    def test_steps_read_like_a_coach_wrote_them(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_workout_by_id": INTERVALS})
        w = j(workouts.workouts(ctx, "77", False))
        warmup, repeat = w["steps"]
        assert warmup == {"type": "warmup", "until": "10:00", "target": "HR zone 2"}
        assert repeat["repeat"] == 6
        assert repeat["steps"][0] == {"type": "interval", "until": "400 m", "target": "pace 3:45-4:00 /km"}
        assert repeat["steps"][1] == {"type": "recovery", "until": "lap button"}

    def test_a_uuid_is_a_garmin_coach_workout(self, tmp_path):
        ctx = ctx_with(tmp_path, {"connectapi": {"workoutName": "Base"}})
        workouts.workouts(ctx, "0f1e-aa", False)
        assert ctx.g.called("connectapi")[0][0] == ("/workout-service/fbt-adaptive/0f1e-aa",)

    def test_raw_returns_garmins_json(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_workout_by_id": INTERVALS})
        assert j(workouts.workouts(ctx, "77", True)) == INTERVALS


class TestCalendar:
    def test_it_looks_two_weeks_ahead_by_default(self, tmp_path):
        ctx = ctx_with(tmp_path, {"query_garmin_graphql": {"data": {"workoutScheduleSummariesScalar": []}}})
        out = j(workouts.calendar(ctx, None, None, ["scheduled"]))
        assert (out["start"], out["end"]) == ("2026-10-07", "2026-10-20")
        query = ctx.g.called("query_garmin_graphql")[0][0][0]["query"]
        assert 'startDate:"2026-10-07", endDate:"2026-10-20"' in query

    def test_scheduled_entries_carry_the_id_garmin_delete_needs(self, tmp_path):
        # Shaped like the live GraphQL entry.
        entry = {"scheduledWorkoutId": 9100000001, "workoutId": 9200000001, "workoutName": "Easy",
                 "workoutType": "running", "scheduleDate": "2026-10-08", "estimatedDurationInSecs": 300}
        ctx = ctx_with(tmp_path, {"query_garmin_graphql": {"data": {"workoutScheduleSummariesScalar": [entry]}}})
        out = j(workouts.calendar(ctx, "2026-10-08", "2026-10-08", ["scheduled"]))
        assert out["scheduled"] == [{"date": "2026-10-08", "scheduled_id": 9100000001, "workout_id": 9200000001,
                                     "name": "Easy", "sport": "running", "est_duration": "5:00"}]

    def test_events_come_from_each_months_calendar_feed(self, tmp_path):
        feed = {
            "calendarItems": [
                {"itemType": "event", "date": "2026-10-18", "title": "City 10k", "isRace": True,
                 "completionTarget": {"value": 10, "unit": "kilometer"}},
                {"itemType": "activity", "date": "2026-10-18", "title": "Run"},
                {"itemType": "event", "date": "2026-12-01", "title": "Too late"},
            ]
        }
        ctx = ctx_with(tmp_path, {"get_scheduled_workouts": feed})
        out = j(workouts.calendar(ctx, "2026-10-07", "2026-11-03", ["events"]))
        assert out["events"] == [{"date": "2026-10-18", "title": "City 10k", "race": True, "distance_km": 10.0}]
        assert [a for a, _ in ctx.g.called("get_scheduled_workouts")] == [(2026, 10), (2026, 11)]


class TestNutrition:
    def test_range_summary_counts_logged_items(self, tmp_path):
        data = {
            "dailyNutritionSummaries": [
                {"mealDate": "2026-10-05", "dailyNutritionContent": {"calories": 2400, "protein": 150},
                 "mealDetails": [{"loggedFoods": [{}, {}]}]},
            ]
        }
        ctx = ctx_with(tmp_path, {"connectapi": data})
        out = j(nutrition.nutrition(ctx, "summary", None, "2026-10-01", "2026-10-06"))
        assert out["days"] == [{"date": "2026-10-05", "kcal": 2400, "protein_g": 150.0, "items": 2}]

    def test_energy_balance_compares_intake_burn_and_weight(self, tmp_path):
        days = [
            {"mealDate": f"2026-10-0{d}", "dailyNutritionContent": {"calories": 2000},
             "mealDetails": [{"loggedFoods": [{}]}]}
            for d in (1, 2, 3)
        ]
        burn = [{"calendarDate": f"2026-10-0{d}", "total": 2500} for d in (1, 2, 3)]
        weights = {
            "dailyWeightSummaries": [
                {"summaryDate": f"2026-10-0{d}", "latestWeight": {"weight": 80000 - 100 * d}} for d in (1, 2, 3, 4)
            ]
        }
        ctx = ctx_with(
            tmp_path,
            {"connectapi": {"dailyNutritionSummaries": days}, "get_calories_daily": burn, "get_weigh_ins": weights},
        )
        out = j(nutrition.nutrition(ctx, "energy_balance", None, "2026-10-01", "2026-10-06"))
        assert out["avg_balance_kcal_per_day"] == -500
        assert out["weight_trend_kg_per_week"] == -0.7
        assert out["balance_implied_by_weight_kcal_per_day"] == -770
        assert out["gap_kcal_per_day"] == -270

    def test_ranges_are_capped(self, tmp_path):
        with pytest.raises(InputError):
            nutrition.nutrition(ctx_with(tmp_path, {}), "summary", None, "2026-01-01", "2026-10-06")

    def test_food_search_shows_serving_ids(self, tmp_path):
        found = {
            "results": [
                {"foodMetaData": {"foodId": "f1", "foodName": "Oats"},
                 "nutritionContents": [{"servingId": "s1", "servingUnit": "G", "numberOfUnits": 100,
                                        "calories": 370}]}
            ],
            "moreDataAvailable": False,
        }
        ctx = ctx_with(tmp_path, {"connectapi": found})
        out = j(nutrition.food_search(ctx, "oats", "catalog", 0, 20))
        assert out["foods"][0]["servings"][0] == {"serving_id": "s1", "unit": "G", "units": 100, "kcal": 370}


class TestCourses:
    def test_a_course_with_null_course_points_does_not_crash(self, tmp_path):
        ctx = ctx_with(tmp_path, {"connectapi": {"courseId": 5, "courseName": "Loop", "distanceMeter": 10500.0,
                                                 "coursePoints": None, "geoPoints": [{}, {}]}})
        out = j(courses.courses(ctx, 5))
        assert out["distance_km"] == 10.5 and out["track_points"] == 2 and "waypoints" not in out


class TestWomensHealth:
    def test_long_calendars_are_fetched_three_months_at_a_time(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_menstrual_calendar_data": {}})
        womens_health.womens_health(ctx, "menstrual_calendar", None, "2026-01-01", "2026-10-06")
        assert len(ctx.g.called("get_menstrual_calendar_data")) == 4
