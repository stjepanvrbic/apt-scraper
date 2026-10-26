---
title: The installer ends in the Hermes web dashboard, where the agent runs the setup
type: Feature
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: [0005, 0006, 0007, 0018]
---

## Outcome

After `install`, the user is in the Hermes web dashboard's chat for the `apt-scraper` profile, not a terminal, and the agent itself runs the account connections (Gmail, Calendar, the messaging gateway) and the interview from there. The user signs in and clicks consent screens; the agent does everything else.

## Acceptance

- [x] `install.ps1` and `install.sh` finish by starting the Hermes web dashboard for the profile and opening it in the user's browser. The dashboard's web build is produced by the installer, so a later `hermes -p apt-scraper dashboard` needs no npm.
- [x] `README.md` tells the user to use the dashboard, with the shortcut to reopen it, and no longer leads with `hermes -p apt-scraper` in a terminal.
- [x] The dashboard starts without a window at login, and an apt-scraper shortcut (Start menu on Windows, Applications on macOS) opens its chat, starting the dashboard first when it is not running. Reopening the dashboard after a restart needs no terminal.
- [ ] The first conversation in the dashboard offers the connections and the interview as `profile/SOUL.md` says; the agent runs every command and opens every page of the Google and gateway setup itself and asks the user only to sign in and approve. Verified on a throwaway profile with a headless browser against the dashboard, with screenshots in the pull request.
- [x] The account-connections skill works when the user is in the dashboard rather than a terminal: nothing it does assumes a TTY, and `hermes gateway setup`'s interactive wizard is driven by the agent, not the user.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Installer](../spec.md), [Spec › Setup](../spec.md)

## Design

- `hermes -p <profile> dashboard` routes to one machine dashboard with the profile preselected, serves on 127.0.0.1:9119 and has a Chat tab. `--skip-build` serves an existing `web/dist`.
- Agents verifying this never open the owner's browser: the dashboard is started with `--no-open` and read headlessly.

## Deviations

- The agent does not drive `hermes gateway setup`. In Hermes 0.22.5 every chat turn runs inside the one host gateway, and the wizard needs a terminal. The agent writes `TELEGRAM_ALLOWED_USERS` and `TELEGRAM_HOME_CHANNEL` with `hermes config set`, opens the dashboard's Channels page, and the user pastes the token into its Manual setup form. Saving connects the bot in the running gateway without a restart.
- The installers also install and start the host gateway service (`hermes -p default gateway install --if-missing --start-now --start-on-login`). The wizard used to do this, and the agent cannot do it safely from inside the gateway. Hermes refuses gateway services for named profiles, so the skill's gateway commands now use `-p default`.
- The installers install Hermes's built-in `google-workspace` skill into the profile. `hermes profile install` from a directory does not copy it, so on a fresh install the Google setup stopped at "The Hermes google-workspace skill is missing".
- The macOS iMessage step restarts the gateway with a background job that retries every 30 seconds until no turn or scheduled job runs, because a restart during the agent's turn ends the turn and stalls the dashboard chat. The second iMessage check from the bot chat is gone: with the gateway service running, dashboard and Telegram turns run in the same gateway context.
- Delivery is an open pull request, not a merge, as requested. The ticket stays In Progress.

## Result

The installers end by starting `hermes -p apt-scraper dashboard --no-open` in the background. They wait for its ready line, or for the address a running dashboard prints, and open `/chat?profile=apt-scraper`. The first start builds the web UI. Hermes rebuilds it only when its sources change, so a later start needs no npm. Before that, the installers install the `google-workspace` skill and the host gateway service. The account-connections skill takes the Telegram token through the dashboard's Channels form and never restarts the gateway during a turn. README, `docs/gallery.md` and Spec › Installation and setup now name the dashboard.

Verified on Windows with Hermes 0.22.5, in throwaway `HERMES_HOME` roots that were deleted afterwards. The agent's `cmd` and `open` were stubbed to a log and computer use was off:

- Both installers' dashboard steps were extracted and run for three cases: fresh start, a dashboard already running, and a failing start. `tests/test_installers.py` repeats this against a fake `hermes`.
- First "Hello" in the dashboard chat: the agent ran the connection status, reported the unconnected accounts and started the interview.
- Connection setup: the agent ran the Google check, opened the Cloud Console and stopped at sign-in. On the Telegram path it opened BotFather, the bot chat and userinfobot, wrote both IDs with `config set`, found the dashboard's port and opened the Channels page.
- After a fake token was saved in that form, `check gateway` reached Telegram and reported "Unauthorized". Saving during a live turn left the turn running.
- The deferred gateway restart ran after the turn and changed the gateway PID, and the reloaded chat answered in the same session.
- `python -m unittest discover -s tests`: 408 passed, 0 skipped. `ruff check .` passed. Hermes's skill linter and skills guard report 0 findings on `account-connections`. The PowerShell parser reports 0 errors, and `bash -n install.sh` passes.
- An independent review of `7bc84eb` found one high, four medium and four low issues. All are fixed in `95d77d6` and `a392db0` except two deliberate low ones: a timed-out dashboard is left running, and a missing `xdg-open` fails the Linux install. The reassessment found two new risks in the restart step, both fixed in `a392db0`.

The acceptance item on the first conversation stays open. Real Google and Telegram sign-in were not possible, and computer use was off, so the agent asked the user to click where it would otherwise click itself. Also not run: the installer on a fresh machine (first web build, gateway service install), and anything on macOS.

Review of `21537d1` found that after a restart the dashboard came back only from a terminal kept open. Hermes 0.22.5 has no way to start the dashboard at login, so the installers now do it the way Hermes starts its own gateway:

- Windows: the installer writes `dashboard.vbs` into the profile home. It starts `hermes -p apt-scraper dashboard --no-open --skip-build` with its console hidden when `http://127.0.0.1:9119/` does not answer. With `/open` it also waits up to two minutes for the dashboard and opens `/chat?profile=apt-scraper`, or shows a message naming the terminal command that shows the error. `apt-scraper dashboard.lnk` in the Startup folder and `apt-scraper.lnk` in the Start menu run it through `wscript.exe`, which has no console.
- macOS: the LaunchAgent `io.github.stjepanvrbic.apt-scraper.dashboard` runs the same command with `RunAtLoad`. `~/Applications/apt-scraper.app` checks the same address, starts a stopped dashboard with `launchctl bootstrap` or, when the agent is loaded already, `launchctl kickstart`, then opens the chat. launchd owns the dashboard, so `hermes update` restarts it through the job.
- `--skip-build` is safe at login because `hermes update` rebuilds the web UI when its sources change and the installer's first start builds it.
- Every installer run rewrites the launcher, both shortcuts, the LaunchAgent and the app. README, both installers' closing lines and Spec › Installation and setup point to the shortcut. The account-connections skill's fallback starts the dashboard the way login does, with `cscript` on Windows so a missing file is reported instead of shown in a dialog.

Verified on Windows against a fake `hermes`, in temporary `APPDATA` and `HOME` directories. The owner's Startup folder, Start menu and Hermes profiles were not touched and no browser opened:

- `tests/test_installers.py` runs the Windows step twice and checks both shortcuts. It then runs each shortcut's own arguments under `cscript` for four cases: the login entry starts the dashboard and opens nothing, the shortcut starts a stopped dashboard and opens its chat, it opens a running dashboard's chat without starting another, and it reports a dashboard that does not start. The macOS step runs twice under Git Bash with fake `launchctl`, `open` and `osascript`. The tests check the LaunchAgent and the app bundle's plist, then the same cases for the app, including `kickstart` after a failed `bootstrap`.
- `python -m unittest discover -s tests`, run four times: 416 tests, 0 skipped. Two runs passed. Each of the other two failed one test, the 2-second rerank timing test, at 2.24 and 2.33 seconds. Ticket `0030` on `main`, "The rerank timing test fails on a busy machine", covers that test failing under load. The 13 installer tests passed on every run. `ruff check .` passed. PowerShell parser: 0 errors. `bash -n install.sh` passes. Hermes's skill linter reports 0 findings on `account-connections`. Its skills guard adds one informational medium finding, "macOS launch agent/daemon persistence", for the LaunchAgent the fallback starts. The verdict stays caution, which `connections.py` already causes.
- Not run: a real login on either platform, the real `hermes` started from the launcher, and anything on macOS.

Tech debt fixed: the parity test's shell step pattern did not match indented steps, so its filter for the macOS-only permission step had no effect. The skill's gateway commands used `-p apt-scraper`, which Hermes 0.22.5 refuses for a profile served by the host gateway. The skill also told the user to quit and reopen the terminal to pick up the iMessage grant, which does not restart the gateway the agent runs in.

Delivered on `feat/dashboard_first_run` in [PR #26](https://github.com/stjepanvrbic/apt-scraper/pull/26), open against `main`, not merged. Spec decisions checked: none have ADR identifiers.

## Comments
