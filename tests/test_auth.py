"""The saved login: diagnosing it, refusing to retry too soon, and proving a new one works."""

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from garmin_mcp import auth
from garmin_mcp.errors import AuthExpired, NotLoggedIn, TokenUnreadable

from .fakes import FakeGarmin, make_cfg


def jwt(exp: datetime) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part({'exp': int(exp.timestamp())})}.sig"


def write_tokens(cfg, **fields):
    cfg.home.mkdir(parents=True, exist_ok=True)
    cfg.token_file.write_text(json.dumps(fields), encoding="utf-8")


NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


class TestTokenState:
    def test_no_file_means_not_logged_in(self, tmp_path):
        assert auth.token_state(make_cfg(tmp_path)).status == "missing"

    def test_a_file_that_is_not_json_is_corrupt(self, tmp_path):
        cfg = make_cfg(tmp_path)
        cfg.home.mkdir(parents=True)
        cfg.token_file.write_text("{nope", encoding="utf-8")
        state = auth.token_state(cfg)
        assert state.status == "corrupt" and "JSON" in state.detail

    def test_a_file_without_an_access_token_is_corrupt(self, tmp_path):
        cfg = make_cfg(tmp_path)
        write_tokens(cfg, di_refresh_token="x")
        assert auth.token_state(cfg).status == "corrupt"

    def test_expiry_dates_are_read_from_the_tokens(self, tmp_path):
        cfg = make_cfg(tmp_path)
        write_tokens(
            cfg,
            di_token=jwt(NOW + timedelta(hours=4)),
            di_refresh_token=jwt(NOW + timedelta(days=90)),
        )
        state = auth.token_state(cfg, now=NOW)
        assert state.status == "ok"
        assert state.access_expires == NOW + timedelta(hours=4)
        assert state.refresh_expires == NOW + timedelta(days=90)

    def test_an_expired_refresh_token_means_logging_in_again(self, tmp_path):
        cfg = make_cfg(tmp_path)
        write_tokens(
            cfg, di_token=jwt(NOW - timedelta(days=40)), di_refresh_token=jwt(NOW - timedelta(days=1))
        )
        state = auth.token_state(cfg, now=NOW)
        assert state.status == "expired" and "refresh token ran out" in state.detail

    def test_an_expired_access_token_alone_is_fine_because_it_renews(self, tmp_path):
        cfg = make_cfg(tmp_path)
        write_tokens(cfg, di_token=jwt(NOW - timedelta(hours=1)), di_refresh_token="opaque")
        assert auth.token_state(cfg, now=NOW).status == "ok"


class TestOpenWithTokens:
    def test_missing_tokens_say_how_to_log_in(self, tmp_path):
        with pytest.raises(NotLoggedIn, match="garmin-mcp-auth login"):
            auth.open_with_tokens(make_cfg(tmp_path))

    def test_corrupt_tokens_are_reported_as_unusable(self, tmp_path):
        cfg = make_cfg(tmp_path)
        write_tokens(cfg, nothing=True)
        with pytest.raises(TokenUnreadable):
            auth.open_with_tokens(cfg)

    def test_expired_tokens_are_reported_before_calling_garmin(self, tmp_path):
        cfg = make_cfg(tmp_path)
        past = datetime.now(UTC) - timedelta(days=2)
        write_tokens(cfg, di_token=jwt(past), di_refresh_token=jwt(past))
        with pytest.raises(AuthExpired):
            auth.open_with_tokens(cfg, garmin_cls=lambda **kw: pytest.fail("called Garmin"))

    def test_good_tokens_log_in_from_the_home_folder_only(self, tmp_path):
        cfg = make_cfg(tmp_path)
        write_tokens(cfg, di_token="opaque", di_refresh_token="opaque")
        made = []

        def garmin_cls(**kwargs):
            g = FakeGarmin()
            g.init_kwargs = kwargs
            made.append(g)
            return g

        garmin, hybrid = auth.open_with_tokens(cfg, garmin_cls)
        assert made[0].init_kwargs == {"is_cn": False}  # no email, no password
        assert garmin.called("login") == [((str(cfg.home),), {})]
        assert hybrid is not None  # the 403 fallback is installed


class TestCooldown:
    def test_a_failure_blocks_new_attempts_for_a_while(self, tmp_path):
        cfg = make_cfg(tmp_path)
        auth.record_failure(cfg, "bad password", rate_limited=False)
        left, reason = auth.cooldown_remaining(cfg)
        assert timedelta(minutes=14) < left <= timedelta(minutes=15)
        assert reason == "bad password"

    def test_a_rate_limit_blocks_for_an_hour(self, tmp_path):
        cfg = make_cfg(tmp_path)
        auth.record_failure(cfg, "429", rate_limited=True)
        left, _ = auth.cooldown_remaining(cfg)
        assert left > timedelta(minutes=59)

    def test_the_cooldown_runs_out(self, tmp_path):
        cfg = make_cfg(tmp_path)
        auth.record_failure(cfg, "x", rate_limited=False)
        later = datetime.now(UTC) + timedelta(minutes=16)
        assert auth.cooldown_remaining(cfg, now=later) is None


class TestInteractiveLogin:
    def make_cls(self, cfg, *, fail_login=None, name="Test Runner"):
        def garmin_cls(**kwargs):
            g = FakeGarmin(full_name=name, display_name=name)
            if kwargs.get("email"):  # the password login
                if fail_login:
                    g.responses["login"] = fail_login
                else:
                    g.responses["login"] = lambda *a, **k: (None, None)

                def dump(path):
                    write_tokens(cfg, di_token="opaque", di_refresh_token="opaque")

                g.client.dump = dump
            return g

        return garmin_cls

    def test_success_saves_tokens_and_proves_they_work(self, tmp_path):
        cfg = make_cfg(tmp_path)
        name = auth.interactive_login(cfg, "a@b.c", "pw", lambda: "123456", self.make_cls(cfg))
        assert name == "Test Runner"
        assert cfg.token_file.exists()
        assert auth.cooldown_remaining(cfg) is None

    def test_failure_is_recorded_for_the_cooldown(self, tmp_path):
        cfg = make_cfg(tmp_path)
        cls = self.make_cls(cfg, fail_login=RuntimeError("401 Unauthorized"))
        with pytest.raises(RuntimeError):
            auth.interactive_login(cfg, "a@b.c", "pw", lambda: "1", cls)
        assert auth.cooldown_remaining(cfg) is not None

    def test_a_login_whose_tokens_cannot_load_the_profile_fails(self, tmp_path):
        cfg = make_cfg(tmp_path)
        cls = self.make_cls(cfg, name="")
        with pytest.raises(Exception, match="could not load the profile"):
            auth.interactive_login(cfg, "a@b.c", "pw", lambda: "1", cls)
