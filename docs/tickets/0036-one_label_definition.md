---
title: Extraction and scoring share one definition of a labeled listing
type: Bug
state: In Progress
priority: 3
assignee: agent
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

Whether a listing is labeled (favorite, dismissed, contact request, note, or an endpoint of a pairwise answer) is defined once and used by both the features job and the scoring model, so a compared listing always keeps a usable feature record.

## Acceptance

- [x] `labeled` lives once, in `scoring.py`, over fields the store's snapshot computes the same way for `ranking_inputs` and the features job; `features.py` uses it for `labeled` and `needs_record`.
- [x] Test: a paired listing whose content changes while unavailable is extracted, and its pair row stays in the fit.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking)

## Design

`queries.py` has `answered_pairs`, `paired` and `user_noted`. `snapshot()` (behind `ListingStore.state()`) and `ListingStore.ranking_inputs()` both use them for each listing's `user_notes` and `paired`, so `scoring.labeled` reads the same fields from either. `features.py` imports `labeled` from `scoring.py` for its ordering and `needs_record`.

## Deviations

## Result

A listing in a pairwise answer now needs a record when it is not shown, so a compared listing whose content changes while unavailable is extracted and its answer stays in the fit. `test_a_paired_listing_whose_content_changes_while_unavailable_is_extracted_and_its_answer_stays_in_the_fit` fails on `main` (no extraction task) and passes here. `docs/ranking.md` names the pairwise answer among the labels.

Checks at `5ea8fba`: `python -m unittest discover -s tests`: 517 passed, 0 skipped. `ruff check profile tests`: clean. Hermes's skill linter (`tools/skill_linter.py`, under Hermes's venv Python): 0 findings for `listing-features`, the same 6 `gallery` warnings as `main`. Its cron prompt scanner (`_scan_cron_prompt`, `_scan_cron_skill_assembled`) passes the job prompt with the skill, and its context scanner (`_scan_context_content`) leaves `SKILL.md`, `extraction.md` and `judgment.md` unchanged. Checked against Spec › Listing features, Ranking, Contracts and invariants and Decisions at the same revision, none changed; the project keeps its decisions in the spec, with no ADR register.

Checks at `6f369aa`, after merging `main` (`6feee4c`): `python -m unittest discover -s tests`: 524 passed, 0 skipped. `ruff check profile tests`: clean. Hermes's skill linter: 0 findings for `listing-features`, the same 6 `gallery` warnings as `main`. Its context scanner leaves `SKILL.md`, `extraction.md` and `judgment.md` unchanged.

Delivered on `fix/features_job` in [PR #33](https://github.com/stjepanvrbic/apt-scraper/pull/33), open against `main`, not merged; review is pending with the coordinator.

Tech debt fixed: `ListingStore.pairwise_answers` reuses the shared query instead of repeating it.

## Comments

- 2026-10-25: Found by the deep review: `features.py` omits pairwise answers from its labeled set, so a compared listing that becomes unavailable before extraction gets no record and `scoring.py` drops the whole pair.
