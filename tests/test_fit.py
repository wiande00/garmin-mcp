"""FIT decoding and analysis, using real FIT files built with Garmin's own encoder."""

import gzip
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta

import pytest
from garmin_fit_sdk import Encoder, Profile

from garmin_mcp.errors import InputError
from garmin_mcp.tools import downloads, fit

from .fakes import FakeGarmin, FixedCtx, make_cfg

START = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)
MESG = Profile["mesg_num"]


def ride(seconds=1500, climb=(300, 900), power=lambda i: 200, hr=lambda i: 130):
    """A ride at 5 m/s: flat, then a steady 6% climb between the given seconds, then flat."""
    enc = Encoder()
    enc.on_mesg(MESG["FILE_ID"], {"manufacturer": "development", "product": 1, "time_created": START, "type": "activity"})
    alt = 20.0
    for i in range(seconds):
        if climb[0] <= i < climb[1]:
            alt += 5 * 0.06
        enc.on_mesg(
            MESG["RECORD"],
            {
                "timestamp": START + timedelta(seconds=i),
                "distance": i * 5.0,
                "speed": 5.0,
                "altitude": alt,
                "heart_rate": hr(i),
                "power": power(i),
                "temperature": 12,
            },
        )
    enc.on_mesg(
        MESG["LAP"],
        {"timestamp": START + timedelta(seconds=seconds), "total_distance": seconds * 5.0, "total_timer_time": float(seconds)},
    )
    enc.on_mesg(
        MESG["SESSION"],
        {
            "timestamp": START + timedelta(seconds=seconds),
            "sport": "cycling",
            "total_distance": seconds * 5.0,
            "total_timer_time": float(seconds),
            "avg_power": 200,
            "threshold_power": 250,
        },
    )
    return enc.close()


@pytest.fixture(scope="module")
def ride_msgs():
    return fit.decode(ride())


class TestFile:
    def test_the_original_download_is_unzipped(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("123_ACTIVITY.fit", b".FIT-bytes")
        assert fit.unpack(buf.getvalue()) == b".FIT-bytes"

    def test_gzip_and_bare_files_are_handled(self):
        assert fit.unpack(gzip.compress(b"abc")) == b"abc"
        assert fit.unpack(b"plain") == b"plain"

    def test_something_that_is_not_fit_is_refused(self):
        with pytest.raises(InputError, match="not a FIT file"):
            fit.decode(b"<html>nope</html>" * 4)


class TestAnalysis:
    def test_the_climb_is_found_with_its_grade_and_vam(self, ride_msgs):
        (climb,) = fit.climbs(ride_msgs)
        assert climb["gain_m"] == pytest.approx(180, abs=10)
        assert climb["avg_grade_pct"] == pytest.approx(6.0, abs=0.5)
        # 180 m in 10 minutes is about 1080 m/h.
        assert climb["vam_m_per_h"] == pytest.approx(1080, rel=0.1)

    def test_time_on_each_grade(self, ride_msgs):
        bands = {b["grade_pct"]: b for b in fit.grades(ride_msgs)}
        assert bands["4..8"]["share_pct"] == pytest.approx(40, abs=5)
        assert bands["-1..1"]["share_pct"] == pytest.approx(60, abs=5)

    def test_power_curve_from_a_steady_ride(self, ride_msgs):
        curve = fit.power_curve(ride_msgs)
        assert curve["5s"] == 200 and curve["20min"] == 200 and "60min" not in curve

    def test_variability_uses_the_files_ftp(self, ride_msgs):
        v = fit.variability(ride_msgs)
        assert v["norm_power_w"] == 200 and v["variability_index"] == 1.0
        assert v["ftp_w"] == 250 and v["intensity_factor"] == 0.8

    def test_decoupling_shows_heart_rate_drifting_up(self):
        msgs = fit.decode(ride(seconds=1800, climb=(0, 0), hr=lambda i: 130 + i // 120))
        drift = fit.hr_drift(msgs)
        assert drift["based_on"] == "power" and drift["decoupling_pct"] > 3

    def test_analyse_reports_what_had_no_data(self, ride_msgs):
        out = fit.analyse(ride_msgs, ["summary", "hrv", "temperature"])
        assert out["summary"]["sport"] == "cycling" and out["temperature"]["max_c"] == 12
        assert out["no_data_for"] == ["hrv"]

    def test_rr_intervals_give_rmssd(self):
        msgs = {"hrv_mesgs": [{"time": [1.0, 1.02] * 20}]}
        assert fit.hrv(msgs)["rmssd_ms"] == 20.0

    def test_gear_changes_are_counted(self):
        events = [
            {"event": "rear_gear_change", "timestamp": START + timedelta(seconds=s), "front_gear": 50, "rear_gear": r}
            for s, r in ((0, 17), (60, 15), (90, 17))
        ]
        out = fit.di2({"event_mesgs": events})
        assert out["rear_shifts"] == 3 and out["most_used_gears"][0] == {"gear": "50x17", "time": "1:00"}


class TestTools:
    def ctx(self, tmp_path, responses):
        return FixedCtx(FakeGarmin(responses), make_cfg(tmp_path), date(2026, 10, 7))

    def test_fit_messages_leave_out_records_by_default(self, tmp_path):
        fit._FIT_CACHE.clear()
        ctx = self.ctx(tmp_path, {"download_activity": ride(seconds=200)})
        out = json.loads(fit.fit_messages(ctx, 9, None, False, 0, 1000))
        assert out["available"]["record"] == 200 and "record" not in out["messages"]
        assert "session" in out["messages"]

    def test_fit_messages_page_through_records(self, tmp_path):
        fit._FIT_CACHE.clear()
        ctx = self.ctx(tmp_path, {"download_activity": ride(seconds=200)})
        out = json.loads(fit.fit_messages(ctx, 9, ["record"], False, 150, 100))
        assert out["returned"] == 50 and out.get("next_offset") is None

    def test_season_curve_takes_the_best_of_each_duration(self, tmp_path):
        fit._FIT_CACHE.clear()
        files = {"1": ride(seconds=400, power=lambda i: 180), "2": ride(seconds=400, power=lambda i: 300 if i < 30 else 150)}
        acts = [{"activityId": 1, "avgPower": 180, "startTimeLocal": "2026-10-01 07:00:00"},
                {"activityId": 2, "avgPower": 150, "startTimeLocal": "2026-10-03 07:00:00"}]
        ctx = self.ctx(tmp_path, {"get_activities": acts, "download_activity": lambda aid, dl_fmt: files[aid]})
        out = json.loads(fit.season_power_curve(ctx, "cycling", 2))
        best = {b["duration"]: b for b in out["best"]}
        assert best["5s"]["watts"] == 300 and best["5s"]["activity_id"] == 2
        assert best["5min"]["watts"] == 180 and best["5min"]["activity_id"] == 1

    def test_a_download_lands_in_the_folder_as_a_fit_file(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("9_ACTIVITY.fit", b"fit-bytes")
        ctx = self.ctx(tmp_path, {"download_activity": buf.getvalue()})
        out = downloads.download(ctx, "activity", 9, "fit", str(tmp_path / "out"), False)
        assert (tmp_path / "out" / "activity_9.fit").read_bytes() == b"fit-bytes"
        assert "Saved" in out

    def test_a_course_becomes_a_gpx_track_with_waypoints(self):
        gpx = downloads.course_gpx(
            {"courseName": "Loop & back", "geoPoints": [{"latitude": 59.3, "longitude": 18.0, "elevation": 12}],
             "coursePoints": [{"lat": 59.3, "lon": 18.0, "name": "Water"}]}
        )
        assert "<name>Loop &amp; back</name>" in gpx
        assert '<trkpt lat="59.3" lon="18.0"><ele>12</ele></trkpt>' in gpx and "<name>Water</name>" in gpx

    def test_a_relative_output_folder_is_refused(self, tmp_path):
        with pytest.raises(InputError, match="full path"):
            downloads.download(self.ctx(tmp_path, {"download_workout": b"x"}), "workout", 1, "fit", "relative/dir", False)
