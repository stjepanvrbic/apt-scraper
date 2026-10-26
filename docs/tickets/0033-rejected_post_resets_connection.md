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

- [ ] A test opens a raw socket, sends a `POST` the server rejects, sends its whole body, then reads. It gets the full status line and error, without a connection reset, 200 times for each of: missing token (401), foreign origin (403) and wrong content type (400), each with a 99,000-byte body; and a body of 200,000 bytes, declared and sent in full (400).
- [ ] `test_rerank_requires_authentication_same_origin_and_an_empty_json_object` in `tests/test_rerank_server.py` uses that method, and the pull request shows it failing on the unfixed server and passing on the fixed one.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Gallery](../spec.md#gallery)

## Design

`do_POST` in `profile/skills/gallery/scripts/server.py` returns after `allowed()` has replied 401 or 403, and raises before `self.rfile.read(length)` for a wrong content type or a length outside 1 to 100,000 bytes. In each case the request body is still unread when the handler replies and the HTTP/1.0 connection closes. The client then sometimes gets `ConnectionAbortedError: [WinError 10053]` in place of the status.

Within the gallery, a user meets it when a tab outlives the server it was opened from, since each server start issues a new token, and on any request with a body over 100,000 bytes, such as a long note.

Read and discard the declared body before replying to a rejected request, including a declared length over the limit, up to a cap such as 1 MB. Past the cap, or with a `Content-Length` that is not a number, reply, shut down the sending side of the socket, and read until the client closes or a few seconds pass, so the close carries no unread data.

## Deviations

## Result

## Comments

- 2026-10-25: Found while verifying the ranking redesign on `main` at `cf3ac2f`. `python -m unittest discover -s tests` had 2 errors in one of two full runs: one subtest of the test above got the reset, which escaped `assertRaises(HTTPError)`, and the test then failed on `raised.exception`. `tests/test_rerank_server.py` alone failed once in 16 saved runs with 2 errors; that run's traceback was not kept. A script that sends unauthenticated `POST /api/rerank` requests to a server on a temporary store, 400 per row, got a reset for 0 of 800 requests with no body, 4 of 800 with the 2-byte body `{}` the test sends, 1 of 400 with 1,000 bytes, 6 of 400 with 60,000 bytes and 19 of 800 with 99,000 bytes. Every other request got the 401. The script and its output are `scripts\reset_repro.py` and `results\reset-repro.txt` in `%TEMP%\apt-verify-0027-root`.
