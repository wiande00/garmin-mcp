"""Register garmin-connect with Claude Desktop's chat side.

Claude Desktop reads claude_desktop_config.json once at startup and then keeps
rewriting the whole file from memory while it runs, so an entry added while it is
open is erased within seconds. The edit therefore has to happen while it is
closed. This script:

  1. waits until Claude Desktop has fully quit,
  2. adds the garmin-connect entry to every config copy it finds -- including
     the Microsoft Store edition's private copy, which is the one it reads --
     leaving every other server and setting exactly as it was,
  3. reopens Claude Desktop.

Run it by double-clicking scripts\\add-to-claude-desktop.cmd, *not* from a
terminal inside Claude: quitting Claude would end it before it got to step 2.

    --dry-run   show what would change, write nothing, do not wait
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SERVER_NAME = "garmin-connect"
WAIT_SECONDS = 600
REPO = Path(__file__).resolve().parents[1]


def find_uv() -> str:
    found = shutil.which("uv")
    if found:
        return str(Path(found).resolve())
    winget = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Packages"
    for candidate in winget.glob("astral-sh.uv_*/uv.exe"):
        return str(candidate)
    sys.exit("Could not find uv. Install it, or put it on PATH.")


def store_package_family() -> str | None:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-AppxPackage -Name Claude).PackageFamilyName"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    return out or None


def config_paths(family: str | None) -> list[Path]:
    paths = [Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json"]
    if family:
        # The Store edition's virtualised AppData. Its log says this is the copy
        # it actually reads.
        paths.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Packages" / family
                     / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")
    return [p for p in paths if p.exists()]


def desktop_running() -> bool:
    """Only the desktop app counts, not Claude Code's own claude.exe."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         ("@(Get-Process -Name claude -ErrorAction SilentlyContinue | "
          "Where-Object { $_.Path -like '*\\WindowsApps\\*' -or $_.Path -like '*\\AnthropicClaude\\*' }).Count")],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    return out not in ("", "0")


def merged(raw: str, entry: dict) -> tuple[str, bool]:
    """Return the file with the entry added, and whether anything changed."""
    data = json.loads(raw)
    servers = data.setdefault("mcpServers", {})
    if servers.get(SERVER_NAME) == entry:
        return raw, False
    servers[SERVER_NAME] = entry
    m = re.search(r'\n( +)"', raw)
    text = json.dumps(data, indent=len(m.group(1)) if m else 2, ensure_ascii=False)
    return text + ("\n" if raw.endswith("\n") else ""), True


def write_one(path: Path, entry: dict, *, dry_run: bool) -> str:
    raw = path.read_text(encoding="utf-8")
    before = json.loads(raw)
    text, changed = merged(raw, entry)
    if not changed:
        return f"already present: {path}"

    after = json.loads(text)
    others = lambda d: {k: v for k, v in d.get("mcpServers", {}).items() if k != SERVER_NAME}
    rest = lambda d: {k: v for k, v in d.items() if k != "mcpServers"}
    assert others(after) == others(before), "another server would change"
    assert rest(after) == rest(before), "another setting would change"

    if dry_run:
        return f"would add to: {path}  (keeps: {', '.join(others(before)) or 'none'})"
    backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")  # noqa: DTZ005
    shutil.copy2(path, backup)
    path.write_text(text, encoding="utf-8", newline="")
    json.loads(path.read_text(encoding="utf-8"))
    return f"added to: {path}\n    backup: {backup.name}"


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    family = store_package_family()
    entry = {"command": find_uv(),
             "args": ["--directory", str(REPO), "run", "--quiet", "garmin-mcp"]}
    paths = config_paths(family)
    if not paths:
        print("No claude_desktop_config.json found. Open Claude Desktop once, then retry.")
        return 1

    if not dry_run:
        if desktop_running():
            print("Waiting for Claude Desktop to quit.")
            print("Quit it completely: right-click its tray icon (by the clock) -> Quit.\n")
        deadline = time.monotonic() + WAIT_SECONDS
        while desktop_running():
            if time.monotonic() > deadline:
                print("Gave up waiting after 10 minutes. Nothing was changed.")
                return 1
            time.sleep(1)
        time.sleep(3)  # let its final save land before we touch the file
        if desktop_running():
            print("Claude Desktop started again before the edit. Nothing was changed; rerun.")
            return 1

    for path in paths:
        print(write_one(path, entry, dry_run=dry_run))

    if not dry_run and family:
        print("\nReopening Claude Desktop...")
        os.startfile(f"shell:AppsFolder\\{family}!Claude")
        print("Done. garmin-connect should now be listed under Settings -> Developer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
