# apt-scraper

Hermes Agent profile for apartment hunting. The repository is the profile: `distribution.yaml` at the root names what `hermes profile install` copies (`SOUL.md`, `config.yaml`, `cron`, `scripts`, `skills`); `docs/`, `tests/` and `gallery/` (the gallery's TypeScript source, built into `skills/gallery/web`) stay in the repository. `install.sh` (macOS, Linux) and `install.ps1` (Windows) are the one-line install: a short list of commands that run Hermes's own installer, Claude Code's own installer and login, `hermes profile install` of this repository, and the Hermes commands that add the subscription plugin, the Google Workspace skill, the computer-use driver, the scheduled jobs and the gateway service. They contain nothing else. The user opens the chat with `hermes -p apt-scraper dashboard`. Owner: Stjepan Vrbic.

Intent, spec and work are tracked in files: `docs/intent.md`, `docs/spec.md`, tickets in `docs/tickets/`, format in `docs/tickets/README.md`.
Documentation lives under `docs/`. `README.md` is the only other prose file at the root.

Agents never open or focus the owner's browser. Browser checks run headless (Playwright) with screenshots; the gallery's open-in-browser path is verified by checking the command it would run, not by running it. Hermes sessions started for testing use `-Q` or an equivalent that opens nothing.

The checks run through `tests/run.sh` (macOS, Linux) or `tests/run.ps1` (Windows) in a fresh virtual environment, and CI (`.github/workflows/ci.yml`) runs them on all three on every push. Every test runs on every platform; `tests/skips.py` fails a run that skipped anything. Test runs happen in CI, not on the owner's PC.
