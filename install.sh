#!/usr/bin/env bash
# Installs apt-scraper on macOS or Linux: Hermes Agent, Claude Code for the Claude subscription, and this
# repository as the Hermes profile apt-scraper. Safe to run again: every step updates what is there.
#   bash <(curl -fsSL https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/install.sh)
# An argument names another profile source for `hermes profile install`, such as a clone of this repository.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
profile=apt-scraper
source=${1:-github.com/stjepanvrbic/apt-scraper}

command -v hermes >/dev/null || curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive
# The Claude subscription provider plugin runs on the official Claude Code CLI, installed and logged in.
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash
if ! claude auth status 2>/dev/null | grep -q '"loggedIn": *true'; then
    if [ -t 0 ]; then claude auth login; else echo "No terminal to log in to Claude on. Run: claude auth login"; fi
fi

hermes profile install "$source" --force --yes
hermes -p "$profile" plugins install claude-subscription-directsdk --force --enable
hermes -p "$profile" skills install google-workspace --yes
hermes -p "$profile" computer-use install --upgrade
if [ "$(uname -s)" = Darwin ] && [ -t 0 ]; then hermes -p "$profile" computer-use permissions grant; fi
hermes -p "$profile" cron resume apt-scraper-contact
hermes -p "$profile" cron resume apt-scraper-search
hermes -p "$profile" cron resume apt-scraper-features
hermes -p "$profile" cron resume apt-scraper-review
hermes -p "$profile" cron resume apt-scraper-encoder
hermes gateway install --if-missing --start-now --start-on-login

echo "Installed. Open the chat with: hermes -p $profile dashboard"
