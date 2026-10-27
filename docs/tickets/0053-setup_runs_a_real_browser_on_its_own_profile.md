---
title: The account setup runs the user's real browser on its own profile, attached over CDP
type: Bug
state: In Progress
priority: 1
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: [0052]
---

## Outcome

The account setup drives a full instance of the user's own browser binary (Brave, Chrome, Edge or Chromium, whichever is the default) running on a profile folder that belongs to apt-scraper, attached through the Chrome DevTools Protocol. Google and Telegram sign-in work in that window, the user signs in once, the user's main browser and its profile are never copied or closed, and nothing needs the foreground.

## Acceptance

- [ ] Observed on the owner's machine: with Brave open, the real-profile copy of 0052 was unavailable, Hermes fell back to its bundled Chrome for Testing, and Google refused the sign-in with "Couldn't sign you in. This browser or app may not be secure." The setup cannot complete on that path. After the fix the setup never uses Chrome for Testing or a profile copy.
- [ ] `connections.py browser on` launches the default browser's real binary, found the way Hermes finds it, on a user-data directory under the profile (`$HERMES_HOME/apt-scraper/browser`), with a remote debugging port bound to 127.0.0.1 only, as a visible window, with no automation flags that mark the browser as controlled; then sets `browser.cdp_url` for the profile to that endpoint. `browser off` clears `browser.cdp_url` and closes that browser instance; the user-data directory stays, so the sign-ins persist across setups. The existing record, lock, 30-minute stale rule and contact-job gate from 0052 apply to this key instead of `use_real_profile`, `headed` and `real_profile_autoclose`, which are no longer set anywhere.
- [ ] Hermes's browser tools in the dashboard chat act in that window through the CDP endpoint (verify on a throwaway `HERMES_HOME` with a throwaway user-data directory that `browser_navigate` and `browser_snapshot` reach it, and that `navigator.webdriver` is not true and the user agent names the real browser, not "Chrome for Testing" or "HeadlessChrome"). Google's own sign-in can only be confirmed by the owner; say so.
- [ ] The account-connections skill drops the profile-copy and close-Brave text and describes the window: it is the user's own browser, signed out at first, the user signs in to Google and Telegram there once, and later setups reuse it. The user is never asked for a password in the chat.
- [ ] Without a supported default browser, the skill tells the user to install Brave, Chrome or Edge and stops, instead of falling back to Chrome for Testing.
- [ ] README, spec and the ticket 0052 wording are updated to this design.
- [ ] Tests in the existing style: the launch command (binary, user-data dir, port, flags, no automation flag), the config key set and cleared, the instance closed on `off`, the gate and stale rule on the new key; no test opens the owner's browser or a real window (use a fake binary).
- [ ] Merged to `main` through a reviewed pull request.

## Spec

docs/spec.md, Installation and setup: the setup works in a window of the user's own browser that apt-scraper opens on a profile of its own.

## Design

Hermes attaches its browser tools to any CDP endpoint given in `browser.cdp_url` (hermes-agent `tools/browser_tool_cdp.py`, docs `website/docs/user-guide/features/browser.md`, "Local Chromium-family browser via CDP"); Chrome 136+ ignores a debugging port on the default profile, so the dedicated user-data directory is required and also what keeps the user's own profile untouched. Google blocks sign-in in Chrome for Testing and in browsers that advertise automation; a real binary launched with only `--remote-debugging-port` and `--user-data-dir` does not advertise it. Hermes's `browser_connect.py` and `/browser connect` show how it finds the binaries on each platform and launches them; the skill's script does the same, under the profile's lock, because `/browser connect` is a CLI-only command. The window is the agent's: it opens once per setup and closes at `browser off`.

Verification must not open the owner's browser or touch its profile: a fake binary for the tests, a throwaway user-data directory and a throwaway `HERMES_HOME` for the CDP check, headless where a window is not needed.
