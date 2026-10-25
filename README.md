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

Before the first search, the agent asks about the apartment you want: where, your budget, size, rooms, move-in date and your other must-haves, then what you like and dislike, which listing sites to search, and when you can attend viewings. It starts these questions in your first conversation and picks up where you left off if you stop. Your must-haves filter listings out. Your likes and dislikes only affect the ranking.

To change anything later, tell the agent, in the terminal or in the gallery's Notes tab. When you change a must-have, the gallery hides the listings that no longer fit and shows again the ones that do.

Once the questions are answered, the search starts. It reads your listing sites every hour, adds new listings to the gallery, updates changed ones and marks gone ones unavailable. Zillow is the only listing site it can read so far. This runs as the Hermes scheduled job "Listing search" inside the gateway, so the gateway must be running, and the agent tells you when the search has stopped or a site has blocked it. See [the search guide](docs/search.md).

## Ranking

Every listing in Rankings shows its rank and a one-line reason. Favorite the listings you like and dismiss the ones you don't, and add notes after a viewing. Within a few minutes, listings that resemble your favorites and the places your notes praise move up, and listings that resemble what you dismissed or criticized move down, by their photos, price, description, location and amenities. Your stated likes and dislikes count too. The agent looks at each new listing once, and again only when the listing changes, and reads each note and each like or dislike once. A like or dislike the agent's fixed list of features describes well is matched against what it stored about every listing, so it costs one read, not one per listing. A wish the list describes only loosely or not at all, such as being near a particular address, is checked listing by listing, so the ranking stays right, and the agent records why. When your search has other must-haves, the search reads each new listing once more to check them. Reordering after a favorite or a dismissal uses what was stored and makes no model call. This runs as the Hermes scheduled job "Listing ranking" inside the gateway, every 2 minutes. See [the ranking guide](docs/ranking.md).

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
