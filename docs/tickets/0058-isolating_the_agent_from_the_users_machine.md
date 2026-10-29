---
title: How far to isolate the agent from the user's machine
type: Decision
state: Todo
priority: 3
assignee:
milestone:
parent:
blocked_by: [0059]
related: [0056, 0057, 0059]
---

## Question

After a user's install failed on a MacBook (ticket 0056's kqueue crash), the owner proposed running the whole agent in a fully isolated container, with only its working directory mounted and every dependency controlled, so it works the same on any operating system. Which parts of that give the reliability it is after, and which fight what the product does?

## Options

1. **The whole agent in a container.** Docker Desktop or WSL2 on Windows, arm64 images on a Mac, the user's working directory mounted. Every dependency is pinned in the image. The install surface moves rather than disappears: Docker Desktop and WSL2 on Windows, Rosetta or arm64 images on a Mac, and the bootstrap stays a host script, which is where the tty bug of ticket 0056 lived. The agent must use the user's real browser over CDP, because Google blocks sign-in in bundled and headless browsers; it uses iMessage and AppleScript on macOS, computer use for macOS permission dialogs, and login items. All of those reach out of a container only through host bridges that would have to be built and kept working.
2. **Control the dependencies, keep the host.** Run the real one-liner on a real Mac and a real Windows machine in CI; pin everything the installer pulls, which today takes the latest Hermes, Claude Code, Python and wheels; sandbox only what the model executes, through Hermes's Docker terminal backend; containerize the pure-compute core (search, ranking, gallery server) later, and only if drift keeps causing problems.

## Decision

Option 2, in that order:

1. Ticket 0057 runs the check suite on fresh Linux, macOS and Windows machines. The next step is the one-line install itself, end to end on a real Mac and a real Windows machine in CI with the real Hermes and Claude Code, up to the first interactive question.
2. Pin what the installer pulls: Hermes, the Claude Code version, Python, and the wheels, with one place that records the versions and a test that fails when an installer takes "latest".
3. Check whether Hermes's Docker terminal backend can run the commands the model executes, so a model mistake cannot reach the user's files, while the browser, iMessage and login items stay on the host.
4. Only if drift between machines keeps causing failures after 1 to 3: containerize the pure-compute core, keeping the browser, messaging and login items on the host.

## Reason

The failures seen so far were dependency drift and host differences that tests on a developer's PC hide (tickets 0056 and 0057), not the host itself. Controlled dependencies remove the drift; a container would remove it too, but at the price of a bigger install surface and bridges for everything the product does through the user's own browser, accounts and desktop.

## Consequences

- The owner's reading of the same facts (2026-10-29): the failures were in machinery this repository built around Hermes, not in Hermes or in the agent, so the first consequence is ticket 0059, which deletes that machinery and leaves a Hermes profile installed by Hermes's own commands. The steps below apply to what remains.

- Each numbered step becomes a work ticket when it is picked up; this ticket is their record of why.
- The product keeps depending on the host, so host findings keep being product findings: the Visual C++ runtime that onnxruntime needs on a fresh Windows, and the VBScript engine the dashboard's Windows shortcuts need, both found by ticket 0057's sandbox, each need a ticket of their own.
