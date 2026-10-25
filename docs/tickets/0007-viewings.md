---
title: Book viewings on Google Calendar
type: Feature
state: Done
priority: 2
assignee: Stjepan Vrbic
milestone:
parent:
blocked_by: [0006]
related: []
---

## Outcome

A viewing agreed in correspondence becomes a Google Calendar event within the user's availability and shows in Viewings.

## Acceptance

- [ ] A confirmed time becomes an event with the address, listing link and listing agent contact.
- [ ] A moved or canceled viewing updates or removes the event.
- [ ] No viewing is booked over an existing event.

## Spec

[Spec › Contact and viewings](../spec.md)

## Design

Booking is part of the contact script, so it happens in the correspondence flow without the user asking and without a model call. `contact.py` brings the calendar up to date with the store at the end of `agree` and `cancel`, and again in the cron job's pre-run step every 2 minutes, until each write works. Agreed viewings get an event or an update to their event, and canceled viewings with an event lose it. Every write runs `connections.py require calendar` first and goes ahead only on exit 0.

The event goes on the primary calendar. Its summary names the address, its location is the address, and its description holds the listing title and address, every source link, the listing agent's name, email, phone and contact form, and the meeting details from the correspondence. It lasts the store's one-hour viewing length and uses the time zone of the saved availability.

Each viewing's write stands alone: one that fails leaves that viewing agreed, or canceled with its event identifier kept, and the others still run. Only a calendar that is not connected fails every remaining write unsent. The cron job retries failed writes and tells the user once per distinct error through the gateway, naming the viewings it holds up, with the same once-only notices as the reply polling. `agree` and `cancel` report their own viewing's error in their output and never fail after they have committed. A new viewing's event identifier is its UUID without hyphens, so a create retried after a lost response updates the event it already made, and a cancel keeps that identifier for a viewing without a recorded event, so an event whose creation answer was lost is removed too. Every write sends `"status": "confirmed"`, so an update restores an event the user deleted.

The contact skill is the only way to create, move or cancel a viewing. The gallery's `viewing` command and the store's `book_viewing` and `remove_viewing` are gone.

## Deviations

- Calendar writes call the Calendar API through the google-workspace connector's `build_service`, in a new script `viewing_calendar.py`, not through the connector's command line. In Hermes 0.22.5, `google_api.py calendar` has only `list`, `create` and `delete` (source of `skills/productivity/google-workspace/scripts/google_api.py` and its SKILL.md), and a moved viewing must update its event, which needs `events.patch`. The connector's command line also cannot set an event identifier. The Calendar API reference says a client may choose an event's `id` (base32hex characters, 5 to 1024 long, unique per calendar), patch replaces only the fields sent, an existing identifier answers 409 "duplicate" with the advice to update instead, and deleting a deleted event answers 410 (Events resource, `events.patch`, and the "Handle API errors" guide on developers.google.com). The script patches the recorded event when there is one, and creates the event under the viewing's identifier when there is none or the patch answers 404, as after the event was purged or a different Google account was connected. A create that answers 409 patches instead. A delete that answers 404 or 410 counts as removed. The Events resource documents `cancelled` as the status of a deleted event, and every write sends `confirmed`. The Google client libraries the script imports are Hermes's own `google` extra, which the connector's setup requires.
- The calendar check in `agree` now leaves out events that belong to agreed or booked viewings. The store's one-hour overlap rule decides about those and answers "propose another time". Without this, the booked event of another listing made the agent ask the user instead, and a moved viewing clashed with its own event.
- A canceled viewing can no longer be moved with `agree`. The agent agrees a new viewing instead, so an event identifier is never reused after its event was deleted.
- The manual booking path is removed: the gallery skill's instruction to record bookings with `viewing --event-id`, the `gallery.py viewing` command, and the store's `book_viewing` and `remove_viewing`. They booked or moved a viewing with no status check, which revived canceled viewings, replaced an event identifier still waiting for its delete, skipped the one-hour overlap rule, and moved viewings the calendar never heard of. `remove_viewing` deleted a row whose event then stayed in the calendar. Tests that used them as fixtures now go through `agree_viewing` and `record_booking`.
- A write retried by the cron job does not check the calendar again. The check runs in `agree`, right before the first write. A retry follows only a failed write, minutes later, or a time the user confirmed while the calendar was not connected, which skips the check as before.
- No schema change. The existing `status` and `calendar_event_id` columns hold every state: `agreed` waits for a write, `booked` has its event, and `canceled` with an event identifier waits for the delete, after which the identifier is cleared.
- The poll error notices became general background notices, and their state file moved from `apt-scraper/contact-poll.json` to `apt-scraper/contact-notices.json`. A `contact-poll.json` left by the earlier version stays on disk unread, and an error it held is reported once more.

## Result

Delivered in the single commit on `feat/calendar_viewings` with the ticket's title, in its pull request. The ticket stays In Progress until the owner verifies with a connected Google Calendar and the pull request merges.

```
profile/skills/contact/scripts/viewing_calendar.py  save and delete events through the connector's credentials
profile/skills/contact/scripts/contact.py           event content, per-viewing calendar sync after agree and cancel and in due
profile/skills/contact/SKILL.md                     agree books and moves, cancel removes, canceled viewings are not moved
profile/skills/gallery/scripts/store.py             booking and removal records, cancel keeps the possible event, manual booking removed
profile/skills/gallery/scripts/gallery.py           viewing command removed
profile/skills/gallery/SKILL.md                     manual booking instruction removed
docs/gallery.md, README.md                          booking described
tests/                                              stand-in Calendar API, booking tests, fixtures through agree_viewing and record_booking
gallery/tests/gallery.spec.ts                       bookings recorded through record_booking, shown in Viewings with their time
```

Verified on this Windows machine with Hermes 0.22.5:

- `python -m unittest discover -s tests`: 177 passed, 0 skipped. `ruff check` on the contact and gallery scripts and the tests passed. The contact tests run `contact.py` and the cron gate as processes against a stand-in connector whose `build_service` keeps events in a file, with Google's status codes for a missing, duplicate or deleted event. They cover an agreed time becoming an event with the address, source link, agent name, email and phone, and details, in the user's time zone; a move patching the same event, also one booked under another identifier; a move to a time overlapping the viewing's own event; a move restoring an event the user deleted, and a retried create doing the same; a move whose event is gone from the calendar getting a new event; cancel deleting the event and clearing its identifier; cancel of a viewing whose create answer was lost deleting that event, and of a viewing that never got one counting the missing event as removed; an event already gone; one viewing's write failing with 403 while a new booking and a delete in the same run go through, with one notice naming that viewing; a store error after `agree` committed, reported as `calendar_error` instead of failing the command; a canceled viewing refused as a move; a disconnected calendar writing nothing, telling the user once and booking on the first tick after it passes; a canceled viewing's event kept until the calendar passes; a failed write retried as one event; a calendar event or another agreed or booked viewing at that time refusing the time with no write; and the races where a viewing moves or is canceled during a write.
- `viewing_calendar.py` against Hermes's real `googleapiclient` 2.194.0 and its bundled Calendar discovery document, with a mocked HTTP sequence and no network: a new event is `POST /calendars/primary/events` with the chosen `id` and `"status": "confirmed"`, a 409 is followed by `PATCH /calendars/primary/events/{id}`, a move is a `PATCH`, a 404 on that `PATCH` is followed by the `POST`, a 403 is raised, and a 404 or 410 on `DELETE` counts as removed. Importing the real 0.22.5 connector module with no token stops with the connector's "Not authenticated" message.
- Gallery: ESLint and TypeScript passed. Headless Playwright: 15 passed, 0 skipped. The agreed-viewing test now records the booking and checks that Viewings shows it with "Sat, Oct 31, 11:00 AM CDT" and the listing details say "Booked viewing", with a screenshot.
- Hermes's skill linter reports no findings for `contact`. For `gallery` it reports 6 warnings about frontmatter and sections, all present on `main`, which has 7. The skills guard reports only `HERMES_HOME` reads for `contact`, the same class accepted for the contact and connection scripts.
- Live refusal, with the branch scripts on the real profile `apt-scraper`, which has no Google account: `connections.py require calendar` exited 3 with "Do not use Google Calendar: not connected. No Google account is connected." `agree` without confirmation exited 3 and stored nothing. `agree` with `user_confirmed` stored the viewing as agreed and returned `"booked": false` with the same refusal as `calendar_error`, which shows that the write was never attempted: the connector itself would have answered "Not authenticated". `cancel` kept the viewing's possible event identifier, wrote nothing and returned the same refusal. `due` named the viewing in its notice, which `require gateway` refused, because no gateway is set up. The test store was removed afterwards, and `connections.json` was unchanged. No browser was opened and no Hermes session was started.

Not verified, so all three acceptance items stay open. Each needs the owner's connected Google Calendar, which this machine does not have. To finish, after the contact ticket's steps 1 to 3 (connect the accounts, re-run the installer, import a test listing whose agent email is a second mailbox you control):

1. Request contact on the test listing. From the second mailbox, propose a time inside your availability when your calendar is free. Within a few minutes the agent confirms it, and an event "Apartment viewing: <address>" is in your Google Calendar at that time with the address as location and the listing link, the agent's name and email and the meeting details in the description. Viewings in the gallery shows it with its time.
2. From the second mailbox, move it to another free time. The same event moves, and no second event appears.
3. Put an event of your own in your calendar at another time inside your availability, then propose that time from the second mailbox. No viewing is booked. The agent asks you in Telegram whether the time works.
4. From the second mailbox, cancel the viewing. The event leaves your calendar, and the viewing leaves Viewings.

Tech debt fixed in the touched code: the calendar check no longer counts the viewings' own events as clashes; no store method moves or revives a canceled viewing any more, because `agree_viewing` refuses one and `book_viewing` is gone; and the poll notice code became one notice function for every background step.
