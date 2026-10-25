# Listing ranking

The ranking learns the user's taste from a feature record of every listing. A record holds what a model read once from all the listing's photos and its text, and what code computes from the listing. This guide describes how records are made. Scoring and reranks build on them.

## Extraction

Extraction is the one model read of a listing content that produces the model's part of its feature record. The [listing-features skill](../profile/skills/listing-features/SKILL.md) runs it.

### When a listing is read

A listing needs a record when the ranking ranks it, that is when it is shown in Rankings, or when the user labeled it: a favorite, a dismissal, a contact request or a note from the user. A labeled listing keeps needing a record when it is no longer shown, because the ranking learns from it. Labeled listings are read first, then the others in the order they were first seen.

A record belongs to one listing content, identified by the content fingerprint the store computes. A listing whose content has not changed is never read again, however often the search finds it. A changed listing needs a new record, and its old one no longer counts.

### How it runs

The profile ships the Hermes cron job `apt-scraper-features`, named "Listing features", in `profile/cron/jobs.json`. The installers resume it. It runs every 10 minutes. Each run starts with `profile/scripts/features-run.py`, which hands over to `features.py run` on Hermes's own Python. That Python has Pillow, which Hermes lists among its base dependencies.

The run finds the listings waiting for a record and prepares each one, for at most 10 minutes. Listings it does not reach wait for the next run. Preparing a listing:

1. drops repeated photos: Zillow serves one photo at several sizes under one photo key (`fp/<key>-cc_ft_1536.jpg`, `fp/<key>-cc_ft_384.jpg`), and the widest is kept,
2. downloads the rest, 8 at a time, and leaves out a photo the site answers with 404 or 410, or that Pillow cannot read,
3. drops near duplicates: a photo whose 64-bit difference hash is within 6 bits of an earlier photo's,
4. numbers the remaining photos from 1 and tiles them into contact sheets of 3 by 3 tiles of 512 by 384 pixels on 1536 by 1152, each tile marked with its photo's number,
5. writes a task file for the subagent: the instructions, the listing as one JSON-encoded object, the full-resolution photo files, the command that prints the tag list, the preferences to judge and the record format.

A listing with a photo that cannot be downloaded for any other reason, such as a timeout, a block or a server error, is not prepared and waits for the next run, without counting as a failed extraction. After an hour of such failures, the next run extracts the listing without the failing photos, when any other photo loads. A listing none of whose photos loads for an hour stays waiting, and `features.py status` reports it with the time and reason. The prepared files live in `extraction/` next to the listing store, one directory per listing content. A run reuses a directory prepared earlier for the same content and preferences, and removes directories no waiting listing needs.

When a listing or a preference waits, the run prints one task per prepared listing, grouped into `delegate_task` calls the agent makes in order, and Hermes starts the agent with the report. While the store has fewer than 5 records, each call holds one task, so each of the first extractions sees the tags the ones before it added. The rest run 10 to a call, Hermes's default `delegation.max_concurrent_children`. Otherwise the report's last line is `{"wakeAgent": false}` and Hermes ends the run without a model call. A run never fails, because Hermes would start the agent to report the failure: an exception is recorded in `features.json` next to the listing store, and `features.py status` reports it.

### The extraction subagent

The agent passes each task to Hermes's `delegate_task`, which runs one subagent per listing with the listing's contact sheets as its `images` (Hermes `tools/delegate_tool.py`, at most 8 images per task, `_MAX_TASK_IMAGES` in `tools/delegate_tool_tasks.py`). Eight sheets hold 72 photos. The search keeps at most 40 per listing. Within a cron run, `delegate_task` runs the subagents in the same turn and returns their answers to it (`cron/scheduler.py`).

The subagent reads its task file, looks at every photo on the sheets, and may open up to 6 photos at full resolution with `vision_analyze` when a detail is too small on a sheet. It writes the record to a file in the profile's scratch directory and saves it with `features.py save`, which checks it against the contract below. A rejected record is not stored and the error names the field that breaks the contract, so the subagent corrects it and saves again. Its final answer to the agent is one line. The images, the full-resolution photos and the record stay in the subagent's conversation. Only that line reaches the agent's.

The subagent runs on the model `delegation.model` names in the profile's `config.yaml`, `sonnet` as shipped. Without a provider, `delegation.model` keeps the parent's provider (`_resolve_delegation_credentials` in `tools/delegate_tool_config.py`). The installers set it when a kept `config.yaml` names no subagent model.

Listing text and photos are data. The task file carries the listing as one JSON object in a code block, so no line of a title, an amenity or a description can pass for a line of the instructions around it, and tells the subagent never to follow instructions in it. The batches of `features.py preferences` carry it the same way. Listing text stays out of the cron job's prompt, which Hermes scans for prompt injection.

### Failures

Each run records in `features.json` the reads it handed to the agent: its extraction tasks and the preference judgments waiting. The next run counts a failed attempt for each of them that still waits, in the store's `model_reads` table, so an attempt counts only once the wake that had it has ended. A saved record ends the count. After 3 attempts without a record, the job stops handing the listing over, `given_up_extractions` in `features.py` names it so the ranking can rank it from its code-derived features alone, and `features.py status` reports it, which the agent passes on to the user at the start of a conversation. A changed listing starts again from zero, and `features.py retry` clears every count.

### Preferences

Each stated like and dislike is judged for each listing as "has", "rules out" or "does not say", with its evidence. A listing's extraction judges the preferences stated at the time. A preference stated later is judged for every listing that has a record, from its record and text, without the photos: the job's report counts the listings waiting, and the agent judges them in batches of 25 with `features.py preferences` and `features.py judge`. Until then, the listing counts as not saying.

## The feature record

### The model's part

| Field | Values |
| --- | --- |
| `photos` | One entry per unique photo: its number, its URL and its room type: kitchen, bathroom, bedroom, living, dining, exterior, view, outdoor space, laundry, floor plan, building amenity or other. |
| `ratings` | Natural light, window size, ceiling height, room size, layout openness, kitchen finish, kitchen size, bathroom finish, condition, renovation recency, storage and view, each a value from 1 to 5 with a confidence from 0 to 1, or "not shown". |
| `floor`, `style`, `outdoor_space`, `building_type` | Hardwood, carpet, tile or vinyl, mixed. Modern, vintage, industrial, plain. None, shared, private balcony, private yard. House, townhouse, low-rise, mid-rise, high-rise. Each also "not shown". |
| `unit_level` | Garden or basement, ground, middle, top or "not shown", with the evidence. |
| `flags` | In-unit laundry, laundry in the building, dishwasher, air conditioning, central air, parking, pets allowed, utilities included, elevator and furnished: true, false, or null when the listing does not say. |
| `traits`, `concerns` | At most 8 and 5 tags from the tag list, or new tags that join it. |
| `summary` | One line of at most 200 characters. |

The judgments of the stated preferences are kept next to the extraction, by phrase.

A record is stored only when it keeps this contract, checked by `validate_extraction` in `profile/skills/gallery/scripts/record.py`: every unique photo has exactly one room type from the set, ratings are 1 to 5 or "not shown" with confidences from 0 to 1, every value is from its set, every rating and flag is present, and each asked preference is judged.

### The tag list

The tag list is the seed tags in `SEED_TAGS` of `record.py`, 72 common apartment traits and concerns, plus every tag an extraction added, in the store's `tags` table. A tag is 2 to 40 lowercase letters, digits, spaces, hyphens, apostrophes, & or /.

The subagent runs `features.py tags` after it has looked at the listing, just before it chooses tags. The command prints every tag with the number of listings whose current record uses it, most used first, as the list is at that moment, including tags that extractions running alongside added. The task file tells the subagent to use the listed tag that means the same thing over its own word, and to write a new tag only for something no listed tag comes close to.

Saving normalizes each trait and concern: lowercase with single spaces, the last word singular ("bay windows" becomes "bay window"), an unambiguous synonym replaced by its listed tag ("stainless appliances" becomes "stainless steel appliance", `SYNONYMS_BY_VARIANT`), and a spelling that differs from a known tag only by hyphens and spaces takes the known spelling ("walk in closet" becomes "walk-in closet"). Tags that become the same tag merge. A tag that only restates a rating, a value or a flag, such as "hardwood floors", "no pets" or "high ceilings" (`RESTATEMENTS`), is dropped. A new tag joins the list in the transaction that stores its record, so the next extraction sees it.

### Code-derived features

Code computes what it reads reliably from the listing, such as the monthly price, size, unit level from unit labels and coordinates, whenever its content is saved, without a model call. They are in the store's `code_features` table, apart from the extraction, so neither overwrites the other. See `code_features` in [the gallery guide](gallery.md).

## Commands

```bash
python "$HERMES_HOME/skills/listing-features/scripts/features.py" status
python "$HERMES_HOME/skills/listing-features/scripts/features.py" tags
python "$HERMES_HOME/skills/listing-features/scripts/features.py" preferences
python "$HERMES_HOME/skills/listing-features/scripts/features.py" judge "$HERMES_HOME/cache/scratch/judgments.json"
python "$HERMES_HOME/skills/listing-features/scripts/features.py" save "$HERMES_HOME/cache/scratch/extraction-<listing>.json"
python "$HERMES_HOME/skills/listing-features/scripts/features.py" retry
```

`run` is the cron job's command. `save` is the subagent's. Every command but `run` needs only the Python standard library.

## Checks

```powershell
python -m unittest discover -s tests -v
ruff check profile tests
```

`tests/test_feature_record.py` covers the record contract, `tests/test_listing_photos.py` the duplicate removal and the contact sheets on a 40-photo fixture, `tests/test_feature_store.py` the store, and `tests/test_features_run.py` the job against real SQLite stores: which listings wait, preparation, saving through the command line, preferences stated later, attempts, failures and the shipped job.
