---
title: The rerank timing tests scale their budget by a speed measured minutes before the rerank
type: Bug
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: [0030, 0057]
---

## Outcome

The two-second rerank checks judge the rerank against the machine's speed at the time of the rerank, so a shared runner whose speed drifts within a job neither fails a rerank that is fast for its moment nor passes one that is slow for it.

## Acceptance

- [ ] Every rerank timing check, the four CPU-time tests, the HTTP rerank test and the gallery's Playwright rerank test, scales its measurement by the reference work timed right before and right after it.
- [ ] The CI workflow is green on Linux, macOS and Windows.

## Spec

docs/spec.md, Acceptance, Rerank: with 300 listings and 50 favorites, Score and rerank shows the new order and the leave-one-out check within 2 seconds, judged on the owner's PC.

## Design

The macOS run of the Python suite on pull request #64, which changes only the gallery's front end, failed `test_the_300_listing_50_favorite_rerank_with_every_photo_column_stays_under_two_seconds`: 4.16 s of CPU against a budget of 2.82 s, a speed factor of 1.41. The budget came from `speed_factor`, which timed the reference work once per test process, at its first use, and cached it.

A diagnostic run logged every timed rerank with the reference work timed just before it, on two `macos-latest` runners (Apple M1, virtual) and one `ubuntu-latest` runner, through the whole suite and then five more runs of the timing tests:

| Runner | Reference work | Photo-column rerank | Rerank ÷ reference just before |
| --- | --- | --- | --- |
| macOS 1 | 0.46 to 0.88 s | 1.79 to 3.56 s | 3.1 to 4.8 |
| macOS 2 | 0.49 to 0.69 s | 1.78 to 2.28 s | 3.1 to 4.0 |
| Ubuntu | 0.84 to 0.92 s | 2.20 to 2.32 s | 2.5 to 2.7 |

On macOS the machine's speed changed by up to 1.9 times within one job, and the rerank and the reference work changed together; process CPU time equaled the thread's CPU time with one thread alive, so no other work in the process inflated it. A factor timed when the runner was fast and a rerank timed when it was slow is the failure above: scaled by a reference of 0.8 to 0.9 s, which macOS 1 measured during its slow stretch, 4.16 s is within budget.

`speed_factor` now times the reference work once, when called, and is never cached. `scaled` times it right before and right after a measurement and keeps the mean, and `least_scaled_cpu_seconds` takes the run whose CPU time is least once scaled to the owner's PC. The four CPU-time tests and the HTTP rerank test compare that against `RERANK_SECONDS`, and the Playwright test takes the factor before the click and after the order is visible. The highest ratio in the table, 4.8, is the reference's 5.7 (2 s over 0.35 s) less 16%, and the least of three runs stayed at or below 4.1.

## Deviations

## Result
