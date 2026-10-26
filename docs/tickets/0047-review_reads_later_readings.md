---
title: The review pass reads the preferences of the rerank it reviews
type: Bug
state: In Progress
priority: 4
assignee: agent
milestone:
parent:
blocked_by: []
related: [0021]
---

## Outcome

The review task describes the ranking state of the rerank it reviews, so a preference or note read after that rerank cannot make the review report its weights as running against starting weights the rerank never used.

## Acceptance

- [ ] The task's preferences, note readings, starting weights and label counts come from the inputs the rerank used, stored with its waiting review as the check's scores are.
- [ ] A test saves a note reading between a rerank and its review and checks that the review task does not contain it.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking) ("After each rerank, the review pass reads the top 50 and the state of the whole ranking.").

## Design

`pending_review` in `rerank.py` builds the task with `review_task(store.ranking_inputs(), …)` when the job hands it over, and `review_task` in `review.py` takes the settings, preference readings, note readings and starting weights from those current inputs while the weights are the rerank's. In the end-to-end run of 2026-10-26 ([report](../research/e2e_run_2026-10-26.md#4-gallery-reranks-compare-and-review)), rerank 5 finished at 23:00:16, a note's reading was saved at 23:00:55 and the review task was written at 23:01:11. The review's finding was "The weights of tag:galley kitchen and rating:kitchen_size run against the stated dislikes.", dislikes that came from that note.

## Deviations

## Result
