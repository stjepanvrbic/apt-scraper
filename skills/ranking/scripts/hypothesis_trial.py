"""Prepare and apply a hypothesis trial on an explicit working database copy."""

import argparse
import json
import sys
from pathlib import Path
from string import Template
from typing import TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gallery/scripts'))

from hypotheses import active, assess, has_new_labels, pending_judgments, prepare, save, save_judgments  # noqa: E402
from record import Extraction  # noqa: E402
from review import CONNECTING_WORDS, MODEL_WORDS  # noqa: E402
from rerank import rerank  # noqa: E402
from shapes import is_array, is_object, loads  # noqa: E402
from store import ListingStore  # noqa: E402
from store_copy import copy_database  # noqa: E402
from extraction_retest import CacheManifest, MissingCachedPhotos, cached_files  # noqa: E402
from features import ListingFields, listing_fields  # noqa: E402

SKILLS = Path(__file__).resolve().parents[2]


class NoPass(TypedDict):
    """No hypothesis pass is requested, and why."""
    requested_passes: int
    reason: str


class ProposalPass(TypedDict):
    """The one hypothesis pass requested: its task file, where the review goes and how many proposals it may make."""
    requested_passes: int
    task: str
    output: str
    max_hypotheses: int


class Expected(TypedDict):
    """What a judgment file must cover for one listing: its content and the hypotheses to judge, id by phrase."""
    fingerprint: str
    hypotheses: dict[str, str]


class JudgmentFile(TypedDict):
    """One judgment task, as judgment-tasks.json keeps it."""
    task: str
    output: str
    expected: dict[str, Expected]
    images: list[str]
    listings: int


class JudgedListing(TypedDict):
    """A listing as its judgment task shows it: the content fields features.listing_fields gives and the record."""
    id: str
    fingerprint: str
    unjudged: list[str]
    listing: ListingFields
    record: Extraction | None


class JudgmentReport(TypedDict):
    tasks: list[JudgmentFile]
    visual_waiting_for_cached_photos: list[str]
    visual_listing_reads: int
    text_batches: int


class SavedJudgments(TypedDict):
    saved: int


def proposal_file(store: ListingStore, directory: Path) -> NoPass | ProposalPass:
    task = prepare(store)
    if task is None:
        return {'requested_passes': 0, 'reason': 'No unreviewed new labels.'}
    written = False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / f'review-{task["rerank"]}.json'
        text = Template((SKILLS / 'ranking/hypothesis_review.md').read_text(encoding='utf-8')).substitute(
            connecting_words=', '.join(sorted(CONNECTING_WORDS)), model_words=', '.join(sorted(MODEL_WORDS)),
            output=output.as_posix(), task=json.dumps(task, ensure_ascii=False, indent=2))
        path = directory / f'review-{task["rerank"]}.md'
        _ = path.write_text(text, encoding='utf-8')
        written = True
    finally:
        if not written:
            with store.connect(write=True) as connection:
                _ = connection.execute('DELETE FROM hypothesis_passes WHERE rerank_id = ? AND saved = 0', (task['rerank'],))
    return {'requested_passes': 1, 'task': str(path), 'output': str(output), 'max_hypotheses': task['max_hypotheses']}


def judgment_files(store: ListingStore, directory: Path, cache: Path | None = None) -> JudgmentReport:
    directory.mkdir(parents=True, exist_ok=True)
    cached: dict[tuple[str, str], tuple[Path, Path, CacheManifest]] = {}
    if cache:
        for path in cache.rglob('task.json'):
            manifest = cast(CacheManifest, loads(path.read_text(encoding='utf-8')))
            cached[manifest['id'], manifest['fingerprint']] = (cache, path, manifest)
    tasks: list[JudgmentFile] = []
    waiting: list[str] = []
    template = (SKILLS / 'listing-features/judgment.md').read_text(encoding='utf-8')
    template = template.replace('The user stated likes and dislikes after these listings were extracted.',
                                'These phrases are unconfirmed hypotheses, not stated preferences.')
    for index, task in enumerate(pending_judgments(store)):
        images: list[Path] = []
        full_photos: list[Path] = []
        if task['photos_required']:
            listing = task['listings'][0]
            found = cached.get((listing['id'], listing['fingerprint']))
            if not found:
                waiting.append(listing['id'])
                continue
            root, manifest_file, manifest = found
            try:
                images = cached_files(root, manifest_file, manifest['sheets'])
                full_photos = cached_files(root, manifest_file, manifest['files'])
            except MissingCachedPhotos:
                waiting.append(listing['id'])
                continue
            if not images or not full_photos:
                waiting.append(listing['id'])
                continue
        output = directory / f'judgments-{index}.json'
        listings: list[JudgedListing] = [{'id': item['id'], 'fingerprint': item['fingerprint'], 'unjudged': list(item['unjudged']),
                                          'listing': listing_fields(item['listing']), 'record': item['record']}
                                         for item in task['listings']]
        text = Template(template).substitute(count=len(listings), correction='', listings_json=json.dumps(listings, indent=2), judgments_file=output.as_posix())
        if images:
            text = ('This is a visual hypothesis. Re-read these cached photo sheets; do not infer its presence from text alone. '
                    + 'No URL may be fetched. Open at most six full-resolution photos if a detail needs it.\nSheets:\n'
                    + '\n'.join(str(path) for path in images) + '\nFull photos:\n'
                    + '\n'.join(str(path) for path in full_photos) + '\n\n' + text)
        path = directory / f'judgments-{index}.md'
        _ = path.write_text(text, encoding='utf-8')
        tasks.append({'task': str(path), 'output': str(output),
                      'expected': {item['id']: {'fingerprint': item['fingerprint'], 'hypotheses': item['unjudged']} for item in task['listings']},
                      'images': [str(path) for path in images], 'listings': len(listings)})
    _ = (directory / 'judgment-tasks.json').write_text(json.dumps(tasks, indent=2), encoding='utf-8')
    return {'tasks': tasks, 'visual_waiting_for_cached_photos': sorted(set(waiting)),
            'visual_listing_reads': sum(bool(task['images']) for task in tasks), 'text_batches': sum(not task['images'] for task in tasks)}


def apply_judgments(store: ListingStore, directory: Path) -> SavedJudgments:
    entries: list[dict[str, object]] = []
    for task in cast(list[JudgmentFile], loads((directory / 'judgment-tasks.json').read_text(encoding='utf-8'))):
        output = Path(task['output']).resolve()
        if not output.is_relative_to(directory.resolve()):
            raise ValueError('Judgment output must remain inside the trial directory.')
        value = loads(output.read_text(encoding='utf-8'))
        if not is_object(value) or set(value) != {'judgments'} or not is_array(value['judgments']):
            raise ValueError('A judgment file must contain a judgments array.')
        seen: set[str] = set()
        for item in value['judgments']:
            if not is_object(item) or set(item) != {'id', 'fingerprint', 'preferences'}:
                raise ValueError('Each judgment needs id, fingerprint and preferences.')
            identifier, preferences = item['id'], item['preferences']
            if not isinstance(identifier, str):
                raise ValueError('Each judgment needs id, fingerprint and preferences.')
            expected = task['expected'].get(identifier)
            if not expected or identifier in seen or item['fingerprint'] != expected['fingerprint']:
                raise ValueError('Judgments must cover exactly the task listings and content.')
            if not is_object(preferences) or set(preferences) != set(expected['hypotheses']):
                raise ValueError('Judge exactly the requested hypotheses.')
            seen.add(identifier)
            for phrase, judgment in preferences.items():
                if not is_object(judgment) or set(judgment) != {'verdict', 'evidence'}:
                    raise ValueError('Each hypothesis judgment needs verdict and evidence.')
                entries.append({'id': identifier, 'fingerprint': item['fingerprint'], 'hypothesis': expected['hypotheses'][phrase], **judgment})
        if seen != set(task['expected']):
            raise ValueError('The judgment batch is incomplete.')
    save_judgments(store, entries)
    return {'saved': len(entries)}


class Arguments(argparse.Namespace):
    command: str = ''
    database: Path = Path()
    directory: Path = Path()
    file: Path | None = None
    cache: Path | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('command', choices=('init', 'prepare', 'apply-review', 'judgments', 'apply-judgments', 'assess', 'status'))
    _ = parser.add_argument('database', type=Path)
    _ = parser.add_argument('directory', type=Path)
    _ = parser.add_argument('--file', type=Path)
    _ = parser.add_argument('--cache', type=Path)
    args = parser.parse_args(namespace=Arguments())
    if args.command == 'init':
        target = copy_database(args.database, args.directory / 'listings.sqlite3')
        print(json.dumps({'database': str(target)}))
        return
    store = ListingStore(args.database)
    if args.command == 'prepare':
        if has_new_labels(store):
            _ = rerank(store)
            result = proposal_file(store, args.directory)
        else:
            result = {'requested_passes': 0, 'reason': 'No unreviewed new labels.'}
    elif args.command == 'apply-review':
        if not args.file:
            parser.error('apply-review needs --file')
        result = save(store, loads(args.file.read_text(encoding='utf-8')))
    elif args.command == 'judgments':
        result = judgment_files(store, args.directory, args.cache)
    elif args.command == 'apply-judgments':
        result = apply_judgments(store, args.directory)
    elif args.command == 'assess':
        result = assess(store)
    else:
        result = active(store)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
