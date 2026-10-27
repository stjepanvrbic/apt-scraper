---
title: The installer hides the questions Hermes asks on the console
type: Bug
state: Done
priority: 1
assignee: agent
milestone:
parent: 0015
blocked_by: []
related: [0027, 0049]
---

## Outcome

The one-line install on Windows runs through to the dashboard chat without stopping on a question the user cannot see.

## Acceptance

- [x] On the owner's first real run, `install.ps1` stopped at `hermes -p default gateway install --if-missing --start-now --start-on-login`: Hermes printed "Open the UAC prompt now? [y/N]:" and waited on stdin, but the installer runs every Hermes command through `Invoke-Checked`, which pipes stdout into `Out-Host`, so Python block-buffered the question and the screen showed only the two lines before it. After the fix, a Hermes command that asks a question shows the question and takes the answer from the keyboard, with no pipe between Hermes and the console.
- [x] Every command in `install.ps1` that may ask the user something (`claude auth login`, `hermes update`, `hermes profile install`, `hermes gateway install`, `hermes computer-use install`) runs with the console attached. Commands whose output the script needs to read keep capturing it.
- [x] `install.sh` is checked for the same shape and has no equivalent pipe.
- [x] `tests/test_installers.py` covers the case with a fake `hermes` that prints a question without a trailing newline, reads one line from stdin and continues: the question appears in the console output before the answer is read.
- [x] Merged to `main` through a reviewed pull request.

## Spec

docs/spec.md, Installation and setup: the one-line install ends in the dashboard chat. A question that the user cannot see is a stop, not an install.

## Design

Hermes's `prompt_yes_no` reads `input()`; it only skips the question under `HERMES_NONINTERACTIVE=1` or a closed stdin. The installer leaves stdin on the console but pipes stdout, the one combination where the question is asked and never shown. The fix is in how the installer runs commands, not in answering Hermes's questions for the user: the gateway question is the user's choice (Scheduled Task behind UAC, or a Startup-folder entry without it), and the defaults Hermes applies when no console is present are not the same as an answer.

Verification must not run the real `hermes gateway install`: it installs login entries and Scheduled Tasks for the current Windows account whatever `HERMES_HOME` says. Use a stub executable that asks on stdin.

## Deviations

- The question was hidden by Windows PowerShell, not by Python's buffering. `input()` writes its prompt to a piped stdout and flushes it, so the question did reach the pipe. PowerShell reads a piped native command's output a line at a time, and it held the question back because the question has no newline. A stub that runs `input()` with stdout on a raw pipe shows the question there before the answer. Under `Invoke-Native ... | Out-Host` it shows only after the answer. The fix is the same either way.
- `Invoke-Native` and `Invoke-Checked` are replaced by two functions, one for each way of running a command. `Invoke-OnConsole` runs a command as a statement of its own and returns nothing. `Get-NativeOutput` returns a command's output as one string. With two functions, each call site says whether it reads the command's output, and a test can check every call site. The commands the installer runs and their arguments are unchanged.
- `bootstrap.ps1` needed no change in code. It runs `install.ps1` through a native call at statement level, which keeps the console. A test proves this through `irm | iex`, and a comment there now says so. Spec › Installation and setup gains a sentence: a question asked during the install shows as it is asked and takes its answer from the keyboard.
- Delivery is an open pull request, not a merge, as requested. The ticket stays In Progress.

## Result

`install.ps1` runs every native command through `Invoke-OnConsole`, a statement of its own with nothing taking its output, so Windows PowerShell hands the command this window's console. Only the six commands whose output the installer reads go through `Get-NativeOutput`: `claude --version`, `claude auth status`, `hermes --version`, `hermes pm env`, `hermes config get delegation.model` and the test message. `Out-Host` is gone. `claude auth login`, `claude update`, `hermes update`, every `profile`, `skills`, `cron`, `config set`, `plugins` and `computer-use` command, both `gateway` commands, the search environment's install and the upstream installers all run on the console. Hermes's "Open the UAC prompt now? [y/N]:" shows as it is asked and waits for the user's own answer. Nothing answers it for them.

`install.sh` has no equivalent pipe. Its interactive commands run directly on the terminal, and only the reads use `$(...)` or a pipe into `sed`. `bootstrap.sh` already gives the installer `/dev/tty`.

Verified on Windows, Windows PowerShell 5.1:

- Probe, with a stub that asks through `input()`: a native command run as a statement of its own shows the question before the answer is read. That holds inside a function, inside nested functions, in `try`/`finally`, in an `if` block and in a script block, under both `-File` and `Invoke-Expression`. Under `| Out-Host` the question shows only after the answer.
- `tests/test_installers.py` gains `QuestionTests` and one bootstrap test. They run each installer's real gateway step against a fake `hermes` whose `gateway install` asks the question as Hermes does and reads one line from stdin. The test drives stdin and types the answer only once the question appears in the output. That covers `install.ps1`, `install.sh`, and `bootstrap.ps1` through `$text | Invoke-Expression` into an installer that asks through `Invoke-OnConsole`. A parse-tree test over `install.ps1` checks that every `Invoke-OnConsole` is a statement of its own and that only the runner functions use `&`. It also checks that there is no `Out-Host`, that `Get-NativeOutput` runs exactly the six reads, and that the commands named above run on the console. With `| Out-Host` put back into `Invoke-OnConsole`, the PowerShell gateway test, the bootstrap test and the parse-tree test fail.
- `python -m pytest tests -q` from Git Bash: 604 passed, 277 subtests passed. `ruff check .` passed. The PowerShell parser reports 0 errors on `install.ps1` and `bootstrap.ps1`, and `bash -n` passes on `install.sh` and `bootstrap.sh`.

Not run: the real `hermes gateway install`, the installers end to end, and anything on macOS. No real Hermes command was run, and the owner's Hermes root and profile were not touched.

Delivered on `fix/installer_hides_questions` in [PR #42](https://github.com/stjepanvrbic/apt-scraper/pull/42), merged to `main` as 40d163e after one review round with no blocking findings.
