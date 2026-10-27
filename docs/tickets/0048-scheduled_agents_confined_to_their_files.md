---
title: Scheduled agents reach only their job's files
type: Chore
state: Backlog
priority: 3
assignee:
milestone:
parent:
blocked_by: []
related: [0031]
---

## Outcome

The agents the search, listing-features and review jobs wake, and their subagents, can neither read files outside their job's task and inbox files nor write outside their job's inbox, and the features job's vision tool opens only the job's own photo files.

## Acceptance

- [ ] A scheduled session cannot read `~/.secrets/` or any file outside its job's task files, inbox and photos.
- [ ] A scheduled session cannot write the profile's scripts, skills, `SOUL.md` or any file outside its job's inbox.
- [ ] The features job's `vision_analyze` cannot fetch a URL.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Contracts and invariants](../spec.md#contracts-and-invariants)

## Design

- With the terminal gone, Hermes 0.22.5's file tools still read any file the user can, apart from Hermes's own credential stores (`get_read_block_error` in `agent/file_safety.py`), and write any file the user can outside Hermes's deny lists, including the profile's `scripts/*-run.py` and `skills/*/scripts/*.py`, which the next cron run executes, and files such as the Windows Startup folder's. The protected-instruction gate exempts the Hermes home (`tools/file_tools_write_guards.py`). `HERMES_WRITE_SAFE_ROOT` confines writes for the whole process, not one job. `vision_analyze` downloads public image URLs (`tools/vision_tools.py`).
- Candidates to weigh: a Hermes change for per-job read and write roots, a profile plugin with a `pre_tool_call` hook that refuses paths outside the job's directories, or running the cron jobs in a process of their own with `HERMES_WRITE_SAFE_ROOT` set.

## Deviations

## Result

## Comments

- 2026-10-26: Found by the review of the change that took the terminal from the scheduled agents.
