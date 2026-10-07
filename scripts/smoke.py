"""Read-only live check against the real Garmin account. Run after logging in:

    uv run python scripts\\smoke.py              every read tool and selector, once
    uv run python scripts\\smoke.py --capture    also save Garmin's raw payloads to
                                                 data/captures/ (git-ignored) to check
                                                 the field mappings against real data

Nothing here writes to the account.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from garmin_mcp import config
from garmin_mcp.client import GarminSession
from garmin_mcp.tools import (
    account,
    activities,
    body,
    courses,
    fit,
    nutrition,
    training,
    trends,
    wellness,
    workouts,
)

CAPTURES = Path(__file__).resolve().parents[1] / "data" / "captures"
PAUSE = 0.3  # be gentle with Garmin


def _days_ago(ctx, n: int) -> str:
    return ctx.today().fromordinal(ctx.today().toordinal() - n).isoformat()


def main() -> int:
    capture = "--capture" in sys.argv
    session = GarminSession(config.load())
    run = session.run
    results: list[tuple[str, str, int, str]] = []

    def check(name: str, fn) -> str | None:
        started = time.monotonic()
        try:
            text = fn()
        except Exception as exc:  # noqa: BLE001 - report every failure
            results.append((name, "FAIL", 0, str(exc).splitlines()[0][:160]))
            print(f"FAIL  {name}: {str(exc).splitlines()[0][:160]}", flush=True)
            return None
        took = time.monotonic() - started
        results.append((name, "ok", len(text), f"{took:.1f}s"))
        print(f"ok    {name}  {len(text)} chars  {took:.1f}s", flush=True)
        time.sleep(PAUSE)
        return text

    print(account.status(session), "\n", flush=True)
    if not session.logged_in:
        print("Not logged in; run garmin-mcp-auth login first.")
        return 1

    check("profile/profile", lambda: run(lambda c: account.profile(c, "profile", None)))
    check("profile/hr_zones", lambda: run(lambda c: account.profile(c, "hr_zones", None)))
    for m in wellness.METRICS:
        check(f"wellness/{m}", lambda m=m: run(lambda c: wellness.wellness(c, m, "yesterday", "summary")))
    for m in training.METRICS:
        check(f"training/{m}", lambda m=m: run(lambda c: training.training(c, m, "today", "summary")))
    for m in trends.METRICS:
        check(
            f"trend/{m}",
            lambda m=m: run(lambda c: trends.trend(c, m, _days_ago(c, 13), None, "daily")),
        )
    for m in sorted(trends.WEEKLY):
        check(f"trend/{m}/weekly", lambda m=m: run(lambda c: trends.trend(c, m, None, None, "weekly")))

    listing = check("activities", lambda: run(lambda c: activities.activities(c, None, None, None, 5, 0)))
    check("activities/count", lambda: run(lambda c: activities.activities(c, None, None, None, 0, 0)))
    first_id = None
    if listing:
        rows = json.loads(listing).get("activities") or []
        first_id = rows[0]["id"] if rows else None
    if first_id:
        for section in activities.SECTIONS:
            check(
                f"activity/{section}",
                lambda section=section: run(lambda c: activities.activity(c, first_id, [section], "summary", 60)),
            )
        check("activity_analysis", lambda: run(lambda c: fit.activity_analysis(c, first_id, [])))
        check("fit_messages", lambda: run(lambda c: fit.fit_messages(c, first_id, None, False, 0, 200)))

    for kind in body.KINDS:
        check(f"body/{kind}", lambda kind=kind: run(lambda c: body.body(c, kind, None, None)))
    for kind in ("list", "last_used", "primary", "alarms"):
        check(f"devices/{kind}", lambda kind=kind: run(lambda c: account.devices(c, kind, None, None)))
    check("gear", lambda: run(lambda c: account.gear(c, True)))
    for kind in account.ACHIEVEMENT_KINDS:
        check(f"achievements/{kind}", lambda kind=kind: run(lambda c: account.achievements(c, kind, "active", 1, 10)))
    for kind in ("activity_types", "event_types"):
        check(f"reference/{kind}", lambda kind=kind: run(lambda c: account.reference(c, kind, None)))
    check("workouts", lambda: run(lambda c: workouts.workouts(c, None, False)))
    check("calendar", lambda: run(lambda c: workouts.calendar(c, None, None, [])))
    check("nutrition/settings", lambda: run(lambda c: nutrition.nutrition(c, "settings", "today", None, None)))
    check("nutrition/summary", lambda: run(lambda c: nutrition.nutrition(c, "summary", None, None, None)))
    check("courses", lambda: run(lambda c: courses.courses(c, None)))

    if capture:
        _capture(session, first_id)

    failed = [r for r in results if r[1] == "FAIL"]
    print(f"\n{len(results) - len(failed)} ok, {len(failed)} failed.")
    for name, _, _, why in failed:
        print(f"  {name}: {why}")
    return 1 if failed else 0


def _capture(session: GarminSession, activity_id) -> None:
    """Garmin's raw payloads, for checking field mappings and unit quirks."""
    CAPTURES.mkdir(parents=True, exist_ok=True)

    def save(name: str, fn) -> None:
        try:
            data = session.run(lambda c: fn(c.g, c))
        except Exception as exc:  # noqa: BLE001
            data = {"error": str(exc)}
        (CAPTURES / f"{name}.json").write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        time.sleep(PAUSE)

    ago = _days_ago
    save("user_summary", lambda g, c: g.get_user_summary(ago(c, 1)))
    save("sleep", lambda g, c: g.get_sleep_data(c.today().isoformat()))
    save("hrv", lambda g, c: g.get_hrv_data(c.today().isoformat()))
    save("stress", lambda g, c: g.get_stress_data(ago(c, 1)))
    save("body_battery", lambda g, c: g.get_body_battery(ago(c, 1), ago(c, 1)))
    save("body_battery_events", lambda g, c: g.get_body_battery_events(ago(c, 1)))
    save("training_readiness", lambda g, c: g.get_training_readiness(c.today().isoformat()))
    save("training_status", lambda g, c: g.get_training_status(c.today().isoformat()))
    save("max_metrics", lambda g, c: g.get_max_metrics(c.today().isoformat()))
    save("race_predictions", lambda g, c: g.get_race_predictions())
    save("lactate_threshold", lambda g, c: g.get_lactate_threshold(latest=True))
    save("user_profile", lambda g, c: g.get_user_profile())
    save("userprofile_settings", lambda g, c: g.get_userprofile_settings())
    save("sleep_daily", lambda g, c: g.get_sleep_daily(ago(c, 7), c.today().isoformat()))
    save("hrv_range", lambda g, c: g.get_hrv_data_range(ago(c, 7), c.today().isoformat()))
    save("progress", lambda g, c: g.get_progress_summary_between_dates(ago(c, 30), c.today().isoformat(), "distance"))
    save("progress_calories", lambda g, c: g.get_progress_summary_between_dates(ago(c, 30), c.today().isoformat(), "calories"))
    save("running_tolerance", lambda g, c: g.get_running_tolerance(ago(c, 14), c.today().isoformat(), "daily"))
    save("weigh_ins", lambda g, c: g.get_weigh_ins(ago(c, 30), c.today().isoformat()))
    save("devices", lambda g, c: g.get_devices())
    save("device_last_used", lambda g, c: g.get_device_last_used())
    save("personal_records", lambda g, c: g.get_personal_record())
    save("calendar_month", lambda g, c: g.get_scheduled_workouts(c.today().year, c.today().month))
    save("activities", lambda g, c: g.get_activities(0, 3))
    if activity_id:
        aid = str(activity_id)
        save("activity", lambda g, c: g.get_activity(aid))
        save("activity_splits", lambda g, c: g.get_activity_splits(aid))
        save("activity_weather", lambda g, c: g.get_activity_weather(aid))
        save("activity_hr_zones", lambda g, c: g.get_activity_hr_in_timezones(aid))
        save("activity_exercise_sets", lambda g, c: g.get_activity_exercise_sets(aid))
    print(f"Raw payloads saved to {CAPTURES}")


if __name__ == "__main__":
    sys.exit(main())
