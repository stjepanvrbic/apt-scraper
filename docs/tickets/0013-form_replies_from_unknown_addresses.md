---
title: Match contact-form replies that come from an unknown address
type: Feature
state: Backlog
priority: 4
assignee:
milestone:
parent:
blocked_by: []
related: [0006]
---

## Outcome

A reply to a contact-form submission reaches the right listing even when it comes from an address the store has not seen and does not keep the first message's subject.

## Acceptance

- [ ] A reply from an address not stored on any contact is matched to its listing when the content identifies the listing, and is otherwise reported as unmatched rather than filed under the wrong listing.

## Spec

[Spec › Contact and viewings](../spec.md)

## Design

Contact-form replies are matched today by the agent's known email address or the first email's subject. Searching on the listing's street address was considered and deferred because it may pull in unrelated site emails.

## Deviations

## Result
