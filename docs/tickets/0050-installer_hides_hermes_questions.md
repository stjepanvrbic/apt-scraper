---
title: The installer hides the questions Hermes asks on the console
type: Bug
state: In Progress
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

- [ ] On the owner's first real run, `install.ps1` stopped at `hermes -p default gateway install --if-missing --start-now --start-on-login`: Hermes printed "Open the UAC prompt now? [y/N]:" and waited on stdin, but the installer runs every Hermes command through `Invoke-Checked`, which pipes stdout into `Out-Host`, so Python block-buffered the question and the screen showed only the two lines before it. After the fix, a Hermes command that asks a question shows the question and takes the answer from the keyboard, with no pipe between Hermes and the console.
- [ ] Every command in `install.ps1` that may ask the user something (`claude auth login`, `hermes update`, `hermes profile install`, `hermes gateway install`, `hermes computer-use install`) runs with the console attached. Commands whose output the script needs to read keep capturing it.
- [ ] `install.sh` is checked for the same shape and has no equivalent pipe.
- [ ] `tests/test_installers.py` covers the case with a fake `hermes` that prints a question without a trailing newline, reads one line from stdin and continues: the question appears in the console output before the answer is read.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

docs/spec.md, Installation and setup: the one-line install ends in the dashboard chat. A question that the user cannot see is a stop, not an install.

## Design

Hermes's `prompt_yes_no` reads `input()`; it only skips the question under `HERMES_NONINTERACTIVE=1` or a closed stdin. The installer leaves stdin on the console but pipes stdout, the one combination where the question is asked and never shown. The fix is in how the installer runs commands, not in answering Hermes's questions for the user: the gateway question is the user's choice (Scheduled Task behind UAC, or a Startup-folder entry without it), and the defaults Hermes applies when no console is present are not the same as an answer.

Verification must not run the real `hermes gateway install`: it installs login entries and Scheduled Tasks for the current Windows account whatever `HERMES_HOME` says. Use a stub executable that asks on stdin.
