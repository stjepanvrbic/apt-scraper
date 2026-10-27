"""Prepare two independent reads of cached listing content, without fetching any URL."""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from measurement import agreement, read_copy

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'listing-features/scripts'))

from features import Features, extracted_preferences  # noqa: E402
from record import validate_extraction  # noqa: E402


def cached_files(root, manifest_file, names):
    paths = [(manifest_file.parent / name).resolve() for name in names]
    if any(not path.is_relative_to(root.resolve()) for path in paths):
        raise ValueError('Cached photo paths must stay inside the supplied cache.')
    if any(not path.is_file() for path in paths):
        raise ValueError('A cached photo or sheet is missing.')
    return paths


def prepare(store, cache, destination, count=20):
    tasks, selected = [], set()
    with store.connect() as connection:
        known = {row[0] for row in connection.execute('SELECT id FROM listings')}
    phrases = extracted_preferences(store.settings(), store.preference_readings())
    used = Counter(tag for record in store.feature_records().values() for field in ('traits', 'concerns') for tag in record['extraction'][field])
    tags = [f'{tag} ({used[tag]} listings)' for tag in sorted(store.tags(), key=lambda tag: (-used[tag], tag))]
    for manifest_file in sorted(cache.rglob('task.json')):
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
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
            task_file.write_text(Features.task_text(listing, phrases, files, tags, output, None), encoding='utf-8')
            tasks.append({'goal': f'Read {task_file.as_posix()} and extract only this listing. Do not read other tasks or outputs.',
                          'images': [path.as_posix() for path in sheets], 'output': str(output), 'listing': listing['id'],
                          'fingerprint': manifest['fingerprint'], 'photo_count': len(files), 'phrases': phrases, 'repeat': repeat})
        if len(tasks) == count * 2:
            break
    if len(tasks) != count * 2:
        raise ValueError(f'Need {count} listings with cached full photos and sheets; found {len(tasks) // 2}. No network requests made.')
    (destination / 'tasks.json').write_text(json.dumps(tasks, indent=2), encoding='utf-8')
    prompt = ('Run these independent extraction tasks with delegate_task on Sonnet, one listing per child. '
              'Pass only goal and images to the tool; the other fields are collection metadata. '
              'Never open a browser, fetch a URL, search listing sites, or start a gateway. Only use the cached files. '
              'Do not send one extraction output to another child. Read the tasks at ' + (destination / 'tasks.json').as_posix())
    (destination / 'prompt.txt').write_text(prompt, encoding='utf-8')
    return {'listings': count, 'calls': len(tasks), 'prompt': str(destination / 'prompt.txt')}


def collect(destination):
    tasks = json.loads((destination / 'tasks.json').read_text(encoding='utf-8'))
    records = {1: [], 2: []}
    for task in tasks:
        output = Path(task['output']).resolve()
        if not output.is_relative_to(destination.resolve()):
            raise ValueError('An extraction output must stay inside the retest directory.')
        value = json.loads(output.read_text(encoding='utf-8'))
        if value['id'] != task['listing'] or value['fingerprint'] != task['fingerprint']:
            raise ValueError('An extraction output belongs to different listing content.')
        records[task['repeat']].append(validate_extraction(value['record'], task['photo_count'], task['phrases']))
    return agreement(records[1], records[2])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--store', type=Path)
    parser.add_argument('--cache', type=Path)
    args = parser.parse_args()
    if args.store and args.cache:
        result = read_copy(args.store, lambda store: prepare(store, args.cache, args.destination))
    elif args.store or args.cache:
        parser.error('--store and --cache are required together')
    else:
        result = collect(args.destination)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
