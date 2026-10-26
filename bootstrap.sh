#!/usr/bin/env bash
# Installs Git when it is missing, clones this repository into ~/Library/Application Support/apt-scraper
# or updates the clone there, and runs the installer from the clone with this script's arguments.
# Written for:
#   curl -fsSL https://raw.githubusercontent.com/stjepanvrbic/apt-scraper/main/bootstrap.sh | bash
set -euo pipefail

REPOSITORY_URL=https://github.com/stjepanvrbic/apt-scraper.git
REPOSITORY="$HOME/Library/Application Support/apt-scraper/repo"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }

# bash runs a piped script while it reads it, so everything happens in main, called on the last line:
# a cut-off download runs nothing.
main() {
    if ! git --version >/dev/null 2>&1; then
        if [ "$(uname -s)" != Darwin ]; then
            echo 'Install Git and run this again.' >&2
            exit 1
        fi
        xcode-select --install || true
        echo 'Git comes with the Xcode command line tools. Install them in the dialog that opened, then run this again.' >&2
        exit 1
    fi

    if [ -d "$REPOSITORY/.git" ]; then
        step "Updating $REPOSITORY"
        git -C "$REPOSITORY" pull --ff-only
    else
        step "Cloning apt-scraper into $REPOSITORY"
        git clone "$REPOSITORY_URL" "$REPOSITORY"
    fi

    # Standard input is this script's pipe, and the installer asks for the Claude login and passwords.
    if (exec </dev/tty) 2>/dev/null; then
        bash "$REPOSITORY/install.sh" "$@" </dev/tty
    else
        bash "$REPOSITORY/install.sh" "$@"
    fi
}

main "$@"
