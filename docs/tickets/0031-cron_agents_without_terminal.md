---
title: Scheduled agents save their results through file tools, not the terminal
type: Chore
state: In Progress
priority: 3
assignee: agent
milestone:
parent:
blocked_by: [0021]
related: [0006, 0016, 0042]
---

## Outcome

The search, listing-features and review agents that cron wakes save and correct their results without the terminal tool, so a scheduled session can neither reach the network nor read `~/.secrets/`.

## Acceptance

- [x] Each of the three jobs' wake sessions runs with file tools only, and its save and correct-and-resave paths work, the next run handing a rejected save back for correction, since Hermes runs no code of a cron job after its session.
- [x] A test per job shows the session's toolset excludes the terminal.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Contracts and invariants](../spec.md#contracts-and-invariants)

## Design

- Today each job saves through a script run in the terminal, and a rejected file is corrected and saved again in the same turn. The scripts' validation moves to the job's own check after the session, or the save becomes a file write the job picks up.

## Deviations

- Corrections take a run, not a turn. Hermes 0.22.5 runs no code of a cron job after its session: a job has only the pre-run `script` (`JOB_DEFINITION_FIELDS` in `cron/job_definition.py`). The plugin hooks `on_session_end` and `transform_tool_result` (`VALID_HOOKS` in `hermes_cli/plugins.py`) would allow saving at the end of the session or at the write itself, but need a plugin the installers install and enable per profile, which the brief did not include. So an agent writes its result into its job's inbox, the next run saves it with the checks the removed commands ran, and a rejected file stays while the run hands its task back, first, with the error and the file to correct. A correction takes one run: a minute for the review, ten minutes for the features, an hour for the search. The first acceptance item says so, as the brief allowed.
- A rejection counts as a failed attempt, so a read gets 3 tries in all, where each attempt used to allow up to 3 tries within the turn. The review still fails after 3 hand-offs and now names the last rejection's error.
- A judgment or reading that stopped waiting after the run handed it, because the listing changed or the user removed the preference, is left out of its file and the rest saved. Before, the whole file was rejected, which a run's delay would have made common. An entry the run did not hand that names no waiting phrase is still rejected.
- Extraction tasks carry the tag list as the saved records left it. The first extractions of a new store no longer run one call at a time, because the records of one wake are saved only by the next run, so the extractions of a wake do not see each other's new tags, and the 72 seed tags carry the first wake.
- The commands agents ran in the terminal are gone: `ranking.py review-save`, `features.py save`, `judge`, `read`, `readings` and `preferences`, and `search.py pending` and `judge`. The job runs write the readings and judgment task files themselves. A conversation judges must-haves with `search.py tasks` and `apply`, through an inbox and task directory of its own, so it never changes a wake's files.
- All notes waiting for a reading go into one readings task file, where the agent used to read them 25 at a time with `readings`.
- The search report names judgment task files instead of carrying the waiting listings as JSON, so listing text no longer enters the search job's cron prompt.
- The review job's prompt no longer tells the agent to report a script error through the messaging gateway, which Hermes takes from every cron session. The agent ends with the error as its final answer.
- The Outcome holds only for the shell. Without the terminal, `read_file` still reads any file the user can, `~/.secrets/` included, apart from Hermes's own credential stores (`get_read_block_error` in `agent/file_safety.py`), `write_file` can still change the profile's scripts and skills, which later runs execute (`tools/file_tools_write_guards.py` exempts the Hermes home), and the features job's `vision_analyze` downloads public URLs (`tools/vision_tools.py`). Hermes has no per-job read or write root. The related Backlog ticket "Scheduled agents reach only their job's files" carries these.

## Result

## Comments

- 2026-10-25: Raised by the review pass's reviewer; the review agent keeps the terminal for now like the other two jobs.
