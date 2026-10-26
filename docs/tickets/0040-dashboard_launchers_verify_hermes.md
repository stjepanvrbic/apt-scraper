---
title: The dashboard launchers verify they reached Hermes before opening the chat
type: Bug
state: Done
priority: 3
assignee:
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

The login launcher and the shortcut on both platforms open the chat only when Hermes's dashboard answers at the address the installer recorded, never a foreign listener.

## Acceptance

- [x] Both launchers probe `GET /api/health` and accept only a 200 whose JSON has `"ok": true` and a `version`; anything else starts Hermes or reports failure.
- [x] The installer writes the dashboard address it learned from Hermes's log to a file the launchers read, instead of a fixed port 9119. `tests/test_installers.py` covers a foreign 404 listener and a moved port.
- [x] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Installation and setup](../spec.md)

## Design

The installer's dashboard step writes the address Hermes reported to `dashboard-address` in the profile home, with no trailing newline. That address comes from `HERMES_DASHBOARD_READY port=N`, or from the `Managing profile` line of a dashboard that is already running. The launcher step now runs after the dashboard step, so the file exists before any launcher reads it. `hermes profile install --force` leaves files the profile does not ship alone, so a re-run keeps it. The launchers start the dashboard with `--port <recorded port>`. Hermes attaches to a running owner on that port and exits 0, and it refuses a different port with exit 78 (`_endpoint_conflict` in `hermes_cli/main_dashboard.py`). So the start and the probe always point at the same address.

## Deviations

- The macOS LaunchAgent does not read the file. Its `ProgramArguments` carry `--port <recorded port>`, which the installer writes in the same run as the file. Hermes 0.22.5 finds a launchd dashboard job only when its arguments start with `hermes` (`_loaded_launchd_backend_jobs` and `_parse_dashboard_runtime` in `hermes_cli/main_dashboard.py`), and `hermes update` restarts the dashboard through that job. A `/bin/sh -c` wrapper that read the file hid the job, and the review found it. `dashboard.vbs`, the macOS app and the skill read the file.
- The installers now run "Hermes web dashboard" before "Dashboard at login and the apt-scraper shortcut", so the address exists before the launchers that need it.

## Result

`dashboard.vbs` on Windows and `~/Applications/apt-scraper.app` on macOS read `dashboard-address`. They probe `<address>/api/health` and treat the dashboard as running only on a 200 whose JSON has `"ok": true` and a `version`. Otherwise they start it on the recorded port, and with `/open`, or from the app, they wait up to two minutes. If Hermes still does not answer, they report the address and the `hermes -p apt-scraper dashboard --port N` command to run. VBScript has no JSON parser, so `dashboard.vbs` matches a pattern. The app uses `curl` and `grep`. The account-connections skill's Channels-page fallback reads the same file and runs the same check with `curl`. It no longer reads the port from `hermes dashboard --status`.

The response shape is from Hermes 0.22.5 (`bf0203a794`). `get_health` in `hermes_cli/web_routers/status.py` returns `{"ok": True, "version", "displayVersion", "commit", "auth_required"}`, and `/api/health` is in `PUBLIC_API_PATHS` (`hermes_cli/dashboard_auth/public_paths.py`). Called in-process with FastAPI's `TestClient` under a throwaway `HERMES_HOME`, it returned `200 {"ok":true,"version":"0.22.5",…}`.

Verified on Windows at `15aeda7`. The owner's Startup folder, Start menu, LaunchAgents and Hermes profiles were not touched, no dashboard or gateway was started and no browser opened:

- `tests/test_installers.py`, 15 tests against a fake `hermes` in temporary `APPDATA`, `HOME` and profile directories. Both installers' dashboard steps record `http://127.0.0.1:9119` for a fresh start and `http://127.0.0.1:9200` for a dashboard already running on a moved port, and they record nothing when the start fails. The launchers use a recorded port other than 9119. Both launchers open the chat of Hermes at that address without starting another, and start a stopped dashboard with `--port`. With a foreign listener on the port, answering 404 or a 200 without `version`, they start Hermes and report that it did not answer, opening nothing. The listeners refuse a shared port, because Windows otherwise lets a second socket bind it. The LaunchAgent test checks `hermes` first and the full command with `--port`.
- Rehearsal against Hermes's real app, served by uvicorn on a free loopback port with its lifespan off, under a throwaway `HERMES_HOME`. The `HermesAnswers` function of `dashboard.vbs` under `cscript`, the app's `hermes_answers` under Git Bash, and the skill's `curl` command all accepted the reply. Hermes's `_parse_dashboard_runtime` reads the generated LaunchAgent as `('dashboard', '127.0.0.1', 9200)`. A `/bin/sh -c` wrapper reads as `None`.
- `python -m unittest discover -s tests`: 510 passed, 0 skipped. `ruff check .` passed. PowerShell parser on `install.ps1`: 0 errors. `bash -n install.sh` passes. Hermes's skill linter: 0 findings on `account-connections`. Its skills guard reports the same five findings as on `main`, with the LaunchAgent line moved from 153 to 160.
- Review of `8a08139` found one medium and two low issues. The medium one was the LaunchAgent hidden from `hermes update`, fixed in `15aeda7`. One low was a bash 3.2 quoting construct in the app, also fixed in `15aeda7`. The other low, the claim commit's subject, was declined, because implementing commits carry the ticket title. The reassessment of `15aeda7` found all resolved and nothing new.
- Not run: anything on macOS or under bash 3.2, a real login, and the real `hermes dashboard` started from a launcher.

Tech debt fixed: the tests' fake dashboard served directory listings, so it would have passed any launcher that accepted every HTTP answer. It now answers `/api/health` the way Hermes does.

Found, not fixed here, also true on `main`: Hermes never moves off a busy port. It exits 75 (`PORT_IN_USE_EXIT_CODE` in `hermes_cli/web_server_lifecycle.py`). So a foreign program on the recorded port blocks the dashboard, the installer cannot choose another port, and the shortcut reports this only after its two-minute wait.

Delivered on `fix/launcher_health` in [PR #34](https://github.com/stjepanvrbic/apt-scraper/pull/34), open against `main`, not merged. Spec decisions checked: none have ADR identifiers.

## Comments

- 2026-10-25: Found by the deep review: `install.ps1` and `install.sh` launchers treat any HTTP response on port 9119 as readiness; a foreign 404 opened the chat path without Hermes.

- 2026-10-25: Known limit, not fixed: Hermes exits 75 on a busy port instead of moving, so a foreign program on the recorded port blocks the dashboard until it is freed.
