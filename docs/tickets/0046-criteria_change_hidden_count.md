---
title: A criteria change reports how many listings it hid
type: Bug
state: In Progress
priority: 3
assignee: agent
milestone:
parent:
blocked_by: []
related: [0002, 0003]
---

## Outcome

After the user changes a criterion, the agent tells them how many listings the change hid and how many it showed again, not how many are outside the criteria in total.

## Acceptance

- [ ] `settings.py save` prints, for a criteria change, the number of listings it hid and the number it showed again, next to the totals it prints now.
- [ ] The interview skill tells the agent to report those two numbers.
- [ ] A test covers a change that hides listings while others were already hidden.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Interview](../spec.md#interview), [Spec › Search](../spec.md#search).

## Design

`save` prints `criteria` with `outside`, `matching` and `awaiting_judgment` for all stored listings, and step 5 of "Changing settings later" in the interview skill says "say how many listings are hidden, from the `outside` count". In the end-to-end run of 2026-10-26 ([report](../research/e2e_run_2026-10-26.md#6-must-have-change)), lowering the price limit from 2,600 to 2,300 USD hid 50 listings, and 25 were already hidden for "Cats allowed". `save` printed `outside: 75`, and the agent said the change "hid 75 listings that were over the new limit".

## Deviations

## Result
