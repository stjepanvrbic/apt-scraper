---
title: Log in to Claude with a pasted setup token
type: Feature
state: In Progress
priority: 4
assignee: agent
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

The installer asks the user for a token from `claude setup-token`, stores it as `CLAUDE_CODE_OAUTH_TOKEN` for the `apt-scraper` profile, and the agent authenticates with it instead of a `claude auth login` session.

## Acceptance

- [ ] On a machine with no Claude login, pasting a setup token lets the installer's test message pass.
- [ ] The token is stored only in the profile's Hermes `.env`, never in the repository or the installer output.

## Spec

[Spec › Installation and setup](../spec.md)

## Design

The plugin accepts `CLAUDE_CODE_OAUTH_TOKEN` from Hermes' environment. The token lasts one year and only makes model requests.

## Deviations

## Result
