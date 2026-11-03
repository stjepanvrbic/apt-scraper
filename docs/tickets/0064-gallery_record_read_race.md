---
title: Stopping the gallery fails when the server deletes its record as the launcher reads it
type: Bug
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: [0062, 0063]
---

## Outcome

`gallery.py stop`, and `open` when it restarts an outdated gallery, succeed however the server's exit lines up with the launcher's reads of the connection record.

## Acceptance

- [ ] A test deletes the record between the launcher's decision to read it and the read itself, and the launcher reports no record instead of failing.
- [ ] The CI workflow is green on Linux, macOS and Windows.

## Spec

docs/spec.md, Behavior, Gallery: the launcher opens, reuses and stops the gallery server.

## Design

The macOS run of the Python suite on main after #62 failed `test_open_returns_a_launch_secret_and_stop_ends_the_server`: `gallery.py stop` exited 1 with `[Errno 2] No such file or directory` on the gallery's connection record under `~/.secrets/apt-scraper/gallery`. After `/api/stop`, `stop_gallery` polls `read_connection` until the record is gone, and the exiting server deletes the record (`forget_connection` in `gallery.py`). `read_connection` checked `is_file()` and then read the file, so a delete between the two raised `FileNotFoundError` out of `stop`. The next run passed because the delete fell elsewhere.

`read_connection` now reads the file and treats `FileNotFoundError` as no record, so there is no gap between a check and a read. The test replaces `Path.read_text` with one that deletes the file and then reads it, which fails on the old code every time.

## Deviations

## Result
