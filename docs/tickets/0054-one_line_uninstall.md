---
title: One line uninstalls apt-scraper completely
type: Feature
state: In Progress
priority: 1
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: [0027, 0049]
---

## Outcome

One line pasted into a terminal, on Windows and on macOS, removes everything apt-scraper put on the machine, and the README shows the uninstall lines the same way it shows the install lines.

## Acceptance

- [x] `uninstall.ps1` and `uninstall.sh` at the repository root, each runnable as a one-liner from GitHub the way the bootstrap scripts are: `irm https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/uninstall.ps1 | iex` and `curl -fsSL https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/uninstall.sh | bash`. They work without the clone present and finish without questions; nothing stops to ask, and the user is told at the start what will be removed.
- [x] They remove: the running dashboard and any setup browser instance the profile started; the dashboard login entry and shortcut (`dashboard.vbs`, the Start menu shortcut, the LaunchAgent and the Applications entry); the Hermes profile `apt-scraper` with its cron jobs, Google token, connection results, setup browser profile and the profile's Python environment, through `hermes profile delete` where Hermes is present, then its directory and tombstone so a later install is a fresh one; apt-scraper's data directory (`%LOCALAPPDATA%\apt-scraper` or `~/Library/Application Support/apt-scraper`: the listing store, the clone and everything else there); and any apt-scraper entries in the Hermes host root that the installer created (the `default` profile's `delegation.model` is not one of them; check what else the installer writes outside the profile and remove exactly that).
- [x] They leave Hermes itself, the Hermes gateway service and Claude Code in place by default, because they are general tools the user may use otherwise, and say at the end how to remove them: `hermes gateway uninstall` and Hermes's own uninstall, Claude Code's own uninstall. A `-Everything` / `--everything` argument also runs `hermes gateway uninstall` and Hermes's uninstall; Claude Code is never removed, because it holds the user's own login.
- [x] A missing piece (no Hermes, no profile, no clone, no login entry) is skipped silently, so the line is safe to run twice.
- [x] README's Install section gets an Uninstall subsection with both lines, in the same form as the install lines, and docs/spec.md Installation says what the uninstall removes and keeps.
- [x] Tests in the existing installer style with the fake `hermes` and fake login entries: each item removed, missing items skipped, `--everything` runs the extra commands, Claude Code untouched.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

docs/spec.md, Installation and setup: one line installs; one line uninstalls.

## Design

Mirror the bootstrap scripts' shape (piped script, main called last, no stdin needed). Read install.ps1, install.sh, bootstrap.ps1, bootstrap.sh and profile/skills/gallery/scripts/store.py for every path the installer and the profile create; the uninstall is their inverse, and a test that lists the installer's created paths against the uninstaller's removed ones keeps them in step. Hermes 0.22.5: `hermes profile delete <name>` stops the profile's processes and removes its login items; it leaves a tombstone at `profiles/.deleted/<name>` that the uninstaller removes too, so the next install needs no special case.

Verification: fake hermes and a throwaway HOME / LOCALAPPDATA-shaped directory in tests only; never run the real uninstall on this machine, never touch the owner's Hermes root, profile, data directory or browser.

## Deviations

- The installers' dashboard log is now named `apt-scraper-dashboard-*` (`%TEMP%` on Windows, `$TMPDIR` on macOS) instead of `hermes-dashboard-*.log` and a bare `mktemp` name, so the uninstaller removes exactly the installer's file. Logs of earlier installs keep their old names in the temporary folder, because those names are not apt-scraper's alone.
- `-Everything` runs `hermes uninstall --yes`, which removes Hermes's code and keeps its home (`%LOCALAPPDATA%\hermes`, `~/.hermes`): its settings, other profiles and sessions are not apt-scraper's. `--full` would delete them; the line ends by saying where they stay.
- On Windows the gateway's Scheduled Task was added as administrator, and only an administrator removes it. With `-Everything` and `gateway status` reporting the task, `gateway uninstall` runs elevated through one UAC prompt, hidden, as the installer runs `gateway install`; without the task it runs unelevated. A declined prompt stops the line before Hermes's uninstall, with apt-scraper already removed and the instruction to run it again. Every Hermes command runs with `HERMES_NONINTERACTIVE=1`, so `gateway uninstall`'s own UAC question cannot appear, and `profile delete` gets `--yes`.
- Only what apt-scraper starts is stopped, matched argument by argument: the setup browser by `--user-data-dir=<profile>/apt-scraper/browser` (0053) or one starting with `--user-data-dir=<profile>/browser-profile/` (Hermes's copy under 0052); wscript or cscript running `<profile>/dashboard.vbs`; the gallery server, `gallery.py` from the profile's skill or the clone's with `serve`, because on Windows its open store would keep the data folder from being removed; and hermes whose executable or script is under this Hermes root, with `-p`/`--profile apt-scraper`, on both platforms. A process that merely names a path in the profile, such as an editor on `config.yaml`, keeps running. On macOS `ps` joins the arguments with spaces, so each is matched between spaces, and a hermes outside `~/.hermes` is not stopped there; booting out the LaunchAgent stops the one launchd runs, and `hermes profile delete` stops whatever else Hermes counts as the profile's backend.
- The computer-use driver is Hermes's (Hermes PM prepares it, and the profile enables no autostart), so it stays with Hermes. Nothing else the installer writes lands in the Hermes host root: the profile is created with `--no-alias`, and its skills, plugin, cron jobs, search environment and dashboard address live in the profile.
- `profile/` is not edited, since another pull request changed `connections.py`. AGENTS.md gains one sentence on the uninstall scripts.
- Delivery is an open pull request, not a merge, as requested. The ticket stays In Progress.

## Result

`uninstall.ps1` and `uninstall.sh` are written for the one line, in the bootstrap scripts' shape: the PowerShell one in a script block that throws instead of exiting and leaves the caller's session as it was, the shell one in `main`, called on the last line. They take their roots from `LOCALAPPDATA` and `APPDATA` or `HOME`, as the installers do, and print what they remove before the first step. In order, they:

1. remove the dashboard's login entry and the apt-scraper shortcut: the two Start menu shortcuts on Windows; the LaunchAgent, booted out first, and `~/Applications/apt-scraper.app` on macOS. `dashboard.vbs` goes with the profile,
2. stop the dashboard, the gallery server and the setup browser, as described in Deviations,
3. run `hermes profile delete apt-scraper --yes` when Hermes is present and the profile is live, then remove what is left of the profile directory and its mark in `profiles/.deleted`,
4. remove the data folder (`%LOCALAPPDATA%\apt-scraper`, `~/Library/Application Support/apt-scraper`) with the listing store, the sample store and the clone, the installer's dashboard log, and `.secrets\apt-scraper` in the user folder or `~/.secrets/apt-scraper`, where the gallery saves its address and access token each time it opens (`launch.py`), keeping the rest of `.secrets`,
5. with `-Everything` / `--everything`, run `hermes -p default gateway uninstall` and `hermes uninstall --yes`.

They end with `hermes gateway uninstall`, `hermes uninstall`, the `-Everything` line and Claude Code's own uninstall (https://code.claude.com/docs/en/setup#uninstall-claude-code and its native-install commands). Claude Code is never run or removed. README's Install section has an Uninstall subsection with both lines and both `-Everything` lines; Spec › Installation and setup says what the uninstall removes and keeps.

Verified on Windows at 4f77053, after the first review:

- `tests/test_installers.py` gains `UninstallTests`, 12 tests that run each uninstaller as its one line runs it, PowerShell through `Invoke-Expression` and bash from standard input, against a fake `hermes` with Hermes 0.22.5's `profile delete` (asks without `--yes`, refuses a deleted name, leaves the mark), fake login entries, a fake `launchctl`, a fake `ps` and a fake `claude`, on a throwaway machine directory with `LOCALAPPDATA`, `APPDATA`, `TEMP`, `USERPROFILE` or `HOME` and `TMPDIR` set for that run only, and a PATH of the fakes and System32 or Git Bash's tools, so no real `hermes` is reachable. The gallery connection is saved through `launch.save_connection` with the home on that machine, checked first to be inside it. They cover: everything removed through `profile delete`, with only Hermes's, Claude Code's and the rest of `.secrets` left; a second run that calls nothing; the removal without Hermes; a deleted profile's leftover and mark; `--everything` on macOS and on Windows with and without the Scheduled Task; a declined UAC prompt; the caller's session left as it was; and on each platform the setup browser, Hermes's earlier browser copy, the dashboard launcher (cscript on Windows), the gallery server and hermes with the profile stopped, while bystanders naming the profile's paths, a gallery not serving, another profile and another Hermes keep running. Three tests keep the uninstallers in step with what is created: one runs the bootstrap with a fake git, the installer's own location lines, profile step, dashboard step and login step, the gallery store's `store_path` and `launch.save_connection`, then the uninstaller, and checks the machine is back to what Hermes and Claude Code keep; the others list every line of the installers and bootstraps, and of the profile's scripts, that names a location outside the profile, with the uninstaller line that removes it or why none does. Against the first review's uninstallers, 15 checks fail.
- `python -m pytest tests -q` from Git Bash: 655 passed, 317 subtests passed. A first run under load failed 2 of the rerank's CPU-time budget tests (`test_implicit_signals.py`), which this change does not touch; the two runs after it passed. `ruff check .` passed. The PowerShell parser reports 0 errors on `uninstall.ps1`, `install.ps1` and `bootstrap.ps1`, and `bash -n` passes on `uninstall.sh`, `install.sh` and `bootstrap.sh`.

Not run: the real uninstall, a real `hermes profile delete`, `gateway uninstall` or `hermes uninstall` on this machine, and anything on macOS.

## Comments

- 2026-10-27: The first review of PR #48 blocked on two findings. The gallery's saved connections in `~/.secrets/apt-scraper/gallery` were left behind: the uninstallers now remove `~/.secrets/apt-scraper`, README, spec and the upfront list name it, and a scan of the profile's scripts joins the installers' one. Processes were matched by any mention of the profile's path, and on macOS hermes from anywhere: they are now matched as Deviations describes, with bystanders in the tests on both platforms. `main` with PR #47 is merged in.
