# Apartment gallery

Ask `hermes -p apt-scraper` to open the gallery. After pulling changes, rerun the repository installer to update the gallery skill. Opening the gallery needs Python 3.12 or newer, which Hermes supplies. The Notes conversation also needs the Hermes runtime, so launch through Hermes to use it. Node is only needed to develop the frontend.

The six tabs are Rankings, Favorites, Compare, Contacted, Viewings and Notes. Open a listing to see its photos, details, sources, notes and contact history. Favorites and dismissals are saved immediately. Notes save when you press Save note. A failed save keeps the text so you can retry. Dismissed listings leave Rankings and Favorites. Their notes, correspondence and viewing details remain in the store.

Contact requests are saved as pending. The gallery itself sends nothing. The [contact skill](../profile/skills/contact/SKILL.md) picks up the request within a few minutes, records each message it sends and receives, and books agreed viewing times in your Google Calendar. A listing shows as contacted once its first message is out, and a viewing appears in Viewings with its time once its calendar event exists. Sample listings are never contacted. The Notes tab is a live conversation with the running Hermes agent. It streams replies and tool activity, and shows questions or approval choices in the tab. The conversation resumes the same Hermes session after reopening.

## Score and rerank

Press **Score and rerank** in Rankings to apply your favorites, dismissals, contact requests, notes, comparisons and stated preferences. Scoring runs locally on the saved feature records. It changes the order only when requested and leaves the review waiting for Hermes. The button stays unavailable while scoring, and a failed request shows an error so you can retry.

The result shows each favorite's held-out rank: where it ranks when fitted without that favorite. Lower is better. Without favorites there is no held-out check yet. Listings marked **Exploration** are shown to learn your taste; with more than 20 eligible ranked listings, two of the top 20 have this mark.

The review starts through the existing Hermes review job, normally within a minute, and can take a few minutes to finish. Keep the gallery open to see its waiting or running state, its result, moves and reasons, preference flags, and findings. The gallery polls the saved result and refreshes the order and listing reasons when the review finishes. A reverted review shows the proposed moves and both check values, with the reason the moves were rejected. Failed reviews show their error. The scoring result remains available while the review runs.

Open a listing for its full scoring explanation, including every feature's contribution and the most similar favorite. Contributions describe the scoring model; any later review move is explained in the review result. Place distances carry the credit "© OpenStreetMap contributors". Listings awaiting ranking follow the ranked ones, with a reason distinguishing current features ready for a rerank, features still waiting, and failed extraction. After three extraction failures, the next rerank uses code-derived features alone and says so in the reason.

Unranked listings in other tabs explain when they are unavailable, dismissed or outside the criteria. These listings cannot receive a rank until they are eligible again.

## Compare

Compare asks "Which of these two?" with the main photo, price, rooms, size and address of each apartment. Open either listing for its full details, choose the one you prefer, or skip. A choice saves immediately and counts at the next rerank; the current ranking stays still. Answered pairs never return. A skipped pair stays out until twenty other questions have been answered or skipped, including across restarts. If every eligible pair is answered or cooling down, Compare shows that there are no questions available. It does not shorten the skip cooldown to fill the screen.

Questions use available, matching, undismissed listings with ranking features. The scoring model chooses the order it is least certain about among its forty leading candidates. After the interview and the first two eligible feature records, the listing features job asks the user through the messaging gateway to answer 5 to 8 questions. Successful delivery is recorded once; a failed delivery remains pending. Sample listings do not trigger an invitation.

## Open from a checkout

On Windows:

```powershell
python profile/skills/gallery/scripts/gallery.py --sample open
```

On macOS:

```bash
python3 profile/skills/gallery/scripts/gallery.py --sample open
```

The command opens the default browser and returns a local URL containing a fresh launch secret in its fragment. The frontend moves the secret into session storage and removes it from the address bar. Do not share or log that URL. `open --redact-token` opens the authenticated link but prints only the base URL. Add `--no-browser` for headless verification. Every `/api/*` request, including health and stop, requires the bearer secret. Connection records stay under `~/.secrets/apt-scraper/gallery/`, protected by a restricted Windows ACL or owner-only POSIX permissions. The Windows server runs with `pythonw.exe`. On macOS it starts a new session. Both redirect diagnostics to `gallery.log` and keep running after the launch command exits. The `serve` command runs in the foreground for development.

The default port is 8765. If it serves another application or database, select a different port with `--port 18765` before `open`. The launcher reports the conflict and leaves the other process alone. A running gallery is reused only when its database and install fingerprint match. Changed installed files cause an authenticated stop and restart. Use the `stop` command to end the server without deleting data.

`--sample` selects a separate sample database. Its fictional listings use stock photos and example source URLs. Sample feedback and requests never become real search data. Omit `--sample` to open the real store, initially empty.

## Data and integration

Inside Hermes, data is under `HERMES_HOME/workspace/gallery/`. Outside Hermes, Windows uses `%LOCALAPPDATA%/apt-scraper/` and macOS uses `~/Library/Application Support/apt-scraper/`. The databases are `listings.sqlite3` and `sample.sqlite3`, and `store_path(sample)` in `store.py` returns their paths. Search settings are always in `listings.sqlite3`. `--database <path>` selects an explicit database. Keep all global options before the command name.

The shared Python interface is `ListingStore` in `profile/skills/gallery/scripts/store.py`. The schema is at version 9, recorded in `PRAGMA user_version`. Each version's migration runs in a transaction when the store opens, so a version-1 store gains the contact workflow's records without losing data, a version-2 store gains the code-derived features of every stored listing, a version-3 store gains the feature records, and a version-4 store gains explanations, exploration marks, reranks, readings and pairwise answers, keeping its ranks. A version-5 store gains the persistent pairwise skip cooldowns. A version-6 store gains geocodes, stations and the distances of every listing, with each saved place kept as its name and address. A version-7 store gains the review passes of later reranks. A version-8 store gains implicit gallery signals. Databases from newer schema versions are rejected. Scraped fields, analysis, code-derived features, feature records, ranking, user feedback, implicit signals, contacts, approvals, viewings and chat records are separate.

| Method | Behavior |
| --- | --- |
| `update_settings(judge=None, **values)`, `settings()`, `missing_settings()` | Replaces each named setting among `criteria`, `preferences`, `places`, `location_priorities`, `listing_sites`, `viewing_availability` and `transit_feed`, in the format the [interview skill](../profile/skills/interview/SKILL.md) gives. Settings left out stay unchanged. Saving criteria while listings are stored needs `judge`, which the same transaction applies to every stored listing as `apply_criteria` does. The interview's `settings.py` passes the search's criteria judgment, as the [search guide](search.md) describes. Saving criteria drops the location priority of every area they no longer name, and a location priority for an area the criteria do not name is rejected. Saving places or the transit feed computes every listing's distances again. `missing_settings(names)` lists what the interview still needs, limited to the named settings. `places_without_coordinates()` names the saved places whose address has no stored geocode. |
| `save_geocodes(geocodes)`, `geocodes(addresses)` | Keeps and returns the coordinates a geocoder found for place addresses, by address: `latitude`, `longitude` and `found`, the name the geocoder gave the location. |
| `transit_feed()`, `save_stations(url, stations)` | The feed URL the `transit_feed` setting names, or CTA's by default, with `fetched_at` and its `stations` once fetched. `save_stations` replaces the stations with those fetched from `url`, each `id`, `name`, `latitude` and `longitude`, and computes every listing's distances again. |
| `upsert_listing(listing, matches_criteria=None)` | Saves scraped content without rank or reason. Returns `changed`, `needs_analysis` and `content_fingerprint`. Preserves first-seen date, ranking and history. `matches_criteria` true or false shows or hides the listing in the same transaction, so a new listing outside the criteria is never shown. |
| `apply_criteria(judge)` | In one transaction, calls `judge(criteria, features)` for every stored listing with the stored criteria and the listing's current features or `None`, shows the listings judged `inside` and hides every other one. Returns the count of each verdict, or `None` without criteria. |
| `set_matches_criteria(id, matches)` | Hides or restores a listing without deleting history. Search updates preserve this decision. |
| `set_availability(id, status)` | Marks a listing available or unavailable without requiring the original source payload or changing analysis. |
| `analyze(id, fingerprint, features)` | Saves a JSON object of features against the exact content fingerprint. Rejects stale work. |
| `features(id)` | Returns current stored features, or `None` when analysis is missing or stale. |
| `code_features(today=None)` | The code-derived part of each listing's feature record, by listing id: monthly price, size, price per square foot, bedrooms, bathrooms, unit label, floor and level, photo count, coordinates, the nearest rapid-transit station with its distance and the distance to each place, as `code_features.py` describes them, with the content fingerprint they belong to, `first_seen` and `days_since_first_seen` counted to `today` (by default today in UTC). Saving a listing whose content changed and saving its features recompute them, without a model call. |
| `save_extraction(id, fingerprint, extraction, photos=..., phrases=...)` | Stores the model's part of the feature record of this content when it keeps the contract of `record.py`, with each photo's URL from `photos`, the unique photo URLs the extraction numbered from 1. `phrases` are the stated preferences it was asked to judge. Rejects a record that breaks the contract, naming the field, and stale content, and stores nothing then. New traits and concerns join the tag list in the same transaction. Returns `new_tags`. |
| `judge_preferences(entries)` | Adds judgments of preferences stated after the extraction, each entry `(id, fingerprint, judgments, phrases)`, to the records of those contents, all in one transaction or none when any entry is invalid or its listing has no record of that content. |
| `feature_records()`, `feature_record(id)` | The record of every listing whose current content has one, or of one listing, or `None`: `extraction`, `preferences` judged by phrase, and `extracted_at`. |
| `tags()` | The tag list, sorted: the seed tags of `record.py` and every tag an extraction added. |
| `read_attempts()`, `record_read_attempts(failed, waiting)`, `reset_read_attempts()` | How many times each model read, such as an extraction, was handed to the agent and ended without a saved result. `record_read_attempts` counts one more for each read in `failed` and forgets each read in neither list. |
| `ranking_inputs()`, `save_rerank(result)`, `last_rerank()` | What a rerank reads, from one snapshot: each listing with its current feature record, code features, criteria areas, whether extraction gave up on it, whether it is shown in Rankings and the user's actions on it, plus the settings, readings, latest note sentiments and pairwise answers. `save_rerank` writes a rerank's result in one transaction: each ranked listing's rank, reason, explanation and exploration mark, each unranked listing's reason, and the summary, and clears every other rank. Entries whose listing changed meanwhile are left out and listed in the summary's `stale`. It also leaves the rerank's review pass waiting with the scores its check reads, supersedes a review still waiting for an earlier rerank, and returns the rerank's id. `rerank(id)` is that rerank's summary with its `id`, `finished_at` and `review`: the review's `state` (waiting, running, done, failed or superseded), `attempts`, `updated_at` and `result`. `last_rerank()` is the latest. `rerank(store)` in `rerank.py` is the one caller, as [the ranking guide](ranking.md) describes. |
| `ranked_listings()`, `review_snapshot(rerank_id)`, `hand_review(rerank_id)`, `fail_review(rerank_id, error)`, `save_review(rerank_id, ranks, reasons, result)` | The review pass's records. `ranked_listings` is each listing the latest rerank ranked whose content has not changed, with its rank, reason, explanation and exploration mark. `review_snapshot` is what the rerank stored for a waiting review's check: its order, exploration listings, full-fit scores and each favorite's held-out scores, removed once the review ends. `hand_review` counts one more handing of the review to the agent, or returns `None` when it no longer waits, `fail_review` ends a waiting review as failed with the error in its result, and `save_review` applies a saved review in one transaction: new ranks and rewritten reasons of the listings that have not changed since the rerank, and the result. It is refused when a later rerank replaced the review. `pending_review(store)` and `save_review(store, value)` in `rerank.py` are the callers. |
| `unread_notes()`, `unread_preferences()`, `save_readings(notes=..., preferences=...)`, `note_readings()`, `preference_readings()`, `reading_vocabulary()` | The user's notes and stated `(kind, phrase)` preferences without a reading, and the readings the model saved: notes as `(note id, {sentiment, likes, dislikes})`, preferences as `(kind, phrase, features)`, each feature `{feature, direction}` from the vocabulary, all in one transaction or none. |
| `answer_pair(chosen, other)`, `pairwise_answers()` | Records the user's answer to a pairwise question, chosen over other, which counts at the next rerank. An identical retry is unchanged; an opposite answer to an answered pair is rejected. Both listings must still be available, matching and undismissed. |
| `skip_pair(first, second)`, `skipped_pairs()` | Suppresses the unordered pair for twenty subsequent answered or skipped questions. Reading or refreshing a question does not advance the cooldown. Repeated skips during the cooldown are unchanged. |
| `given_up_extractions()` | The listings whose current content failed extraction 3 times, which the ranking ranks from their code features. |
| `record_signals(events)` | Atomically records 1 to 20 implicit events, returning the number inserted. An identical event ID retry is unchanged; a conflicting retry or invalid event rejects the whole batch. Does not change the gallery revision. |
| `rank(id, fingerprint, rank, reason)` | Sets one listing's rank, used only to seed the sample store. Rejects stale content fingerprints. |
| `listing(id, notes_page=1, correspondence_page=1, viewings_page=1)` | Full details for one listing, with its `explanation` and `exploration` mark from the latest rerank. Each history has pages of 50 and a total count. |
| `browse(view, page=1, page_size=24)` | Summaries for Rankings, Favorites, Contacted, Viewings or Dismissed, each with its rank, reason and exploration mark. Page size is at most 100. Viewings sorts by UTC time and excludes canceled visits. |
| `state()` | Raw full export using bulk queries, including unavailable and dismissed listings. It preserves historical ranks and reasons, with `analysis_current` marking whether they describe current content. `listing()` and `browse()` instead return current ranks and pending reasons. It is not the browser polling endpoint. |
| `feedback(id, favorite=True/False, dismissed=True/False)` | Saves supplied flags. Dismissal clears the favorite. Restore with `dismissed=False`. |
| `add_note(text, listing_id=id, role='user')` | Appends a listing note with author `user` or `agent`. |
| `request_contact(id)` | Records one pending request and copies the source's agent details. Repeated pending requests are unchanged. Retrying a failed request resets it to pending and clears its error. |
| `set_contact(id, status, channel=None, agent=None, error=None, subject=None)` | Updates pending, in_progress, contacted, failed or closed state. Channels are email, imessage or contact_form. Failed state requires an error. `subject` keeps the first email's subject, which reply polling searches for. |
| `correspond(id, direction, channel, text, external_message_id=..., external_thread_id=...)` | Records a message only for an existing requested contact. Deduplicates by listing, channel and external message id. It preserves contact state, including failed or closed. An outgoing message marks every earlier incoming message and every answered question of the listing handled. |
| `has_message(id, channel, external_message_id)` | Whether that message is already recorded, so polling fetches only new ones. |
| `due_contacts()`, `mark_handled(id)`, `active_contacts()`, `thread_owners(channel)` | `due_contacts` lists the contacts with an incoming message newer than the last handled one (`reply`), with an answer from the user not yet acted on (`answer`), or with no message sent yet (`start`), leaving out listings with an open question and failed or closed contacts. `mark_handled` marks every message and answer so far handled without a reply. `active_contacts` lists the pending, in-progress and contacted ones, with agent details, for polling. `thread_owners` maps each email thread or iMessage chat of an open contact to the listing that last wrote in it. |
| `ask_user(id, question, field=None, purpose=None, external_message_id=None)`, `open_questions(id=None)`, `answer_question(question_id, answer, share=None)`, `unhandled_answers(id)` | Records a question sent to the user, and moves a pending request to in progress. An answer closes it, and `unhandled_answers` lists it until the next outgoing message or `mark_handled`. `share` records the value as approved for the question's listing, field and purpose in the same transaction, and needs a question with both. |
| `approve_information(id, field, value, purpose)` | Records the exact personal information approved for this listing and purpose. Returns an approval identifier. |
| `approved_information(id, purpose)`, `approvals(id)`, `revoke_approval(approval_id)` | Reads only approvals for this scope, every approval of the listing with its purpose, or revokes one. Never infer permission from another listing or purpose. |
| `agree_viewing(id, time, details, viewing_id=None)`, `agreed_viewings()`, `scheduled_viewings()` | Records a time agreed in correspondence with status `agreed` and no calendar event yet, or moves the specified agreed or booked viewing back to `agreed` with its event identifier kept, so the calendar booking updates that event. A canceled viewing cannot be moved. A time within `VIEWING_MINUTES` (60) of another agreed or booked viewing raises `ViewingConflict`, checked in the same transaction. `agreed_viewings` lists every viewing waiting for a calendar write, and `scheduled_viewings` every agreed or booked one. |
| `record_booking(viewing_id, time, details, calendar_event_id)`, `canceled_viewing_events()`, `record_event_removed(viewing_id, calendar_event_id)` | `record_booking` books an agreed viewing once its event is written, and returns false when the viewing moved, changed or was canceled in the meantime: a moved viewing stays agreed for the next write, and a canceled one takes the identifier of the event just written so that event is removed. `canceled_viewing_events` lists canceled viewings that may still have an event, and `record_event_removed` clears the event identifier once the event is deleted or found missing. |
| `cancel_viewing(viewing_id, possible_event_id=...)` | Keeps a canceled viewing in history. A viewing without a recorded event keeps `possible_event_id`, the identifier the contact skill creates its event under, so an event whose creation answer was lost is removed too. Canceling again changes nothing. |

Scraped records require only `id` and `sources`. Title, price, currency, rental_period, size, rooms, address and description can be missing or null. A textual price is retained in `price_note` and its numeric price remains null. Missing photos, amenities and agent details default to empty collections. The first-seen date defaults to the import date and is preserved on later imports. Availability defaults to available. Unknown information is displayed as not listed.

`rental_period` is day, week, month, year or total when known. Agent details may include name, email, phone and contact_form. Numeric prices must be nonnegative, and supplied currency codes have three uppercase letters. Dates use `YYYY-MM-DD`. Status is available or unavailable. Sources need a label and an HTTP or HTTPS URL without credentials. Packaged photos use `/media/<filename>`. Unsafe URLs and malformed values are rejected.

The model's part of a listing's feature record is in the `feature_records` table, one row per listing with the content fingerprint it describes, next to the code-derived part in `code_features`. A record of earlier content no longer counts, and the next extraction replaces it. See [the ranking guide](ranking.md).

The fingerprint hashes canonical JSON of the scraped content, excluding id, first_seen and status. An availability change alone does not request another model analysis. A content change retains previous analysis and rank as historical values, but marks them stale. Rankings displays current ranked records first, followed by matching listings awaiting ranking. Saving changed criteria applies their exclusions, and `set_matches_criteria` excludes a single listing, such as a newly found one. Excluded listings are absent from Rankings, Favorites and Dismissed and from detail requests, but remain accessible to the agent for reevaluation. A listing with a contact request or a viewing stays in Contacted and Viewings, and its details open. The store does not decide criteria or compute rankings. The bundled sample file includes rank and reason for seeding, which are not accepted as scraped fields.

The browser checks `/api/revision` every 15 seconds and on window focus. Listing and detail queries do not refetch on focus independently of that revision. It fetches `/api/listings?view=...&page=...` when the revision changes, on navigation, on manual refresh, or when the rerank status reports a new rerank or a completed review. Rerank status polls every second while scoring or a review is pending, and every 15 seconds otherwise, so progress appears promptly. Waiting-to-running review changes update only the status panel. Summaries omit descriptions, amenities and full photo sets. Contacted includes the three latest messages, capped at 500 characters each, with a link to all correspondence. Opening a listing fetches `/api/listings/<id>`. Only the manual Refresh button displays the refresh spinner.

Compare reads `/api/compare`, returning one question with both listings (or `null`) and the total number of answered pairs. It writes `/api/compare/answer` with `{chosen, other}` or `/api/compare/skip` with `{first, second}`. These routes use the same authentication, origin checks and JSON validation as other gallery actions.

`POST /api/rerank` accepts an empty JSON object and returns the saved rerank summary, including the held-out ranks and waiting review. It calls `rerank(store)` without starting Hermes or making a model call. The server rejects overlapping scoring requests with 409 and releases the scoring lock even on failure. `GET /api/rerank` returns `{running, last_rerank}`; `last_rerank` is null before the first run and otherwise includes the stored review state and result. Reading it does not start a rerank or a review. Listing reads hide stale ranks, exploration marks and explanations, and derive pending reasons from the current feature record and extraction attempts.

Opening details from any tab records an `open` event once the details are loaded and visible. The gallery records visible time in `dwell` increments and the selected photo URL each time the full photo viewer changes photos, including its initial photo. It records `close` when details close or the page exits. Loading time, hidden-tab time and preloaded adjacent photos do not count. Refreshing the same details keeps the visit; reopening starts a new one. The interface is unchanged.

These events go only to the local, authenticated `POST /api/signals` route as `{events: [...]}`. Every event has `event_id`, `visit_id`, `listing_id`, `fingerprint`, `kind` and an ISO `occurred_at` timestamp with timezone. Only `dwell` has integer `duration_ms`; only `photo` has `photo_url`. The `implicit_signals` table retains the content fingerprint and photo URL so changed content cannot reinterpret an old interaction. Signals do not invalidate listing queries or change ranks.

The browser uses Mantine's [window-event](https://mantine.dev/hooks/use-window-event/) and [interval](https://mantine.dev/hooks/use-interval/) hooks and the lightbox's [view callback](https://yet-another-react-lightbox.com/documentation). It batches at ten-second checkpoints, at twenty queued events, and when details close or the tab hides or exits. Each request contains at most twenty events and 60,000 UTF-8 bytes, using the existing bearer fetch layer with `keepalive`. Failed batches retain their IDs and retry at the next checkpoint. The in-memory buffer holds at most 200 events; failures and dropped events are reported in the browser console without interrupting browsing. A visit that loses events to overflow is excluded from the diagnostic. Browser termination or closing during a server outage can lose unsaved events. There is no external telemetry; signals are not persisted in browser storage.

Writes use `/api/listings/<id>/{feedback,notes,contact}` and return a small acknowledgment. Invalid input returns 400, missing listings return 404 and storage failures return 500. The server binds to IPv4 loopback, authenticates every API request, checks Host and Origin, and accepts mutations only as JSON. It serves files only from its bundled web directory.

## Notes conversation

Ant Design X supplies Bubble.List, Sender and ThoughtChain. X Markdown renders reply text. Ant Design buttons and checkbox groups handle suggestions, clarification and approval choices, with labels such as Allow once, Allow for this conversation and Deny. Pagination and alerts use the same library. The application does not implement its own message layout, composer, scrolling or Markdown parser.

The backend uses the installed Hermes 0.22.5 gateway client in `hermes_cli/gateway_client.py` and the event viewer in `hermes_cli/gateway_chat_view.py`. It creates one session in the `apt-scraper` profile, resumes its saved session id, submits through `prompt.submit`, consumes text and tool events, and obtains final responses through `prompt.receipt`. `approval.respond`, `clarify.respond` and `session.interrupt` use the execution generation from the gateway. The user's explicit response is required. The profile's model, tools and permission policy remain in effect.

The Notes adapter uses the gateway client behind `hermes chat`, with interactive approval and clarification responses. The server must run inside the Hermes Python environment. A missing runtime produces a visible error rather than a simulated reply.

Messages and tool activity persist locally. Only one reply runs at a time. The UI polls the active conversation while it runs and shows 20 messages per history page. The gateway adapter waits on events and control responses. It batches text and tool updates at 100 ms and flushes prompt transitions and final results immediately. Omitted update fields preserve stored prompts and errors. Gateway session and admission identifiers remain server-side. A gallery restart reconnects to an admitted turn without resending its text. If admission was interrupted before it could be recorded, the error says so and the message is not automatically replayed.

## Command line

All global options precede the command:

```text
python gallery.py status
python gallery.py --sample stop
python gallery.py export
python gallery.py import listings.json
python gallery.py note "Check the bedroom windows." --listing example:100 --role user
python gallery.py correspond example:100 incoming email "Friday at 3 works." --message-id message-100 --thread-id thread-100
```

Viewings are agreed, moved, canceled and booked only through the contact skill's `agree` and `cancel`, which keep the calendar in step. Other shared-store operations use the Python interface above. The Hermes terminal runs Bash, including Git Bash on Windows. The skill uses that shell's quoting and the Hermes Python environment.

## Development and checks

The frontend source is in `gallery/`. It uses React, TypeScript, Mantine, TanStack Query, Tabler icons and Yet Another React Lightbox. Vite builds the assets into the gallery skill's `web/` directory. Commit the generated assets with source changes so an installed profile can open the app without a frontend toolchain. Source and output line endings are pinned to LF. Assets have stable names. The build normalizes HTML and bundled license line endings to LF. Hermes profile installation replaces the complete gallery skill directory, removing obsolete files.

```powershell
python -m unittest discover -s tests -v
ruff check profile/skills/gallery/scripts tests
cd gallery
npm ci
npx playwright install chromium
npm run lint
npm run build
npm test
```

The Python tests use real SQLite files and HTTP servers. Launcher tests start through a virtual environment, exit the caller, and check that the server remains reachable. On Windows they verify the server has no console. On POSIX they verify a separate session. Automated adapter tests execute `run_turn` against a controlled external Hermes interface. Live verification uses the installed gateway. Browser checks run headless with screenshots. The opener is stubbed in its command test. Hermes test launches use `open --no-browser`. Browser tests use isolated stores and the production build, including real server restarts, keyboard navigation, failed saves, missing photos, source navigation and accessibility checks at desktop and mobile sizes.

Screenshots from the test suite stay in the ignored `gallery/test-results/` directory. To refresh the six rerank documentation screenshots explicitly, run `npm run screenshots:docs` from `gallery/` after building. This runs the desktop and mobile screenshot checks in an isolated output directory, then copies their images into `docs/images/` only if both checks pass.

## Assets and references

The UI follows the photo grid and note patterns of [Airbnb](https://www.airbnb.com), with primary action color informed by [Planpoint](https://www.planpoint.io). Compare reuses the listing card with equal choice buttons and the existing details drawer, following [Mantine card patterns](https://ui.mantine.dev/category/app-cards/). The layout keeps both choices at equal visual weight and hides their ranking cues. Component behavior follows [Mantine](https://mantine.dev/getting-started/), [TanStack Query](https://tanstack.com/query/latest/docs/framework/react/quick-start) and [Yet Another React Lightbox](https://yet-another-react-lightbox.com/documentation).

The rerank panel keeps that photo-led layout, Inter metadata and blue primary action. Its compact summary and expandable results follow [Aboard's summary and disclosure pattern](https://refero.design/pages/a0a318b7-2e11-40a4-84ae-d0b87e7b812b), using Mantine Paper, Accordion and Table. The favorite check scrolls within the panel, and the listing drawer holds the signed feature contribution table. Exploration uses an explicit text label beside a compass icon.

The sample photos are distributed under the [Unsplash license](https://unsplash.com/license). They illustrate the sample interface and do not depict the fictional addresses:

- [Living room](https://images.unsplash.com/photo-1600210492486-724fe5c67fb0)
- [Kitchen](https://images.unsplash.com/photo-1484154218962-a197022b5858)
- [Apartment interior](https://images.unsplash.com/photo-1522708323590-d24dbb6b0267)
- [Living area](https://images.unsplash.com/photo-1502672260266-1c1ef2d93688)

Vite emits bundled dependency notices as `web/licenses.txt`. The font license is bundled alongside the photos. Python's [windowed interpreter](https://docs.python.org/3/using/windows.html#python-install-manager) avoids a Windows console. The server uses `pythonw.exe` without console creation flags. Console flags apply only to short-lived console utilities used by the launcher.
