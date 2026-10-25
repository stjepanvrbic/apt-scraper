---
name: ranking
description: "Rerank the listings and explain their ranks."
version: 1.0.0
author: Stjepan Vrbic
license: Proprietary
platforms: [windows, macos]
metadata:
  hermes:
    tags: [apt-scraper, Ranking, Listings]
    related_skills: [listing-features, gallery, interview]
---

# Listing ranking

Ranks change only when the user asks: with Score and rerank in the gallery, or by asking you. A rerank fits the scoring model in code on the listings' feature records, the user's favorites, contact requests, dismissals, notes and pairwise answers, and their stated likes and dislikes. It ranks every listing shown in Rankings that has a feature record, writes a one-line reason and a full explanation for each, places two exploration listings in the top 20, and checks itself: each favorite's held-out rank is where the model ranks it when fitted without it. It takes about a second and calls no model.

## When to Use

- The user asks to rerank, to update the ranking, or to apply what they favorited, dismissed or wrote: run `rerank`.
- The user asks why a listing ranks where it does: run `explain` with its id.
- The user asks how well the ranking fits their favorites: run `status`.

## The ranking script

```bash
python "${HERMES_SKILL_DIR}/scripts/ranking.py" rerank
python "${HERMES_SKILL_DIR}/scripts/ranking.py" status
python "${HERMES_SKILL_DIR}/scripts/ranking.py" explain "<listing id>"
python "${HERMES_SKILL_DIR}/scripts/ranking.py" questions
```

Each prints JSON and exits 0. Any other exit code means the script failed: show the user its output.

`rerank` prints `ranked`, the listings ranked, `waiting_for_features`, the shown listings not ranked yet because their feature record is still being made, `held_out`, each favorite's held-out rank with its title, `held_out_median` and `held_out_mean`, and `exploration`, the two listings shown to learn the user's taste. Tell the user in two or three lines: how many listings are ranked, the held-out median against the number of ranked listings that are not favorites, and that two listings in the top 20 are there to learn their taste. A favorite with a held-out rank far below the others is one the model cannot place from the rest; say which, as a hint that it differs from the other favorites or that its record misses what the user liked in it.

`status` prints `last_rerank`, the latest rerank's summary with `finished_at`, or null before the first, and `not_ranked`: `new_or_changed`, the shown listings with a feature record found or changed since, and `waiting_for_features`. When the user asks whether to rerank, say how many are new or changed.

`explain` prints the listing's `rank`, `reason`, `exploration` and `explanation`: its `score`, every feature's `contribution` with its `value` as the listing's record states it, largest first, and the `similar_favorite`. The contributions add up to the score. Explain from these, citing only the values given. A rank of null means the listing changed or appeared since the last rerank.

`questions` prints the pairwise questions the model is least sure of, each the two `listings` and the model's `probability_first_above`. Answered pairs are excluded. A skipped pair is excluded until twenty other questions have been answered or skipped, including across restarts. The gallery's Compare tab asks them.

## Changing what the ranking weighs

When the user wants the ranking to weigh something, it is a preference: load the `interview` skill and save it there. The listing features job reads it within the hour into the record features it names, and the next rerank applies it. A favorite or a dismissal counts at the next rerank without any reading. Favorites can outweigh a stated preference: when the favorites keep having something the user said they dislike, it stops counting against listings.
