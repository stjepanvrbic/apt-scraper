---
title: Log in to Claude with a pasted setup token
type: Feature
state: Backlog
priority: 4
assignee:
milestone:
parent:
blocked_by: []
related: [0011]
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

A token in the `apt-scraper` profile `.env` never reaches Claude Code under Hermes 0.22.5, because the host gateway serves every profile from the default profile's environment. The owner deferred the ticket (`0011`). Its installer change, draft pull request #6, was closed unmerged: #59 replaced the installers it edited, so the token step is written against the current `install.sh` and `install.ps1` once the plugin reads the token from the profile's secret scope.

## Result
