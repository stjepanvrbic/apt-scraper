---
title: Can the user dismiss a listing?
type: Decision
state: Done
priority: 2
assignee: Stjepan Vrbic
milestone:
parent:
blocked_by: []
related: []
---

## Question

Favorites only tell the ranking what the user likes. Should the user also be able to dismiss a listing, hiding it and teaching the ranking what they dislike? Blocks the ranking ticket.

## Options

- Dismiss action on every listing: hides it and counts as a negative signal. One more button, and the ranking learns dislikes as well as likes.
- Favorites only: simpler, but disliked listings stay in Rankings until the ranking pushes them down.

## Decision

Dismiss action on every listing.

## Reason

Favorites alone give the ranking no signal about dislikes, and rejected listings would stay in Rankings. The owner took the recommended option.

## Consequences

Spec Ranking and Gallery gain dismissals. `0001` and `0004` acceptance updated.
