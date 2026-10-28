# apt-scraper

Hermes Agent distribution for apartment hunting: `install.ps1` (Windows) and `install.sh` (macOS) install Hermes with its computer-use driver and `profile/` as the Hermes profile `apt-scraper`. By default the agent runs on the user's Claude subscription, for which they also install Claude Code and the Claude subscription provider plugin; a user who declines picks another provider in Hermes's own model menu. `bootstrap.ps1` and `bootstrap.sh` are the one-line install: they clone or update the repository in a per-user location and run the installer from there. `uninstall.ps1` and `uninstall.sh` are the one-line uninstall: they remove everything the installer, the bootstraps and the profile put on the machine and keep Hermes and Claude Code. Owner: Stjepan Vrbic.

Intent, spec and work are tracked in files, not Linear: `docs/intent.md`, `docs/spec.md`, tickets in `docs/tickets/`, format in `docs/tickets/README.md`.
Documentation lives under `docs/`. `README.md` is the only other prose file at the root.
`profile/` reaches installed agents when the user re-runs the installer, which keeps each user's `config.yaml`.

Agents never open or focus the owner's browser. Browser checks run headless (Playwright) with screenshots; the gallery's open-in-browser path is verified by checking the command it would run, not by running it. Hermes sessions started for testing use `-Q` or an equivalent that opens nothing.
