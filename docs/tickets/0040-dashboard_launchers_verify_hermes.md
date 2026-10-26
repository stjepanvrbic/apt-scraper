---
title: The dashboard launchers verify they reached Hermes before opening the chat
type: Bug
state: Todo
priority: 3
assignee:
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

The login launcher and the shortcut on both platforms open the chat only when Hermes's dashboard answers at the address the installer recorded, never a foreign listener.

## Acceptance

- [ ] Both launchers probe `GET /api/health` and accept only a 200 whose JSON has `"ok": true` and a `version`; anything else starts Hermes or reports failure.
- [ ] The installer writes the dashboard address it learned from Hermes's log to a file the launchers read, instead of a fixed port 9119. `tests/test_installers.py` covers a foreign 404 listener and a moved port.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Installation and setup](../spec.md)

## Design

## Deviations

## Result

## Comments

- 2026-10-25: Found by the deep review: `install.ps1` and `install.sh` launchers treat any HTTP response on port 9119 as readiness; a foreign 404 opened the chat path without Hermes.
