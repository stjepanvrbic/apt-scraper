"""Keeps a feature record for every listing the ranking needs, read once per listing content.

Commands:
  features.py run           one pass of the scheduled job apt-scraper-features: fetches the rapid-transit stations
                            once per transit feed, prepares the photos of the listings waiting for extraction, prints
                            one extraction task per listing for the agent, and ends with a line that tells Hermes
                            whether to wake the agent. Never fails.
  features.py save FILE     the extraction child's command: save one record from a JSON file in the scratch directory,
                            then delete the file
  features.py tags          the tag list, most used first, which the extraction child reads before choosing tags
  features.py preferences   the next listings waiting for judgments of preferences stated after their extraction
  features.py judge FILE    save those judgments from a JSON file in the scratch directory, then delete it
  features.py readings      the user's notes and stated preferences waiting to be read into record features
  features.py read FILE     save those readings from a JSON file in the scratch directory, then delete it
  features.py status        whether extraction needs the user's attention
  features.py retry         try again every extraction and judgment given up after 3 attempts
  features.py invite-compare ask for the first 5 to 8 pairwise answers through the messaging gateway, once ready

Exit codes: 0 done (for status: no problem), 3 extraction needs attention or the invitation gateway is not connected,
1 the input or a step failed, 2 usage error.

Hermes wakes the model whenever a cron script fails (cron/scheduler_prompt.py, "Script Error"), so run records a
failure for status and ends with {"wakeAgent": false} instead.
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

from contact import NotConnected, tell_user, usable  # noqa: E402
from geodata import SourceUnavailable, fetch_stations  # noqa: E402
from record import CATEGORIES, FLAGS, NOT_SHOWN, RATINGS, ROOM_TYPES, UNIT_LEVELS, choices, validate_judgments  # noqa: E402
from scoring import stated_phrases  # noqa: E402
from scratch import read_scratch_object, scratch_directory  # noqa: E402
from store import READ_ATTEMPTS, ListingStore, extraction_read, store_path  # noqa: E402

ATTENTION = 3
SEQUENTIAL_RECORDS = 5
PARALLEL_TASKS = 10
SUBAGENTS_PER_WAKE = 50
PREPARE_BUDGET = timedelta(minutes=10)
PHOTO_PATIENCE = timedelta(hours=1)
STATION_RETRY = timedelta(hours=6)
DOWNLOADS = 8
JUDGMENT_BATCH = 25
NOTE_BATCH = 25
TASK_DESCRIPTION_LIMIT = 6000
JUDGMENT_DESCRIPTION_LIMIT = 2000
TEMPLATE = Path(__file__).resolve().parents[1] / 'extraction.md'
MEDIA = SKILLS / 'gallery' / 'web' / 'media'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36'
EXTENSIONS = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp', 'GIF': 'gif'}


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


def labeled(listing):
    return bool(listing['favorite'] or listing['dismissed'] or listing['contact'] or any(note['role'] == 'user' for note in listing['notes']))


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


def price_text(listing):
    if listing.get('price') is None:
        return listing.get('price_note') or 'not listed'
    return ' '.join(str(part) for part in (listing['price'], listing.get('currency'), listing.get('rental_period') and f'a {listing["rental_period"]}') if part)


def listing_fields(listing, limit):
    """What a listing site says about the listing, as data for a model. It enters a prompt only JSON-encoded, so no line
    of a title or description can pass for a line of the instructions around it."""
    description = listing.get('description') or None
    if description and len(description) > limit:
        description = description[:limit] + ' [shortened]'
    return {'title': listing.get('title'), 'address': listing.get('address'), 'price': price_text(listing), 'rooms': listing.get('rooms'),
            'size': listing.get('size'), 'amenities': listing.get('amenities') or [], 'description': description}


def bullet_lines(items):
    return '\n'.join(f'  - {item}' for item in items)


def photo_overview(count):
    if not count:
        return 'This listing has no photos. Read it from its text and give every rating that needs a photo as "not shown".'
    ranges = [f'sheet {sheet + 1} shows photos {sheet * 9 + 1} to {min(count, sheet * 9 + 9)}' for sheet in range((count + 8) // 9)]
    return (f'The contact sheets attached to your task show all {count} unique photos of the listing, in order: '
            f'{", ".join(ranges)}. Each photo has its number in yellow in its top-left corner. Look at every photo on the sheets.')


class RunState:
    """The job's records next to the listing store: the last run's failure, the listings whose photos could not be
    downloaded, since when and why, the transit feed whose stations could not be fetched, since when, when last tried
    and why, and the model reads the last run handed to the agent. A read handed over that still
    waits at the next run counts as a failed attempt then, so an attempt counts only once its wake has ended."""

    def __init__(self, path):
        self.path = path

    def load(self):
        return {'problem': None, 'photos_unavailable': {}, 'stations_unavailable': None, 'handed': [],
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

    def outstanding(self):
        """The listings without a record of their current content, and the extracted listings with stated preferences not
        judged yet, each split into those still given to the agent and those given up after READ_ATTEMPTS attempts. Listings the
        user labeled come first, then the longest known."""
        snapshot = self.store.state()
        records, attempts = self.store.feature_records(), self.store.read_attempts()
        readings = self.store.preference_readings()
        phrases = extracted_preferences(snapshot['settings'], readings)
        later = judged_preferences(snapshot['settings'], readings)
        listings = sorted((listing for listing in snapshot['listings'] if needs_record(listing)),
                          key=lambda listing: (not labeled(listing), listing['first_seen'], listing['id']))
        extract = [listing for listing in listings if listing['id'] not in records]
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
            'extract_given_up': [listing for listing in extract if attempts.get(extraction_read(listing['id'], listing['content_fingerprint']), 0) >= READ_ATTEMPTS],
            'judge': [(listing, [phrase for phrase in missing if attempts.get(judgment_read(listing, phrase), 0) < READ_ATTEMPTS])
                      for listing, missing in judge if any(attempts.get(judgment_read(listing, phrase), 0) < READ_ATTEMPTS for phrase in missing)],
            'judge_given_up': [(listing, [phrase for phrase in missing if attempts.get(judgment_read(listing, phrase), 0) >= READ_ATTEMPTS])
                               for listing, missing in judge if any(attempts.get(judgment_read(listing, phrase), 0) >= READ_ATTEMPTS for phrase in missing)],
        }

    def run(self):
        """One pass of the job. A wake holds at most SUBAGENTS_PER_WAKE extractions, because Hermes blocks the agent's
        delegate_task once one turn has spawned that many subagents (tool_loop_guardrails.loop_caps.max_subagents in
        hermes_cli/config_defaults.py), which would end the wake before its readings and judgments."""
        if self.store.missing_settings():
            self.state.update(problem=None)
            return ['The interview is not complete, so no listing is extracted.'], False
        station_lines = self.refresh_stations()
        state = self.state.load()
        waiting = self.reads(self.outstanding())
        self.store.record_read_attempts([read for read in state['handed'] if read in waiting], waiting)
        work = self.outstanding()
        now = self.now()
        deadline = now + PREPARE_BUDGET
        tasks, unavailable, deferred = [], {}, 0
        for listing in work['extract']:
            if self.now() >= deadline or len(tasks) == SUBAGENTS_PER_WAKE:
                deferred += 1
                continue
            since = state['photos_unavailable'].get(listing['id'], {}).get('since')
            overdue = since is not None and now - datetime.fromisoformat(since) >= PHOTO_PATIENCE
            try:
                tasks.append((listing, self.prepare(listing, work['phrases'], leave_out_failing=overdue)))
            except PhotosUnavailable as error:
                unavailable[listing['id']] = {'since': since or now.isoformat(), 'reason': str(error)}
        handed = [extraction_read(listing['id'], listing['content_fingerprint']) for listing, _ in tasks]
        handed += [judgment_read(listing, phrase) for listing, phrases in work['judge'] for phrase in phrases]
        handed += [note_read(note) for note in work['notes']] + [preference_read(*item) for item in work['unread']]
        self.state.update(problem=None, photos_unavailable=unavailable, handed=handed)
        self.remove_work_directories({work_directory(self.workspace, listing['id'], listing['content_fingerprint'])
                                      for listing in work['extract']})
        lines = [*station_lines, f'{len(work["extract"])} listings wait for extraction: {len(tasks)} prepared below, {deferred + len(unavailable)} wait '
                 f'for the next run, {len(work["extract_given_up"])} given up after {READ_ATTEMPTS} attempts.']
        if unavailable:
            lines.append(f'The photos of {len(unavailable)} listings could not be downloaded now, so they wait for the next run.')
        if tasks:
            lines += ['Extraction calls, in order, one delegate_task call each:',
                      json.dumps(self.calls([task for _, task in tasks], len(work['records'])), ensure_ascii=False)]
        if work['notes'] or work['unread']:
            lines.append(f'{len(work["notes"])} notes and {len(work["unread"])} stated preferences wait for a reading.')
        if work['judge']:
            lines.append(f'{len(work["judge"])} listings wait for judgments of preferences stated after their extraction.')
        if self.compare_invitation_pending() and usable('gateway'):
            self.invite_compare()
        return lines, bool(tasks or work['judge'] or work['notes'] or work['unread'])

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

    def compare_invitation_pending(self):
        if self.state.load().get('compare_invited_at') or self.store.missing_settings():
            return False
        inputs = self.store.ranking_inputs()
        return not inputs['pairs'] and sum(
            listing['rankable'] and listing['record'] is not None and not listing['id'].startswith('sample:')
            for listing in inputs['listings']) >= 2

    def invite_compare(self):
        if not self.compare_invitation_pending():
            return {'sent': False}
        tell_user('Your first apartments are ready to compare. Please answer 5 to 8 questions in the gallery\'s Compare tab. '
                  'For each pair, choose the apartment you prefer, or skip if you cannot decide. '
                  'Your choices will count the next time you ask me to score and rerank.')
        self.state.update(compare_invited_at=self.now().isoformat())
        return {'sent': True}

    @staticmethod
    def reads(work):
        """Every model read that waits, given up or not."""
        reads = {extraction_read(listing['id'], listing['content_fingerprint']) for listing in work['extract'] + work['extract_given_up']}
        reads |= {note_read(note) for note in work['notes'] + work['notes_given_up']}
        reads |= {preference_read(*item) for item in work['unread'] + work['unread_given_up']}
        return reads | {judgment_read(listing, phrase) for listing, phrases in work['judge'] + work['judge_given_up'] for phrase in phrases}

    @staticmethod
    def calls(tasks, records):
        """The tasks grouped into delegate_task calls. Subagents of one call run at once and choose their tags from the
        same tag list, so while the store has fewer than SEQUENTIAL_RECORDS records, each call runs one task and the next
        sees the tags it added. The rest run PARALLEL_TASKS at a time, Hermes's default delegation.max_concurrent_children."""
        single = max(0, SEQUENTIAL_RECORDS - records)
        return [[task] for task in tasks[:single]] + [tasks[start:start + PARALLEL_TASKS] for start in range(single, len(tasks), PARALLEL_TASKS)]

    def tag_lines(self):
        """The tag list, most used first, as the extraction subagent reads it."""
        tags = sorted(self.tag_counts(self.store.feature_records()).items(), key=lambda item: (-item[1], item[0]))
        return [f'{tag} ({count} listing{"" if count == 1 else "s"})' for tag, count in tags]

    def tag_counts(self, records):
        """Every tag of the tag list with the number of listings whose current record uses it."""
        used = Counter(tag for record in records.values() for field in ('traits', 'concerns') for tag in record['extraction'][field])
        return {tag: used[tag] for tag in self.store.tags()}

    def prepare(self, listing, phrases, *, leave_out_failing):
        """Writes the child's task in the listing content's own directory. Its photos and sheets are prepared once per
        content, and the task file is written again on every run, so it always has the current preferences."""
        directory = work_directory(self.workspace, listing['id'], listing['content_fingerprint'])
        manifest_file = directory / 'task.json'
        if manifest_file.exists():
            manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        else:
            manifest = {'id': listing['id'], 'fingerprint': listing['content_fingerprint'],
                        **self.prepare_photos(listing, directory, leave_out_failing=leave_out_failing)}
        files = [directory / name for name in manifest['files']]
        record_file = scratch_directory() / f'extraction-{directory.name}.json'
        task_file = directory / 'task.md'
        task_file.write_text(self.task_text(listing, phrases, files, record_file), encoding='utf-8')
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
    def task_text(listing, phrases, files, record_file):
        script = Path(__file__).resolve().as_posix()
        return Template(TEMPLATE.read_text(encoding='utf-8')).substitute(
            listing_id=listing['id'], fingerprint=listing['content_fingerprint'], photo_count=len(files),
            photo_overview=photo_overview(len(files)),
            photo_files='\n'.join(f'- Photo {number}: `{file.as_posix()}`' for number, file in enumerate(files, 1)),
            listing_json=json.dumps(listing_fields(listing, TASK_DESCRIPTION_LIMIT), ensure_ascii=False, indent=1),
            room_types=choices([f'"{room}"' for room in ROOM_TYPES]),
            rating_lines=bullet_lines(f'`{name}`: 1 {low}, 5 {high}' for name, (low, high) in RATINGS.items()),
            category_lines=bullet_lines(f'`{name}`: ' + choices([f'"{value}"' for value in values]) for name, values in CATEGORIES.items()),
            unit_levels=choices([f'"{level}"' for level in UNIT_LEVELS]),
            flags=', '.join(f'`{flag}`' for flag in FLAGS),
            tags_command=f'python "{script}" tags',
            preferences=bullet_lines(json.dumps(phrase, ensure_ascii=False) for phrase in phrases) or '  (none)',
            record_file=record_file.as_posix(),
            save_command=f'python "{script}" save "{record_file.as_posix()}"')

    def remove_work_directories(self, keep):
        if self.workspace.exists():
            for directory in self.workspace.iterdir():
                if directory.is_dir() and directory not in keep:
                    shutil.rmtree(directory, ignore_errors=True)

    def save(self, value):
        if not isinstance(value, dict) or set(value) != {'id', 'fingerprint', 'record'}:
            raise ValueError('The file must hold a JSON object with exactly id, fingerprint and record.')
        if not isinstance(value['id'], str) or not isinstance(value['fingerprint'], str):
            raise ValueError('id and fingerprint must be the text given in the task file.')
        directory = work_directory(self.workspace, value['id'], value['fingerprint'])
        try:
            manifest = json.loads((directory / 'task.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            raise ValueError(f'No extraction of {value["id"]} with this fingerprint is waiting: the listing changed or its record is '
                             'saved already. Stop.') from None
        result = self.store.save_extraction(value['id'], value['fingerprint'], value['record'], photos=manifest['photos'], phrases=manifest['phrases'])
        shutil.rmtree(directory, ignore_errors=True)
        return {'saved': value['id'], **result}

    def preferences(self):
        work = self.outstanding()
        batch = work['judge'][:JUDGMENT_BATCH]
        return {'listings': [{'id': listing['id'], 'fingerprint': listing['content_fingerprint'], 'unjudged': phrases,
                              'listing': listing_fields(listing, JUDGMENT_DESCRIPTION_LIMIT), 'record': self.digest(work['records'][listing['id']])}
                             for listing, phrases in batch],
                'remaining': len(work['judge']) - len(batch)}

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
        for entry in value['judgments']:
            if not isinstance(entry, dict) or set(entry) != {'id', 'fingerprint', 'preferences'} or not isinstance(entry['preferences'], dict):
                raise ValueError('Each judgment needs exactly id, fingerprint and preferences, an object.')
            if entry['id'] not in waiting:
                raise ValueError(f'{entry["id"]} waits for no preference judgment.')
            missing = set(waiting[entry['id']][1]) - set(entry['preferences'])
            extra = set(entry['preferences']) - set(waiting[entry['id']][1])
            if missing or extra:
                raise ValueError(f'Judge exactly the unjudged preferences of {entry["id"]}: {json.dumps(waiting[entry["id"]][1], ensure_ascii=False)}.')
            try:
                validate_judgments(entry['preferences'], waiting[entry['id']][1])
            except ValueError as error:
                raise ValueError(f'{entry["id"]}: {error}') from None
        current = [entry for entry in value['judgments'] if waiting[entry['id']][0] == entry['fingerprint']]
        self.store.judge_preferences([(entry['id'], entry['fingerprint'], entry['preferences'], list(entry['preferences'])) for entry in current])
        return {'saved': len(current), 'stale': [entry['id'] for entry in value['judgments'] if entry not in current]}

    def readings(self):
        """The next notes and every stated preference waiting for a reading, with the features a reading may name."""
        work = self.outstanding()
        batch = work['notes'][:NOTE_BATCH]
        return {'vocabulary': self.store.reading_vocabulary(),
                'preferences': [{'kind': kind, 'phrase': phrase} for kind, phrase in work['unread']],
                'notes': [{'id': note['id'], 'listing': note['listing'], 'text': note['text']} for note in batch],
                'remaining': len(work['notes']) - len(batch)}

    def read(self, value):
        if not isinstance(value, dict) or set(value) != {'preferences', 'notes'} or not all(isinstance(value[key], list) for key in value):
            raise ValueError('The file must hold a JSON object with exactly "preferences" and "notes", both arrays.')
        preferences, notes = [], []
        for item in value['preferences']:
            if not isinstance(item, dict) or set(item) != {'kind', 'phrase', 'features'}:
                raise ValueError('Each preference reading has exactly kind, phrase and features.')
            preferences.append((item['kind'], item['phrase'], item['features']))
        for item in value['notes']:
            if not isinstance(item, dict) or set(item) != {'id', 'sentiment', 'likes', 'dislikes'}:
                raise ValueError('Each note reading has exactly id, sentiment, likes and dislikes.')
            notes.append((item['id'], {key: item[key] for key in ('sentiment', 'likes', 'dislikes')}))
        self.store.save_readings(notes=notes, preferences=preferences)
        return {'preferences': len(preferences), 'notes': len(notes)}

    def status(self):
        state = self.state.load()
        problems = [state['problem']] if state['problem'] else []
        if self.store.missing_settings():
            return {'problems': problems, 'records': 0, 'waiting': 0, 'given_up': [], 'awaiting_judgment': 0, 'awaiting_reading': 0,
                    'photos_unavailable': {}}
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
                'photos_unavailable': state['photos_unavailable']}

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
            state.update(problem=lines[0])
        except Exception as record_error:
            lines.append(f'The failure could not be recorded for status. {failure(record_error)}')
    print('\n'.join([*lines, json.dumps({'wakeAgent': wake})]))
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description='Keep a feature record for every listing the ranking needs.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('run')
    saving = commands.add_parser('save')
    saving.add_argument('file', type=Path, help='JSON object {"id", "fingerprint", "record"} in the scratch directory. Deleted once saved.')
    commands.add_parser('tags')
    commands.add_parser('preferences')
    judging = commands.add_parser('judge')
    judging.add_argument('file', type=Path, help='JSON object {"judgments": [{id, fingerprint, preferences}]} in the scratch directory. Deleted once saved.')
    commands.add_parser('readings')
    reading = commands.add_parser('read')
    reading.add_argument('file', type=Path, help='JSON object {"preferences": [...], "notes": [...]} in the scratch directory. Deleted once saved.')
    commands.add_parser('status')
    commands.add_parser('retry')
    commands.add_parser('invite-compare')
    arguments = parser.parse_args(argv)
    if arguments.command == 'run':
        return run()
    try:
        features = open_features()
        if arguments.command == 'status':
            report = features.status()
            print(json.dumps(report, ensure_ascii=False))
            return ATTENTION if report['problems'] else 0
        if arguments.command == 'invite-compare':
            print(json.dumps(features.invite_compare()))
        elif arguments.command == 'retry':
            features.retry()
            print('Every extraction and judgment given up is tried again from the next run.')
        elif arguments.command == 'tags':
            print('\n'.join(features.tag_lines()))
        elif arguments.command == 'preferences':
            print(json.dumps(features.preferences(), ensure_ascii=False, indent=1))
        elif arguments.command == 'readings':
            print(json.dumps(features.readings(), ensure_ascii=False, indent=1))
        else:
            names = {'save': ('The record', features.save), 'judge': ('The judgments', features.judge), 'read': ('The readings', features.read)}
            name, command = names[arguments.command]
            file, value = read_scratch_object(arguments.file, name)
            print(json.dumps(command(value), ensure_ascii=False))
            file.unlink()
        return 0
    except NotConnected as error:
        print(str(error), file=sys.stderr)
        return 3
    except (ValueError, KeyError, OSError, sqlite3.Error, RuntimeError) as error:
        print(error.args[0] if isinstance(error, KeyError) else error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
