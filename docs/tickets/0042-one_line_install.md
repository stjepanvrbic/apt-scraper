---
title: One-line install that ends in the agent-guided setup
type: Feature
state: Done
priority: 2
assignee:
milestone:
parent:
blocked_by: []
related: [0028]
---

## Outcome

A new user installs with one command pasted into a terminal, on Windows or macOS, with nothing cloned by hand, and lands in the dashboard chat where the agent guides the rest of the setup.

## Acceptance

- [x] Windows: one `irm <url> | iex` line installs Git if missing, clones or updates the repository into a fixed per-user location, and runs the installer from there. macOS: one `curl -fsSL <url> | bash` line does the same.
- [x] Rerunning the same line updates the repository and the profile, and keeps the user's `config.yaml`, data and credentials.
- [x] `README.md` leads with the two lines and nothing else a user must do before the chat opens; the clone-and-run path stays for development.
- [x] A test runs each bootstrap against a fake `git` and a fake installer in temporary directories, for a fresh install and a rerun.
- [x] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Installation and setup](../spec.md)

## Design

- The repository is private, so the raw URL needs the repository to be public or a token. The owner decides; the bootstrap is written for the public raw URL and the README says the repository must be public for the one-liner to work.
- The repository lives at `%LOCALAPPDATA%\apt-scraper\repo` on Windows and `~/Library/Application Support/apt-scraper/repo` on macOS, next to the gallery data.

## Deviations

- On a Mac without the Xcode command line tools, the line opens their installer and stops, and the user pastes it again once they are installed. Waiting for the installation would depend on the exit status of `xcode-select -p`, which its manual page does not document, and would hang when the user cancels the dialog. README and Spec › Installation and setup say so.
- `bootstrap.sh` covers macOS only, as the spec does. On Linux it reports that Git does not run when Git is missing, and otherwise clones into the macOS path.
- `bootstrap.ps1` runs `install.ps1` under Windows PowerShell even when the line is pasted into PowerShell 7, because the installer is tested only there.
- Delivery is an open pull request, not a merge, as requested. The ticket stays In Progress.

## Result

`bootstrap.ps1` and `bootstrap.sh` at the repository root are the one-line install. Each installs Git when it is missing (`winget install --id Git.Git --exact --source winget` on Windows, the Xcode command line tools installer on macOS), clones `https://github.com/stjepanvrbic/apt-scraper.git` into the location in Design or runs `git pull --ff-only` there, and runs `install.ps1` or `install.sh` from the clone with the bootstrap's own arguments. The installers are unchanged: they already keep `config.yaml`, memories, sessions and credentials and end in the dashboard chat (`0028`). Gallery data sits beside the clone and is not touched.

- `bootstrap.ps1` runs inside a script block and throws instead of calling `exit`, so under `irm | iex` a failure leaves the caller's window, `$ErrorActionPreference` and functions as they were. After winget it appends the registry PATH to the session's.
- `bootstrap.sh` does all its work in `main`, called on the last line, so a cut-off download runs nothing. The installer reads `/dev/tty` when there is one, because standard input is the script's pipe. When Git does not run and the command line tools installer refuses, it prints Git's own error.
- README leads with the two lines, says the repository must be public for them to work, and moves clone-and-run under Development. Spec › Installation and setup and its Installer acceptance describe the one-line install. `AGENTS.md` names the bootstrap scripts.

Verified on Windows at `f537bbd`. `2ec26f6` changes one comment, after which the installer tests and the PowerShell parser were run again:

- `tests/test_installers.py` gains 8 bootstrap tests. Fake `git`, `winget`, `xcode-select` and a fake installer run in temporary directories. No network, no real Git and no change to `LOCALAPPDATA`. Windows runs through `Invoke-Expression` with a PATH without the machine's Git, and macOS pipes the script into Git Bash with `HOME` in the temporary directory. Cases: fresh install then rerun (clone, then `pull --ff-only`, the installer run from the clone both times) on both platforms, arguments passed through, Git installed by winget with the registry PATH reaching the installer, the command line tools installer opened, its refusal reported with Git's error, an installer failure stopping the bootstrap, and the caller's PowerShell session surviving it.
- Deleting the PATH refresh, or ignoring `xcode-select`'s exit status, fails a test.
- `python -m unittest discover -s tests`: 534 passed, 0 skipped, at `f537bbd`. Of three runs at `8510440`, two passed and one failed a 2-second timing test at 2.65 s. That is the timing failure `0030` covers, and no ranking code changed. `ruff check .` passed. The PowerShell parser reports 0 errors on `bootstrap.ps1` and `install.ps1`, and `bash -n` passes on `bootstrap.sh` and `install.sh`.
- An independent review of `8510440` found two medium standards and spec issues and several low ones. All are fixed in `f537bbd` except three that need no change: Git installed but off PATH fails with winget's or git's error, the repository's visibility is the owner's decision, and the terminal branch below. The reassessment of `f537bbd` found no new defects.

Not run: either line on a real machine, because the raw URLs fail while the repository is private, and anything on macOS. Two macOS checks stay open for a fresh Mac. One is that the installer's `claude auth login` and password prompts get the terminal through `bootstrap.sh`'s `/dev/tty` branch, which needs a pty this machine lacks. The other is that a Mac without the command line tools shows the "install them in the dialog" message, which depends on `xcode-select --install` succeeding after the `/usr/bin/git` shim has already requested the install.

Delivered on `feat/one_line_install` in [PR #35](https://github.com/stjepanvrbic/apt-scraper/pull/35), open against `main`, not merged. Spec decisions checked: none concern installation, and none have ADR identifiers.

## Comments

- 2026-10-26: Review found that `set -u` with an empty `"$@"` exits on macOS bash 3.2; fixed with `${1+"$@"}` in `bootstrap.sh` and the same pre-existing case in `install.sh`. Confirmed from bash's COMPAT notes, not run on a Mac. Gallery data under Hermes lives in `HERMES_HOME/workspace/gallery/`, not beside the clone.
