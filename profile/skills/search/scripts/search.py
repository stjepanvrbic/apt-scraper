"""Searches the user's listing sites and keeps the profile's listing store current.

Commands:
  search.py run          one search pass, as the scheduled job apt-scraper-search runs it, prints a
                         report whose last line tells Hermes whether the agent has listings to judge
  search.py status       whether the search runs and what needs the user's attention
  search.py pending      the next listings waiting for a judgment of the must-haves
  search.py judge FILE   save judgments from a JSON file in the scratch directory, then delete it
  search.py reported     record that the user was told about the new listings

Exit codes: 0 done (for status: the search runs without problems), 3 the search needs attention,
1 the input or a step failed, 2 usage error.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gallery' / 'scripts'))

from matching import INSIDE, OUTSIDE, PENDING, evaluate, required, unjudged  # noqa: E402
from schema import content_fingerprint  # noqa: E402
from scratch import read_scratch_object  # noqa: E402
from sites import site_for, site_named  # noqa: E402
from sites.errors import Blocked, Gone  # noqa: E402
from state import SearchState  # noqa: E402
from store import ListingStore, store_path, validate_listing  # noqa: E402

ATTENTION = 3
RUN_BUDGET = timedelta(minutes=25)
SCHEDULE = timedelta(hours=1)
STOPPED_AFTER = 3 * SCHEDULE
BATCH = 25
REREADS_PER_RUN = 20
DESCRIPTION_LIMIT = 2000
COUNTS = ('new', 'changed', 'unchanged', 'gone', 'deferred', 'unreadable')
ERRORS_PER_SITE = 10


def site_key(site):
    return site.__name__.rsplit('.', 1)[-1]


def failure(error):
    return f'{type(error).__name__}: {error}'


def note(result, text):
    if len(result['errors']) < ERRORS_PER_SITE:
        result['errors'].append(text)


class Search:
    def __init__(self, store, state, client, site_for, site_named, now):
        self.store, self.state, self.client = store, state, client
        self.site_for, self.site_named, self.now = site_for, site_named, now

    def run(self, budget):
        started = self.now()
        run_id = self.state.start_run(started)
        report = {'missing': self.state.missing_settings(self.store), 'sites': [], 'awaiting_judgment': [], 'awaiting_total': 0, 'wake': False, 'error': None}
        try:
            if not report['missing']:
                settings = self.store.settings()
                for url in settings['listing_sites']:
                    report['sites'].append(self.search_site(url, settings['criteria'], started + budget))
                self.store.apply_criteria(evaluate)
                pending = self.pending()
                report.update(awaiting_judgment=pending[:BATCH], awaiting_total=len(pending), wake=bool(pending))
        except Exception as error:
            report['error'] = failure(error)
        finally:
            self.state.finish_run(run_id, self.now(), {key: value for key, value in report.items() if key != 'awaiting_judgment'})
        return report

    def search_site(self, url, criteria, deadline):
        result = {'site': url, **dict.fromkeys(COUNTS, 0), 'errors': []}
        site = self.site_for(url)
        if site is None:
            note(result, f'The search has no reader for {url} yet.')
            return result
        key = site_key(site)
        until = self.state.blocked_until(key)
        if until and until > self.now():
            note(result, f'{site.NAME} blocked the search, so it is left alone until {until:%Y-%m-%d %H:%M} UTC.')
            return result
        try:
            regions = self.state.regions(key)
            searches = []
            try:
                for area in criteria['location']:
                    try:
                        searches += site.searches({**criteria, 'location': [area]}, self.client, regions)
                    except LookupError as error:
                        note(result, str(error))
                    except (Blocked, ConnectionError):
                        raise
                    except Exception as error:
                        note(result, f'{area}: {failure(error)}')
            finally:
                self.state.save_regions(key, regions)
            seen, complete = self.read_results(site, searches, deadline, result)
            self.recheck_missing(site, criteria, seen, complete, deadline, result)
            self.state.unblock(key)
        except Blocked as error:
            until = self.state.block(key, self.now())
            note(result, f'{error} The search leaves {site.NAME} alone until {until:%Y-%m-%d %H:%M} UTC.')
        except Exception as error:
            note(result, failure(error))
        return result

    def read_results(self, site, searches, deadline, result):
        streams = [(search['area'], iter(site.pages(search, self.client))) for search in searches]
        seen, complete = {}, set()
        while streams and self.now() < deadline:
            for stream in list(streams):
                if self.now() >= deadline:
                    break
                area, pages = stream
                try:
                    cards = next(pages, None)
                except (Blocked, ConnectionError):
                    raise
                except Exception as error:
                    note(result, f'Results for {area}: {failure(error)}')
                    streams.remove(stream)
                    continue
                if cards is None:
                    complete.add(area)
                    streams.remove(stream)
                    continue
                for card in cards:
                    if card['id'] in seen:
                        seen[card['id']].add(area)
                        self.add_area(card['id'], area)
                    elif self.now() >= deadline:
                        result['deferred'] += 1
                    else:
                        seen[card['id']] = {area}
                        try:
                            outcome = self.consider(site, card, area)
                        except (Blocked, ConnectionError):
                            raise
                        except Exception as error:
                            outcome = 'unreadable'
                            note(result, f'{card["url"]}: {failure(error)}')
                        if outcome:
                            result[outcome] += 1
        return seen, complete

    def consider(self, site, card, area):
        try:
            stored = self.store.listing(card['id'])
        except KeyError:
            stored = None
        previous = self.state.card(card['id'])
        if stored and stored['status'] == 'available' and previous and previous['signature'] == card['signature']:
            self.state.saw(card['id'], card['signature'], previous['areas'], self.now())
            self.add_area(card['id'], area)
            return 'unchanged'
        try:
            listing, facts = site.read(card, self.client)
        except Gone:
            if stored and stored['status'] == 'available':
                self.store.set_availability(card['id'], 'unavailable')
                return 'gone'
            return None
        areas = sorted({area, *(previous['areas'] if previous else [])})
        self.save(listing, facts, areas)
        self.state.saw(card['id'], card['signature'], areas, self.now())
        return 'changed' if stored else 'new'

    def save(self, listing, facts, areas):
        """Saves the listing hidden. The run applies the criteria current at its end to every listing."""
        fingerprint = content_fingerprint(validate_listing({**listing, 'status': 'available'}))
        try:
            current = self.store.listing(listing['id'])['content_fingerprint'] == fingerprint
        except KeyError:
            current = False
        previous = (self.store.features(listing['id']) if current else None) or {}
        features = {**previous, 'facts': {**facts, 'areas': areas}, 'must_haves': previous.get('must_haves', {})}
        self.store.upsert_listing({**listing, 'status': 'available'}, matches_criteria=False)
        self.store.analyze(listing['id'], fingerprint, features)

    def add_area(self, listing_id, area):
        previous = self.state.card(listing_id)
        if previous is None or area in previous['areas']:
            return
        areas = sorted({area, *previous['areas']})
        self.state.saw(listing_id, previous['signature'], areas, self.now())
        features = self.store.features(listing_id)
        if features is not None:
            features = {**features, 'facts': {**features.get('facts', {}), 'areas': areas}}
            self.store.analyze(listing_id, self.store.listing(listing_id)['content_fingerprint'], features)

    def recheck_missing(self, site, criteria, seen, complete, deadline, result):
        prefix = site_key(site) + ':'
        rereads = 0
        for item in self.store.state()['listings']:
            if rereads >= REREADS_PER_RUN or self.now() >= deadline:
                return
            previous = self.state.card(item['id'])
            if (not item['id'].startswith(prefix) or item['id'] in seen or item['status'] != 'available'
                    or not item['matches_criteria'] or previous is None):
                continue
            areas = set(previous['areas']) & set(criteria['location'])
            if not areas or not areas <= complete:
                continue
            rereads += 1
            try:
                listing, facts = site.reread(item, self.client)
            except Gone:
                self.store.set_availability(item['id'], 'unavailable')
                result['gone'] += 1
                continue
            except (Blocked, ConnectionError):
                raise
            except Exception as error:
                result['unreadable'] += 1
                note(result, f'{item["sources"][0]["url"]}: {failure(error)}')
                continue
            self.save(listing, facts, previous['areas'])

    def pending(self):
        criteria = self.store.settings().get('criteria')
        if not criteria:
            return []
        waiting = []
        for item in self.store.state()['listings']:
            features = item['features'] if item.get('analysis_current') else None
            if item['status'] != 'available' or evaluate(criteria, features) != PENDING:
                continue
            description = item.get('description') or ''
            waiting.append({
                'id': item['id'], 'fingerprint': item['content_fingerprint'], 'unjudged': unjudged(criteria, features),
                **{key: item.get(key) for key in ('title', 'address', 'price', 'price_note', 'currency', 'rental_period', 'rooms', 'size', 'amenities')},
                'description': description[:DESCRIPTION_LIMIT] + (' [shortened]' if len(description) > DESCRIPTION_LIMIT else ''),
            })
        return waiting

    def judge(self, entries):
        criteria = self.store.settings()['criteria']
        if not isinstance(entries, list):
            raise ValueError('Judgments must be a JSON array.')
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'id', 'fingerprint', 'must_haves'} or not isinstance(entry['must_haves'], dict):
                raise ValueError('Each judgment needs exactly id, fingerprint and must_haves, an object.')
            allowed = required(criteria, self.store.features(entry['id']))
            for phrase, value in entry['must_haves'].items():
                if phrase not in allowed:
                    raise ValueError(f'"{phrase}" is not a phrase to judge for {entry["id"]}. Its phrases are: {json.dumps(allowed)}.')
                if value not in (True, False, None) or isinstance(value, int) and not isinstance(value, bool):
                    raise ValueError(f'The judgment of "{phrase}" must be true, false or null.')
        result = {'saved': 0, 'stale': [], 'shown': 0, 'outside': 0}
        for entry in entries:
            if self.store.listing(entry['id'])['content_fingerprint'] != entry['fingerprint']:
                result['stale'].append(entry['id'])
                continue
            features = self.store.features(entry['id']) or {'must_haves': {}}
            features = {**features, 'must_haves': {**features.get('must_haves', {}), **entry['must_haves']}}
            self.store.analyze(entry['id'], entry['fingerprint'], features)
            verdict = evaluate(criteria, features)
            result['saved'] += 1
            result['shown'] += verdict == INSIDE
            result['outside'] += verdict == OUTSIDE
        self.store.apply_criteria(evaluate)
        return result

    def status(self):
        problems = []
        missing = self.state.missing_settings(self.store)
        latest, last, running_since = self.state.latest_run(), self.state.last_run(), self.state.running_since()
        now = self.now()
        if missing:
            state = 'waiting for the interview'
            problems.append('The interview is not complete, so the search waits for it.')
        elif latest is None:
            state = 'not started'
            problems.append('The search has not run yet.')
        elif now - (last_active := datetime.fromisoformat(latest['finished_at'] or latest['started_at'])) > STOPPED_AFTER:
            state = 'stopped'
            problems.append(f'The search last ran at {last_active:%Y-%m-%d %H:%M} UTC. Scheduled runs happen only while the Hermes gateway runs.')
        elif running_since:
            state = 'running'
            if now - running_since > 2 * RUN_BUDGET:
                problems.append(f'The search run that started at {running_since:%Y-%m-%d %H:%M} UTC has not finished after '
                                f'{(now - running_since) // timedelta(minutes=1)} minutes.')
        else:
            state = 'failed' if latest.get('error') else 'finished'
        environment = self.state.problem()
        if environment:
            problems.append(environment)
        if not missing and last:
            if last.get('error'):
                problems.append(f'The last search run failed. {last["error"]}')
            problems += [f'{site["site"]}: {error}' for site in last['sites'] for error in site['errors']]
        listings = self.store.state()['listings']
        shown = [item for item in listings if item['matches_criteria'] and item['status'] == 'available' and not item['dismissed']]
        first_seen, reported_at = self.state.first_seen(), self.state.reported_at()
        return {
            'state': state, 'problems': problems, 'running_since': running_since and running_since.isoformat(),
            'last_run': last, 'shown': len(shown), 'awaiting_judgment': len(self.pending()) if not missing else 0,
            'unavailable': sum(item['status'] == 'unavailable' for item in listings),
            'new_since_report': sum(first_seen.get(item['id'], reported_at) > reported_at for item in shown),
        }


def describe(report):
    lines = [f'Search finished at {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.']
    if report['missing']:
        lines.append('The interview is not complete, so nothing was searched. Missing: ' + ', '.join(report['missing']) + '.')
    if report['error']:
        lines.append(f'The run failed. {report["error"]}')
    for site in report['sites']:
        lines.append(f'{site["site"]}: ' + ', '.join(f'{site[key]} {key}' for key in COUNTS) + '.')
        lines += [f'Problem: {error}' for error in site['errors']]
    if report['awaiting_total']:
        lines.append(f'{report["awaiting_total"]} listings wait for a judgment of the must-haves. The first {len(report["awaiting_judgment"])}:')
        lines.append(json.dumps(report['awaiting_judgment'], ensure_ascii=False, indent=1))
    lines.append(json.dumps({'wakeAgent': report['wake']}))
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='Search the listing sites and keep the listing store current.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('run')
    commands.add_parser('status')
    commands.add_parser('pending')
    judging = commands.add_parser('judge')
    judging.add_argument('file', type=Path, help='JSON object {"judgments": [{id, fingerprint, must_haves}]} in the scratch directory. Deleted once saved.')
    commands.add_parser('reported')
    arguments = parser.parse_args()
    path = store_path()
    state = SearchState(path.parent / 'search.sqlite3')
    client = None
    if arguments.command == 'run':
        running = state.running_since()
        if running and datetime.now(timezone.utc) - running < 2 * RUN_BUDGET:
            print(f'Another search run started at {running:%Y-%m-%d %H:%M} UTC and is still going.')
            print(json.dumps({'wakeAgent': False}))
            return 0
        from client import Client
        client = Client()
    search = Search(ListingStore(path), state, client, site_for, site_named, lambda: datetime.now(timezone.utc))
    if arguments.command == 'run':
        print(describe(search.run(RUN_BUDGET)))
    elif arguments.command == 'status':
        report = search.status()
        print(json.dumps(report, ensure_ascii=False))
        return ATTENTION if report['problems'] else 0
    elif arguments.command == 'pending':
        waiting = search.pending()
        print(json.dumps({'listings': waiting[:BATCH], 'remaining': max(0, len(waiting) - BATCH)}, ensure_ascii=False, indent=1))
    elif arguments.command == 'judge':
        file, value = read_scratch_object(arguments.file, 'Judgments')
        if set(value) != {'judgments'}:
            raise ValueError('Judgments must be a JSON object with only "judgments", an array.')
        print(json.dumps(search.judge(value['judgments'])))
        file.unlink()
    else:
        state.mark_reported(datetime.now(timezone.utc))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError) as error:
        print(error.args[0] if isinstance(error, KeyError) else error, file=sys.stderr)
        sys.exit(1)
