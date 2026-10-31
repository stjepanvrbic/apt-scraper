---
title: The gallery server closes a connection per request, which exhausts Windows's ports under the Playwright suite
type: Bug
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: [0057, 0060, 0061]
---

## Outcome

The gallery server speaks HTTP/1.1 and keeps a browser's connections open across requests, so a page that loads thirty listings' photos uses a handful of connections instead of one per request, and the Playwright suite on a Windows runner no longer runs the machine out of ports.

## Acceptance

- [ ] A test opens one connection to the server and gets three responses over it, each HTTP/1.1 without a `Connection: close` header.
- [x] Twenty thousand sequential requests to the server on a Windows runner complete over a handful of connections with the `TIME_WAIT` count flat, where against the HTTP/1.0 server each request opened a connection and the count climbed into the thousands.
- [ ] The CI workflow is green on Linux, macOS and Windows.

## Spec

docs/spec.md, Behavior, the gallery: a local browser app served by the gallery skill's server. The server's protocol is an implementation choice; the spec's rerank budget and the suite's run time depend on it.

## Design

On the Windows runner, #60's checks job failed in the Playwright suite with `page.goto: net::ERR_NO_BUFFER_SPACE` on the first navigation of the 33rd test, after the fourteen-minute Python suite and thirty-two passing browser tests. Chromium's `ERR_NO_BUFFER_SPACE` is its name for the socket error "no buffer space available", `WSAENOBUFS` on Windows, which Windows returns from `connect` when no ephemeral port can be assigned. Main's own Windows runs had passed.

`BaseHTTPRequestHandler` speaks HTTP/1.0 unless `protocol_version` says otherwise, and closes the connection after every response. Each request from the browser or a test is therefore a new TCP connection to `127.0.0.1`, closed by the server, which leaves a `TIME_WAIT` entry naming the client's ephemeral port for the system's `TcpTimedWaitDelay`. Windows does not hand out an ephemeral port that a `TIME_WAIT` entry names, so the dynamic range drains at the request rate. A gallery page loads a listing card and photos per listing, thirty listings per test; the Python suite's own servers and clients had drawn on the same range minutes before.

A diagnostic workflow on a Windows runner started the gallery server with the sample database and sent twenty thousand sequential `GET /` requests through one Node `http.Agent` with keep-alive on, logging the connection count and the `TIME_WAIT` count every two thousand requests, first against the HTTP/1.0 server and then against this change. Its numbers are in Result.

The fix sets `protocol_version = 'HTTP/1.1'` on the handler. Every response already carries `Content-Length`, which HTTP/1.1 keep-alive needs. The one path that must close, `close_unread`, which shuts the socket after refusing a request body, now says so with `close_connection`. The rest of the server is unchanged; `ThreadingHTTPServer`'s daemon threads let an idle kept-alive connection outlive nothing.

## Deviations

## Result

Measured on a `windows-latest` runner, dynamic port range 49152 with 16,384 ports, `TcpTimedWaitDelay` at its default (the registry value is absent). Twenty thousand sequential `GET /` through one Node keep-alive agent:

| Server | Connections opened | `TIME_WAIT` after 2,000 / 8,000 / 16,000 / 20,000 requests | Time |
| --- | --- | --- | --- |
| HTTP/1.0, before (run 41165832099) | 20,000 | 2,008 / 8,008 / 16,008 / 16,150 | 27.0 s |
| HTTP/1.1, after (run 41165930197) | 1 | 3 / 3 / 3 / 3 | 6.5 s |

Against the old server the `TIME_WAIT` count rose one per request until it covered the whole dynamic range, then held at about 16,150 while requests kept succeeding, so the sequential loop alone did not reach `ENOBUFS`; the navigation failure appeared in the real suite when the browser's parallel connections and the Python suite's own sockets came on top of a saturated range. Against the fixed server the loop used one connection, left the count where it started, and ran four times faster. The diagnostic branch that ran both was temporary and is deleted.
