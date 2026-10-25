---
title: Where the Claude setup token lives when the host gateway serves the profile
type: Decision
state: Todo
priority: 4
assignee: Stjepan Vrbic
milestone:
parent:
blocked_by: []
related: [10]
---

## Question

A token saved in the `apt-scraper` profile's `.env` never reaches Claude Code, so it cannot replace a `claude auth login` session as planned in 0010 (Log in to Claude with a pasted setup token). This blocks that ticket's first acceptance item.

Hermes 0.22.5 runs one host gateway per machine, launched from the default profile, and it serves every other profile. `hermes chat` always attaches to it (Hermes docs, `user-guide/cli.md` and `user-guide/multi-profile-gateways.md` › No new per-profile gateways). A served profile's `.env` stays out of the process environment. Hermes exposes it only through `agent.secret_scope.get_secret` (`agent/secret_scope.py`). The provider plugin `claude-subscription-directsdk` 0.3.3 starts Claude Code with a copy of `os.environ` (`directsdk.py`, `_run`), so it sees only the default profile's `.env`.

Verified on the owner's machine: with a bogus `CLAUDE_CODE_OAUTH_TOKEN` in `profiles/apt-scraper/.env`, `hermes -p apt-scraper chat -q 'Reply with exactly: ready' --oneshot -Q` still answered `ready` through the owner's Claude login. The agent log shows the turn ran in the host gateway. Claude Code ranks `CLAUDE_CODE_OAUTH_TOKEN` above a `/login` session (code.claude.com/docs/en/authentication › Authentication precedence), so a token that reached it would have failed the request.

## Options

1. **Save the token in the default profile's `.env`** (`%LOCALAPPDATA%\hermes\.env`, `~/.hermes/.env`). That file is in the host gateway's process environment, so the plugin passes the token to Claude Code with no upstream change. The token then applies to every profile the host gateway serves and to Hermes' own Anthropic provider in the default profile. The installer would write into a profile this repository does not own. The gateway loads that file into its environment at start (Hermes docs, `user-guide/configuration.md`), so a running gateway probably needs a restart to pick up a new token. Not yet verified, because the test needs a restart of the owner's gateway.
2. **Have the plugin read the token through Hermes' profile secret scope.** The plugin would look up `CLAUDE_CODE_OAUTH_TOKEN` with `get_secret` for the profile being served and put it in Claude Code's environment. The token stays in the `apt-scraper` profile as 0010 specifies and works under the host gateway. This needs a change in `NousResearch/hermes-official-plugins` and a release, and the installer change waits for it.

`gateway.standalone: true` in the profile's `config.yaml` would also load the profile's `.env`. The Hermes docs call it a temporary shim that will be removed, so it is not an option.

Recommendation: option 2. It keeps the token scoped to the profile that uses it, and the installer change on `feat/claude_setup_token_login` already writes it there. Option 1 is the fallback if upstream declines or is slow.

## Decision

## Reason

## Consequences
