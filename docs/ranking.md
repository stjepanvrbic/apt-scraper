# Listing ranking

The ranking learns the user's taste from a feature record of every listing. A record holds what a model read once from all the listing's photos and its text, and what code computes from the listing. This guide describes how records are made, how notes and stated preferences are read into the features they name, and how a rerank fits the scoring model on them.

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

When a listing or a preference waits, the run prints one task per prepared listing, grouped into `delegate_task` calls the agent makes in order, and Hermes starts the agent with the report. While the store has fewer than 5 records, each call holds one task, so each of the first extractions sees the tags the ones before it added. The rest run 10 to a call, Hermes's default `delegation.max_concurrent_children`. A wake holds at most 50 extractions, because Hermes blocks `delegate_task` once one turn has spawned 50 subagents (`tool_loop_guardrails.loop_caps.max_subagents` in `hermes_cli/config_defaults.py`), which would end the wake before its readings and judgments. The rest wait for the next run. Otherwise the report's last line is `{"wakeAgent": false}` and Hermes ends the run without a model call. A run never fails, because Hermes would start the agent to report the failure: an exception is recorded in `features.json` next to the listing store, and `features.py status` reports it.

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

## Readings of notes and preferences

The scoring model learns through the features of the record, so each note the user writes on a listing and each stated like and dislike is read once by the model into the features it names. A stated preference is read into the record features that express it, each with a direction: the dislike "Carpet" is `floor:carpet` disliked. A preference no feature expresses, such as "Near a park", is read as naming none and is then judged per listing, as [Preferences](#preferences) says. A note is read into its sentiment about the listing, positive, negative, mixed or neutral, and the features it praises and criticizes.

The readings run in the wakes of the `apt-scraper-features` job: its run counts the notes and preferences without a reading and wakes the agent for them, and the agent reads them with `features.py readings` and saves them with `features.py read`, as the [listing-features skill](../profile/skills/listing-features/SKILL.md) says. A preference is identified by its kind and wording, so a reworded preference is read again and an unchanged one never. A note is read once. A reading that fails three times is given up, as extractions are, and `status` reports it. Readings are in the store's `preference_readings` and `note_readings` tables, checked against the vocabulary below by `validate_preference_reading` and `validate_note_reading` in `scoring.py`.

The vocabulary a reading may name: `rating:<rating>` (toward 5), `floor:`, `style:`, `outdoor_space:` and `building_type:` with a value, `unit_level:` with garden, ground, middle or top, `flag:<flag>`, `tag:<tag>` for any tag of the tag list, `price`, `size`, `price_per_sqft`, `bedrooms`, `bathrooms` (toward more), and `area:<area>` for each area of the criteria.

## Scoring

### Reranks

Ranks change only on a rerank, which the user starts with Score and rerank in the gallery or by asking the agent. Both call `rerank(store, seed=None)` in `profile/skills/gallery/scripts/rerank.py`, which the ranking skill's `ranking.py rerank` runs. No scheduled job reranks. A rerank reads the store once (`ListingStore.ranking_inputs`), fits the scoring model in `scoring.py`, which is pure code with the standard library only, and writes every rank, reason, explanation and exploration mark, and the rerank's summary, in one transaction (`ListingStore.save_rerank`). It calls no model.

It ranks every listing shown in Rankings that has a feature record of its current content, and every one extraction gave up on, from its code-derived features alone, with a reason that says so. A shown listing waiting for its record gets no rank and the reason "Not ranked yet: waiting for its feature record." A listing that changes after the rerank keeps no rank until the next one. Among listings of equal score the listing id decides, so a rerank is reproducible apart from the exploration listings.

On this Windows machine, 300 listings with 50 favorites and about 180 features rerank in about 1.4 seconds, store read and write included (`test_spec_rerank_timing_with_300_listings_and_50_favorites` in `tests/test_rerank.py`).

### Features

`design` in `scoring.py` turns each listing into columns:

| Columns | From | Value |
| --- | --- | --- |
| `rating:<rating>` | the record's 12 ratings | the value shrunk toward the mean by its confidence, `mean + confidence × (value − mean)` |
| `rating_not_shown:<rating>` | the same | 1 when the rating is "not shown" |
| `floor:`, `style:`, `outdoor_space:`, `building_type:` | the record's categories | one column per value, 1 for the listing's |
| `unit_level:garden`, `ground`, `middle`, `top` | the code features' level from the unit label, otherwise the record's | one column per level |
| `flag:<flag>` | the record's flags | 1 true, 0 false |
| `tag:<tag>` | the record's traits and concerns | 1 when the listing has it, for tags at least 2 listings have or a reading names |
| `price`, `size`, `price_per_sqft`, `bedrooms`, `bathrooms`, `photos` | the code features | log monthly price, log size, the others as they are |
| `area:<area>` | the criteria areas whose search found the listing | 1 for each |
| `near_favorites` | the coordinates | the mean Gaussian kernel, 1 km wide, of the favorites other than the listing itself. A mean, because a favorite has one favorite fewer to be near than any other listing, and a sum would count that against favorites |
| `preference:<phrase>` | the judgments of each stated preference read as naming no feature, or not read yet | 1 has, 0 rules out |

Days since first seen is not a feature: a favorite's age at rerank time says when the user favorited it, not what they like.

Numeric columns are centered and scaled to standard deviation 0.5, binary columns centered (Gelman et al. 2008, "A weakly informative default prior distribution for logistic and other regression models"). A value the listing does not state, such as a flag that is null, a category or rating "not shown", a size not given or a "does not say", is the column mean, so it counts neither way. A column with one value across the listings is left out.

### The model

A Bayesian logistic regression. Each listing in the fit is a row: a favorite is positive with weight 1, a contact request positive with weight 2, a dismissal negative with weight 1, and a listing whose latest note reading is positive or negative is labeled so, unless an action labels it. Every other ranked listing is a weak negative with a small weight. Each pairwise answer is a row on the two listings' difference, the chosen one above. Labeled listings that are no longer shown, such as an unavailable favorite, stay in the fit.

The prior on each weight is Gaussian with the same scale for all. Its mean is zero except where a reading names the feature: a stated like or dislike adds 1 or −1 to the features its reading names (or to its `preference:` column), a note's like or dislike adds 0.5 or −0.5, capped at ±2. Each area of the criteria starts at 1, 0.5 or 0.25 for high, medium or low priority, and `near_favorites` at the highest area priority. Labels can outweigh any of them: with the dislike "Carpet" and five carpeted favorites, the carpet weight becomes positive (`OverruledPreferenceTests` in `tests/test_scoring.py`).

Newton iterations with step halving find the posterior mode, and the Hessian there gives the Laplace posterior. The weak-negative weight is chosen among 0.05 and 0.1, then the prior scale among 1, 0.5 and 2, by the leave-one-out check below, each time the value with the best mean held-out rank of the favorites, the first on a tie. The choice uses one Newton step from each full fit, the usual approximate leave-one-out. With fewer than 3 favorites, the scale is 1 and the weight 0.05.

Most listings have few of the many tags, so the likelihood keeps tags and "not shown" columns uncentered and sparse, and the intercept's prior is nearly flat, so this changes no weight but the intercept.

### Explanations

A listing's score is the sum of its contributions `w_j (x_j − mean_j)`, exact for a linear model. Its stored explanation lists every column's contribution, largest first, with the value as its record states it, such as "natural light 5 of 5" or "garden unit (unit label G)", and the favorite it most resembles: the one whose contributions differ least from its own. Its one-line reason names the three largest contributions for it and the two largest against it whose value the record states, then that favorite: "For it: garden unit (unit label G), exposed brick, natural light 5 of 5. Against it: 2,450 USD a month. Most like your favorite 2650 N Kedzie Ave."

### The leave-one-out check

For each favorite, the model is fitted again without its labels and pairwise answers and with `near_favorites` computed without it. The refit starts from the full fit and uses the full fit's Hessian with the favorite's rows taken out (the Woodbury identity) until its steps are below 10⁻⁶. The favorite's held-out rank is one plus the number of ranked listings that are not favorites and score above it. The rerank's summary stores every held-out rank, their median and mean, and `ranking.py status` prints them. A favorite with a poor held-out rank is one the others do not explain, which points to a feature the record lacks.

### Exploration

With more than 20 ranked listings, ranks 7 and 14 hold exploration listings, marked in the store's `ranking.exploration` and in their reason ("Shown to learn your taste."). Each is drawn by posterior sampling: weights drawn from the Laplace posterior pick the highest-scoring listing outside the top 18 by posterior mean that has no label from the user, no favorite, dismissal, contact request, note or pairwise answer. The draws come from the rerank's seed, recorded in its summary, so a rerank with the same seed is reproducible, and with 20 or fewer ranked listings no seed changes anything.

### Pairwise questions

`pairwise_questions(store, count)` in `rerank.py` fits the model with the latest rerank's prior scale and weak-negative weight and returns pairs among the 40 best-ranked listings whose order the posterior is least sure of: the probability that one scores above the other, from the Laplace posterior, is closest to one half. Each listing is in at most one question, and a pair answered either way is not asked again. Answers are stored with `ListingStore.answer_pair(chosen, other)` and count at the next rerank. Before any label, the posterior is the prior from the stated preferences.

### Location features still to come

Distance to rapid transit and travel time to each place join as columns like `near_favorites`, with prior means from the location priorities: the code features gain the values, `raw_values` in `scoring.py` maps them to columns, and `prior_means` gives a place beyond its longest accepted trip a negative starting weight by the place's priority.

## Commands

```bash
python "$HERMES_HOME/skills/listing-features/scripts/features.py" status
python "$HERMES_HOME/skills/listing-features/scripts/features.py" tags
python "$HERMES_HOME/skills/listing-features/scripts/features.py" preferences
python "$HERMES_HOME/skills/listing-features/scripts/features.py" judge "$HERMES_HOME/cache/scratch/judgments.json"
python "$HERMES_HOME/skills/listing-features/scripts/features.py" save "$HERMES_HOME/cache/scratch/extraction-<listing>.json"
python "$HERMES_HOME/skills/listing-features/scripts/features.py" readings
python "$HERMES_HOME/skills/listing-features/scripts/features.py" read "$HERMES_HOME/cache/scratch/readings.json"
python "$HERMES_HOME/skills/listing-features/scripts/features.py" retry
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" rerank
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" status
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" explain "<listing id>"
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" questions --count 8
```

`run` is the cron job's command. `save` is the subagent's. Every command but `run` needs only the Python standard library, and so does `ranking.py`.

## Checks

```powershell
python -m unittest discover -s tests -v
ruff check profile tests
```

`tests/test_feature_record.py` covers the record contract, `tests/test_listing_photos.py` the duplicate removal and the contact sheets on a 40-photo fixture, `tests/test_feature_store.py` the store, and `tests/test_features_run.py` the job against real SQLite stores: which listings wait, preparation, saving through the command line, preferences stated later, readings, attempts, failures and the shipped job. `tests/test_scoring.py` covers the scoring model on constructed listings, among them Spec › Acceptance "Overruled preference", "Exploration" and a single pairwise answer, and `tests/test_rerank.py` reranks against real SQLite stores, the 300-listing timing and the ranking script.
