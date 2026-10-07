"""Error translation, the lazy login, and the cache."""

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from garmin_mcp.client import Cache, GarminSession, map_error
from garmin_mcp.errors import (
    AuthExpired,
    Blocked,
    InputError,
    NotFound,
    RateLimited,
    UploadConsent,
    Upstream,
)

from .fakes import FakeGarmin, make_cfg


class TestMapError:
    @pytest.mark.parametrize(
        "exc, kind",
        [
            (GarminConnectTooManyRequestsError("slow down"), RateLimited),
            (GarminConnectAuthenticationError("nope"), AuthExpired),
            (GarminConnectNotFoundError("API Error 404"), NotFound),
            (GarminConnectConnectionError("API Error 401 - Unauthorized"), AuthExpired),
            (GarminConnectConnectionError("API Error 403"), Blocked),
            (GarminConnectConnectionError("API Error 412"), UploadConsent),
            (GarminConnectConnectionError("API Error 429"), RateLimited),
            (GarminConnectConnectionError("API Error 503"), Upstream),
            (ValueError("Invalid API path: '/a?b'"), InputError),
        ],
    )
    def test_each_failure_gets_its_own_kind(self, exc, kind):
        assert isinstance(map_error(exc), kind)

    def test_a_4xx_keeps_garmins_status_and_text(self):
        err = map_error(GarminConnectConnectionError("API Error 400 - Invalid date"))
        assert "400" in str(err) and "Invalid date" in str(err)
        assert "unreachable" not in str(err).lower()

    def test_a_403_mentions_the_curl_cffi_retry_when_there_was_one(self):
        err = map_error(GarminConnectConnectionError("API Error 403"), "Already retried.")
        assert "Already retried." in str(err)


class TestSession:
    def test_nothing_logs_in_until_a_tool_runs(self, tmp_path):
        opened = []
        s = GarminSession(make_cfg(tmp_path), lambda: opened.append(1) or (FakeGarmin(), None))
        assert opened == [] and not s.logged_in
        assert s.run(lambda ctx: ctx.g.get_full_name()) == "Test Runner"
        s.run(lambda ctx: "again")
        assert opened == [1]

    def test_an_expired_login_is_dropped_so_the_next_call_reopens(self, tmp_path):
        opened = []

        def opener():
            opened.append(1)
            return FakeGarmin({"get_stats": GarminConnectAuthenticationError("expired")}), None

        s = GarminSession(make_cfg(tmp_path), opener)
        with pytest.raises(AuthExpired):
            s.run(lambda ctx: ctx.g.get_stats("2026-10-06"))
        assert not s.logged_in
        assert "expired" in s.last_error[1]

    def test_bad_input_is_not_remembered_as_the_last_error(self, tmp_path):
        s = GarminSession(make_cfg(tmp_path), lambda: (FakeGarmin(), None))

        def bad(ctx):
            raise InputError("date must be YYYY-MM-DD")

        with pytest.raises(InputError):
            s.run(bad)
        assert s.last_error is None


class TestCache:
    def test_a_value_is_computed_once(self):
        c, calls = Cache(), []
        assert c.get_or("k", lambda: calls.append(1) or 5) == 5
        assert c.get_or("k", lambda: calls.append(1) or 6) == 5
        assert calls == [1]

    def test_the_oldest_entry_goes_first(self):
        c = Cache(maxsize=2)
        for k in "abc":
            c.get_or(k, lambda k=k: k)
        assert c.get_or("a", lambda: "new") == "new"
