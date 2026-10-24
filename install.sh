#!/usr/bin/env bash
# Installs Claude Code, Hermes Agent with its computer-use driver, the Claude
# subscription provider plugin and this repository's Hermes profile on macOS
# and Linux.
# Safe to re-run: every step updates what is already there.
set -euo pipefail

PROFILE_NAME=apt-scraper
PLUGIN_NAME=claude-subscription-directsdk
MINIMUM_HERMES_VERSION=0.22.5
PROFILE_SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/profile"
export PATH="$HOME/.local/bin:$PATH"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }

run_remote_installer() {
    local url=$1 directory
    shift
    directory=$(mktemp -d)
    curl -fsSL "$url" -o "$directory/install.sh"
    bash "$directory/install.sh" "$@"
    rm -rf "$directory"
}

step 'Claude Code'
command -v claude >/dev/null || run_remote_installer https://claude.ai/install.sh
claude --version

if ! claude auth status 2>/dev/null | grep -q '"loggedIn": *true'; then
    step 'Log in to Claude with your Pro or Max subscription'
    claude auth login
fi

step 'Hermes Agent and computer-use driver'
if ! command -v hermes >/dev/null; then
    run_remote_installer https://hermes-agent.nousresearch.com/install.sh --non-interactive --skip-setup
else
    installed=$(hermes --version | head -n 1 | sed -E 's/^Hermes Agent v([0-9]+\.[0-9]+\.[0-9]+).*$/\1/')
    oldest=$(printf '%s\n%s\n' "$installed" "$MINIMUM_HERMES_VERSION" | sort -V | head -n 1)
    if [ "$installed" != "$MINIMUM_HERMES_VERSION" ] && [ "$oldest" = "$installed" ]; then
        hermes update
    fi
fi

step "Hermes profile '$PROFILE_NAME'"
if hermes profile show "$PROFILE_NAME" >/dev/null 2>&1; then
    hermes profile update "$PROFILE_NAME" --yes
else
    hermes profile install "$PROFILE_SOURCE" --alias --yes
fi

step 'Claude subscription provider plugin'
hermes -p "$PROFILE_NAME" plugins install "$PLUGIN_NAME" --force --enable

step 'Computer-use driver'
hermes -p "$PROFILE_NAME" computer-use install --upgrade
if [ "$(uname -s)" = Darwin ]; then
    step 'Grant Accessibility and Screen Recording to CuaDriver in the dialogs that open'
    hermes -p "$PROFILE_NAME" computer-use permissions grant
fi
hermes -p "$PROFILE_NAME" computer-use status

step 'Smoke test: one turn through the Claude subscription'
reply=$(hermes -p "$PROFILE_NAME" chat -q 'Reply with exactly: ready' --oneshot -Q 2>/dev/null) || {
    echo "Smoke test failed (exit code $?)" >&2
    exit 1
}
if [ "$(printf '%s' "$reply" | tr -d '[:space:]')" != ready ]; then
    echo "Smoke test failed. Reply: $reply" >&2
    exit 1
fi

printf '\n\033[32mInstalled. Open a new terminal and run: %s\033[0m\n' "$PROFILE_NAME"
