#!/usr/bin/env bash
# Installs Claude Code, Hermes Agent with its computer-use driver, the Claude
# subscription provider plugin and this repository's Hermes profile on macOS
# and Linux.
# Safe to re-run: every step updates what is already there.
set -euo pipefail

PROFILE_NAME=apt-scraper
PLUGIN_NAME=claude-subscription-directsdk
MINIMUM_CLAUDE_VERSION=2.1.284
MINIMUM_HERMES_VERSION=0.22.5
PROFILE_SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/profile"
export PATH="$HOME/.local/bin:$PATH"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }

# True when the first x.y.z version in $1 is older than $2.
version_below() {
    local version
    version=$(printf '%s\n' "$1" | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -n 1)
    [ "$version" != "$2" ] && [ "$(printf '%s\n%s\n' "$version" "$2" | sort -V | head -n 1)" = "$version" ]
}

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
if version_below "$(claude --version)" "$MINIMUM_CLAUDE_VERSION"; then claude update; fi
auth_status=$(claude auth status || true)
if ! [[ $auth_status =~ \"loggedIn\":\ *true ]]; then
    step 'Log in to Claude with your Pro or Max subscription'
    claude auth login
fi

step 'Hermes Agent and computer-use driver'
if ! command -v hermes >/dev/null; then
    run_remote_installer https://hermes-agent.nousresearch.com/install.sh --non-interactive
elif version_below "$(hermes --version)" "$MINIMUM_HERMES_VERSION"; then
    hermes update
fi

# Reinstalling from this checkout keeps working after the clone moves, which
# `hermes profile update` does not. The user's config.yaml is kept.
step "Hermes profile '$PROFILE_NAME'"
config="$HOME/.hermes/profiles/$PROFILE_NAME/config.yaml"
saved_config=$(mktemp)
had_config=false
if [ -f "$config" ]; then cp "$config" "$saved_config"; had_config=true; fi
install_status=0
hermes profile install "$PROFILE_SOURCE" --force --yes || install_status=$?
if $had_config; then cp "$saved_config" "$config"; fi
rm -f "$saved_config"
[ "$install_status" -eq 0 ] || exit "$install_status"

step 'Claude subscription provider plugin'
hermes -p "$PROFILE_NAME" plugins install "$PLUGIN_NAME" --force --enable

step 'Computer-use driver'
hermes -p "$PROFILE_NAME" computer-use install --upgrade
if [ "$(uname -s)" = Darwin ]; then
    step 'Grant Accessibility and Screen Recording to CuaDriver in the dialogs that open'
    hermes -p "$PROFILE_NAME" computer-use permissions grant
fi
hermes -p "$PROFILE_NAME" computer-use status

step 'Test message through the Claude subscription'
reply=$(hermes -p "$PROFILE_NAME" chat -q 'Reply with exactly: ready' --oneshot -Q) || {
    echo "Test message failed (exit code $?)" >&2
    exit 1
}
reply_lower=$(printf '%s' "$reply" | tr '[:upper:]' '[:lower:]')
if ! [[ $reply_lower =~ (^|[^a-z])ready([^a-z]|$) ]]; then
    echo "Test message failed. Reply: $reply" >&2
    exit 1
fi

printf '\n\033[32mInstalled. Run: hermes -p %s\033[0m\n' "$PROFILE_NAME"
