---
title: Log in to Claude with a pasted setup token
type: Feature
state: In Progress
priority: 4
assignee: agent
milestone:
parent:
blocked_by: [11]
related: []
---

## Outcome

The installer asks the user for a token from `claude setup-token`, stores it as `CLAUDE_CODE_OAUTH_TOKEN` for the `apt-scraper` profile, and the agent authenticates with it instead of a `claude auth login` session.

## Acceptance

- [ ] On a machine with no Claude login, pasting a setup token lets the installer's test message pass.
- [x] The token is stored only in the profile's Hermes `.env`, never in the repository or the installer output.

## Spec

[Spec › Installation and setup](../spec.md)

## Design

The plugin accepts `CLAUDE_CODE_OAUTH_TOKEN` from Hermes' environment. The token lasts one year and only makes model requests.

## Deviations

- The design's premise fails under Hermes 0.22.5. One host gateway, launched from the default profile, serves every profile, and `hermes chat` always runs through it. The plugin passes Claude Code the host gateway's process environment, which never holds a served profile's `.env`. A token saved for `apt-scraper` therefore does not reach Claude Code. The first acceptance item waits on 0011 (Where the Claude setup token lives when the host gateway serves the profile).
- The installers write the `.env` line themselves instead of calling `hermes config set CLAUDE_CODE_OAUTH_TOKEN <token>`. That command takes the value as an argument, which puts the token on a process command line and, on failure, in `Invoke-Native`'s error message. Hermes documents editing a profile's `.env` by hand (`user-guide/profiles.md`).

## Result

Delivered in commit `802132a` on `feat/claude_setup_token_login`. `install.ps1` and `install.sh` no longer run `claude auth login`. After installing the profile they ask for a token from `claude setup-token` with hidden input, accept only the `sk-ant-oat<digits>-…` form that Hermes' source gives for setup tokens (`hermes_cli/auth.py`), and replace any `CLAUDE_CODE_OAUTH_TOKEN` line in the profile's `.env` through a temporary file in the same directory. On a re-run, Enter keeps a saved token. A failed test message now says to re-run the installer with a new token. `README.md` describes the new step.

Verified:

- Both scripts parse (`bash -n`, PowerShell `Parser.ParseFile` with 0 errors on Windows PowerShell 5.1).
- Prompt flow, dry run with simulated input: PowerShell with `Read-Host` replaced by a queue, bash with piped input. Garbage and an API key (`sk-ant-api03-…`) are refused, surrounding whitespace is stripped, Enter keeps a saved token and is refused when none is saved. Bash exits 1 with "No terminal to read the setup token from" at end of input.
- `.env` writing on scratch files. Other keys and non-ASCII text survive. `export` and spaced variants of the old line are replaced. The result has no BOM and no CR, no temporary file is left behind, and an empty `CLAUDE_CODE_OAUTH_TOKEN=` does not count as saved. On Linux (WSL Ubuntu, bash 5.2) the file is mode 600, both new and replacing a 644 file. On Windows it inherits the profile directory's ACL, as Hermes' own `.env` writer does.
- Storage. The token appears in no command line and is never printed. The only `sk-ant-` strings in the repository are the validation patterns. In a scratch Hermes home, Hermes' own credential pool recorded the env variable with only a `sha256` fingerprint in `auth.json`, and the logs held no token.
- Live negative test on the owner's machine, which found the deviation above: with a bogus token in `profiles/apt-scraper/.env` the test message still passed through the owner's Claude login. The bogus `.env` was then deleted, which returns the profile to its earlier state.

Not verified: the first acceptance item, which needs a real token and a resolution of 0011. macOS (bash 3.2, BSD `mktemp`) was not run.

Tech debt fixed: the unused `-AllowFailure` switch on `Invoke-Native` is gone, and each installer defines the profile directory once.
