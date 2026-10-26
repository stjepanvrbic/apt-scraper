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
- The Outcome holds only for the shell. Without the terminal, `read_file` still reads any file the user can, `~/.secrets/` included, apart from Hermes's own credential stores (`get_read_block_error` in `agent/file_safety.py`), `write_file` can still write any file the user can outside Hermes's deny lists, the profile's scripts and skills included, which later runs execute (`tools/file_tools_write_guards.py` exempts the Hermes home), and the features job's `vision_analyze` downloads public URLs (`tools/vision_tools.py`). Hermes has no per-job read or write root. The related Backlog ticket "Scheduled agents reach only their job's files" carries these.

## Result

The search, listing-features and review jobs' wake sessions run with the file tools only (`enabled_toolsets` `["file", "no_mcp"]`, plus `vision` and `delegation` for features), and their subagents inherit that set. Agents save by writing JSON files into their job's inbox, `cache/inbox/<job>/`, which `inbox.py` in the gallery's scripts applies on the job's next run with the checks the removed commands ran. A rejected file stays and its task is handed back first, with the error as a JSON string in a code block and the file to correct; any exception but an OSError or a locked database rejects the file alone. A conversation judges must-haves through `search.py tasks` and `apply` and an inbox of its own. Tech debt fixed: `docs/ranking.md` "How it runs" cited `cron/scheduler_tick.py` for Hermes's one-run-at-a-time rule, which is `try_register_running_job` in `cron/scheduler.py`.

Tests: per job, the shipped toolsets exclude the terminal and the task templates name no terminal (`test_the_review_session_has_the_file_tools_and_no_terminal`, `test_the_features_session_has_the_file_vision_and_delegation_tools_and_no_terminal`, `test_the_search_session_has_the_file_tools_and_no_terminal`), and a save, a rejection and a resave through the inbox work for the review, extractions, readings, preference judgments and must-have judgments, in the job and in a conversation. `tests/test_inbox.py` covers the shared module, a forged error included.

Checks at `2f35291`: `python -m unittest discover -s tests`: 556 passed, 0 skipped. `ruff check profile tests`: clean. An earlier full run at the same revision had 2 failures, both two-second rerank timing tests, `test_rerank_with_signals_meets_the_two_second_cpu_budget` (2.016 s) and `test_http_rerank_and_order_read_fit_the_two_second_budget` (timeout), while other sessions loaded the machine; this branch does not change the rerank, and the CPU-budget test then passed 4 of 4 runs on both this branch and `main`, the busy-machine case the Backlog ticket "The rerank timing test fails on a busy machine" tracks. With Hermes 0.22.5's own code on a throwaway home: the skill linter has no findings for `search`, `listing-features` and `ranking`, the skills guard rates all three safe, the cron prompt scanners pass each job's prompt alone and with its skill, the context scanner leaves every SKILL.md and task template unchanged, and `_resolve_cron_enabled_toolsets` with `_resolve_child_toolsets` give each session and subagent only `read_file`, `write_file`, `patch` and `search_files`, plus `delegate_task` and `vision_analyze` for features, with an MCP server in the config left out. Checked against Spec › Listing features, Ranking and Contracts and invariants at the same revision, none changed; the project keeps no ADR register.

Live, in the throwaway Hermes root `%TEMP%\apt-verify-0031-root` (profile `apt-verify`, installed and then updated from this branch, with the store of 129 listings with records copied from an earlier throwaway root, listing sites set to a site the search has no reader for, so no listing-site requests): the review session called only `read_file` and `write_file`, and the next run saved its review (50 reasons, 2 flags, 1 finding). A planted review with an uncited finding was rejected, handed back, read and rewritten by the next session and saved. The features session corrected a planted rejected readings file with `read_file` and `patch` while one extraction subagent wrote a record for a listing with the gallery's local photos; the next run saved both and handed 129 judgments to 6 subagents, which the run after saved (40 "has", 90 "does not say" with the new listing). The search session judged the hand-imported listing; its file, made invalid, was rejected, corrected with `patch` by the next session and saved, and the listing shows. The root's own gateway, which Hermes started on demand, was stopped and the jobs paused; the owner's root, its profiles and its gateway were not touched (its `gateway-starts.log` stayed at 15 lines). Before the correction text named the read first, two correcting agents tried `write_file` on the rejected file, which Hermes refused until they read it. With the final text, a third correcting session read the file first, and its write went through.

Review: an independent `review-code` pass of `40556d6` found 14 issues, all answered in `e63e321`, among them the rejection error fenced as data, stale judgments and readings left out instead of rejecting the file, a conversation inbox of its own, apply failures that count no attempt, and the docs' claims about what the file tools reach. Its reassessment of `fd94405` found one new problem, a file with values of the wrong type stalling the features and search jobs, fixed in `2f35291` with a test per job. Left open, recorded above or for the owner: the attempt budget, the tag-list snapshot per wake, the search's missing attempt cap, all notes in one readings file, a rejected judgments batch the budget held back being rejected whole when one of its listings changed meanwhile, and a conversation and a wake possibly judging the same listings in the same hour.

Delivered on `chore/cron_agents_file_tools` in [PR #36](https://github.com/stjepanvrbic/apt-scraper/pull/36), open against `main`, not merged.

## Comments

- 2026-10-25: Raised by the review pass's reviewer; the review agent keeps the terminal for now like the other two jobs.
- 2026-10-26: Questions for the owner, none blocking the pull request. (1) Should a profile plugin save each file at the moment the agent writes it (`transform_tool_result` hook), restoring the in-turn correction and the shared tag list within a wake, at the cost of a plugin the installers install and enable? Recommended: not now; decide it together with the related Backlog ticket, since a `pre_tool_call` hook in the same plugin could confine the agents' paths. (2) Is 3 tries per read, each correction now one of them, the right budget? Recommended: keep 3, since each retry now carries the error and the file. (3) Accept that the first wake of a new store extracts against the 72 seed tags only? Recommended: accept; the review pass reports tags that mean the same thing.
