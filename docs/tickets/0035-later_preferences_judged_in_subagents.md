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

No listing text enters the features job agent's conversation. The agent sees the tag list, in the readings' vocabulary and from `features.py tags`: tags are a controlled vocabulary that `features.py save` validates to 2 to 40 characters of the letters a to z, digits, spaces, hyphens, apostrophes, `&` and `/`. Preferences stated after a listing's extraction are judged in `delegate_task` subagents from task files, like extraction, and saved with `features.py judge`.

## Acceptance

- [x] `features.py preferences` writes one task file per batch into `extraction/` and the report lists the `delegate_task` calls; the job agent's turn holds no listing text.
- [x] The `judge` command and attempt bookkeeping are unchanged; `docs/ranking.md` describes the isolation for both reads.
- [x] `docs/ranking.md` and the listing-features `SKILL.md` say the job agent sees the tag list, validated to the tag shape. Test: every entry `readings` prints is a fixed vocabulary entry or a tag of that shape, with no other listing text.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features), [Spec › Invariants](../spec.md#invariants)

## Design

`features.py preferences` writes one task file per batch of 25 listings into `extraction/judgments/`, from the template `judgment.md`, and prints `{"calls": [...], "remaining": n}`: `delegate_task` calls of up to 10 tasks, each task a goal that names the task file. The task file carries the listings as one JSON block, as the extraction task does, and the command the subagent saves with, `features.py judge` on a file in the scratch directory. A wake's subagents count against Hermes's 50 per turn, so the run gives the judgments what its extractions leave (`judgment_batches` in `features.json`), hands over the judgment reads of the listings in those batches, and `preferences` puts those listings first. The next run removes the task files with the other stale work directories.

## Deviations

`features.py readings` printed each note's listing title, which is listing text in the job agent's conversation. It now prints the listing id, and `ListingStore.unread_notes` no longer joins the title.

Attempt bookkeeping changed in one respect: the run hands over only the judgments its wake has subagents for, so a listing a wake cannot reach no longer counts an attempt.

## Result

No listing text reaches the features job agent: the run's report and `preferences` print only listing ids, task file paths and counts, `readings` prints listing ids and the vocabulary, whose only part that extraction adds is the tag list, validated to the tag shape, and judgment subagents read the listings from their task files and save with `judge`, whose checks are unchanged. `docs/ranking.md` describes the isolation of both reads, `SKILL.md` and the job prompt tell the agent to run the judgment tasks once after the readings. Tests: `test_a_preference_stated_after_the_extraction_is_judged_from_the_record_and_text` runs `preferences` and `judge` through the command line and checks that the printed calls hold no listing text; batching, the wake's subagent budget and a forged task file have their own tests in `PreferenceTests`. All of these tests fail on `main` (`adc3201`). `test_the_vocabulary_holds_no_listing_text_only_tags_of_the_tag_shape` saves a listing whose title, description and amenities hold an injection, rejects it as a trait, and checks that every vocabulary entry beyond the fixed ones is `tag:` with a tag of the shape and nothing from the listing; it guards behavior `main` already has.

Checks at `5ea8fba`: `python -m unittest discover -s tests`: 517 passed, 0 skipped. `ruff check profile tests`: clean. Hermes's skill linter (`tools/skill_linter.py`, under Hermes's venv Python): 0 findings for `listing-features`, the same 6 `gallery` warnings as `main`. Its cron prompt scanner (`_scan_cron_prompt`, `_scan_cron_skill_assembled`) passes the job prompt with the skill, and its context scanner (`_scan_context_content`) leaves `SKILL.md`, `extraction.md` and `judgment.md` unchanged. Checked against Spec › Listing features, Ranking, Contracts and invariants and Decisions at the same revision, none changed; the project keeps its decisions in the spec, with no ADR register.

Checks at `6f369aa`, after merging `main` (`6feee4c`): `python -m unittest discover -s tests`: 524 passed, 0 skipped. `ruff check profile tests`: clean. Hermes's skill linter: 0 findings for `listing-features`, the same 6 `gallery` warnings as `main`. Its context scanner leaves `SKILL.md`, `extraction.md` and `judgment.md` unchanged.

Delivered on `fix/features_job` in [PR #33](https://github.com/stjepanvrbic/apt-scraper/pull/33), open against `main`, not merged; review is pending with the coordinator.

## Comments

- 2026-10-25: Found by the deep review: `preferences()` prints up to 2,000 characters of each listing's description into the cron agent's turn, and that agent has terminal, file, vision and delegation tools.
- 2026-10-25: PR review: the tags extraction subagents add reach the job agent through the readings' vocabulary, against the outcome as first written. Decided: tags are a controlled vocabulary validated at save, so they may enter the job agent's conversation; the outcome and `docs/ranking.md` say so.
