---
title: The repository is the Hermes profile, and the install is a few Hermes commands
type: Chore
state: In Progress
priority: 1
assignee: agent
milestone:
parent:
blocked_by: []
related: [0028, 0040, 0054, 0056, 0057, 0058]
---

## Outcome

apt-scraper is a Hermes profile and nothing more. The user installs it with one line that runs Hermes's own installer, Claude Code's own installer and a handful of Hermes commands, and opens the chat with `hermes -p apt-scraper dashboard`. Nothing in the repository is specific to Windows or macOS beyond what the agent inherently does on each (the browser start and the macOS permission dialogs), and nothing hand-rolls what Hermes already does.

## Acceptance

- [x] The profile's files are at the repository root, and `distribution.yaml` names what `hermes profile install` copies, so `hermes profile install github.com/stjepanvrbic/apt-scraper` installs the profile straight from GitHub and `hermes profile update` re-pulls it.
- [x] `install.sh` and `install.ps1` are each a short list of commands with no logic of their own: Hermes's installer, Claude Code's installer and login, `hermes profile install`, the subscription plugin, the Google Workspace skill, the computer-use driver, the scheduled jobs and the gateway service.
- [x] The bootstraps, the uninstallers, the dashboard launchers (`dashboard.vbs`, the LaunchAgent and the app bundle), the Start menu and login entries, their tests and the Windows Sandbox scripts are deleted. Uninstall is `hermes profile delete` plus two folders, documented in README.
- [x] Every test runs on every platform; `tests/skips.py` fails any skip.
- [ ] CI is green on Linux, macOS and Windows.
- [x] README, the spec, the agent instructions and the skills say the new install, open and uninstall, and nothing mentions the deleted machinery.

## Spec

docs/spec.md, Installation and setup.

## Design

- **Why.** Tickets 0028, 0040, 0054, 0056 and 0057 each added machinery around the install: a bootstrap that clones the repository, installers of 400 lines each with a Windows and a macOS copy, dashboard launchers that start Hermes's dashboard at login through a VBScript and a LaunchAgent, uninstallers that undo all of it, 2,250 lines of tests of all of it, and a Windows Sandbox to run those tests in. Their failures on fresh machines (ticket 0057) were failures of that machinery, not of the agent. Hermes already installs itself, installs a profile from a git URL, keeps the user's `config.yaml` on update, installs its gateway as a login service on each platform, deletes a profile, and serves its dashboard on demand.
- **Layout.** `profile/` moved to the root: `SOUL.md`, `config.yaml`, `cron/`, `scripts/`, `skills/`, `distribution.yaml`. The manifest's `distribution_owned` allowlist keeps `docs/`, `tests/`, `gallery/` and the install scripts out of the installed profile.
- **Install scripts.** Two files of about twenty lines that only call installers and `hermes`. The macOS line is `bash <(curl ...)`, so the script's standard input stays the terminal and `claude auth login` reads the keyboard without the terminal-device workaround of ticket 0056. The Windows line is `iex (irm ...)` in the user's own PowerShell.
- **Setup.** The account connections and the interview were already the agent's job on first chat and stay so. The scheduled jobs' Python environments are built by their own first run, as before.
- **Checks.** `tests/run.sh` and `tests/run.ps1` lose the installer prerequisites and the sandbox guard; CI's three jobs stay the one place test runs happen.

## Deviations

- The cron jobs are resumed by the install line, not by the agent, because `hermes profile install` ships them paused and the agent should not be the one deciding to start them.

## Result
