---
title: The installer can leave a gateway running as administrator that nothing can reach
type: Bug
state: In Progress
priority: 1
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: [0027, 0050]
---

## Outcome

On Windows, the one-line install ends with the gateway running as the user, whichever answer the user gives to Hermes's UAC question, and a failing step shows what the failing command printed.

## Acceptance

- [x] Observed on the owner's first run through the fix of 0050: `hermes -p default gateway install --if-missing --start-now --start-on-login` offered the UAC prompt, the user answered y, Hermes's elevated helper created the Scheduled Task and, because of `--start-now`, started the gateway as administrator. The non-elevated `hermes chat` then failed with "Gateway inaccessible: gateway_elevated (The running gateway (PID 49984) was started as administrator, so this non-elevated app cannot reach it)". After the fix, the installer never asks Hermes to start the gateway from an elevated helper: the login entry is installed first, and the gateway is started afterwards by an unelevated `hermes -p default gateway start`.
- [x] Right after the UAC handoff the installer's `gateway start` found no login entry yet and asked "Install it now so the gateway starts on login?", producing a second elevated install. After the fix, the installer waits for the elevated install to finish (the Scheduled Task or Startup entry exists, or the helper exited) before starting the gateway, with a bounded wait and a clear error when it runs out.
- [x] When a gateway started as administrator is already running (the state the owner's machine is in), the installer stops it itself inside the same run: it launches `hermes gateway stop` elevated through one UAC prompt (ShellExecuteW "runas", the way Hermes's own `_launch_elevated_gateway_command` does; the unelevated `hermes gateway stop` cannot end it and offers no UAC), waits with a bound until no gateway is running, then starts the gateway unelevated. The user runs one line and approves one prompt; nothing is left for a second window. If the user declines the prompt, the installer stops with a message that names what is running and why the install cannot continue.
- [x] `Get-NativeOutput` includes the command's captured output in the error it throws, so a failed captured command (the test message) shows its reason on the console.
- [x] `install.sh` is checked for the same shape (macOS launchd does not elevate; confirm nothing equivalent is possible there).
- [x] `tests/test_installers.py` covers: the gateway install runs without `--start-now`; the wait for the login entry; the elevated-gateway state stopped through the elevated handoff and the gateway restarted unelevated, and the declined prompt reported; a failed captured command's output appears in the error.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

docs/spec.md, Installation and setup: the gateway and dashboard run as the user who installed them.

## Design

Hermes (hermes_cli/gateway_windows.py) offers UAC before touching schtasks whenever the process is not elevated. The elevated child runs the same `install` with the same `start_now`, so `--start-now` is what starts the gateway elevated. The Scheduled Task it creates runs unelevated at login, so only the first session is affected. Separating "install the login entry" from "start the gateway now" keeps the user's UAC choice and keeps the gateway unelevated.

Verification must not run the real `hermes gateway install` or `gateway start` on this machine; use the fake `hermes` and stubs. The owner's machine currently has the elevated gateway running; do not touch it.

## Deviations

- The wait asks Hermes, not Windows. The installer runs `hermes -p default gateway status` once a second until it shows "Scheduled Task registered" or "Windows login item installed". Those lines come from `is_task_registered` and `is_startup_entry_installed`, the same checks whose failure makes `gateway start` offer to install again. The names of the task and the Startup entry depend on how Hermes names a home's service (`_profile_suffix`), and the installer does not copy that logic. The wait does not watch for the elevated helper to exit: an unelevated process cannot read an elevated process's command line, so it cannot tell the helper from the gateway or the dashboard. If the helper fails, the wait runs out after 2 minutes, with an error that says what to do and what `gateway status` printed.
- `gateway install` gets `--no-start-now`, not merely no `--start-now`. With neither flag, Hermes asks "Start the gateway now after install?", yes by default. A yes before the UAC prompt would start the gateway as administrator again.
- The installer detects an administrator gateway after `gateway start`, with `hermes -p default gateway ensure --timeout 60`. Hermes's desktop app uses the same machine-readable check. It reports `reason_code` `gateway_elevated`, as classified in `gateway/runtime_bootstrap_windows.py`, and exits 4. `gateway status` does not report elevation, and `ensure` would start a gateway on a machine without one, so it runs after `gateway start`, not before. `gateway_other_session`, a gateway in another logon session, is handled the same way, because Hermes's remedy for it is the same administrator `gateway stop`.
- The elevated stop is `Start-Process -FilePath <hermes.exe> -ArgumentList -p default gateway stop --yes -Verb RunAs -WindowStyle Hidden`. That is ShellExecuteEx with "runas" and a hidden window, as in Hermes's `_launch_elevated_gateway_command`. Hermes has no command that runs `stop` elevated: `_launch_elevated_gateway_command` is called only from `install` and `uninstall`.
- `--yes` is passed to the elevated `gateway stop`. Without it, `refuse_while_sessions_live` (`hermes_cli/gateway_lifecycle_guards.py`) asks y/N before it interrupts live sessions whenever stdin and stdout are a terminal. In a hidden console that question would wait for an answer nobody can see. Instead, the installer tells the user before the UAC prompt that approving stops the gateway and interrupts any work it is doing, so approving the prompt is the user's answer.
- The installer waits until the elevated stop has exited and `gateway status` shows no gateway process, for at most 2 minutes. Then it runs `gateway start` unelevated and `gateway ensure` again. It fails if the gateway is still refused at that point.
- From Hermes's refusal text, the installer shows only the sentence that names the gateway, for example "The running gateway (PID 49984) was started as administrator, so this non-elevated app cannot reach it." Hermes's remedy sentence ("Stop it from an administrator prompt ...") would send the user to another window, which this ticket rules out.
- `Get-NativeOutput` captures stderr as well as stdout. A failed command's error carries both. For a command that succeeds, stderr is shown on the console and only stdout is returned, so `hermes pm env` and `gateway ensure` still parse as JSON.
- `install.sh` is unchanged. Hermes's launchd install (`hermes_cli/gateway_launchd.py`) bootstraps the LaunchAgent into the user's `gui/<uid>` domain in the same process, without sudo or a helper. Neither the administrator gateway nor the race exists on macOS.
- The account-connections skill told the agent to run `gateway install --start-now --start-on-login` when no gateway service is installed. It now tells the agent to run `gateway install --no-start-now --start-on-login` and then `gateway start`, in the installer's order.
- Spec › Installation and setup now says that the gateway and the dashboard run as the user who installed them, whatever the user answers to the UAC question, and that a failing step shows what its command printed.
- Delivery is an open pull request, not a merge, as requested. The ticket stays In Progress.

## Result

`install.ps1`'s gateway step:

1. `hermes -p default gateway install --if-missing --no-start-now --start-on-login` on the console. Hermes still asks its UAC question, and the user still answers it.
2. A wait of at most 2 minutes until `hermes -p default gateway status` shows the Scheduled Task or the Startup entry. The installer says once that it is waiting for the administrator install. After a no to the UAC question, or with the entry already installed, the first check passes.
3. `hermes -p default gateway start` on the console, unelevated.
4. `hermes -p default gateway ensure --timeout 60`. On `gateway_elevated` or `gateway_other_session`, the installer names the gateway and says what approving does. It then opens one UAC prompt for a hidden `hermes -p default gateway stop --yes`, waits until that stop has exited and no gateway runs, starts the gateway again unelevated and checks it with `ensure`. If the user declines the prompt, the install stops: it names the running gateway, explains that the test message, the dashboard's chat and the search all need a gateway this user can reach, and says to run the install again and approve the prompt. Any other state goes on to the test message, which decides.

`Get-NativeOutput` puts the command's stdout and stderr in the error it throws, so a failed test message shows Hermes's reason, such as "Gateway inaccessible: gateway_elevated".

Verified on Windows, Windows PowerShell 5.1:

- Hermes's source, read only, at `%LOCALAPPDATA%\hermes\hermes-agent` (0.22.5, `bf0203a`): `gateway_windows.install`, `_offer_elevated_install`, `_launch_elevated_install`, `_launch_elevated_gateway_command`, `start`, `stop`, `status` and `uninstall`; `gateway.py`'s `_cmd_install` and its status dispatch; `gateway_lifecycle_guards.refuse_while_sessions_live`; `gateway_runtime_cli.cmd_gateway_ensure`; and `gateway/runtime_bootstrap_windows.py`, where `gateway_elevated` is classified and its remedy written.
- `tests/test_installers.py` replaces the asking fake with `FAKE_GATEWAY_HERMES`, which models Hermes's Windows gateway commands:
  - On y, its `gateway install` returns at once, and a detached "elevated copy" adds the login entry seconds later. That copy starts the gateway as administrator if it was given `--start-now`.
  - `gateway start` without the entry asks to install again.
  - `gateway status` shows the entry and whether a gateway process runs.
  - `gateway stop` ends any gateway after a second.
  - `gateway ensure` reports an administrator gateway as Hermes does.
- New tests:
  - The install gets `--no-start-now`, and `--start-now` appears nowhere in `install.ps1`.
  - The step waits for the elevated install, calling only `gateway status` until the entry exists. It then starts the gateway with the entry present and no administrator gateway.
  - When the entry never appears, the wait ends with its error and `gateway start` never runs.
  - On a machine like the owner's (entry installed, gateway running as administrator), the step runs the elevated `gateway stop --yes`, waits for it, starts the gateway again as the user and passes `ensure`. The test removes `-Verb RunAs`, because no test may ask for elevation.
  - A declined prompt, which throws the same exception as Windows PowerShell 5.1's `Start-Process -Verb RunAs` does on No, stops the install with the gateway named and the reason, and the gateway is not stopped.
  - `-Verb RunAs` appears once in `install.ps1`, on that stop.
  - A failed `Get-NativeOutput` shows the command's stdout and stderr. A successful one returns only stdout and shows stderr.
- The question tests and the parse-tree test use the new commands. Against `main`'s `install.ps1`, 8 of the then 9 `QuestionTests` failed. Against the earlier revision of this branch, which only printed the administrator command, the handoff, declined-prompt, RunAs and parse-tree tests fail. With `--start-now` put back into the step, the wait test fails because the fake's elevated copy starts an administrator gateway.
- `python -m pytest tests -q` from Git Bash: 612 passed, 277 subtests passed. `ruff check .` passed. The PowerShell parser reports 0 errors on `install.ps1` and `bootstrap.ps1`, and `bash -n` passes on `install.sh` and `bootstrap.sh`.

Not run: any real `hermes gateway` command, a real UAC prompt, the installers end to end, and anything on macOS. The owner's Hermes root, profile and the administrator gateway running there were not touched. The owner's next run of the install line should find that gateway, ask for one approval, stop it and start it again as the user.
