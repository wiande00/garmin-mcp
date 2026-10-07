# Changelog

## 0.1.0 - 2026-10-07

First release.

- 39 tools covering every feature of Taxuspt/garmin_mcp (see PARITY.md): activities,
  wellness, training, trends, body, devices, gear, achievements, workouts and calendar,
  nutrition, courses, women's health, downloads and deletes.
- `garmin-mcp-auth` logs in once with MFA; the server only reuses tokens. Failed logins
  start a cooldown.
- Windows 11 Cloudflare 403 fallback through curl_cffi.
- FIT analysis with Garmin's official FIT SDK: climbs, grades, decoupling, power curve,
  NP/IF/TSS, R-R HRV, running and cycling dynamics, Di2.
- A validated step language for structured workouts, and checks for raw workout JSON.
- Deletes always preview first; `GARMIN_MCP_READ_ONLY=1` removes every write.
- Unit conversions checked against Garmin Connect on a real account.
