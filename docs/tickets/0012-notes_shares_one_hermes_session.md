---
title: Notes chat shares one Hermes session across gallery databases
type: Bug
state: Todo
priority: 3
assignee:
milestone:
parent:
blocked_by: []
related: [0001, 0002]
---

## Outcome

Each gallery database has its own Hermes session for the Notes chat, and a Notes turn works regardless of which working folder the session was first created in.

## Acceptance

- [ ] The sample database and the profile's real database get different Hermes sessions.
- [ ] A Notes turn succeeds after the folder the session was first created in no longer exists.

## Spec

[Spec › Gallery](../spec.md)

## Design

Found while verifying the interview: in Hermes 0.22.5 the gallery's `session.create` returns the same stored session for every gallery database and keeps the working folder from its first use, so Notes turns fail once that folder is gone. Cause not yet diagnosed.

## Deviations

## Result
