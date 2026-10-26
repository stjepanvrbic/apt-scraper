---
title: A rejected gallery request can reset the connection instead of answering
type: Bug
state: Todo
priority: 3
assignee:
milestone:
parent:
blocked_by: []
related: [0022, 0027]
---

## Outcome

The gallery server answers every `POST` it rejects with the rejection's status and error, whatever the request's body, and the client receives that answer.

## Acceptance

- [ ] A test sends, over a raw socket, a `POST` whose headers and body the server rejects for each of: missing token (401), foreign origin (403), wrong content type (400), a declared length over 100,000 bytes (400) and an invalid `Content-Length` (400). It finishes sending the whole body before reading, then reads the full status line and error for each, 200 times each without a connection reset.
- [ ] `test_rerank_requires_authentication_same_origin_and_an_empty_json_object` in `tests/test_rerank_server.py` sends bodies large enough that the unfixed server fails it in most runs, and passes on the fixed one.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Gallery](../spec.md#gallery)

## Design

`do_POST` in `profile/skills/gallery/scripts/server.py` returns after `allowed()` has replied 401 or 403, and raises before `self.rfile.read(length)` for a wrong content type or a length outside 1 to 100,000 bytes. In each case the request body is still unread when the handler replies and the HTTP/1.0 connection closes. The client then sometimes gets `ConnectionAbortedError: [WinError 10053]` in place of the status.

Within the gallery, a user meets it when a tab outlives the server it was opened from, since each server start issues a new token, and on any request with a body over 100,000 bytes, such as a long note.

Read and discard the declared body before replying to a rejected request. A body over the limit cannot be drained cheaply, so that reply needs a way to end the connection without a reset, which the first acceptance item checks.

## Deviations

## Result

## Comments

- 2026-10-25: Found while verifying the ranking redesign on `main` at `cf3ac2f`. `python -m unittest discover -s tests` had 2 errors in one of two full runs: one subtest of the test above got the reset, which escaped `assertRaises(HTTPError)`, and the test then failed on `raised.exception`. `tests/test_rerank_server.py` alone failed the same way in 1 of 12 runs on an idle machine. A script that sends unauthenticated `POST /api/rerank` requests to a server on a temporary store, 400 per row, got a reset for 0 of 800 requests with no body, 4 of 800 with the 2-byte body `{}` the test sends, 1 of 400 with 1,000 bytes, 6 of 400 with 60,000 bytes and 19 of 800 with 99,000 bytes. Every other request got the 401. The script and its output are `scripts\reset_repro.py` and `results\reset-repro.txt` in `%TEMP%\apt-verify-0027-root`.
