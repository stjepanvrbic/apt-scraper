"""Prepare two independent reads of cached listing content, without fetching any URL."""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict, cast

from measurement import AgreementReport, agreement, read_copy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'listing-features/scripts'))

from features import Features, extracted_preferences  # noqa: E402
from record import Extraction, validate_extraction  # noqa: E402
from shapes import is_object, loads  # noqa: E402
from store import ListingStore, select  # noqa: E402


class CacheManifest(TypedDict):
    """The task.json an extraction's work directory keeps: the listing, its content and the cached photo and sheet
    files beside it, as features.py writes it."""
    id: str
    fingerprint: str
    files: list[str]
    sheets: list[str]


class RetestTask(TypedDict):
    """One extraction task of the retest, as tasks.json keeps it."""
    goal: str
    images: list[str]
    output: str
    listing: str
    fingerprint: str
    photo_count: int
    phrases: list[str]
    repeat: int


class PrepareReport(TypedDict):
    listings: int
    calls: int
    prompt: str


class MissingCachedPhotos(ValueError):
    """The cache no longer contains all of the named photos or sheets."""


def cached_files(root: Path, manifest_file: Path, names: Iterable[str]) -> list[Path]:
    paths = [(manifest_file.parent / name).resolve() for name in names]
    if any(not path.is_relative_to(root.resolve()) for path in paths):
        raise ValueError('Cached photo paths must stay inside the supplied cache.')
    if any(not path.is_file() for path in paths):
        raise MissingCachedPhotos('A cached photo or sheet is missing.')
    return paths


def prepare(store: ListingStore, cache: Path, destination: Path, count: int = 20) -> PrepareReport:
    tasks: list[RetestTask] = []
    selected: set[str] = set()
    with store.connect() as connection:
        known = {cast(str, row[0]) for row in select(connection, 'SELECT id FROM listings')}
    phrases: list[str] = extracted_preferences(store.settings(), store.preference_readings())
    used = Counter(tag for record in store.feature_records().values() for field in ('traits', 'concerns') for tag in record['extraction'][field])
    tags = [f'{tag} ({used[tag]} listings)' for tag in sorted(store.tags(), key=lambda tag: (-used[tag], tag))]
    for manifest_file in sorted(cache.rglob('task.json')):
        manifest = cast(CacheManifest, loads(manifest_file.read_text(encoding='utf-8')))
        if manifest['id'] not in known or manifest['id'] in selected:
            continue
        listing = store.listing(manifest['id'])
        if listing['content_fingerprint'] != manifest['fingerprint']:
            continue
        files = cached_files(cache, manifest_file, manifest['files'])
        sheets = cached_files(cache, manifest_file, manifest['sheets'])
        if not files or not sheets:
            continue
        selected.add(manifest['id'])
        for repeat in (1, 2):
            directory = destination / str(repeat) / str(len(tasks) // 2)
            directory.mkdir(parents=True, exist_ok=False)
            task_file, output = directory / 'task.md', directory / 'record.json'
            text: str = Features.task_text(listing, phrases, files, tags, output, None)
            _ = task_file.write_text(text, encoding='utf-8')
            tasks.append({'goal': f'Read {task_file.as_posix()} and extract only this listing. Do not read other tasks or outputs.',
                          'images': [path.as_posix() for path in sheets], 'output': str(output), 'listing': listing['id'],
                          'fingerprint': manifest['fingerprint'], 'photo_count': len(files), 'phrases': phrases, 'repeat': repeat})
        if len(tasks) == count * 2:
            break
    if len(tasks) != count * 2:
        raise ValueError(f'Need {count} listings with cached full photos and sheets; found {len(tasks) // 2}. No network requests made.')
    _ = (destination / 'tasks.json').write_text(json.dumps(tasks, indent=2), encoding='utf-8')
    prompt = ('Run these independent extraction tasks with delegate_task on Sonnet, one listing per child. '
              + 'Pass only goal and images to the tool; the other fields are collection metadata. '
              + 'Never open a browser, fetch a URL, search listing sites, or start a gateway. Only use the cached files. '
              + 'Do not send one extraction output to another child. Read the tasks at ' + (destination / 'tasks.json').as_posix())
    _ = (destination / 'prompt.txt').write_text(prompt, encoding='utf-8')
    return {'listings': count, 'calls': len(tasks), 'prompt': str(destination / 'prompt.txt')}


def collect(destination: Path) -> AgreementReport:
    tasks = cast(list[RetestTask], loads((destination / 'tasks.json').read_text(encoding='utf-8')))
    records: dict[int, list[Extraction]] = {1: [], 2: []}
    for task in tasks:
        output = Path(task['output']).resolve()
        if not output.is_relative_to(destination.resolve()):
            raise ValueError('An extraction output must stay inside the retest directory.')
        value = loads(output.read_text(encoding='utf-8'))
        if not is_object(value) or value['id'] != task['listing'] or value['fingerprint'] != task['fingerprint']:
            raise ValueError('An extraction output belongs to different listing content.')
        records[task['repeat']].append(validate_extraction(value['record'], task['photo_count'], task['phrases']))
    return agreement(records[1], records[2])


class Arguments(argparse.Namespace):
    destination: Path = Path()
    store: Path | None = None
    cache: Path | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('destination', type=Path)
    _ = parser.add_argument('--store', type=Path)
    _ = parser.add_argument('--cache', type=Path)
    args = parser.parse_args(namespace=Arguments())
    if args.store and args.cache:
        cache = args.cache
        result = read_copy(args.store, lambda store: prepare(store, cache, args.destination))
    elif args.store or args.cache:
        parser.error('--store and --cache are required together')
    else:
        result = collect(args.destination)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
