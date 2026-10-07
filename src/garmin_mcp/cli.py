"""garmin-mcp-auth: log in once in a terminal, check the saved login, or remove it.

    garmin-mcp-auth login [--email ADDRESS] [--force]
    garmin-mcp-auth status [--offline]
    garmin-mcp-auth logout [--yes]

The MCP server never sees the password. `login` asks for it here, plus the MFA code if
Garmin wants one, then saves only the tokens it gets back.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from zoneinfo import ZoneInfo

from . import auth, config, dates
from .client import local_tz, map_error


def _say(text: str = "") -> None:
    print(text, flush=True)


def cmd_login(cfg: config.Config, args: argparse.Namespace) -> int:
    wait = auth.cooldown_remaining(cfg)
    if wait and not args.force:
        left, reason = wait
        minutes = int(left.total_seconds() // 60) + 1
        _say(f"Not trying yet: a login failed recently ({reason}).")
        _say(
            f"Garmin can block an account for days after repeated attempts, so wait "
            f"{minutes} more minutes, or pass --force if you are sure."
        )
        return 2

    state = auth.token_state(cfg)
    if state.usable and not args.force:
        try:
            garmin, _ = auth.open_with_tokens(cfg)
            name = garmin.get_full_name() or garmin.display_name
            _say(f"Already logged in as {name}; the saved login works.")
            _say("Nothing to do. Use --force to log in again anyway.")
            return 0
        except Exception as exc:  # noqa: BLE001 - a broken login is why we're here
            _say(f"The saved login no longer works ({map_error(exc)}).")
            _say("Logging in again.")

    remembered = config.read_settings(cfg.home).get("email")
    email = args.email or input(
        f"Garmin email{f' [{remembered}]' if remembered else ''}: "
    ).strip() or remembered
    if not email:
        _say("No email given.")
        return 1
    password = getpass.getpass("Garmin password (not shown): ")
    if not password:
        _say("No password given.")
        return 1

    def prompt_mfa() -> str:
        return input("Garmin sent a verification code (email or authenticator app). Code: ").strip()

    _say("Logging in. This can take up to half a minute...")
    try:
        name = auth.interactive_login(cfg, email, password, prompt_mfa)
    except Exception as exc:  # noqa: BLE001 - shown to the user
        _say(f"Login failed: {map_error(exc)}")
        _say("A cooldown now applies before the next attempt (see `status`).")
        return 1
    finally:
        password = ""

    config.write_setting(cfg.home, "email", email)
    _say(f"Logged in as {name}. Tokens saved to {cfg.token_file}.")
    _say("The MCP server picks them up on its next call; no restart needed.")
    return 0


def cmd_status(cfg: config.Config, args: argparse.Namespace) -> int:
    tz = ZoneInfo(cfg.timezone) if cfg.timezone else local_tz()
    state = auth.token_state(cfg)
    _say(state.describe(tz))
    wait = auth.cooldown_remaining(cfg)
    if wait:
        left, reason = wait
        _say(f"Login cooldown: {int(left.total_seconds() // 60) + 1} min left ({reason}).")
    if not state.usable:
        _say("Run: garmin-mcp-auth login")
        return 1
    if args.offline:
        return 0
    try:
        garmin, hybrid = auth.open_with_tokens(cfg)
        name = garmin.get_full_name() or garmin.display_name
        last = garmin.get_device_last_used() or {}
    except Exception as exc:  # noqa: BLE001 - shown to the user
        _say(f"Live check FAILED: {map_error(exc)}")
        return 1
    _say(f"Live check OK: logged in as {name}.")
    synced = dates.local_time(last.get("lastUsedDeviceUploadTime"), tz)
    if last.get("lastUsedDeviceName") or synced:
        _say(f"Last watch sync: {last.get('lastUsedDeviceName', 'device')} at {synced}.")
    if hybrid is not None:
        _say(f"HTTP: {hybrid.describe()}.")
    return 0


def cmd_logout(cfg: config.Config, args: argparse.Namespace) -> int:
    if not cfg.token_file.exists():
        _say(f"No saved login at {cfg.token_file}.")
        return 0
    if not args.yes:
        answer = input(f"Delete the saved Garmin login at {cfg.token_file}? [y/N] ")
        if answer.strip().lower() not in {"y", "yes"}:
            _say("Kept.")
            return 0
    cfg.token_file.unlink()
    _say("Deleted. The MCP server will ask for a new login on its next call.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="garmin-mcp-auth", description="Log in to Garmin once, for the garmin-mcp server."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("login", help="log in with password (+ MFA) and save the tokens")
    p.add_argument("--email")
    p.add_argument("--force", action="store_true", help="ignore the cooldown / working login")
    p = sub.add_parser("status", help="show the saved login and test it against Garmin")
    p.add_argument("--offline", action="store_true", help="only inspect the token file")
    p = sub.add_parser("logout", help="delete the saved tokens")
    p.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)

    cfg = config.load()
    handler = {"login": cmd_login, "status": cmd_status, "logout": cmd_logout}[args.command]
    try:
        return handler(cfg, args)
    except KeyboardInterrupt:
        _say("\nCancelled.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
