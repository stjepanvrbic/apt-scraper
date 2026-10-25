---
title: Gallery with listings, favorites and notes
type: Feature
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: []
related: []
---

## Outcome

The user browses listings in a local gallery with the five tabs, opens any listing in full, favorites or dismisses it and adds notes.

## Acceptance

- [x] The five tabs appear in spec order and each listing shows every field the spec lists.
- [x] Rankings is a photo grid, and a listing opens to all its photos and details.
- [x] Favorite, unfavorite, dismiss and add note persist across a gallery restart, and a dismissed listing leaves Rankings and Favorites.
- [x] Source links open the original listings.
- [ ] Hermes opens the gallery on request on Windows and macOS.
- [x] On Windows, opening the gallery through Hermes leaves no visible terminal window, and the server survives the agent turn ending.
- [x] Notes sends messages to the apt-scraper Hermes agent, displays streamed replies and tool activity, and accepts clarification choices in the tab.

## Spec

[Spec › Gallery](../spec.md)

## Design

Local web app served on localhost and backed by one listing store that the search, ranking and contact work also use. Start from sample listings until the search exists.

React and TypeScript, built with Vite, use Mantine for cards, tabs, responsive grids, the detail drawer, forms and feedback. Yet Another React Lightbox handles the full photo set and keyboard navigation. Tabler supplies icons. TanStack Query handles loading, refresh and saves. The built frontend and Python standard-library server ship under `profile/skills/gallery/`, so opening the installed gallery needs no Node installation or separate package setup. SQLite lives outside the installed skill and keeps listing updates separate from user feedback and contact history. A documented Python interface and command line share this store with future search, ranking and contact work.

The shared store starts at schema version 1, with transactional initialization and a versioned migration mechanism for future releases. No migrations for unpublished development schemas are shipped. Scraped records accept partial and unranked listings, preserving text prices and unknown values. They store a content fingerprint, rental period and listing-agent details. Criteria eligibility is independent of source availability and excludes records from every gallery view while retaining history. Analysis features and ranking are separate records tied to that fingerprint. Search updates keep first-seen date, ranking and user history. Settings hold criteria, preferences, listing sites and viewing availability. Contact state records channel, agent details and failures. Correspondence requires an existing user request, validates the channel, keeps external message/thread identifiers and deduplicates polled messages without changing contact state. Failed requests expose their error and can be retried. Personal-information approvals are scoped to a listing and purpose and can be revoked. Multiple viewings retain calendar event identifiers and support moves, cancellation and removal.

The browser polls a small revision value. Listing summaries are paginated, with full details and bounded history pages fetched only when opened. Contacted cards include recent correspondence. Viewings sorts by UTC time. Dismissed listings have a recovery view. Source and generated text use LF endings. The build normalizes HTML and license line endings. Hermes profile installation replaces the whole skill directory, so the gallery does not prune installed files.

The owner chose a live two-way Notes conversation. Ant Design X supplies Bubble.List, Sender and ThoughtChain, with X Markdown for streaming replies and native Ant Design buttons and checkbox groups for choices. Approval tokens have human labels. Mantine remains the listing component system and Ant Design X remains the specialized chat system. This deliberately ships two systems to retain maintained chat behavior. The approximately 705 kB chat JavaScript chunk is loaded only when Notes opens. The chat bundle loads only when Notes opens.

Notes connects to the running apt-scraper agent through the installed Hermes 0.22.5 gateway client, reusing `hermes_cli.gateway_client.connect_gateway` and `hermes_cli.gateway_chat_view.GatewayChatView`. The adapter creates or resumes a dedicated session, submits messages, streams text and tool activity, and uses gateway approval and clarification responses with the current execution generation. The gateway client supports interactive approval and clarification responses. Session and admission identifiers persist server-side for recovery and are omitted from browser responses. The adapter waits on gateway/control events, batches text and tool persistence at 100 ms, and flushes prompt transitions and terminal results immediately. Partial updates preserve prompts and errors until explicitly cleared. Notes instructions require accepted preference and criteria changes to be saved with update_settings. These interfaces were inspected in the installed source and exercised against the real gateway.

Every launch creates a bearer secret. Open returns it in a URL fragment, the browser keeps it in session storage, and every API route requires it. Restricted connection records are stored under ~/.secrets. The launcher authenticates health checks, compares the installed-file fingerprint, restarts outdated instances and supports stop. Redacted output and no-browser modes support safe agent verification. Windows uses pythonw.exe without ineffective console creation flags. The merged account-connections skill and SOUL policy remain unchanged.

All browser verification is headless Playwright with screenshots. The opener is stubbed and its target URL asserted. Browser traces are disabled to keep bearer headers out of network recordings. No test is skipped.

The visual reference is [Airbnb's photo-led marketplace](https://www.airbnb.com), with a near-white canvas, white surfaces, Inter type, 20px photo corners, restrained active-state color and no card shadows. [Planpoint](https://www.planpoint.io) contributes blue primary actions. [Airbnb's note dialog](https://refero.design/pages/ec031fad-bb1f-4daa-a522-821edda73cea) informs the labeled text field and explicit save action. The [21st Property Preview Card](https://21st.dev/@shadcnspace/components/card-02) was considered, but its hover-dependent details would hide information on touch devices. Mantine components keep the listing details visible.

Tests exercise the public store interface with real SQLite files, HTTP requests against a live local server, and browser interactions against the built React app. Sample listings are explicitly marked and kept in a separate store from real search results.

## Deviations

- The owner's frontend direction is implemented with React and maintained component libraries. The browser app ships prebuilt inside the gallery skill. No installer or `profile/config.yaml` changes were needed.
- Samples use a separate database so their feedback and contact requests cannot mix with real listings. Sending listing correspondence and booking calendars remain separate workflows. Notes is a live conversation with the agent.
- The owner required a windowless server that survives the Hermes turn. Windows now uses `pythonw.exe`. macOS uses a separate session with redirected output, but its real launch remains unverified here.

## Result

The implementation and this verification record are in the single commit on `feat/gallery`, shown in [PR #8's commits](https://github.com/stjepanvrbic/apt-scraper/pull/8/commits). The ticket remains In Progress for review and merge. No stale intermediate commit identifier is used here.

Round-two findings addressed:

1. Every API route requires a launch bearer secret, including health, chat, controls and stop. Requests without Origin still require authentication. The browser receives the secret in a fragment, removes it from the address bar, and uses the shared request helper. Private connection records have restricted filesystem permissions.
2. Partial chat updates preserve pending questions and errors. Explicit null clears them at settlement.
3. Unranked and stale-ranked matches appear under Awaiting ranking. Criteria exclusion hides a listing from every browser view and detail request while retaining history for reevaluation.
4. Partial imports accept missing price, size, rooms and other optional details. Text prices are retained instead of blocking a batch.
5. Correspondence records external message/thread IDs, deduplicates polling, validates channels, requires a user-requested contact and preserves lifecycle state.
6. Drawer, export and booked-view ordering use viewing time with an identifier tie-breaker.
7. The unpublished migration chain and fixture are replaced by a single version-1 schema. Version recording and newer-schema rejection remain. Asset pruning and its manifest are removed because Hermes replaces each authored skill directory on install.
8. Generated HTML and license text use LF. An unchanged revision does not refetch listing data on background checks or focus. The Result points to the PR's single commit instead of an intermediate hash.
9. Gallery and chat use one authenticated fetch helper with the same network-error message.
10. Read and revision failures have read-error labels. Save errors are separate.
11. The dead listing alias is removed, and the count uses place or places correctly.
12. The gallery guide describes current behavior without implementation history.
13. Automated tests execute run_turn against a controlled external Hermes interface, covering streaming, question retention, denial, batching and preference instructions. The HTTP test explicitly names its controlled runner. Live gateway checks are recorded separately.
14. Failed contacts show their error and a Retry contact action.
15. Approval buttons have human labels and send the gateway's original value. A real no-op test tool invoked Hermes's approval gate. Headless Playwright selected Deny, and Hermes reported the denial without any subsequent action. Screenshots captured both states. The temporary tool was removed and the original profile configuration restored exactly.
16. The launcher detects changed installed files, rotates the secret after restart, and provides an authenticated stop command. Automated tests verify replacement and stop.
17. The skill and Notes context require accepted preference changes to be persisted. A real headless conversation saved quiet_street: true in the sample store, verified through the store interface. The account-connections SOUL instructions coexist unchanged.

Final Windows verification:

- `python -m unittest discover -s tests -v`: 76 passed, 0 skipped, including the merged account-connections suite.
- `ruff check profile/skills/gallery/scripts tests`, frontend ESLint, TypeScript and the Vite build passed.
- Headless Playwright: 14 passed, 0 skipped. Checks cover authenticated access, invalid origins, partial/unranked listings, failed-contact retry, read-error labeling, focus/background revision behavior, keyboard choices and the existing gallery flows.
- `npm audit --omit=dev`: zero vulnerabilities. The lazy chat chunk retains the documented size warning.
- Rebuilding against the staged output produced no diff. HTML and bundled license text contain no CR bytes. The final post-commit rebuild is checked before pushing.
- Real Hermes tests used `-Q` with `open --no-browser --redact-token`. The launcher succeeded, the authenticated server remained alive after the turn, and a console probe returned false. Unauthenticated health requests returned 401.
- Headless screenshots recorded the genuine Hermes approval prompt and denial. The temporary no-op native tool called `request_tool_approval`, then returned the decision without dispatching an action. The probe was removed through Hermes and the original config restored byte-for-byte.
- A real headless Notes conversation persisted `preferences.quiet_street = true` in the sample database. The store was read back to verify it.
- The opener test stubs `webbrowser.open` and asserts the full authenticated URL. No owner browser check is used by the final verification.

 macOS execution remains unverified on this Windows machine and the combined platform acceptance item remains unticked. Earlier development databases were retained as backups before verifying the requested clean version-1 store. No account setup or lasting configuration changes were made. The owner's browser rule is honored by the final checks: no owner tab is opened, focused or closed, and opener verification uses a stub.
