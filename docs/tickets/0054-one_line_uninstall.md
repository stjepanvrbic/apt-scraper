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

- [ ] `uninstall.ps1` and `uninstall.sh` at the repository root, each runnable as a one-liner from GitHub the way the bootstrap scripts are: `irm https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/uninstall.ps1 | iex` and `curl -fsSL https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/uninstall.sh | bash`. They work without the clone present and finish without questions; nothing stops to ask, and the user is told at the start what will be removed.
- [ ] They remove: the running dashboard and any setup browser instance the profile started; the dashboard login entry and shortcut (`dashboard.vbs`, the Start menu shortcut, the LaunchAgent and the Applications entry); the Hermes profile `apt-scraper` with its cron jobs, Google token, connection results, setup browser profile and the profile's Python environment, through `hermes profile delete` where Hermes is present, then its directory and tombstone so a later install is a fresh one; apt-scraper's data directory (`%LOCALAPPDATA%\apt-scraper` or `~/Library/Application Support/apt-scraper`: the listing store, the clone and everything else there); and any apt-scraper entries in the Hermes host root that the installer created (the `default` profile's `delegation.model` is not one of them; check what else the installer writes outside the profile and remove exactly that).
- [ ] They leave Hermes itself, the Hermes gateway service and Claude Code in place by default, because they are general tools the user may use otherwise, and say at the end how to remove them: `hermes gateway uninstall` and Hermes's own uninstall, Claude Code's own uninstall. A `-Everything` / `--everything` argument also runs `hermes gateway uninstall` and Hermes's uninstall; Claude Code is never removed, because it holds the user's own login.
- [ ] A missing piece (no Hermes, no profile, no clone, no login entry) is skipped silently, so the line is safe to run twice.
- [ ] README's Install section gets an Uninstall subsection with both lines, in the same form as the install lines, and docs/spec.md Installation says what the uninstall removes and keeps.
- [ ] Tests in the existing installer style with the fake `hermes` and fake login entries: each item removed, missing items skipped, `--everything` runs the extra commands, Claude Code untouched.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

docs/spec.md, Installation and setup: one line installs; one line uninstalls.

## Design

Mirror the bootstrap scripts' shape (piped script, main called last, no stdin needed). Read install.ps1, install.sh, bootstrap.ps1, bootstrap.sh and profile/skills/gallery/scripts/store.py for every path the installer and the profile create; the uninstall is their inverse, and a test that lists the installer's created paths against the uninstaller's removed ones keeps them in step. Hermes 0.22.5: `hermes profile delete <name>` stops the profile's processes and removes its login items; it leaves a tombstone at `profiles/.deleted/<name>` that the uninstaller removes too, so the next install needs no special case.

Verification: fake hermes and a throwaway HOME / LOCALAPPDATA-shaped directory in tests only; never run the real uninstall on this machine, never touch the owner's Hermes root, profile, data directory or browser.
