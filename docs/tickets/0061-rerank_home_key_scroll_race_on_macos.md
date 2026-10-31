---
title: The rerank favorites table's Home key is lost on the last frame of End's scroll animation
type: Bug
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: [0057, 0060]
---

## Outcome

The gallery's Playwright test of the favorites table's keyboard scrolling passes on every platform's CI run, because it presses Home once End has scrolled the table to its end, which is the interaction it checks, instead of at whatever point End's scroll animation has reached.

## Acceptance

- [x] `rerank results and full explanations are accessible on desktop` and `on mobile` pass 24 of 24 repeats on a macOS runner, where before the fix 3 of 24 failed, and with the first attempt at a fix, the equality wait, 12 of 24.
- [ ] The CI workflow is green on Linux, macOS and Windows.

## Spec

docs/spec.md, Behavior, the gallery's rerank panel: the favorites and held-out ranks table scrolls within the panel. The test covers its keyboard scrolling and its pinned header.

## Design

On the macOS runner the test `rerank results and full explanations are accessible on <viewport>` failed intermittently at `await expect.poll(() => favorites.evaluate(element => element.scrollTop)).toBe(0)` after `favorites.press('Home')`: the table stayed at 115 or 116 of 116 pixels for the five-second poll. Linux and Windows never failed it.

A diagnostic run repeated the test twelve times per viewport on a macOS runner with listeners on the table for `keydown`, `scroll`, `focus` and `blur`, and sampled `scrollTop` for two seconds after Home. It failed 3 of 24 repeats. In every failure, the Home `keydown` reached the focused table, not default-prevented, while End's smooth-scroll animation was on its last frame, at 115 of 116 pixels; after it, no `scroll` event fired at all, and the table stayed at 115. In the passing repeats Home arrived earlier in End's animation, at around 105 pixels, and the table ran on to 116 and then back to 0. So Chromium drops a keyboard scroll that arrives as the previous keyboard scroll's animation completes, and the earlier animation does not complete either. The table is a native scroll container (Mantine's `ScrollArea.Autosize` viewport); nothing in the gallery handles those keys.

The test pressed Home as soon as `scrollTop` was greater than zero, that is, at an arbitrary point of End's animation. Its intent is that End scrolls to the end and Home back to the start, so it now waits until the table's scroll position has stopped moving before pressing Home: two readings 200 ms apart equal and above zero, where the whole animation takes about 170 ms. Waiting for `scrollTop` to equal `scrollHeight - clientHeight` was tried first and failed every mobile repeat: on the 390-pixel viewport End's animation ends at 115 of 116, one pixel short of that difference, so the equality never holds. The header-pinning check between the two presses also meant to run at the end of the table.

## Deviations

## Result

The 24-repeat run on a macOS runner passed 24 of 24 with the settled-scroll wait (GitHub Actions run 41160544247), against 3 of 24 failures before the fix (run 41159156179) and 12 of 24 with the equality wait (run 41159962501). The diagnostic branch that ran them was temporary and is deleted.
