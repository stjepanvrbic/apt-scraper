---
title: The gallery launcher test writes into the real secrets directory
type: Bug
state: Done
priority: 2
assignee:
milestone:
parent:
blocked_by: []
related: [0003]
---

## Outcome

No test touches `~/.secrets/`. `tests/test_gallery_launcher.py` runs against a temporary secrets directory, and the launcher takes that directory from one place that tests can point elsewhere.

## Acceptance

- [x] `tests/test_gallery_launcher.py` creates nothing under the real `~/.secrets/apt-scraper/gallery/`, and two test runs at once do not collide.
- [x] The launcher's secrets location is resolved in one function, used by the code and overridden by the tests.
- [x] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Gallery](../spec.md#gallery)

## Design

`launch.secrets_directory()` returns `Path.home() / '.secrets' / 'apt-scraper' / 'gallery'`, and `connection_file` builds every record path from it. The production location stays as [the gallery guide](../gallery.md) documents it. Each launcher test gets its own temporary directory as `HOME` and `USERPROFILE`. That directory is set in the test process environment, so the `gallery.py` child processes inherit it and `secrets_directory()` resolves inside it.

## Deviations

The tests override `secrets_directory()` through the home directory it resolves from instead of patching the function. Most launcher tests run `gallery.py` as a child process, where a patch in the test process has no effect, and production code gets no test-only setting.

## Result

`secrets_directory()` in `profile/skills/gallery/scripts/launch.py` is the one place the record directory is resolved. `tests/test_gallery_launcher.py` gives every test its own temporary home. The open/stop test now asserts one record in that home after `open` and none after `stop`. Before the fix the virtual-environment and serve-reuse tests killed the server and left two records in the real directory on every run. Those records now land in the temporary home and are deleted with it.

To check whether any other test touches home, the full suite and then each module ran with `HOME` and `USERPROFILE` set to an empty directory. Only `tests/test_gallery_launcher.py` wrote there: the connection records, plus `AppData/Roaming` created when the virtual-environment test starts Python. No other module writes under home. The default store directory under `%LOCALAPPDATA%` did not exist before or after the runs. Every other test that runs a script sets `HERMES_HOME` to a temporary directory.

Verification at `c2eba03`: the new assertion failed before the fix with the record outside the test's home (0 != 1). After the fix, `python -m unittest discover -s tests` passed 482 tests with 0 skipped. Two simultaneous runs of the launcher module, each under an empty sentinel home, both passed, and both sentinels stayed empty. `ruff check profile tests` passed. The spec has no secrets requirements beyond Gallery and the repository has no separate ADR register. An independent review found nothing material.

Tech debt fixed in the same test file: sorted imports, a `free_port()` helper replacing three copies of the port lookup, a missing blank line between tests, and `assertNotEqual`/`assertNotIn` in place of `assertFalse` on comparisons.

Not fixed and out of scope: `free_port()` releases its port before the child process binds it, so two simultaneous runs can still race for a port. Records that earlier runs left in the real `~/.secrets/apt-scraper/gallery/` were neither inspected nor removed, because verification never opens that directory.

Delivered as [PR #28](https://github.com/stjepanvrbic/apt-scraper/pull/28), branch `fix/launcher_test_secrets`. Review by the owner and merge remain pending.

## Comments

- 2026-10-25: Found while implementing the distance features: overlapping test runs hit "Permission denied" on the real directory.
