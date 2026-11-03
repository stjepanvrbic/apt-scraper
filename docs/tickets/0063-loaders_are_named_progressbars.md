---
title: The gallery's loading indicators carry a name ARIA prohibits on their element
type: Bug
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: [0057, 0061]
---

## Outcome

Every loading indicator in the gallery is an indeterminate progress bar whose name says what is loading, so a screen reader announces it and the accessibility check passes whether or not the gallery is still loading when it runs.

## Acceptance

- [ ] The test `desktop and mobile are accessible, fit the viewport and load bundled photos` holds the listing request, checks accessibility with the listing still loading and its progress bar named "Loading listing" visible, then releases the request and checks again with the listing shown.
- [ ] The CI workflow is green on Linux, macOS and Windows.

## Spec

docs/spec.md, Behavior, Gallery: the listing details drawer. The Playwright suite checks every gallery view with axe.

## Design

The Ubuntu run of the Playwright suite failed that test with one axe violation, `aria-prohibited-attr`, on `<span class="… mantine-Loader-root" aria-label="Loading listing">`. Mantine's `Loader` renders a `span`, which has the generic role, and ARIA 1.2 prohibits naming an element with that role, so the `aria-label` on the five labeled loaders (listing, listings, conversation, comparison, ranking status) was never exposed. The test opened a listing and ran axe at once, so it saw the loader only when the listing request was still pending at that moment; on the other runs it had finished.

The theme gives every `Loader` the role `progressbar` as a default prop, next to the existing `Pagination` default. A progress bar without `aria-valuenow` is indeterminate, and its role requires the name the loaders already carry. The loader inside a Mantine button sits in an `aria-hidden` span, and the rerank panel's review loader is `aria-hidden` itself, so both stay out of the accessibility tree and need no name.

The test no longer depends on timing: it holds the listing request with a route until it has checked the loading state, then checks the loaded drawer. The four copies of the axe call in the spec share a helper.

## Deviations

## Result
