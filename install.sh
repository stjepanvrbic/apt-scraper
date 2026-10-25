#!/usr/bin/env bash
# Installs Claude Code, Hermes Agent with its computer-use driver, the Claude
# subscription provider plugin and this repository's Hermes profile on macOS
# and Linux, and stores a Claude setup token for the profile.
# Safe to re-run: every step updates what is already there.
set -euo pipefail

PROFILE_NAME=apt-scraper
PLUGIN_NAME=claude-subscription-directsdk
MINIMUM_CLAUDE_VERSION=2.1.284
MINIMUM_HERMES_VERSION=0.22.5
PROFILE_SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/profile"
PROFILE_HOME="$HOME/.hermes/profiles/$PROFILE_NAME"
TOKEN_LINE='^[[:space:]]*(export[[:space:]]+)?CLAUDE_CODE_OAUTH_TOKEN[[:space:]]*='
export PATH="$HOME/.local/bin:$PATH"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }

# Sets token to one from `claude setup-token`, or to nothing to keep the saved
# one when $1 is true.
read_setup_token() {
    local prompt='Setup token: '
    if $1; then prompt='Setup token (Enter keeps the saved one): '; fi
    while :; do
        IFS= read -rsp "$prompt" token || { echo 'No terminal to read the setup token from' >&2; exit 1; }
        echo
        token=${token//[[:space:]]/}
        if [[ $token =~ ^sk-ant-oat[0-9]+-[A-Za-z0-9_-]+$ ]] || { $1 && [ -z "$token" ]; }; then return; fi
        echo 'That is not a token from `claude setup-token`. Paste the whole line it prints.' >&2
    done
}

# Written here rather than with `hermes config set`, which would put the token
# on a command line. mktemp creates the file readable by its owner only.
save_setup_token() {
    local env_file=$1 new_env
    new_env=$(mktemp "$env_file.XXXXXX")
    {
        if [ -f "$env_file" ]; then grep -Ev "$TOKEN_LINE" "$env_file" || true; fi
        printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$2"
    } > "$new_env"
    mv "$new_env" "$env_file"
}

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

step 'Hermes Agent and computer-use driver'
if ! command -v hermes >/dev/null; then
    run_remote_installer https://hermes-agent.nousresearch.com/install.sh --non-interactive
elif version_below "$(hermes --version)" "$MINIMUM_HERMES_VERSION"; then
    hermes update
fi

# Reinstalling from this checkout keeps working after the clone moves, which
# `hermes profile update` does not. The user's config.yaml is kept.
step "Hermes profile '$PROFILE_NAME'"
config="$PROFILE_HOME/config.yaml"
saved_config=$(mktemp)
had_config=false
if [ -f "$config" ]; then cp "$config" "$saved_config"; had_config=true; fi
install_status=0
hermes profile install "$PROFILE_SOURCE" --force --yes || install_status=$?
if $had_config; then cp "$saved_config" "$config"; fi
rm -f "$saved_config"
[ "$install_status" -eq 0 ] || exit "$install_status"

# Claude Code prefers this token over a `claude auth login` session.
step 'Claude setup token'
env_file="$PROFILE_HOME/.env"
has_saved_token=false
if [ -f "$env_file" ] && grep -Eq "${TOKEN_LINE}[^[:space:]]" "$env_file"; then has_saved_token=true; fi
echo 'Run `claude setup-token` in another terminal, approve access in the browser, and paste the token it prints.'
read_setup_token "$has_saved_token"
if [ -n "$token" ]; then save_setup_token "$env_file" "$token"; fi

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
    echo "Test message failed. If the setup token is wrong or expired, run the installer again and paste a new one. Reply: $reply" >&2
    exit 1
fi

printf '\n\033[32mInstalled. Run: hermes -p %s\033[0m\n' "$PROFILE_NAME"
