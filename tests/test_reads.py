"""The read tools, against synthetic payloads shaped like Garmin's."""

import json
from datetime import date

import pytest

from garmin_mcp.errors import InputError
from garmin_mcp.tools import account, activities, body, training, trends, wellness

from .fakes import FakeGarmin, FixedCtx, make_cfg

TODAY = date(2026, 10, 7)


def ctx_with(tmp_path, responses):
    return FixedCtx(FakeGarmin(responses), make_cfg(tmp_path), TODAY)


def j(text):
    return json.loads(text)


RUN = {
    "activityId": 111,
    "activityName": "Morning Run",
    "activityType": {"typeKey": "running"},
    "startTimeLocal": "2026-10-06 07:12:34",
    "distance": 8000.0,
    "duration": 2400.0,
    "movingDuration": 2380.0,
    "averageSpeed": 1000 / 300,
    "averageHR": 142.4,
    "maxHR": 171.0,
    "elevationGain": 54.0,
    "calories": 512.0,
    "activityTrainingLoad": 88.6,
    "aerobicTrainingEffect": 3.1,
    "trainingEffectLabel": "TEMPO",
}
RIDE = {**RUN, "activityId": 222, "activityType": {"typeKey": "road_biking"}, "averageSpeed": 7.5}


class TestActivities:
    def test_rows_show_pace_for_runs_and_speed_for_rides(self, tmp_path):
        ctx = ctx_with(tmp_path, {"connectapi": [RUN, RIDE]})
        out = j(activities.activities(ctx, None, None, None, 2, 0))
        run, ride = out["activities"]
        assert run["pace_min_km"] == "5:00" and run["distance_km"] == 8.0
        assert run["avg_hr_bpm"] == 142 and run["start"] == "2026-10-06 07:12"
        assert ride["speed_kmh"] == 27.0 and "pace_min_km" not in ride
        assert out["has_more"] is True and out["next_page"] == 1

    def test_a_date_range_and_type_go_to_the_search_endpoint_one_page_at_a_time(self, tmp_path):
        ctx = ctx_with(tmp_path, {"connectapi": []})
        activities.activities(ctx, "2026-10-01", None, "running", 20, 2)
        (path,), kwargs = ctx.g.called("connectapi")[0]
        assert path == activities.SEARCH
        assert kwargs["params"] == {
            "start": 40,
            "limit": 20,
            "startDate": "2026-10-01",
            "endDate": "2026-10-07",
            "activityType": "running",
        }

    def test_limit_zero_is_just_the_count(self, tmp_path):
        ctx = ctx_with(tmp_path, {"count_activities": 312})
        assert j(activities.activities(ctx, None, None, None, 0, 0)) == {"total_activities": 312}

    def test_an_absurd_limit_is_refused(self, tmp_path):
        with pytest.raises(InputError):
            activities.activities(ctx_with(tmp_path, {}), None, None, None, 500, 0)


DETAIL = {
    "activityId": 111,
    "activityName": "Morning Run",
    "activityTypeDTO": {"typeKey": "running"},
    "summaryDTO": {
        "startTimeLocal": "2026-10-06T07:12:34.0",
        "distance": 8000.0,
        "duration": 2400.0,
        "averageSpeed": 1000 / 300,
        "maxSpeed": 1000 / 240,
        "averageHR": 142.0,
        "directWorkoutRpe": 70,
        "directWorkoutFeel": 75,
        "strideLength": 105.0,
        "trainingEffect": 3.1,
        "aerobicTrainingEffectMessage": "IMPROVING_AEROBIC_BASE_8",
    },
}


class TestActivity:
    def test_the_summary_converts_garmins_units(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_activity": DETAIL})
        s = j(activities.activity(ctx, 111, ["summary"], "summary", 100))["summary"]
        assert s["pace_min_km"] == "5:00" and s["best_pace_min_km"] == "4:00"
        assert s["rpe"] == 7.0 and s["feel"] == "strong" and s["stride_m"] == 1.05

    def test_time_in_zones_gets_percentages(self, tmp_path):
        zones = [
            {"zoneNumber": 1, "secsInZone": 600.0, "zoneLowBoundary": 98},
            {"zoneNumber": 2, "secsInZone": 1800.0, "zoneLowBoundary": 118},
        ]
        ctx = ctx_with(tmp_path, {"get_activity_hr_in_timezones": zones})
        out = j(activities.activity(ctx, 111, ["hr_zones"], "summary", 100))["hr_zones"]
        assert out == [
            {"zone": 1, "from_bpm": 98, "time": "10:00", "pct": 25},
            {"zone": 2, "from_bpm": 118, "time": "30:00", "pct": 75},
        ]

    def test_weather_arrives_in_fahrenheit_knots_and_gmt(self, tmp_path):
        # The values of a real run: Connect showed 13 °C, 17 km/h wind, 19:20 local.
        raw = {"temp": 55, "windSpeed": 9, "issueDate": "2026-10-06T17:20:00.000+00:00",
               "weatherTypeDTO": {"desc": "Fair"}}
        ctx = ctx_with(tmp_path, {"get_activity_weather": raw})
        w = j(activities.activity(ctx, 111, ["weather"], "summary", 100))["weather"]
        assert w == {"time": "2026-10-06 19:20", "conditions": "Fair", "temp_c": 12.8, "wind_kmh": 17}

    def test_exercise_set_weights_arrive_in_grams(self, tmp_path):
        sets = {
            "exerciseSets": [
                {
                    "setType": "ACTIVE",
                    "repetitionCount": 5,
                    "weight": 80000.0,
                    "exercises": [{"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS", "probability": 90}],
                },
                {"setType": "REST", "duration": 120.0},
            ]
        }
        ctx = ctx_with(tmp_path, {"get_activity_exercise_sets": sets})
        out = j(activities.activity(ctx, 111, ["exercise_sets"], "summary", 100))["exercise_sets"]
        assert out["sets"][0]["weight_kg"] == 80.0 and out["sets"][0]["reps"] == 5
        assert out["total_rest"] == "2:00"

    def test_details_are_thinned_to_the_requested_points(self, tmp_path):
        raw = {
            "measurementCount": 1000,
            "metricDescriptors": [
                {"metricsIndex": 0, "key": "sumDuration"},
                {"metricsIndex": 1, "key": "directHeartRate"},
            ],
            "activityDetailMetrics": [{"metrics": [i, 100 + i % 50]} for i in range(1000)],
        }
        ctx = ctx_with(tmp_path, {"get_activity_details": raw})
        out = j(activities.activity(ctx, 111, ["details"], "summary", 20))["details"]
        assert out["columns"] == ["time", "hr_bpm"] and len(out["rows"]) == 20

    def test_garmins_broken_moving_speed_in_interval_splits_is_ignored(self, tmp_path):
        # Real values from a run/walk session: movingDuration 528 s for 42 minutes.
        whole = {"type": "INTERVAL_ACTIVE", "distance": 5611.66, "duration": 2519.9, "movingDuration": 528.0,
                 "averageSpeed": 2.227, "averageMovingSpeed": 10.628, "startTimeGMT": "2026-10-06T17:37:19.0"}
        blip = {"type": "RWD_WALK", "distance": 0.0, "duration": 1.0, "averageSpeed": 0.0}
        ctx = ctx_with(tmp_path, {"get_activity": DETAIL, "get_activity_typed_splits": {"splits": [whole, blip]}})
        (seg,) = j(activities.activity(ctx, 111, ["typed_splits"], "summary", 100))["typed_splits"]
        assert seg["pace_min_km"] == "7:29" and seg["start"] == "2026-10-06 19:37"
        assert "moving" not in seg

    def test_unknown_sections_are_refused_with_the_choices(self, tmp_path):
        with pytest.raises(InputError, match="splits"):
            activities.activity(ctx_with(tmp_path, {}), 111, ["map"], "summary", 100)


SLEEP = {
    "dailySleepDTO": {
        "sleepTimeSeconds": 27000,
        "sleepStartTimestampGMT": 1791327600000,
        "sleepEndTimestampGMT": 1791355200000,
        "deepSleepSeconds": 5400,
        "lightSleepSeconds": 14400,
        "remSleepSeconds": 7200,
        "awakeSleepSeconds": 900,
        "sleepScores": {"overall": {"value": 82, "qualifierKey": "GOOD"}},
    },
    "restingHeartRate": 49,
    "avgOvernightHrv": 61.0,
}


class TestWellness:
    def test_sleep_is_summarised_in_hours_minutes_and_shares(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_sleep_data": SLEEP})
        s = j(wellness.wellness(ctx, "sleep", "2026-10-06", "summary"))
        assert s["sleep_h"] == 7.5 and s["score"] == 82 and s["quality"] == "GOOD"
        assert s["stages_min"] == {"deep": 90, "light": 240, "rem": 120, "awake": 15}
        assert s["stages_pct"]["deep"] == 20

    def test_a_night_without_sleep_says_so(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_sleep_data": {"dailySleepDTO": {}}})
        assert "No sleep recorded" in wellness.wellness(ctx, "sleep", "today", "summary")

    def test_stress_minutes_per_band_from_three_minute_readings(self, tmp_path):
        values = [[1791360000000 + i * 180000, level] for i, level in enumerate([10, 20, 30, 60, 80, -2, -1])]
        ctx = ctx_with(tmp_path, {"get_stress_data": {"avgStressLevel": 30, "stressValuesArray": values}})
        s = j(wellness.wellness(ctx, "stress", "2026-10-06", "summary"))
        assert s["minutes"] == {"rest": 6, "low": 3, "medium": 3, "high": 3}
        assert s["activity_minutes"] == 3

    def test_today_is_marked_partial(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_user_summary": {"totalSteps": 4000}})
        assert j(wellness.wellness(ctx, "summary", "today", "summary"))["partial_day"] is True

    def test_finished_days_are_cached_and_today_is_not(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_user_summary": {"totalSteps": 4000}})
        for _ in range(2):
            wellness.wellness(ctx, "summary", "2026-10-06", "summary")
            wellness.wellness(ctx, "summary", "today", "summary")
        days = [args[0] for args, _ in ctx.g.called("get_user_summary")]
        assert days.count("2026-10-06") == 1 and days.count("2026-10-07") == 2

    def test_the_future_is_refused(self, tmp_path):
        with pytest.raises(InputError, match="future"):
            wellness.wellness(ctx_with(tmp_path, {}), "sleep", "2026-10-09", "summary")

    def test_full_detail_is_garmins_payload(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_sleep_data": SLEEP})
        assert j(wellness.wellness(ctx, "sleep", "2026-10-06", "full")) == SLEEP


STATUS = {
    "mostRecentTrainingStatus": {
        "latestTrainingStatusData": {
            "1": {"deviceId": 1, "primaryTrainingDevice": False, "weeklyTrainingLoad": 0},
            "2": {
                "deviceId": 2,
                "primaryTrainingDevice": True,
                "weeklyTrainingLoad": 410,
                "trainingStatusFeedbackPhrase": "PRODUCTIVE_3",
                "acuteTrainingLoadDTO": {
                    "dailyTrainingLoadAcute": 420,
                    "dailyTrainingLoadChronic": 380,
                    "dailyAcuteChronicWorkloadRatio": 1.1,
                },
            },
        },
        "recordedDevices": [{"deviceId": 2, "deviceName": "Forerunner 965"}],
    },
    "mostRecentVO2Max": {"generic": {"vo2MaxPreciseValue": 52.4}},
}


class TestTraining:
    def test_status_reads_the_primary_device_not_the_first(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_training_status": STATUS})
        s = j(training.training(ctx, "status", "today", "summary"))
        assert s["weekly_load"] == 410 and s["device"] == "Forerunner 965"
        assert s["form_tsb"] == -40 and s["vo2max"] == 52.4

    def test_race_predictions_come_with_pace(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_race_predictions": {"time5K": 1500, "time10K": 3150}})
        p = j(training.training(ctx, "race_predictions", "today", "summary"))
        assert p["5k"] == {"time": "25:00", "pace_min_km": "5:00"}

    def test_lactate_threshold_speed_is_scaled_by_ten(self, tmp_path):
        data = {"speed_and_heart_rate": {"speed": 0.392, "heartRate": 168}, "power": {}}
        ctx = ctx_with(tmp_path, {"get_lactate_threshold": data})
        lt = j(training.training(ctx, "lactate_threshold", "today", "summary"))
        assert lt["pace_min_km"] == "4:15" and lt["hr_bpm"] == 168

    def test_vo2max_is_the_latest_update_carried_forward(self, tmp_path):
        # Shaped like a new Forerunner: one value, the day before.
        rng = [{"generic": {"calendarDate": "2026-10-06", "vo2MaxPreciseValue": 48.3}}]
        ctx = ctx_with(tmp_path, {"get_max_metrics_range": rng})
        v = j(training.training(ctx, "vo2max", "today", "summary"))
        assert v["running"] == 48.3 and "carried forward" in v["note"]

    def test_vo2max_falls_back_to_training_status_then_the_profile(self, tmp_path):
        status = {"mostRecentVO2Max": {"generic": {"calendarDate": "2026-10-01", "vo2MaxValue": 52}}}
        ctx = ctx_with(tmp_path, {"get_max_metrics_range": [], "get_training_status": status})
        assert j(training.training(ctx, "vo2max", "today", "summary"))["running"] == 52
        nothing = {"mostRecentVO2Max": None}
        ctx = ctx_with(tmp_path, {"get_max_metrics_range": [], "get_training_status": nothing,
                                  "get_user_profile": {"userData": {"vo2MaxRunning": 48.0}}})
        v = j(training.training(ctx, "vo2max", "today", "summary"))
        assert v["running"] == 48.0 and "profile" in v["note"]

    def test_a_new_watch_without_status_says_so(self, tmp_path):
        empty = {"mostRecentVO2Max": None, "mostRecentTrainingStatus": None}
        ctx = ctx_with(tmp_path, {"get_training_status": empty, "get_race_predictions": {}})
        assert "no training status yet" in training.training(ctx, "status", "today", "summary")
        assert "no race predictions yet" in training.training(ctx, "race_predictions", "today", "summary")

    def test_the_onboarding_hrv_placeholder_is_not_shown_as_ms(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_training_readiness": [{"hrvWeeklyAverage": 511, "score": 60}]})
        assert "hrv_weekly_avg_ms" not in training.training(ctx, "readiness", "today", "summary")


class TestTrends:
    def test_daily_steps_are_fetched_in_four_week_chunks(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_daily_steps": []})
        trends.trend(ctx, "steps", "2026-08-01", "2026-10-07", "daily")
        assert len(ctx.g.called("get_daily_steps")) == 3

    def test_day_by_day_metrics_are_capped(self, tmp_path):
        with pytest.raises(InputError, match="weekly"):
            trends.trend(ctx_with(tmp_path, {}), "stress", "2026-08-01", "2026-10-07", "daily")

    def test_weekly_is_only_offered_where_garmin_has_it(self, tmp_path):
        with pytest.raises(InputError, match="Weekly"):
            trends.trend(ctx_with(tmp_path, {}), "hrv", None, None, "weekly")

    def test_vo2max_reports_the_value_in_force_at_the_start(self, tmp_path):
        data = [
            {"generic": {"calendarDate": "2026-08-20", "vo2MaxPreciseValue": 51.0}},
            {"generic": {"calendarDate": "2026-09-15", "vo2MaxPreciseValue": 52.0}},
        ]
        ctx = ctx_with(tmp_path, {"get_max_metrics_range": data})
        out = j(trends.trend(ctx, "vo2max", "2026-09-01", "2026-09-30", "daily"))["data"]
        assert out["in_force_at_start"]["running"] == 51.0
        assert out["updates"] == [{"date": "2026-09-15", "running": 52.0}]

    def test_sleep_uses_the_range_endpoint(self, tmp_path):
        rows = [{"calendarDate": "2026-10-06", "values": {"totalSleepTimeInSeconds": 27000, "deepTime": 5400}}]
        ctx = ctx_with(tmp_path, {"get_sleep_daily": rows})
        out = j(trends.trend(ctx, "sleep", "2026-10-01", "2026-10-06", "daily"))
        assert out["data"] == [{"date": "2026-10-06", "sleep_h": 7.5, "deep_min": 90}]
        assert len(ctx.g.called("get_sleep_daily")) == 1

    def test_progress_totals_are_converted_from_garmins_units(self, tmp_path):
        # The values of a real 5.61 km, 42-minute, 41 m, 530 kcal run.
        sums = {"distance": 561166.0, "duration": 2519928.0, "movingDuration": 2516071.0,
                "elevationGain": 4100.0, "calories": 2220.7106}

        def progress(start, end, metric):
            return [{"stats": {"running": {metric: {"count": 1, "sum": sums[metric]}}}}]

        ctx = ctx_with(tmp_path, {"get_progress_summary_between_dates": progress})
        (row,) = j(trends.trend(ctx, "progress", "2026-10-01", "2026-10-07", "daily"))["data"]
        assert row == {"type": "running", "activities": 1, "distance_km": 5.61, "time": "42:00",
                       "moving": "41:56", "elev_gain_m": 41, "kcal": 530}

    def test_days_without_data_are_left_out_and_said_so(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_training_status": {"mostRecentTrainingStatus": None}})
        out = j(trends.trend(ctx, "training_load", "2026-10-01", "2026-10-06", "daily"))
        assert "data" not in out and "no training load data" in out["note"]

    def test_training_load_comes_from_garmins_status_per_day(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_training_status": STATUS})
        out = j(trends.trend(ctx, "training_load", "2026-10-05", "2026-10-06", "daily"))["data"]
        assert out[0] == {"date": "2026-10-05", "acute": 420, "chronic": 380, "form_tsb": -40,
                          "acwr": 1.1, "status": "PRODUCTIVE_3"}


class TestBody:
    def test_one_day_of_weigh_ins_in_kilograms(self, tmp_path):
        day = {"dateWeightList": [{"samplePk": 9, "calendarDate": "2026-10-06", "weight": 78400.0, "bodyFat": 21.5}]}
        ctx = ctx_with(tmp_path, {"get_daily_weigh_ins": day})
        out = j(body.body(ctx, "weigh_ins", "2026-10-06", None))
        assert out["entries"] == [{"id": 9, "date": "2026-10-06", "weight_kg": 78.4, "body_fat_pct": 21.5}]

    def test_an_empty_period_says_so(self, tmp_path):
        ctx = ctx_with(tmp_path, {"get_blood_pressure": {}})
        assert "Nothing recorded" in body.body(ctx, "blood_pressure", None, None)


class TestAccount:
    def test_hr_zones_become_bpm_ranges(self, tmp_path):
        zones = [{"sport": "RUNNING", "trainingMethod": "HR_MAX", "maxHeartRateUsed": 190,
                  "zone1Floor": 95, "zone2Floor": 114, "zone3Floor": 133, "zone4Floor": 152, "zone5Floor": 171}]
        ctx = ctx_with(tmp_path, {"get_heart_rate_zones": zones})
        out = j(account.profile(ctx, "hr_zones", "running"))[0]
        assert out["zones_bpm"] == {"z1": [95, 113], "z2": [114, 132], "z3": [133, 151],
                                    "z4": [152, 170], "z5": [171, 190]}

    def test_personal_records_are_labelled_and_formatted(self, tmp_path):
        prs = [{"typeId": 3, "value": 1498.2, "activityId": 5}, {"typeId": 7, "value": 21100.0}]
        ctx = ctx_with(tmp_path, {"get_personal_record": prs})
        out = j(account.achievements(ctx, "personal_records", "active", 1, 20))
        assert out[0]["record"] == "run_5km" and out[0]["value"] == "24:58"
        assert out[1]["value"] == "21.1 km"

    def test_gear_shows_totals_and_default_activity_types(self, tmp_path):
        g = FakeGarmin(
            {
                "get_gear": [{"uuid": "u1", "displayName": "Pegasus", "gearStatusName": "active"}],
                "get_gear_defaults": [{"uuid": "u1", "activityTypePk": 1, "defaultGear": True}],
                "get_activity_types": [{"typeId": 1, "typeKey": "running"}],
                "get_gear_stats": {"totalDistance": 412345.0, "totalActivities": 51},
            }
        )
        g.profile_id = 12345
        out = j(account.gear(FixedCtx(g, make_cfg(tmp_path)), True))[0]
        assert out == {"uuid": "u1", "name": "Pegasus", "status": "active", "default_for": ["running"],
                       "total_km": 412.3, "activities": 51}

    @pytest.mark.parametrize("path", ["https://evil.example/x", "usersummary", "/a?b=1"])
    def test_the_passthrough_only_takes_api_paths(self, tmp_path, path):
        with pytest.raises(InputError):
            account.api_get(ctx_with(tmp_path, {}), path, None)
