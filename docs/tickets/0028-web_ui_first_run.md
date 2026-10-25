---
title: The installer ends in the Hermes web dashboard, where the agent runs the setup
type: Feature
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: [0005, 0006, 0007, 0018]
---

## Outcome

After `install`, the user is in the Hermes web dashboard's chat for the `apt-scraper` profile, not a terminal, and the agent itself runs the account connections (Gmail, Calendar, the messaging gateway) and the interview from there. The user signs in and clicks consent screens; the agent does everything else.

## Acceptance

- [ ] `install.ps1` and `install.sh` finish by starting the Hermes web dashboard for the profile and opening it in the user's browser. The dashboard's web build is produced by the installer, so a later `hermes -p apt-scraper dashboard` needs no npm.
- [ ] `README.md` tells the user to use the dashboard, with the command to reopen it, and no longer leads with `hermes -p apt-scraper` in a terminal.
- [ ] The first conversation in the dashboard offers the connections and the interview as `profile/SOUL.md` says; the agent runs every command and opens every page of the Google and gateway setup itself and asks the user only to sign in and approve. Verified on a throwaway profile with a headless browser against the dashboard, with screenshots in the pull request.
- [ ] The account-connections skill works when the user is in the dashboard rather than a terminal: nothing it does assumes a TTY, and `hermes gateway setup`'s interactive wizard is driven by the agent, not the user.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Installer](../spec.md), [Spec › Setup](../spec.md)

## Design

- `hermes -p <profile> dashboard` routes to one machine dashboard with the profile preselected, serves on 127.0.0.1:9119 and has a Chat tab. `--skip-build` serves an existing `web/dist`.
- Agents verifying this never open the owner's browser: the dashboard is started with `--no-open` and read headlessly.

## Deviations

## Result

## Comments
