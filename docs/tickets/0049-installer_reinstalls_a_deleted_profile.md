---
title: The installer installs the profile under a name that was deleted before
type: Bug
state: In Progress
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

- [ ] On a Hermes root where `apt-scraper` was deleted (a tombstone at `profiles/.deleted/apt-scraper`), both installers install the profile without error. Verified against the real `hermes` on a throwaway `HERMES_HOME` on Windows.
- [ ] `config.yaml` is kept across a rerun only when the profile was installed from this distribution before (`distribution.yaml` present); otherwise the distribution's `config.yaml` is installed.
- [ ] A half-copied profile directory from a failed install does not stop the next run.
- [ ] `tests/test_installers.py` covers the deleted-name case and the config rule against the fake `hermes`.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Installation and setup](../spec.md)

## Design

- Hermes 0.22.5: `hermes profile delete` writes a tombstone; `create_profile` (`hermes_cli/profiles.py`) calls `clear_named_profile_deleted` before building, but `install_distribution` (`hermes_cli/profile_distribution.py`) does not, so `write_manifest` fails in `assert_named_profile_home_live` with "Named profile home does not exist" after the payload was already copied.
- The installer creates the profile with `hermes profile create` when it is missing or tombstoned, then installs the distribution with `--force`, which takes `config.yaml` from the distribution.

## Deviations

## Result

## Comments

- 2026-10-26: Hit on the owner's first one-line install after the test profiles were deleted. Unblocked by hand with `profile create`, removing its `config.yaml`, then `profile install --force`.
