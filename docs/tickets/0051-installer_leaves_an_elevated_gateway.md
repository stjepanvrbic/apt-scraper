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

- [ ] Observed on the owner's first run through the fix of 0050: `hermes -p default gateway install --if-missing --start-now --start-on-login` offered the UAC prompt, the user answered y, Hermes's elevated helper created the Scheduled Task and, because of `--start-now`, started the gateway as administrator. The non-elevated `hermes chat` then failed with "Gateway inaccessible: gateway_elevated (The running gateway (PID 49984) was started as administrator, so this non-elevated app cannot reach it)". After the fix, the installer never asks Hermes to start the gateway from an elevated helper: the login entry is installed first, and the gateway is started afterwards by an unelevated `hermes -p default gateway start`.
- [ ] Right after the UAC handoff the installer's `gateway start` found no login entry yet and asked "Install it now so the gateway starts on login?", producing a second elevated install. After the fix, the installer waits for the elevated install to finish (the Scheduled Task or Startup entry exists, or the helper exited) before starting the gateway, with a bounded wait and a clear error when it runs out.
- [ ] When a gateway started as administrator is already running (the state the owner's machine is in), the installer stops it itself inside the same run: it launches `hermes gateway stop` elevated through one UAC prompt (ShellExecuteW "runas", the way Hermes's own `_launch_elevated_gateway_command` does; the unelevated `hermes gateway stop` cannot end it and offers no UAC), waits with a bound until no gateway is running, then starts the gateway unelevated. The user runs one line and approves one prompt; nothing is left for a second window. If the user declines the prompt, the installer stops with a message that names what is running and why the install cannot continue.
- [ ] `Get-NativeOutput` includes the command's captured output in the error it throws, so a failed captured command (the test message) shows its reason on the console.
- [ ] `install.sh` is checked for the same shape (macOS launchd does not elevate; confirm nothing equivalent is possible there).
- [ ] `tests/test_installers.py` covers: the gateway install runs without `--start-now`; the wait for the login entry; the elevated-gateway state stopped through the elevated handoff and the gateway restarted unelevated, and the declined prompt reported; a failed captured command's output appears in the error.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

docs/spec.md, Installation and setup: the gateway and dashboard run as the user who installed them.

## Design

Hermes (hermes_cli/gateway_windows.py) offers UAC before touching schtasks whenever the process is not elevated. The elevated child runs the same `install` with the same `start_now`, so `--start-now` is what starts the gateway elevated. The Scheduled Task it creates runs unelevated at login, so only the first session is affected. Separating "install the login entry" from "start the gateway now" keeps the user's UAC choice and keeps the gateway unelevated.

Verification must not run the real `hermes gateway install` or `gateway start` on this machine; use the fake `hermes` and stubs. The owner's machine currently has the elevated gateway running; do not touch it.
