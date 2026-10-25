"""Reranks the listings and explains their ranks, with the scoring model in code and no model call, and runs the
review pass after each rerank.

Commands:
  ranking.py rerank [--seed N]       fit the scoring model, write every rank, reason and explanation, and print the
                                     summary with each favorite's held-out rank. The rerank's review then waits.
  ranking.py status                  the latest rerank's summary with its review, and the shown listings not ranked since
  ranking.py explain ID              a listing's rank, reason and full explanation
  ranking.py questions [--count N]   the pairwise questions the model is least sure of
  ranking.py run                     one pass of the scheduled job apt-scraper-review: writes the task of a waiting
                                     review for the agent and ends with a line that tells Hermes whether to wake it.
                                     Never fails.
  ranking.py review-save FILE        the review agent's command: check and apply its review from a JSON file in the
                                     scratch directory, then delete the file

Exit codes: 0 done, 1 the input or a step failed, 2 usage error.

Hermes wakes the model whenever a cron script fails (cron/scheduler_prompt.py, "Script Error"), so run records a
failure on the waiting review and ends with {"wakeAgent": false} instead.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gallery' / 'scripts'))

from rerank import pairwise_questions, pending_review, rerank, save_review  # noqa: E402
from review import CONNECTING_WORDS  # noqa: E402
from scratch import read_scratch_object, scratch_directory  # noqa: E402
from store import ListingStore, store_path  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[1] / 'review.md'


def status(store):
    """The latest rerank and how many listings shown in Rankings are not ranked since: those with a feature record
    found or changed after it, and those waiting for their record."""
    shown = [listing for listing in store.ranking_inputs()['listings'] if listing['rankable'] and listing['rank'] is None]
    return {'last_rerank': store.last_rerank(),
            'not_ranked': {'new_or_changed': sum(listing['record'] is not None or listing['given_up'] for listing in shown),
                           'waiting_for_features': sum(listing['record'] is None and not listing['given_up'] for listing in shown)}}


def explain(store, listing_id):
    details = store.listing(listing_id)
    return {'id': listing_id, 'rank': details['rank'] if details['analysis_current'] else None, 'reason': details['reason'],
            'exploration': details['exploration'], 'explanation': details['explanation'] if details['analysis_current'] else None}


def compact_json(task):
    """The task as JSON with one listing, preference or state entry per line, which keeps it short and readable."""
    lines = ['{"rerank": ' + json.dumps(task['rerank']) + ',']
    for key in ('listings', 'preferences', 'note_readings'):
        items = [f'  {json.dumps(item, ensure_ascii=False)}' for item in task[key]]
        lines += [f' "{key}": [', *[item + ',' for item in items[:-1]], *items[-1:], ' ],']
    state = [f'  {json.dumps(name)}: {json.dumps(value, ensure_ascii=False)}' for name, value in task['state'].items()]
    lines += [' "state": {', *[item + ',' for item in state[:-1]], *state[-1:], ' }', '}']
    return '\n'.join(lines)


def task_text(task):
    script = Path(__file__).resolve().as_posix()
    review_file = scratch_directory() / f'review-{task["rerank"]}.json'
    return Template(TEMPLATE.read_text(encoding='utf-8')).substitute(
        rerank_id=task['rerank'], task_json=compact_json(task), review_file=review_file.as_posix(),
        connecting_words=', '.join(sorted(CONNECTING_WORDS)),
        save_command=f'python "{script}" review-save "{review_file.as_posix()}"')


def review_run(store, workspace):
    """One pass of the review job: when the latest rerank's review waits, writes its task file in workspace, removing
    the task files and review files of earlier reranks, and returns the report's lines and whether to wake the agent."""
    task = pending_review(store)
    if task is None:
        last = store.last_rerank()
        return [f'No review waits. The latest review is {last["review"]["state"]}.' if last and last['review'] else 'No review waits.'], False
    workspace.mkdir(parents=True, exist_ok=True)
    task_file = workspace / f'review-{task["rerank"]}.md'
    review_file = scratch_directory() / f'review-{task["rerank"]}.json'
    for earlier in [*workspace.glob('review-*.md'), *scratch_directory().glob('review-*.json')]:
        if earlier not in (task_file, review_file):
            earlier.unlink()
    task_file.write_text(task_text(task), encoding='utf-8')
    return [f'The review of rerank {task["rerank"]} waits, over its top {len(task["listings"])} listings.',
            f'Review task file: {task_file.as_posix()}'], True


def run():
    """The cron job's command. Every failure ends here, recorded on the waiting review, so Hermes does not wake the
    model for it."""
    store = None
    try:
        store = ListingStore(store_path())
        lines, wake = review_run(store, store.path.parent / 'review')
    except Exception as error:
        lines, wake = [f'The review could not be prepared. {type(error).__name__}: {error}'], False
        try:
            last = store.last_rerank() if store else None
            if last and last['review'] and last['review']['state'] in ('waiting', 'running'):
                store.fail_review(last['id'], lines[0])
        except Exception as record_error:
            lines.append(f'The failure could not be recorded on the review. {type(record_error).__name__}: {record_error}')
    print('\n'.join([*lines, json.dumps({'wakeAgent': wake})]))
    return 0


def main(argv):
    parser = argparse.ArgumentParser(description='Rerank the listings in the gallery and explain their ranks.')
    commands = parser.add_subparsers(dest='command', required=True)
    reranking = commands.add_parser('rerank')
    reranking.add_argument('--seed', type=int, help='Draws the exploration listings. Without it, a seed is drawn and printed.')
    commands.add_parser('status')
    explaining = commands.add_parser('explain')
    explaining.add_argument('listing')
    asking = commands.add_parser('questions')
    asking.add_argument('--count', type=int, default=8)
    commands.add_parser('run')
    saving = commands.add_parser('review-save')
    saving.add_argument('file', type=Path, help='JSON object {"rerank", "reasons", "moves", "flags", "findings"} in the scratch directory. Deleted once saved.')
    arguments = parser.parse_args(argv)
    if arguments.command == 'run':
        return run()
    try:
        store = ListingStore(store_path())
        if arguments.command == 'rerank':
            report = rerank(store, seed=arguments.seed)
        elif arguments.command == 'status':
            report = status(store)
        elif arguments.command == 'explain':
            report = explain(store, arguments.listing)
        elif arguments.command == 'questions':
            report = pairwise_questions(store, arguments.count)
        else:
            file, value = read_scratch_object(arguments.file, 'The review')
            report = save_review(store, value)
            file.unlink()
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    except (ValueError, KeyError, OSError, sqlite3.Error) as error:
        print(error.args[0] if isinstance(error, KeyError) else error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
