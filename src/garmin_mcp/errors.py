"""Failures we expect, each with a message written for the model to relay to the user.

The server turns every one of these into an MCP `ToolError`, so the call is marked as
failed and the message arrives intact.
"""

from __future__ import annotations

from pathlib import Path

GIT_SOURCE = "git+https://github.com/wiande00/garmin-mcp"
_CHECKOUT = Path(__file__).resolve().parents[2]


def _login_command() -> str:
    """The command that works for how this copy was installed."""
    if (_CHECKOUT / "pyproject.toml").is_file() and (_CHECKOUT / "src" / "garmin_mcp").is_dir():
        return f'uv run --directory "{_CHECKOUT}" garmin-mcp-auth login'
    return f"uvx --from {GIT_SOURCE} garmin-mcp-auth login   (or: garmin-mcp-auth login)"


LOGIN_COMMAND = _login_command()


class GarminMcpError(Exception):
    """Base class. The message is shown to the model as-is."""


class InputError(GarminMcpError):
    """The arguments don't make sense (bad date, unknown selector, missing field)."""


class NotLoggedIn(GarminMcpError):
    def __init__(self, token_file: Path):
        super().__init__(
            f"Not logged in to Garmin: no saved login at {token_file}. "
            f"Run this once in a terminal (it asks for the password and MFA code "
            f"there, never in chat):\n    {LOGIN_COMMAND}"
        )


class TokenUnreadable(GarminMcpError):
    def __init__(self, token_file: Path, why: str):
        super().__init__(
            f"The saved Garmin login at {token_file} can't be used ({why}). "
            f"Log in again in a terminal:\n    {LOGIN_COMMAND}"
        )


class AuthExpired(GarminMcpError):
    def __init__(self, detail: str = ""):
        extra = f" ({detail})" if detail else ""
        super().__init__(
            f"Garmin rejected the saved login: the session has expired or was revoked{extra}. "
            f"Log in again in a terminal:\n    {LOGIN_COMMAND}"
        )


class RateLimited(GarminMcpError):
    def __init__(self, detail: str = ""):
        extra = f" ({detail})" if detail else ""
        super().__init__(
            f"Garmin is rate-limiting this account (HTTP 429){extra}. Wait before trying "
            "again, and don't retry a login repeatedly: repeated attempts can lock the "
            "account for 48-72 hours."
        )


class Blocked(GarminMcpError):
    """HTTP 403 that survived the curl_cffi retry."""


class NotFound(GarminMcpError):
    pass


class UploadConsent(GarminMcpError):
    def __init__(self) -> None:
        super().__init__(
            "Garmin refused the write with HTTP 412 (precondition failed). On EU accounts "
            "this usually means the consent for uploads has not been granted yet: grant it "
            "in Garmin Connect's privacy/data settings, then try again."
        )


class Upstream(GarminMcpError):
    """Any other Garmin failure. The message keeps Garmin's status and text."""


class GarminTimeout(GarminMcpError):
    def __init__(self, seconds: float):
        super().__init__(
            f"Garmin did not answer within {seconds:.0f} s. It may be slow or unreachable; "
            "try again, or narrow the request (shorter date range)."
        )
