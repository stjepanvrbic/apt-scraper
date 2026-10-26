---
title: Installers build and check the ranking environments
type: Feature
state: Canceled
priority: 3
assignee:
milestone:
parent: 0015
blocked_by: [0025]
related: []
---

## Outcome

`install.ps1` and `install.sh` build every Python environment the ranking needs, the embedding environment included, and check that each loads its packages, on Windows and macOS.

## Acceptance

- [ ] The embedding environment is built in its own virtual environment from requirements pinned with hashes, CPU wheels on Windows and Apple Silicon wheels on macOS, with the model weights downloaded and checked by hash. A rerun rebuilds it only when its Python or requirements changed.
- [ ] The installers check every environment the profile's scripts run in: the search environment loads `curl_cffi`, the Python that runs extraction loads Pillow, and the embedding environment embeds a test image and a test sentence. A failed check stops the installer with a message naming the environment.
- [ ] A rerun keeps `config.yaml` and the user's data.
- [ ] A full installer run passes on this Windows machine. The macOS run needs a Mac, so the owner runs it, and its output is in Result.
- [ ] The README states the download size and time. Merged to `main` through a reviewed pull request.

## Spec

[Spec › Installation and setup](../spec.md#installation-and-setup), [Spec › Acceptance](../spec.md#acceptance) (Installer)

## Design

Follow the search environment: `profile/scripts/search-run.py install` with the Python `hermes pm env` lists, and a rebuild only on change. The weights live in the workspace next to the environment, outside the installed profile, so a profile reinstall keeps them.

## Deviations

## Result

## Comments

- 2026-10-25: Canceled. The embedding path stays out, so the ranking runs on the standard library and the installers already build and check the search environment.
