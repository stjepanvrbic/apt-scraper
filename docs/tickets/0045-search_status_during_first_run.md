---
title: The search status reports a first run in progress
type: Bug
state: Done
priority: 4
assignee:
milestone:
parent:
blocked_by: []
related: [0003]
---

## Outcome

While the first search runs, `search.py status` says it is running and reports no problem, so a conversation during the first search tells the user it is under way.

## Acceptance

- [x] With a run in progress and none finished, `status` reports a state for a run in progress, exits 0 and lists no problem.
- [x] A run in progress that has gone on longer than twice the run budget still counts as a problem.
- [x] The search skill's list of states matches.
- [x] A test covers both cases.
- [x] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Search](../spec.md#search).

## Design

`Search.status` in `search.py` sets `not started` and the problem "The search has not run yet." whenever `last_run` is empty, though it reports `running_since` in the same output. In the end-to-end run of 2026-10-26 ([report](../research/e2e_run_2026-10-26.md#2-search)), `status` exited 3 with that problem while the run was 97 seconds in. A conversation then told the user "The search script reports 'not started'" and that the job was running. The agent started no second run, and `search.py run` refuses one while a run is in progress.

## Deviations

- `state` now describes the latest run, so it gained `finished` and `failed`. Before, `running` meant "the last run finished less than three hours ago". Now it means a run is in progress, and that case reads `finished` or `failed`.
- `running_since` now comes from the latest run only, and `status` reports it only while `state` is `running`. Before, a run killed without finishing kept its start in `running_since` after later runs finished.

## Result

`SearchState.latest_run()` reads the latest row of `runs`, which `start_run` writes when a run starts and `finish_run` completes when it ends. `Search.status` derives `state` from it: `not started`, `running`, `finished` or `failed`, plus `waiting for the interview` and `stopped` as before. A run started 97 s ago with none finished is `running`, exits 0 and lists no problem. A run still going after twice the 25-minute budget is `running` with the problem "The search run that started at … has not finished after N minutes." `stopped` is now measured from the latest run's end, or from its start when it never ended. `profile/skills/search/SKILL.md` lists the states. Its "Running the search now" section tells the agent to report a run in progress with its start time and not to run the job again. `docs/search.md` says what `status` follows.

A run that hangs for three hours is reported as `stopped`, not as a stuck run. Hermes gives a cron script 3600 s by default (`cron/scheduler.py:1391` in Hermes's checkout) and skips a fire only while the job still runs (`:4308`). The `run` guard allows a new run after 50 minutes, so with the gateway up a new run starts within the hour.

Verified at `324f202`:

- New tests in `tests/test_search_run.py`: a first run in progress (exit 0, `running`, no problem, `running_since` set); a run of 51 minutes (exit 3, one problem); an unfinished run superseded by a finished one (`finished`, `running_since` null), then a new run (`running`, `last_run` the finished one); an unfinished run older than three hours (`stopped`, `running_since` null); a crashed run (`failed`). Against `main`'s code, 6 tests in this file fail.
- `python -m unittest discover -s tests`: 530 passed, 0 skipped. Two earlier full runs each failed `test_rerank_with_signals_meets_the_two_second_cpu_budget` (2.06 s against 2.0 s) while other test runs shared the machine. The test passes alone, and on the run above, and it calls no changed code. `main` at `1b19404`: 526 passed.
- `ruff check .` clean. Hermes's skill linter: 0 findings for `search`, as on `main`.
- Merged with open PR #36 (`chore/cron_agents_file_tools`) in a scratch worktree: no conflict, and `tests/test_search_run.py` and `tests/test_interview.py` pass (73).
- Review of `e4922dd` found the f-string SQL helper and `running_since` reported while `stopped`, both fixed in `a34ddcc`. It also found test lines that conflicted with PR #36, moved in `a34ddcc`. It flagged a hung run over three hours shown as `stopped`, which was kept for the reason above. The reassessment of `324f202` accepted all of them.

Tech debt fixed: `running_since` no longer reports a killed run after later runs finished.

Delivered on `fix/e2e_search_bugs`, in a pull request to `main`, not merged. No ADR register; the spec's Search section is unchanged.
