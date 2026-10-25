---
title: The gallery launcher test writes into the real secrets directory
type: Bug
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: [0003]
---

## Outcome

No test touches `~/.secrets/`. `tests/test_gallery_launcher.py` runs against a temporary secrets directory, and the launcher takes that directory from one place that tests can point elsewhere.

## Acceptance

- [ ] `tests/test_gallery_launcher.py` creates nothing under the real `~/.secrets/apt-scraper/gallery/`, and two test runs at once do not collide.
- [ ] The launcher's secrets location is resolved in one function, used by the code and overridden by the tests.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Gallery](../spec.md#gallery)

## Design

## Deviations

## Result

## Comments

- 2026-10-25: Found while implementing the distance features: overlapping test runs hit "Permission denied" on the real directory.
