"""Reads and saves the search settings the interview collects, in the profile's real listing store.

Commands:
  settings.py status      print the settings, what the interview still needs and the saved places without
                          coordinates
  settings.py save FILE   replace each setting named in the JSON object in FILE, a file in the scratch
                          directory, and delete FILE once saved. Saved criteria are applied to every
                          stored listing in the same transaction, from its stored features: listings
                          outside them, and listings still waiting for a model judgment, are hidden. It
                          prints the totals and how many listings this change hid and showed again.
                          Each place address without stored coordinates is geocoded first, with
                          OpenStreetMap's Nominatim, one request a second.

Exit codes: 0 done (for status: the interview is complete and every place has coordinates), 3 the interview is
incomplete or a place has no coordinates,
1 the input or the store was rejected and nothing was saved, 2 usage error,
4 this Python has no time zone database and nothing was saved,
5 Nominatim found no location for a place and nothing was saved,
6 Nominatim could not be reached and nothing was saved.
"""

import argparse
import json
import sys
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(SKILLS / 'gallery' / 'scripts'), str(SKILLS / 'search' / 'scripts')]

from geodata import ATTRIBUTION, SourceUnavailable, geocode  # noqa: E402
from matching import INSIDE, OUTSIDE, PENDING, evaluate  # noqa: E402
from scratch import read_scratch_object  # noqa: E402
from store import SETTINGS, ListingStore, TimeZoneDataMissing, store_path  # noqa: E402

INCOMPLETE = 3
NO_TIME_ZONE_DATA = 4
PLACE_NOT_FOUND = 5
GEOCODING_UNAVAILABLE = 6


class PlacesNotFound(Exception):
    pass


def locate(store, places):
    """The location of each place, {name, address, found}, geocoding the addresses the store has no coordinates for and
    keeping what is found. Raises PlacesNotFound naming the places the geocoder found nothing for."""
    SETTINGS['places'](places)
    addresses = list(dict.fromkeys(place['address'] for place in places))
    known = store.geocodes(addresses)
    found, missing, unavailable = geocode([address for address in addresses if address not in known])
    store.save_geocodes(found)
    if unavailable:
        raise unavailable
    if missing:
        raise PlacesNotFound('Nominatim found no location for ' + ', '.join(
            f'{place["name"]} ("{place["address"]}")' for place in places if place['address'] in missing) + '. Nothing was saved.')
    known.update(found)
    return [{'name': place['name'], 'address': place['address'], 'found': known[place['address']]['found']} for place in places]


def run(argv):
    parser = argparse.ArgumentParser(description='Read and save the apartment search settings.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    saving = commands.add_parser('save')
    saving.add_argument('file', type=Path, help=f'JSON object with any of {", ".join(SETTINGS)}, in the scratch directory. Deleted once saved.')
    arguments = parser.parse_args(argv)
    store = ListingStore(store_path())
    if arguments.command == 'status':
        missing, unlocated = store.missing_settings(), store.places_without_coordinates()
        print(json.dumps({'complete': not missing, 'missing': missing, 'places_without_coordinates': unlocated, 'settings': store.settings()}))
        return INCOMPLETE if missing or unlocated else 0
    file, settings = read_scratch_object(arguments.file, 'Settings')
    if not settings or not set(settings) <= set(SETTINGS):
        raise ValueError(f'Settings must be a JSON object with any of {", ".join(SETTINGS)}.')
    places = locate(store, settings['places']) if 'places' in settings else None
    applied = store.update_settings(**settings, judge=evaluate if 'criteria' in settings else None)
    missing = store.missing_settings()
    result = {'saved': sorted(settings), 'complete': not missing, 'missing': missing}
    if 'criteria' in settings:
        verdicts = applied.verdicts
        result['criteria'] = {'outside': verdicts.get(OUTSIDE, 0), 'matching': verdicts.get(INSIDE, 0), 'awaiting_judgment': verdicts.get(PENDING, 0),
                              'newly_hidden': len(applied.hidden), 'shown_again': len(applied.shown)}
    if places is not None:
        result['places'] = places
        result['attribution'] = ATTRIBUTION
    file.unlink()
    print(json.dumps(result))
    return 0


def main(argv=None):
    try:
        return run(argv)
    except TimeZoneDataMissing as error:
        print(error, file=sys.stderr)
        return NO_TIME_ZONE_DATA
    except PlacesNotFound as error:
        print(error, file=sys.stderr)
        return PLACE_NOT_FOUND
    except SourceUnavailable as error:
        print(f'{error} Nothing was saved.', file=sys.stderr)
        return GEOCODING_UNAVAILABLE
    except (ValueError, KeyError, OSError) as error:
        print(error.args[0] if isinstance(error, KeyError) else error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
