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

The gallery server answers every rejected `POST` with its status and error, and the client receives that answer, also on a busy Windows machine.

## Acceptance

- [ ] A `POST` with a body that the server rejects for authentication (401), origin (403) or content type (400) is answered with that status every time, in a test that sends a body large enough to stay unread.
- [ ] `test_rerank_requires_authentication_same_origin_and_an_empty_json_object` in `tests/test_rerank_server.py` passes in 20 consecutive full-suite runs.
- [ ] Merged to `main` through a reviewed pull request.

## Spec

[Spec › Gallery](../spec.md#gallery)

## Design

`do_POST` in `profile/skills/gallery/scripts/server.py` returns after `allowed()` rejects a request, and raises before `self.rfile.read(length)` for a wrong content type, so the request body stays unread when the handler replies and the connection closes. On Windows, closing a socket with unread received data sends a reset, and a client still sending or not yet reading gets `ConnectionAbortedError: [WinError 10053]` in place of the 401, 403 or 400.

Observed once in two full runs of `python -m unittest discover -s tests` on `main` at `cf3ac2f`, while a Hermes search and extraction ran on the same machine: the subtest's `ConnectionAbortedError` escaped `assertRaises(HTTPError)`, and the test then failed on `raised.exception`. Five further runs of the module alone and the second full run passed.

Reproduced against a server on a temporary store, 200 unauthenticated `POST /api/rerank` requests per row: with an empty body all 800 got 401; with a body of 2 bytes 1 of 200 ended in `ConnectionAbortedError`, 1,000 bytes 0, 60,000 bytes 2, and 99,000 bytes 3, 8 and 5 in three rows. Only rejected requests leave a body unread, and the gallery's own requests carry the token and JSON, so a user meets it when the token has expired, as a connection error in place of "Gallery access is missing or expired".

Read and discard the declared body, up to the server's 100,000-byte limit, before replying to a rejected `POST`.

## Deviations

## Result

## Comments
