---
title: A failed Compare invitation aborts the features job and gives up on waiting listings
type: Bug
state: Todo
priority: 2
assignee:
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

The features job's report, wake decision and handed-read bookkeeping never depend on the messaging gateway. A failed Compare invitation is recorded as the run's problem, shown by `status`, and retried on the next run.

## Acceptance

- [ ] With `tell_user` raising a send error, a run still lists its extraction tasks, wakes the agent as before, counts no attempt for any read, records the failure as the run's problem and leaves the invitation pending; the next run retries it. A job-level test covers this.
- [ ] The invitation is its own guarded step after the report is complete.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features)

## Design

## Deviations

## Result

## Comments

- 2026-10-25: Found by the deep review: `invite_compare()` runs inside `Features.run()` after the handed reads are written; a `RuntimeError` from `tell_user` or `usable()` replaces the report with "Extraction failed" and the next run counts a failed attempt for every read handed before, so a persistent send error gives up every waiting listing after 40 minutes with the wrong cause.
