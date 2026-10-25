"""Reads and saves the search settings the interview collects, in the profile's real listing store.

Commands:
  settings.py status                           print the settings and what the interview still needs
  settings.py save FILE [--outside [ID ...]]   replace each setting named in the JSON object in FILE,
                                               a file in the scratch directory, and delete FILE once saved
  settings.py listings                         print the facts of every stored listing, to judge criteria

Exit codes: 0 done (for status: the interview is complete), 3 the interview is incomplete,
1 the input or the store was rejected and nothing was saved, 2 usage error,
4 this Python has no time zone database and nothing was saved.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gallery' / 'scripts'))

from scratch import read_scratch_object  # noqa: E402
from store import SETTINGS, ListingStore, TimeZoneDataMissing, store_path  # noqa: E402

INCOMPLETE = 3
NO_TIME_ZONE_DATA = 4
LISTING_FACTS = ('id', 'title', 'price', 'price_note', 'currency', 'rental_period', 'size', 'rooms', 'address',
                 'description', 'amenities', 'status', 'matches_criteria')


def main():
    parser = argparse.ArgumentParser(description='Read and save the apartment search settings.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    saving = commands.add_parser('save')
    saving.add_argument('file', type=Path, help='JSON object with any of criteria, preferences, listing_sites, viewing_availability, '
                        'in the scratch directory. Deleted once saved.')
    saving.add_argument('--outside', nargs='*', metavar='ID', help='With criteria: ids of stored listings outside them.')
    commands.add_parser('listings')
    arguments = parser.parse_args()
    store = ListingStore(store_path())
    if arguments.command == 'status':
        missing = store.missing_settings()
        print(json.dumps({'complete': not missing, 'missing': missing, 'settings': store.settings()}))
        return INCOMPLETE if missing else 0
    if arguments.command == 'listings':
        listings = sorted(store.state()['listings'], key=lambda item: item['id'])
        print(json.dumps([{key: item.get(key) for key in LISTING_FACTS} for item in listings]))
        return 0
    file, settings = read_scratch_object(arguments.file, 'Settings')
    if not settings or not set(settings) <= set(SETTINGS):
        raise ValueError(f'Settings must be a JSON object with any of {", ".join(SETTINGS)}.')
    if 'criteria' in settings and arguments.outside is None and store.has_listings():
        raise ValueError('Stored listings must be judged against the new criteria. Run listings, then save again with '
                         '--outside and the ids of the listings outside them, or --outside alone when none are.')
    exclusions = store.update_settings(**settings, outside=arguments.outside)
    missing = store.missing_settings()
    result = {'saved': sorted(settings), 'complete': not missing, 'missing': missing}
    if exclusions is not None:
        result['criteria'] = exclusions
    file.unlink()
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except TimeZoneDataMissing as error:
        print(error, file=sys.stderr)
        sys.exit(NO_TIME_ZONE_DATA)
    except (ValueError, KeyError, OSError) as error:
        print(error.args[0] if isinstance(error, KeyError) else error, file=sys.stderr)
        sys.exit(1)
