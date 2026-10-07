# Parity with Taxuspt/garmin_mcp

Every one of the 153 tools in [Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp)
(as of 2026-10-07) and the garmin-mcp tool that covers it. `tests/test_parity.py` reads
this table and checks that each tool exists and that each `key=value` is a value the tool
accepts, so the table can't drift from the code.

## Tools

| Taxuspt tool | garmin-mcp tool | Arguments |
|---|---|---|
| get_activities_by_date | garmin_activities | start_date, end_date, activity_type, page, limit |
| get_activities_fordate | garmin_activities | start_date |
| get_activity | garmin_activity | sections=summary |
| set_activity_name | garmin_update_activity | name |
| set_activity_type | garmin_update_activity | type_key |
| set_activity_description | garmin_update_activity | description |
| set_activity_event_type | garmin_update_activity | event_type |
| set_perceived_effort | garmin_update_activity | rpe |
| set_activity_feel | garmin_update_activity | feel |
| get_activity_splits | garmin_activity | sections=splits |
| get_activity_typed_splits | garmin_activity | sections=typed_splits |
| get_activity_split_summaries | garmin_activity | sections=split_summaries |
| get_activity_weather | garmin_activity | sections=weather |
| get_activity_hr_in_timezones | garmin_activity | sections=hr_zones |
| get_activity_power_in_timezones | garmin_activity | sections=power_zones |
| get_activity_gear | garmin_activity | sections=gear |
| get_activity_exercise_sets | garmin_activity | sections=exercise_sets |
| count_activities | garmin_activities | limit=0 |
| get_activities | garmin_activities | page, limit |
| create_manual_activity | garmin_create_activity | |
| get_activity_types | garmin_reference | kind=activity_types |
| get_stats | garmin_wellness | metric=summary |
| get_stats_range | garmin_trend | metric=stats |
| get_energy_balance | garmin_nutrition | kind=energy_balance |
| get_user_summary | garmin_wellness | metric=summary, detail=full |
| get_body_composition | garmin_body | kind=body_composition |
| get_stats_and_body | garmin_wellness | metric=summary, detail=full |
| get_steps_data | garmin_wellness | metric=steps |
| get_daily_steps | garmin_trend | metric=steps |
| get_training_readiness | garmin_training | metric=readiness |
| get_body_battery | garmin_trend | metric=body_battery |
| get_body_battery_events | garmin_wellness | metric=body_battery |
| get_blood_pressure | garmin_body | kind=blood_pressure |
| get_floors | garmin_wellness | metric=floors |
| get_rhr_day | garmin_wellness | metric=resting_hr |
| get_heart_rates | garmin_wellness | metric=heart_rate, detail=full |
| get_heart_rates_summary | garmin_wellness | metric=heart_rate |
| get_hydration_data | garmin_wellness | metric=hydration |
| get_sleep_data | garmin_wellness | metric=sleep, detail=full |
| get_sleep_summary | garmin_wellness | metric=sleep |
| get_sleep_summary_range | garmin_trend | metric=sleep |
| get_stress_data | garmin_wellness | metric=stress, detail=full |
| get_stress_summary | garmin_wellness | metric=stress |
| get_respiration_data | garmin_wellness | metric=respiration, detail=full |
| get_respiration_summary | garmin_wellness | metric=respiration |
| get_spo2_data | garmin_wellness | metric=spo2 |
| get_all_day_stress | garmin_wellness | metric=stress, detail=full |
| get_all_day_events | garmin_wellness | metric=events |
| get_lifestyle_logging_data | garmin_wellness | metric=lifestyle |
| get_weekly_steps | garmin_trend | metric=steps, aggregation=weekly |
| get_weekly_stress | garmin_trend | metric=stress, aggregation=weekly |
| get_weekly_intensity_minutes | garmin_trend | metric=intensity_minutes, aggregation=weekly |
| get_morning_training_readiness | garmin_training | metric=morning_readiness |
| get_recovery_time_remaining | garmin_training | metric=recovery_time |
| get_full_name | garmin_status | |
| get_unit_system | garmin_status | |
| get_user_profile | garmin_profile | section=profile |
| get_userprofile_settings | garmin_profile | section=settings |
| get_heart_rate_zones | garmin_profile | section=hr_zones |
| set_heart_rate_zones | garmin_set_hr_zones | |
| get_devices | garmin_devices | kind=list |
| get_device_last_used | garmin_devices | kind=last_used |
| get_device_settings | garmin_devices | kind=settings |
| get_primary_training_device | garmin_devices | kind=primary |
| get_device_solar_data | garmin_devices | kind=solar |
| get_device_alarms | garmin_devices | kind=alarms |
| get_gear | garmin_gear | |
| add_gear_to_activity | garmin_update_activity | add_gear |
| remove_gear_from_activity | garmin_update_activity | remove_gear |
| get_weigh_ins | garmin_body | kind=weigh_ins |
| get_daily_weigh_ins | garmin_body | kind=weigh_ins |
| delete_weigh_ins | garmin_delete | kind=weigh_ins |
| add_weigh_in | garmin_add_weigh_in | |
| add_weigh_in_with_timestamps | garmin_add_weigh_in | when |
| get_goals | garmin_achievements | kind=goals |
| get_personal_record | garmin_achievements | kind=personal_records |
| get_earned_badges | garmin_achievements | kind=badges_earned |
| get_adhoc_challenges | garmin_achievements | kind=challenges_adhoc |
| get_available_badge_challenges | garmin_achievements | kind=challenges_available |
| get_badge_challenges | garmin_achievements | kind=challenges_badge |
| get_non_completed_badge_challenges | garmin_achievements | kind=challenges_unfinished |
| get_race_predictions | garmin_training | metric=race_predictions |
| get_inprogress_virtual_challenges | garmin_achievements | kind=challenges_virtual |
| get_progress_summary_between_dates | garmin_trend | metric=progress |
| get_hill_score | garmin_trend | metric=hill_score |
| get_endurance_score | garmin_trend | metric=endurance_score |
| get_training_effect | garmin_activity | sections=training_effect |
| get_hrv_data | garmin_wellness | metric=hrv |
| get_fitnessage_data | garmin_training | metric=fitness_age |
| get_training_status | garmin_training | metric=status |
| get_cycling_ftp | garmin_training | metric=cycling_ftp |
| get_lactate_threshold | garmin_training | metric=lactate_threshold |
| request_reload | garmin_request_reload | |
| get_training_load_trend | garmin_trend | metric=training_load |
| get_training_load_balance | garmin_training | metric=load_balance |
| get_hrv_trend | garmin_trend | metric=hrv |
| get_vo2max_trend | garmin_trend | metric=vo2max |
| get_respiration_trend | garmin_trend | metric=respiration |
| get_running_tolerance | garmin_training | metric=running_tolerance |
| get_running_tolerance_trend | garmin_trend | metric=running_tolerance |
| get_acclimation | garmin_training | metric=acclimation |
| get_workouts | garmin_workouts | |
| get_workout_by_id | garmin_workouts | workout_id |
| download_workout | garmin_download | kind=workout |
| upload_workout | garmin_upload_workouts | |
| upload_workouts | garmin_upload_workouts | |
| delete_workout | garmin_delete | kind=workouts |
| delete_workouts | garmin_delete | kind=workouts |
| get_scheduled_workouts | garmin_calendar | include=scheduled |
| get_garmin_coach_workouts | garmin_calendar | include=coach |
| get_training_plan_workouts | garmin_calendar | include=coach |
| schedule_workout | garmin_schedule_workouts | |
| schedule_workouts | garmin_schedule_workouts | |
| unschedule_workout | garmin_delete | kind=scheduled_workouts |
| unschedule_workouts | garmin_delete | kind=scheduled_workouts |
| add_body_composition | garmin_add_body_composition | |
| set_blood_pressure | garmin_add_blood_pressure | |
| add_hydration_data | garmin_add_hydration | |
| get_pregnancy_summary | garmin_womens_health | kind=pregnancy |
| get_menstrual_data_for_date | garmin_womens_health | kind=menstrual_day |
| get_menstrual_calendar_data | garmin_womens_health | kind=menstrual_calendar |
| get_nutrition_daily_food_log | garmin_nutrition | kind=food_log |
| get_nutrition_summary_between_dates | garmin_nutrition | kind=summary |
| get_nutrition_daily_meals | garmin_nutrition | kind=meals |
| get_nutrition_daily_settings | garmin_nutrition | kind=settings |
| set_nutrition_daily_settings | garmin_set_nutrition_goals | |
| search_foods | garmin_food_search | source=catalog |
| get_custom_foods | garmin_food_search | source=custom |
| get_custom_food_serving_units | garmin_reference | kind=food_serving_units |
| create_custom_food | garmin_save_custom_food | |
| update_custom_food | garmin_save_custom_food | food_id |
| delete_custom_food | garmin_delete | kind=custom_food |
| log_custom_food | garmin_log_food | food_id, serving_id |
| log_food | garmin_log_food | name, calories |
| delete_food_log | garmin_delete | kind=food_log |
| upsert_and_log | garmin_log_food | save_as_custom |
| create_walk_run_workout | garmin_create_workout | sport=running |
| create_run_workout | garmin_create_workout | sport=running |
| create_run_interval_workout | garmin_create_workout | sport=running |
| create_z2_walk_workout | garmin_create_workout | sport=walking |
| create_strength_workout | garmin_create_workout | sport=strength_training |
| schedule_week | garmin_schedule_workouts | |
| get_courses | garmin_courses | |
| get_course_details | garmin_courses | course_id |
| download_course_gpx | garmin_download | kind=course |
| upload_course | garmin_upload_course | |
| delete_course | garmin_delete | kind=course |
| get_activity_fit_messages | garmin_fit_messages | |
| get_activity_fit_data | garmin_activity_analysis | |
| get_power_duration_curve | garmin_power_curve | |
| download_activity_file | garmin_download | kind=activity |
| set_fit_download_dir | garmin_download | remember_dir |
| get_calendar_events | garmin_calendar | include=events |

## Resources

| Taxuspt resource | garmin-mcp resource |
|---|---|
| workout://templates/simple-run | workout://templates/simple-run |
| workout://templates/interval-running | workout://templates/interval-running |
| workout://templates/tempo-run | workout://templates/tempo-run |
| workout://templates/strength-circuit | workout://templates/strength-circuit |
| workout://reference/structure | workout://reference/structure |

## Not one-to-one

- `get_stress_data` and `get_all_day_stress` call the same Garmin endpoint, so one
  selector covers both. Likewise `get_stats` and `get_user_summary`.
- The six workout builders are one step language in `garmin_create_workout`, which also
  covers cycling, swimming, hiking and any mix of targets.
- `set_fit_download_dir` is `remember_dir=true` on `garmin_download`, or the
  `GARMIN_MCP_DOWNLOAD_DIR` setting.
- `upsert_and_log` is `garmin_log_food` with `save_as_custom=true`.
- Taxuspt's nine delete tools are one `garmin_delete`, which previews before it deletes.

## Beyond Taxuspt

- `garmin_status`: login, token lifetimes, last watch sync, last error.
- `garmin_activity` `sections=details`: the downsampled time series Taxuspt skipped.
- `garmin_api_get`: a read-only escape hatch for any Connect API path.
- Wellness `intensity_minutes`, trends for `resting_hr`, `calories` and `weight`,
  `badges_available` and `badges_in_progress`, `power_zones`, and blood-pressure deletes.
- `garmin_fit_messages` and `garmin_activity_analysis` decode with Garmin's official FIT
  SDK, and add running dynamics and R-R HRV.
- Workout FIT downloads are saved (Taxuspt discarded the bytes).
