<img src="assets/icon.svg" width="64" height="64" alt="">

# apt-scraper

An apartment-hunting agent built on [Hermes Agent](https://hermes-agent.nousresearch.com). It runs on your Claude Pro or Max subscription and can operate desktop apps through computer use.

## Install

You need Windows 10 1809 or later, or macOS 13 or later on Apple Silicon, plus Git and a Claude Pro or Max subscription. Clone the repository and run the installer from its root.

Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

macOS:

```bash
./install.sh
```

The installer:

1. installs or updates Claude Code, and opens the Claude login if you are logged out,
2. installs Hermes Agent with its computer-use driver, or updates an older Hermes,
3. installs `profile/` as the Hermes profile `apt-scraper`,
4. installs the Claude subscription provider plugin into that profile,
5. prepares the computer-use driver, and on macOS asks for Accessibility and Screen Recording access,
6. sends one test message through your subscription.

Run it again after pulling changes. Your settings, memories, sessions and credentials stay in place.

## Run

Open a new terminal and run `hermes -p apt-scraper`.

Ask Hermes to open the apartment gallery. It opens a local browser app with listings, favorites and notes. Until search results exist, it opens a separate sample gallery. See [the gallery guide](docs/gallery.md) for direct launch commands, development and the shared listing store.

## Your search

Before the first search, the agent asks about the apartment you want: where, your budget, size, rooms, move-in date and your other must-haves, then what you like and dislike, which listing sites to search, and when you can attend viewings. It also asks about the places you go to regularly, such as work: how you get there, the longest trip you accept and how often you go. "None" is an answer too. Then it asks how much those places, being near a train or bus stop, and each of your areas matter to you. It starts these questions in your first conversation and picks up where you left off if you stop. Your must-haves filter listings out. Your likes and dislikes, places and location priorities only affect the ranking, and the agent never tells listing agents about your places.

To change anything later, such as a new work address, tell the agent, in the terminal or in the gallery's Notes tab. When you change a must-have, the gallery hides the listings that no longer fit and shows again the ones that do.

Once the questions are answered, the search starts. After that, a new question in a later version is asked in your next conversation and does not stop the search. It reads your listing sites every hour, adds new listings to the gallery, updates changed ones and marks gone ones unavailable. Zillow is the only listing site it can read so far. This runs as the Hermes scheduled job "Listing search" inside the gateway, so the gateway must be running, and the agent tells you when the search has stopped or a site has blocked it. See [the search guide](docs/search.md).

## Listing features

Within an hour of finding a listing, the agent reads all its photos and its text once and stores what it saw: the room each photo shows, ratings such as natural light and kitchen finish, facts from the text such as in-unit laundry or pets, short tags such as "exposed brick", and whether the listing meets each like and dislike you stated, for the ranking. A listing is read again only when it changes. Each listing is read by a subagent of its own on Claude Sonnet, so the photos never fill your conversation with the agent. When you state a new like or dislike, the agent checks the listings already read against it from what it stored, without the photos. This runs as the Hermes scheduled job "Listing features" inside the gateway, every 10 minutes, and makes no model call when nothing waits. When a listing cannot be read after three tries, the agent tells you. See [the ranking guide](docs/ranking.md).

## Contacting listings

Press Request contact on a listing in the gallery. Within a few minutes the agent writes to the listing agent as you, by email, by text on macOS, or through the listing site's contact form, and answers the replies without asking you each time. It agrees a viewing time only inside your viewing availability and when your calendar is free, then puts the viewing in your Google Calendar with the address, the listing link and the listing agent's contact details. A moved viewing updates its event and a canceled one removes it. The conversation shows in the gallery's Contacted tab, and booked viewings in Viewings. When the listing agent asks for something about you that you have not approved for that listing, such as your date of birth or phone number, or proposes a time the agent cannot settle, the question reaches you in the Telegram chat, and nothing more goes to that listing agent until you answer. The agent never applies, pays, signs or negotiates.

This runs as the Hermes scheduled job "Listing correspondence" inside the gateway, every 2 minutes. A run with nothing to do makes no model call. The gateway must be running, and Gmail, Google Calendar, the messaging gateway and, on macOS, iMessage must pass their checks.

## Usage and billing

Every message draws on your Claude subscription allowance, the same pool `claude -p` uses. Turn off extra usage in your Claude account settings if you don't want overage charges. The provider plugin is marked experimental by its maintainers.

## Connect your accounts

The first time you talk to the agent, it checks your connections and offers to set up the missing ones. It offers again in later conversations until they work. The agent does the setup itself through the terminal, your browser and computer use. You sign in, approve Google's consent screen, paste your Telegram bot token into the setup wizard, and answer macOS permission dialogs.

- Gmail and Google Calendar: an OAuth client in a Google Cloud project of your own, about ten minutes.
- Messaging gateway: a Telegram bot you create with BotFather. The agent's questions arrive there, and your replies reach it while the gateway runs.
- iMessage, macOS only: the `imsg` tool from Homebrew, which needs macOS 14 or later. On macOS 13, texting stays off. You give Full Disk Access to the terminal app you run Hermes in and to `/usr/bin/osascript`, which runs the gateway, and you allow both to control Messages when macOS asks.

A connection that fails its check is not used until it passes. To check again, tell the agent "check my connections".
