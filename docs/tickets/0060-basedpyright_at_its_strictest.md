---
title: basedpyright at its strictest
type: Chore
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: [0057]
related: []
---

## Outcome

Every Python file of the profile and the test suite type-checks under basedpyright's strictest mode, and the check runs wherever "all checks pass" is decided: `tests/run.sh`, `tests/run.ps1`, so CI on all three platforms and the Windows Sandbox. A change that passes a value of the wrong shape, reads a key a record may lack, or leaves a result unused fails the checks before it reaches a user.

## Acceptance

- [ ] `pyrightconfig.json` sets `typeCheckingMode` to `all`, every rule reports an error, and `basedpyright` over `tests`, `profile` and `typings` reports zero errors on all three CI platforms.
- [ ] No `# type: ignore` or `# pyright: ignore` in the repository. Each rule relaxed in `pyrightconfig.json` is justified below.
- [ ] `typings/` holds real stubs for the modules the test environment lacks, declaring the names the code uses with their real types, and `typings/README.md` says which module each stub stands for.
- [ ] `tests/run.sh` and `tests/run.ps1` run basedpyright right after Ruff, from `tests/requirements.txt`, which pins it.
- [ ] README names the check.

## Spec

docs/spec.md, Installation and setup, and ticket 0057: the one definition of "all checks pass", which this ticket extends by one check.

## Design

- **Configuration** `pyrightconfig.json`: `typeCheckingMode` `all`, Python 3.14 (what Hermes runs the profile's scripts with), `include` `tests`, `profile` and `typings`, and `extraPaths` for every skill's script directory, since Hermes runs each script with its own directory on the path and the tests put the same directories on `sys.path`.
- **Stubs** `typings/`: Hermes's `hermes_cli` and `hermes_constants`, the google-workspace connector's `google_api` and the `googleapiclient` it uses, the search's `curl_cffi`, the photo encoder's `onnxruntime` and the encoder build tools' `onnx`, `onnxconverter_common` and `transformers`. Each declares only what the repository's code uses, with the signatures the real module has.
- **Shapes** `profile/skills/gallery/scripts/shapes.py`: the JSON records every skill exchanges (listings, criteria, preferences, settings), as TypedDicts, with the narrowing of a parsed JSON value into them. Records one module owns stay in that module. A parsed JSON value is `object` until the one place that knows its shape narrows or casts it.
- **Relaxed rules**, each because it cannot hold here:
  - `reportMissingModuleSource`: fires on every import of a stubbed module whose source is absent, which is what `typings/` is for; the modules are present only where the agent runs.
  - `reportUninitializedInstanceVariable`: fires on every attribute a `unittest.TestCase.setUp` assigns, the unittest way of giving each test fresh fixtures; the attributes are annotated where they are assigned.
- **Runners**: basedpyright runs right after Ruff in both scripts, over the whole repository, so a run stops on a type error before the slow suites start.

## Deviations

## Result
