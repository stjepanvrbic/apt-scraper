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

- [x] Observed on the owner's machine: with Brave open, the real-profile copy of 0052 was unavailable, Hermes fell back to its bundled Chrome for Testing, and Google refused the sign-in with "Couldn't sign you in. This browser or app may not be secure." The setup cannot complete on that path. After the fix the setup never uses Chrome for Testing or a profile copy.
- [x] `connections.py browser on` launches the default browser's real binary, found the way Hermes finds it, on a user-data directory under the profile (`$HERMES_HOME/apt-scraper/browser`), with a remote debugging port bound to 127.0.0.1 only, as a visible window, with no automation flags that mark the browser as controlled; then sets `browser.cdp_url` for the profile to that endpoint. `browser off` clears `browser.cdp_url` and closes that browser instance; the user-data directory stays, so the sign-ins persist across setups. The existing record, lock, 30-minute stale rule and contact-job gate from 0052 apply to this key instead of `use_real_profile`, `headed` and `real_profile_autoclose`, which are no longer set anywhere.
- [x] Hermes's browser tools in the dashboard chat act in that window through the CDP endpoint (verify on a throwaway `HERMES_HOME` with a throwaway user-data directory that `browser_navigate` and `browser_snapshot` reach it, and that `navigator.webdriver` is not true and the user agent names the real browser, not "Chrome for Testing" or "HeadlessChrome"). Google's own sign-in can only be confirmed by the owner; say so.
- [x] The account-connections skill drops the profile-copy and close-Brave text and describes the window: it is the user's own browser, signed out at first, the user signs in to Google and Telegram there once, and later setups reuse it. The user is never asked for a password in the chat.
- [x] Without a supported default browser, the skill tells the user to install Brave, Chrome or Edge and stops, instead of falling back to Chrome for Testing.
- [x] README, spec and the ticket 0052 wording are updated to this design.
- [x] Tests in the existing style: the launch command (binary, user-data dir, port, flags, no automation flag), the config key set and cleared, the instance closed on `off`, the gate and stale rule on the new key; no test opens the owner's browser or a real window (use a fake binary).
- [ ] Merged to `main` through a reviewed pull request.

## Spec

docs/spec.md, Installation and setup: the setup works in a window of the user's own browser that apt-scraper opens on a profile of its own.

## Design

Hermes attaches its browser tools to any CDP endpoint given in `browser.cdp_url` (hermes-agent `tools/browser_tool_cdp.py`, docs `website/docs/user-guide/features/browser.md`, "Local Chromium-family browser via CDP"); Chrome 136+ ignores a debugging port on the default profile, so the dedicated user-data directory is required and also what keeps the user's own profile untouched. Google blocks sign-in in Chrome for Testing and in browsers that advertise automation; a real binary launched with only `--remote-debugging-port` and `--user-data-dir` does not advertise it. Hermes's `browser_connect.py` and `/browser connect` show how it finds the binaries on each platform and launches them; the skill's script does the same, under the profile's lock, because `/browser connect` is a CLI-only command. The window is the agent's: it opens once per setup and closes at `browser off`.

Verification must not open the owner's browser or touch its profile: a fake binary for the tests, a throwaway user-data directory and a throwaway `HERMES_HOME` for the CDP check, headless where a window is not needed.

## Deviations

- `browser on` also sets `browser.allow_private_urls` to `true`, and `browser off` sets it back to `false` right after clearing `browser.cdp_url`. With a CDP endpoint, Hermes 0.22.5 treats the browser as one on another computer (`_is_local_backend()` is false), so its SSRF rule refused the dashboard's Channels page on 127.0.0.1, the `localhost:1` page where Google's authorization ends, and even a snapshot of `about:blank`. Seen live on the throwaway profile. The setup's browser runs on the same computer as the terminal, so the rule protects nothing there. Under the profile home override that gateway turns use, both settings follow `config set` without a restart (checked with `_allow_private_urls()` and `_get_cdp_override_raw()`). Neither key is in `profile/config.yaml` or the installers, and the installer test checks that.
  - `browser.allow_private_urls` is also Hermes's global private-address switch, not a browser-only one: `tools/url_safety.py` (lines 144 to 176) reads it as the legacy form of `security.allow_private_urls`. While the setup's browser is on, private-address protection is off for the whole apt-scraper profile, including the web extraction and vision fetches of the listing features job, for at most the 30-minute window. The impact is low, and Hermes 0.22.5 has no key that allows private addresses for the browser alone.
- Upgrade from 0052: clearing the settings, in `browser off`, the stale rule and the contact gate, also sets `browser.use_real_profile`, `browser.headed` and `browser.real_profile_autoclose` to `false`. Without that, a 0052 setup left on when the installer is re-run could keep giving the contact forms a copy of the user's own profile. The installers' "Browser tools" step also sets each of the three to `false` when `config get` prints `true`, once, at the upgrade.
- When the default browser is not one Hermes knows (Firefox, Safari, a Beta channel), `browser on` uses the first installed of Brave, Chrome, Edge and Chromium. The window is signed out either way, so nothing is lost. `browser on` exits 5, and the skill tells the user to install Brave, Chrome or Edge, only when none of them is installed. Detection is Hermes's own `detect_default_chromium()` and `chromium_executable()` from `hermes_cli/browser_connect.py`, imported, not copied.
- The flags are `--remote-debugging-port`, `--user-data-dir`, `--no-first-run` and `--no-default-browser-check`, the set Hermes's `/browser connect` uses. The last two only skip the first-run pages; they do not mark the browser as automated. Without `--remote-debugging-address`, Brave listened on 127.0.0.1 only (`netstat`).
- `browser off` asks the browser to quit with CDP `Browser.close` through `websockets`, a Hermes dependency like `psutil`. On Windows, `taskkill` without `/F` left Brave running, and the hard kill that followed lost the cookies written in the last seconds and can make Brave show a crash notice next time. After `Browser.close`, Brave recorded `exit_type: Normal` and kept its cookies. If the browser still runs after 10 seconds, it is ended. Only processes whose command line carries `--user-data-dir=<the setup's profile>` and that are not child processes are touched, and the recorded ID must also match its start time.
- `browser on` reuses a running instance, so the skill's per-turn `browser on` refreshes the record without opening another window. A closed window is opened again, on the same port when it is free, so `browser.cdp_url` rarely changes.
- The check of the browser tools ran through Hermes's own `browser_navigate`, `browser_snapshot` and `browser_console` functions, in Hermes's Python, under the throwaway profile's config. It did not run through `hermes -Q chat`, because the throwaway home has no model provider and the owner's credentials were not copied into it. The dashboard chat calls the same functions.
- Hermes bug found, not fixed here: on Windows, agent-browser runs through a `.cmd` shim, and `_shim_safe_args()` in `tools/browser_tool_session.py` only protects arguments that carry a newline or `%`. A `browser_navigate` URL with `&` but no `%` is cut at the first `&`, and cmd.exe runs the rest as a command (`'y' is not recognized as an internal or external command`). Google's consent URL is percent-encoded, and the other URLs in the skill have at most one query parameter, so the setup is not affected. Checked live with both kinds of URL.

## Result

- Delivered on branch `feat/setup_browser_over_cdp`.
- `connections.py`:
  - `browser on` finds the default browser the way Hermes does and starts its real binary, detached, on `$HERMES_HOME/apt-scraper/browser` with a free debugging port on 127.0.0.1 (9333 first). It records PID, start time and port in `apt-scraper/browser-instance.json` and sets `browser.allow_private_urls` and `browser.cdp_url`. It exits 4 while a contact run is recorded and 5 without a supported browser.
  - `browser off` clears both settings and turns off 0052's three real-profile keys, closes the browser, and then removes the record. The profile directory stays.
  - The 30-minute stale rule in `status` and in the contact gate does the same through `turn_off`. A record rewritten during a turn-off keeps the browser and points the settings at it again.
- The `account-connections` skill (1.5.0), section "The browser":
  - the window is the user's own browser on a profile of apt-scraper's own, signed out the first time, signed in once and reused later,
  - exit codes 4 and 5, and no browser step unless `browser on` exited 0 in the turn,
  - `browser off` closes the window,
  - the profile-copy, `close-profile`, `used_real_profile` and `real_profile_unavailable` text is gone.
- The contact skill's form sentence, `profile/config.yaml`'s comment, README's "Connect your accounts" and `docs/spec.md` › Installation and setup describe this design. Ticket 0052's Result has a line that this ticket replaced its lane.
- Tests, with a fake browser that the tests start through Python and that answers `/json/version` (no test starts a real browser):
  - `tests/test_connections.py`:
    - the launch arguments, with no automation, headless, pipe or address flag,
    - both settings set and cleared in order,
    - reuse of an open browser and a restart on the same port,
    - the browser closed and its profile kept on `off`,
    - a process that took over the recorded ID left alone,
    - exit 5 with nothing left set,
    - default-browser detection and the fallback order,
    - `Browser.close` sent over the debugging port,
    - the stale rule and the contact gate closing the browser,
    - the two interleaving tests from 0052 on the new settings,
    - skill tests for the window's wording and the no-browser stop.
  - `GateTests` (`tests/test_contact.py`) and `BrowserSettingsTests` (`tests/test_installers.py`) check the new keys. Both installers' Browser tools step, run against a fake hermes, turns off real-profile keys left `true` and changes nothing otherwise. A 0052 record left on is turned off with the three keys by `status`.
  - `python -m pytest tests -q` from Git Bash: 643 passed, 304 subtests passed. `ruff check .` is clean.
- Verified live on a throwaway `HERMES_HOME` under `%TEMP%`, with the throwaway profile's own browser directory. The owner's Brave kept running, its profile was not opened, and the owner's Hermes configs kept their modification times.
  - `browser on` started `C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe`, listening on 127.0.0.1:9333 only, and set the two keys.
  - Through Hermes's browser tools, a local page read `webdriver=false`, a user agent of `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36` (no HeadlessChrome or Chrome for Testing), `navigator.brave` present, and brands Chromium and Brave.
  - The full snapshot of the `localhost:1` error page named the address after "The webpage at".
  - A persistent cookie set before `browser off` was there after the next `browser on`.
  - Hermes's `cleanup_all_browsers()` left the window open.
  - The throwaway directories were deleted afterwards.
- Not verified:
  - Google's own sign-in check in this window: only the owner can confirm it, on the rerun.
  - The Telegram Web flow, the dashboard chat end to end, and macOS.
