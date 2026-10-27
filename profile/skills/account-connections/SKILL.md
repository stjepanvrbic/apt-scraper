---
name: account-connections
description: "Connect and check Gmail, Calendar, gateway and iMessage."
version: 1.5.0
author: Stjepan Vrbic
license: Proprietary
platforms: [windows, macos]
metadata:
  hermes:
    tags: [apt-scraper, Setup, Gmail, Calendar, Telegram, iMessage]
    related_skills: [google-workspace, imessage]
---

# Account connections

apt-scraper works through four of the user's own accounts. Each has a check, and a channel is used only while its check passes.

| Channel | Used for | Connected through |
|---|---|---|
| `gmail` | Email to listing agents | Hermes `google-workspace` skill, the user's own Google OAuth client |
| `calendar` | Viewings as calendar events | the same Google connection |
| `gateway` | Questions to the user and their replies | the dashboard's Channels page, Telegram with the user's own bot |
| `imessage` | Texts to listing agents, macOS only | Hermes `imessage` skill and the `imsg` command |

## When to Use

- A connection is not connected, or was never checked, and the user agreed to set it up.
- The user asks to connect, reconnect or check Gmail, Google Calendar, the messaging gateway or iMessage.
- Using a channel failed with an authorization or permission error.

## Who does what

You do the work. Run every command, open every page, and do the clicking and typing in the browser yourself. The user does only what needs their own hands:

- signing in to Google and Telegram,
- approving Google's consent screen,
- answering macOS permission dialogs and switching on macOS privacy settings,
- typing their Mac password, and pasting the Telegram bot token into the dashboard's Telegram form.

Never type a password, never click a permission dialog or consent button, and never ask for a password, bot token or client secret in the chat. Tell the user in one line what you are doing before each page. The one exception to reading every page is BotFather's reply with the bot token: the gateway section says how to get past it without capturing it.

The user talks to you in the chat of the Hermes web dashboard, in their browser, or in Telegram. Your commands run inside the Hermes gateway with no terminal the user can see, so every command you run must finish without asking for input.

Never restart the gateway during your own turn, because the turn runs in it: `hermes gateway restart` refuses while the turn runs, and with `--yes` it ends your turn and the dashboard chat stops answering until the user reloads the page.

## The browser

Do every step on a web page, Telegram included, with the browser tools: `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_press`, `browser_scroll` and `browser_back`. None of them needs the window in front, so the user keeps chatting and using the mouse while you work. Never use `computer_use` on a web page and never open one with `start` or `open`. `computer_use` is only for what no browser can do: macOS permission dialogs, macOS privacy settings and a terminal program. The Windows setup needs none of them.

On their own, the browser tools drive Hermes's own test browser, which the scheduled jobs use and where Google refuses to sign in. For the setup, open the setup's browser before your first browser action in every turn of the setup, and again whenever you start waiting for the user in the window:

```bash
python "${HERMES_SKILL_DIR}/scripts/connections.py" browser on
```

It starts the user's own default browser (Brave, Chrome, Edge or Chromium) in a window of its own, on a profile that belongs to apt-scraper, and points the browser tools at it. It is the user's real browser, but signed out of everything the first time, and it never touches the profile the user works in, which stays open meanwhile. The output names the browser and says whether the window is new or already open. Exit codes:

- 4: the listing correspondence job is filling in contact forms, which would share this browser, and no run starts while a setup uses it. Tell the user in one line that you wait for it, run `sleep 60` and try again, for up to 10 minutes.
- 5: no supported browser is installed. Tell the user to install Brave, Google Chrome or Microsoft Edge and to say "continue the connection setup" afterwards, and stop the setup's browser steps there.
- Any other code but 0: show the user the output and stop the browser steps.

Never do a setup step in the browser tools unless `browser on` exited 0 in this turn, because they would fall back to Hermes's test browser.

Turn it off again whenever the setup ends, however it ends: after the final `check`, when the user stops or declines the rest, and when a step fails and you report it. That closes the window. The profile stays, so the next setup finds the user signed in.

```bash
python "${HERMES_SKILL_DIR}/scripts/connections.py" browser off
```

A setup that stops without `browser off` is turned off by the next `status`, and by the listing correspondence job, once 30 minutes have passed since the last `browser on`.

Act on the refs of the latest snapshot; `browser_navigate` returns one. When the user must act in the window, tell them in one line what to do there, then run `browser on` and wait for the page to change: take a `browser_snapshot` about every 30 seconds, with `sleep 30` in the terminal in between, for up to 10 minutes, and carry on once it has. If it has not changed by then, ask in the chat and end your turn. The window stays open between turns. If the user closed it, `browser on` opens it again, signed in as before.

The first time, tell the user in one line that the window is their own browser on a profile of apt-scraper's own, so they sign in to Google and Telegram there once, when a page asks, and later setups reuse those sign-ins. They sign in in the window, never in the chat.

## The check

```bash
python "${HERMES_SKILL_DIR}/scripts/connections.py" check                     # every channel
python "${HERMES_SKILL_DIR}/scripts/connections.py" check gmail calendar       # some channels
python "${HERMES_SKILL_DIR}/scripts/connections.py" check imessage --handle "+15551234567"
python "${HERMES_SKILL_DIR}/scripts/connections.py" status
python "${HERMES_SKILL_DIR}/scripts/connections.py" require gmail
```

Use a channel only when the script exits 0. Exit code 3 means at least one channel asked about is not connected, and each line of output gives the reason. Any other exit code means the script itself failed: show the user its output and do not use the channel. On Windows, iMessage reports `unavailable` and counts as fine for `status`: Windows has no texting. Results are stored in `$HERMES_HOME/apt-scraper/connections.json`.

`check` tests the channels now and sends the gateway test message and the iMessage test text. `status` and `require` use the stored results and test again any result that is missing or older than a day. They never send a test message or a test text, so a channel that has never passed a `check` stays not connected until you run one. A command that does not answer in time, or a `check` that runs out of its 150 seconds, stores `not checked`, which `status` and `require` test again at once. Check iMessage on its own, because its first send waits for a macOS dialog.

What each check proves:

- **Gmail**: a readable Google token exists, it grants sending and reading mail, and Gmail answers a request.
- **Google Calendar**: the token grants calendar access, and the calendar answers a request.
- **Messaging gateway**: a platform is configured, a test message reached the user's home channel through it, and the gateway is running, so the user's replies reach you.
- **iMessage**: `imsg` is installed, the Messages history is readable, and a test text to the user's own number or Apple ID was sent. macOS grants this per program, so the terminal and the gateway each have their own result, shown as "iMessage from the terminal" and "iMessage from the gateway". `require imessage` uses the result of the context it runs in.

## Procedure

1. Run `check`. Tell the user in one line per channel what works and what doesn't.
2. For each channel that is not connected, ask whether to set it up now, then follow its section below. Google first, then the gateway, then iMessage on macOS.
3. After each section, run `check` for that channel. If it still fails, read the reason, fix that cause, and check again.
4. Finish with `check`, run `browser off` as "The browser" says, and report the final state. A channel the user chose to skip stays unused. Say so.

## Google: Gmail and Calendar

One Google Cloud OAuth client in the user's own Google account connects both. Shorthand for the commands below:

```bash
GOOGLE_SETUP="$HERMES_HOME/skills/productivity/google-workspace/scripts/setup.py"
```

1. Run `python "$GOOGLE_SETUP" --check`. If it prints `AUTHENTICATED`, skip to step 9. If `$HERMES_HOME/google_client_secret.json` exists, an earlier attempt stored the client: skip to step 8.
2. Ask whether the Google account uses Advanced Protection. If it does, a Workspace admin must allow the OAuth client first. Tell the user before starting.
3. Open https://console.cloud.google.com/cloud-resource-manager. If Google asks the user to sign in, or to accept the Google Cloud terms of service, they do it in the window. If the list has a project named `apt-scraper`, an earlier attempt created it: use it. Otherwise open https://console.cloud.google.com/projectcreate, type the project name `apt-scraper`, press **Create** and wait for the notification that the project exists. Read the project's ID from the list, such as `apt-scraper-123456`, and add `?project=<the ID>` to every console address below, so each page acts on that project.
4. Open each page and press **Enable**, then wait until the API's overview page shows. A page that shows **Manage** instead is enabled already.
   - https://console.cloud.google.com/apis/library/gmail.googleapis.com
   - https://console.cloud.google.com/apis/library/calendar-json.googleapis.com
5. Open https://console.cloud.google.com/auth/branding. If it shows the app's branding form instead of **Get started**, the consent screen exists: go on with step 6. Otherwise press **Get started**. Type the app name `apt-scraper` and choose the signed-in address as support email, then **Next**. Choose audience **External**, then **Next**. Type the same address as contact email, then **Next**. Tick the agreement to the Google API Services User Data Policy, then **Continue** and **Create**. Read the address from the account menu if you are unsure of it.
6. Open https://console.cloud.google.com/auth/audience. Unless its publishing status is **In production**, press **Publish app** and confirm. Google expires every authorization of an app left in Testing after 7 days, so Gmail would stop working every week. A published app that Google has not verified still works for its owner, behind an "unverified app" warning.
7. If an earlier attempt downloaded the client, `ls -t ~/Downloads/client_secret_*.json | head -n 1` finds its file: store that one. Otherwise open https://console.cloud.google.com/auth/clients and press **Create client**. Choose application type **Desktop app**, type the name `apt-scraper`, press **Create**, then press **Download JSON** in the dialog and look for the file again. If none arrived after 10 seconds, read the client ID and secret from a snapshot of the dialog and write the file yourself, as below. Google does not treat a desktop client's secret as a secret, so reading it is fine. Then store the file:

   ```bash
   # Only without a download:
   printf '{"installed": {"client_id": "%s", "client_secret": "%s", "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}' \
       "<client ID>" "<client secret>" > ~/Downloads/client_secret_apt-scraper.json
   python "$GOOGLE_SETUP" --client-secret "<that file>"
   ```

8. Authorize:
   1. Run `python "$GOOGLE_SETUP" --auth-url`. It prints the consent URL and nothing else. This version of the script takes no `--services` or `--format` options.
   2. Open that URL with `browser_navigate`. The consent is the user's: they pick their account, and on "Google hasn't verified this app" they press **Advanced**, then **Go to apt-scraper (unsafe)**, because the app is their own.
   3. The consent screen lists Gmail, Calendar, Drive, Contacts, Sheets and Docs. Gmail and Calendar must stay allowed. The others may be unticked. Then the user presses **Continue**.
   4. The window lands on `http://localhost:1/?...` and shows an error page. That is expected. Take a `browser_snapshot` with `full` set to true: the error page gives the full address after "The webpage at". It is a one-time code, not a password.
   5. Run `python "$GOOGLE_SETUP" --auth-code "<the full address>"`. If it says the code expired or was used, run `--auth-url` again and repeat with the newest address only.
9. Run `check gmail calendar`.

Failures:

| Reason in the check | Fix |
|---|---|
| No Google account is connected | Steps 3 to 8 |
| The stored Google token is corrupt | `python "$GOOGLE_SETUP" --revoke`, then step 8 |
| lacks permission to send mail or read replies, or to manage the calendar | `python "$GOOGLE_SETUP" --revoke`, then step 8, leaving Gmail and Calendar allowed |
| `Access Not Configured` | Step 4 for the API named, wait a minute, check again |
| `invalid_grant` | The user changed their password or removed the app's access, or the app was left in Testing. Step 6 if it is in Testing, then step 8 |
| `Error 403: access_denied` during consent | Step 6: the app must be published |
| `not checked`, did not answer within 30 seconds | Check the internet connection, then check again |
| The Hermes google-workspace skill is missing | Ask the user to run the apt-scraper installer again |

## Messaging gateway

Your questions reach the user here while the gallery is closed, and their replies come back through the running gateway. Use Telegram with a bot the user creates through BotFather in their own Telegram account. Hermes can also create the bot through a Nous-hosted service, so always take the manual setup in step 7.

1. Open https://web.telegram.org/k/. If it asks the user to log in, they do it in the window, with the QR code and Telegram on their phone.
2. Create the bot. Open https://web.telegram.org/k/#@BotFather, press **Start** if shown, and send `/newbot`: type it into the message field and press Enter. When BotFather asks for a name, send `apt-scraper`. When it asks for a username, note the message field's ref from that snapshot, then send one ending in `bot`, such as `<first name>_aptscraper_bot`.
3. BotFather's answer to the username is either the bot's token or a note that the username is taken. From the moment you send the username until step 4 has opened another page, take no snapshot, screenshot or `browser_vision` of the window and evaluate nothing in it with `browser_console`. Ask the user in the chat which answer they see, and end your turn instead of waiting with snapshots. If the username is taken, send another one through the noted ref and ask again. Once BotFather shows the token, the user copies it from the reply and keeps it for step 7. Then send `/help` through the same ref, so that the chat list no longer shows the token as BotFather's last message, and open `about:blank`.
4. Open `https://web.telegram.org/k/#@<the username>`. Snapshots are safe again. Press **Start** and send any message. This chat is where your questions will arrive.
5. Find the user's Telegram ID. Open https://web.telegram.org/k/#@userinfobot, press **Start**, and read the number it replies with. It is an ID, not a secret.
6. Allow only that ID to use the bot, and make its chat the home channel your questions go to:

   ```bash
   hermes -p apt-scraper config set TELEGRAM_ALLOWED_USERS "<the ID>"
   hermes -p apt-scraper config set TELEGRAM_HOME_CHANNEL "<the ID>"
   ```

7. Open the dashboard's Channels page. The installer records the dashboard's address in `$HERMES_HOME/dashboard-address`. Check that Hermes answers there:

   ```bash
   curl -s --max-time 2 -w '\n%{http_code}\n' "$(cat "$HERMES_HOME/dashboard-address")/api/health"
   ```

   Hermes answers only when the last line is `200` and the reply above it is JSON with `"ok": true` and a `version`. Any other program on that port answers differently. Then open `<the address>/channels?profile=apt-scraper` with `browser_navigate`.

   If anything else answers, or nothing does, start the dashboard without a window, the way it starts at login, and check again after a few seconds. If the file is missing, a command fails or Hermes still does not answer, ask the user to run the apt-scraper installer again:

   ```bash
   cscript //nologo "$LOCALAPPDATA/hermes/profiles/apt-scraper/dashboard.vbs"   # Windows
   # macOS
   launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/io.github.stjepanvrbic.apt-scraper.dashboard.plist 2>/dev/null ||
       launchctl kickstart "gui/$(id -u)/io.github.stjepanvrbic.apt-scraper.dashboard"
   ```

   On the Telegram card press **Manual setup**. Never press **Create with QR**: it creates the bot through a Nous-hosted service. The form shows the ID from step 6 under **Allowed Telegram user IDs**. Take a snapshot, then ask the user to click the **Telegram bot token** field in your window and paste the token from step 3. The field shows dots, and you never type into it. Leave **Webhook secret** empty and press **Save & enable**. The running gateway connects the bot within seconds, without a restart, and the Telegram card shows its state.
8. Run `check gateway`. Ask the user to confirm the test message arrived in the bot chat.

Failures:

| Reason in the check | Fix |
|---|---|
| No messaging platform is set up | Steps 1 to 7 |
| No test message has gone through yet | `check gateway`, then fix whatever it reports |
| `No home channel set for telegram` | The `TELEGRAM_HOME_CHANNEL` command in step 6, then check again |
| the gateway is not running | `hermes -p default gateway start`. If it reports no installed service, run `hermes -p default gateway install --no-start-now --start-on-login`, then `hermes -p default gateway start` again: on Windows, an install that starts the gateway after an administrator approval starts it as administrator, where no chat can reach it. One gateway serves every profile, and Hermes refuses these commands with `-p apt-scraper` |
| Listing messaging platforms failed or returned unreadable output | Run `hermes -p apt-scraper send --list` and fix what it reports, or ask the user to run the apt-scraper installer again |
| Any other test message failure | Read the error on the Telegram card of the Channels page. Step 4 if the bot chat was never started. If the token was mistyped or revoked, the user gets a new one from BotFather with `/token`, then step 7 with the new token |

## iMessage on macOS

iMessage exists only on macOS, and texting is off elsewhere. `imsg` needs macOS 14 or later. macOS asks the user for two permissions, and they grant them by hand: Full Disk Access, so `imsg` can read the Messages history, and Automation, so it can send through Messages. Both grants belong to the program that starts `imsg`. Your commands run inside the gateway whether the user writes in the dashboard, in Telegram or in a terminal, and Hermes's launchd service runs the gateway through `/usr/bin/osascript` (`hermes_cli/gateway_launchd.py`), so `osascript` needs both grants. When the gateway service is not running, Hermes starts a gateway from the program the user opened Hermes from, usually a terminal app, and that app needs them instead. The script tells the two apart by whether `osascript` started it, and keeps one result for each, shown as "iMessage from the gateway" and "iMessage from the terminal". `require imessage` uses the result of the context it runs in.

1. Install `imsg`. If `command -v brew` finds nothing, run `open -a Terminal`, then type this into the new Terminal window with computer use and press Enter. Hermes asks the user to approve computer-use actions, so ask them first to allow them for this session:

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

   When it asks for the Mac password, the user types it. When it asks to press RETURN, press Enter. At the end, type the commands it prints under "Next steps". Then run `brew install steipete/tap/imsg` and `imsg --version`.
2. Run `open -a Messages`. The user signs in with their Apple ID if Messages asks. For texts to phones without iMessage, they turn on **Settings → Apps → Messages → Text Message Forwarding** for this Mac on their iPhone.
3. Full Disk Access. Ask the user which terminal app they run the installer and `hermes` commands in (Terminal, iTerm and so on). Open the pane:

   ```bash
   open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
   ```

   If it does not open, the user goes to **System Settings → Privacy & Security → Full Disk Access**. Tell them to press **+** and add these two, then switch each on:
   - that terminal app,
   - `/usr/bin/osascript`. In the file chooser, they press Command-Shift-G, type `/usr/bin/osascript` and press Enter, then **Open**.

   macOS reads this grant only when a program starts, so the gateway must start again after your turn has ended. Tell the user that the chat pauses for about a minute, and that they then reload the dashboard page and say "continue the connection setup". Then, as the last command of your reply, run this with the terminal tool's `background=true`. Hermes refuses a restart while any turn or scheduled job runs, so the command tries every 30 seconds for five minutes and restarts the gateway once nothing runs. It interrupts nothing, your own turn included:

   ```bash
   sh -c 'date; for attempt in 1 2 3 4 5 6 7 8 9 10; do sleep 30; hermes -p default gateway restart && exit 0; done; exit 1' > "$HERMES_HOME/logs/gateway-restart.log" 2>&1
   ```

   The stored results carry over.
4. Ask for the user's own phone number, with country code, or Apple ID email. The check texts it.
5. Run `check imessage --handle "<number or Apple ID>"` on its own. The first send makes macOS ask whether `osascript`, or the terminal app when the result names the terminal, may control Messages, and the user presses **OK**. Ask them to confirm the test text appeared in Messages. A later `check imessage` reuses the stored handle.

Failures:

| Reason in the check | Fix |
|---|---|
| The imsg command is not installed | Step 1 |
| `unable to open database file`, or empty results | Read `$HERMES_HOME/logs/gateway-restart.log`, whose first line is when the restart began. If its last attempt was refused or failed, show the user what it says and repeat the restart in step 3. Otherwise step 3 for the context that failed. If the entries are already on, switch them off and on again, because they go stale after macOS, Homebrew or terminal updates. If the terminal still fails, also add `/System/Applications/Utilities/Terminal.app`, which the imsg documentation says macOS sometimes consults |
| `not authorized to send Apple events` | Open `x-apple.systempreferences:com.apple.preference.security?Privacy_Automation`, or **System Settings → Privacy & Security → Automation**. The user switches on **Messages** under the terminal app or `osascript`, then check again |
| `not checked`, did not finish within 90 seconds | A permission dialog was waiting. The user answers it, then check again |
| No test text has been sent from the terminal or the gateway yet | Step 5 in that context. For the terminal, first try `hermes -p default gateway start`: with the gateway service running, your commands run in the gateway |
| The test text needs your own phone number or Apple ID | Step 4 |
| The test text failed, any other reason | Step 2: Messages must be signed in, and Text Message Forwarding is needed for a number without iMessage |
