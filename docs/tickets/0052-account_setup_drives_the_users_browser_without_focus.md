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

- [x] Observed on the owner's first setup (Windows, Brave as default browser): the `account-connections` skill drove the Google Cloud console with `computer_use` in the user's browser. Typing from the background was blocked, typing in the foreground minimized the window, and the agent gave up and handed the user a click-by-click list. After the fix the skill never uses `computer_use` for a web page.
- [x] The profile turns on Hermes's real-profile browsing and headed mode (`browser.use_real_profile`, `browser.headed`): Hermes snapshots the default browser's active profile (Chrome, Edge, Brave, Chromium) into its own copy, launches the real browser binary on it in a visible window, and the agent acts in that window with `browser_navigate`, `browser_click`, `browser_type`, `browser_snapshot` and the rest. The user sees every page and does the sign-in and consent clicks there. The keys are on only while a setup runs, because the scheduled jobs share the profile's config: the skill turns them on before its browser steps and off however the setup ends, and a setup that stops without turning them off cannot leave them on. Neither `profile/config.yaml` nor the installers set them.
- [x] The Google section (project, APIs, consent screen, publish, client, download, authorization) and the Telegram section (BotFather, userinfobot, the bot's chat, through https://web.telegram.org) are rewritten for the browser tools. The one-time authorization address is read with `browser_snapshot` or the tab list, not from a screenshot. The bot token is never captured: the skill keeps its rule about screenshots and snapshots of the BotFather chat.
- [x] Windows: a running browser locks its cookie database, so the first snapshot needs the browser fully quit. Hermes then falls back to a fresh browser and reports `real_profile_unavailable`. The skill handles both without stopping: it tells the user the window is signed out and asks them to sign in there, or offers to close their browser and reopen the dashboard afterwards with `browser.real_profile_autoclose`. The user is never sent to a terminal or asked to type a password in the chat.
- [x] `computer_use` remains only for what no browser can do: macOS permission dialogs, privacy settings and a terminal program. The skill says so, and the Windows setup needs none of them.
- [x] After `hermes profile install --force` through the installer, the saved interview and the connection results are still there and the next conversation continues the setup from them: `settings.py status` and `connections.py status` read what the previous install saved. Verify on a throwaway profile, and make the installer or the skill resume rather than restart.
- [x] A Google Cloud project `apt-scraper` that the earlier attempt may have created is reused, not duplicated: the project step checks the picker first.
- [x] Tests cover the skill's rules (no `computer_use` on web pages, the browser keys set by both installers) with the existing test style; no test drives a real Google or Telegram page, opens the owner's browser, or snapshots the owner's browser profile.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

docs/spec.md, Installation and setup: the setup is guided by the agent in the dashboard chat; the user does only what needs their own hands. Add that the agent works in the user's browser through Hermes's browser tools, in a window it opens with the user's logins, and that nothing in the setup needs the user to keep a window in front.

## Design

Hermes 0.22.5 offers three lanes beside computer use: its headless Chromium (no logins), `browser.cdp_url` or `/browser connect` to a browser started with a debugging port (needs a non-default user-data dir, so no logins either), and `browser.use_real_profile`, which copies the user's active profile, launches the user's own browser binary on the copy and drives it through agent-browser. The real-profile lane is the one that carries the user's Google and Telegram logins, works with Brave, Chrome and Edge, and keeps the user's live browser and the dashboard chat untouched. Read `website/docs/user-guide/features/browser.md` in the installed Hermes (`%LOCALAPPDATA%\hermes\hermes-agent`) for its contract, including the Windows first-snapshot lock, `real_profile_autoclose`, `hermes browser close-profile` and the `real_profile_unavailable` result. Hermes's browser extension protocol has no shipped extension, so it is not an option.

The interview state lives in the listing store outside the profile and the connection results in `$HERMES_HOME/apt-scraper/connections.json`, which a distribution reinstall leaves alone; resuming is the first-message `status` calls in SOUL.md.

Verification must not touch the owner's browser, browser profile or Hermes profile: no real-profile snapshot on this machine, no Google or Telegram page, no `computer_use`. Hermes experiments run on a throwaway `HERMES_HOME` under `%TEMP%` with the headless Chromium or stubs.

## Deviations

- Review of PR #45 blocked the first design, which set `use_real_profile`, `headed` and `real_profile_autoclose` in `profile/config.yaml` and from both installers. The `apt-scraper-contact` cron job has no `enabled_toolsets`, so it gets every browser tool, and it runs under the profile's config in the same gateway. Every contact form would have opened a visible Brave window with the user's logins and sent their cookies to the listing site, and on Windows an unattended run could have taken the first profile copy. `headed` alone already opened the window. The keys are now on only during a setup:
  - `connections.py browser on` writes `$HERMES_HOME/apt-scraper/setup-browser` and then sets the three keys to `true` with `hermes -p apt-scraper config set`. `browser off` sets them to `false` and then removes the record, so a failed `off` leaves the record behind for the next try.
  - The skill runs `browser on` before its first browser action in every turn and whenever it starts waiting for the user, and `browser off` however the setup ends: after the final check, when the user stops, when a step fails, and in the fallback browser too.
  - A crash cannot leave the keys on. `connections.py`'s `setup_browser_running()` treats a record older than 30 minutes as a setup that stopped and turns the keys off. Two places call it. `status` runs at the first message of every conversation. The contact job's pre-run gate (`contact.py due`) runs every 2 minutes, before that job can wake. While the record is fresh, the gate starts no run of the job. I chose a script over SOUL.md because it does not depend on the model following prose. I chose the contact gate as the second place because it is the only scheduled consumer of the browser and runs whether or not the user chats, so the keys are off within 32 minutes of a crash.
  - Checked on a throwaway `HERMES_HOME`, with `HERMES_HOME` pointing at the profile as in the gateway terminal. `browser on` and `browser off` change the profile's `config.yaml`. Under the profile home override that gateway turns and cron jobs use, `_use_real_profile()` and `_is_headed_mode()` follow every change without a restart. With the keys off, Hermes deletes `browser-profile/` at its next browser use, so each setup takes a fresh copy. On Windows that means the browser is closed once per setup.
- Round 2 of the review found two races, both fixed:
  - A contact run already in progress when `browser on` ran would have shared the user's window for its next form, because Hermes decides real-profile per new browser session and every real-profile session attaches to the one shared `hermes-real-profile` session. Hermes runs a job's pre-run script only while no run of that job is in progress. So the gate clears the previous run's record (`$HERMES_HOME/apt-scraper/contact-run`) and records the run it wakes. `browser on` exits 4 while that record is less than an hour old; the hour bounds a run that the gateway's exit cut off. The skill waits a minute and tries again. The contact skill's form step also runs `connections.py browser status` before its first browser action and leaves the listing to a later run while a setup uses the browser.
  - `hermes config set` rewrites config.yaml without a lock. A `browser on` interleaving with a stale turn-off could have ended with the keys on and no record. `on`, `off`, the stale check and the gate now hold an operating-system lock: `msvcrt.locking` on Windows, `flock` elsewhere. The lock is released when its process dies, and a waiter gives up after 60 seconds. After the turn-off writes, the record is checked again, and one rewritten meanwhile is kept with the keys turned back on.
  - The guarantee is now: no contact run starts while a setup uses the browser; a setup does not turn it on while a run is in progress, unless that run's record is over an hour old; and a contact form checks first.
- The profile keeps `browser.backend: "off"`, and the installers set it while `config get` prints an empty backend, as they set `delegation.model`. In Hermes 0.22.5 an unset backend gives the agent only `browser_exec`, which writes Python against the page, instead of `browser_navigate` and the rest. The contact skill's form filling needs those tools too. Checked on a throwaway `HERMES_HOME`: the `hermes-cli` and `hermes-api-server` toolsets get `browser_exec` alone without the setting, and the built-in browser tools with it.
- `browser.inactivity_timeout` stays at Hermes's default. The gateway reads it once at start, from the root profile's config, so the profile cannot raise it.
  - A real-profile browser that Hermes launched survives the idle cleanup; Hermes terminates it only when the gateway exits.
  - A fresh browser from the `real_profile_unavailable` fallback closes about two minutes after the agent's last browser action. So the skill waits for the user's sign-in and consent with a `browser_snapshot` about every 30 seconds, with `sleep 30` between them, for up to 10 minutes. This also spares the user a "done" in the chat.
  - When a turn ends in the fallback window, as it must while BotFather may show the token, the user may have to log in to Telegram Web again. The skill says so; this is not fixed.
- Hermes 0.22.5 has no tab-list tool among the built-in browser tools: `browser_cdp` appears only with `/browser connect` or `browser.cdp_url`. The authorization address comes from `browser_snapshot` with `full` true: Chrome's error page for `http://localhost:1/?...` names the full address after "The webpage at". Checked in Hermes's headless Chromium on a throwaway `HERMES_HOME`, with a local stand-in for the consent page. The plain snapshot shows only the heading, and `location.href` is `chrome-error://chromewebdata/`. The full snapshot carries the address, with its `code` not redacted.
- On Windows, the skill decides about closing the browser before its first browser action in a conversation. It decides from whether `$HERMES_HOME/browser-profile/*/.hermes-snapshot-complete` exists. Hermes keeps one browser session per conversation, so after a fallback to a fresh browser, closing the user's browser would not switch that conversation to their logins. If `hermes -p apt-scraper browser close-profile` fails, the user signs in in the agent's window.
- Telegram runs in Telegram Web (`https://web.telegram.org/k/#@<username>`), not in `t.me` links, which hand off to the desktop app.
  - After the user has copied the token, the agent sends `/help` to BotFather through the message field's ref, before it opens another page. Otherwise the chat list's preview of BotFather's last message, which the next snapshot reads, would be the token.
  - While BotFather may show the token, the agent asks in the chat and ends its turn instead of waiting with snapshots.
- The dashboard's Channels page opens in the agent's window too, and the user pastes the token into the form there.
- When the client download does not arrive, the agent writes the client file from the creation dialog. The file holds the client ID and secret; Google does not treat a desktop client's secret as a secret. Checked on the throwaway profile: the google-workspace `setup.py` stores such a file and builds an authorization URL from it.
- Resuming needed no installer change. A reinstall keeps `connections.json`, the listing store, `.env` and `google_client_secret.json`. The skill now skips what an earlier attempt finished:
  - a stored client goes straight to authorization,
  - an `apt-scraper` project in the resource list is reused,
  - enabled APIs, an existing consent screen, a published app and an earlier client download are skipped.
- The contact skill's sentence about its browser now reads "a fresh browser without the user's logins". The first design had the skill empty fields a site filled in from the user's account. The reviewer noted that this gives false assurance, because a signed-in site attributes the inquiry server-side anyway, so that change is reverted.
- README's "Connect your accounts" describes the agent's browser window, which is used only during the setup, the Windows close offer once per setup, and on macOS computer use for the Homebrew terminal.

## Result

- `profile/config.yaml` sets `browser.backend: "off"`. Both installers have a step "Browser tools" that sets it while `config get` prints an empty backend. Neither the profile nor the installers set `use_real_profile`, `headed` or `real_profile_autoclose`.
- `connections.py` has `browser on|off` and the 30-minute stale reset in `status`. `contact.py due` runs the same reset and keeps the contact job asleep while a setup uses the browser.
- The `account-connections` skill (1.4.0) has a section "The browser" that covers:
  - every web page goes through the browser tools, and nothing needs a window in front,
  - `browser on` before its browser steps and `browser off` on every way the setup ends,
  - `computer_use` only for macOS permission dialogs, macOS privacy settings and a terminal program,
  - waiting with snapshots while the user acts,
  - `used_real_profile` and `real_profile_unavailable`,
  - the Windows choice, once per setup, between `hermes browser close-profile` and signing in in the window.

  The Google and Telegram sections are rewritten for it, with the resume skips above.
- `docs/spec.md` › Installation and setup says:
  - the setup is guided in the dashboard chat,
  - the agent works in the user's browser through Hermes's browser tools, in a window it opens with their logins, only while a setup runs,
  - nothing needs a window in front,
  - each setup takes a fresh copy,
  - a reinstall continues from the saved state.
- Tests:
  - `BrowserSettingsTests` (`tests/test_installers.py`): the distribution config and both installers set `browser.backend` and none of the setup-only keys.
  - `tests/test_connections.py`:
    - `browser on` records the setup and turns the keys on, and `browser off` turns them off and removes the record.
    - `status` turns off a record older than 30 minutes and leaves a fresh one alone.
    - A failed `off` keeps the record for the next `status`.
    - `SkillTest`: the Google and Telegram sections use `browser_navigate` and name no computer use, `start`/`open`, address bar or `t.me` link. Computer use appears only for a terminal. The BotFather no-capture rule is there. `browser on` comes before the browser steps, and `browser off` covers every way the setup ends.
  - `GateTests` (`tests/test_contact.py`): a running setup keeps the contact job asleep, and a stopped one is turned off and the job wakes and its run is recorded.
  - Round 2 tests:
    - `browser on` exits 4 while a contact run is recorded, and goes ahead once that record is over an hour old.
    - The gate records the run it wakes and clears it at the next gate.
    - `browser status` exits 0 only while no setup uses the browser.
    - The contact skill's form step runs `browser status` before opening the form.
    - Two deterministic interleaving tests fail on the round-1 code: a `browser on` started in a thread during a stale turn-off waits and ends with the record present and the keys on, and a record rewritten during the turn-off is kept with the keys on. `python -m pytest tests -q` from Git Bash: 631 passed, 294 subtests passed. `ruff check .` is clean.
- Reinstall, verified on a throwaway `HERMES_HOME` under `%TEMP%` with Hermes 0.22.5 (in the first round):
  - Setup: the profile was created and the distribution installed, part of the interview saved, `connections.py check gmail calendar` run, a client file stored, `TELEGRAM_ALLOWED_USERS` set and a real-profile copy marker created.
  - Then the installer's profile step ran: keep the config, `hermes profile install --force --yes`, restore.
  - Every file outside `skills/` and `cron/` was unchanged except `config.yaml`, `distribution.yaml` (install time) and Hermes's config backups.
  - `settings.py status` and `connections.py status` printed the same before and after.
- Not verified:
  - Nothing ran against Google, Telegram, the owner's browser or a real-profile copy on this machine, and no headed window was opened.
  - Untested live: Brave's real-profile copy and launch on Windows, `close-profile` against a running Brave, Google sign-in inside a browser that Hermes drives over CDP, the Telegram Web flow, the dashboard's Channels form in the agent's window, a download's location in the copy window, and macOS.
