---
title: Contact listing agents
type: Feature
state: In Progress
priority: 2
assignee: agent
milestone:
parent:
blocked_by: [0001, 0005, 0009]
related: []
---

## Outcome

A contact request starts and continues correspondence with the listing agent by email, iMessage on macOS or the site's contact form, and the conversation shows in Contacted.

## Acceptance

- [ ] Within ten minutes of a contact request on a test listing, a first message is in Gmail Sent or Messages, or submitted through the listing's contact form.
- [ ] Replies are answered and the thread shows in Contacted.
- [ ] A test request for the user's date of birth reaches the user through the messaging gateway, and nothing is sent until the user answers.

## Spec

[Spec › Contact and viewings](../spec.md)

## Design

A `contact` skill with `scripts/contact.py`, the only way the agent corresponds with listing agents and asks the user about a listing. Every send runs `connections.py require <channel>` first and sends nothing unless it exits 0. Email goes through the Hermes `google-workspace` skill's `google_api.py` (`gmail send`, `gmail reply`, `gmail search`, `gmail get`), texts through `imsg`, questions to the user through `hermes send` to the gateway platform that passed its check. Contact forms are filled with Hermes's browser tools and recorded after submission. Message bodies come from files in the profile's scratch directory, as in the interview.

A Hermes cron job shipped in `profile/cron/jobs.json` runs every 2 minutes in the gateway. Its pre-run script polls Gmail and iMessage for replies on active contacts and records them in the store, then wakes the agent only when a request is pending or a reply is unanswered, and no question to the user is open for that listing. Idle ticks make no model call. The installer resumes the job, because Hermes installs shipped jobs paused.

The store gains questions to the user, a handled marker per contact, the first email's subject and agreed viewings. While a question is open on a listing, `contact.py` refuses to send to its listing agent. An answer with information to share records the approval for that listing and purpose. An agreed time is checked against the saved availability and the calendar, then stored as an agreed viewing without a calendar event, with the listing's address, links and agent contact already in the store for the calendar booking.

## Deviations

- Correspondence runs as a Hermes cron job the profile ships in `cron/jobs.json`, every 2 minutes, with a pre-run script in `profile/scripts/contact-due.py`. Hermes runs pre-run scripts only from the profile's `scripts/` directory and installs shipped jobs paused (`hermes_cli/profile_distribution.py`, `_merge_cron_store`, and `cron/job_definition.py`, `import_job_definitions`), so both installers now run `hermes -p apt-scraper cron resume apt-scraper-contact`. A script whose last line is `{"wakeAgent": false}` skips the model run (`cron/scheduler.py` and `cron/scheduler_prompt.py`, `_parse_wake_gate`), so idle ticks cost no model call. Cron runs in the gateway (Hermes docs, `user-guide/features/cron.md`, "How it works"), the same running gateway the user's replies need.
- The job delivers its final response locally. Inside a cron run, `hermes send` to the job's own delivery target is skipped as a duplicate (`tools/send_message_tool.py`, `_maybe_skip_cron_duplicate_send`), and cron delivery would bypass `require gateway`. Questions and notices to the user go through `contact.py ask` and `tell`, which run `require gateway` and treat a skipped or failed delivery as an error. Failure notices of the job itself go to the home channel (`failure_deliver: origin`), which Hermes sends once per distinct error (`cron.md`, "Failure incidents").
- A failed reply poll does not wake the model. The pre-run script tells the user through the gateway once per distinct error and stays quiet while the error repeats.
- The user's answer is recorded in whichever conversation it arrives, and the cron job sends the follow-up within minutes, so each listing has one writer. `hermes send` mirrors the question into the gateway session (`gateway/mirror.py`), and the open questions in the store let any session match the answer. An answer makes its listing due, and `work` hands it to the cron job until the next message to that listing agent or `done`, so a declined request, other wording or a confirmed time reaches the follow-up.
- While a question is open on a listing, `contact.py send` refuses to email or text its listing agent. A contact form has no `require` channel and no script step before submission, so for forms the hold rests on the `due` filter, which leaves the listing out of `work`, and on the skill's instructions. An answer with `share` records the approval for that listing and purpose in the same transaction.
- When `send` or `ask` exits 3, the script marks the contact failed with the connection's reason, and for `ask` with the question, so the gallery shows it with Retry and the listing stops waking the model. A later send that works clears the failure. Retry makes an unanswered reply due again. A `tell` that exits 3 is lost, and the gallery still shows the contact's state.
- Agreeing a time checks it before the hand-off to the calendar work. A one-hour viewing must fit the saved availability, and the calendar must have no timed event then (`google_api.py calendar list`). Otherwise the user is asked. A time within an hour of another agreed or booked viewing is refused in the store's transaction, also when the user confirmed it, because agreed viewings have no calendar event until the booking. The agreed time is stored as a viewing with status `agreed` and no event. Moving a booked viewing sets it back to `agreed` with its event identifier kept. The gallery shows agreed viewings in the listing details, and Viewings still shows booked ones only.
- The store moves to schema version 2, with questions to the user, a handled marker and the first email's subject per contact. Version-1 stores migrate on open.
- Contact forms are filled with Hermes's headless browser tools. Computer use needs an approver, and a cron run has none (`tools/approval.py`, cron approval context).
- Email replies are found by a Gmail search for messages not from the user, from the agent's address or with the first email's subject, since the request date. A message is filed under the listing that last wrote in its Gmail thread. A message outside those threads is filed only when exactly one open contact uses the sender's address. Texts are filed the same way by iMessage chat and phone number. A reply from an agency address or number shared by several listings, outside a known thread, is not filed, and neither is a reply to a contact form from an address the store does not know.

## Result

Delivered in the single commit on `feat/contact_listing_agents` with the ticket's title, in its pull request. The ticket stays In Progress until the owner verifies with connected accounts and the pull request merges.

```
profile/skills/contact/SKILL.md            how to start, reply, ask, agree times and fill forms
profile/skills/contact/scripts/contact.py  work, send, submitted, ask, tell, questions, answer, agree, cancel, done, close, fail, due
profile/scripts/contact-due.py             cron pre-run gate, hands over to contact.py due
profile/cron/jobs.json                     "Listing correspondence", every 2m, local delivery
profile/SOUL.md                            listing agents only through the contact script, answers recorded from gateway messages
profile/skills/gallery/scripts/store.py    schema 2 records, due contacts, questions, agreed viewings
profile/skills/gallery/scripts/scratch.py  scratch-file reader shared with the interview script
install.ps1, install.sh                    resume the shipped job
gallery/src                                agreed viewings, correspondence hint, sample listings never contacted
tests/test_contact.py, tests/test_contact_store.py
```

Verified on this Windows machine with Hermes 0.22.5. The live runs below used the first revision. The review fixes change `contact.py`, the store and the skill text, and were verified by the tests only:

- `python -m unittest discover -s tests`: 156 passed, 0 skipped. `ruff check` on the changed scripts and tests passed. The contact tests run `contact.py` and the cron gate as processes against stand-in `google_api.py`, `imsg` and `hermes`, and the real `connections.py` with stored results. They cover the first email, threaded replies, follow-ups, texts with message GUIDs, contact forms, transport failures, scratch-file checks, questions holding every email and text, failures recorded on exit 3, answers that reach the cron job (shared, declined, a confirmed time), overlapping viewings, availability and calendar checks, user-confirmed times, cancellation, reply polling for Gmail and iMessage with shared addresses and numbers, a crashing connection check, skipped polling of disconnected channels, and one notice per polling error.
- Gallery: ESLint, TypeScript and the Vite build passed. Headless Playwright: 15 passed, 0 skipped, including an agreed viewing shown in the details and absent from Viewings, with a screenshot.
- Hermes's skill linter reports no findings for `contact`. Its prompt scanner passes `SOUL.md`, the skill and the job prompt. Its skills guard reports the same class of findings as for `connections.py`, which are `HERMES_HOME` reads and subprocess calls.
- Live, with the branch profile installed into `apt-scraper` the way the installer does (`config.yaml` byte-identical before and after), the job arrived paused, `scripts/` was installed, and `cron resume` exited 0. The running gateway fired the job on schedule. With no contact work, the run document said "Script gate returned `wakeAgent=false` — agent skipped".
- Live refusal: `contact.py send` for email exited 3 with "Do not use Gmail: not connected. No Google account is connected.", and `ask` exited 3 with "Do not use the messaging gateway: not connected. No messaging platform is set up." Nothing was sent or recorded, and the input files stayed.
- Live unattended run: a contact request on a test listing with an `example.com` agent address woke the agent on the next tick. It loaded the `contact` skill, ran `work`, got exit 3 from `require gmail`, and marked the contact failed with "Gmail is not connected, and the listing has no other contact." Nothing was sent. The next tick did not wake the model. An earlier run on a listing with an `example.invalid` address marked it failed as a placeholder without trying a channel.
- Afterwards the job was removed and the profile reinstalled from `main`, with `scripts/`, `skills/contact`, the test store, the poll state and the empty job store removed. The cron runs left their sessions in the profile's session list. No browser was opened. Hermes sessions ran with `-Q` or in cron.

Not verified, so the acceptance items stay open. Each needs the owner's connected Google and Telegram accounts, which this machine does not have:

1. Connect the accounts: run `hermes -p apt-scraper`, accept the connection setup, and confirm `check` reports Gmail, Google Calendar and the gateway as connected.
2. Run the installer again, then confirm `hermes -p apt-scraper cron list` shows "Listing correspondence" as active.
3. Save your viewing availability, and import a test listing whose agent email is a second mailbox you control. In PowerShell: `$env:HERMES_HOME = "$env:LOCALAPPDATA\hermes\profiles\apt-scraper"; python profile/skills/gallery/scripts/gallery.py import test-listing.json`.
4. In the gallery, press Request contact on it. Within ten minutes the first email is in Gmail's Sent folder.
5. From the second mailbox, reply proposing a time inside your availability. Within a few minutes the agent's answer is sent, and Contacted shows the thread.
6. Reply asking for your date of birth. The question arrives in the Telegram chat, and no email goes to the second mailbox until you answer there.
7. On a Mac, repeat steps 3 to 5 with a phone number you control instead of an email address, to check texting.

Tech debt fixed in the touched code: the interview script's scratch-file reading moved into `scratch.py`, shared with the contact script, and `book_viewing`'s time parsing became `viewing_time`, shared with `agree_viewing`. The BOM at the start of `schema.py` is gone. External commands run through the connection script's `Runner`, which kills the whole process tree on a timeout.
