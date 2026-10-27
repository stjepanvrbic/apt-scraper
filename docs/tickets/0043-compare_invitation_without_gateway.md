---
title: A user without a messaging gateway is asked for Compare answers
type: Bug
state: Done
priority: 2
assignee:
milestone:
parent: 0015
blocked_by: []
related: [0023, 0027, 0034]
---

## Outcome

Once the interview is complete and the first feature records exist, the user is asked to answer 5 to 8 Compare questions, also when no messaging gateway is connected.

## Acceptance

- [x] On a profile with no messaging gateway, the first conversation after the first two eligible feature records asks the user to answer 5 to 8 questions in the gallery's Compare tab, once.
- [x] With a gateway connected, the invitation is still sent through it once, as now.
- [x] A test covers both paths against a real store.
- [x] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking) ("After the interview, once the first listings have feature records, the agent asks the user to answer 5 to 8 of them."), [Spec › Acceptance](../spec.md#acceptance) "Pairwise questions".

## Design

`send_compare_invitation` in `features.py` sends only when `usable('gateway')` holds, and otherwise returns no problem, so `features.py status` exits 0 and nothing tells the conversation agent to ask. The invitation should stay pending without a gateway and reach the user through the next conversation instead, for example by `status` reporting the pending invitation, which `SOUL.md` already has the agent pass on, and a command that records it as asked.

Evidence from the end-to-end run of 2026-10-26 ([report](../research/e2e_run_2026-10-26.md#4-gallery-reranks-compare-and-review)): with the interview complete and 100 records, `features.json` had no `compare_invited_at`, `status` reported no problem, and the next conversation ("Hi, anything new?") reported the new listings and the connections without mentioning Compare.

## Deviations

- The invitation's delivery state is one record in the listing store (`compare_invitation`, one row: when the gateway, the gallery and a conversation delivered it, and the last failed attempt while none had) rather than in `features.json`, because the gallery reads it. The job, the gallery and `status` each derive what to do from that record alone. A `compare_invited_at` already in `features.json` carries over at migration as delivered through the gateway.
- A failed send or gateway check is not one of `status`'s `problems`. It stays in the invitation's record, where it makes `ask_to_compare` true, and the first delivery on any surface clears it. A problem stored in `features.json` lasted until the job's next run, so a second conversation in that window was told again to ask.
- Without a gateway the gallery also shows the invitation, as a banner above the tabs with Open Compare, until the first pairwise answer. A skipped pair does not end it.
- `features.py invite-compare` is replaced by `features.py compare-asked`, which records that a conversation asked. `status` reports `ask_to_compare`. Before the job has delivered the invitation, `status` checks the gateway, so a conversation between the first records and the job's next run also asks when no gateway is connected.

## Result

Delivered on branch `fix/e2e_ranking_bugs` at `de6f538`, in a pull request to `main`, not merged. The features job records the surface that delivered the invitation (`deliver_compare_invitation` in `features.py`, `compare_invitation` and `deliver_compare_invitation` in `store.py`). With a gateway it sends the message once. Without one it records the gallery, whose `/api/compare/invitation` turns on the banner, and `SOUL.md` has the conversation agent ask once when `status` reports `ask_to_compare`, then run `compare-asked`. A conversation that asked first makes the job show the banner without sending a message. The first answer ends the invitation on every surface.

Tests: `tests/test_compare_invitation.py` covers both paths against a real store: no gateway (gallery, then a conversation asks once via the `compare-asked` command), gateway (sent once, no conversation ask), the window before the job's next run, a failed send, a failed gateway check, an answer ending it, the HTTP endpoint and the migration of `compare_invited_at`. `gallery/tests/compare.spec.ts` checks the banner headless: hidden after a gateway delivery, shown after a gallery delivery, axe clean, Open Compare selects Compare, gone after the first answer.

`python -m unittest discover -s tests`: 554 tests; the only failure was the CPU-budget test `test_rerank_with_signals_meets_the_two_second_cpu_budget` at 2.09 s while the suite loaded the machine. It passes 3 of 3 runs alone, and a 300-listing rerank takes 1.69 to 1.70 s of CPU on both `main` and this branch. Playwright headless, all 40 specs pass. `ruff check profile tests`, `npm run lint`, `tsc` and `npm run build` are clean. Hermes's skill linter and skills guard give the same findings for `listing-features`, `ranking` and `gallery` as on `main`, and its context scanner passes `SOUL.md`, both changed skills and `review.md`. The new tests fail against `main`'s code.

Review fix at `df72c44`, after merging `main`: a failed send or gateway check left a problem in `features.json` that `status` reported until the job's next run, so after one conversation had asked and run `compare-asked`, the next was told to ask again. The invitation's delivery state is now one record, the store's `compare_invitation` row (migration `compare_invitation`), which holds when each surface delivered it and the last failed attempt while none had. `deliver_compare_invitation` in `features.py` records a failure there with `fail_compare_invitation` and puts the line in the run's report only. `ask_to_compare` is true while the record holds a failure, and `deliver_compare_invitation` in `store.py` clears the failure with any surface's delivery. The Compare bullet is gone from the problems in `listing-features/SKILL.md`. Tests: in `tests/test_compare_invitation.py`, `test_a_conversation_that_asked_after_a_failed_send_leaves_the_gallery_to_show_it_without_another_message` runs `features.py status` after the failed send (exit 0, no problem, `ask_to_compare` true), after `compare-asked` twice (exit 0, `ask_to_compare` false) and after the next run. The failed-send and failed-check tests check that the failure is in the record and not in `problems`, and that a delivery clears it.

`python -m unittest discover -s tests`, after merging `main` at `46c75fd`: 594 tests, all pass, none skipped. Playwright headless, all 40 specs pass. `ruff check profile tests`, `npm run lint`, `tsc` and `npm run build` are clean, and the build leaves the committed `web/` assets unchanged. The new and changed tests fail against `4ccdf64`.
