"""Writes: the workout step language, raw-JSON checks, edits, body data, and the
delete guard. Nothing here touches a real account."""

import json
from datetime import date

import pytest

from garmin_mcp.errors import InputError
from garmin_mcp.tools import delete, nutrition, resources, workouts, writes
from garmin_mcp.tools.workout_dsl import Repeat, Step, build, normalize

from .fakes import FakeGarmin, FixedCtx, make_cfg

TODAY = date(2026, 10, 7)


def ctx_with(tmp_path, responses=None):
    return FixedCtx(FakeGarmin(responses or {}), make_cfg(tmp_path), TODAY)


def steps_of(workout):
    return workout["workoutSegments"][0]["workoutSteps"]


class TestStepLanguage:
    def test_intervals_become_garmins_exact_json(self):
        w = build(
            "6x400",
            "running",
            [
                Step(type="warmup", duration="10:00", hr_zone=2),
                Repeat(repeat=6, steps=[Step(distance_km=0.4, pace_min_km=("4:00", "3:45")), Step(type="recovery", lap_button=True)]),
            ],
        )
        warm, group = steps_of(w)
        assert w["sportType"] == {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}
        assert warm["endCondition"]["conditionTypeId"] == 2 and warm["endConditionValue"] == 600
        assert warm["targetType"]["workoutTargetTypeId"] == 4 and warm["zoneNumber"] == 2
        assert group["type"] == "RepeatGroupDTO" and group["numberOfIterations"] == 6
        assert group["endCondition"] == {
            "conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False,
        }
        interval, recovery = group["workoutSteps"]
        assert interval["endCondition"]["conditionTypeKey"] == "distance" and interval["endConditionValue"] == 400
        # Pace ranges are speeds in m/s, the slower pace first.
        assert interval["targetValueOne"] == pytest.approx(1000 / 240)
        assert interval["targetValueTwo"] == pytest.approx(1000 / 225)
        assert recovery["endCondition"]["conditionTypeKey"] == "lap.button"
        assert [s["stepOrder"] for s in (warm, group, interval, recovery)] == [1, 2, 3, 4]
        assert w["estimatedDurationInSecs"] == 600

    def test_watt_ranges_use_power_zone_never_type_six(self):
        step = steps_of(build("FTP", "cycling", [Step(duration="20:00", power_w=(270, 240))]))[0]
        assert step["targetType"]["workoutTargetTypeId"] == 2
        assert (step["targetValueOne"], step["targetValueTwo"]) == (240, 270)

    def test_strength_steps_use_the_catalog_and_grams(self):
        step = steps_of(build("Gym", "strength_training", [Step(exercise="bench press", reps=5, weight_kg=80)]))[0]
        assert (step["category"], step["exerciseName"]) == ("BENCH_PRESS", "BENCH_PRESS")
        assert step["endCondition"]["conditionTypeId"] == 10 and step["weightValue"] == 80000

    def test_near_miss_exercise_names_still_resolve(self):
        step = steps_of(build("Gym", "strength_training", [Step(exercise="Pull Up", reps=6)]))[0]
        assert step["category"] == "PULL_UP"

    def test_an_ambiguous_exercise_suggests_close_ones(self):
        with pytest.raises(InputError, match="Closest: .*Bench"):
            build("Gym", "strength_training", [Step(exercise="Bench", reps=5)])

    def test_an_exact_name_wins_over_a_variant_sharing_its_key(self):
        # BANDED_EXERCISES also has an exercise keyed SQUAT; plain 'Squat' is not that.
        step = steps_of(build("Gym", "strength_training", [Step(exercise="Squat", reps=5)]))[0]
        assert (step["category"], step["exerciseName"]) == ("SQUAT", "SQUAT")

    def test_a_step_ends_one_way(self):
        with pytest.raises(InputError, match="one way"):
            build("x", "running", [Step(duration="5:00", distance_km=1)])

    def test_a_zone_and_a_range_are_not_both_allowed(self):
        with pytest.raises(InputError, match="one target"):
            build("x", "running", [Step(duration="5:00", hr_zone=2, hr_bpm=(120, 140))])

    def test_names_longer_than_garmin_keeps_are_refused(self):
        with pytest.raises(InputError, match="256"):
            build("x" * 257, "running", [Step(duration="5:00")])

    def test_walking_and_hiking_have_their_real_sport_ids(self):
        assert build("w", "walking", [Step(duration="5:00")])["sportType"]["sportTypeId"] == 17
        assert build("h", "hiking", [Step(duration="5:00")])["sportType"]["sportTypeId"] == 18

    @pytest.mark.parametrize("slug", sorted(resources.TEMPLATES))
    def test_every_template_builds(self, slug):
        t = resources.TEMPLATES[slug]
        items = [Repeat(**s) if "repeat" in s else Step(**s) for s in t["steps"]]
        assert build(t["name"], t["sport"], items)["workoutSegments"]


RAW_STEP = {
    "type": "ExecutableStepDTO",
    "stepOrder": 1,
    "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
    "endCondition": {"conditionTypeKey": "time"},
    "endConditionValue": 300,
}


def raw(*steps):
    return {
        "workoutName": "raw",
        "sportType": {"sportTypeKey": "running"},
        "workoutSegments": [{"segmentOrder": 1, "workoutSteps": list(steps)}],
    }


class TestRawWorkoutChecks:
    def test_targets_nested_in_target_type_are_moved_to_the_step(self):
        step = {**RAW_STEP, "targetType": {"workoutTargetTypeKey": "pace.zone", "targetValueOne": 3.0, "targetValueTwo": 3.5}}
        fixed = steps_of(normalize(raw(step)))[0]
        assert (fixed["targetValueOne"], fixed["targetValueTwo"]) == (3.0, 3.5)
        assert fixed["targetType"]["workoutTargetTypeId"] == 6
        assert fixed["endCondition"]["conditionTypeId"] == 2

    def test_power_between_is_refused_with_the_fix(self):
        step = {**RAW_STEP, "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "power.between"}}
        with pytest.raises(InputError, match="power.zone"):
            normalize(raw(step))

    def test_mismatched_ids_and_keys_are_refused(self):
        step = {**RAW_STEP, "endCondition": {"conditionTypeId": 3, "conditionTypeKey": "time"}}
        with pytest.raises(InputError, match="does not match"):
            normalize(raw(step))

    def test_repeat_groups_get_their_iteration_condition(self):
        group = {"type": "RepeatGroupDTO", "endCondition": {"conditionTypeKey": "iterations"},
                 "endConditionValue": 4, "workoutSteps": [dict(RAW_STEP)]}
        fixed = steps_of(normalize(raw(group)))[0]
        assert fixed["endCondition"]["conditionTypeId"] == 7 and fixed["numberOfIterations"] == 4

    def test_an_hr_zone_given_as_a_value_becomes_a_zone(self):
        step = {**RAW_STEP, "targetType": {"workoutTargetTypeKey": "heart.rate.zone"}, "targetValueOne": 3}
        fixed = steps_of(normalize(raw(step)))[0]
        assert fixed["zoneNumber"] == 3 and fixed["targetValueOne"] is None

    def test_upload_checks_every_workout_before_uploading_any(self, tmp_path):
        ctx = ctx_with(tmp_path, {"upload_workout": {"workoutId": 1}})
        bad = raw({**RAW_STEP, "targetType": {"workoutTargetTypeKey": "power.between"}})
        with pytest.raises(InputError):
            workouts.upload_workouts(ctx, [raw(dict(RAW_STEP)), bad])
        assert ctx.g.called("upload_workout") == []


class TestWorkoutWrites:
    def test_create_uploads_and_schedules(self, tmp_path):
        ctx = ctx_with(
            tmp_path,
            {
                "upload_workout": {"workoutId": 55},
                "schedule_workout": {"workoutScheduleId": 9},
                "query_garmin_graphql": {"data": {"workoutScheduleSummariesScalar": []}},
            },
        )
        out = workouts.create_workout(ctx, "Easy", "running", [Step(duration="30:00")], None, "2026-10-09", None)
        assert "id 55" in out and "scheduled_id 9" in out
        assert ctx.g.called("schedule_workout") == [((55, "2026-10-09"), {})]

    def test_scheduling_is_idempotent(self, tmp_path):
        already = {"data": {"workoutScheduleSummariesScalar": [{"scheduleDate": "2026-10-09", "workoutId": 55}]}}
        ctx = ctx_with(tmp_path, {"query_garmin_graphql": already, "schedule_workout": {}})
        out = workouts.schedule_workouts(ctx, [{"date": "2026-10-09", "workout_id": 55}, {"date": "2026-10-10", "workout_id": 55}])
        assert "already on 2026-10-09" in out
        assert ctx.g.called("schedule_workout") == [((55, "2026-10-10"), {})]


class TestActivityEdits:
    def test_rpe_is_stored_times_ten_and_feel_as_its_number(self, tmp_path):
        ctx = ctx_with(tmp_path)
        writes.update_activity(ctx, 111, rpe=7, feel="strong")
        bodies = [k["json"] for _, k in ctx.g.called("put")]
        assert {"activityId": 111, "summaryDTO": {"directWorkoutRpe": 70}} in bodies
        assert {"activityId": 111, "summaryDTO": {"directWorkoutFeel": 75}} in bodies

    def test_a_bad_field_stops_everything_before_any_write(self, tmp_path):
        ctx = ctx_with(tmp_path, {"connectapi": [{"typeKey": "race", "typeId": 1}]})
        with pytest.raises(InputError):
            writes.update_activity(ctx, 111, name="New", event_type="party")
        assert ctx.g.called("set_activity_name") == [] and ctx.g.called("put") == []

    def test_manual_activities_use_his_time_zone_not_utc(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_activity_types": [{"typeKey": "yoga"}], "create_manual_activity": {"activityId": 3}})
        writes.create_activity(ctx, "yoga", "2026-10-06", "18:30", 45, 0, None)
        args, _ = ctx.g.called("create_manual_activity")[0]
        assert args[:3] == ("2026-10-06T18:30:00.000", "Europe/Stockholm", "yoga")


class TestBodyWrites:
    def test_weigh_ins_get_a_timezone_aware_timestamp(self, tmp_path):
        ctx = ctx_with(tmp_path)
        writes.add_weigh_in(ctx, 78.4, "kg", "2026-10-07 07:30")
        _, kwargs = ctx.g.called("add_weigh_in")[0]
        assert kwargs["timestamp"] == "2026-10-07T07:30:00+02:00"

    def test_water_on_a_date_without_a_time_goes_to_that_day(self, tmp_path):
        ctx = ctx_with(tmp_path)
        writes.add_hydration(ctx, 500, "2026-10-05")
        assert ctx.g.called("add_hydration_data") == [((500,), {"cdate": "2026-10-05"})]

    def test_max_hr_zones_are_recalculated_from_a_new_max(self, tmp_path):
        current = [{"sport": "RUNNING", "trainingMethod": "HR_MAX", "maxHeartRateUsed": 185}]
        ctx = ctx_with(tmp_path, {"connectapi": current, "request": None})
        writes.set_hr_zones(ctx, "running", "max_hr", 190, None, None, None)
        (_path,), kwargs = ctx.g.called("put")[0]
        sent = kwargs["json"][0]
        assert [sent[f"zone{i}Floor"] for i in range(1, 6)] == [95, 114, 133, 152, 171]
        assert sent["changeState"] == "CHANGED"


class TestNutritionWrites:
    def test_goals_explain_a_stale_weight_goal_date(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_nutrition_daily_settings": {"targetDate": "2026-09-01"}})
        with pytest.raises(InputError, match="target_date"):
            nutrition.set_goals(ctx, "today", 2700, None, None, None, None)

    def test_macros_are_sent_as_integer_strings(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_nutrition_daily_settings": {"calorieGoal": 2500, "macroGoals": {"fat": "70"}}})
        nutrition.set_goals(ctx, "today", None, None, None, 160.0, None)
        (path,), kwargs = ctx.g.called("put")[0]
        assert path == "/nutrition-service/settings/2026-10-07"
        assert kwargs["json"]["macroGoals"] == {"fat": "70", "protein": "160"}

    def test_a_quick_add_lands_in_the_meal_covering_its_time(self, tmp_path):
        meals = {"meals": [{"mealId": 1, "mealName": "BREAKFAST", "startTime": "05:00:00", "endTime": "10:59:59"},
                           {"mealId": 4, "mealName": "SNACKS", "startTime": None, "endTime": None}]}
        ctx = ctx_with(tmp_path, {"get_nutrition_daily_meals": meals})
        out = nutrition.log_food(ctx, "today", "07:15", None, None, 1, "GARMIN", "Oats", 370, 60, 13, 7, False)
        (path,), kwargs = ctx.g.called("put")[0]
        item = kwargs["json"]["quickAddItems"][0]
        assert path.endswith("/quickAdd") and item["mealId"] == 1 and item["calories"] == "370"
        assert "BREAKFAST" in out


class TestDeleteGuard:
    def setup_ctx(self, tmp_path):
        day = {"dateWeightList": [{"samplePk": 9, "calendarDate": "2026-10-06", "weight": 78400.0}]}
        return ctx_with(tmp_path, {"get_daily_weigh_ins": day})

    def test_without_confirm_nothing_is_deleted(self, tmp_path):
        ctx = self.setup_ctx(tmp_path)
        out = delete.delete(ctx, "weigh_ins", None, "2026-10-06", False)
        assert "PREVIEW" in out and "78.4" in out
        assert ctx.g.called("delete_weigh_in") == []

    def test_confirm_without_a_preview_only_previews(self, tmp_path):
        ctx = self.setup_ctx(tmp_path)
        out = delete.delete(ctx, "weigh_ins", None, "2026-10-06", True)
        assert "has to be previewed first" in out
        assert ctx.g.called("delete_weigh_in") == []

    def test_preview_then_confirm_deletes_exactly_what_was_shown(self, tmp_path):
        ctx = self.setup_ctx(tmp_path)
        delete.delete(ctx, "weigh_ins", None, "2026-10-06", False)
        out = delete.delete(ctx, "weigh_ins", None, "2026-10-06", True)
        assert out.startswith("Deleted:")
        assert ctx.g.called("delete_weigh_in") == [(("9", "2026-10-06"), {})]

    def test_a_different_request_needs_its_own_preview(self, tmp_path):
        ctx = self.setup_ctx(tmp_path)
        delete.delete(ctx, "weigh_ins", None, "2026-10-06", False)
        out = delete.delete(ctx, "weigh_ins", ["9"], "2026-10-06", True)
        assert "previewed first" in out

    def test_kinds_that_need_ids_or_a_date_say_so(self, tmp_path):
        with pytest.raises(InputError, match="ids"):
            delete.delete(ctx_with(tmp_path), "workouts", None, None, False)
        with pytest.raises(InputError, match="date"):
            delete.delete(ctx_with(tmp_path), "weigh_ins", None, None, False)


def test_templates_are_valid_json_resources():
    for template in resources.TEMPLATES.values():
        assert json.loads(json.dumps(template))["steps"]
