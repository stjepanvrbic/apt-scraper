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

- [x] `settings.py save` prints, for a criteria change, the number of listings it hid and the number it showed again, next to the totals it prints now.
- [x] The interview skill tells the agent to report those two numbers.
- [x] A test covers a change that hides listings while others were already hidden.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Interview](../spec.md#interview), [Spec › Search](../spec.md#search).

## Design

`save` prints `criteria` with `outside`, `matching` and `awaiting_judgment` for all stored listings, and step 5 of "Changing settings later" in the interview skill says "say how many listings are hidden, from the `outside` count". In the end-to-end run of 2026-10-26 ([report](../research/e2e_run_2026-10-26.md#6-must-have-change)), lowering the price limit from 2,600 to 2,300 USD hid 50 listings, and 25 were already hidden for "Cats allowed". `save` printed `outside: 75`, and the agent said the change "hid 75 listings that were over the new limit".

## Deviations

- `save` prints three counts, not two: `newly_hidden` (shown listings now outside), `newly_awaiting_judgment` (shown listings now waiting for a judgment of a new must-have) and `shown_again`. Without the third, adding a must-have would count every listing awaiting its judgment as hidden.
- The interview skill also has the agent report the judgments that follow: how many of the judged listings are shown and how many stay hidden, summed over the batches.
- The counts cover every stored listing, dismissed and unavailable ones included, as the totals do.

## Result

`apply_criteria` in `profile/skills/gallery/scripts/store.py` reads each listing's `matches_criteria` with its features. In the same transaction that saves and applies the criteria, it updates only the listings whose verdict changes that flag. It returns `CriteriaApplied(verdicts, hidden, awaiting, shown)` with their ids. `settings.py save` prints `newly_hidden`, `newly_awaiting_judgment` and `shown_again` next to `outside`, `matching` and `awaiting_judgment`. Step 5 of "Changing settings later" in the interview skill reports `newly_hidden` and `shown_again`, never `outside`. `docs/gallery.md` describes the return value.

Verified at `324f202`:

- `tests/test_interview.py`, new script test: four listings. Lowering the price hides 2 while 1 was already hidden (`outside: 3`, `newly_hidden: 2`). Adding a must-have leaves 1 shown and 2 hidden listings pending (`newly_awaiting_judgment: 1`). Reverting shows 3 again. Store tests check the ids and that applying unchanged criteria writes nothing (the store revision stays). Against `main`'s code, 4 tests in this file fail.
- `python -m unittest discover -s tests`: 530 passed, 0 skipped. `ruff check .` clean. Hermes's skill linter: 0 findings for `interview`, as on `main`.
- Review of `e4922dd` found that the skill's rule to subtract the judgments' shown count from `newly_hidden` miscounted pending listings. Fixed in `324f202` with `newly_awaiting_judgment`. The reassessment of `324f202` found one wording issue: the judgments' outside count also covers listings hidden before. The skill now says how many judged listings "stay hidden", summed across batches.

Delivered on `fix/e2e_search_bugs`, in a pull request to `main`, not merged. No ADR register; the spec's Interview and Search sections are unchanged.
