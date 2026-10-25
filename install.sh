#!/usr/bin/env bash
# Installs Claude Code, Hermes Agent with its computer-use driver, the Claude
# subscription provider plugin and this repository's Hermes profile on macOS
# and Linux, then opens the profile's chat in the Hermes web dashboard.
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

# The account connections use Hermes's built-in google-workspace skill, which installing a profile
# from a directory does not copy.
step 'Google Workspace skill'
hermes -p "$PROFILE_NAME" skills install google-workspace --yes

# The search runs curl_cffi in an environment of its own, built from hash-pinned wheels with the Python
# Hermes runs cron scripts with, which `hermes pm env` puts on PATH.
step 'Search environment'
hermes_path=$(hermes pm env | sed -n 's/^ *"PATH": "\(.*\)",\{0,1\}$/\1/p')
python=""
IFS=: read -r -a hermes_directories <<< "$hermes_path"
for directory in "${hermes_directories[@]}"; do
    if [ -x "$directory/python3" ]; then python="$directory/python3"; break; fi
done
if [ -z "$python" ]; then
    echo 'Hermes reports no Python of its own in `hermes pm env`.' >&2
    exit 1
fi
profile_home="$HOME/.hermes/profiles/$PROFILE_NAME"
HERMES_HOME="$profile_home" "$python" "$profile_home/scripts/search-run.py" install

# Hermes installs a profile's shipped cron jobs paused. The listing correspondence job wakes the
# model only when a contact request or a reply needs it, and the listing search searches only once
# the interview is complete, so both stay on.
step 'Listing correspondence and listing search jobs'
hermes -p "$PROFILE_NAME" cron resume apt-scraper-contact
hermes -p "$PROFILE_NAME" cron resume apt-scraper-search

# Each listing is read once by a subagent of its own, pinned to Sonnet through delegation.model. A kept
# config.yaml gains the pin only when it names no subagent model of its own.
step 'Listing features job'
if [ -z "$(hermes -p "$PROFILE_NAME" config get delegation.model)" ]; then
    hermes -p "$PROFILE_NAME" config set delegation.model sonnet
fi
hermes -p "$PROFILE_NAME" cron resume apt-scraper-features

step 'Claude subscription provider plugin'
hermes -p "$PROFILE_NAME" plugins install "$PLUGIN_NAME" --force --enable

step 'Computer-use driver'
hermes -p "$PROFILE_NAME" computer-use install --upgrade
if [ "$(uname -s)" = Darwin ]; then
    step 'Grant Accessibility and Screen Recording to CuaDriver in the dialogs that open'
    hermes -p "$PROFILE_NAME" computer-use permissions grant
fi
hermes -p "$PROFILE_NAME" computer-use status

# One gateway on the machine serves every profile. It runs the scheduled jobs, the messaging gateway
# and every chat turn, including the dashboard's, so it starts at login and the search keeps running
# while no chat is open. Hermes refuses gateway services for named profiles.
step 'Hermes gateway service'
hermes -p default gateway install --if-missing --start-now --start-on-login
hermes -p default gateway start

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

# The first start builds the dashboard's web UI, so later starts need no npm. The dashboard keeps
# running after this terminal closes. A dashboard that is already running prints its address and exits.
step 'Hermes web dashboard'
dashboard_log=$(mktemp)
nohup hermes -p "$PROFILE_NAME" dashboard --no-open </dev/null >"$dashboard_log" 2>&1 &
dashboard_pid=$!
deadline=$((SECONDS + 900))
dashboard_url=""
while [ -z "$dashboard_url" ]; do
    exited=false
    kill -0 "$dashboard_pid" 2>/dev/null || exited=true
    port=$(sed -n 's/.*HERMES_DASHBOARD_READY port=\([0-9][0-9]*\).*/\1/p' "$dashboard_log" | head -n 1)
    if [ -n "$port" ]; then
        dashboard_url="http://127.0.0.1:$port"
    else
        dashboard_url=$(sed -n "s|.*Managing profile '[^']*': \(https\{0,1\}://[^/ ]*\).*|\1|p" "$dashboard_log" | head -n 1)
    fi
    if [ -z "$dashboard_url" ]; then
        if $exited || [ "$SECONDS" -ge "$deadline" ]; then
            echo 'The Hermes dashboard did not start. Its output:' >&2
            cat "$dashboard_log" >&2
            exit 1
        fi
        sleep 1
    fi
done
chat_url="$dashboard_url/chat?profile=$PROFILE_NAME"
if [ "$(uname -s)" = Darwin ]; then open "$chat_url"; else xdg-open "$chat_url"; fi

printf '\n\033[32mInstalled. The Hermes dashboard is open at %s\033[0m\n' "$chat_url"
printf '\033[32mSay hello in its chat to start. To open it again later, run: hermes -p %s dashboard\033[0m\n' "$PROFILE_NAME"
