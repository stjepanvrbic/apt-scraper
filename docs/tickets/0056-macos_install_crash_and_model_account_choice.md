---
title: The macOS one-line install crashes in the Claude Code step, and the installer assumes Claude
type: Bug
state: Done
priority: 1
assignee: agent
milestone:
parent:
blocked_by: []
related: [0011, 0042, 0050]
---

## Outcome

The one-line install on macOS gets through the Claude Code step and the Claude login. The installer no longer assumes Claude: the user answers one question, and an agent that does not use the Claude subscription installs nothing of Claude and runs on a provider picked in Hermes's own menu.

## Acceptance

- [x] `curl -fsSL .../bootstrap.sh | bash` runs `claude update` and `claude auth login` without Bun's `EINVAL: invalid argument, kqueue` (GitHub issue #55), and the login still reads the keyboard.
- [x] A test fails on the earlier `bootstrap.sh` with the reported error and passes with the fix, in a real pseudo-terminal whose `/dev/tty` is an alias of the terminal device, as on macOS.
- [x] Both installers ask "Use your Claude Pro or Max subscription? [Y/n]" first. Yes, Enter, and an install without a terminal to ask on take the Claude path as before. No skips Claude Code and the subscription plugin and runs `hermes -p apt-scraper model`; the provider and model it writes survive the profile install, and the test message goes through them.
- [x] Tests for the yes, no and non-interactive paths on both platforms, with zero skipped tests.
- [x] README, docs/spec.md, docs/intent.md and the agent instructions say the agent runs on the Claude subscription by default or on any provider Hermes supports, and that only Claude has been measured.

## Spec

docs/spec.md, Installation and setup: the account question.

## Design

### The crash

`bootstrap.sh` gave the installer standard input from `/dev/tty`, because its own standard input is curl's pipe. On macOS `/dev/tty` is the `ctty` driver (character major 2), an alias that forwards to the real terminal. XNU's `spec_kqfilter` (`bsd/miscfs/specfs/spec_vnops.c`) attaches pseudo-terminal slaves (`CDEVSW_IS_PTS`, the `/dev/ttysNNN` a Terminal window has) to `ptsd_kqfilter`. The `ctty` entry in `cdevsw` (`bsd/dev/*/conf.c`) never gets `CDEVSW_SELECT_KQUEUE`, so it falls to `filt_specattach`, which fails with EINVAL. Claude Code 2.1.x is a Bun binary. Bun 1.4.4 reads `process.stdin` through a native reader that registers file descriptor 0 with `kevent64(EVFILT_READ)` and tags a failure `kqueue` (`src/io/posix_event_loop.rs`). The failed read destroys `process.stdin` (`src/js/builtins/ProcessObjectInternals.ts`, `getStdinStream`), and with no error listener the process dies with exactly the trace in #55: `emitError` → `emitErrorNT` → `emitErrorCloseNT`. `claude --version` and `claude auth status` never read standard input, so only `claude update` and `claude auth login` failed. Bun fixed the write side of `/dev/tty` (oven-sh/bun#24158, `tty.WriteStream`) but not this read side. Run from a Terminal window, standard input is the `/dev/ttysNNN` device itself, which is why `bash install.sh` worked and the one-liner did not. Claude Code's changelog from 2.1.243 to 2.1.312 changes nothing about standard input on macOS, and both versions crashed in the report.

`bootstrap.sh` now opens the terminal's own device, the one `ps -o tty= -p $$` names, and falls back to its standard input without a terminal, as before. Windows is not affected: there is no `/dev/tty` on that path, `bootstrap.ps1` runs `install.ps1` on the console, and Bun on Windows does not use kqueue.

### The account question

Hermes 0.22.5's `hermes model` (`hermes_cli/main.py`, `cmd_model`) is the interactive provider, login and model picker its setup wizard also uses. Run with `-p apt-scraper` it writes `model.provider`, `model.default`, `model.base_url` and `model.api_mode` into the profile's `config.yaml`, an API key into the profile's `.env` and an OAuth login into the profile's `auth.json`. It exits 0 even when cancelled, so the installer checks that the profile no longer names the Claude provider. It runs after the profile install, so the distribution's `config.yaml` cannot overwrite it.

Ticket 0011 found that a served profile's `.env` is not in the host gateway's process environment. That limits the Claude subscription plugin, which starts Claude Code with `os.environ`. Hermes's own providers are not limited: for each session the gateway binds the profile's home and a secret scope built from its `.env` (`tui_gateway/model_switch.py`, `_profile_runtime_scope_tokens`; `tui_gateway/session_registry.py`, `_bind_build_profile_scopes`). The runtime provider then reads the profile's `config.yaml`, `auth.json` pool and `.env` through `get_hermes_home()` (`hermes_cli/config.py`, `get_env_value_prefer_dotenv`; `agent/credential_pool.py`, `load_pool`), re-reading each one when it changes. A picked provider therefore works for the test message under the gateway with no restart and nothing of ours.

The distribution pins the listing features subagent to `sonnet` through `delegation.model`. With an empty `delegation.provider`, Hermes sends that model name to the main provider (`tools/delegate_tool_config.py`), so on the no path the installer removes the pin while it is still the distribution's, and the subagent runs the picked model. On the yes path a profile an earlier install put on another provider gets the distribution's Claude model settings back. Otherwise answering yes on a later run would leave Claude Code installed and the agent on the other provider.

## Deviations

- The owner first asked for a provider list, a per-provider model mapping, a remembered choice and a `--provider` flag, then replaced that with the yes/no question and Hermes's own menu. None of the first design was built.

## Result

`bootstrap.sh` runs the installer on the terminal device, and both installers ask the account question. Tests: `tests/test_installers.py`, `BootstrapTests.test_the_shell_bootstrap_runs_the_claude_code_step_on_the_terminal_device_and_not_through_dev_tty` and `AccountTests`. The pseudo-terminal comes from Git Bash's MSYS perl, because Windows Python has no `pty`. Not run: a real macOS machine, and a real provider through a real gateway, for which this machine has no account to spare. The installer's own test message checks the latter on every install.
