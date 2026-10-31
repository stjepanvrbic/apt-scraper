---
title: basedpyright at its strictest
type: Chore
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: [0059]
related: []
---

## Outcome

Every Python file of the profile and the test suite type-checks under basedpyright's strictest mode, and the check runs wherever "all checks pass" is decided: `tests/run.sh`, `tests/run.ps1`, so CI on all three platforms. A change that passes a value of the wrong shape, reads a key a record may lack, or leaves a result unused fails the checks before it reaches a user.

## Acceptance

- [ ] `pyrightconfig.json` sets `typeCheckingMode` to `all`, every rule reports an error, and `basedpyright` over `tests`, `skills`, `scripts` and `typings` reports zero errors on all three CI platforms.
- [ ] No `# type: ignore` in the repository. Each `# pyright: ignore` names its rule and, on the same line, why the type system cannot state the truth there; Design lists them all, with each rule relaxed in `pyrightconfig.json` and its reason.
- [ ] `typings/` holds real stubs for the modules the test environment lacks, declaring the names the code uses with their real types, and `typings/README.md` says which module each stub stands for.
- [ ] `tests/run.sh` and `tests/run.ps1` run basedpyright right after Ruff, from `tests/requirements.txt`, which pins it.
- [ ] README and AGENTS.md name the check.
- [ ] The Python suite still passes with zero skips on every platform: behavior did not change.

## Spec

docs/spec.md, Installation and setup, and tickets 0057 and 0059: the one definition of "all checks pass", `tests/run.sh` and `tests/run.ps1`, which this ticket extends by one check.

## Design

- **Configuration** `pyrightconfig.json`: `typeCheckingMode` `all`, Python 3.14 (what Hermes runs the profile's scripts with), `include` `tests`, `skills`, `scripts` and `typings`, and `extraPaths` for every skill's script directory, since Hermes runs each script with its own directory on the path and the tests put the same directories on `sys.path`.
- **Stubs** `typings/`: Hermes's `hermes_cli` and `hermes_constants`, the google-workspace connector's `google_api` and the `googleapiclient` it uses, the search's `curl_cffi`, the photo encoder's `onnxruntime` and the encoder build tools' `onnx`, `onnxconverter_common` and `transformers`. Each declares only what the repository's code uses, with the signatures the real module has.
- **Shapes** `skills/gallery/scripts/shapes.py`: the JSON records every skill exchanges (listings, criteria, preferences, settings), as TypedDicts, with the narrowing of a parsed JSON value into them. Records one module owns stay in that module. A parsed JSON value is `object` until the one place that knows its shape narrows or casts it.
- **Relaxed rules**, each because it cannot hold here:
  - `reportMissingModuleSource`: fires on every import of a stubbed module whose source is absent, which is what `typings/` is for; the modules are present only where the agent runs.
  - `reportUninitializedInstanceVariable`: fires on every attribute a `unittest.TestCase.setUp` assigns, the unittest way of giving each test fresh fixtures; the attributes are annotated where they are assigned.
- **Runners**: basedpyright runs right after Ruff in both scripts, over the whole repository, so a run stops on a type error before the slow suites start.
- **Boundaries**: a value this repository wrote itself (its own JSON files, its own database columns) is cast to its shape where it is read; a value from outside (the model's output, HTTP bodies, connector output, files the user edits) is parsed to `object` and narrowed with the checks the code already made. sqlite3's rows are cast at the query with the columns' real types. argparse namespaces are typed subclasses. A discarded non-None result is written `_ = ...`. Modules that exist on one platform only (`msvcrt`, `fcntl`, `pty`, `termios`, `resource`) are imported and used inside `sys.platform` branches, so the same file checks clean on Linux, macOS and Windows hosts, which is why `pythonPlatform` stays unset.
- **Ignores**, each where the type system cannot state the truth, with the reason on its line:
  - `skills/ranking/scripts/measurement.py`, `bootstrap`: the implementation's default statistic, `median`, serves the numeric overload; the generic implementation signature cannot type it.
  - `skills/ranking/scripts/measurement.py`, `record_simulated_pair`: `pair_days` is a `NotRequired` key that `simulation_start` always sets before this runs; a TypedDict subclass cannot make an inherited key required.
  - `tests/encoder_resources.py`: the tower model literal has no `dimensions`, because the resource measurement never embeds a photo and the dimension stays unknown.
  - `tests/test_photo_ranking.py`: a hypothesis task fixture that holds only the keys `checked_proposals` reads, on purpose.
  - `tests/test_review.py`: an `isinstance` the type already guarantees, kept because the assertion checks what the rerank wrote to the database, not what the type says.

## Deviations

Typing the code changed a few edge behaviors, each listed here because the suite could not run on the owner's PC and CI is the check:

- The search's `judge` rejects a must-have judgment of `1.0` or `0.0` with its existing message; the old check let those two floats through by accident while rejecting `1` and `0`.
- `ListingStore.ranking_inputs` reads a listing's code features by key; the store writes them in the same transaction as the listing, so the `{}` fallback for a missing row could never be reached.
- `ListingStore.update_chat` takes its changes as one typed mapping, so the `Unchanged` sentinel and the runtime status check are gone; every caller is in chat.py and passes literal statuses.
- A command that searches nothing gets a `NoClient` whose `get` raises a `RuntimeError` naming the page, where `None` would have raised an `AttributeError`; no such command asks a site for a page.
- Malformed output from a connector (google_api, imsg, the gateway) or from Hermes's session snapshot raises a `RuntimeError` naming the connector and the missing key, where it raised a `KeyError`, `TypeError` or `AttributeError`; the exit paths are the same.
- The encoder build tools and the ranking experiments raise `ValueError` or `SystemExit` with a message where a malformed lock entry, an empty sweep split, a missing option or a simulation with no runs used to fail with a `KeyError`, `TypeError` or an unbound name. `variants()` lost the models-directory parameter it never read.
- `validate_listing` builds the listing in a fixed field order instead of set order; the content and its fingerprint are unchanged.

## Result

basedpyright reported 21,203 errors in 100 files when the configuration was added, all of `typeCheckingMode` `all`'s rules, led by the unknown-type family on unannotated functions and JSON dicts. Every Python file now passes with 0 errors and 0 warnings, 119 files analyzed, with two rules relaxed and five ignores, each listed under Design. Ruff passes. CI results on the pull request: pending.
