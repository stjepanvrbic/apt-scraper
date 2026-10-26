---
title: A user without a messaging gateway is asked for Compare answers
type: Bug
state: In Progress
priority: 2
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: [0023, 0027, 0034]
---

## Outcome

Once the interview is complete and the first feature records exist, the user is asked to answer 5 to 8 Compare questions, also when no messaging gateway is connected.

## Acceptance

- [ ] On a profile with no messaging gateway, the first conversation after the first two eligible feature records asks the user to answer 5 to 8 questions in the gallery's Compare tab, once.
- [ ] With a gateway connected, the invitation is still sent through it once, as now.
- [ ] A test covers both paths against a real store.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking) ("After the interview, once the first listings have feature records, the agent asks the user to answer 5 to 8 of them."), [Spec › Acceptance](../spec.md#acceptance) "Pairwise questions".

## Design

`send_compare_invitation` in `features.py` sends only when `usable('gateway')` holds, and otherwise returns no problem, so `features.py status` exits 0 and nothing tells the conversation agent to ask. The invitation should stay pending without a gateway and reach the user through the next conversation instead, for example by `status` reporting the pending invitation, which `SOUL.md` already has the agent pass on, and a command that records it as asked.

Evidence from the end-to-end run of 2026-10-26 ([report](../research/e2e_run_2026-10-26.md#4-gallery-reranks-compare-and-review)): with the interview complete and 100 records, `features.json` had no `compare_invited_at`, `status` reported no problem, and the next conversation ("Hi, anything new?") reported the new listings and the connections without mentioning Compare.

## Deviations

## Result
