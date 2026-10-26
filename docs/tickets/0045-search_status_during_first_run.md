---
title: The search status reports a first run in progress
type: Bug
state: Backlog
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

- [ ] With a run in progress and none finished, `status` reports a state for a run in progress, exits 0 and lists no problem.
- [ ] A run in progress that has gone on longer than twice the run budget still counts as a problem.
- [ ] The search skill's list of states matches.
- [ ] A test covers both cases.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Search](../spec.md#search).

## Design

`Search.status` in `search.py` sets `not started` and the problem "The search has not run yet." whenever `last_run` is empty, though it reports `running_since` in the same output. In the end-to-end run of 2026-10-26 ([report](../research/e2e_run_2026-10-26.md#2-search)), `status` exited 3 with that problem while the run was 97 seconds in. A conversation then told the user "The search script reports 'not started'" and that the job was running. The agent started no second run, and `search.py run` refuses one while a run is in progress.

## Deviations

## Result
