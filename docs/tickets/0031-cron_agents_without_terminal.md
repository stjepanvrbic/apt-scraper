---
title: Scheduled agents save their results through file tools, not the terminal
type: Chore
state: Todo
priority: 3
assignee:
milestone:
parent:
blocked_by: [0021]
related: [0006, 0016]
---

## Outcome

The search, listing-features and review agents that cron wakes save and correct their results without the terminal tool, so a scheduled session can neither reach the network nor read `~/.secrets/`.

## Acceptance

- [ ] Each of the three jobs' wake sessions runs with file tools only, and its save and correct-and-resave paths work in one turn.
- [ ] A test per job shows the session's toolset excludes the terminal.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Invariants](../spec.md#invariants)

## Design

- Today each job saves through a script run in the terminal, and a rejected file is corrected and saved again in the same turn. The scripts' validation moves to the job's own check after the session, or the save becomes a file write the job picks up.

## Deviations

## Result

## Comments

- 2026-10-25: Raised by the review pass's reviewer; the review agent keeps the terminal for now like the other two jobs.
