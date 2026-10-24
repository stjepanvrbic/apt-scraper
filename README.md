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

1. installs Claude Code if it is missing, and opens the Claude login if you are logged out,
2. installs Hermes Agent with its computer-use driver, or updates an older Hermes,
3. installs `profile/` as the Hermes profile `apt-scraper`,
4. installs the Claude subscription provider plugin into that profile,
5. prepares the computer-use driver, and on macOS asks for Accessibility and Screen Recording access,
6. sends one test message through your subscription.

Run it again after pulling changes. Your memories, sessions and credentials stay in place.

## Run

Open a new terminal and run `apt-scraper`.

## Usage and billing

Every message draws on your Claude subscription allowance, the same pool `claude -p` uses. Turn off extra usage in your Claude account settings if you don't want overage charges. The provider plugin is marked experimental by its maintainers.
