---
title: Later preference judgments run in extraction subagents, not the job agent
type: Chore
state: In Progress
priority: 3
assignee: agent
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

No listing text enters the features job agent's conversation. Preferences stated after a listing's extraction are judged in `delegate_task` subagents from task files, like extraction, and saved with `features.py judge`.

## Acceptance

- [x] `features.py preferences` writes one task file per batch into `extraction/` and the report lists the `delegate_task` calls; the job agent's turn holds no listing text.
- [x] The `judge` command and attempt bookkeeping are unchanged; `docs/ranking.md` describes the isolation for both reads.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features), [Spec › Invariants](../spec.md#invariants)

## Design

`features.py preferences` writes one task file per batch of 25 listings into `extraction/judgments/`, from the template `judgment.md`, and prints `{"calls": [...], "remaining": n}`: `delegate_task` calls of up to 10 tasks, each task a goal that names the task file. The task file carries the listings as one JSON block, as the extraction task does, and the command the subagent saves with, `features.py judge` on a file in the scratch directory. A wake's subagents count against Hermes's 50 per turn, so the run gives the judgments what its extractions leave (`judgment_batches` in `features.json`), hands over the judgment reads of the listings in those batches, and `preferences` puts those listings first. The next run removes the task files with the other stale work directories.

## Deviations

`features.py readings` printed each note's listing title, which is listing text in the job agent's conversation. It now prints the listing id, and `ListingStore.unread_notes` no longer joins the title.

Attempt bookkeeping changed in one respect: the run hands over only the judgments its wake has subagents for, so a listing a wake cannot reach no longer counts an attempt.

## Result

No listing text reaches the features job agent: the run's report and `preferences` print only listing ids, task file paths and counts, and judgment subagents read the listings from their task files and save with `judge`, whose checks are unchanged. `docs/ranking.md` describes the isolation of both reads, `SKILL.md` and the job prompt tell the agent to run the judgment tasks once after the readings. Tests: `test_a_preference_stated_after_the_extraction_is_judged_from_the_record_and_text` runs `preferences` and `judge` through the command line and checks that the printed calls hold no listing text; batching, the wake's subagent budget and a forged task file have their own tests in `PreferenceTests`. All of these tests fail on `main` (`adc3201`).

Checks at `5ea8fba`: `python -m unittest discover -s tests`: 517 passed, 0 skipped. `ruff check profile tests`: clean. Hermes's skill linter (`tools/skill_linter.py`, under Hermes's venv Python): 0 findings for `listing-features`, the same 6 `gallery` warnings as `main`. Its cron prompt scanner (`_scan_cron_prompt`, `_scan_cron_skill_assembled`) passes the job prompt with the skill, and its context scanner (`_scan_context_content`) leaves `SKILL.md`, `extraction.md` and `judgment.md` unchanged. Checked against Spec › Listing features, Ranking, Contracts and invariants and Decisions at the same revision, none changed; the project keeps its decisions in the spec, with no ADR register.

Delivered on `fix/features_job` in [PR #33](https://github.com/stjepanvrbic/apt-scraper/pull/33), open against `main`, not merged; review is pending with the coordinator.

## Comments

- 2026-10-25: Found by the deep review: `preferences()` prints up to 2,000 characters of each listing's description into the cron agent's turn, and that agent has terminal, file, vision and delegation tools.
