# Listing ranking

Every listing in the gallery's Rankings has a rank and a one-line reason. The ranking follows the user's favorites, dismissals, notes and stated preferences. A favorite or a positive note raises the listings that resemble it, and a dismissal or a negative note lowers them, by their photos, price, description, location and amenities.

## How it runs

The profile ships the Hermes cron job `apt-scraper-ranking`, named "Listing ranking", in `profile/cron/jobs.json`. The installers resume it. It runs every two minutes, so a favorite, a dismissal or a new listing shows in the order within a few minutes.

Each run starts with `profile/scripts/ranking-run.py`, which hands over to `ranking.py run` in the [ranking skill](../profile/skills/ranking/SKILL.md). Before the interview is complete it ranks nothing. Otherwise it ranks every listing again in code, from what the store holds, and writes the whole ranking in one store transaction. A listing whose rank and reason did not change is not written, so an unchanged ranking leaves the gallery's revision alone. When nothing waits for analysis, the last line of its output is `{"wakeAgent": false}` and Hermes ends the run without starting the agent. A re-ranking after a favorite, a dismissal, an analyzed note or a read preference therefore makes no model call.

When something waits, the report lists one batch and Hermes starts the agent with it. The agent analyzes that batch, saves it with `ranking.py analyze`, which ranks again, and stops. The next run brings the next batch, so a run's conversation stays small even though the photos are images in its context. A batch holds up to 6 listings that need a profile, or else up to 25 listings that need only preference judgments, up to 25 notes, and every stated preference not read yet.

A run never fails, because Hermes starts the agent whenever a cron script fails (`cron/scheduler_prompt.py`, "Script Error"), which every two minutes would cost a model call each time. Every exception on the run path, including a listing store that cannot be opened, a corrupt store file or a store locked past its timeout, is caught. The failure is recorded in `ranking.json` next to the listing store, and `ranking.py status` reports it until a run succeeds.

A batch the agent cannot finish does not loop. `ranking.json` also counts, for each listing content, note and preference, how many runs handed it to the agent. After 3 runs without a saved analysis, it is no longer handed over and `status` reports it. A shown listing that never got a profile is then ranked by its price, size and rooms only, with a reason that says so. A listing whose content changes starts counting again, and `ranking.py retry` clears every count. One cause is a photo that no longer loads. Another is a listing text that trips Hermes's prompt-injection scan of the script output (`cron/scheduler_prompt.py`), which blocks the run and sends the failure to the user, at most 3 times for one listing. `profile/SOUL.md` has the agent check that status at the first message of each conversation. Hermes holds a fire claim on a job while it runs and refreshes it (`cron/jobs.py`, `claim_job_for_fire`, and `heartbeat_fire_claim` in `cron/scheduler.py`), so a run that takes longer than two minutes is not started twice.

## What the model analyzes

The model reads a listing only when something about it or about the user's preferences needs judging. Routine re-ranking and unchanged listings cost no model call. Good rankings come first, fewer model reads second.

- A listing's profile is a one-line summary, the neighborhood, and the terms of a fixed vocabulary that apply in four dimensions. Photos come from up to 3 of the listing's photos, looked at with Hermes's `vision_analyze`. Description, amenities and location come from the listing's text and address.
- A stated like or dislike is read once, as a phrase. When vocabulary terms express it faithfully, such as "balcony or terrace" for "Balcony", the reading holds those terms, and a listing has the preference when its profile has one of them. Such a new preference, from the interview or the Notes tab, costs one model read, not one per listing.
- A preference the vocabulary would only approximate, such as a playground nearby when the vocabulary has only "near a park", or cannot express, such as walking distance to a named station, is judged per listing, true, false or unknown, because a loose mapping would rank listings wrongly. Its reading stores the reason, and `ranking.py status` lists each such phrase with that reason under `judged_per_listing`. A new listing gets these judgments in the same read as its profile. When such a phrase is added, each shown listing is judged on it from its text and stored profile, without the photos, and keeps its rank meanwhile.
- A note from the user is read once: its sentiment, positive, negative, mixed or neutral, and the vocabulary terms it liked or disliked. Notes written by the agent are not read.

The vocabulary is `VOCABULARY` in `profile/skills/ranking/scripts/scoring.py`. `ranking.py pending` prints it with every batch, and `analyze` refuses any other term, so the profiles of different listings use the same words and code can compare them.

A listing needs a profile when it is shown in Rankings, or when it teaches the ranking: a favorite, a dismissed listing or a listing with a note from the user, whether or not it is still shown. A preference the user removed stops counting.

When the user has must-haves in `criteria.other`, the search reads a new listing's text once to judge them, before the listing is shown, and the ranking reads it once for its profile after it is shown.

## How listings are ranked

The ranking is computed in `scoring.py` from the stored analysis:

- Each favorite and each positive note counts +1 for its listing, each dismissal and each negative note −1, at most ±2 a listing. Listings with a positive total are positive examples and listings with a negative total negative ones.
- The similarity of two listings is a weighted mean over the dimensions both have: shared photo, description, amenity and location terms, each term weighted by how rare it is among the analyzed listings, the neighborhood as a location term, the monthly price, the size and the bedrooms of the cheapest unit.
- A listing's affinity to the examples is its mean similarity to the 3 closest of them.
- The traits of the positive examples are the terms at least two of them share and that are more common among them than among all analyzed listings. Each trait is valued by that lift times the share of examples that have it, so a term every favorite has counts most. A listing's trait share is the part of that value its own terms cover. The negative examples have traits the same way.
- A listing's score is its affinity to the positive examples minus its affinity to the negative ones, plus its trait share of the positive examples minus its trait share of the negative ones, plus 0.15 for each stated like it has and minus 0.15 for each stated dislike, plus 0.1 for each term a note liked and minus 0.1 for each term a note disliked. A favorite gets 0.5 more and is not compared with itself.
- Listings are ranked by score, then newest first.

The reason names the two strongest parts of a listing's score: the traits it shares with the favorites or with the turned-down listings, the favorite, noted listing or turned-down listing it resembles most and up to three things they share, the likes or dislikes it has, or the terms the notes liked or disliked. A listing with none of these shows its summary. A dismissed, unavailable or excluded listing has no rank, and its reason says why.

A shown listing waiting for its first profile has no rank yet and shows "Awaiting ranking" until a run analyzes it, within minutes when only a few wait. Every shown listing has a rank and a reason once the analysis has caught up.

## Stored data

The ranking keeps `profile` and `preferences` in a listing's features, next to the search's `facts` and `must_haves`. `ListingStore.analyze` merges the given keys into the current features, so the search and the ranking never overwrite each other. Note readings are in the store's `note_readings` table and preference readings, by phrase, in `preference_readings`. `ListingStore.rerank` writes the ranking. See [the gallery guide](gallery.md) for the store interface.

## Commands

```bash
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" status
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" pending
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" analyze "$HERMES_HOME/cache/scratch/analysis.json"
python "$HERMES_HOME/skills/ranking/scripts/ranking.py" retry
```

`run` is the cron job's command. All commands need only the Python standard library.

## Checks

```powershell
python -m unittest discover -s tests -v
ruff check profile tests
```

`tests/test_ranking_scoring.py` covers the ranking itself: favorites, dismissals, notes, preferences, reasons and the vocabulary. `tests/test_ranking_run.py` covers the job against real SQLite stores: batches, analysis files, re-ranking without waking the agent, preference readings, notes, failures, the attempt limit and the shipped job.
