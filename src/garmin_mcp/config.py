"""Paths and settings: environment variables over a remembered settings file over defaults."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

HTTP_MODES = ("auto", "requests", "curl_cffi")


@dataclass(frozen=True)
class Config:
    home: Path
    timezone: str | None  # None: the Garmin account's own time zone
    download_dir: Path
    call_timeout: float
    max_chars: int
    read_only: bool
    enabled_tools: frozenset[str] | None
    disabled_tools: frozenset[str]
    http_mode: str
    is_cn: bool

    @property
    def token_file(self) -> Path:
        return self.home / "garmin_tokens.json"

    @property
    def login_state_file(self) -> Path:
        return self.home / "login_state.json"

    @property
    def settings_file(self) -> Path:
        return self.home / "settings.json"


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _names(value: str | None) -> frozenset[str] | None:
    if value is None or not value.strip():
        return None
    return frozenset(n.strip().lower() for n in value.split(",") if n.strip())


def read_settings(home: Path) -> dict:
    """The small remembered-settings file (download folder, last email). Never secrets."""
    try:
        data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_setting(home: Path, key: str, value: str) -> None:
    data = read_settings(home)
    data[key] = value
    home.mkdir(parents=True, exist_ok=True)
    (home / "settings.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def load(env: dict[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    home = Path(env.get("GARMIN_MCP_HOME") or Path.home() / ".garmin-mcp").expanduser()
    remembered = read_settings(home)

    download = (
        env.get("GARMIN_MCP_DOWNLOAD_DIR")
        or remembered.get("download_dir")
        or Path.home() / "Downloads" / "garmin-mcp"
    )

    http_mode = (env.get("GARMIN_MCP_HTTP") or "auto").strip().lower()
    if http_mode not in HTTP_MODES:
        raise ValueError(f"GARMIN_MCP_HTTP must be one of {', '.join(HTTP_MODES)}")

    timezone = (env.get("GARMIN_MCP_TIMEZONE") or "").strip() or None
    if timezone:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"GARMIN_MCP_TIMEZONE {timezone!r} is not an IANA time zone") from None

    return Config(
        home=home,
        timezone=timezone,
        download_dir=Path(download).expanduser(),
        call_timeout=float(env.get("GARMIN_MCP_CALL_TIMEOUT") or 90),
        max_chars=int(env.get("GARMIN_MCP_MAX_CHARS") or 40_000),
        read_only=_flag(env.get("GARMIN_MCP_READ_ONLY")),
        enabled_tools=_names(env.get("GARMIN_MCP_ENABLED_TOOLS")),
        disabled_tools=_names(env.get("GARMIN_MCP_DISABLED_TOOLS")) or frozenset(),
        http_mode=http_mode,
        is_cn=_flag(env.get("GARMIN_MCP_IS_CN")),
    )
