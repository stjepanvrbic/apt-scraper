---
title: The installer installs the profile under a name that was deleted before
type: Bug
state: Done
priority: 1
assignee: agent
milestone:
parent:
blocked_by: []
related: [0028, 0042]
---

## Outcome

Running the installer after `hermes profile delete apt-scraper` installs the profile again and ends in the dashboard chat, and a rerun over an installed profile keeps the user's `config.yaml` while a first install over a leftover gets the distribution's.

## Acceptance

- [x] On a Hermes root where `apt-scraper` was deleted (a tombstone at `profiles/.deleted/apt-scraper`), both installers install the profile without error. Verified against the real `hermes` on a throwaway `HERMES_HOME` on Windows.
- [x] `config.yaml` is kept across a rerun only when the profile was installed from this distribution before (`distribution.yaml` present); otherwise the distribution's `config.yaml` is installed.
- [x] A half-copied profile directory from a failed install does not stop the next run.
- [x] `tests/test_installers.py` covers the deleted-name case and the config rule against the fake `hermes`.
- [x] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Installation and setup](../spec.md)

## Design

- Hermes 0.22.5: `hermes profile delete` writes a tombstone; `create_profile` (`hermes_cli/profiles.py`) calls `clear_named_profile_deleted` before building, but `install_distribution` (`hermes_cli/profile_distribution.py`) does not, so `write_manifest` fails in `assert_named_profile_home_live` with "Named profile home does not exist" after the payload was already copied.
- The installer creates the profile with `hermes profile create` when it is missing or tombstoned, then installs the distribution with `--force`, which takes `config.yaml` from the distribution.

## Deviations

- Whether a profile is deleted is decided by its mark in `profiles/.deleted`, not by a missing `distribution.yaml`. The leftover of the earlier installer's failed run has a `distribution.yaml`: with no `distribution_owned` list Hermes copies the payload's own manifest along with every other file, and fails only when it rewrites that manifest at the end.
- A name marked deleted that still has a directory is removed before `profile create`, because `create_profile` refuses a directory that holds `config.yaml` ("A profile named 'apt-scraper' already exists").
- A live directory without `distribution.yaml`, on a name that was never deleted, is not created again. `create_profile` refuses an existing directory, and Hermes never removes a live directory that may hold user files. `profile install --force` installs into it, as it already could, with the distribution's `config.yaml`, and the rest of the directory stays.
- `profile create` runs with `--no-alias`, because the installers never created the `apt-scraper` wrapper in `~/.local/bin`. It seeds Hermes's bundled skills, as `hermes update` does for every profile.
- `hermes profile delete` was not run in the verification. It stops every Hermes backend on the machine started with `-p apt-scraper` and removes Startup-folder login items. What it leaves on disk, the mark and then no directory, was written by the harness instead.
- Delivery is an open pull request, not a merge, as requested. The ticket stays In Progress.

## Result

`install.ps1` and `install.sh` decide the profile step from the Hermes root (`$HermesRoot`, `HERMES_ROOT`): `%LOCALAPPDATA%\hermes` and `~/.hermes`.

- A name marked deleted loses what is left of its directory. A missing directory is created with `hermes profile create apt-scraper --no-alias`, which clears the mark, and the `config.yaml` it writes is removed. Then `hermes profile install <checkout>/profile --force --yes` runs as before.
- `config.yaml` is saved and put back around the install only when the profile has `distribution.yaml` and is not marked deleted. Every other case gets the distribution's.
- `profileHome` and `profile_home` are set in the profile step and reused by the later steps. Spec › Installation and setup says a deleted profile or a failed install's leftover is installed anew with the distribution's `config.yaml`.

Verified on Windows at `699d8de`:

- Real `hermes` 0.22.5 (`%LOCALAPPDATA%\hermes\bin\hermes.exe`), with `HERMES_HOME` on throwaway roots under `%TEMP%\hermes-0049`. A harness outside the repository ran only each installer's profile step, under Windows PowerShell and under Git Bash. Both passed all 58 checks: a first install on an empty root; a rerun that keeps a modified `config.yaml`; a reinstall after the delete's on-disk effect; an install over the real half-copied leftover, which a bare `profile install --force` on the marked name produced, failing with "Named profile home does not exist" as in the ticket; and an install over a live leftover without `distribution.yaml`. Each install wrote the manifest, cleared the mark, put the expected `config.yaml` in place, and `hermes profile list` listed `apt-scraper`. No gateway, dashboard, login entry or browser was started.
- `tests/test_installers.py` gains `ProfileStepTests`, 6 tests that run each installer's profile step alone against a fake `hermes` that refuses and fails as Hermes 0.22.5 does. They cover a missing profile, a deleted name, a deleted name's half-copied leftover, a rerun keeping the user's `config.yaml`, keeping it when the install fails, and a live leftover without `distribution.yaml`. Against `main`'s step, 8 subtests of 4 tests fail.
- `python -m unittest discover -s tests`: 600 passed, 0 skipped. `ruff check .` passed. The PowerShell parser reports 0 errors on `install.ps1` and `bootstrap.ps1`, and `bash -n` passes on `install.sh` and `bootstrap.sh`.

Not run: the installers end to end, and anything on macOS.

Delivered on `fix/installer_deleted_profile` in [PR #41](https://github.com/stjepanvrbic/apt-scraper/pull/41), merged to `main` as a984306 after one review round with no blocking findings.

## Comments

- 2026-10-26: Hit on the owner's first one-line install after the test profiles were deleted. Unblocked by hand with `profile create`, removing its `config.yaml`, then `profile install --force`.
- 2026-10-26: While checking the new tests against `main`'s step, a substitution meant to point the old PowerShell step at the test root did not apply. Six runs of `main`'s step therefore copied the owner's `%LOCALAPPDATA%\hermes\profiles\apt-scraper\config.yaml` to a temporary file and wrote it back. The file still matches the distribution's `config.yaml` byte for byte, which is what the owner's manual install at 21:07 had put there. Nothing else under that root was touched.
