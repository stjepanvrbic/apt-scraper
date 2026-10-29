#!/usr/bin/env bash
# Installs apt-scraper on macOS or Linux: Hermes Agent, Claude Code for the Claude subscription, and this
# repository as the Hermes profile apt-scraper. Safe to run again: every step updates what is there.
#   bash <(curl -fsSL https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/install.sh)
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
profile=apt-scraper

command -v hermes >/dev/null || curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --non-interactive
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash
claude auth status 2>/dev/null | grep -q '"loggedIn": *true' || claude auth login

hermes profile install github.com/stjepanvrbic/apt-scraper --force --yes
hermes -p "$profile" plugins install claude-subscription-directsdk --force --enable
hermes -p "$profile" skills install google-workspace --yes
hermes -p "$profile" computer-use install --upgrade
if [ "$(uname -s)" = Darwin ]; then hermes -p "$profile" computer-use permissions grant; fi
for job in contact search features review encoder; do hermes -p "$profile" cron resume "$profile-$job"; done
hermes gateway install --if-missing --start-now --start-on-login

echo "Installed. Open the chat with: hermes -p $profile dashboard"
