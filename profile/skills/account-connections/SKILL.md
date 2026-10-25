---
name: account-connections
description: "Connect and check Gmail, Calendar, gateway and iMessage."
version: 1.2.0
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
| `gateway` | Questions to the user and their replies | `hermes gateway setup`, Telegram with the user's own bot |
| `imessage` | Texts to listing agents, macOS only | Hermes `imessage` skill and the `imsg` command |

## When to Use

- A connection is not connected, or was never checked, and the user agreed to set it up.
- The user asks to connect, reconnect or check Gmail, Google Calendar, the messaging gateway or iMessage.
- Using a channel failed with an authorization or permission error.

## Who does what

You do the work. Run every command, open every page, and do the clicking and typing in the browser, in Telegram and in the setup wizard with computer use. The user does only what needs their own hands:

- signing in to Google and Telegram,
- approving Google's consent screen,
- answering macOS permission dialogs and switching on macOS privacy settings,
- typing their Mac password and pasting secrets into the program that asks for them.

Never type a password, never click a permission dialog or consent button, and never ask for a password, bot token or client secret in the chat. Hermes asks the user to approve computer-use actions. Before you start, ask them to allow them for this session, so the setup does not stop at every click. Take a screenshot before each click and tell the user in one line what you are doing. The one exception is BotFather's reply with the bot token: the gateway section says how to get past it without capturing it.

Work in the user's own browser, where they can see the page and sign in. Open each page with the command below, then act in that browser window with `computer_use`. Do not use the browser tools (`browser_navigate`, `browser_exec` and the rest): they drive Hermes's own headless Chromium, which the user cannot see or sign in to.

```bash
cmd /c start "" "https://example.com"   # Windows
open "https://example.com"               # macOS
```

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
4. Finish with `check` and report the final state. A channel the user chose to skip stays unused. Say so.

## Google: Gmail and Calendar

One Google Cloud OAuth client in the user's own Google account connects both. Shorthand for the commands below:

```bash
GOOGLE_SETUP="$HERMES_HOME/skills/productivity/google-workspace/scripts/setup.py"
```

1. Run `python "$GOOGLE_SETUP" --check`. If it prints `AUTHENTICATED`, skip to step 9.
2. Ask whether the Google account uses Advanced Protection. If it does, a Workspace admin must allow the OAuth client first. Tell the user before starting.
3. Open https://console.cloud.google.com/projectcreate. If Google asks the user to sign in, wait until they have. Then type the project name `apt-scraper`, press **Create**, wait for the notification that the project exists, and select it in the project picker at the top.
4. Open each page and press **Enable**, then wait until the API's overview page shows:
   - https://console.cloud.google.com/apis/library/gmail.googleapis.com
   - https://console.cloud.google.com/apis/library/calendar-json.googleapis.com
5. Open https://console.cloud.google.com/auth/branding and press **Get started**. Type the app name `apt-scraper` and choose the signed-in address as support email, then **Next**. Choose audience **External**, then **Next**. Type the same address as contact email, then **Next**. Tick the agreement to the Google API Services User Data Policy, then **Continue** and **Create**. Read the address from the account menu if you are unsure of it.
6. Open https://console.cloud.google.com/auth/audience, press **Publish app** and confirm. Google expires every authorization of an app left in Testing after 7 days, so Gmail would stop working every week. A published app that Google has not verified still works for its owner, behind an "unverified app" warning.
7. Open https://console.cloud.google.com/auth/clients and press **Create client**. Choose application type **Desktop app**, type the name `apt-scraper`, press **Create**, then download the client's JSON file from the dialog. Google does not treat a desktop client's secret as a secret, so the dialog may stay on screen. Find the file and store it:

   ```bash
   ls -t ~/Downloads/client_secret_*.json | head -n 1
   python "$GOOGLE_SETUP" --client-secret "<that file>"
   ```

8. Authorize:
   1. Run `python "$GOOGLE_SETUP" --auth-url`. It prints the consent URL and nothing else. This version of the script takes no `--services` or `--format` options.
   2. Open that URL in the user's browser. The consent is the user's: they pick their account, and on "Google hasn't verified this app" they press **Advanced**, then **Go to apt-scraper (unsafe)**, because the app is their own.
   3. The consent screen lists Gmail, Calendar, Drive, Contacts, Sheets and Docs. Gmail and Calendar must stay allowed. The others may be unticked. Then the user presses **Continue**.
   4. The browser lands on `http://localhost:1/?code=...` and shows a connection error. That is expected. Read the full address from the address bar with computer use. It is a one-time code, not a password.
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

Your questions reach the user here while the gallery is closed, and their replies come back through the running gateway. Use Telegram with a bot the user creates through BotFather in their own Telegram account. The wizard's automatic option creates the bot through a Nous-hosted service instead, so always take the manual option.

1. Open Telegram where the user is signed in: the Telegram app, or https://web.telegram.org if they have no app. If they are not signed in, wait until they are.
2. Create the bot. Open https://t.me/BotFather, press **Start** if shown, and send `/newbot`. When BotFather asks for a name, send `apt-scraper`. When it asks for a username, send one ending in `bot`, such as `<first name>_aptscraper_bot`.
3. BotFather's answer to the username is either the bot's token or a note that the username is taken. Take no screenshot of Telegram from the moment you send the username until step 4 has moved the window to another chat. Ask the user which answer they see. If the username is taken, send another one into the same message field and ask again. Once BotFather shows the token, the user copies it from the reply and keeps it for step 6.
4. Open `https://t.me/<the username>`. That replaces the BotFather chat on screen, so screenshots are safe again. Press **Start** and send any message. This chat is where your questions will arrive.
5. Find the user's Telegram ID. Open https://t.me/userinfobot, press **Start**, and read the number it replies with. It is an ID, not a secret.
6. Open the wizard in a terminal window and drive it with computer use:

   ```bash
   cmd /c start "Hermes gateway setup" powershell -NoExit -Command "hermes -p apt-scraper gateway setup"   # Windows
   osascript -e 'tell application "Terminal" to do script "hermes -p apt-scraper gateway setup"'         # macOS
   ```

   The menus move with the arrow keys and Enter. Answer:

   | Wizard prompt | Answer |
   |---|---|
   | Select a platform to configure | **Telegram** |
   | Reconfigure Telegram? (only if one is set up) | `y` |
   | How would you like to create your Telegram bot? `Choice [1/2]` | `2`, Manual. Never accept the default `1` |
   | Telegram bot token | The user pastes the token from step 3 and presses Enter. The field stays blank while they paste. Do not type into this prompt |
   | Allowed user IDs (comma-separated, leave empty for open access) | The ID from step 5 |
   | Use your user ID (…) as the home channel? | `y` |
   | Select a platform to configure | **Done** |
   | Restart the gateway, Start the gateway service, Start it now, Start the gateway now, Start the gateway automatically on login | `y` to whichever appears |

   On Windows, installing the gateway to start at login may ask for administrator approval. The user approves it.
7. Close the wizard window and run `check gateway`. Ask the user to confirm the test message arrived in the bot chat.

Failures:

| Reason in the check | Fix |
|---|---|
| No messaging platform is set up | Steps 1 to 6 |
| No test message has gone through yet | `check gateway`, then fix whatever it reports |
| `No home channel set for telegram` | Send `/sethome` in the bot chat, or run the wizard again and answer `y` to the home channel |
| the gateway is not running | `hermes -p apt-scraper gateway start`. If it reports no installed service, run `hermes -p apt-scraper gateway install`, then start it |
| Listing messaging platforms failed or returned unreadable output | Run `hermes -p apt-scraper send --list` and fix what it reports, or ask the user to run the apt-scraper installer again |
| Any other test message failure | Step 4 if the bot chat was never started. If the token was mistyped or revoked, the user gets a new one from BotFather with `/token`, then step 6 with Reconfigure Telegram |

## iMessage on macOS

iMessage exists only on macOS, and texting is off elsewhere. `imsg` needs macOS 14 or later. macOS asks the user for two permissions, and they grant them by hand: Full Disk Access, so `imsg` can read the Messages history, and Automation, so it can send through Messages. Both grants belong to the program that starts `imsg`. That is the terminal app when the user talks to you in a terminal, and `/usr/bin/osascript` when you run from the messaging gateway, because Hermes's launchd service runs the gateway through `osascript` (`hermes_cli/gateway_launchd.py`). The script tells the two apart by whether `osascript` started it, and keeps one result for each. Texts to listing agents can go out from either, so both need their grants and their own passing check.

1. Install `imsg`. If `command -v brew` finds nothing, run `open -a Terminal`, then type this into the new Terminal window with computer use and press Enter:

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

   When it asks for the Mac password, the user types it. When it asks to press RETURN, press Enter. At the end, type the commands it prints under "Next steps". Then run `brew install steipete/tap/imsg` and `imsg --version`.
2. Run `open -a Messages`. The user signs in with their Apple ID if Messages asks. For texts to phones without iMessage, they turn on **Settings → Apps → Messages → Text Message Forwarding** for this Mac on their iPhone.
3. Full Disk Access. Run `echo "$TERM_PROGRAM"` to learn which terminal app the user runs Hermes in (`Apple_Terminal` is Terminal, `iTerm.app` is iTerm, and so on). Open the pane:

   ```bash
   open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
   ```

   If it does not open, the user goes to **System Settings → Privacy & Security → Full Disk Access**. Tell them to press **+** and add these two, then switch each on:
   - that terminal app,
   - `/usr/bin/osascript`. In the file chooser, they press Command-Shift-G, type `/usr/bin/osascript` and press Enter, then **Open**.

   macOS reads this grant only when a program starts. Run `hermes -p apt-scraper gateway restart` for the gateway. Then the user quits the terminal app completely, opens it again, runs `hermes -p apt-scraper`, and says "continue the connection setup". The stored results carry over.
4. Ask for the user's own phone number, with country code, or Apple ID email. The check texts it.
5. Run `check imessage --handle "<number or Apple ID>"` on its own. The first send makes macOS ask whether the terminal app may control Messages, and the user presses **OK**. Ask them to confirm the test text appeared in Messages.
6. Check from the gateway. Ask the user to send "check iMessage" in the bot chat. In that conversation you run `check imessage`, which reuses the stored handle. macOS asks whether `osascript` may control Messages, and the user presses **OK**. Ask them to confirm the second test text arrived.

Failures:

| Reason in the check | Fix |
|---|---|
| The imsg command is not installed | Step 1 |
| `unable to open database file`, or empty results | Step 3 for the context that failed. If the entries are already on, switch them off and on again, because they go stale after macOS, Homebrew or terminal updates. If the terminal still fails, also add `/System/Applications/Utilities/Terminal.app`, which the imsg documentation says macOS sometimes consults |
| `not authorized to send Apple events` | Open `x-apple.systempreferences:com.apple.preference.security?Privacy_Automation`, or **System Settings → Privacy & Security → Automation**. The user switches on **Messages** under the terminal app or `osascript`, then check again |
| `not checked`, did not finish within 90 seconds | A permission dialog was waiting. The user answers it, then check again |
| No test text has been sent from the terminal or the gateway yet | Step 5 or step 6 for that context |
| The test text needs your own phone number or Apple ID | Step 4 |
| The test text failed, any other reason | Step 2: Messages must be signed in, and Text Message Forwarding is needed for a number without iMessage |
