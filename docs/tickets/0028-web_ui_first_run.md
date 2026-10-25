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
- [x] `README.md` tells the user to use the dashboard, with the command to reopen it, and no longer leads with `hermes -p apt-scraper` in a terminal.
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

Tech debt fixed: the skill's gateway commands used `-p apt-scraper`, which Hermes 0.22.5 refuses for a profile served by the host gateway. The skill also told the user to quit and reopen the terminal to pick up the iMessage grant, which does not restart the gateway the agent runs in.

Delivered on `feat/dashboard_first_run` in [PR #26](https://github.com/stjepanvrbic/apt-scraper/pull/26), open against `main`, not merged. Spec decisions checked: none have ADR identifiers.

## Comments
