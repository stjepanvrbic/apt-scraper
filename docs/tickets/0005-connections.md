---
title: Account connections and checks
type: Feature
state: Todo
priority: 2
assignee:
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

The first run connects Gmail, Google Calendar, browser access and computer use, plus iMessage on macOS, and reports each connection's check.

## Acceptance

- [ ] Each connection has a check that passes on a connected account and fails with a clear message otherwise.
- [ ] A channel whose check fails is not used.

## Spec

[Spec › Installation and setup](../spec.md)

## Design

A Hermes setup skill the agent runs after install, using Hermes connectors for Google and the Hermes iMessage skill on macOS.

## Deviations

## Result
