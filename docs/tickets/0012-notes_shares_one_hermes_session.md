---
title: Notes chat shares one Hermes session across gallery databases
type: Bug
state: In Progress
priority: 3
assignee: agent
milestone:
parent:
blocked_by: []
related: [0001, 0002]
---

## Outcome

Each gallery database has its own Hermes session for the Notes chat, and a Notes turn works regardless of which working folder the session was first created in.

## Acceptance

- [x] The sample database and the profile's real database get different Hermes sessions.
- [x] A Notes turn succeeds after the folder the session was first created in no longer exists.
- [x] A gallery server started before the update cannot make a migrated database resume the shared session.
- [x] Delivered as a pull request to `main`, left unmerged for the owner.

## Spec

[Spec › Gallery](../spec.md)

## Design

Found while verifying the interview: in Hermes 0.22.5 the gallery's `session.create` returns the same stored session for every gallery database and keeps the working folder from its first use, so Notes turns fail once that folder is gone.

Cause, from Hermes 0.22.5 (`bf0203a794`): `SessionControls.create` in `gateway/session_controls.py` treats a `title` as "open or create": `resolve_titled_session` in `gateway/session_local_title.py` returns the session that already holds the title, and a new session is created only when none does. Titles are unique (`title_new_session`). The gallery created every Notes session with `title='Apartment preferences'`, so every database after the first got the first database's session. That session's policy froze the `cwd` it was created with, the first database's folder (`build_policy` in `gateway/session_policy.py`), and `policy_scope` refuses every turn with `workspace_unavailable` once that folder is gone.

Fix: `session.create` without a title, so the session id saved in each database is that database's only link to its session, and with the profile home as `cwd`, which exists as long as the profile does. Schema migration 10 adds a `notes_session` table, empty, which the gallery reads and writes instead of `chat_session`, so each existing database starts its own session on its next turn and keeps its messages. A reply still running at that point belongs to the shared session, so the migration ends it with an error asking the user to send the message again.

Neither installer stops a running gallery server, so one started before the update keeps `main`'s code while scripts run by its Notes agent migrate the same database. That server writes the shared session back to `chat_session` when its turn finishes, and recreates the titled session on its next turn. Migration 10 therefore leaves `chat_session` in place and the new code never touches it. Clearing `chat_session.session_id` instead, as the first version of this change did, let the stale server's write land after the migration and stick, because the migration never runs again.

`ListingStore.connect` also checks the schema version on every connection, not only when the store is created, and raises when it differs from the code's, so a store whose database a newer process migrated fails instead of writing in a schema it does not know. That protects only from the next migration on, because a server already running `main` does not have the check. For `connect(write=True)` the check runs inside the `BEGIN IMMEDIATE` transaction. Connections that begin their own transaction check just before it, which leaves a window of a few statements.

## Deviations

- The live check installed `./profile` under the throwaway name `apt-0012`, and its harness replaced `Path` in `chat.py` with a subclass whose `name` is `apt-scraper`, because `run_turn` refuses any other profile name. The rest of `run_turn`, the gateway client and the gateway were Hermes's own.

## Result

Each database creates its own untitled Notes session in the profile home, and a store migration (version 10) moves the saved session to a new `notes_session` table, starting empty, so existing databases stop using a session they may share. Sessions created before this change keep their old title and folder in Hermes. The gallery no longer resumes them, so their earlier context does not carry into the new session. The gallery's own message history is kept.

Verified on Windows 11 with Hermes 0.22.5, on this change over `main` at `9136e65`:

- Live, in a throwaway Hermes root (`HERMES_HOME=%TEMP%\apt-0012-root`, profile installed from `./profile` as `apt-0012`, the subscription provider plugin, its own gateway, stopped afterwards; the owner's `gateway-starts.log` stayed at 15 lines). One Notes turn ("Reply with the single word OK") through `run_turn` in a sample database in folder `first` and a real database in folder `other`, then the sample database copied to `moved` and `first` deleted, then a turn in each.
  - On `main`: both databases got session `local-80c88b35…`, and both turns after deleting `first` failed with `gateway.errors.workspace_unavailable`. Hermes's `state.db` holds that session as `Apartment preferences` with `cwd` `…\first`.
  - With the fix: sessions `local-21a6bed3…` and `local-d342afa9…`, all four turns complete with `OK`, and both sessions' `cwd` is the profile home.
  - The two databases `main` left at schema 9 holding `local-80c88b35…` migrated to version 10 and each completed a turn in a new session of its own (`local-ffd85f9e…`, `local-16b9602d…`), with all earlier messages kept.
- `tests/test_hermes_adapter.py`: a gateway double that resumes a titled session and refuses turns in a missing folder. It fails on `main` (same session for both databases) and, with only the title removed, still fails (`workspace_unavailable` after the folder is deleted).
- `tests/test_gallery_migrations.py`: a version-9 database with a saved session and a running reply migrates with no session, its messages, and the reply closed with that error.
- Code review of the branch: three low findings. The ticket's control characters and a reply left running at migration time were fixed. Using the profile's `workspace` instead of the profile home was withdrawn, because Hermes never recreates `workspace` and the gallery recreates it only for its default database path.
- `python -m unittest discover -s tests`: 528 passed, 0 skipped. `ruff check profile tests`: passed. `npm run lint`, `npm run build` (built assets unchanged), `npx playwright test` (headless): 38 passed.

A review found that a gallery server started before the update could write the shared session back after migration 10 had cleared `chat_session.session_id`, and the database stayed at version 10 with the shared session. The session now lives in `notes_session`, which `main`'s code never writes, and every store connection checks the schema version. Verified on this change over `main` at `1b19404`:

- Reproduction with `main`'s real store code (`git archive origin/main`) and the new code in separate processes on one database: `main` saves `local-shared-titled` at version 9, the new code migrates to 10, `main`'s `update_chat(..., session_id='local-shared-titled')` writes it back, then the new code reads the session. At the previous head `01a0cf6` it reads `10 local-shared-titled`. With this change it reads `10 None`.
- `tests/test_hermes_adapter.py`: a version-9 database holding the shared session migrates, a simulated `main` write puts the shared session in `chat_session` before and after each of two Notes turns, and the turns create one session and then resume it, never the shared one.
- `tests/test_gallery_migrations.py`: a store ignores `chat_session` writes before and after it saves its own session. A store whose database another process moves to a later version refuses `update_chat`, `start_chat`, `upsert_listing` and `chat_state`, and leaves the file unchanged.
- Two migration tests wrote fixtures into an unmigrated database through the current store class, which the check now refuses. `test_rerank.py` writes them with SQL, and `test_implicit_signals.py` runs its version-7 store with the version-7 migration list.
- `python -m unittest discover -s tests`: 531 passed, 0 skipped. `ruff check profile tests`: passed. `npm run lint`, `npm run build` (built assets unchanged), `npx playwright test` (headless): 38 passed.
