#!/usr/bin/env bash
# Removes everything apt-scraper put on this machine: the dashboard and the account setup's browser while
# they run, the dashboard's LaunchAgent and the apt-scraper app in Applications, the Hermes profile
# apt-scraper and its deletion mark, and ~/Library/Application Support/apt-scraper with the listing store
# and the clone. Hermes, its gateway service and Claude Code stay unless given --everything, which also
# removes the gateway service and Hermes. Claude Code always stays, because it holds the user's own login.
# Needs no clone and asks nothing; whatever is already gone is skipped, so it is safe to run twice.
# Written for:
#   curl -fsSL https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/uninstall.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/uninstall.sh | bash -s -- --everything
set -euo pipefail

PROFILE_NAME=apt-scraper
HERMES_ROOT="$HOME/.hermes"
PROFILE_HOME="$HERMES_ROOT/profiles/$PROFILE_NAME"
DELETION_MARK="$HERMES_ROOT/profiles/.deleted/$PROFILE_NAME"
DATA_DIRECTORY="$HOME/Library/Application Support/apt-scraper"
DASHBOARD_LABEL=io.github.stjepanvrbic.apt-scraper.dashboard
DASHBOARD_AGENT="$HOME/Library/LaunchAgents/$DASHBOARD_LABEL.plist"
APP="$HOME/Applications/$PROFILE_NAME.app"
DASHBOARD_LOGS="${TMPDIR:-/tmp}"
DASHBOARD_LOGS="${DASHBOARD_LOGS%/}/apt-scraper-dashboard."
LINE=https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/uninstall.sh
export PATH="$HOME/.local/bin:$PATH"
# Hermes asks nothing then, and takes the answer it would default to.
export HERMES_NONINTERACTIVE=1

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
problem() { printf '\033[33m%s\033[0m\n' "$1" >&2; }

# A failure is reported and the removal goes on.
run_hermes() {
    local status=0
    hermes "$@" || status=$?
    if [ "$status" -ne 0 ]; then problem "'hermes $*' failed with exit code $status."; fi
}

# The dashboard runs as hermes with the profile's name, and the account setup's browser on a user-data
# directory inside the profile. Only processes naming these are stopped.
profile_processes() {
    local pid command
    ps -A -o pid= -o command= 2>/dev/null | while read -r pid command; do
        [ "$pid" = "$$" ] && continue
        case " $command " in
            *"$PROFILE_HOME/"* | *"$PROFILE_HOME "* | \
            *hermes*" -p $PROFILE_NAME "* | *hermes*" --profile $PROFILE_NAME "* | *hermes*" --profile=$PROFILE_NAME "*)
                printf '%s\n' "$pid" ;;
        esac
    done
}

stop_profile_processes() {
    local pids pid waited=0
    pids=$(profile_processes)
    [ -n "$pids" ] || return 0
    for pid in $pids; do kill "$pid" 2>/dev/null || true; done
    while [ "$waited" -lt 50 ]; do
        local running=false
        for pid in $pids; do if kill -0 "$pid" 2>/dev/null; then running=true; fi; done
        $running || return 0
        sleep 0.2
        waited=$((waited + 1))
    done
    for pid in $pids; do kill -9 "$pid" 2>/dev/null || true; done
}

# bash runs a piped script while it reads it, so everything happens in main, called on the last line:
# a cut-off download runs nothing.
main() {
    local everything=false argument hermes_present=false
    for argument in "$@"; do
        case $argument in
            --everything) everything=true ;;
            *) echo "Unknown argument '$argument'. The only argument is --everything." >&2; exit 2 ;;
        esac
    done
    if command -v hermes >/dev/null; then hermes_present=true; fi

    echo 'This removes apt-scraper from this computer:'
    echo "  - the apt-scraper dashboard and the account setup's browser window, if they run,"
    echo "  - the dashboard's login entry and the apt-scraper app in Applications,"
    echo "  - the Hermes profile $PROFILE_NAME with its settings, scheduled jobs, Google token, connection results, setup browser profile and Python environment ($PROFILE_HOME),"
    echo "  - the apt-scraper folder with the listing store and the downloaded copy of apt-scraper ($DATA_DIRECTORY)."
    if $everything; then
        echo 'It also removes the Hermes gateway service and Hermes. Hermes keeps its own settings and data.'
        echo 'Claude Code stays, because it holds your Claude login.'
    else
        echo 'Hermes, the Hermes gateway service and Claude Code stay.'
    fi

    # Booting the LaunchAgent out stops the dashboard launchd runs.
    step 'Dashboard login entry and apt-scraper app'
    if command -v launchctl >/dev/null; then
        launchctl bootout "gui/$(id -u)/$DASHBOARD_LABEL" >/dev/null 2>&1 || true
    fi
    rm -f "$DASHBOARD_AGENT"
    rm -rf "$APP"

    step 'Dashboard and setup browser'
    stop_profile_processes

    # `hermes profile delete` stops the profile's backends, releases what the gateway holds in it and
    # removes it, leaving a deletion mark in profiles/.deleted. The mark goes too, so a later install is a
    # fresh one.
    step "Hermes profile '$PROFILE_NAME'"
    if $hermes_present && [ -d "$PROFILE_HOME" ] && [ ! -e "$DELETION_MARK" ]; then
        run_hermes profile delete "$PROFILE_NAME" --yes
    fi
    rm -rf "$PROFILE_HOME" "$DELETION_MARK"

    step "apt-scraper folder $DATA_DIRECTORY"
    case "$PWD/" in "$DATA_DIRECTORY/"*) cd "$HOME" ;; esac
    rm -rf "$DATA_DIRECTORY"
    rm -f "$DASHBOARD_LOGS"*

    if $everything && $hermes_present; then
        step 'Hermes gateway service'
        run_hermes -p default gateway uninstall
        step 'Hermes'
        run_hermes uninstall --yes
    fi

    printf '\n\033[32mapt-scraper is removed.\033[0m\n'
    if ! $everything; then
        echo 'Hermes, its gateway service and Claude Code stay. To remove the Hermes gateway service and Hermes:'
        echo '  hermes gateway uninstall'
        echo '  hermes uninstall'
        echo 'or run this line again with --everything:'
        echo "  curl -fsSL $LINE | bash -s -- --everything"
    elif $hermes_present; then
        echo "Hermes kept its settings and data in $HERMES_ROOT. Delete that folder to remove them too."
    fi
    echo 'To remove Claude Code, which holds your Claude login, follow https://code.claude.com/docs/en/setup#uninstall-claude-code'
    echo '  rm -f ~/.local/bin/claude'
    echo '  rm -rf ~/.local/share/claude'
}

main ${1+"$@"}
