---
title: Feature records extracted from every photo and the text
type: Feature
state: Done
priority: 2
assignee: Stjepan Vrbic
milestone:
parent: 0015
blocked_by: []
related: [0014]
---

## Outcome

Every new or changed listing gets a validated feature record read from all its photos and its text, once per content, without its photos entering the agent's main conversation.

## Acceptance

- [x] Code removes duplicate photos, the same photo key at another size and near duplicates by perceptual hash, and tiles the rest into numbered contact sheets. A fixture listing with 40 photos and duplicates gets sheets that show every unique photo once.
- [x] One extraction child per listing sees every sheet and opens at most 6 photos at full resolution. Its stored record has a room type for every unique photo, and the ratings, flags, traits and preference judgments of Spec › Listing features.
- [x] A record that breaks the feature record contract is rejected with the reason, and nothing is stored.
- [x] A new trait joins the tag list, and the next extraction is given the list with it.
- [x] A stated preference the record cannot express is judged in the listing's extraction. One added later is judged for every listing from its stored record and text, without the photos.
- [x] A new listing has its record within an hour of being found. Searching an unchanged listing again makes no extraction, and the agent's main session holds no image, both checked in Hermes's `state.db`.
- [ ] After three failed extractions of one content, the listing is ranked from its code-derived features, its reason says so, and `status` reports it.
- [x] Extraction runs on Sonnet through `delegation.model`. On the same 20 real listings, its records are compared field by field with the profile's main model, and agreement and token use are in Result. Where they disagree on fields that change ranks, the owner decides the model before this closes.
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
- Extraction stays on Sonnet. The owner decided on 2026-10-25 from the comparison in Result: Sonnet agrees with Opus nearly as well as with itself, at about half the cache-write tokens. The garden unit Opus read from photos and Sonnet did not is checked again in the five-favorites experiment.
- The tag list is shared and grows during a run: it starts from seed tags of common apartment traits and concerns, the child reads the live list just before choosing its tags, the first extractions of a new store run one at a time, and saving normalizes traits, so the same thing gets the same tag in every listing.
- Tests: duplicate removal, tiling and validation on fixtures, the job's gate against real SQLite stores. Live: a separate test profile on real Zillow listings, sessions with `-Q`.

## Deviations

- The child saves its own record with `features.py save`, which validates it against the contract in code and names the field that breaks it, so the child corrects it and saves again. No `output_schema`: the record never passes through the agent's conversation, the agent copies no JSON, and code checks what a JSON Schema cannot, such as one room type for every photo index and judgments of exactly the asked preferences.
- The record has its own table, `feature_records`, one row per listing with the fingerprint it describes, next to 0017's `code_features`, instead of a key in the search's `features` JSON. The search and extraction cannot overwrite each other, and the migration is version 4, after 0017's.
- Every stated like and dislike is judged per listing, because which preferences the record cannot express is decided by the preference readings of 0020. When those exist, `stated_preferences` in `features.py` narrows to the preferences without a reading.
- "The listing is ranked from its code-derived features, its reason says so" needs the ranking of 0020. This ticket provides `given_up_extractions(store)` for it, and `status` reports the given-up listings.
- The main cron agent keeps the `vision` toolset, because Hermes gives a child no tool its parent lacks (`_resolve_child_toolsets`, `tools/delegate_tool_toolsets.py`). The skill and `SOUL.md` forbid it to open photos, and the report holds only paths. The 6-photo limit on full-resolution opens is an instruction to the child, not enforced by code.
- The profile's main model is `sonnet` in the shipped and the installed `config.yaml`, so the comparison ran Sonnet against Opus, the frontier model the research assumed for the parent, plus a second Sonnet run as a noise floor.

## Result

Delivered on branch `feat/listing_feature_extraction`, pull request #18 to `main`, not merged: migration 4 (`feature_records`, `tags`, `model_reads`), the record contract in `profile/skills/gallery/scripts/record.py`, the `listing-features` skill (`photos.py`, `features.py`, the child's task template `extraction.md`), the cron job `apt-scraper-features` every 10 minutes with `profile/scripts/features-run.py`, `delegation.model: sonnet` in the shipped config and in the installers when a kept config names no subagent model, additive `SOUL.md`, README and installer sections, `docs/ranking.md` and the store interface in `docs/gallery.md`. Tech debt fixed: `test_code_features` built its pre-migration store with `MIGRATIONS[:-1]`, which broke once a later migration existed. It now cuts at `code_derived_features`.

Checks at the delivered revision: `python -m unittest discover -s tests`: 343 passed, 0 skipped. `ruff check profile tests` clean. Hermes's skill linter (`tools/skill_linter.py`): no findings for `listing-features`. Its skills guard (`tools/skills_guard.py`): verdict safe, no findings. Its context scanner (`_scan_context_content` in `agent/prompt_builder.py`) passes `SOUL.md`, the skill and the task template, and its cron prompt scanner (`_scan_cron_prompt` in `tools/cronjob_prompt_scan.py`) passes the job prompt. New tests: `test_feature_record.py` (contract), `test_listing_photos.py` (40-photo fixture: 34 unique, 3 repeated keys at another size, 3 recompressed copies; each sheet tile matched back to its photo by hash), `test_feature_store.py`, `test_features_run.py` (gate, preparation, command-line save, rejection, tag list, later preferences, attempts, failing run, shipped job).

Fixed after review:

- Listing text could forge a section of the task file: a description with a line `-----` followed by `## Saving` rendered outside the data block. The task file now carries the listing as one JSON object in a code block, so every listing line is part of a JSON string, and the preference batches carry the same object. Test: a description, title and amenity with forged headings, fences and commands leave the template's sections and fences unchanged. Live, in a throwaway profile: a listing whose description forged a "## Saving" section asking to run a command that writes a marker file was extracted and saved, the subagent ran only `read_file`, `tags`, `write_file` and `save` (`state.db`), and no marker file was written.
- `judge` saved the valid entries of a rejected file. It now validates every entry, verdicts included, before writing, and the store writes all of them in one transaction. Test: a file with one invalid verdict saves nothing, and the corrected file saves both.
- A judgment reached its third attempt only on paper: the run counted it before the wake, and `preferences` then hid it. A run now records what it hands over, and the next run counts a failed attempt for each handed read that still waits. Test: three wakes each get the judgment, the fourth run does not wake, and `status` reports it.
- Listings whose photos kept failing to download waited unreported. After an hour of failures, a listing is extracted without the failing photos when another photo loads, and a listing with no loadable photo is reported in `status` problems with the time and reason, which exits 3. Tests for both, with the clock moved past the hour.
- Also: a flag of `1` or `1.0` is rejected, the summary must be one line, and the skill's description has under 60 characters. The skill's scratch path comes from `scratch_directory()` in `scratch.py`, shared with `read_scratch_object`.

Each new test fails at the reviewed revision `9c74c0a` and passes now.

Live, on this Windows machine with Hermes 0.22.5 in a throwaway profile installed from this branch, on 20 real Zillow listings in Logan Square, Chicago (211 photos):

- Duplicates: 211 photos became 205 unique. The 6 dropped were exact re-uploads under other keys (hash distance 0). No pair of distinct photos came within 10 bits, so the 6-bit threshold has margin.
- Preparing 20 listings took 19 s. Under the scheduler (`cron tick` every minute), listings shown at 18:27:37 UTC all had records by 18:37:59, from the run due at 18:36:44, in two `delegate_task` calls of 10 and 8 children.
- Every record has a room type for each unique photo, all 12 ratings, the categories, unit level with evidence, 10 flags, traits, concerns and a judgment of each of the 5 stated preferences. Room types of one listing checked against its sheet: all 17 match.
- `state.db`: each child session's first message carries its sheets (`[Image attached at: …]`). The cron session holds no image hint, no `vision_analyze` call and no image read in any of 5 runs. Sonnet opened no photo at full resolution; Opus opened 3 in 20 listings.
- Searching again re-read all 20 listings and saved identical content. The next run's gate returned `wakeAgent=false` and no child session started.
- A like added afterwards ("Quiet street") was judged for all 20 listings by the agent from records and text in 45 s, with no child session and no image.

Sonnet against Opus on the same 20 listings, with a second Sonnet run for comparison:

| Field | Sonnet vs Opus | Sonnet vs Sonnet |
| --- | --- | --- |
| Room type (205 photos) | 92.7% | 95.6% |
| Ratings, exact (240) | 77.1% | 84.2% |
| Ratings, within 1 | 100% | 100% |
| Ratings, mean difference | 0.22 | 0.16 |
| Rating shown or not | 97.1% | 98.3% |
| Floor, style, outdoor space, building type | 95, 90, 85, 85% | 85, 90, 95, 95% |
| Unit level | 80% | 90% |
| Flags (200) | 88.0% | 94.0% |
| Preference verdicts (100) | 83% | 87% |
| Traits, mean Jaccard | 0.31 | 0.24 |
| Concerns, mean Jaccard | 0.14 | 0.18 |

Least stable fields: `room_size` (60% Sonnet vs Opus), `condition` (70%), `furnished` (45%), `parking` (75%). Unit level, which ranks garden units, differs on 4 of 20: on `zillow:2067836598` Opus reads a garden unit (windows just above grade) and both Sonnet runs read ground; on the other three Opus reads a unit label (`#3F`, `3217-105`) that 0017's code features already read.

Trait agreement between runs was 0.24 to 0.31 (concerns 0.14 to 0.18), because ten subagents started at once from an empty tag list and each named the same thing its own way. Fixed in this ticket:

- 72 seed tags (`SEED_TAGS` in `record.py`) are part of the tag list from the start.
- The child runs `features.py tags` just before choosing its tags, so it reads the list as it is then, including tags added by extractions that finished meanwhile. The task file tells it to use the listed tag that means the same thing over its own word.
- While the store has fewer than 5 records, the job puts one task in each `delegate_task` call, so each early extraction sees the tags of the ones before. The rest run 10 to a call.
- Saving normalizes traits and concerns: lowercase, last word singular, unambiguous synonyms to their listed tag, hyphen and space variants to the known spelling, and tags that only restate a rating, value or flag dropped.

Measured again on the same 20 listings, found again by a fresh search, with two Sonnet runs that each started from a store with no records and no added tags:

| Field | Before | After |
| --- | --- | --- |
| Traits, mean Jaccard | 0.24 | 0.73 |
| Concerns, mean Jaccard | 0.18 | 0.64 |
| Room type | 95.6% | 97.1% |
| Ratings, exact | 84.2% | 87.9% |
| Flags | 94.0% | 93.5% |
| Unit level | 90% | 90% |
| Preference verdicts | 87% | 89% |

Each run used 31 or 32 distinct traits over about 105 uses, and added 1 or 2 tags to the 72 seeds. `state.db` of the first run: 5 single-task calls in sequence, then calls of 10 and 5. Every child ran `tags` and saw its sheets, and the cron session holds no image. A run of 20 took 3 min 20 s to 3 min 26 s, against 95 s with all children in parallel. Per child: 382 input, 1,926 output, 56,200 cache read and 13,325 cache write tokens, 4 API calls, in the first run.

Tokens per listing before the tag fix (child averages): Sonnet 379 input, 44,792 cache read, 7,587 cache write, 1,901 output, 3 API calls; Opus 380 input, 42,074 cache read, 15,123 cache write, 2,159 output, 4 API calls. The cron agent per run of 20: about 5,300 output and 59,000 to 77,000 cache read. Wall time for 20: Sonnet 95 s, Opus 114 s.
