---
title: The rerank timing test fails on a busy machine
type: Bug
state: Todo
priority: 3
assignee:
milestone:
parent:
blocked_by: []
related: [0020]
---

## Outcome

The 2-second rerank check in the tests measures the rerank, not the machine's load, and passes reliably while other work runs.

## Acceptance

- [ ] The test measures CPU time of the rerank, or runs it several times and takes the best, so that a loaded machine does not fail it while a real slowdown still does.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Acceptance](../spec.md#acceptance) (Rerank)

## Design

## Deviations

## Result

## Comments

- 2026-10-25: With another process loading the CPU, `main` measured 1.60 to 2.31 s against the 2-second limit; on a quiet machine 1.42 to 1.46 s.
