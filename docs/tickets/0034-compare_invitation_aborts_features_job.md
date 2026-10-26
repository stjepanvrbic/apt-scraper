---
title: A failed Compare invitation aborts the features job and gives up on waiting listings
type: Bug
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

The features job's report, wake decision and handed-read bookkeeping never depend on the messaging gateway. A failed Compare invitation is recorded as the run's problem, shown by `status`, and retried on the next run.

## Acceptance

- [x] With `tell_user` raising a send error, a run still lists its extraction tasks, wakes the agent as before, counts no attempt for any read, records the failure as the run's problem and leaves the invitation pending; the next run retries it. A job-level test covers this.
- [x] The invitation is its own guarded step after the report is complete.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features)

## Design

`Features.run()` runs four steps. Fetching the stations and preparing the extraction tasks each run under `step()`, which turns an exception into one of the run's problems and lets the next step go on. The readings part is computed from the waiting work and cannot fail on its own. `count_attempts()` forgets the previous run's handed reads before counting them, and `run()` writes the new handed reads together with the run's problems only once the report is complete. `send_compare_invitation()` comes last: any exception from `usable()`, `tell_user()` or the store becomes a problem appended to `features.json`, `compare_invited_at` stays unset, and the report and wake decision are returned unchanged. `features.json` holds `problems`, a list, in place of `problem`.

## Deviations

The brief asked only for the invitation to be guarded. The run's other steps are guarded as well, and the handed reads are cleared before counting, because a station failure, a failed preparation or a failure after counting had the same shape of bug: the whole report lost, or one wake counted twice.

## Result

The job's report, wake decision and attempt counts no longer depend on the messaging gateway, and a failed invitation shows in `status` and is sent again by the next run. `test_a_send_error_leaves_the_report_wake_and_attempts_intact_and_the_next_run_sends_again` runs the cron command (`features.main(['run'])`) with `tell_user` raising and covers the whole acceptance item; the station, preparation and double-count cases have their own tests in `AttemptTests`. All of these tests fail on `main` (`adc3201`).

Checks at `5ea8fba`: `python -m unittest discover -s tests`: 517 passed, 0 skipped. `ruff check profile tests`: clean. Hermes's skill linter (`tools/skill_linter.py`, under Hermes's venv Python): 0 findings for `listing-features`, the same 6 `gallery` warnings as `main`. Its cron prompt scanner (`_scan_cron_prompt`, `_scan_cron_skill_assembled`) passes the job prompt with the skill, and its context scanner (`_scan_context_content`) leaves `SKILL.md`, `extraction.md` and `judgment.md` unchanged. Checked against Spec › Listing features, Ranking, Contracts and invariants and Decisions at the same revision, none changed; the project keeps its decisions in the spec, with no ADR register.

Delivered on `fix/features_job` in [PR #33](https://github.com/stjepanvrbic/apt-scraper/pull/33), open against `main`, not merged; review is pending with the coordinator.

Tech debt fixed: a failure after the attempts were counted made the next run count the same wake again.

## Comments

- 2026-10-25: Found by the deep review: `invite_compare()` runs inside `Features.run()` after the handed reads are written; a `RuntimeError` from `tell_user` or `usable()` replaces the report with "Extraction failed" and the next run counts a failed attempt for every read handed before, so a persistent send error gives up every waiting listing after 40 minutes with the wrong cause.
