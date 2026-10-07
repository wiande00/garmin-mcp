"""Logging in to Garmin, and knowing why a saved login does or doesn't work.

The rules come from Garmin's March 2026 login change and the 48-72 h account blocks
people hit by retrying: the user logs in once, in a terminal, with their password and MFA
code; the tokens are saved; the MCP server only ever reuses those tokens. Nothing here
stores the password, and nothing retries a password login on its own.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import transport
from .config import Config
from .errors import AuthExpired, GarminMcpError, NotLoggedIn, TokenUnreadable

FAILURE_COOLDOWN = timedelta(minutes=15)
RATE_LIMIT_COOLDOWN = timedelta(minutes=60)


# ------------------------------------------------------------------ token file


def jwt_claims(token: Any) -> dict | None:
    """The unverified payload of a JWT, or None if it isn't one."""
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    part = token.split(".")[1]
    try:
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _expiry(token: Any) -> datetime | None:
    claims = jwt_claims(token)
    exp = claims.get("exp") if claims else None
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        return datetime.fromtimestamp(exp, UTC)
    return None


@dataclass
class TokenState:
    path: Path
    status: str  # missing | unreadable | corrupt | expired | ok
    detail: str = ""
    saved_at: datetime | None = None
    access_expires: datetime | None = None
    refresh_expires: datetime | None = None

    @property
    def usable(self) -> bool:
        return self.status == "ok"

    def describe(self, tz) -> str:
        if self.status == "missing":
            return f"No saved login at {self.path}."
        if self.status in ("unreadable", "corrupt"):
            return f"Saved login at {self.path} is {self.status}: {self.detail}."
        parts = [f"Saved login at {self.path}"]
        if self.saved_at:
            parts.append(f"written {self.saved_at.astimezone(tz):%Y-%m-%d %H:%M}")
        if self.access_expires:
            parts.append(
                f"access token valid until {self.access_expires.astimezone(tz):%Y-%m-%d %H:%M}"
                " (renewed automatically)"
            )
        if self.refresh_expires:
            parts.append(
                f"refresh token valid until {self.refresh_expires.astimezone(tz):%Y-%m-%d}"
            )
        text = ", ".join(parts) + "."
        if self.status == "expired":
            text += f" EXPIRED: {self.detail}."
        return text


def token_state(cfg: Config, now: datetime | None = None) -> TokenState:
    path = cfg.token_file
    now = now or datetime.now(UTC)
    if not path.exists():
        return TokenState(path, "missing")
    try:
        text = path.read_text(encoding="utf-8")
        saved = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError as exc:
        return TokenState(path, "unreadable", exc.strerror or str(exc))
    try:
        data = json.loads(text)
    except ValueError:
        return TokenState(path, "corrupt", "it is not valid JSON", saved)
    if not isinstance(data, dict) or not data.get("di_token"):
        return TokenState(path, "corrupt", "it holds no access token", saved)

    state = TokenState(
        path,
        "ok",
        saved_at=saved,
        access_expires=_expiry(data.get("di_token")),
        refresh_expires=_expiry(data.get("di_refresh_token")),
    )
    if state.refresh_expires and state.refresh_expires <= now:
        state.status = "expired"
        state.detail = f"the refresh token ran out on {state.refresh_expires:%Y-%m-%d}"
    elif not data.get("di_refresh_token") and state.access_expires and state.access_expires <= now:
        state.status = "expired"
        state.detail = "the access token ran out and there is no refresh token"
    return state


def open_with_tokens(cfg: Config, garmin_cls: Any = None) -> tuple[Any, transport.HybridSession | None]:
    """A logged-in Garmin client from the saved tokens alone. Never uses a password."""
    state = token_state(cfg)
    if state.status == "missing":
        raise NotLoggedIn(cfg.token_file)
    if state.status in ("unreadable", "corrupt"):
        raise TokenUnreadable(cfg.token_file, state.detail)
    if state.status == "expired":
        raise AuthExpired(state.detail)

    if garmin_cls is None:
        from garminconnect import Garmin as garmin_cls
    garmin = garmin_cls(is_cn=cfg.is_cn)
    hybrid = transport.install(garmin, cfg.http_mode)
    # Loads the tokens, refreshes the access token if it is close to expiry (writing the
    # new one back to the file), then loads the profile and settings.
    garmin.login(str(cfg.home))
    return garmin, hybrid


# ------------------------------------------------------------------ cooldown


def _read_login_state(cfg: Config) -> dict:
    try:
        data = json.loads(cfg.login_state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cooldown_remaining(cfg: Config, now: datetime | None = None) -> tuple[timedelta, str] | None:
    """Time left before another password login is sensible, and why. None if clear."""
    data = _read_login_state(cfg)
    when = data.get("last_failure_at")
    if not when:
        return None
    try:
        failed_at = datetime.fromisoformat(when)
    except ValueError:
        return None
    wait = RATE_LIMIT_COOLDOWN if data.get("rate_limited") else FAILURE_COOLDOWN
    left = failed_at + wait - (now or datetime.now(UTC))
    if left <= timedelta(0):
        return None
    return left, str(data.get("reason") or "the last login failed")


def record_failure(cfg: Config, reason: str, rate_limited: bool) -> None:
    cfg.home.mkdir(parents=True, exist_ok=True)
    cfg.login_state_file.write_text(
        json.dumps(
            {
                "last_failure_at": datetime.now(UTC).isoformat(),
                "reason": reason[:300],
                "rate_limited": rate_limited,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def clear_failures(cfg: Config) -> None:
    try:
        cfg.login_state_file.unlink()
    except FileNotFoundError:
        pass


# ------------------------------------------------------------------ password login


def interactive_login(
    cfg: Config,
    email: str,
    password: str,
    prompt_mfa: Callable[[], str],
    garmin_cls: Any = None,
) -> str:
    """Log in with a password (and MFA if asked), save the tokens, and prove they work.

    Returns the account's name. Every failure is recorded so the CLI can enforce a
    cooldown instead of letting repeated attempts get the account blocked.
    """
    from garminconnect import GarminConnectTooManyRequestsError

    if garmin_cls is None:
        from garminconnect import Garmin as garmin_cls
    try:
        garmin = garmin_cls(email=email, password=password, is_cn=cfg.is_cn, prompt_mfa=prompt_mfa)
        transport.install(garmin, cfg.http_mode)
        garmin.login()  # no tokenstore: always a fresh login
        cfg.home.mkdir(parents=True, exist_ok=True)
        garmin.client.dump(str(cfg.home))

        # Prove the saved file works on its own (taxuspt #130 saved tokens that didn't).
        fresh, _ = open_with_tokens(cfg, garmin_cls)
        name = fresh.get_full_name() or getattr(fresh, "display_name", None)
        if not name:
            raise GarminMcpError(
                "Garmin accepted the login, but the saved tokens could not load the profile."
            )
    except GarminConnectTooManyRequestsError as exc:
        record_failure(cfg, str(exc), rate_limited=True)
        raise
    except Exception as exc:
        record_failure(cfg, f"{type(exc).__name__}: {exc}", rate_limited="429" in str(exc))
        raise
    clear_failures(cfg)
    return name
