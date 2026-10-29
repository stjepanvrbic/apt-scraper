<img src="assets/icon.svg" width="64" height="64" alt="">

# apt-scraper

An apartment-hunting agent built on [Hermes Agent](https://hermes-agent.nousresearch.com). It runs on your Claude Pro or Max subscription by default, or on any provider Hermes supports, and can operate desktop apps through computer use.

## Install

You need Windows 10 1809 or later, or macOS 13 or later on Apple Silicon, and a Claude Pro or Max subscription. Paste one line into a terminal.

Windows, in PowerShell:

```powershell
iex (irm https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/install.ps1)
```

macOS, in Terminal:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/install.sh)
```

The line is a short list of commands, and nothing else: it runs [Hermes Agent's installer](https://hermes-agent.nousresearch.com/) if `hermes` is missing, [Claude Code's installer](https://code.claude.com/docs) and the Claude login if `claude` is missing or logged out, then installs this repository as the Hermes profile `apt-scraper` with `hermes profile install`, adds the Claude subscription provider plugin, Hermes's Google Workspace skill and the computer-use driver, resumes the profile's scheduled jobs, and installs the Hermes gateway as a service that starts when you log in. On macOS it also asks for the Accessibility and Screen Recording permissions the computer-use driver needs, and on Windows adding the gateway service may ask for administrator approval. Then it prints how to open the chat.

To update, paste the same line again. Your settings, memories, sessions and credentials stay in place.

To run the agent on another provider Hermes supports, run `hermes -p apt-scraper model` after the install and pick it in Hermes's menu. Only Claude models have been measured for reading and ranking listings.

### Uninstall

```bash
hermes profile delete apt-scraper --yes
```

Then remove the listing store and the gallery's saved connections: `%LOCALAPPDATA%\apt-scraper` and `.secrets\apt-scraper` in your user folder on Windows, `~/Library/Application Support/apt-scraper` and `~/.secrets/apt-scraper` on macOS. Hermes, the gateway service and Claude Code stay; `hermes gateway uninstall` and `hermes uninstall` remove Hermes.

### Development

To install from a clone of your own, point Hermes at it:

```bash
hermes profile install /path/to/your/clone --force --yes
```

The checks run in a fresh virtual environment through one script per platform, which installs `tests/requirements.txt` and runs Ruff, basedpyright, the Python suite with zero skips, then the gallery's lint, build and Playwright suite. macOS or Linux:

```bash
tests/run.sh
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\tests\run.ps1
```

GitHub Actions runs the same scripts on a fresh Linux, macOS and Windows machine for every push, and runs the install line itself on another fresh machine of each, with the checkout as the profile, then checks what it left behind: the five scheduled jobs running, the plugin, the skill, the computer-use driver and the gateway service. The Claude login and the macOS permission dialogs need a person, so the install skips them when it has no terminal and says what to run.

## Run

You talk to the agent in the chat of the Hermes web dashboard. Open it with `hermes -p apt-scraper dashboard`; the first start builds the dashboard and takes a few minutes. Say hello to start. The gateway runs in the background and starts again when you log in, so the search and your conversations keep going while the dashboard is closed.

Ask the agent to open the apartment gallery. It opens a local browser app with listings, favorites and notes. Until search results exist, it opens a separate sample gallery. See [the gallery guide](docs/gallery.md) for direct launch commands, development and the shared listing store.

## Your search

Before the first search, the agent asks about the apartment you want: where, your budget, size, rooms, move-in date and your other must-haves, then what you like and dislike, which listing sites to search, and when you can attend viewings. It also asks about the places you go to regularly, such as work, and looks up each address once on OpenStreetMap. "None" is an answer too. Then it asks how much being close to those places, being close to a train station, and each of your areas matter to you. Closeness is the straight-line distance from a listing. The stations come from Chicago's CTA schedule data, or from your city's transit agency when you tell the agent another city. It starts these questions in your first conversation and picks up where you left off if you stop. Your must-haves filter listings out. Your likes and dislikes, places and location priorities only affect the ranking, and the agent never tells listing agents about your places.

To change anything later, such as a new work address, tell the agent, in the dashboard chat, in Telegram or in the gallery's Notes tab. When you change a must-have, the gallery hides the listings that no longer fit and shows again the ones that do.

Once the questions are answered, the search starts. After that, a new question in a later version is asked in your next conversation and does not stop the search. It reads your listing sites every hour, adds new listings to the gallery, updates changed ones and marks gone ones unavailable. Zillow is the only listing site it can read so far. This runs as the Hermes scheduled job "Listing search" inside the gateway, so the gateway must be running, and the agent tells you when the search has stopped or a site has blocked it. See [the search guide](docs/search.md).

## Listing features

Within an hour of finding a listing, the agent reads all its photos and its text once and stores what it saw: the room each photo shows, ratings such as natural light and kitchen finish, facts from the text such as in-unit laundry or pets, short tags such as "exposed brick", and whether the listing meets each like and dislike you stated, for the ranking. A listing is read again only when it changes. Each listing is read by a subagent of its own, on Claude Sonnet with the Claude subscription or on the model you picked in Hermes's menu otherwise, so the photos never fill your conversation with the agent. When you state a new like or dislike, the agent checks the listings already read against it from what it stored, without the photos. This runs as the Hermes scheduled job "Listing features" inside the gateway, every 10 minutes, and makes no model call when nothing waits. When a listing cannot be read after three tries, the agent tells you. See [the ranking guide](docs/ranking.md).

## Ranking

The order of your listings changes only when you ask the agent to rerank. A rerank takes about a second on your computer and makes no model call. It learns from your favorites, contact requests, dismissals, notes and stated likes and dislikes, and your favorites can outweigh what you said: if you said you dislike carpet but keep favoriting carpeted listings, carpet stops counting against them. Each listing gets a one-line reason naming what moved it most and the favorite it most resembles. Up to two of the top 20 are there to learn your taste and are marked so. After each rerank the agent tells you how well the ranking places each favorite when it learns from the others only. Within a few minutes, the agent's model then reviews the top 50 once, in the Hermes scheduled job "Ranking review", which is the only part of a rerank that calls a model: it rewrites their reasons, flags listings that rank against what you said you like or dislike, points out what looks wrong in the ranking, and may move listings. Its moves are kept only when they do not make your favorites rank worse in that check, and are undone otherwise. The agent reads each note you write and each like or dislike you state once, within the hour, in the "Listing features" job. See [the ranking guide](docs/ranking.md).

## Contacting listings

Press Request contact on a listing in the gallery. Within a few minutes the agent writes to the listing agent as you, by email, by text on macOS, or through the listing site's contact form, and answers the replies without asking you each time. It agrees a viewing time only inside your viewing availability and when your calendar is free, then puts the viewing in your Google Calendar with the address, the listing link and the listing agent's contact details. A moved viewing updates its event and a canceled one removes it. The conversation shows in the gallery's Contacted tab, and booked viewings in Viewings. When the listing agent asks for something about you that you have not approved for that listing, such as your date of birth or phone number, or proposes a time the agent cannot settle, the question reaches you in the Telegram chat, and nothing more goes to that listing agent until you answer. The agent never applies, pays, signs or negotiates.

This runs as the Hermes scheduled job "Listing correspondence" inside the gateway, every 2 minutes. A run with nothing to do makes no model call. The gateway must be running, and Gmail, Google Calendar, the messaging gateway and, on macOS, iMessage must pass their checks.

## Usage and billing

On the Claude subscription, every message draws on your Claude subscription allowance, the same pool `claude -p` uses. Turn off extra usage in your Claude account settings if you don't want overage charges. The provider plugin is marked experimental by its maintainers. On a provider you picked in Hermes's menu, every message is billed by that provider, against your plan or per token, as your account with it bills.

## Connect your accounts

The first time you talk to the agent, it checks your connections and offers to set up the missing ones. It offers again in later conversations until they work. The agent does the setup itself: it runs the commands and works in a window of your own browser (Brave, Chrome or Edge, whichever is your default) that it opens on a profile of apt-scraper's own, while you keep chatting. Your usual browser profile is never touched and can stay open. The window is signed out the first time: sign in to Google and Telegram there when a page asks, and later setups find you signed in. The window is used only while the setup runs. The setup waits for a listing correspondence run that is filling in contact forms, and no new run starts while the setup uses the window; at the end the agent closes it. Without Brave, Chrome or Edge installed, the agent asks you to install one first. You approve Google's consent screen, paste your Telegram bot token into the dashboard's Telegram form when the agent asks, and answer macOS permission dialogs. On a Mac without Homebrew, the agent types its installer into a Terminal window through computer use: allow computer use for the session when Hermes asks.

- Gmail and Google Calendar: an OAuth client in a Google Cloud project of your own, about ten minutes.
- Messaging gateway: a Telegram bot you create with BotFather. The agent's questions arrive there, and your replies reach it while the gateway runs.
- iMessage, macOS only: the `imsg` tool from Homebrew, which needs macOS 14 or later. On macOS 13, texting stays off. You give Full Disk Access to `/usr/bin/osascript`, which runs the gateway, and to the terminal app you run Hermes in, and you allow Messages control when macOS asks.

A connection that fails its check is not used until it passes. To check again, tell the agent "check my connections".
