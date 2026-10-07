"""Dates, units and output shaping."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from garmin_mcp import dates, shape, units
from garmin_mcp.errors import InputError

TZ = ZoneInfo("Europe/Stockholm")


class TestDates:
    def test_iso_dates_are_accepted(self):
        assert dates.parse("2026-10-06", TZ) == date(2026, 10, 6)

    @pytest.mark.parametrize("bad", ["06/10/2026", "2026-13-01", "last week"])
    def test_anything_else_is_refused_with_the_format(self, bad):
        with pytest.raises(InputError):
            dates.parse(bad, TZ)

    def test_a_range_longer_than_allowed_is_refused(self):
        with pytest.raises(InputError, match="at most 28"):
            dates.parse_range("2026-01-01", "2026-03-01", TZ, max_days=28)

    def test_a_missing_start_counts_back_from_the_end(self):
        assert dates.parse_range(None, "2026-10-07", TZ, max_days=90, default_days=7) == (
            date(2026, 10, 1),
            date(2026, 10, 7),
        )

    def test_gmt_timestamps_become_stockholm_time(self):
        # 05:12 GMT in summer time is 07:12 in Stockholm.
        assert dates.gmt_string_to_local("2026-07-01T05:12:00.0", TZ) == "2026-07-01 07:12"
        ms = datetime(2026, 7, 1, 5, 12, tzinfo=UTC).timestamp() * 1000
        assert dates.local_time(ms, TZ) == "2026-07-01 07:12"


class TestUnits:
    def test_pace_from_speed(self):
        assert units.pace_min_km(1000 / 300) == "5:00"
        assert units.pace_min_km(0) is None

    def test_durations(self):
        assert units.hms(3723) == "1:02:03" and units.hms(125) == "2:05"

    def test_the_known_quirks(self):
        assert units.kg(78400) == 78.4  # weights arrive in grams
        assert units.rpe_from_garmin(70) == 7.0  # RPE is stored times ten
        assert units.recovery_hours(1440) == 24.0  # recovery arrives in minutes
        assert units.celsius_from_f(50) == 10.0  # activity weather is Fahrenheit
        assert units.feel_label(75) == "strong"


class TestShape:
    def test_empty_values_are_dropped_but_zero_and_false_are_kept(self):
        assert shape.compact({"a": None, "b": [], "c": {"d": ""}, "e": 0, "f": False}) == {
            "e": 0,
            "f": False,
        }

    def test_a_none_inside_a_list_keeps_its_place(self):
        # Rows of a time series and [low, high] ranges must not shift.
        assert shape.compact({"rows": [["0:00", None, 82]], "range": [None, 5], "x": [{}, []]}) == {
            "rows": [["0:00", None, 82]],
            "range": [None, 5],
        }

    def test_long_answers_are_capped_with_advice(self):
        out = shape.cap("x" * 100, 40)
        assert out.startswith("x" * 40) and "truncated: 60 of 100" in out

    def test_downsample_keeps_both_ends(self):
        out = shape.downsample(list(range(100)), 5)
        assert len(out) == 5 and out[0] == 0 and out[-1] == 99

    def test_dig_stops_at_the_first_gap(self):
        assert shape.dig({"a": [{"b": 1}]}, "a", 0, "b") == 1
        assert shape.dig({"a": None}, "a", 0, "b") is None
