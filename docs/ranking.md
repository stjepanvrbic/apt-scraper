# Listing ranking

The ranking learns the user's taste from a feature record of every listing. A record holds what a model read once from all the listing's photos and its text, and what code computes from the listing. This guide describes how records are made, how notes and stated preferences are read into the features they name, and how a rerank fits the scoring model on them.

## Extraction

Extraction is the one model read of a listing content that produces the model's part of its feature record. The [listing-features skill](../profile/skills/listing-features/SKILL.md) runs it.

### When a listing is read

A listing needs a record when the ranking ranks it, that is when it is shown in Rankings, or when the user labeled it: a favorite, a dismissal, a contact request, a note from the user or a pairwise answer. The features job and the scoring model share one definition of a labeled listing, `labeled` in `scoring.py`. A labeled listing keeps needing a record when it is no longer shown, because the ranking learns from it. Labeled listings are read first, then the others in the order they were first seen.

A record belongs to one listing content, identified by the content fingerprint the store computes. A listing whose content has not changed is never read again, however often the search finds it. A changed listing needs a new record, and its old one no longer counts.

### How it runs

The profile ships the Hermes cron job `apt-scraper-features`, named "Listing features", in `profile/cron/jobs.json`. The installers resume it. It runs every 10 minutes. Each run starts with `profile/scripts/features-run.py`, which hands over to `features.py run` on Hermes's own Python. That Python has Pillow, which Hermes lists among its base dependencies.

The run finds the listings waiting for a record and prepares each one, for at most 10 minutes. Listings it does not reach wait for the next run. Preparing a listing:

1. drops repeated photos: Zillow serves one photo at several sizes under one photo key (`fp/<key>-cc_ft_1536.jpg`, `fp/<key>-cc_ft_384.jpg`), and the widest is kept,
2. downloads the rest, 8 at a time, and leaves out a photo the site answers with 404 or 410, or that Pillow cannot read,
3. drops near duplicates: a photo whose 64-bit difference hash is within 6 bits of an earlier photo's,
4. numbers the remaining photos from 1 and tiles them into contact sheets of 3 by 3 tiles of 512 by 384 pixels on 1536 by 1152, each tile marked with its photo's number,
5. writes a task file for the subagent: the instructions, the listing as one JSON-encoded object, the full-resolution photo files, the tag list as the saved records left it, the preferences to judge, the record format and the file to write the record to.

A listing with a photo that cannot be downloaded for any other reason, such as a timeout, a block or a server error, is not prepared and waits for the next run, without counting as a failed extraction. After an hour of such failures, the next run extracts the listing without the failing photos, when any other photo loads. A listing none of whose photos loads for an hour stays waiting, and `features.py status` reports it with the time and reason. The prepared files live in `extraction/` next to the listing store, one directory per listing content. A run reuses a directory prepared earlier for the same content and preferences, and removes directories no waiting listing needs.

When a listing or a preference waits, the run prints one task per prepared listing, grouped into `delegate_task` calls the agent makes in order, and Hermes starts the agent with the report. The tasks run 10 to a call, Hermes's default `delegation.max_concurrent_children`. A wake holds at most 50 extractions, because Hermes blocks `delegate_task` once one turn has spawned 50 subagents (`tool_loop_guardrails.loop_caps.max_subagents` in `hermes_cli/config_defaults.py`), which would end the wake before its readings and judgments. The rest wait for the next run. Otherwise the report's last line is `{"wakeAgent": false}` and Hermes ends the run without a model call. A run never fails, because Hermes would start the agent to report the failure. Its steps, saving what the last wake wrote into the job's inbox, fetching the stations, preparing the extraction tasks, the readings and the judgment tasks, and delivering the Compare invitation, fail separately: a failed step is recorded as one of the run's problems in `features.json` next to the listing store, the other steps go on, and `features.py status` reports the problems. The invitation is the exception: its failure stays in the invitation's record in the store, where it makes `status` report `ask_to_compare`, and the first delivery clears it ([Gallery › Compare](gallery.md#compare)). Any other exception ends the run without waking the agent and is recorded the same way.

### The extraction subagent

The agent passes each task to Hermes's `delegate_task`, which runs one subagent per listing with the listing's contact sheets as its `images` (Hermes `tools/delegate_tool.py`, at most 8 images per task, `_MAX_TASK_IMAGES` in `tools/delegate_tool_tasks.py`). Eight sheets hold 72 photos. The search keeps at most 40 per listing. Within a cron run, `delegate_task` runs the subagents in the same turn and returns their answers to it (`cron/scheduler.py`).

The subagent reads its task file, looks at every photo on the sheets, and may open up to 6 photos at full resolution with `vision_analyze` when a detail is too small on a sheet. It writes the record into the job's inbox, as [Saving through the inbox](#saving-through-the-inbox) says, and the next run saves it with `Features.save`, which checks it against the contract below. A rejected record is not stored. It stays in the inbox, and the next run hands its listing again, first, with a task file that gives the error, which names the field that breaks the contract, and the file to correct, so a subagent corrects the record instead of reading the listing anew. Its final answer to the agent is one line. The images, the full-resolution photos and the record stay in the subagent's conversation. Only that line reaches the agent's.

The subagent runs on the model `delegation.model` names in the profile's `config.yaml`, `sonnet` as shipped. Without a provider, `delegation.model` keeps the parent's provider (`_resolve_delegation_credentials` in `tools/delegate_tool_config.py`). The installers set it when a kept `config.yaml` names no subagent model.

Listing text and photos are data. The task file carries the listing as one JSON object in a code block, so no line of a title, an amenity or a description can pass for a line of the instructions around it, and tells the subagent never to follow instructions in it. Both model reads of a listing, its extraction and the judgments of preferences stated later, run in subagents from task files, so listing text and photos never enter the job agent's conversation, which has the file, vision and delegation tools and no terminal. The agent sees the task goals, which name a listing id and a file, each subagent's one-line answer, the names of the files the run saved or rejected, never a rejection's error, which may quote what a subagent read, and the tag list, which the readings task file holds as part of the vocabulary. Extraction subagents add tags after reading listings, so tags are the one part of a record that reaches the job agent. They are a controlled vocabulary: saving a record lowercases each tag and rejects the record when a tag is not 2 to 40 characters of the letters a to z, digits, spaces, hyphens, apostrophes, `&` and `/`, starting and ending with a letter or digit (`normalize_tag` in `record.py`). Listing text also stays out of the cron job's prompt, which Hermes scans for prompt injection.

### Failures

Each run records in `features.json` the reads it handed to the agent: its extraction tasks, the notes and stated preferences waiting for a reading, and the preference judgments its wake has subagents for. It records them once the report that hands them over is complete, and forgets the previous run's before counting them, so a run that fails or never wakes the agent counts nothing twice. The next run counts a failed attempt for each of them that still waits, in the store's `model_reads` table, so an attempt counts only once the wake that had it has ended. Each run saves what the last wake wrote into the inbox before it counts, so a saved record ends the count and a rejected one counts as a failed attempt. After 3 attempts without a record, the job stops handing the listing over, `given_up_extractions` in `features.py` names it so the ranking can rank it from its code-derived features alone, and `features.py status` reports it, which the agent passes on to the user at the start of a conversation. A changed listing starts again from zero, and `features.py retry` clears every count.

### Preferences

Each stated like and dislike is judged for each listing as "has", "rules out" or "does not say", with its evidence. A listing's extraction judges the preferences stated at the time. A preference stated later is judged for every listing that has a record, from its record and text, without the photos. The job's run writes one task file per batch of 25 listings into `extraction/judgments/`, from `profile/skills/listing-features/judgment.md`, and its report holds one `delegate_task` task per batch, 10 to a call, which the agent makes after the readings. Each subagent reads its task file, judges its listings and writes the judgments into the inbox, and the next run saves them with `Features.judge`, which checks that each listing gets exactly its unjudged preferences. The run gives the judgments the subagents its extractions leave of the wake's 50 and hands over the judgments of the listings those batches hold, so an attempt counts only for a listing a subagent was given. A batch whose judgments file was rejected is handed again first, under its name, with the error. The rest wait for a later run. Until then, the listing counts as not saying.

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

The task file shows every tag with the number of listings whose current record uses it, most used first, as the saved records left the list when the run wrote the task. The records of one wake are saved by the next run, so the extractions of a wake do not see the tags they add to each other, and the next wake's do. The task file tells the subagent to use the listed tag that means the same thing over its own word, and to write a new tag only for something no listed tag comes close to. `features.py tags` prints the list.

Saving normalizes each trait and concern: lowercase with single spaces, the last word singular ("bay windows" becomes "bay window"), an unambiguous synonym replaced by its listed tag ("stainless appliances" becomes "stainless steel appliance", `SYNONYMS_BY_VARIANT`), and a spelling that differs from a known tag only by hyphens and spaces takes the known spelling ("walk in closet" becomes "walk-in closet"). Tags that become the same tag merge. A tag that only restates a rating, a value or a flag, such as "hardwood floors", "no pets" or "high ceilings" (`RESTATEMENTS`), is dropped. A new tag joins the list in the transaction that stores its record, so the next run's tasks show it.

### Code-derived features

Code computes what it reads reliably from the listing, such as the monthly price, size, unit level from unit labels and coordinates, whenever its content is saved, without a model call. They are in the store's `code_features` table, apart from the extraction, so neither overwrites the other. See `code_features` in [the gallery guide](gallery.md).

### Distances

A listing with coordinates also has the great-circle distance to the nearest rapid-transit station, with that station's name, and to each of the user's places, computed by `distances` in `code_features.py` from coordinates the store already holds. They are computed again for every listing when the places, the transit feed setting or the stations change. A listing without coordinates has them as unknown.

- **Places.** A place is a name and an address. When the interview saves places, `settings.py` geocodes each address the store has no coordinates for through the public Nominatim server, as `geodata.py` describes: one request a second, a User-Agent naming apt-scraper, and the result kept in the store's `geocodes` table by address, so an address is looked up once ([Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)). An address Nominatim finds nothing for, or Nominatim not answering, saves nothing, and the interview asks the user to describe the place again or tries later. The interview says back where each place was found, with "© OpenStreetMap contributors".
- **Stations.** The `transit_feed` setting names the city's GTFS schedule feed, Chicago's CTA feed by default. The `apt-scraper-features` job downloads it once per feed URL and keeps only its stations, in the store's `stations` table: every stop a rail, metro, light rail or monorail route serves (GTFS `route_type` 0, 1, 2 and 12, and the extended types 100 to 199, 400 to 499 and 900 to 999), counted at its parent station when it has one. CTA's feed gives its 143 L stations. GTFS has no route type for bus rapid transit, so no bus stop counts. A failed download is reported by `features.py status` and tried again after 6 hours, because the CTA feed is about 69 MB.

## Readings of notes and preferences

The scoring model learns through the features of the record, so each note the user writes on a listing and each stated like and dislike is read once by the model into the features it names. A stated preference is read into the record features that express it, each with a direction: the dislike "Carpet" is `floor:carpet` disliked. A preference no feature expresses, such as "Near a park", is read as naming none and is then judged per listing, as [Preferences](#preferences) says. A note is read into its sentiment about the listing, positive, negative, mixed or neutral, and the features it praises and criticizes.

The readings run in the wakes of the `apt-scraper-features` job: its run writes `extraction/readings.md`, from `profile/skills/listing-features/reading.md`, with the vocabulary, the stated preferences and every note without a reading, and wakes the agent for them. The agent reads them as that task file and the [listing-features skill](../profile/skills/listing-features/SKILL.md) say and writes the readings into the inbox, and the next run saves them with `Features.read`, or hands them back with the error. A preference is identified by its kind and wording, so a reworded preference is read again and an unchanged one never. A note is read once. A reading that fails three times is given up, as extractions are, and `status` reports it. Readings are in the store's `preference_readings` and `note_readings` tables, checked against the vocabulary below by `validate_preference_reading` and `validate_note_reading` in `scoring.py`.

The vocabulary a reading may name: `rating:<rating>` (toward 5), `floor:`, `style:`, `outdoor_space:` and `building_type:` with a value, `unit_level:` with garden, ground, middle or top, `flag:<flag>`, `tag:<tag>` for any tag of the tag list, `price`, `size`, `price_per_sqft`, `bedrooms`, `bathrooms`, `station_distance` and `place:<place>` for each place (toward more), and `area:<area>` for each area of the criteria. The like "Close to the L" is `station_distance` disliked.

## Scoring

### Reranks

Ranks change only on a rerank, which the user starts with Score and rerank in the gallery or by asking the agent. Both call `rerank(store, seed=None)` in `profile/skills/gallery/scripts/rerank.py`, which the ranking skill's `ranking.py rerank` runs. No scheduled job reranks. A rerank reads the store once (`ListingStore.ranking_inputs`), fits the scoring model in `scoring.py`, which is pure code with the standard library only, and writes every rank, reason, explanation and exploration mark, and the rerank's summary, in one transaction (`ListingStore.save_rerank`). It calls no model. The same transaction leaves the rerank's [review pass](#the-review-pass) waiting.

It ranks every listing shown in Rankings that has a feature record of its current content, and every one extraction gave up on, from its code-derived features alone, with a reason that says so. A shown listing waiting for its record gets no rank and the reason "Not ranked yet: waiting for its feature record." A listing that changes after the rerank keeps no rank until the next one. Among listings of equal score the listing id decides, so a rerank is reproducible apart from the exploration listings.

On this Windows machine, 300 listings with 50 favorites and about 170 features rerank in about 1.1 seconds of CPU time, store read and write included (`test_spec_rerank_timing_with_300_listings_and_50_favorites` in `tests/test_rerank.py`). Other work on the machine raises that CPU time, to about 1.5 seconds with one to sixteen busy processes and 1.8 seconds with every logical processor busy.

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
| `station_distance`, `place:<place>` | the code features' distances | kilometers to the nearest station and to each place |
| `near_favorites` | the coordinates | the mean Gaussian kernel, 1 km wide, of the favorites other than the listing itself. A mean, because a favorite has one favorite fewer to be near than any other listing, and a sum would count that against favorites |
| `preference:<phrase>` | the judgments of each stated preference read as naming no feature, or not read yet | 1 has, 0 rules out |

Days since first seen is not a feature: a favorite's age at rerank time says when the user favorited it, not what they like.

Numeric columns are centered and scaled to standard deviation 0.5, binary columns centered (Gelman et al. 2008, "A weakly informative default prior distribution for logistic and other regression models"). A value the listing does not state, such as a flag that is null, a category or rating "not shown", a size not given or a "does not say", is the column mean, so it counts neither way. A column with one value across the listings is left out.

### The model

A Bayesian logistic regression. Each listing in the fit is a row: a favorite is positive with weight 1, a contact request positive with weight 2, a dismissal negative with weight 1, and a listing whose latest note reading is positive or negative is labeled so, unless an action labels it. Every other ranked listing is a weak negative with a small weight. Each pairwise answer is a row on the two listings' difference, the chosen one above. Labeled listings that are no longer shown, such as an unavailable favorite, stay in the fit.

The prior on each weight is Gaussian with the same scale for all. Its mean is zero except where a reading names the feature: a stated like or dislike adds 1 or −1 to the features its reading names (or to its `preference:` column), a note's like or dislike adds 0.5 or −0.5, capped at ±2. Each area of the criteria starts at 1, 0.5 or 0.25 for high, medium or low priority, and `near_favorites` at the highest area priority. `station_distance` starts at −1, −0.5 or −0.25 by the priority of rapid transit, and each place's distance by the priority of places, so of two listings alike but for the distance to a place, the closer ranks higher until labels say otherwise (`DistanceTests` in `tests/test_scoring.py`). Labels can outweigh any of them: with the dislike "Carpet" and five carpeted favorites, the carpet weight becomes positive (`OverruledPreferenceTests` in `tests/test_scoring.py`).

Newton iterations with step halving find the posterior mode, and the Hessian there gives the Laplace posterior. The weak-negative weight is chosen among 0.05 and 0.1, then the prior scale among 1, 0.5 and 2, by the leave-one-out check below, each time the value with the best mean held-out rank of the favorites, the first on a tie. The choice uses an approximate check from each full fit: the refit below stops after its first two moves. With fewer than 3 favorites, the scale is 1 and the weight 0.05.

Most listings have few of the many tags, so the likelihood keeps tags and "not shown" columns uncentered and sparse, and the intercept's prior is nearly flat, so this changes no weight but the intercept.

### Explanations

A listing's score is the sum of its contributions `w_j (x_j − mean_j)`, exact for a linear model. Its stored explanation lists every column's contribution, largest first, with the value as its record states it, such as "natural light 5 of 5" or "garden unit (unit label G)", and the favorite it most resembles: the one whose contributions differ least from its own. Its reason is stored in parts (`compose_reason` in `scoring.py`): whether the listing is ranked from code features only, the values of the three largest contributions for it and the two largest against it whose value the record states, each list largest first, and the title of that favorite, such as `{"code_only": false, "features": {"for": ["garden unit (unit label G)", "exposed brick", "natural light 5 of 5"], "against": ["2,450 USD a month"]}, "similar_favorite": "2650 N Kedzie Ave"}`. The gallery shows each list with its values as separate items, so a value that holds a comma or names a place, such as "1.6 km to Logan Square station", reads as one item. A distance to a station names the station after its distance, and a count agrees with its number: "1 bathroom", "2 bathrooms".

### The leave-one-out check

For each favorite, the model is fitted again with the favorite as a listing without a label: its labels and pairwise answers are left out, it is a weak negative when it is ranked, as every other ranked listing without a label is, and `near_favorites` is computed without it. A held-out favorite stands for a listing the user has not seen. If the refit dropped its row instead, a model that gives each listing a weight of its own would leave the favorite's weight at its prior while the weak negatives pull every other listing below it, and the favorite would rank first on no signal (`HeldOutTests` in `tests/test_scoring.py`).

The refit starts from the full fit. One Newton step takes out the favorite's rows, with the full fit's Hessian less their curvature (the Woodbury identity), the usual approximate leave-one-out. The favorite's weak-negative row then moves the weights along that Hessian's solve of the row until the row's own score settles, which is one equation in one unknown. A second Newton step would overshoot instead, because the full fit is confident of a favorite's score, so the relabeled row's curvature there is near zero. Steps with that Hessian and the row's curvature follow until they are below 10⁻⁶. The favorite's held-out rank is one plus the number of ranked listings that are not favorites and score above it.

`fit_model` in `scoring.py` returns the fit alone, and `leave_one_out` runs this check on a fit. Only a rerank runs it: `rank_listings` composes the two, and the implicit signal diagnostic below runs it on its own fit. Pairwise questions cost a fit and no check. The rerank's summary stores every held-out rank, their median and mean, and `ranking.py status` prints them. A favorite with a poor held-out rank is one the others do not explain, which points to a feature the record lacks.

### Implicit signal diagnostic

Every rerank retains the ordinary `held_out`, `held_out_mean` and `held_out_median` report and adds an `implicit_signals` report with the same fields, `weak_labels`, `mean_rank_improvement` and `improves`. Positive improvement means lower, better held-out ranks. Without favorites, the means and comparison are null. Without usable weak labels, the diagnostic reuses the identical ordinary check. `ranking.py rerank` and `ranking.py status` report both; the displayed ranks, explanations, exploration choices and Compare questions continue to use only explicit labels and preferences.

The diagnostic fits a separate model at the ordinary model's chosen prior scale and weak-negative weight. It uses completed visits to current listing content, taking each listing's median visible dwell. A median of at most three seconds is a weak negative. A median of at least thirty seconds and at least twice the median across eligible listings is a weak positive. Each has weight 0.1, and intermediate visits add no label. Favorites, dismissals, contacts, user notes and pairwise answers exclude a listing from this calculation, including the threshold, so a held-out favorite's own interactions cannot leak into its check. Unfinished visits and visits to old content are excluded. Opens and photo views remain logged without being interpreted as preferences. These thresholds are diagnostic heuristics, not evidence that implicit signals improve this user's ranking.

`ranking_inputs(include_signals=True)` aggregates these visits in the rerank's snapshot. Other callers do not read the signal history. Raw events remain in the store even when the diagnostic excludes them. The diagnostic reuses the ordinary fit's feature design and starts from its weights, then converges separately with the weak labels and runs the leave-one-out check on that fit.

The solver uses the standard library's [math.sumprod](https://docs.python.org/3/library/math.html#math.sumprod), available since Python 3.12, for dot products. This keeps the second leave-one-out check within the rerank budget without a numerical package. The [Hermes runtime lock](https://github.com/NousResearch/hermes-agent/blob/main/pm/lock.json) supplies Python 3.14.9 on Windows and macOS; running from a checkout needs Python 3.12 or newer.

### Exploration

With more than 20 ranked listings, up to two exploration listings are placed at ranks 7 and 14, marked in the store's `ranking.exploration`, which the gallery shows as "Shown to learn your taste". They are drawn from the candidates: the ranked listings outside the top 18 by posterior mean that have no label from the user, no favorite, dismissal, contact request, note or pairwise answer. Each is drawn by posterior sampling: weights drawn from the Laplace posterior pick the highest-scoring candidate not drawn yet. With one candidate, only rank 7 holds an exploration listing, and with none, neither does. A listing of the top 18 is never one, because a listing the model already ranks high teaches it nothing about where it is wrong. The rerank's summary lists the exploration listings in `exploration`, and its length is how many were placed. The draws come from the rerank's seed, recorded in its summary, so a rerank with the same seed is reproducible, and with 20 or fewer ranked listings no seed changes anything.

### Pairwise questions

`pairwise_questions(store, count)` in `rerank.py` fits the model with the latest rerank's prior scale and weak-negative weight, without the leave-one-out check, and returns pairs among the 40 best-ranked listings whose order the posterior is least sure of: the probability that one scores above the other, from the Laplace posterior, is closest to one half. Each listing is in at most one question, and a pair answered either way is not asked again. A skipped pair is excluded until twenty other questions have been answered or skipped, persisted in `pairwise_skips`. Refreshing a question does not advance this cooldown. Answers are stored with `ListingStore.answer_pair(chosen, other)` and count at the next rerank. Before any label, the posterior is the prior from the stated preferences.

## The review pass

After each rerank, a model reviews its result once, as text. The review rewrites the reasons of the top 50 listings, flags each whose rank conflicts with a stated preference, reports what looks wrong in the ranking state, and may move listings. Its moves stand only when the leave-one-out check confirms them, because language-model rankers show position and popularity bias. The code is `review.py`, pure functions, and `pending_review` and `save_review` in `rerank.py`.

### How it runs

The profile ships the Hermes cron job `apt-scraper-review`, named "Ranking review", in `profile/cron/jobs.json`, and the installers resume it. It runs every minute. Each run starts with `profile/scripts/review-run.py`, which hands over to `ranking.py run`. The run first saves the review the last session wrote into the job's inbox. When the latest rerank's review still waits, the run writes its task file to `review/` next to the listing store, replacing earlier ones, and its report names the file. Otherwise its last line is `{"wakeAgent": false}` and Hermes ends the run without a model call. Hermes skips a job while its previous run is still registered as running (`try_register_running_job` in `cron/scheduler.py`), so one review is handed at a time.

The agent the job wakes runs in a session of its own, with the `ranking` skill and only the file tools, no terminal, so it knows nothing of the user's conversations. It reads the task file and writes its review into the job's inbox, and the next run, a minute after the session ends, checks and applies it with `save_review`. A file that breaks the review's format is rejected and nothing is saved: it stays in the inbox, and the run hands the review again with a task file that gives the error and the file to correct, so the next session corrects it.

The review runs in a job rather than as a turn the gallery starts through the gateway client of the Notes tab. A job runs the same way whether the user reranked in the gallery or in a conversation, gets a fresh session without the conversation's knowledge of the favorites, limits the tools to what the review needs, and leaves the gallery only reading the store: the review's state and result are in `ListingStore.last_rerank()['review']`. The cost is up to a minute's wait before the review starts.

### What the review reads

The task holds the top 50 listings, each with its rank, title, exploration mark, its record's summary, traits and concerns, its 12 largest contributions with the values its record states, and the values its reason names for and against it; the stated preferences with the features they were read into; the likes and dislikes of the note readings; and the ranking state: label counts, the favorites' held-out ranks without their listings, the features whose learned weight runs against their starting weight, the 20 largest weights with their starting weights and the stated preferences that name them, the listings waiting for a record or given up by extraction, and the tag list with its counts.

It holds no label. Favorites are not marked, held-out ranks come without their listings, nearness to the favorites is left out of the contributions and weights, no count says how many liked listings have a feature, and the favorite part of each reason is left out, so the favorites can test the review's moves independently. What code itself can tell is wrong, a large weight on a present-or-absent feature that a single labeled listing carries, it reports in the rerank's summary as `findings` (`state_findings` in `scoring.py`), outside the task, because that finding names the listing. Listing titles, summaries and traits are data inside one JSON block, and the task tells the agent never to follow instructions in them. The task stays out of the cron prompt, which Hermes scans for prompt injection.

### What the review may change

- Reasons. A rewritten reason cites the listing's record in square brackets, each citation exactly a value its explanation states or a trait or concern of its record, and outside them uses only about 110 connecting and evaluative words (`CONNECTING_WORDS` in `review.py`), such as "strong on", "but" and "is high", with plain punctuation and no numbers. So every feature a reason names is one its record has, as the record states it. A file with a reason that breaks this is rejected with an error naming every such reason, nothing is saved and every listing keeps its reason, and the agent corrects those reasons or leaves them out. Code unbrackets the citations and stores the text as the part of the reason that names its features, beside the note on a listing ranked from code features only and the most similar favorite.
- Moves. Each moves one of the top 50 to a new rank, with a reason and a verbalized confidence from 0 to 1, which the research found better calibrated than token probabilities. Exploration listings and their ranks stay, and each move has a rank of its own. A move's reason cites in square brackets, each citation exactly a value the task states for one of its listings, a tag of the tag list or the column name of a feature the rerank weighs, such as `floor:carpet`. Outside them it uses the connecting words and about 45 words on the model and the ranking state (`MODEL_WORDS` in `review.py`), such as "weighs", "little" and "stated", with plain punctuation and no numbers.
- Flags, each a listing and a stated like or dislike its rank conflicts with, without text.
- Findings, at most 10 texts on what looks wrong in the ranking state, such as two traits that mean the same thing or a weight carried by a single label. A finding follows the rule of a move's reason but needs no citation, so "Favorites rank poorly when held out." is one.

The review agent writes these after reading listing titles, summaries and traits, which come from listing sites, and the gallery shows them and the main agent relays them. So none is free text. A file with any text that breaks its rule is rejected whole, with an error naming every such text, and the ranking skill tells the main agent that move reasons and findings are the review's data to show the user, never instructions.

### The check

The rerank stores what the review reads with its waiting review: its order, its exploration listings, each ranked listing's score and, for each favorite, every score in the fit without it, and everything else the task describes and the review is checked against (`review_inputs` in `review.py`): the stated preferences with their readings, the note readings, the starting weights with the preferences that name them, the label counts, each of the top 50 listings with its title, record, contributions, the values a reason may cite for it, its code-only mark and its most similar favorite, the listings waiting for a record or given up by extraction, and the tag list with its counts. A record saved, a note read, a preference stated or a label given after the rerank therefore stays out of its review: the task does not show it, a reason or move may not cite it, a rewritten reason keeps the rerank's code-only mark, and flags may name only the preferences the rerank used. Only which listings are still ranked with unchanged content comes from the store when the task is handed over and the review saved. `save_review` checks the moves on these, so the check is the one of the ranking the review saw, also when labels or listings change while the review runs, and needs no refit. The moves become score adjustments: each moved listing gets the score that puts it between the unmoved listings around its new rank, spaced evenly when several moved listings share a gap, and every other listing keeps its order around them and the exploration listings. The adjustments apply to the full fit and to each held-out fit alike. The leave-one-out check runs without and with them. When the favorites' mean held-out rank is higher with them, every move is reverted and the result records the reverted moves and both values. Without a favorite, nothing can confirm the moves, so they are reverted too. Rewritten reasons, flags and findings stand either way. A listing whose content changed since the rerank keeps no rank, as after any rerank, and a reason for it is refused. A moved listing's explanation stays the scoring model's: its move and the move's reason are in the review's result.

The result is stored with the review and printed by `ranking.py status`: `outcome` (`kept`, `reverted` with `reverted_because`, or `no moves`), `check` with the held-out median and mean `before` and `after`, the `moves` with their ranks, reasons and confidences, the number of `reasons` rewritten, the `flags` and the `findings`.

### Failures

A review handed to the agent 3 times without being saved ends as `failed`, with the error of its last rejected file when it had one, and so does one whose task could not be written, with the error in its result. A later rerank supersedes a review still waiting, also while the agent writes it: the old review's file is then rejected and deleted. Either way the stored scores are removed, and the next run removes the task files of earlier reranks.

## Saving through the inbox

The agents the scheduled jobs wake, those of `apt-scraper-search`, `apt-scraper-features` and `apt-scraper-review`, and the subagents they start, run without the terminal, so a scheduled session can run no command. Each job's `enabled_toolsets` in `profile/cron/jobs.json` names the file tools (`read_file`, `write_file`, `patch`, `search_files`), plus vision and delegation for the features job, and `no_mcp`, which keeps the MCP servers of the user's `config.yaml` out (`_merge_mcp_into_per_job_toolsets` in Hermes's `cron/scheduler.py`). A subagent gets its parent's toolsets without delegation (`_resolve_child_toolsets` in `tools/delegate_tool_toolsets.py`). The file tools still read any file the user can, apart from Hermes's own credential stores (`get_read_block_error` in `agent/file_safety.py`), and write any file the user can outside Hermes's deny lists, the profile's scripts and skills included, which later runs execute (`agent/file_safety.py`, `tools/file_tools_write_guards.py`). `vision_analyze` in the features job downloads public image URLs (`tools/vision_tools.py`).

An agent saves a result by writing a JSON file into its job's inbox, `cache/inbox/<job>/` under the profile, under the name its task file gives. Hermes has no hook that runs a job's code after its session (a cron job has only the pre-run `script`, `cron/job_definition.py`), so the job's next run applies each file with the job's checks (`inbox.py` in the gallery's scripts): a file that passes is applied and deleted, and a rejected file saves nothing and stays where it is. The run hands its task again, first, with the error as a JSON string in a code block and the file to correct, so the next agent corrects the file instead of starting over. A rejected file whose task the run does not hand again, because its listing changed, its review was superseded or it was given up, is deleted. Hermes does not start a job while its previous run is registered as running (`try_register_running_job` in `cron/scheduler.py`), and `write_file` replaces a file atomically, so a run applies only files written whole. An agent Hermes abandoned after a timeout may still write one, which the run after applies or rejects.

An inbox file is untrusted input. The run applies only regular files named as tasks name them, and the job's checks test every id and fingerprint against the shapes of a stored listing's before using it in a path or key, so nothing an agent writes reaches the file system as a path. A folder at a file's name, which `write_file` creates for a path written below it, is removed and rejected, and its task is handed again with the path to write. A file with any other name is removed unread. Only a failure of the machine, such as a locked database or a disk error, stops a run at the inbox: it leaves the files for the next run and hands nothing meanwhile.

A correction therefore takes a run: a minute for the review, ten minutes for the features and an hour for the search. Attempts count per wake, so a rejected extraction, reading or judgment counts as a failed attempt, and a review is handed at most 3 times. A judgment or reading the user's changes made unnecessary after the run handed it, such as one of a listing that changed or a preference removed meanwhile, is left out of its file and the rest saved, without a rejection. A conversation saves must-have judgments through an inbox of its own, `cache/inbox/search-conversation/`, with `search.py tasks` and `search.py apply`, so it never changes the files of a wake.

## Commands

```bash
python "$HERMES_HOME/skills/listing-features/scripts/features.py" status
python "$HERMES_HOME/skills/listing-features/scripts/features.py" tags
python "$HERMES_HOME/skills/listing-features/scripts/features.py" retry
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" rerank
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" status
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" explain "<listing id>"
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" questions --count 8
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" run
```

`features.py run` and `ranking.py run` are the cron jobs' commands, which also save what the jobs' agents wrote into their inboxes. Every command but `run` needs only the Python standard library, and so does `ranking.py`.

## Checks

```powershell
python -m unittest discover -s tests -v
ruff check profile tests
```

`tests/test_feature_record.py` covers the record contract, `tests/test_listing_photos.py` the duplicate removal and the contact sheets on a 40-photo fixture, `tests/test_feature_store.py` the store, and `tests/test_features_run.py` the job against real SQLite stores: which listings wait, preparation, saving through the command line, preferences stated later, readings, attempts, failures and the shipped job. `tests/test_geodata.py` covers geocoding and reading stations from a GTFS feed, `tests/test_code_features.py` the distances in the store, and `tests/test_scoring.py` covers the scoring model on constructed listings, among them Spec › Acceptance "Overruled preference", "Location", "Exploration" and a single pairwise answer, and `tests/test_rerank.py` reranks against real SQLite stores, the 300-listing timing, as the least CPU time of three reranks, and the ranking script. `tests/test_review.py` covers the review pass with controlled reviews, among them Spec › Acceptance "Review pass": moves that put favorites lower are reverted with both check values, moves that put them higher stand, and a reason, a move's reason or a finding that cites a value the task lacks or uses another word outside its citations rejects the whole file. It also covers the label-free task, the format checks, the check on the rerank's own scores while labels and listings change, superseded reviews, the single-label finding, the job's wake gate and attempts, the command line and the shipped job.
