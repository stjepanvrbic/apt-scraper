# apt-scraper

Hermes Agent distribution for apartment hunting: `install.ps1` (Windows) and `install.sh` (macOS) install Claude Code, Hermes with its computer-use driver and the Claude subscription provider plugin, then install `profile/` as the Hermes profile `apt-scraper`. Owner: Stjepan Vrbic.

Intent, spec and work are tracked in files, not Linear: `docs/intent.md`, `docs/spec.md`, tickets in `docs/tickets/`, format in `docs/tickets/README.md`.
Documentation lives under `docs/`. `README.md` is the only other prose file at the root.
`profile/` reaches installed agents when the user re-runs the installer, which keeps each user's `config.yaml`.

Agents never open or focus the owner's browser. Browser checks run headless (Playwright) with screenshots; the gallery's open-in-browser path is verified by checking the command it would run, not by running it. Hermes sessions started for testing use `-Q` or an equivalent that opens nothing.
