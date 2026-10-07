@echo off
rem Double-click this from File Explorer -- not from a terminal inside Claude.
rem It waits for Claude Desktop to quit, adds garmin-connect to its config,
rem then reopens it. See add_to_claude_desktop.py for why it must work this way.
cd /d "%~dp0.."
uv run --quiet python scripts\add_to_claude_desktop.py %*
echo.
pause
