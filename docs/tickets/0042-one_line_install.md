---
title: One-line install that ends in the agent-guided setup
type: Feature
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: [0028]
---

## Outcome

A new user installs with one command pasted into a terminal, on Windows or macOS, with nothing cloned by hand, and lands in the dashboard chat where the agent guides the rest of the setup.

## Acceptance

- [ ] Windows: one `irm <url> | iex` line installs Git if missing, clones or updates the repository into a fixed per-user location, and runs the installer from there. macOS: one `curl -fsSL <url> | bash` line does the same.
- [ ] Rerunning the same line updates the repository and the profile, and keeps the user's `config.yaml`, data and credentials.
- [ ] `README.md` leads with the two lines and nothing else a user must do before the chat opens; the clone-and-run path stays for development.
- [ ] A test runs each bootstrap against a fake `git` and a fake installer in temporary directories, for a fresh install and a rerun.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Installation and setup](../spec.md)

## Design

- The repository is private, so the raw URL needs the repository to be public or a token. The owner decides; the bootstrap is written for the public raw URL and the README says the repository must be public for the one-liner to work.
- The repository lives at `%LOCALAPPDATA%\apt-scraper\repo` on Windows and `~/Library/Application Support/apt-scraper/repo` on macOS, next to the gallery data.

## Deviations

## Result

## Comments
