---
title: A reason's station distance reads as one item of its list
type: Bug
state: In Progress
priority: 3
assignee: agent
milestone:
parent:
blocked_by: []
related: [0019, 0020]
---

## Outcome

Each value a listing's reason or explanation names reads as one thing, so the station distance never reads as an area or another feature counting against the listing.

## Acceptance

- [ ] A reason that names the station distance keeps the station's name with its distance, such as "1.6 km to Logan Square station", and never puts a comma inside a value of the "For it" or "Against it" lists.
- [ ] The review pass's citations of that value still match the explanation's value exactly.
- [ ] A count is grammatical: "1 bathroom", "2 bathrooms".
- [ ] Tests cover both values in `scoring.py`'s reason and explanation.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking) ("A listing's reason names the features that moved its score most").

## Design

`scoring.py:865` writes the value as `{distance} km to the nearest station, {station}`, and `scoring.py:919` joins the values against a listing with ", ". In the end-to-end run of 2026-10-26 ([report](../research/e2e_run_2026-10-26.md#4-gallery-reranks-compare-and-review)) the top listing's reason read "Against it: 1.6 km to the nearest station, Logan Square, subway tile.", while its area Logan Square contributed +0.44. The bathrooms value at `scoring.py:874` prints "1 bathrooms".

## Deviations

## Result
