---
title: Account connections and checks
type: Feature
state: Done
priority: 2
assignee: Stjepan Vrbic
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

The first run connects Gmail, Google Calendar, a Hermes messaging gateway, and iMessage on macOS, and reports each connection's check.

## Acceptance

- [ ] Each connection has a check that passes on a connected account and fails with a clear message otherwise.
- [ ] A channel whose check fails is not used.

## Spec

[Spec › Installation and setup](../spec.md)

## Design

A Hermes setup skill the agent runs after install, using Hermes connectors for Google, `hermes gateway setup` for the gateway and the Hermes iMessage skill on macOS.

## Deviations

- Google connects through the `google-workspace` skill bundled with Hermes, on an OAuth client the user creates in their own Google Cloud project. Hermes's managed connectors (`manage_connections`) need a paid Nous Portal subscription and route mail through the Nous tool gateway (Hermes docs, `reference/tools-reference.md` › `connections` toolset and `user-guide/features/tool-gateway.md`). The owner confirmed that every account stays the user's own.
- The owner added two requirements during the work. The agent drives every setup step itself, with computer use in the user's own browser, in Telegram and in the setup wizard. The user only signs in, approves consent and permission dialogs, and pastes secrets. `profile/SOUL.md` makes the agent check the connections at the first message of every conversation and offer setup when one fails, so the user never has to know the skill exists.
- The Google consent screen also asks for Drive, Contacts, Sheets and Docs. The connector's `setup.py` in Hermes 0.22.5 always requests that fixed set and rejects the `--services` option its own SKILL.md documents. The user may untick everything but Gmail and Calendar, and the check requires only those.
- The setup publishes the Google app to production, as the owner confirmed. Google expires an app's authorizations after 7 days while it is in Testing (Google Identity, "Using OAuth 2.0" › Refresh token expiration, and Cloud Console help "Manage App Audience"). An unverified published app works behind a warning and is limited to 100 users (Cloud Console help "Unverified apps").
- Telegram uses a bot the user creates with BotFather, the wizard's manual option. The wizard's default automatic option creates the bot through a Nous-hosted onboarding service (`hermes_cli/telegram_managed_bot.py`), which the owner excluded.
- The gateway check also requires a running gateway, so the user's replies reach the agent. The owner moved this here from the contact work.

## Result

Skill `account-connections`, installed as part of the profile:

```
profile/
├── SOUL.md                              every conversation: status, offer setup, require before use, check on auth errors
└── skills/account-connections/
    ├── SKILL.md                         setup procedure for Google, gateway and iMessage
    └── scripts/connections.py           check, status, require
tests/test_connections.py                40 tests with fake hermes, imsg and google_api
README.md                                Connect your accounts, macOS 14 for iMessage
```

Hermes does not need a category directory. `hermes skills list` lists the skill from `profile/skills/account-connections/`, skill discovery recurses (`agent/skill_utils.py`, `iter_skill_index_files`), and the category is optional (`user-guide/features/skills.md`).

`connections.py check` tests each channel now and stores the result in `$HERMES_HOME/apt-scraper/connections.json`:

- Gmail and Calendar need a readable Google token with the right scopes and a live answer from `google_api.py`. A corrupt token fails both with its reason.
- The gateway needs a configured platform, a test message delivered by `hermes send` to a home channel, and a running gateway. It tries the platform that worked before first, then the others, and reports each failure. Liveness comes from `hermes_cli.profiles.list_profiles`, the same source as `hermes profile list`, because Hermes has no machine-readable gateway status.
- iMessage needs `imsg`, a readable Messages history and a test text to the user's own number or Apple ID. macOS grants Full Disk Access and Automation to the program that starts `imsg`, so the script keeps one result for the terminal and one for the gateway. It tells them apart by whether `/usr/bin/osascript` is among its ancestor processes, because Hermes's launchd gateway runs under `osascript`. `require imessage` uses the result of the context it runs in.

`status` and `require` check again any result that is missing, older than a day or `not checked`, and they never send a test message or test text in any state. Without test messages, the gateway passes only on the platform where an earlier `check` delivered one and while the gateway runs. iMessage passes only if an earlier `check` texted the same handle from the same context. Otherwise both report that a `check` is needed. Each command has a timeout, and a whole check stops after 150 seconds, inside Hermes's 180-second terminal limit. A timeout and a channel the check never reached both store `not checked`, kept apart from `not connected` and always checked again, so a network blip does not block a channel for a day. On a timeout the script kills the whole process tree, because `hermes.exe` runs Python as a child that holds the output pipes. The kill and the wait for the pipes are each bounded at 5 seconds. The results file is merged and replaced in one step, and a corrupt file is replaced. The skill declares `platforms: [windows, macos]`, the project's platforms, because the linter warns without a declaration for a script that uses `osascript` and `signal.SIGKILL`. Exit code 0 means usable, and every other code means do not use the channel.

Ran on this Windows machine with Hermes 0.22.5 and profile `apt-scraper`, installed with `hermes profile install` the way `install.ps1` does:

- `python -m unittest discover -s tests`: 40 passed, 0 skipped, in 17 seconds. They cover pass and fail paths of all four checks, including the macOS iMessage path, against fake commands. That includes a corrupt token, a token without `scopes`, timeouts, the overall budget, several platforms, an unreadable platform listing, a gateway that is not running, and stale connected and stale failed records. They also cover missing records with no test message or text sent, `not checked` results, per-context iMessage results and a corrupt results file. The timeout tests run through the real Windows `taskkill` path.
- Hermes's skill linter (`tools/skill_linter.py`) reports 0 findings. Its prompt-injection scanner passes `SOUL.md` and `SKILL.md`.
- "Hello", on a profile with no stored results: the agent ran `status` before replying, which checked every channel. It then listed the unconnected accounts and offered setup.
- "Check my account connections": the agent ran `check`. Gmail and Calendar reported "No Google account is connected.", the gateway "No messaging platform is set up." and iMessage "unavailable" with the macOS reason.
- "Email the listing agent at agent@example.com …": the agent ran `require gmail`, got exit 3, sent nothing and offered setup.
- `require gmail` checked again a stored Gmail result aged to two days and refreshed its timestamp. A results file overwritten with `{not json` was replaced by `status` with all four results, and no temporary file was left.
- `gateway_running()` against the real profile returned true, matching `hermes gateway list` (served by the host gateway, PID 15712). It imports `hermes_cli`, which works because the agent's bare `python` in this profile is Hermes's own venv, as the agent's terminal shows here. Whether that holds for sessions started from the gateway, and on macOS, is not verified.
- Hermes's own `_setup_telegram`, run against a scratch Hermes home with scripted answers, asked `Choice [1/2]`, then the hidden token, the allowlist and the home channel, in the order the skill's table gives. It wrote `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS` and `TELEGRAM_HOME_CHANNEL` to that scratch home only.
- `cmd /c start "Hermes gateway setup" powershell …` opened a Windows Terminal window titled "Hermes gateway setup" running `%LOCALAPPDATA%\hermes\bin\hermes.exe -p apt-scraper gateway setup`. It was closed without answering anything.
- "Yes, set up my Google account now.": the agent ran the connector's `setup.py --check` (`NOT_AUTHENTICATED`) and opened the Cloud Console with `cmd /c start`. `computer_use list_windows` then showed Brave on "New Project – Google Cloud console". Its first click was refused because one-shot mode has nobody to approve computer use. An earlier run used Hermes's headless browser tool, which the user cannot see, so the skill now rules those tools out for setup.

Not run, so both acceptance items stay open:

- The pass path on real accounts, and the agent clicking through Cloud Console, BotFather and the wizard. This needs the owner's Google and Telegram accounts and an interactive session that approves computer use. To finish: run `hermes -p apt-scraper`, allow computer use for the session, accept the setup, sign in and approve when asked. Then confirm that `check` reports Gmail, Google Calendar and the gateway as connected and that the Telegram test message arrived.
- The macOS path: the iMessage section, the Full Disk Access and Automation dialogs for the terminal and for `/usr/bin/osascript`, telling the two contexts apart through the `ps` ancestry, the Homebrew install, the `osascript` wizard window, and `open` for pages. It is written from the imsg documentation (`docs/permissions.md`, `docs/troubleshooting.md`, `docs/install.md`), Hermes's `hermes_cli/gateway_launchd.py` and Apple's Text Message Forwarding page. To finish: on a Mac with macOS 14 or later, run the installer, accept the setup and follow the iMessage section, including the check from the bot chat. Then confirm that `check imessage` passes in both contexts and the test texts appear in Messages.
- `SOUL.md` and `require` refuse a failed channel, and nothing sends yet. The contact and viewing work must call `connections.py require <channel>` in its own code before every send, so a background run holds to it too.

Tech debt: none in the touched code.
