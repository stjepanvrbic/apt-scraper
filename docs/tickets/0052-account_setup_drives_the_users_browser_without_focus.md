---
title: The account setup drives the user's own browser without needing the foreground
type: Bug
state: In Progress
priority: 1
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: [0027, 0028]
---

## Outcome

The Google and Telegram parts of the account setup run in the user's own browser, with their logins, through Hermes's browser tools, while the user keeps chatting in the dashboard and keeps using the mouse. Nothing in the setup depends on a window being in the foreground, and the setup continues from what is saved after a reinstall.

## Acceptance

- [ ] Observed on the owner's first setup (Windows, Brave as default browser): the `account-connections` skill drove the Google Cloud console with `computer_use` in the user's browser. Typing from the background was blocked, typing in the foreground minimized the window, and the agent gave up and handed the user a click-by-click list. After the fix the skill never uses `computer_use` for a web page.
- [ ] The profile turns on Hermes's real-profile browsing and headed mode (`browser.use_real_profile`, `browser.headed`): Hermes snapshots the default browser's active profile (Chrome, Edge, Brave, Chromium) into its own copy, launches the real browser binary on it in a visible window, and the agent acts in that window with `browser_navigate`, `browser_click`, `browser_type`, `browser_snapshot` and the rest. The user sees every page and does the sign-in and consent clicks there. Set the keys in `profile/config.yaml` and, because a reinstall keeps the user's `config.yaml`, also from both installers when unset, as `delegation.model` is set.
- [ ] The Google section (project, APIs, consent screen, publish, client, download, authorization) and the Telegram section (BotFather, userinfobot, the bot's chat, through https://web.telegram.org) are rewritten for the browser tools. The one-time authorization address is read with `browser_snapshot` or the tab list, not from a screenshot. The bot token is never captured: the skill keeps its rule about screenshots and snapshots of the BotFather chat.
- [ ] Windows: a running browser locks its cookie database, so the first snapshot needs the browser fully quit. Hermes then falls back to a fresh browser and reports `real_profile_unavailable`. The skill handles both without stopping: it tells the user the window is signed out and asks them to sign in there, or offers to close their browser and reopen the dashboard afterwards with `browser.real_profile_autoclose`. The user is never sent to a terminal or asked to type a password in the chat.
- [ ] `computer_use` remains only for what no browser can do: macOS permission dialogs, privacy settings and a terminal program. The skill says so, and the Windows setup needs none of them.
- [ ] After `hermes profile install --force` through the installer, the saved interview and the connection results are still there and the next conversation continues the setup from them: `settings.py status` and `connections.py status` read what the previous install saved. Verify on a throwaway profile, and make the installer or the skill resume rather than restart.
- [ ] A Google Cloud project `apt-scraper` that the earlier attempt may have created is reused, not duplicated: the project step checks the picker first.
- [ ] Tests cover the skill's rules (no `computer_use` on web pages, the browser keys set by both installers) with the existing test style; no test drives a real Google or Telegram page, opens the owner's browser, or snapshots the owner's browser profile.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

docs/spec.md, Installation and setup: the setup is guided by the agent in the dashboard chat; the user does only what needs their own hands. Add that the agent works in the user's browser through Hermes's browser tools, in a window it opens with the user's logins, and that nothing in the setup needs the user to keep a window in front.

## Design

Hermes 0.22.5 offers three lanes beside computer use: its headless Chromium (no logins), `browser.cdp_url` or `/browser connect` to a browser started with a debugging port (needs a non-default user-data dir, so no logins either), and `browser.use_real_profile`, which copies the user's active profile, launches the user's own browser binary on the copy and drives it through agent-browser. The real-profile lane is the one that carries the user's Google and Telegram logins, works with Brave, Chrome and Edge, and keeps the user's live browser and the dashboard chat untouched. Read `website/docs/user-guide/features/browser.md` in the installed Hermes (`%LOCALAPPDATA%\hermes\hermes-agent`) for its contract, including the Windows first-snapshot lock, `real_profile_autoclose`, `hermes browser close-profile` and the `real_profile_unavailable` result. Hermes's browser extension protocol has no shipped extension, so it is not an option.

The interview state lives in the listing store outside the profile and the connection results in `$HERMES_HOME/apt-scraper/connections.json`, which a distribution reinstall leaves alone; resuming is the first-message `status` calls in SOUL.md.

Verification must not touch the owner's browser, browser profile or Hermes profile: no real-profile snapshot on this machine, no Google or Telegram page, no `computer_use`. Hermes experiments run on a throwaway `HERMES_HOME` under `%TEMP%` with the headless Chromium or stubs.
