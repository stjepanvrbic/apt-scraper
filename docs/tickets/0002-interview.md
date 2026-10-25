---
title: Preference interview
type: Feature
state: Done
priority: 2
assignee: Stjepan Vrbic
milestone:
parent:
blocked_by: []
related: [0012]
---

## Outcome

The agent interviews the user before the first search and keeps criteria, preferences, listing sites and availability that the user can change by talking to it.

## Acceptance

- [x] A first chat runs the interview and stores criteria, preferences, listing sites and availability.
- [x] Telling the agent a new criterion changes the stored criteria.

## Spec

[Spec › Interview](../spec.md)

## Design

A Hermes skill that drives the interview and writes the result to the listing store's settings.

## Deviations

- The owner added requirements to the brief. The user never has to know the skill exists: `profile/SOUL.md` runs the settings check at the first message of every conversation, the same way as the connection check, and starts or continues the interview when it is incomplete. Changes stated in any conversation, including the gallery's Notes tab, are saved to the store. Changing criteria also applies the store's criteria exclusion. The interview is a conversation led by the model, and the skill gives its structure, what to capture and how to save it.
- The settings have a fixed format that the store validates, so the search, ranking and contact work can rely on it. The interview is complete when every criteria entry, both preference lists, at least one listing site, a time zone and one weekly viewing window are saved. An explicit "no limit" counts as an answer.
- Criteria and their exclusions are saved in one transaction. `update_settings` refuses criteria while listings are stored unless it receives the ids of the listings outside them, so the stored criteria and the hidden listings cannot disagree. The model judges which listings fall outside, because listing size, rooms and addresses are free text.
- Settings always live in the profile's real store. Before, the Notes conversation saved changes into the database the gallery had open, which was the sample store until the search finds real listings. The settings script has no option to choose another store, after a live Notes run used such an option to write into the gallery's own database.
- The agent passes settings to the script in a file it writes with `write_file`. The script accepts only files in the profile's scratch directory, `HERMES_HOME/cache/scratch` (`hermes_constants.py`, `get_scratch_dir`), and deletes a file only after its settings are saved, so a rejected file stays for correction. Hermes 0.22.5 asks for approval of any heredoc into Python (`tools/approval_detection.py`, "script execution via heredoc"), and a shell-escaped apostrophe trips its "command parser limit or malformed executable payload" rule, both found in a live run. `write_file` refuses to overwrite a file the current conversation has not read (`tools/file_tools_write_guards.py`), so a reused path would fail from the second conversation on.
- A Python without a time zone database cannot check the viewing time zone. The store raises `TimeZoneDataMissing` and the script exits 4, apart from exit 1 for invalid input, so the agent reports a broken environment instead of asking the user for another time zone. Windows has no system time zone database, and Hermes's bundled `tools/python-3.14.9` has no `tzdata` (0 time zones), while Hermes's venv pins it (`pyproject.toml`, `tzdata` for win32).
- The Notes adapter no longer passes `skills` to `session.create`. Hermes 0.22.5 never reads that parameter (`tui_gateway/methods_session.py`, `_create_session`). Skills are preloaded only from the TUI environment (`tui_gateway/agent_factory.py`, `_parse_tui_skills_env`).

## Result

The implementation is the single commit on `feat/preference_interview`, with subject "Preference interview". The ticket stays In Progress until its pull request merges.

```
profile/SOUL.md                                  Search settings: status at every first message, interview when incomplete, save every change
profile/skills/interview/SKILL.md                what to capture, how to interview, how to save and change settings
profile/skills/interview/scripts/settings.py     status (exit 0 complete, 3 incomplete), save FILE [--outside ID ...], listings
profile/skills/gallery/scripts/store.py          settings validation, missing_settings, criteria saved with their exclusions, store_path
profile/skills/gallery/scripts/chat.py           Notes context points settings at the interview skill
profile/skills/gallery/SKILL.md, docs/gallery.md, README.md
tests/test_interview.py                          24 tests on the store's settings and the script
```

Verified on this Windows machine with Hermes 0.22.5. The branch's profile was installed with `hermes profile install --force --yes` as `install.ps1` does, keeping `config.yaml` byte for byte (same SHA-256 before and after). Every Hermes session ran with `-Q`. The gallery ran with `open --no-browser`. Nothing opened or focused a browser.

- `python -m unittest discover -s tests`: 99 passed, 0 skipped. `ruff check` on the gallery and interview scripts and the tests passed. Hermes's skill linter reports no findings for `interview`. Its skills guard reports one caution, its generic `os.environ` pattern for reading `HERMES_HOME`, the same finding it reports for `connections.py` and `store.py`. Its scanner passes `SOUL.md`.
- The script under Hermes's bundled Python 3.14.9 (0 time zones) and its venv Python 3.14.9 (598 time zones), each with a temporary `HERMES_HOME`: saving availability exited 4 with the time zone message under the bundled Python and kept the file, and saved under the venv Python. Under both, preferences saved and their file was deleted, `status` exited 3, and a text file in the scratch directory exited 1 and stayed. Pointing `save` at `listings.sqlite3` exited 1 and left the store byte for byte unchanged.
- First chat, "Hi", on a store without settings: the agent ran both status scripts, reported the unconnected accounts and asked for location and budget. Three answers in the same session saved all four settings: two Chicago neighborhoods, up to 2400 USD a month, at least 700 sqft and 2 rooms, move-in 2026-12-01, "Pets allowed (dog)", three likes including "Kid's playground nearby", one dislike, Zillow and Apartments.com, six weekly windows in America/Chicago and a December 20 to January 3 absence. `missing_settings()` then returned `[]`. The agent saved after each topic and its tool calls logged no errors.
- With three fake listings imported (three bedrooms, two bedrooms, rooms not stated), a new conversation said "Change of plans: I need three bedrooms now, not two." The stored `rooms.min` became 3. The two-bedroom listing was hidden, the other two stayed in Rankings, and the agent reported this in one line. It did not raise the completed interview again.
- Notes: a gallery on port 18766 with its own database received "Ground floor is fine after all, I don't mind it. What I'd really like is a balcony." through `/api/chat`. The profile store's dislikes became empty and its likes gained "Balcony (really wanted)".
- An earlier Notes run, with a script that still accepted another store, saved into the gallery's own database. The option was removed before the run above.
- After the live runs, two sentences of the skill were reworded and a single-use helper in the script was inlined, without a change in behavior. The tests above ran on the final code.
- Afterwards the verification store was moved out of the profile and the profile was reinstalled from `main`, with the added `interview` skill directory removed, so the owner's first conversation starts the real interview.

Not verified:

- Which Python the scripts run with. SOUL and the skills call `python`, and Hermes's terminal resolves it from PATH. On this machine the first `python` in a Hermes terminal is Hermes's venv, which has `tzdata`, and the second is its bundled Python, which has none. Nothing guarantees that order on another machine or on macOS. The gallery and account-connections scripts make the same assumption, and this change leaves them as they are.
- macOS. The scripts and skill have no platform-specific code, and the store's time-zone check uses `zoneinfo`.
- An interactive `hermes -p apt-scraper` session in a terminal. All runs were one-shot `-Q` sessions or Notes turns through the gateway.
- Criteria judged against real search results, which need the search.

Found, not fixed here: in Hermes 0.22.5, `session.create` from the Notes adapter returns the same stored session (`local-b562…` on this machine) for every gallery database, with the working folder of its first use. That folder was a deleted test worktree, so every Notes turn failed with "This chat's working folder no longer exists". For the runs above the empty folder was recreated and removed afterwards. A user's first folder is their gallery directory, which persists, but the Notes session is shared between the sample and real galleries. Ticket 0012 (Notes chat shares one Hermes session across gallery databases) tracks it.

Tech debt fixed in the touched code: the store path function moved from `gallery.py` into `store.py`, so every workflow finds the same store. `validate_listing` reuses the shared rental periods. The ignored `skills` argument is gone from the Notes adapter.
