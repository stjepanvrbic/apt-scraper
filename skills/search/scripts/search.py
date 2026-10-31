"""Searches the user's listing sites and keeps the profile's listing store current.

Commands:
  search.py run          one search pass, as the scheduled job apt-scraper-search runs it: saves the judgments the
                         last wake wrote into the job's inbox, searches, writes the judgment task files of the
                         listings waiting for a judgment of the must-haves and prints a report whose last line tells
                         Hermes whether the agent has listings to judge
  search.py status       whether the search runs and what needs the user's attention
  search.py tasks        in a conversation: save the judgments in the conversation's inbox, then write the judgment
                         task files of every listing waiting for a judgment of the must-haves
  search.py apply        in a conversation: save the judgments in the conversation's inbox now
  search.py reported     record that the user was told about the new listings

Exit codes: 0 done (for status: the search runs without problems, for apply: every file saved), 3 the search needs
attention, 1 the input or a step failed or apply rejected a file, 2 usage error.

The agent the job wakes runs with file tools only. It saves its judgments by writing a JSON file into the job's inbox,
which the next run checks and saves (inbox.py). In a conversation, tasks and apply use an inbox and a task
directory of their own, so they never change the files of a wake that runs meanwhile.
"""

import argparse
import json
import shutil
import sys
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template
from typing import Literal, Protocol, TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gallery' / 'scripts'))

from client import Fetcher  # noqa: E402
from code_features import RankingFeatures  # noqa: E402
from inbox import Rejections, apply, batches, correction, discard, inbox_directory, listing_key, load_batches, save_batches  # noqa: E402
from matching import INSIDE, OUTSIDE, PENDING, Facts, Features, MustHaves, Verdict, evaluate, required, unjudged  # noqa: E402
from schema import content_fingerprint  # noqa: E402
from shapes import Criteria, RentalPeriod, is_array, is_object  # noqa: E402
from sites import site_for, site_named  # noqa: E402
from sites.errors import Blocked, Gone  # noqa: E402
from sites.zillow import SiteListing, SourcedListing  # noqa: E402
from state import RunRecord, RunReport, SearchState, SiteReport  # noqa: E402
from store import ListingStore, store_path, validate_listing  # noqa: E402

ATTENTION = 3
RUN_BUDGET = timedelta(minutes=25)
SCHEDULE = timedelta(hours=1)
STOPPED_AFTER = 3 * SCHEDULE
BATCH = 25
REREADS_PER_RUN = 20
DESCRIPTION_LIMIT = 2000
Count = Literal['new', 'changed', 'unchanged', 'gone', 'deferred', 'unreadable']
COUNTS: tuple[Count, ...] = ('new', 'changed', 'unchanged', 'gone', 'deferred', 'unreadable')
ERRORS_PER_SITE = 10
JUDGMENT_TEMPLATE = Path(__file__).resolve().parents[1] / 'judgment.md'

Outcome = Literal['new', 'changed', 'unchanged', 'gone', 'unreadable']
"""What reading a card did to the store."""
SearchStatus = Literal['waiting for the interview', 'not started', 'stopped', 'running', 'failed', 'finished']


class AreaSearch(TypedDict):
    """A site's search: the criteria area it covers, and whatever the site's own pages need."""
    area: str


class SiteCard(TypedDict):
    """A listing as a site's results page shows it: its id, its page and a signature of what the card showed."""
    id: str
    url: str
    signature: str


class Site[S: AreaSearch, C: SiteCard](Protocol):
    """A listing site module as the search uses it (sites/__init__.py), with its own search and card shapes."""

    __name__: str

    @property
    def NAME(self) -> str: ...

    def searches(self, criteria: Criteria, client: Fetcher, regions: dict[str, object], /) -> list[S]: ...

    def pages(self, search: S, client: Fetcher, /) -> Iterator[list[C]]: ...

    def read(self, card: C, client: Fetcher, /) -> tuple[SiteListing, Facts]: ...

    def reread(self, listing: SourcedListing, client: Fetcher, /) -> tuple[SiteListing, Facts]: ...


class Handed(TypedDict):
    """What handing the judgments over did: the inbox files saved and rejected, the listings waiting and their task files."""
    saved: int
    rejected: list[str]
    awaiting_total: int
    tasks: list[str]
    wake: bool


class Report(Handed):
    """A run's report: the settings the interview still misses, each site's counts, and the error that stopped the run."""
    missing: list[str]
    sites: list[SiteReport]
    error: str | None


class PendingListing(TypedDict):
    """A listing waiting for a judgment, as its task file shows it: its key, the phrases to judge and what its site says."""
    id: str
    fingerprint: str
    unjudged: list[str]
    title: str | None
    address: str | None
    price: int | float | None
    price_note: str | None
    currency: str | None
    rental_period: RentalPeriod | None
    rooms: str | None
    size: str | None
    amenities: list[str] | None
    description: str


class Judgment(TypedDict):
    """An agent's judgment of one listing's must-haves, once its id, fingerprint and phrases are checked."""
    id: str
    fingerprint: str
    must_haves: MustHaves


class JudgeResult(TypedDict):
    """What saving judgments did: the listings saved, those whose content changed meanwhile, and the verdicts."""
    saved: int
    stale: list[str]
    shown: int
    outside: int


class StatusReport(TypedDict):
    state: SearchStatus
    problems: list[str]
    running_since: str | None
    last_run: RunRecord | None
    shown: int
    awaiting_judgment: int
    unavailable: int
    new_since_report: int


class TasksReport(TypedDict):
    """What search.py tasks prints."""
    listings: int
    tasks: list[str]
    saved: int
    rejected: list[str]


class ApplyReport(TypedDict):
    """What search.py apply prints: each file's result and each rejected file's error, by name."""
    saved: dict[str, object]
    rejected: Rejections


def site_key[S: AreaSearch, C: SiteCard](site: Site[S, C]) -> str:
    return site.__name__.rsplit('.', 1)[-1]


def failure(error: BaseException) -> str:
    return f'{type(error).__name__}: {error}'


def note(result: SiteReport, text: str) -> None:
    if len(result['errors']) < ERRORS_PER_SITE:
        result['errors'].append(text)


def stored_features(features: RankingFeatures | None) -> Features | None:
    """A listing's features as the store reads them, in the shape matching.py judges: code_features.py and matching.py
    describe the same record."""
    return cast(Features | None, features)


def judge(criteria: Criteria, features: RankingFeatures | None) -> Verdict:
    """evaluate, on the features as the store reads them."""
    return evaluate(criteria, stored_features(features))


def recorded(report: Report) -> RunReport:
    """The report as the run records it: without the task files."""
    return {'missing': report['missing'], 'sites': report['sites'], 'saved': report['saved'], 'rejected': report['rejected'],
            'awaiting_total': report['awaiting_total'], 'wake': report['wake'], 'error': report['error']}


class Search[S: AreaSearch, C: SiteCard]:
    def __init__(self, store: ListingStore, state: SearchState, client: Fetcher | None, site_for: Callable[[str], Site[S, C] | None],
                 site_named: Callable[[str], Site[S, C] | None], now: Callable[[], datetime], inbox: Path, workspace: Path) -> None:
        """inbox is the job's inbox, workspace the directory of its judgment task files."""
        self.store: ListingStore = store
        self.state: SearchState = state
        self.client: Fetcher | None = client
        self.site_for: Callable[[str], Site[S, C] | None] = site_for
        self.site_named: Callable[[str], Site[S, C] | None] = site_named
        self.now: Callable[[], datetime] = now
        self.inbox: Path = inbox
        self.workspace: Path = workspace

    def fetcher(self) -> Fetcher:
        """The run's client. The commands that search nothing have none, and never ask a site for a page."""
        return self.client  # pyright: ignore[reportReturnType]  # None only for the commands that search nothing

    def run(self, budget: timedelta) -> Report:
        """One search pass: searches every listing site, then hands the judgments over as hand_judgments says."""
        started = self.now()
        run_id = self.state.start_run(started)
        report: Report = {'missing': self.state.missing_settings(self.store), 'sites': [], 'saved': 0, 'rejected': [], 'awaiting_total': 0,
                          'tasks': [], 'wake': False, 'error': None}
        try:
            if not report['missing']:
                settings = self.store.settings()
                for url in settings.get('listing_sites', []):
                    report['sites'].append(self.search_site(url, settings.get('criteria', {}), started + budget))
                _ = self.store.apply_criteria(judge)
                report = {**report, **self.hand_judgments()}
        except Exception as error:
            report['error'] = failure(error)
        finally:
            self.state.finish_run(run_id, self.now(), recorded(report))
        return report

    def hand_judgments(self) -> Handed:
        """Saves the judgments in the inbox, then writes the task files of every listing waiting for a judgment, the
        batch of a rejected judgments file first, with the error, and deletes the rejected files whose listings no longer
        wait. Returns the counts and task files for the report."""
        saved, rejected = self.save_inbox()
        pending = self.pending()
        tasks, handed = self.write_tasks(pending, rejected)
        discard(self.inbox, rejected, handed)
        return {'saved': len(saved), 'rejected': sorted(rejected), 'awaiting_total': len(pending), 'tasks': tasks, 'wake': bool(tasks)}

    def save_inbox(self) -> tuple[dict[str, object], Rejections]:
        """Saves the judgments files in the inbox, and returns their results and the errors of those rejected, by name."""
        return apply(self.inbox, {'judgments': self.judge_file})

    def write_tasks(self, pending: list[PendingListing], rejected: Rejections) -> tuple[list[str], list[str]]:
        """Writes one judgment task file per batch of BATCH listings waiting, a batch whose judgments file was rejected
        first under its name, with the error. Returns the task files in order and the names of their judgments files."""
        again = [name.removeprefix('judgments-').removesuffix('.json') for name in rejected]
        named = batches([(item['id'], item) for item in pending], BATCH, load_batches(self.workspace), again)
        shutil.rmtree(self.workspace, ignore_errors=True)
        if not named:
            return [], []
        save_batches(self.workspace, named, lambda item: item['id'])
        tasks: list[str] = []
        for name, listings in named.items():
            judgments_file = self.inbox / f'judgments-{name}.json'
            task_file = self.workspace / f'{name}.md'
            _ = task_file.write_text(Template(JUDGMENT_TEMPLATE.read_text(encoding='utf-8')).substitute(
                count=len(listings), listings_json=json.dumps(listings, ensure_ascii=False, indent=1),
                judgments_file=judgments_file.as_posix(), correction=correction(judgments_file, rejected.get(judgments_file.name))),
                encoding='utf-8')
            tasks.append(task_file.as_posix())
        return tasks, [f'judgments-{name}.json' for name in named]

    def search_site(self, url: str, criteria: Criteria, deadline: datetime) -> SiteReport:
        result: SiteReport = {'site': url, 'new': 0, 'changed': 0, 'unchanged': 0, 'gone': 0, 'deferred': 0, 'unreadable': 0, 'errors': []}
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
            searches: list[S] = []
            try:
                for area in criteria.get('location', []):
                    try:
                        searches += site.searches({**criteria, 'location': [area]}, self.fetcher(), regions)
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

    def read_results(self, site: Site[S, C], searches: list[S], deadline: datetime, result: SiteReport) -> tuple[dict[str, set[str]], set[str]]:
        streams = [(search['area'], iter(site.pages(search, self.fetcher()))) for search in searches]
        seen: dict[str, set[str]] = {}
        complete: set[str] = set()
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

    def consider(self, site: Site[S, C], card: C, area: str) -> Outcome | None:
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
            listing, facts = site.read(card, self.fetcher())
        except Gone:
            if stored and stored['status'] == 'available':
                self.store.set_availability(card['id'], 'unavailable')
                return 'gone'
            return None
        areas: list[str] = sorted({area, *(previous['areas'] if previous else [])})
        self.save(listing, facts, areas)
        self.state.saw(card['id'], card['signature'], areas, self.now())
        return 'changed' if stored else 'new'

    def save(self, listing: SiteListing, facts: Facts, areas: list[str]) -> None:
        """Saves the listing hidden. The run applies the criteria current at its end to every listing."""
        fingerprint = content_fingerprint(validate_listing({**listing, 'status': 'available'}))
        try:
            current = self.store.listing(listing['id'])['content_fingerprint'] == fingerprint
        except KeyError:
            current = False
        previous: Features = (stored_features(self.store.features(listing['id'])) if current else None) or {}
        features: Features = {**previous, 'facts': {**facts, 'areas': areas}, 'must_haves': previous.get('must_haves', {})}
        _ = self.store.upsert_listing({**listing, 'status': 'available'}, matches_criteria=False)
        self.store.analyze(listing['id'], fingerprint, features)

    def add_area(self, listing_id: str, area: str) -> None:
        previous = self.state.card(listing_id)
        if previous is None or area in previous['areas']:
            return
        areas = sorted({area, *previous['areas']})
        self.state.saw(listing_id, previous['signature'], areas, self.now())
        features = stored_features(self.store.features(listing_id))
        if features is not None:
            features = {**features, 'facts': {**features.get('facts', {}), 'areas': areas}}
            self.store.analyze(listing_id, self.store.listing(listing_id)['content_fingerprint'], features)

    def recheck_missing(self, site: Site[S, C], criteria: Criteria, seen: dict[str, set[str]], complete: set[str], deadline: datetime,
                        result: SiteReport) -> None:
        prefix = site_key(site) + ':'
        rereads = 0
        for item in self.store.state()['listings']:
            if rereads >= REREADS_PER_RUN or self.now() >= deadline:
                return
            previous = self.state.card(item['id'])
            if (not item['id'].startswith(prefix) or item['id'] in seen or item['status'] != 'available'
                    or not item['matches_criteria'] or previous is None):
                continue
            areas = set(previous['areas']) & set(criteria.get('location', []))
            if not areas or not areas <= complete:
                continue
            rereads += 1
            try:
                listing, facts = site.reread(item, self.fetcher())
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

    def pending(self) -> list[PendingListing]:
        criteria = self.store.settings().get('criteria')
        if not criteria:
            return []
        waiting: list[PendingListing] = []
        for item in self.store.state()['listings']:
            features = item['features'] if item.get('analysis_current') else None
            if item['status'] != 'available' or judge(criteria, features) != PENDING:
                continue
            description = item.get('description') or ''
            waiting.append({
                'id': item['id'], 'fingerprint': item['content_fingerprint'], 'unjudged': unjudged(criteria, stored_features(features)),
                'title': item.get('title'), 'address': item.get('address'), 'price': item.get('price'), 'price_note': item.get('price_note'),
                'currency': item.get('currency'), 'rental_period': item.get('rental_period'), 'rooms': item.get('rooms'), 'size': item.get('size'),
                'amenities': item.get('amenities'),
                'description': description[:DESCRIPTION_LIMIT] + (' [shortened]' if len(description) > DESCRIPTION_LIMIT else ''),
            })
        return waiting

    def judge_file(self, value: dict[str, object]) -> JudgeResult:
        if set(value) != {'judgments'}:
            raise ValueError('Judgments must be a JSON object with only "judgments", an array.')
        return self.judge(value['judgments'])

    def judge(self, entries: object) -> JudgeResult:
        criteria = self.store.settings().get('criteria', {})
        if not is_array(entries):
            raise ValueError('Judgments must be a JSON array.')
        judgments: list[Judgment] = []
        for entry in entries:
            if not is_object(entry) or set(entry) != {'id', 'fingerprint', 'must_haves'} or not is_object(entry['must_haves']):
                raise ValueError('Each judgment needs exactly id, fingerprint and must_haves, an object.')
            identifier, fingerprint = listing_key(entry, 'Each judgment')
            allowed = required(criteria, stored_features(self.store.features(identifier)))
            must_haves: MustHaves = {}
            for phrase, value in entry['must_haves'].items():
                if phrase not in allowed:
                    raise ValueError(f'"{phrase}" is not a phrase to judge for {identifier}. Its phrases are: {json.dumps(allowed)}.')
                if value is not None and not isinstance(value, bool):
                    raise ValueError(f'The judgment of "{phrase}" must be true, false or null.')
                must_haves[phrase] = value
            judgments.append({'id': identifier, 'fingerprint': fingerprint, 'must_haves': must_haves})
        result: JudgeResult = {'saved': 0, 'stale': [], 'shown': 0, 'outside': 0}
        for judgment in judgments:
            if self.store.listing(judgment['id'])['content_fingerprint'] != judgment['fingerprint']:
                result['stale'].append(judgment['id'])
                continue
            features: Features = stored_features(self.store.features(judgment['id'])) or {'must_haves': {}}
            features = {**features, 'must_haves': {**features.get('must_haves', {}), **judgment['must_haves']}}
            self.store.analyze(judgment['id'], judgment['fingerprint'], features)
            verdict = evaluate(criteria, features)
            result['saved'] += 1
            result['shown'] += verdict == INSIDE
            result['outside'] += verdict == OUTSIDE
        _ = self.store.apply_criteria(judge)
        return result

    def status(self) -> StatusReport:
        problems: list[str] = []
        missing = self.state.missing_settings(self.store)
        latest, last, running_since = self.state.latest_run(), self.state.last_run(), self.state.running_since()
        now = self.now()
        state: SearchStatus
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
                                + f'{(now - running_since) // timedelta(minutes=1)} minutes.')
        else:
            state = 'failed' if latest.get('error') else 'finished'
        environment = self.state.problem()
        if environment:
            problems.append(environment)
        if not missing and last:
            if failed := last.get('error'):
                problems.append(f'The last search run failed. {failed}')
            problems += [f'{site["site"]}: {error}' for site in last.get('sites', []) for error in site['errors']]
        listings = self.store.state()['listings']
        shown = [item for item in listings if item['matches_criteria'] and item['status'] == 'available' and not item['dismissed']]
        first_seen, reported_at = self.state.first_seen(), self.state.reported_at()
        return {
            'state': state, 'problems': problems, 'running_since': running_since.isoformat() if state == 'running' and running_since else None,
            'last_run': last, 'shown': len(shown), 'awaiting_judgment': len(self.pending()) if not missing else 0,
            'unavailable': sum(item['status'] == 'unavailable' for item in listings),
            'new_since_report': sum(first_seen.get(item['id'], reported_at) > reported_at for item in shown),
        }


def describe(report: Report) -> str:
    """The run's report for the agent. It names files and counts only: listing text stays in the task files."""
    lines = [f'Search finished at {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.']
    if report['missing']:
        lines.append('The interview is not complete, so nothing was searched. Missing: ' + ', '.join(report['missing']) + '.')
    if report['saved'] or report['rejected']:
        lines.append(f'From the last wake: {report["saved"]} files saved, {len(report["rejected"])} rejected'
                     + (f' ({", ".join(report["rejected"])}).' if report['rejected'] else '.'))
    if report['error']:
        lines.append(f'The run failed. {report["error"]}')
    for site in report['sites']:
        lines.append(f'{site["site"]}: ' + ', '.join(f'{site[key]} {key}' for key in COUNTS) + '.')
        lines += [f'Problem: {error}' for error in site['errors']]
    if report['tasks']:
        lines.append(f'{report["awaiting_total"]} listings wait for a judgment of the must-haves. Judgment task files, in order:')
        lines += report['tasks']
    lines.append(json.dumps({'wakeAgent': report['wake']}))
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description='Search the listing sites and keep the listing store current.')
    commands = parser.add_subparsers(dest='command', required=True)
    _ = commands.add_parser('run')
    _ = commands.add_parser('status')
    _ = commands.add_parser('tasks')
    _ = commands.add_parser('apply')
    _ = commands.add_parser('reported')
    arguments = parser.parse_args()
    command = cast(str, arguments.command)
    path = store_path()
    state = SearchState(path.parent / 'search.sqlite3')
    client: Fetcher | None = None
    if command == 'run':
        running = state.running_since()
        if running and datetime.now(timezone.utc) - running < 2 * RUN_BUDGET:
            print(f'Another search run started at {running:%Y-%m-%d %H:%M} UTC and is still going.')
            print(json.dumps({'wakeAgent': False}))
            return 0
        from client import Client
        client = Client()
    job = command == 'run'
    search = Search(ListingStore(path), state, client, site_for, site_named, lambda: datetime.now(timezone.utc),
                    inbox_directory('search' if job else 'search-conversation'), path.parent / ('must-haves' if job else 'must-haves-conversation'))
    if command == 'run':
        print(describe(search.run(RUN_BUDGET)))
    elif command == 'status':
        report = search.status()
        print(json.dumps(report, ensure_ascii=False))
        return ATTENTION if report['problems'] else 0
    elif command == 'tasks':
        handed = search.hand_judgments()
        tasks: TasksReport = {'listings': handed['awaiting_total'], 'tasks': handed['tasks'], 'saved': handed['saved'], 'rejected': handed['rejected']}
        print(json.dumps(tasks, ensure_ascii=False, indent=1))
    elif command == 'apply':
        saved, rejected = search.save_inbox()
        applied: ApplyReport = {'saved': saved, 'rejected': rejected}
        print(json.dumps(applied, ensure_ascii=False, indent=1))
        return 1 if rejected else 0
    else:
        state.mark_reported(datetime.now(timezone.utc))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, OSError) as error:
        print(error.args[0] if isinstance(error, KeyError) else error, file=sys.stderr)
        sys.exit(1)
