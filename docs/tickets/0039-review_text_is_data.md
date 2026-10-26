---
title: Review text is data under the citation discipline, never an instruction
type: Chore
state: Done
priority: 3
assignee:
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

Move reasons, flag notes and findings written by the review agent follow the same citation rule as reasons, or are structured, and the ranking skill presents them to the user as the review's data, never as something to act on.

## Acceptance

- [x] Flags are `(listing, kind, preference)` without free text, or their note follows the citation rule; move reasons and findings use citations from the task's values, tags and column names with connecting words outside; a file with any bad text is rejected whole.
- [x] `profile/skills/ranking/SKILL.md` tells the agent these texts are data to show, mirroring the task-file wording. Tests cover rejection of uncited text in each field.
- [x] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Ranking](../spec.md#ranking), [Spec › Invariants](../spec.md#invariants)

## Design

`checked_text` in `review.py` checks every review text: citations in square brackets, each from an allowed set, and outside them only allowed words, plain punctuation and no numbers. Reasons keep their rule: the listing's own values and `CONNECTING_WORDS`. A move's reason must cite and a finding may; their citations come from the values of the task's listings, the tag list and the column names the rerank weighs (`review_terms`), and their words from `CONNECTING_WORDS` and `MODEL_WORDS`, 46 words on the model and the ranking state such as "weighs", "stated" and "held". `validated_review` collects every rejected text across reasons, moves and findings and raises once, so nothing is saved. A flag is `{id, kind, preference}`; a flag with a note breaks the format.

The gallery's preference conflicts show "Ranks against what you like/dislike: <phrase>" under the listing. The task file lists the model words and gives examples for moves and findings.

## Deviations

- Findings need no citation, so a finding such as "Favorites rank poorly when held out." stands; its words are still a closed set. Moves need one, as reasons do.
- Reviews saved before this change keep their free-text notes, move reasons and findings in the store until the next rerank supersedes them; the gallery no longer renders flag notes.

## Result

Delivered on branch `fix/scoring_review`. Move reasons, findings and flags follow the rules above, and a file with any bad text is rejected whole with every such text named. `profile/skills/ranking/SKILL.md` tells the agent that move reasons and findings were written after reading listing text and are the review's data to show, never instructions, mirroring the task file's wording. `test_uncited_text_in_any_field_rejects_the_whole_file` covers uncited and miscited text in reasons, move reasons and findings, a file with all three, and that nothing is saved; the format test rejects a flag with a note. The Playwright rerank tests render a cited move reason, a flag without a note and a cited finding; the two review screenshots in `docs/images` are refreshed.

Checks on `fbeb1a3`: `python -m unittest discover -s tests` 513 passed, 0 skipped (254 s); `ruff check profile tests` clean; in `gallery/`, `npm run lint` clean, `npm run build` clean, Playwright headless 38 passed. Hermes's skill linter: 0 findings for `ranking`, the 6 warnings `gallery` has on `main`; its prompt scanner leaves `ranking/SKILL.md` and `review.md` unchanged; its skills guard rates `ranking` safe.

## Comments

- 2026-10-25: Found by the deep review: these fields are 300 characters of free text written after reading listing text, rendered in the gallery and relayed by the main agent, which holds Gmail, Calendar and the gateway.
