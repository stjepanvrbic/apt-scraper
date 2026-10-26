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
- [ ] Delivered as a pull request to `main`, left unmerged for the owner.

## Spec

[Spec › Gallery](../spec.md)

## Design

Found while verifying the interview: in Hermes 0.22.5 the gallery's `session.create` returns the same stored session for every gallery database and keeps the working folder from its first use, so Notes turns fail once that folder is gone.

Cause, from Hermes 0.22.5 (`bf0203a794`): `SessionControls.create` in `gateway/session_controls.py` treats a `title` as "open or create": `resolve_titled_session` in `gateway/session_local_title.py` returns the session that already holds the title, and a new session is created only when none does. Titles are unique (`title_new_session`). The gallery created every Notes session with `title='Apartment preferences'`, so every database after the first got the first database's session. That session's policy froze the `cwd` it was created with, the first database's folder (`build_policy` in `gateway/session_policy.py`), and `policy_scope` refuses every turn with `workspace_unavailable` once that folder is gone.

Fix: `session.create` without a title, so the session id saved in each database is that database's only link to its session, and with the profile home as `cwd`, which exists as long as the profile does. Schema migration 10 drops the saved session id of existing databases, which may share the titled session, so each starts its own on its next turn and keeps its messages. A reply still running at that point belongs to the dropped session, so the migration ends it with an error asking the user to send the message again.

## Deviations

- The live check installed `./profile` under the throwaway name `apt-0012`, and its harness replaced `Path` in `chat.py` with a subclass whose `name` is `apt-scraper`, because `run_turn` refuses any other profile name. The rest of `run_turn`, the gateway client and the gateway were Hermes's own.

## Result

Each database creates its own untitled Notes session in the profile home, and a store migration (version 10) makes existing databases drop a saved session they may share. Sessions created before this change keep their old title and folder in Hermes. The gallery no longer resumes them, so their earlier context does not carry into the new session. The gallery's own message history is kept.

Verified on Windows 11 with Hermes 0.22.5, on this change over `main` at `9136e65`:

- Live, in a throwaway Hermes root (`HERMES_HOME=%TEMP%\apt-0012-root`, profile installed from `./profile` as `apt-0012`, the subscription provider plugin, its own gateway, stopped afterwards; the owner's `gateway-starts.log` stayed at 15 lines). One Notes turn ("Reply with the single word OK") through `run_turn` in a sample database in folder `first` and a real database in folder `other`, then the sample database copied to `moved` and `first` deleted, then a turn in each.
  - On `main`: both databases got session `local-80c88b35…`, and both turns after deleting `first` failed with `gateway.errors.workspace_unavailable`. Hermes's `state.db` holds that session as `Apartment preferences` with `cwd` `…\first`.
  - With the fix: sessions `local-21a6bed3…` and `local-d342afa9…`, all four turns complete with `OK`, and both sessions' `cwd` is the profile home.
  - The two databases `main` left at schema 9 holding `local-80c88b35…` migrated to version 10 and each completed a turn in a new session of its own (`local-ffd85f9e…`, `local-16b9602d…`), with all earlier messages kept.
- `tests/test_hermes_adapter.py`: a gateway double that resumes a titled session and refuses turns in a missing folder. It fails on `main` (same session for both databases) and, with only the title removed, still fails (`workspace_unavailable` after the folder is deleted).
- `tests/test_gallery_migrations.py`: a version-9 database with a saved session and a running reply migrates with no session, its messages, and the reply closed with that error.
- `python -m unittest discover -s tests`: 528 passed, 0 skipped. `ruff check profile tests`: passed. `npm run lint`, `npm run build` (built assets unchanged), `npx playwright test` (headless): 38 passed.
