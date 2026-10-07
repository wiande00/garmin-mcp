# garmin-mcp

[![CI](https://github.com/wiande00/garmin-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/wiande00/garmin-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)
![MCP](https://img.shields.io/badge/MCP-server-8A2BE2.svg)

**A Model Context Protocol (MCP) server for Garmin Connect.** Ask Claude (or any MCP
client) about your sleep, HRV, Body Battery, stress, training readiness, VO2max, runs and
rides, and have it build structured workouts that sync to your watch.

> "How did I sleep last night, and am I ready for intervals today?"
> "Show the splits and heart-rate zones of my last run."
> "Plot my HRV and resting heart rate for September."
> "Build me 6 x 400 m at 3:50-4:05/km for Thursday."
> "Where did I lose time on the climbs in Sunday's ride?"

It works with any Garmin device that syncs to Garmin Connect (Forerunner, Fenix, Epix,
Venu, Vivoactive, Edge, Index scales...). It runs locally on your computer, and your
password never reaches the server.

## Why this one

- **Everything, in 39 tools.** It covers every one of the 153 tools in
  [Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp), mapped one by one in
  [PARITY.md](PARITY.md) and enforced by a test. Related endpoints sit behind one tool
  with a `metric` or `kind` selector, so the whole server costs a quarter of the context
  in every chat.
- **A login that survives Garmin's changes.** Built on
  [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) 0.3.x,
  which recovered from Garmin's March 2026 login change. You log in once in a terminal
  (MFA supported). The server only reuses the saved tokens and never retries a password
  login, because repeated attempts can lock an account for days.
- **Answers a model can use.** Compact JSON with the unit in every key (`distance_km`,
  `avg_hr_bpm`, `pace_min_km`), in your account's time zone, with today's partial data
  marked. Garmin's unit quirks (grams, RPE ×10, Fahrenheit, knots, centimetres...) are
  converted in one place and were checked against Garmin Connect on a real account.
- **Deeper analysis.** Decodes the original FIT file with Garmin's official FIT SDK:
  climbs and VAM, time on each grade, aerobic decoupling, power curve, NP/IF/TSS, R-R
  HRV, running and cycling dynamics, Di2 gear use.
- **Safe writes.** Every tool carries MCP annotations, so clients know which ones change
  your account. Deletes always preview first. `GARMIN_MCP_READ_ONLY=1` removes every
  write.
- **Windows-proof.** A Cloudflare 403 on Windows 11
  ([python-garminconnect #444](https://github.com/cyberjunky/python-garminconnect/issues/444))
  is retried automatically through curl_cffi.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/).

**1. Log in once**, in a terminal. It asks for your Garmin email, password and, if Garmin
sends one, the verification code. Only the tokens are saved, to `~/.garmin-mcp/`.

```bash
uvx --from git+https://github.com/wiande00/garmin-mcp garmin-mcp-auth login
```

**2. Add the server to your MCP client.**

Claude Code:

```bash
claude mcp add --scope user garmin -- uvx --from git+https://github.com/wiande00/garmin-mcp garmin-mcp
```

Claude Desktop (`claude_desktop_config.json`; quit Claude Desktop completely before
editing, since it rewrites the file while running):

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/wiande00/garmin-mcp", "garmin-mcp"]
    }
  }
}
```

If Claude Desktop can't find `uvx`, use its full path (`which uvx`, or `where uvx` on
Windows). Other MCP clients take the same command and arguments. To pin a release, add
`@v0.1.0` to the git URL.

**3. Ask away.** Start with "check my Garmin status".

<details>
<summary>Running from a clone instead</summary>

```bash
git clone https://github.com/wiande00/garmin-mcp
cd garmin-mcp
uv sync
uv run garmin-mcp-auth login
claude mcp add --scope user garmin -- uv --directory "$PWD" run --quiet garmin-mcp
```

On Windows with the Microsoft Store edition of Claude Desktop,
`scripts\add-to-claude-desktop.cmd` registers the server for you: double-click it from
File Explorer, quit Claude Desktop from its tray icon, and it adds the entry and reopens
Claude.
</details>

## Tools

| Tool | What it covers |
|---|---|
| `garmin_status` | Login, token lifetimes, last watch sync, time zone, last error |
| `garmin_activities` | Activities by date range and type, newest first; `limit=0` for the count |
| `garmin_activity` | One activity: summary, laps, run/walk or interval splits, weather, HR and power zones, gear, strength sets, training effect, time series |
| `garmin_activity_analysis` | From the FIT file: climbs, grades, decoupling, power curve, NP/IF/TSS, R-R HRV, dynamics, Di2 |
| `garmin_fit_messages` | Every decoded FIT message, paged |
| `garmin_power_curve` | Best power for 5 s to 60 min across recent activities, with an FTP estimate |
| `garmin_wellness` | One day: summary, sleep, HRV, stress, Body Battery, heart rate, steps, SpO2, respiration, floors, hydration, intensity minutes, events, lifestyle |
| `garmin_training` | Readiness, recovery time, training status, load balance, fitness age, VO2max, acclimation, running tolerance, race predictions, lactate threshold, FTP |
| `garmin_trend` | A metric over a date range: sleep, HRV, resting HR, steps, stress, calories, Body Battery, training load (acute, chronic, ACWR, form), VO2max, endurance and hill score, weight, totals by sport |
| `garmin_body` | Weigh-ins, body composition, blood pressure |
| `garmin_profile` | Profile, settings, heart-rate and power zones |
| `garmin_devices` | Devices, last sync, settings, primary device, solar intake, alarms |
| `garmin_gear` | Shoes and bikes, with distance and default activity types |
| `garmin_achievements` | Goals, personal records, badges, challenges |
| `garmin_workouts` | Saved workouts as readable steps (or raw JSON) |
| `garmin_calendar` | Scheduled workouts, races and events, Garmin Coach plans |
| `garmin_nutrition` / `garmin_food_search` | Garmin Connect food log, goals, energy balance; food search |
| `garmin_courses` | Courses and their waypoints |
| `garmin_womens_health` | Menstrual cycle and pregnancy tracking |
| `garmin_reference` | Activity types, event types, strength-exercise catalog, workout format |
| `garmin_api_get` | Read-only GET of any Garmin Connect API path |
| `garmin_download` | Save an activity (FIT, GPX, TCX, KML, CSV), workout or course to disk |
| `garmin_create_workout` | Build a structured workout from simple steps and schedule it |
| `garmin_upload_workouts` / `garmin_schedule_workouts` | Upload Garmin workout JSON; schedule workouts (never twice on a day) |
| `garmin_update_activity` / `garmin_create_activity` | Rename, retype, describe, RPE, feel, gear; add manual activities |
| `garmin_add_weigh_in` / `garmin_add_body_composition` / `garmin_add_blood_pressure` / `garmin_add_hydration` | Log body data |
| `garmin_set_hr_zones` / `garmin_request_reload` | Heart-rate zones; reprocess a day |
| `garmin_set_nutrition_goals` / `garmin_save_custom_food` / `garmin_log_food` | Garmin Connect nutrition |
| `garmin_upload_course` | Create a course from a GPX file |
| `garmin_delete` | Delete weigh-ins, blood pressure, workouts, scheduled workouts, foods, courses; always previews first |

Five workout templates are also available as MCP resources (`workout://templates/...`).

### Building workouts

`garmin_create_workout` takes plain steps and emits Garmin's exact workout JSON:

```json
[
  {"type": "warmup", "duration": "10:00", "hr_zone": 2},
  {"repeat": 6, "steps": [
    {"type": "interval", "distance_km": 0.4, "pace_min_km": ["3:50", "4:05"]},
    {"type": "recovery", "duration": "1:30"}
  ]},
  {"type": "cooldown", "lap_button": true}
]
```

Each step ends one way (`duration`, `distance_km`, `reps`, `lap_button`) and has at most
one target (`hr_zone`, `hr_bpm`, `pace_min_km`, `speed_kmh`, `power_w`, `power_zone`,
`cadence`). Strength steps name an exercise from Garmin's catalog (`"exercise": "Barbell
Bench Press", "reps": 5, "weight_kg": 80`). Running, cycling, swimming, walking, hiking,
strength and more are supported.

## Configuration

All optional, as environment variables on the server entry:

| Variable | Default | Effect |
|---|---|---|
| `GARMIN_MCP_HOME` | `~/.garmin-mcp` | Where the tokens live; use one per account |
| `GARMIN_MCP_TIMEZONE` | the account's | IANA time zone for "today" and timestamps |
| `GARMIN_MCP_READ_ONLY` | off | `1` removes every tool that changes the account |
| `GARMIN_MCP_ENABLED_TOOLS` / `GARMIN_MCP_DISABLED_TOOLS` | all | Comma-separated allow / deny lists |
| `GARMIN_MCP_DOWNLOAD_DIR` | `~/Downloads/garmin-mcp` | Where downloads go |
| `GARMIN_MCP_HTTP` | `auto` | `curl_cffi` forces the Windows workaround, `requests` disables it |
| `GARMIN_MCP_CALL_TIMEOUT` | `90` | Seconds per call; `0` disables |
| `GARMIN_MCP_MAX_CHARS` | `40000` | Cap on answer length |
| `GARMIN_MCP_IS_CN` | off | `1` for garmin.cn accounts (untested) |

`garmin-mcp --transport streamable-http --port 8765` serves over HTTP on 127.0.0.1 instead
of stdio. That transport has no authentication, so it never listens beyond loopback.

## Troubleshooting

| Symptom | What to do |
|---|---|
| "Not logged in" / "session has expired" | Run `garmin-mcp-auth login` again in a terminal |
| 429 during login | Garmin is rate-limiting. The login command enforces a cooldown (15 min, 60 after a 429); wait it out rather than retrying |
| 403 on every call | Usually Cloudflare. It is retried through curl_cffi automatically; set `GARMIN_MCP_HTTP=curl_cffi`, and avoid VPNs and datacenter networks |
| 412 on a write | EU accounts must grant upload consent in Garmin Connect's privacy settings first |
| Training status, race predictions empty | A new watch needs a few weeks of activities first; the tools say so |
| Anything else | `garmin-mcp-auth status` tests the saved login; `garmin_status` shows the last error |

## How it works

```
src/garmin_mcp/
  server.py          the MCP server: tool names, parameters, descriptions, annotations
  cli.py             garmin-mcp-auth login | status | logout
  auth.py            tokens, the login and its cooldown, why a login doesn't work
  client.py          one Garmin client per process, error translation, cache
  transport.py       the Windows 403 fallback
  units.py           every Garmin unit quirk
  tools/             what each tool does, testable without MCP
    workout_dsl.py   the workout step language and checks for raw workout JSON
    fit.py           FIT decoding (Garmin FIT SDK) and analysis
```

- The server logs in lazily on the first call, so the MCP handshake never waits on Garmin.
- Calls run in a worker thread with a timeout, serialised so token refreshes stay safe.
- Range views use Garmin's range endpoints where they exist: a 90-day sleep or HRV trend
  is a few requests, not 90.
- Failures keep Garmin's status and text, and each kind gets its own message.
- `garmin_delete` deletes only a request previewed in the last 15 minutes.

## Development

```bash
uv sync
uv run pytest           # no network: a fake Garmin client and synthetic payloads
uv run ruff check
uv run python scripts/smoke.py   # read-only check of every tool against your real account
```

Issues and pull requests are welcome. For a bug, include the output of `garmin_status`
(it holds no secrets).

## Credits

- [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) by
  cyberjunky does the hard part: logging in and talking to Garmin Connect.
- [Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp) defined the feature set,
  and its issue tracker was the best guide to what goes wrong. Endpoints that
  python-garminconnect doesn't wrap follow its source.
- [Garmin FIT SDK](https://developer.garmin.com/fit/) decodes activity files.

## Disclaimer

This is an unofficial project, not affiliated with or endorsed by Garmin. It uses the
same private API as the Garmin Connect apps, which Garmin can change at any time, and
Garmin's terms of use don't cover third-party access. Use it with your own account, at
your own risk.

## License

[MIT](LICENSE)
