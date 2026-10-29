"""Writes viewing events to the user's primary Google Calendar with the google-workspace connector's credentials.

The connector's command line can list, create and delete events but cannot change one, and a moved viewing must
update its event, so this script calls the Calendar API through the connector's `build_service`. Events are created
with an identifier the caller chooses, so a write retried after a lost response updates the same event instead of
adding a second one (Calendar API, Events resource `id`, and the 409 "duplicate" error).

Commands:
  viewing_calendar.py save FILE       FILE holds {"event_id", "new_id", "event"}: update the event `event_id`, or when
                                      it is null or Google no longer has it, create the event `new_id`;
                                      prints {"id", "htmlLink"}
  viewing_calendar.py delete EVENT    remove the event; one that is already gone counts as removed

The event body carries `"status": "confirmed"`, so an update also restores an event the user deleted, which Google
keeps as `cancelled` until it is purged.
"""

import json
import os
import sys
from pathlib import Path
from typing import TypedDict, cast

sys.path.insert(0, str(Path(os.environ['HERMES_HOME']) / 'skills' / 'productivity' / 'google-workspace' / 'scripts'))

from google_api import build_service  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402

CALENDAR = 'primary'
DUPLICATE = 409
NOT_FOUND = 404
GONE = 410


class SaveRequest(TypedDict):
    """The file `save` reads, which contact.py writes."""
    event_id: str | None
    new_id: str
    event: dict[str, object]


class Deletion(TypedDict):
    deleted: str
    already_gone: bool


def save(event_id: str | None, new_id: str, event: dict[str, object]) -> dict[str, object]:
    events = build_service('calendar', 'v3').events()
    if event_id:
        try:
            return events.patch(calendarId=CALENDAR, eventId=event_id, body=event).execute()
        except HttpError as error:
            if error.status_code != NOT_FOUND:
                raise
    try:
        return events.insert(calendarId=CALENDAR, body={**event, 'id': new_id}).execute()
    except HttpError as error:
        if error.status_code != DUPLICATE:
            raise
    return events.patch(calendarId=CALENDAR, eventId=new_id, body=event).execute()


def delete(event_id: str) -> Deletion:
    try:
        _ = build_service('calendar', 'v3').events().delete(calendarId=CALENDAR, eventId=event_id).execute()
    except HttpError as error:
        if error.status_code not in (NOT_FOUND, GONE):
            raise
        return {'deleted': event_id, 'already_gone': True}
    return {'deleted': event_id, 'already_gone': False}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ('save', 'delete'):
        print(__doc__, file=sys.stderr)
        return 2
    if argv[0] == 'delete':
        print(json.dumps(delete(argv[1])))
        return 0
    value = cast(SaveRequest, json.loads(Path(argv[1]).read_text(encoding='utf-8')))
    saved = save(value['event_id'], value['new_id'], value['event'])
    print(json.dumps({'id': saved['id'], 'htmlLink': saved.get('htmlLink')}))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except HttpError as error:
        print(f'Google Calendar answered {error.status_code}: {error.reason}', file=sys.stderr)
        sys.exit(1)
