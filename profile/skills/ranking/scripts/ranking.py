"""Ranks the listings in the gallery from features the model stores once per listing content.

Commands:
  ranking.py run            one pass, as the scheduled job apt-scraper-ranking runs it: ranks every listing in code,
                            then prints the next batch waiting for analysis and a last line that tells Hermes whether
                            the agent has anything to analyze. Never fails: a failure is recorded for status.
  ranking.py status         whether the ranking needs attention
  ranking.py pending        the next batch of listings and notes waiting for analysis
  ranking.py analyze FILE   save analyses from a JSON file in the scratch directory, rank again, then delete it
  ranking.py retry          try again the listings, notes and preferences the agent could not analyze

Exit codes: 0 done (for status: no problem), 3 the ranking needs attention, 1 the input or a step failed, 2 usage error.
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gallery' / 'scripts'))

from scoring import (VOCABULARY, current_features, rank, rankable, validate_preference_reading, validate_preferences,  # noqa: E402
                     validate_profile, validate_reading)
from scratch import read_scratch_object  # noqa: E402
from store import ListingStore, store_path  # noqa: E402

ATTENTION = 3
ATTEMPTS = 3
PROFILE_BATCH = 6
PREFERENCE_BATCH = 25
NOTE_BATCH = 25
PHOTOS = 3
DESCRIPTION_LIMIT = 2000


def stated(settings):
    preferences = settings.get('preferences', {})
    return [*preferences.get('likes', []), *preferences.get('dislikes', [])]


def failure(error):
    return f'{type(error).__name__}: {error}'


def listing_key(identifier, fingerprint, needs, unjudged):
    """One analysis of one listing content: its profile, or its judgment of these preferences."""
    return json.dumps([identifier, fingerprint, 'profile' in needs, unjudged], ensure_ascii=False)


def note_key(identifier):
    return json.dumps(['note', identifier])


def preference_key(phrase):
    return json.dumps(['preference', phrase], ensure_ascii=False)


def unexpressed(phrases, readings):
    """The stated preferences the vocabulary does not express well, which each shown listing is judged on."""
    return [phrase for phrase in phrases if phrase in readings and not readings[phrase]['terms']]


class RankingState:
    """The ranking job's records next to the listing store: the last run's failure, and how many runs handed each
    analysis to the agent. An analysis handed over ATTEMPTS times without being saved is given up on."""

    def __init__(self, path):
        self.path = path

    def load(self):
        return {'problem': None, 'attempts': {}, **(json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {})}

    def update(self, **values):
        value = {**self.load(), **values}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.path)


class Ranking:
    def __init__(self, store, state):
        self.store, self.state = store, state

    def rerank(self):
        snapshot = self.store.state()
        unanalyzable = {listing['id'] for listing, needs, _ in self.work(snapshot)['listings_given_up'] if 'profile' in needs}
        listings = {listing['id']: listing for listing in snapshot['listings']}
        ranked, unranked = rank(snapshot['listings'], snapshot['settings'].get('preferences', {}), self.store.note_readings(), unanalyzable,
                                self.store.preference_readings())
        return self.store.rerank({identifier: (listings[identifier]['content_fingerprint'], position, reason) for identifier, (position, reason) in ranked.items()},
                                 {identifier: (listings[identifier]['content_fingerprint'], reason) for identifier, reason in unranked.items()})

    @staticmethod
    def waiting(snapshot, readings):
        """Listings that teach the ranking come first: favorites, dismissals and listings with notes need a profile
        whether or not they are shown. Listings shown in Rankings also need a judgment of each stated preference the
        vocabulary cannot express. Every other preference counts through the terms it maps to, without the listing."""
        phrases = unexpressed(stated(snapshot['settings']), readings)
        teaching, shown = [], []
        for listing in snapshot['listings']:
            features = current_features(listing) or {}
            shows = listing['matches_criteria'] and listing['status'] == 'available' and not listing['dismissed']
            teaches = listing['favorite'] or listing['dismissed'] or any(note['role'] == 'user' for note in listing['notes'])
            unjudged = [text for text in phrases if text not in features.get('preferences', {})] if shows else []
            if not features.get('profile') and (shows or teaches):
                (shown if shows and not teaches else teaching).append((listing, ['profile', *(['preferences'] if unjudged else [])], unjudged))
            elif features.get('profile') and unjudged:
                shown.append((listing, ['preferences'], unjudged))
        return teaching + shown

    def work(self, snapshot):
        """What waits for the agent, split into what it is still given and what it was given ATTEMPTS times."""
        tried = {key for key, count in self.state.load()['attempts'].items() if count >= ATTEMPTS}
        readings = self.store.preference_readings()
        listings, notes = self.waiting(snapshot, readings), self.store.unread_notes()
        preferences = snapshot['settings'].get('preferences', {})
        unread = [{'phrase': phrase, 'kind': kind} for kind, key in (('like', 'likes'), ('dislike', 'dislikes'))
                  for phrase in preferences.get(key, []) if phrase not in readings]
        given_up = [entry for entry in listings if listing_key(entry[0]['id'], entry[0]['content_fingerprint'], *entry[1:]) in tried]
        notes_given_up = [note for note in notes if note_key(note['id']) in tried]
        preferences_given_up = [item for item in unread if preference_key(item['phrase']) in tried]
        return {'listings': [entry for entry in listings if entry not in given_up], 'listings_given_up': given_up,
                'notes': [note for note in notes if note not in notes_given_up], 'notes_given_up': notes_given_up,
                'preferences': [item for item in unread if item not in preferences_given_up], 'preferences_given_up': preferences_given_up}

    def pending(self):
        snapshot = self.store.state()
        return self.batch(snapshot, self.work(snapshot))

    def batch(self, snapshot, work):
        profiles = [entry for entry in work['listings'] if 'profile' in entry[1]]
        listings = profiles[:PROFILE_BATCH] if profiles else work['listings'][:PREFERENCE_BATCH]
        titles = {listing['id']: listing.get('title') or listing.get('address') for listing in snapshot['listings']}
        return {
            'vocabulary': {dimension: list(terms) for dimension, terms in VOCABULARY.items()},
            'listings': [self.describe(*entry) for entry in listings],
            'notes': [{'id': note['id'], 'listing_id': note['listing_id'], 'listing': titles[note['listing_id']], 'text': note['text']}
                      for note in work['notes'][:NOTE_BATCH]],
            'preferences': work['preferences'],
            'remaining': {'listings': len(work['listings']) - len(listings), 'notes': max(0, len(work['notes']) - NOTE_BATCH)},
        }

    @staticmethod
    def describe(listing, needs, unjudged):
        description = listing.get('description') or ''
        entry = {'id': listing['id'], 'fingerprint': listing['content_fingerprint'], 'needs': needs, 'unjudged_preferences': unjudged,
                 **{key: listing.get(key) for key in ('title', 'address', 'price', 'price_note', 'currency', 'rental_period', 'rooms', 'size', 'amenities')},
                 'description': description[:DESCRIPTION_LIMIT] + (' [shortened]' if len(description) > DESCRIPTION_LIMIT else '')}
        if 'profile' in needs:
            entry['photos'] = listing['photos'][:PHOTOS]
        else:
            entry['profile'] = current_features(listing)['profile']
        return entry

    def analyze(self, value):
        if not isinstance(value, dict) or set(value) != {'listings', 'notes', 'preferences'} or not all(isinstance(value[key], list) for key in value):
            raise ValueError('An analysis is a JSON object with "listings", "notes" and "preferences", all arrays.')
        stated_phrases = stated(self.store.settings())
        readings = self.store.preference_readings()
        for item in value['preferences']:
            if not isinstance(item, dict) or set(item) != {'phrase', 'terms', 'reason'}:
                raise ValueError('Each preference reading has exactly phrase, terms and reason.')
            if item['phrase'] not in stated_phrases:
                raise ValueError(f'"{item["phrase"]}" is not a stated preference. The preferences are: {json.dumps(stated_phrases)}.')
            validate_preference_reading({'terms': item['terms'], 'reason': item['reason']})
            readings = {**readings, item['phrase']: {'terms': item['terms'], 'reason': item['reason']}}
        phrases = set(unexpressed(stated_phrases, readings))
        unread = {note['id'] for note in self.store.unread_notes()}
        for entry in value['listings']:
            if not isinstance(entry, dict) or not {'id', 'fingerprint'} <= set(entry) <= {'id', 'fingerprint', 'profile', 'preferences'}:
                raise ValueError('Each listing analysis has id and fingerprint, with profile and preferences.')
            try:
                listing = self.store.listing(entry['id'])
            except KeyError:
                raise ValueError(f'No listing has the id {entry["id"]}.') from None
            if 'profile' in entry:
                validate_profile(entry['profile'])
            elif listing['content_fingerprint'] == entry['fingerprint'] and not (self.store.features(entry['id']) or {}).get('profile'):
                raise ValueError(f'{entry["id"]} has no profile yet, so its analysis needs one.')
            validate_preferences(entry.get('preferences', {}))
            for text in entry.get('preferences', {}):
                if text not in phrases:
                    raise ValueError(f'"{text}" is not a preference to judge per listing. The stated preferences read with no terms '
                                     f'are: {json.dumps(sorted(phrases))}.')
        for note in value['notes']:
            if not isinstance(note, dict) or note.get('id') not in unread:
                raise ValueError(f'Note {note.get("id") if isinstance(note, dict) else note} is not a note by the user waiting for a reading.')
            validate_reading({key: item for key, item in note.items() if key != 'id'})
        for item in value['preferences']:
            self.store.read_preference(item['phrase'], {'terms': item['terms'], 'reason': item['reason']})
        stale = []
        for entry in value['listings']:
            if self.store.listing(entry['id'])['content_fingerprint'] != entry['fingerprint']:
                stale.append(entry['id'])
                continue
            judged = (self.store.features(entry['id']) or {}).get('preferences', {})
            values = {'preferences': {**judged, **entry.get('preferences', {})}}
            if 'profile' in entry:
                values['profile'] = entry['profile']
            self.store.analyze(entry['id'], entry['fingerprint'], values)
        for note in value['notes']:
            self.store.read_note(note['id'], {key: item for key, item in note.items() if key != 'id'})
        result = self.rerank()
        return {'listings': len(value['listings']) - len(stale), 'notes': len(value['notes']), 'preferences': len(value['preferences']),
                'stale': stale, 'ranked': result['ranked']}

    def run(self):
        """Every run that hands an analysis to the agent counts an attempt for it. Counts of analyses no longer waiting
        are dropped, so a saved analysis, or a listing whose content changed, starts again from zero."""
        if self.store.missing_settings():
            self.state.update(problem=None)
            return ['The interview is not complete, so nothing was ranked.'], False
        result = self.rerank()
        snapshot = self.store.state()
        work = self.work(snapshot)
        batch = self.batch(snapshot, work)
        current = {listing_key(listing['id'], listing['content_fingerprint'], needs, unjudged)
                   for listing, needs, unjudged in work['listings'] + work['listings_given_up']}
        current |= {note_key(note['id']) for note in work['notes'] + work['notes_given_up']}
        current |= {preference_key(item['phrase']) for item in work['preferences'] + work['preferences_given_up']}
        attempts = {key: count for key, count in self.state.load()['attempts'].items() if key in current}
        handed = [listing_key(entry['id'], entry['fingerprint'], entry['needs'], entry['unjudged_preferences']) for entry in batch['listings']]
        handed += [note_key(note['id']) for note in batch['notes']] + [preference_key(item['phrase']) for item in batch['preferences']]
        for key in handed:
            attempts[key] = attempts.get(key, 0) + 1
        self.state.update(problem=None, attempts=attempts)
        lines = [f'Ranked {result["ranked"]} listings.']
        listings = len(batch['listings']) + batch['remaining']['listings']
        notes = len(batch['notes']) + batch['remaining']['notes']
        preferences = len(batch['preferences'])
        if listings or notes or preferences:
            lines += [f'{listings} listings, {notes} notes and {preferences} preferences wait for analysis. The first batch:',
                      json.dumps(batch, ensure_ascii=False, indent=1)]
        return lines, bool(listings or notes or preferences)

    def status(self):
        problem = self.state.load()['problem']
        snapshot = self.store.state()
        work = self.work(snapshot)
        unanalyzable = [listing['id'] for listing, needs, _ in work['listings_given_up'] if 'profile' in needs]
        unjudged = [listing['id'] for listing, needs, _ in work['listings_given_up'] if 'profile' not in needs]
        problems = [problem] if problem else []
        if unanalyzable:
            problems.append(f'The agent could not analyze {len(unanalyzable)} listings in {ATTEMPTS} attempts. Shown ones are ranked by price, size '
                            f'and rooms only until their content changes or the user asks to retry: {", ".join(unanalyzable)}.')
        if unjudged:
            problems.append(f'The agent could not judge the latest preferences for {len(unjudged)} listings in {ATTEMPTS} attempts. Those preferences '
                            f'do not count for them until their content changes or the user asks to retry: {", ".join(unjudged)}.')
        if work['notes_given_up']:
            problems.append(f'The agent could not read {len(work["notes_given_up"])} notes in {ATTEMPTS} attempts, so they do not count until the '
                            f'user asks to retry. They are notes on {", ".join(sorted({note["listing_id"] for note in work["notes_given_up"]}))}.')
        if work['preferences_given_up']:
            problems.append(f'The agent could not read the preferences {", ".join(item["phrase"] for item in work["preferences_given_up"])} in '
                            f'{ATTEMPTS} attempts, so they do not count until the user asks to retry.')
        readings = self.store.preference_readings()
        return {'problems': problems, 'ranked': sum(rankable(listing, set(unanalyzable)) for listing in snapshot['listings']),
                'awaiting_analysis': len(work['listings']), 'unread_notes': len(work['notes']), 'unread_preferences': len(work['preferences']),
                'judged_per_listing': {phrase: readings[phrase]['reason'] for phrase in unexpressed(stated(snapshot['settings']), readings)}}


def run():
    """The cron job's command. Hermes wakes the model whenever a cron script fails, so every failure ends here."""
    state = None
    try:
        path = store_path()
        state = RankingState(path.parent / 'ranking.json')
        lines, wake = Ranking(ListingStore(path), state).run()
    except Exception as error:
        lines, wake = [f'The ranking failed. {failure(error)}'], False
        if state is not None:
            try:
                state.update(problem=lines[0])
            except Exception as record_error:
                lines.append(f'The failure could not be recorded for status. {failure(record_error)}')
    print('\n'.join([*lines, json.dumps({'wakeAgent': wake})]))
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description='Rank the listings in the gallery.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('run')
    commands.add_parser('status')
    commands.add_parser('pending')
    analyzing = commands.add_parser('analyze')
    analyzing.add_argument('file', type=Path, help='JSON object {"listings": [...], "notes": [...]} in the scratch directory. Deleted once saved.')
    commands.add_parser('retry')
    arguments = parser.parse_args(argv)
    if arguments.command == 'run':
        return run()
    try:
        path = store_path()
        state = RankingState(path.parent / 'ranking.json')
        if arguments.command == 'status':
            try:
                report = Ranking(ListingStore(path), state).status()
            except sqlite3.Error as error:
                problem = state.load()['problem']
                report = {'problems': [*([problem] if problem else []), f'The listing store cannot be read. {failure(error)}'],
                          'ranked': None, 'awaiting_analysis': None, 'unread_notes': None}
            print(json.dumps(report, ensure_ascii=False))
            return ATTENTION if report['problems'] else 0
        if arguments.command == 'retry':
            state.update(attempts={})
            print('Every listing, note and preference the agent could not analyze is tried again from the next run.')
            return 0
        ranking = Ranking(ListingStore(path), state)
        if arguments.command == 'pending':
            print(json.dumps(ranking.pending(), ensure_ascii=False, indent=1))
        else:
            file, value = read_scratch_object(arguments.file, 'The analysis')
            print(json.dumps(ranking.analyze(value)))
            file.unlink()
        return 0
    except (ValueError, KeyError, OSError, sqlite3.Error) as error:
        print(error.args[0] if isinstance(error, KeyError) else error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
