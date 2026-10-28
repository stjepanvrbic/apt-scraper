"""Keeps a feature record for every listing the ranking needs, read once per listing content.

Commands:
  features.py run           one pass of the scheduled job apt-scraper-features: saves the records, readings and
                            judgments the last wake wrote into the job's inbox, fetches the rapid-transit stations once
                            per transit feed, prepares the photos of the listings waiting for extraction, writes the
                            task files of the extractions, the readings and the judgments of preferences stated later,
                            prints their delegate_task calls for the agent, delivers the Compare invitation once it
                            is due, and ends with a line that tells Hermes whether to wake the agent. Never fails.
  features.py tags          the tag list, most used first
  features.py status        whether extraction needs the user's attention, and whether to ask the user to answer
                            Compare questions
  features.py retry         try again every extraction and judgment given up after 3 attempts
  features.py compare-asked record that a conversation asked the user to answer 5 to 8 Compare questions

Exit codes: 0 done (for status: no problem), 3 extraction needs attention, 1 the input or a step failed, 2 usage error.

Hermes wakes the model whenever a cron script fails (cron/scheduler_prompt.py, "Script Error"), so run records its
failures for status and ends with its wake decision instead.

The agent and its subagents run with file tools only. They save by writing JSON files into the job's inbox, which the
next run applies with save, judge and read (inbox.py).
"""

import argparse
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from string import Template

SKILLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILLS / 'gallery' / 'scripts'))
sys.path.insert(0, str(SKILLS / 'contact' / 'scripts'))

from contact import tell_user, usable  # noqa: E402
from geodata import SourceUnavailable, fetch_stations  # noqa: E402
from inbox import apply, batches, correction, discard, inbox_directory, listing_key, load_batches, save_batches  # noqa: E402
from record import CATEGORIES, FLAGS, NOT_SHOWN, RATINGS, ROOM_TYPES, UNIT_LEVELS, choices, validate_judgments  # noqa: E402
from scoring import labeled, stated_phrases  # noqa: E402
from store import READ_ATTEMPTS, ListingStore, extraction_read, store_path  # noqa: E402

ATTENTION = 3
PARALLEL_TASKS = 10
SUBAGENTS_PER_WAKE = 50
PREPARE_BUDGET = timedelta(minutes=10)
PHOTO_PATIENCE = timedelta(hours=1)
STATION_RETRY = timedelta(hours=6)
DOWNLOADS = 8
JUDGMENT_BATCH = 25
TASK_DESCRIPTION_LIMIT = 6000
JUDGMENT_DESCRIPTION_LIMIT = 2000
EXTRACTION_TEMPLATE = Path(__file__).resolve().parents[1] / 'extraction.md'
JUDGMENT_TEMPLATE = Path(__file__).resolve().parents[1] / 'judgment.md'
READING_TEMPLATE = Path(__file__).resolve().parents[1] / 'reading.md'
MEDIA = SKILLS / 'gallery' / 'web' / 'media'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36'
EXTENSIONS = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp', 'GIF': 'gif'}
COMPARE_INVITATION = ('Your first apartments are ready to compare. Please answer 5 to 8 questions in the gallery\'s Compare tab. '
                      'For each pair, choose the apartment you prefer, or skip if you cannot decide. '
                      'Your choices will count the next time you ask me to score and rerank.')


class PhotosUnavailable(Exception):
    """A photo could not be downloaded for a reason that may pass, so the listing waits for a later run."""


def download(url):
    """The photo's bytes, or None when the site no longer has it."""
    if url.startswith('/media/'):
        path = MEDIA / url.removeprefix('/media/')
        return path.read_bytes() if path.is_file() else None
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code in (404, 410):
            return None
        raise PhotosUnavailable(f'{url} answered HTTP {error.code}.') from None
    except (urllib.error.URLError, OSError) as error:
        raise PhotosUnavailable(f'{url} could not be downloaded: {error}.') from None


def failure(error):
    return f'{type(error).__name__}: {error}'


def extracted_preferences(settings, readings):
    """The stated likes and dislikes an extraction judges: every one whose reading names no record feature, or which
    is not read yet."""
    return list(dict.fromkeys(phrase for kind, phrase in stated_phrases(settings) if not readings.get((kind, phrase))))


def judged_preferences(settings, readings):
    """The stated likes and dislikes judged later for listings extracted before: those read as naming no record
    feature, which the ranking counts through these judgments."""
    return list(dict.fromkeys(phrase for kind, phrase in stated_phrases(settings) if readings.get((kind, phrase)) == []))


def needs_record(listing):
    """A listing the ranking ranks, or one the user labeled, which the ranking learns from even when it is not shown."""
    return bool(listing['matches_criteria'] and listing['status'] == 'available' and not listing['dismissed'] or labeled(listing))


def judgment_read(listing, phrase):
    return json.dumps(['preference', listing['id'], listing['content_fingerprint'], phrase], ensure_ascii=False)


def note_read(note):
    return json.dumps(['note', note['id']])


def preference_read(kind, phrase):
    return json.dumps(['preference reading', kind, phrase], ensure_ascii=False)


def work_directory(workspace, listing_id, fingerprint):
    return workspace / f'{re.sub(r"[^A-Za-z0-9._-]", "_", listing_id)}-{fingerprint[:16]}'


def record_file_name(listing):
    """The name of the inbox file the extraction of a listing's content saves its record in."""
    return f'extraction-{work_directory(Path(), listing["id"], listing["content_fingerprint"]).name}.json'


def price_text(listing):
    if listing.get('price') is None:
        return listing.get('price_note') or 'not listed'
    return ' '.join(str(part) for part in (listing['price'], listing.get('currency'), listing.get('rental_period') and f'a {listing["rental_period"]}') if part)


def listing_fields(listing, limit=None):
    """What a listing site says about the listing, as data for a model. It enters a prompt only JSON-encoded, so no line
    of a title or description can pass for a line of the instructions around it."""
    description = listing.get('description') or None
    if description and limit is not None and len(description) > limit:
        description = description[:limit] + ' [shortened]'
    return {'title': listing.get('title'), 'address': listing.get('address'), 'price': price_text(listing), 'rooms': listing.get('rooms'),
            'size': listing.get('size'), 'amenities': listing.get('amenities') or [], 'description': description}


def chunks(items, size):
    return [items[start:start + size] for start in range(0, len(items), size)]


def bullet_lines(items):
    return '\n'.join(f'  - {item}' for item in items)


def photo_overview(count):
    if not count:
        return 'This listing has no photos. Read it from its text and give every rating that needs a photo as "not shown".'
    ranges = [f'sheet {sheet + 1} shows photos {sheet * 9 + 1} to {min(count, sheet * 9 + 9)}' for sheet in range((count + 8) // 9)]
    return (f'The contact sheets attached to your task show all {count} unique photos of the listing, in order: '
            f'{", ".join(ranges)}. Each photo has its number in yellow in its top-left corner. Look at every photo on the sheets.')


class RunState:
    """The job's records next to the listing store: the last run's problems, the listings whose photos could not be
    downloaded, since when and why, the transit feed whose stations could not be fetched, since when, when last tried
    and why, and the model reads the last run handed to the agent. A read handed over that still waits at the next
    run, after it applied the inbox, counts as a failed attempt then, so an attempt counts only once its wake has
    ended."""

    def __init__(self, path):
        self.path = path

    def load(self):
        return {'problems': [], 'photos_unavailable': {}, 'stations_unavailable': None, 'handed': [],
                **(json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {})}

    def update(self, **values):
        value = {**self.load(), **values}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, self.path)


class Features:
    def __init__(self, store, state, workspace, fetch, now, stations):
        self.store, self.state, self.workspace, self.fetch, self.now, self.stations = store, state, workspace, fetch, now, stations
        self.inbox = inbox_directory('features')

    def outstanding(self):
        """The listings without a record of their current content, and the extracted listings with stated preferences not
        judged yet, each split into those still given to the agent and those given up after READ_ATTEMPTS attempts. Listings the
        user labeled come first, then the longest known. A listing whose record's room types the photo encoder
        disagrees with on most photos is extracted once more, and keeps its record if that read is given up."""
        snapshot = self.store.state()
        records, attempts = self.store.feature_records(), self.store.read_attempts()
        readings = self.store.preference_readings()
        phrases = extracted_preferences(snapshot['settings'], readings)
        later = judged_preferences(snapshot['settings'], readings)
        listings = sorted((listing for listing in snapshot['listings'] if needs_record(listing)),
                          key=lambda listing: (not labeled(listing), listing['first_seen'], listing['id']))
        rereads = self.store.photo_rereads()
        extract = [listing for listing in listings if listing['id'] not in records or listing['id'] in rereads]
        judge = [(listing, [phrase for phrase in later if phrase not in records[listing['id']]['preferences']])
                 for listing in listings if listing['id'] in records]
        judge = [(listing, missing) for listing, missing in judge if missing]
        notes = self.store.unread_notes()
        unread = self.store.unread_preferences()
        return {
            'notes': [note for note in notes if attempts.get(note_read(note), 0) < READ_ATTEMPTS],
            'notes_given_up': [note for note in notes if attempts.get(note_read(note), 0) >= READ_ATTEMPTS],
            'unread': [item for item in unread if attempts.get(preference_read(*item), 0) < READ_ATTEMPTS],
            'unread_given_up': [item for item in unread if attempts.get(preference_read(*item), 0) >= READ_ATTEMPTS],
            'phrases': phrases, 'records': records,
            'extract': [listing for listing in extract if attempts.get(extraction_read(listing['id'], listing['content_fingerprint']), 0) < READ_ATTEMPTS],
            'extract_given_up': [listing for listing in extract if attempts.get(extraction_read(listing['id'], listing['content_fingerprint']), 0) >= READ_ATTEMPTS
                                 and listing['id'] not in records],
            'judge': [(listing, [phrase for phrase in missing if attempts.get(judgment_read(listing, phrase), 0) < READ_ATTEMPTS])
                      for listing, missing in judge if any(attempts.get(judgment_read(listing, phrase), 0) < READ_ATTEMPTS for phrase in missing)],
            'judge_given_up': [(listing, [phrase for phrase in missing if attempts.get(judgment_read(listing, phrase), 0) >= READ_ATTEMPTS])
                               for listing, missing in judge if any(attempts.get(judgment_read(listing, phrase), 0) >= READ_ATTEMPTS for phrase in missing)],
        }

    def run(self):
        """One pass of the job: the files the last wake wrote into the inbox, the stations, the extraction tasks, the
        readings and judgments waiting for the agent, and then the Compare invitation. A step that fails becomes one of
        the run's problems without stopping the others, except the invitation, whose failure its record in the store
        holds. A rejected file stays in the inbox while its task is handed
        again, with the error, and is deleted otherwise. The reads handed to the agent are recorded once the report
        that hands them over is complete, and the invitation comes after that, so the messaging gateway cannot change
        the report, the wake or the attempts."""
        if self.store.missing_settings():
            self.state.update(problems=[])
            return ['The interview is not complete, so no listing is extracted.'], False
        problems = []
        applied = self.step(problems, 'Saving what the last wake wrote', self.save_inbox)
        if applied is None:
            self.state.update(problems=problems)
            return [*problems, 'The files stay in the inbox for the next run, which counts no attempt before it saves them.'], False
        saved_lines, rejected = applied
        lines = self.step(problems, 'Fetching the rapid-transit stations', self.refresh_stations) or []
        work = self.count_attempts()
        extraction = self.step(problems, 'Preparing the extraction tasks', lambda: self.prepare_extractions(work, rejected))
        tasks = extraction[1] if extraction else []
        reading = self.step(problems, 'Preparing the readings', lambda: self.prepare_readings(work, rejected))
        judgment = self.step(problems, 'Preparing the judgment tasks', lambda: self.prepare_judgments(work, SUBAGENTS_PER_WAKE - len(tasks), rejected))
        prepared = [extraction, reading, judgment]
        (extraction_lines, _, _), (reading_lines, readings, _), (judgment_lines, judgments, _) = (step or ([], [], []) for step in prepared)
        if all(prepared):
            discard(self.inbox, rejected, [name for _, _, files in prepared for name in files])
        handed = [extraction_read(listing['id'], listing['content_fingerprint']) for listing in tasks] + readings + judgments
        self.state.update(problems=problems, handed=handed)
        return [*saved_lines, *lines, *extraction_lines, *reading_lines, *judgment_lines, *problems,
                *self.deliver_compare_invitation()], bool(handed)

    def save_inbox(self):
        """Saves the records, readings and judgments in the inbox, and returns the report's line about them and the
        errors of the files rejected, by file name. The line names files only: a rejection's error may quote what a
        subagent read from a listing, so it goes into the task handed again, never into the agent's report."""
        saved, rejected = apply(self.inbox, {'extraction': self.save, 'judgments': self.judge, 'readings': self.read})
        if not saved and not rejected:
            return [], {}
        return [f'From the last wake: {len(saved)} files saved, {len(rejected)} rejected'
                + (f' ({", ".join(sorted(rejected))}).' if rejected else '.')], rejected

    @staticmethod
    def step(problems, name, action):
        """What action returns, or None when it fails, with the failure added to problems."""
        try:
            return action()
        except Exception as error:
            problems.append(f'{name} failed. {failure(error)}')
            return None

    def count_attempts(self):
        """Counts a failed attempt for each read the last run handed to the agent that still waits, and returns the work
        waiting now. The handed reads are forgotten before they are counted, so a run that fails before it hands
        new ones over cannot count the same wake twice."""
        handed = self.state.load()['handed']
        self.state.update(handed=[])
        waiting = self.reads(self.outstanding())
        self.store.record_read_attempts([read for read in handed if read in waiting], waiting)
        return self.outstanding()

    def prepare_extractions(self, work, rejected):
        """Prepares the extraction tasks of this wake and returns the report's lines, the listings prepared and the
        names of their record files. A listing whose last record was rejected comes first, with the error for its
        subagent to correct the record. A wake holds at most SUBAGENTS_PER_WAKE extractions, because Hermes blocks the
        agent's delegate_task once one turn has spawned that many subagents (tool_loop_guardrails.loop_caps.max_subagents
        in hermes_cli/config_defaults.py), which would end the wake before its judgments."""
        photos_unavailable = self.state.load()['photos_unavailable']
        now = self.now()
        deadline = now + PREPARE_BUDGET
        tags = self.tag_lines()
        tasks, unavailable, deferred = [], {}, 0
        for listing in sorted(work['extract'], key=lambda listing: record_file_name(listing) not in rejected):
            if self.now() >= deadline or len(tasks) == SUBAGENTS_PER_WAKE:
                deferred += 1
                continue
            since = photos_unavailable.get(listing['id'], {}).get('since')
            overdue = since is not None and now - datetime.fromisoformat(since) >= PHOTO_PATIENCE
            try:
                tasks.append((listing, self.prepare(listing, work['phrases'], tags, rejected.get(record_file_name(listing)), leave_out_failing=overdue)))
            except PhotosUnavailable as error:
                unavailable[listing['id']] = {'since': since or now.isoformat(), 'reason': str(error)}
        self.state.update(photos_unavailable=unavailable)
        self.remove_work_directories({self.workspace / 'judgments', *(work_directory(self.workspace, listing['id'], listing['content_fingerprint'])
                                                                       for listing in work['extract'])})
        lines = [f'{len(work["extract"])} listings wait for extraction: {len(tasks)} prepared below, {deferred + len(unavailable)} wait '
                 f'for the next run, {len(work["extract_given_up"])} given up after {READ_ATTEMPTS} attempts.']
        if unavailable:
            lines.append(f'The photos of {len(unavailable)} listings could not be downloaded now, so they wait for the next run.')
        if tasks:
            lines += ['Extraction calls, in order, one delegate_task call each:',
                      json.dumps(chunks([task for _, task in tasks], PARALLEL_TASKS), ensure_ascii=False)]
        return lines, [listing for listing, _ in tasks], [record_file_name(listing) for listing, _ in tasks]

    def prepare_readings(self, work, rejected):
        """Writes the readings task file when notes or stated preferences wait for a reading, and returns the report's
        lines, the reads it hands over, every note and stated preference waiting, and the name of its readings file."""
        task_file = self.workspace / 'readings.md'
        if not work['notes'] and not work['unread']:
            task_file.unlink(missing_ok=True)
            return [], [], []
        file = self.inbox / 'readings.json'
        self.workspace.mkdir(parents=True, exist_ok=True)
        task_file.write_text(Template(READING_TEMPLATE.read_text(encoding='utf-8')).substitute(
            readings_json=json.dumps(self.readings(work), ensure_ascii=False, indent=1), readings_file=file.as_posix(),
            correction=correction(file, rejected.get(file.name))), encoding='utf-8')
        reads = [note_read(note) for note in work['notes']] + [preference_read(*item) for item in work['unread']]
        return [f'{len(work["notes"])} notes and {len(work["unread"])} stated preferences wait for a reading.',
                f'Readings task file: {task_file.as_posix()}'], reads, [file.name]

    def prepare_judgments(self, work, subagents, rejected):
        """Writes the task file of one judgment subagent per batch of JUDGMENT_BATCH listings waiting for judgments of
        preferences stated after their extraction, for at most subagents batches, and returns the report's lines with
        their delegate_task calls, the reads the batches hand over and the names of the judgments files to keep. A batch
        whose judgments file was rejected is handed again first, under its name, with the error, and when the wake has no
        subagent left for it, its file and batch stay for a later run. Listing text stays in the task files, out of the
        agent's conversation."""
        directory = self.workspace / 'judgments'
        again = [name.removeprefix('judgments-').removesuffix('.json') for name in rejected if name.startswith('judgments-')]
        every = batches([(listing['id'], (listing, phrases)) for listing, phrases in work['judge']], JUDGMENT_BATCH,
                        load_batches(directory), again)
        named = dict(list(every.items())[:max(subagents, 0)])
        shutil.rmtree(directory, ignore_errors=True)
        if not work['judge']:
            return [], [], []
        save_batches(directory, every, lambda item: item[0]['id'])
        tasks = [self.judgment_task(directory / f'{name}.md', batch, work['records'], rejected.get(f'judgments-{name}.json'))
                 for name, batch in named.items()]
        lines = [f'{len(work["judge"])} listings wait for judgments of preferences stated after their extraction: '
                 f'{sum(len(batch) for batch in named.values())} in the tasks below, the rest wait for a later run.']
        if tasks:
            lines += ['Judgment calls, in order, one delegate_task call each:', json.dumps(chunks(tasks, PARALLEL_TASKS), ensure_ascii=False)]
        reads = [judgment_read(listing, phrase) for batch in named.values() for listing, phrases in batch for phrase in phrases]
        return lines, reads, [f'judgments-{name}.json' for name in every if name in named or name in again]

    def deliver_compare_invitation(self):
        """Delivers the Compare invitation once it is due and neither the gateway nor the gallery has: through the
        messaging gateway when it is connected and no conversation has asked yet, otherwise in the gallery, whose
        invitation the next conversation passes on. A send or gateway check that fails is recorded in the invitation's
        record, so a conversation asks while the invitation stays pending and the next run tries again, and the
        report's line about it is returned. The record is the only place that holds it, so the first delivery on any
        surface ends it."""
        try:
            invitation = self.store.compare_invitation()
            if not invitation['due'] or {'gateway', 'gallery'} & set(invitation['delivered']):
                return []
            surface = 'gallery'
            try:
                if 'conversation' not in invitation['delivered'] and usable('gateway'):
                    tell_user(COMPARE_INVITATION)
                    surface = 'gateway'
            except Exception as error:
                self.store.fail_compare_invitation(failure(error))
                raise
            self.store.deliver_compare_invitation(surface)
            return []
        except Exception as error:
            return [f'The invitation to answer questions in the Compare tab could not be delivered, so the next conversation '
                    f'asks and the next run tries again. {failure(error)}']

    def refresh_stations(self):
        """Fetches the rapid-transit stations of the transit feed once, and again only when the feed changes. A feed is tens
        of megabytes, so a failed fetch is tried again after STATION_RETRY."""
        feed = self.store.transit_feed()
        if feed['fetched_at']:
            return []
        now = self.now()
        failure = self.state.load()['stations_unavailable']
        failure = failure if failure and failure['url'] == feed['url'] else None
        if failure and now - datetime.fromisoformat(failure['tried']) < STATION_RETRY:
            return []
        try:
            stations = self.stations(feed['url'])
        except SourceUnavailable as error:
            self.state.update(stations_unavailable={'url': feed['url'], 'since': failure['since'] if failure else now.isoformat(),
                                                    'tried': now.isoformat(), 'reason': str(error)})
            return [f'{error} Distances to stations stay unknown, and the next run after {now + STATION_RETRY:%Y-%m-%d %H:%M} UTC tries again.']
        self.store.save_stations(feed['url'], stations)
        self.state.update(stations_unavailable=None)
        return [f'{len(stations)} rapid-transit stations fetched from {feed["url"]}.']

    def compare_asked(self):
        """Records that a conversation asked the user to answer Compare questions, once."""
        return {'recorded': self.store.deliver_compare_invitation('conversation')}

    def ask_to_compare(self):
        """Whether a conversation should ask the user to answer Compare questions, from the invitation's record: it is
        due, neither the gateway nor a conversation has delivered it, and the gallery has, the job's last attempt failed,
        or the job's next run will deliver it in the gallery, because the gateway is not connected. The gateway is
        checked only while the record says nothing else, so a conversation between the first records and the job's
        next run asks too. A gateway check that fails counts as no gateway, since the job's run could not send through
        it either."""
        invitation = self.store.compare_invitation()
        delivered = set(invitation['delivered'])
        if not invitation['due'] or delivered & {'gateway', 'conversation'}:
            return False
        if 'gallery' in delivered or invitation['failed']:
            return True
        try:
            return not usable('gateway')
        except RuntimeError:
            return True

    @staticmethod
    def reads(work):
        """Every model read that waits, given up or not."""
        reads = {extraction_read(listing['id'], listing['content_fingerprint']) for listing in work['extract'] + work['extract_given_up']}
        reads |= {note_read(note) for note in work['notes'] + work['notes_given_up']}
        reads |= {preference_read(*item) for item in work['unread'] + work['unread_given_up']}
        return reads | {judgment_read(listing, phrase) for listing, phrases in work['judge'] + work['judge_given_up'] for phrase in phrases}

    def tag_lines(self):
        """The tag list, most used first, as the extraction task shows it."""
        tags = sorted(self.tag_counts(self.store.feature_records()).items(), key=lambda item: (-item[1], item[0]))
        return [f'{tag} ({count} listing{"" if count == 1 else "s"})' for tag, count in tags]

    def tag_counts(self, records):
        """Every tag of the tag list with the number of listings whose current record uses it."""
        used = Counter(tag for record in records.values() for field in ('traits', 'concerns') for tag in record['extraction'][field])
        return {tag: used[tag] for tag in self.store.tags()}

    def prepare(self, listing, phrases, tags, rejection, *, leave_out_failing):
        """Writes the child's task in the listing content's own directory. Its photos and sheets are prepared once per
        content, and the task file is written again on every run, so it always has the current preferences, the tag
        list as the saved records left it and the error of its last record when that was rejected."""
        directory = work_directory(self.workspace, listing['id'], listing['content_fingerprint'])
        manifest_file = directory / 'task.json'
        if manifest_file.exists():
            manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        else:
            manifest = {'id': listing['id'], 'fingerprint': listing['content_fingerprint'],
                        **self.prepare_photos(listing, directory, leave_out_failing=leave_out_failing)}
        files = [directory / name for name in manifest['files']]
        task_file = directory / 'task.md'
        task_file.write_text(self.task_text(listing, phrases, files, tags, self.inbox / record_file_name(listing), rejection), encoding='utf-8')
        manifest_file.write_text(json.dumps({**manifest, 'phrases': phrases}, ensure_ascii=False), encoding='utf-8')
        return {'goal': f'Extract the feature record of listing {listing["id"]}. Read your task file {task_file.as_posix()} with '
                        'read_file first and follow it exactly. The attached images are the contact sheets it describes.',
                'images': [(directory / name).as_posix() for name in manifest['sheets']]}

    def prepare_photos(self, listing, directory, *, leave_out_failing):
        """Downloads the listing's photos, keeps the unique ones and tiles them into contact sheets in directory. Returns
        the URLs of the unique photos in the order the sheets number them, and the names of the photo and sheet files.
        A photo that cannot be downloaded now raises PhotosUnavailable, unless leave_out_failing and another photo loaded."""
        from photos import contact_sheets, distinct_photos, without_near_duplicates
        from PIL import Image

        def attempt(url):
            try:
                return self.fetch(url), None
            except PhotosUnavailable as error:
                return None, error

        urls = distinct_photos(listing['photos'])
        with ThreadPoolExecutor(DOWNLOADS) as pool:
            results = dict(zip(urls, pool.map(attempt, urls)))
        failing = [error for _, error in results.values() if error]
        if failing and (not leave_out_failing or len(failing) == len(urls)):
            raise PhotosUnavailable(f'{len(failing)} of {len(urls)} photos could not be downloaded, such as: {failing[0]}')
        contents = {url: content for url, (content, error) in results.items() if error is None}
        images = []
        for url in contents:
            if contents[url] is None:
                continue
            try:
                image = Image.open(io.BytesIO(contents[url]))
                image.load()
            except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
                continue
            images.append((url, image))
        unique = without_near_duplicates(images)
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(parents=True)
        files = []
        for number, (url, image) in enumerate(unique, 1):
            files.append(f'photo-{number:02}.{EXTENSIONS.get(image.format, "png")}')
            if image.format in EXTENSIONS:
                (directory / files[-1]).write_bytes(contents[url])
            else:
                image.save(directory / files[-1], 'PNG')
        sheets = []
        for number, sheet in enumerate(contact_sheets([image for _, image in unique]), 1):
            sheets.append(f'sheet-{number}.jpg')
            sheet.save(directory / sheets[-1], 'JPEG', quality=85)
        return {'photos': [url for url, _ in unique], 'files': files, 'sheets': sheets}

    @staticmethod
    def task_text(listing, phrases, files, tags, record_file, rejection):
        return Template(EXTRACTION_TEMPLATE.read_text(encoding='utf-8')).substitute(
            listing_id=listing['id'], fingerprint=listing['content_fingerprint'], photo_count=len(files),
            photo_overview=photo_overview(len(files)),
            photo_files='\n'.join(f'- Photo {number}: `{file.as_posix()}`' for number, file in enumerate(files, 1)),
            listing_json=json.dumps(listing_fields(listing, TASK_DESCRIPTION_LIMIT), ensure_ascii=False, indent=1),
            room_types=choices([f'"{room}"' for room in ROOM_TYPES]),
            rating_lines=bullet_lines(f'`{name}`: 1 {low}, 5 {high}' for name, (low, high) in RATINGS.items()),
            category_lines=bullet_lines(f'`{name}`: ' + choices([f'"{value}"' for value in values]) for name, values in CATEGORIES.items()),
            unit_levels=choices([f'"{level}"' for level in UNIT_LEVELS]),
            flags=', '.join(f'`{flag}`' for flag in FLAGS),
            tags='\n'.join(tags) or '(none yet)',
            preferences=bullet_lines(json.dumps(phrase, ensure_ascii=False) for phrase in phrases) or '  (none)',
            record_file=record_file.as_posix(), correction=correction(record_file, rejection))

    def remove_work_directories(self, keep):
        if self.workspace.exists():
            for directory in self.workspace.iterdir():
                if directory.is_dir() and directory not in keep:
                    shutil.rmtree(directory, ignore_errors=True)

    def save(self, value):
        if not isinstance(value, dict) or set(value) != {'id', 'fingerprint', 'record'}:
            raise ValueError('The file must hold a JSON object with exactly id, fingerprint and record.')
        directory = work_directory(self.workspace, *listing_key(value, 'The record'))
        try:
            manifest = json.loads((directory / 'task.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise ValueError(f'No extraction of {value["id"]} with this fingerprint is waiting: the listing changed or its record is '
                             'saved already. Stop.') from None
        result = self.store.save_extraction(value['id'], value['fingerprint'], value['record'], photos=manifest['photos'], phrases=manifest['phrases'])
        shutil.rmtree(directory, ignore_errors=True)
        return {'saved': value['id'], **result}

    def judgment_task(self, task_file, batch, records, rejection):
        judgments_file = self.inbox / f'judgments-{task_file.stem}.json'
        listings = [{'id': listing['id'], 'fingerprint': listing['content_fingerprint'], 'unjudged': phrases,
                     'listing': listing_fields(listing, JUDGMENT_DESCRIPTION_LIMIT), 'record': self.digest(records[listing['id']])}
                    for listing, phrases in batch]
        task_file.write_text(Template(JUDGMENT_TEMPLATE.read_text(encoding='utf-8')).substitute(
            count=len(batch), listings_json=json.dumps(listings, ensure_ascii=False, indent=1), judgments_file=judgments_file.as_posix(),
            correction=correction(judgments_file, rejection)), encoding='utf-8')
        return {'goal': f'Judge stated preferences for {len(batch)} listings. Read your task file {task_file.as_posix()} with read_file '
                        'first and follow it exactly.'}

    @staticmethod
    def digest(record):
        extraction = record['extraction']
        rooms = Counter(photo['room'] for photo in extraction['photos'])
        return {'summary': extraction['summary'], 'rooms_photographed': dict(rooms), 'traits': extraction['traits'],
                'concerns': extraction['concerns'], **{name: extraction[name] for name in CATEGORIES}, 'unit_level': extraction['unit_level'],
                'flags': {flag: value for flag, value in extraction['flags'].items() if value is not None},
                'ratings': {name: rating['value'] for name, rating in extraction['ratings'].items() if rating != NOT_SHOWN}}

    def judge(self, value):
        if not isinstance(value, dict) or set(value) != {'judgments'} or not isinstance(value['judgments'], list):
            raise ValueError('The file must hold a JSON object with only "judgments", an array.')
        work = self.outstanding()
        waiting = {listing['id']: (listing['content_fingerprint'], phrases) for listing, phrases in work['judge']}
        handed = self.handed_reads('preference')
        saves, stale = [], []
        for entry in value['judgments']:
            if not isinstance(entry, dict) or set(entry) != {'id', 'fingerprint', 'preferences'} or not isinstance(entry['preferences'], dict):
                raise ValueError('Each judgment needs exactly id, fingerprint and preferences, an object.')
            listing_key(entry, 'Each judgment')
            given = {phrase for _, identifier, _, phrase in handed if identifier == entry['id']}
            if entry['id'] not in waiting or waiting[entry['id']][0] != entry['fingerprint']:
                if entry['id'] not in waiting and not given:
                    raise ValueError(f'{entry["id"]} waits for no preference judgment.')
                stale.append(entry['id'])
                continue
            unjudged = waiting[entry['id']][1]
            if set(entry['preferences']) - set(unjudged) - given or set(unjudged) & given - set(entry['preferences']):
                raise ValueError(f'Judge exactly the unjudged preferences of {entry["id"]}: {json.dumps(unjudged, ensure_ascii=False)}.')
            current = {phrase: judgment for phrase, judgment in entry['preferences'].items() if phrase in unjudged}
            try:
                validate_judgments(current, list(current))
            except ValueError as error:
                raise ValueError(f'{entry["id"]}: {error}') from None
            if current:
                saves.append((entry['id'], entry['fingerprint'], current, list(current)))
            else:
                stale.append(entry['id'])
        self.store.judge_preferences(saves)
        return {'saved': len(saves), 'stale': stale}

    def handed_reads(self, kind):
        """The reads of a kind the last run handed to the agent, each as the list its key encodes. The inbox is applied
        before the run counts attempts, so these are the reads the files in the inbox were written for."""
        return [read for read in map(json.loads, self.state.load()['handed']) if read[0] == kind]

    def readings(self, work):
        """Every note and stated preference waiting for a reading, with the features a reading may name."""
        return {'vocabulary': self.store.reading_vocabulary(),
                'preferences': [{'kind': kind, 'phrase': phrase} for kind, phrase in work['unread']],
                'notes': [{'id': note['id'], 'listing': note['listing_id'], 'text': note['text']} for note in work['notes']]}

    def read(self, value):
        if not isinstance(value, dict) or set(value) != {'preferences', 'notes'} or not all(isinstance(value[key], list) for key in value):
            raise ValueError('The file must hold a JSON object with exactly "preferences" and "notes", both arrays.')
        preferences, notes = [], []
        stated = set(stated_phrases(self.store.settings()))
        given = {(kind, phrase) for _, kind, phrase in self.handed_reads('preference reading')}
        for item in value['preferences']:
            if not isinstance(item, dict) or set(item) != {'kind', 'phrase', 'features'}:
                raise ValueError('Each preference reading has exactly kind, phrase and features.')
            if not isinstance(item['kind'], str) or not isinstance(item['phrase'], str):
                raise ValueError("Each preference reading's kind and phrase are the text the task file gives.")
            if (item['kind'], item['phrase']) in stated or (item['kind'], item['phrase']) not in given:
                preferences.append((item['kind'], item['phrase'], item['features']))
        for item in value['notes']:
            if not isinstance(item, dict) or set(item) != {'id', 'sentiment', 'likes', 'dislikes'}:
                raise ValueError('Each note reading has exactly id, sentiment, likes and dislikes.')
            if type(item['id']) is not int:
                raise ValueError("Each note reading's id is the number the task file gives.")
            notes.append((item['id'], {key: item[key] for key in ('sentiment', 'likes', 'dislikes')}))
        self.store.save_readings(notes=notes, preferences=preferences)
        return {'preferences': len(preferences), 'notes': len(notes)}

    def status(self):
        state = self.state.load()
        problems = list(state['problems'])
        if self.store.missing_settings():
            return {'problems': problems, 'records': 0, 'waiting': 0, 'given_up': [], 'awaiting_judgment': 0, 'awaiting_reading': 0,
                    'photos_unavailable': {}, 'ask_to_compare': False}
        work = self.outstanding()
        given_up = [listing['id'] for listing in work['extract_given_up']]
        if given_up:
            problems.append(f'Extraction failed {READ_ATTEMPTS} times for {len(given_up)} listings, so they are ranked only from what code reads '
                            f'about them until their content changes or the user asks to retry: {", ".join(given_up)}.')
        if work['judge_given_up']:
            problems.append(f'Preferences could not be judged in {READ_ATTEMPTS} attempts for {len(work["judge_given_up"])} listings, so those '
                            f'preferences do not count for them until the user asks to retry: '
                            f'{", ".join(listing["id"] for listing, _ in work["judge_given_up"])}.')
        if work['notes_given_up'] or work['unread_given_up']:
            problems.append(f'{len(work["notes_given_up"])} notes and {len(work["unread_given_up"])} stated preferences could not be read in '
                            f'{READ_ATTEMPTS} attempts, so they do not count in the ranking until the user asks to retry.')
        now = self.now()
        if state['stations_unavailable']:
            failure = state['stations_unavailable']
            problems.append(f'The rapid-transit stations of {failure["url"]} could not be fetched since '
                            f'{datetime.fromisoformat(failure["since"]):%Y-%m-%d %H:%M} UTC, so no listing has a distance to a station. '
                            f'The job tries again every {STATION_RETRY // timedelta(hours=1)} hours. {failure["reason"]}')
        stalled = {identifier: entry for identifier, entry in state['photos_unavailable'].items()
                   if now - datetime.fromisoformat(entry['since']) >= PHOTO_PATIENCE}
        if stalled:
            problems.append(f'No photo of {len(stalled)} listings could be downloaded for over an hour, so they have no feature record '
                            'and are not ranked yet. The job keeps trying every 10 minutes: '
                            + '; '.join(f'{identifier} since {datetime.fromisoformat(entry["since"]):%Y-%m-%d %H:%M} UTC ({entry["reason"]})'
                                        for identifier, entry in stalled.items()) + '.')
        return {'problems': problems, 'records': len(work['records']), 'waiting': len(work['extract']), 'given_up': given_up,
                'awaiting_judgment': len(work['judge']), 'awaiting_reading': len(work['notes']) + len(work['unread']),
                'photos_unavailable': state['photos_unavailable'], 'ask_to_compare': self.ask_to_compare()}

    def retry(self):
        self.store.reset_read_attempts()


def open_features():
    path = store_path()
    return Features(ListingStore(path), RunState(path.parent / 'features.json'), path.parent / 'extraction', download,
                    lambda: datetime.now(timezone.utc), fetch_stations)


def run():
    """The cron job's command. Every failure ends here, recorded for status, so Hermes does not wake the model for it."""
    state = RunState(store_path().parent / 'features.json')
    try:
        lines, wake = open_features().run()
    except Exception as error:
        lines, wake = [f'Extraction failed. {failure(error)}'], False
        try:
            state.update(problems=lines)
        except Exception as record_error:
            lines.append(f'The failure could not be recorded for status. {failure(record_error)}')
    print('\n'.join([*lines, json.dumps({'wakeAgent': wake})]))
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description='Keep a feature record for every listing the ranking needs.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('run')
    commands.add_parser('tags')
    commands.add_parser('status')
    commands.add_parser('retry')
    commands.add_parser('compare-asked')
    arguments = parser.parse_args(argv)
    if arguments.command == 'run':
        return run()
    try:
        features = open_features()
        if arguments.command == 'status':
            report = features.status()
            print(json.dumps(report, ensure_ascii=False))
            return ATTENTION if report['problems'] else 0
        if arguments.command == 'compare-asked':
            print(json.dumps(features.compare_asked()))
        elif arguments.command == 'retry':
            features.retry()
            print('Every extraction and judgment given up is tried again from the next run.')
        else:
            print('\n'.join(features.tag_lines()))
        return 0
    except (ValueError, KeyError, OSError, sqlite3.Error, RuntimeError) as error:
        print(error.args[0] if isinstance(error, KeyError) else error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
