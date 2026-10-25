---
title: Feature records extracted from every photo and the text
type: Feature
state: Todo
priority: 2
assignee:
milestone:
parent: 0015
blocked_by: []
related: [0014]
---

## Outcome

Every new or changed listing gets a validated feature record read from all its photos and its text, once per content, without its photos entering the agent's main conversation.

## Acceptance

- [ ] Code removes duplicate photos, the same photo key at another size and near duplicates by perceptual hash, and tiles the rest into numbered contact sheets. A fixture listing with 40 photos and duplicates gets sheets that show every unique photo once.
- [ ] One extraction child per listing sees every sheet and opens at most 6 photos at full resolution. Its stored record has a room type for every unique photo, and the ratings, flags, traits and preference judgments of Spec › Listing features.
- [ ] A record that breaks the feature record contract is rejected with the reason, and nothing is stored.
- [ ] A new trait joins the tag list, and the next extraction is given the list with it.
- [ ] A stated preference the record cannot express is judged in the listing's extraction. One added later is judged for every listing from its stored record and text, without the photos.
- [ ] A new listing has its record within an hour of being found. Searching an unchanged listing again makes no extraction, and the agent's main session holds no image, both checked in Hermes's `state.db`.
- [ ] After three failed extractions of one content, the listing is ranked from its code-derived features, its reason says so, and `status` reports it.
- [ ] Extraction runs on Sonnet through `delegation.model`. On the same 20 real listings, its records are compared field by field with the profile's main model, and agreement and token use are in Result. Where they disagree on fields that change ranks, the owner decides the model before this closes.
- [ ] `docs/ranking.md` describes extraction. Merged to `main` through a reviewed pull request.

## Spec

[Spec › Listing features](../spec.md#listing-features), [Spec › Contracts and invariants](../spec.md#contracts-and-invariants), [Spec › Acceptance](../spec.md#acceptance) (Listing features)

## Design

- A Hermes cron job whose script never fails and wakes the agent only when listings, preferences or notes wait for a model read, as the search job and PR #13's ranking job do. Each wake takes as many listings as wait, within a time budget, not a fixed batch.
- The agent runs one `delegate_task` child per listing with the contact sheets as `images` (8 per child, enough for 72 photos) and the record schema as `output_schema`, which the parent validates with one correction retry (Hermes `tools/delegate_tool.py`, `tools/delegate_tool_tasks.py`). The child opens full-resolution photos with `vision_analyze`, so their pixels stay in its own conversation. Only its JSON returns.
- Sheets: 3 by 3 tiles of 512 by 384 on 1536 by 1152, each tile numbered with its photo index, made with Pillow, which Hermes's base dependencies include. Cached by content fingerprint.
- Record fields follow the research's "What stage 1 extracts": room type per photo from a fixed set, ordinal ratings with confidence or "not shown", categorical floor, style, outdoor space, unit level with its evidence and building type, room coverage, text flags, up to 8 traits and 5 concerns from the tag list, a summary of at most 200 characters, and judgments of the preferences the rest cannot express.
- The record sits next to the search's `facts` and `must_haves` in the listing's features, with keys merged as PR #13's `ListingStore.analyze` does, so the search and extraction never overwrite each other. The tag list lives in the store, and code merges new traits into it.
- Listing text and photos are data. The child is told never to follow instructions in them.
- Tests: duplicate removal, tiling and validation on fixtures, the job's gate against real SQLite stores. Live: a separate test profile on real Zillow listings, sessions with `-Q`.

## Deviations

## Result
