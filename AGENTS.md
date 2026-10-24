# apt-scraper

Hermes Agent distribution for apartment hunting: `install.ps1` (Windows) and `install.sh` (macOS) install Claude Code, Hermes with its computer-use driver and the Claude subscription provider plugin, then install `profile/` as the Hermes profile `apt-scraper`. Owner: Stjepan Vrbic.

Intent, spec and work are tracked in files, not Linear: `docs/intent.md`, `docs/spec.md`, tickets in `docs/tickets/` and milestones in `docs/milestones/`, format in `docs/tickets/README.md`.
Documentation lives under `docs/`. `README.md` is the only other prose file at the root.
`profile/` reaches installed agents through `hermes profile update`, which keeps each user's `config.yaml`.
