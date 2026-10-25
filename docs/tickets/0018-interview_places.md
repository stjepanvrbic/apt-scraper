---
title: Interview captures places and location priorities
type: Feature
state: In Progress
priority: 3
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: []
---

## Outcome

The interview captures the places the user travels to and what matters about them, and the location priorities, and the user changes them later by telling the agent.

## Acceptance

- [ ] The settings gain places, each with a name, an address or landmark precise enough to geocode, the way of getting there (walk, bike, transit or car), the longest accepted trip in minutes and how often the user goes, and location priorities: how much the places, nearness to rapid transit and each area of the criteria matter. The store validates both.
- [ ] `missing_settings()` lists them until they are answered. "No places" saves an empty list and counts as answered. A profile whose interview was complete before is asked only for these at its next conversation.
- [ ] A real `-Q` session runs the interview and stores both, and a later "I work at <new address> now" changes the stored place. The same change through the Notes tab is saved too.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Interview](../spec.md#interview), [Spec › Terms](../spec.md#terms) (Place, Location priorities), [Spec › Acceptance](../spec.md#acceptance) (Interview)

## Design

- Extend `SETTINGS` validation and `missing_settings` in `profile/skills/gallery/scripts/store.py`, the interview skill's "What to capture" and "Running the interview", `settings.py` and the README's "Your search".
- Importance as low, medium or high, so the user answers in words and the scoring model maps each to a prior strength.
- Places are search settings, used only for ranking. The contact work never shares them with listing agents.
- Geocoding the places belongs to the transit and travel-time ticket, not here.

## Deviations

## Result
