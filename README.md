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

1. installs or updates Claude Code,
2. installs Hermes Agent with its computer-use driver, or updates an older Hermes,
3. installs `profile/` as the Hermes profile `apt-scraper`,
4. asks for a Claude setup token and saves it in that profile's `.env`,
5. installs the Claude subscription provider plugin into that profile,
6. prepares the computer-use driver, and on macOS asks for Accessibility and Screen Recording access,
7. sends one test message through your subscription.

When the installer asks for the token, open a second terminal and run `claude setup-token`. It opens your browser, and after you approve access it prints a token that lasts one year. Paste it into the installer, which keeps the input hidden.

Run the installer again after pulling changes, and press Enter at the token prompt to keep the saved token. Your settings, memories, sessions and credentials stay in place. When the token expires, run the installer again and paste a new one.

## Run

Open a new terminal and run `hermes -p apt-scraper`.

## Usage and billing

Every message draws on your Claude subscription allowance, the same pool `claude -p` uses. Turn off extra usage in your Claude account settings if you don't want overage charges. The provider plugin is marked experimental by its maintainers.
