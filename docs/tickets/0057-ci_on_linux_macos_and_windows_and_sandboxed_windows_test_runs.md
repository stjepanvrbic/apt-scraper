---
title: CI on Linux, macOS and Windows, and sandboxed test runs on Windows
type: Feature
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: [0042, 0056]
---

## Outcome

Every push and pull request runs the whole check suite on a fresh Linux, macOS and Windows machine, so a change that breaks a platform shows before it reaches a user. The macOS installer tests run on real macOS, where ticket 0056's crash class lives, instead of only under Git Bash on Windows. On the owner's Windows PC the same suite runs in a Windows Sandbox that starts with nothing but Windows, so a local result depends on the repository and its pinned toolchain, not on what the PC has installed.

## Acceptance

- [ ] A GitHub Actions workflow runs on push to `main`, on pull requests and by hand, with one job each on `ubuntu-latest`, `macos-latest` and `windows-latest`, and all three pass on this branch.
- [ ] One script per platform, `tests/run.sh` and `tests/run.ps1`, runs every check in a fresh virtual environment built from `tests/requirements.txt`: Ruff, the Python suite, the gallery's lint, build, a check that the committed gallery assets are the build of the committed source, and the Playwright suite. CI and the sandbox run nothing else.
- [ ] The Windows run skips nothing. The macOS and Linux runs skip only the tests that run Windows PowerShell 5.1, each with the one reason `tests/skips.py` accepts, and the macOS installer, bootstrap, uninstall, login-item and account tests run there against the real `ps`, `bash` and pseudo-terminals.
- [ ] `tests/sandbox.ps1` runs the Windows suite in a Windows Sandbox with the checkout mapped read-only: it downloads the Python, Git for Windows and Node installers once, refuses any whose SHA-256 differs from the pinned one, shows the run's output as it happens, exits with the run's exit code, and shuts the sandbox down. The sandbox's package caches are mapped so later runs skip the downloads.
- [ ] README, the gallery guide and the agent instructions name the scripts, and say that test runs on the owner's Windows PC go through the sandbox.

## Spec

docs/spec.md, Installation and setup: the installer acceptance on a fresh Windows machine and a fresh Mac. This ticket gives the check suite the same footing; the one-line install itself is still not run end to end in CI.

## Design

- **Workflow** `.github/workflows/ci.yml`: checkout, Python 3.14 (what Hermes runs the profile's scripts with), Node 24, then `tests/run.sh` with bash on macOS and Linux, and `tests/run.ps1` in Windows PowerShell 5.1 on Windows, the shell the installer, bootstrap and uninstaller run in. A failed run keeps the Playwright output and the pytest report as an artifact. Runs on the same ref cancel each other.
- **Runners**: `tests/run.sh` and `tests/run.ps1` are the one definition of "all checks pass". Each builds a virtual environment under its work directory from `tests/requirements.txt`, which pins pytest, Ruff and the photo encoder's numpy, Pillow and onnxruntime at the versions the encoder's own requirements pin. `run.ps1` puts Git for Windows's `bin` and `usr\bin` ahead of System32, whose `bash.exe` starts WSL. The Python suite writes a JUnit report, and `tests/skips.py` fails the run on any skip on Windows, or on any skip on macOS and Linux other than the Windows PowerShell reason.
- **Test portability** in `tests/test_installers.py`: the 26 tests that run Windows PowerShell or the Windows shortcuts carry `@windows_only`; loops over both platforms and both installers run only the macOS one off Windows; `run_in_a_terminal` opens the pseudo-terminal with Python's `pty` on macOS and Linux, where Git Bash's perl is not there, with the same session, controlling-terminal and standard-stream arrangement; the fake `ps` that stands in for macOS's `ps -o tty=` is used only under MSYS, whose `ps` has no `-o`; the fake `claude` reads its standard input's device through `/proc` where there is one and `tty` elsewhere; and the uninstall tests' macOS PATH holds the system's own tool directories instead of Git Bash's.
- **Timing budgets**: the spec's two-second rerank budget is judged on the owner's PC. `tests/test_rerank.py` times a reference workload (numpy least squares and plain Python) once per process and scales the budget by how much slower the machine runs it than the owner's PC, never below the spec's two seconds. The first Linux run showed why: a shared runner took 2.3 and 2.5 seconds on reranks that take under two here, with every other test passing.
- **Sandbox** `tests/sandbox.ps1` writes a `.wsb` configuration that maps the checkout read-only, the toolchain installers read-only, and a cache folder and an output folder read-write, with `tests/sandbox-inside.ps1` as the logon command. Inside, that script installs Python 3.14.10, Git for Windows 2.56.0.4 and Node 24.22.2 silently, copies the checkout to a writable folder, points pip, npm and Playwright at the mapped caches, runs `tests/run.ps1` with its output appended to the mapped log, writes the exit code, and shuts the sandbox down unless `-KeepOpen` was given. The host script starts the sandbox through `wsb start --config`, so it has an ID, tails the log, and stops it through `wsb stop` in a `finally`: when the checks end, when the timeout passes and when the script is interrupted. It refuses to start while `wsb list` names a sandbox or a `vmmemWindowsSandbox` process exists, because a sandbox that outlives its run holds gigabytes until a restart, and says how to stop each. `-KeepOpen` is the one exception, announced with the stop command. Windows Sandbox needs enabling once as an optional feature; the script says how when it is missing.

## Deviations

- The first sandbox run on a bare Windows found that onnxruntime's extension does not load without the Visual C++ runtime DLLs (`DLL load failed while importing onnxruntime_pybind11_state`). The test environment gets them from the `msvc-runtime` wheel. The same gap exists in the product: the photo encoder's environment, built from Hermes's Python on a user's PC, would fail the same way on a Windows without the runtime, and `encoder-run.py` would record it in `status.json` and rank without photos. That is a product finding for a ticket of its own, not changed here.

## Result
