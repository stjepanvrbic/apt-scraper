# Apartment gallery

Ask `hermes -p apt-scraper` to open the gallery. After pulling changes, rerun the repository installer to update the gallery skill. Opening the gallery needs Python, which Hermes supplies. The Notes conversation also needs the Hermes runtime, so launch through Hermes to use it. Node is only needed to develop the frontend.

The five tabs are Rankings, Favorites, Contacted, Viewings and Notes. Open a listing to see its photos, details, sources, notes and contact history. Favorites and dismissals are saved immediately. Notes save when you press Save note. A failed save keeps the text so you can retry. Dismissed listings leave Rankings and Favorites. Their notes, correspondence and viewing details remain in the store.

Contact requests are saved as pending. The gallery itself sends nothing. The [contact skill](../profile/skills/contact/SKILL.md) picks up the request within a few minutes, records each message it sends and receives, and books agreed viewing times in your Google Calendar. A listing shows as contacted once its first message is out, and a viewing appears in Viewings with its time once its calendar event exists. Sample listings are never contacted. The Notes tab is a live conversation with the running Hermes agent. It streams replies and tool activity, and shows questions or approval choices in the tab. The conversation resumes the same Hermes session after reopening.

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

The shared Python interface is `ListingStore` in `profile/skills/gallery/scripts/store.py`. The schema is at version 3, recorded in `PRAGMA user_version`. Each version's migration runs in a transaction when the store opens, so an older store gains the contact workflow's records and the ranking's note and preference readings without losing data. Databases from newer schema versions are rejected. Scraped fields, analysis, ranking, user feedback, contacts, approvals, viewings and chat records are separate.

| Method | Behavior |
| --- | --- |
| `update_settings(judge=None, **values)`, `settings()`, `missing_settings()` | Replaces each named setting among `criteria`, `preferences`, `listing_sites` and `viewing_availability`, in the format the [interview skill](../profile/skills/interview/SKILL.md) gives. Settings left out stay unchanged. Saving criteria while listings are stored needs `judge`, which the same transaction applies to every stored listing as `apply_criteria` does. The interview's `settings.py` passes the search's criteria judgment, as the [search guide](search.md) describes. `missing_settings()` lists what the interview still needs. |
| `upsert_listing(listing, matches_criteria=None)` | Saves scraped content without rank or reason. Returns `changed`, `needs_analysis` and `content_fingerprint`. Preserves first-seen date, ranking and history. `matches_criteria` true or false shows or hides the listing in the same transaction, so a new listing outside the criteria is never shown. |
| `apply_criteria(judge)` | In one transaction, calls `judge(criteria, features)` for every stored listing with the stored criteria and the listing's current features or `None`, shows the listings judged `inside` and hides every other one. Returns the count of each verdict, or `None` without criteria. |
| `set_matches_criteria(id, matches)` | Hides or restores a listing without deleting history. Search updates preserve this decision. |
| `set_availability(id, status)` | Marks a listing available or unavailable without requiring the original source payload or changing analysis. |
| `analyze(id, fingerprint, features)` | Merges the given top-level feature keys into the listing's current features, saved against the exact content fingerprint, so the search and the ranking each write their own keys. Features of earlier content are dropped. Rejects stale work. |
| `features(id)` | Returns current stored features, or `None` when analysis is missing or stale. |
| `rank(id, fingerprint, rank, reason)` | Updates ranking independently of scraped content. Rejects stale content fingerprints. |
| `rerank(ranked, unranked)` | Replaces the whole ranking in one transaction: `ranked` maps listing ids to `(fingerprint, rank, reason)`, `unranked` maps listing ids to `(fingerprint, reason)` for listings left out. Every other listing loses its rank and reason. Unchanged rows are not written. Returns the count ranked and the ids skipped because their content changed. |
| `listing(id, notes_page=1, correspondence_page=1, viewings_page=1)` | Full details for one listing. Each history has pages of 50 and a total count. |
| `browse(view, page=1, page_size=24)` | Summaries for Rankings, Favorites, Contacted, Viewings or Dismissed. Page size is at most 100. Viewings sorts by UTC time and excludes canceled visits. |
| `state()` | Explicit full export using bulk queries, including unavailable and dismissed listings. It is not the browser polling endpoint. |
| `feedback(id, favorite=True/False, dismissed=True/False)` | Saves supplied flags. Dismissal clears the favorite. Restore with `dismissed=False`. |
| `add_note(text, listing_id=id, role='user')` | Appends a listing note with author `user` or `agent`. |
| `unread_notes()`, `read_note(note_id, reading)`, `note_readings()` | The user's notes without a reading, saving the ranking's reading of one, and every reading by note id. Agent notes are never read. |
| `read_preference(phrase, reading)`, `preference_readings()` | Saves the ranking's reading of a stated preference by its phrase, replacing an earlier one, and returns every reading by phrase. |
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

The fingerprint hashes canonical JSON of the scraped content, excluding id, first_seen and status. An availability change alone does not request another model analysis. A content change retains previous analysis and rank as historical values, but marks them stale. Rankings displays current ranked records first, followed by matching listings awaiting ranking. Saving changed criteria applies their exclusions, and `set_matches_criteria` excludes a single listing, such as a newly found one. Excluded listings are absent from Rankings, Favorites and Dismissed and from detail requests, but remain accessible to the agent for reevaluation. A listing with a contact request or a viewing stays in Contacted and Viewings, and its details open. The store does not decide criteria or compute rankings. The bundled sample file includes rank and reason for seeding, which are not accepted as scraped fields.

The browser checks `/api/revision` every 15 seconds and on window focus. Listing and detail queries do not refetch on focus independently of that revision. It fetches `/api/listings?view=...&page=...` only when the revision changes, on navigation, or on manual refresh. Summaries omit descriptions, amenities and full photo sets. Contacted includes the three latest messages, capped at 500 characters each, with a link to all correspondence. Opening a listing fetches `/api/listings/<id>`. Only the manual Refresh button displays the refresh spinner.

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

## Assets and references

The UI follows the photo grid and note patterns of [Airbnb](https://www.airbnb.com), with primary action color informed by [Planpoint](https://www.planpoint.io). Component behavior follows [Mantine](https://mantine.dev/getting-started/), [TanStack Query](https://tanstack.com/query/latest/docs/framework/react/quick-start) and [Yet Another React Lightbox](https://yet-another-react-lightbox.com/documentation).

The sample photos are distributed under the [Unsplash license](https://unsplash.com/license). They illustrate the sample interface and do not depict the fictional addresses:

- [Living room](https://images.unsplash.com/photo-1600210492486-724fe5c67fb0)
- [Kitchen](https://images.unsplash.com/photo-1484154218962-a197022b5858)
- [Apartment interior](https://images.unsplash.com/photo-1522708323590-d24dbb6b0267)
- [Living area](https://images.unsplash.com/photo-1502672260266-1c1ef2d93688)

Vite emits bundled dependency notices as `web/licenses.txt`. The font license is bundled alongside the photos. Python's [windowed interpreter](https://docs.python.org/3/using/windows.html#python-install-manager) avoids a Windows console. The server uses `pythonw.exe` without console creation flags. Console flags apply only to short-lived console utilities used by the launcher.
