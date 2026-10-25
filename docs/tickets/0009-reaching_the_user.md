---
title: How does the agent reach the user outside the gallery?
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

A listing agent asks for personal information or proposes a time while the gallery is closed. Where does the user see the question? Blocks the contact ticket.

## Options

- The gallery only: no setup, but questions wait until the user opens it, and viewings can be lost.
- An iMessage to the user's own number on macOS and an email on Windows: no new app, but it mixes with the user's own messages.
- A Hermes messaging gateway such as Telegram: immediate and separate, with one more account to set up.

## Decision

A Hermes messaging gateway such as Telegram.

## Reason

Questions reach the user while the gallery is closed, and they stay apart from the user's own messages. The owner took the recommended option.

## Consequences

Spec setup and Contact name the gateway. `0005` and `0006` acceptance updated.
