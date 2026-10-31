"""Reranks the listings and explains their ranks, with the scoring model in code and no model call, and runs the
review pass after each rerank.

Commands:
  ranking.py rerank [--seed N]       fit the scoring model, write every rank, reason and explanation, and print the
                                     summary with each favorite's held-out rank. The rerank's review then waits.
  ranking.py status                  the latest rerank's summary with its review, and the shown listings not ranked since
  ranking.py explain ID              a listing's rank, reason and full explanation
  ranking.py questions [--count N]   pairs with the largest posterior feature-difference variance
  ranking.py blind-test [--count N] [--page FILE]
                                     the photo encoder's blind test, run by the user: records the criterion, shows at
                                     least 30 listings without a label in random order on a page with their photos
                                     and facts and no rank, takes the 10 the user would view, and reports precision at
                                     10 of the model with and without the encoder. The encoder is on once it passes.
  ranking.py run                     one pass of the scheduled job apt-scraper-review: saves the review the agent
                                     wrote into the job's inbox, writes the task of a waiting review for the agent and
                                     ends with a line that tells Hermes whether to wake it. Fails only when it cannot
                                     record a failure on the waiting review.

Exit codes: 0 done, 1 the input or a step failed, 2 usage error.

Hermes wakes the model whenever a cron script fails (cron/scheduler_prompt.py, "Script Error"), so run records a
failure on the waiting review, where status reports it, and ends with {"wakeAgent": false} instead. A failure it cannot
record there exits 1 with the error on stderr, which Hermes reports.
"""

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from string import Template
from typing import NotRequired, TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gallery' / 'scripts'))

from inbox import apply, correction, discard, inbox_directory  # noqa: E402
from rerank import pairwise_questions, pending_review, rerank, save_review  # noqa: E402
from review import CONNECTING_WORDS, MODEL_WORDS, Rerank, ReviewResult, ReviewTask  # noqa: E402
from scoring import Explanation, Reason  # noqa: E402
from shapes import loads  # noqa: E402
from store import BlindTestResult, ListingStore, store_path  # noqa: E402

from blind_test import MINIMUM_LISTINGS, BlindTestUnavailable, blind_test  # noqa: E402

TEMPLATE = Path(__file__).resolve().parents[1] / 'review.md'


class EncoderJobStatus(TypedDict):
    """The encoder job's status.json: when it last ran, the problem that stopped it, why the encoder is off for good
    and the summary of its last run, as encoder-run.py writes them."""
    checked_at: str
    problem: str | None
    off: NotRequired[str]
    last: NotRequired[object]


class EncoderStatus(TypedDict):
    model: str | None
    quality_passed: bool
    enabled: bool
    blind_test: BlindTestResult | None
    job: EncoderJobStatus | None


class NotRanked(TypedDict):
    new_or_changed: int
    waiting_for_features: int


class Status(TypedDict):
    last_rerank: Rerank | None
    not_ranked: NotRanked
    photo_encoder: EncoderStatus


class Explained(TypedDict):
    id: str
    rank: int | None
    reason: Reason | None
    unranked_reason: str | None
    exploration: bool
    explanation: Explanation | None


def status(store: ListingStore) -> Status:
    """The latest rerank and how many listings shown in Rankings are not ranked since: those with a feature record
    found or changed after it, and those waiting for their record."""
    shown = [listing for listing in store.ranking_inputs()['listings'] if listing['rankable'] and listing.get('rank') is None]
    return {'last_rerank': store.last_rerank(),
            'not_ranked': {'new_or_changed': sum(listing['record'] is not None or listing['given_up'] for listing in shown),
                           'waiting_for_features': sum(listing['record'] is None and not listing['given_up'] for listing in shown)},
            'photo_encoder': encoder_status(store)}


def encoder_status(store: ListingStore) -> EncoderStatus:
    """Whether the photo encoder is on, why not, and its job's last run."""
    state = store.encoder_state()
    job = store.path.parent.parent / 'encoder' / 'status.json'
    return {'model': state['model'], 'quality_passed': state['quality_passed'], 'enabled': state['enabled'], 'blind_test': state['blind_test'],
            'job': cast(EncoderJobStatus, loads(job.read_text(encoding='utf-8'))) if job.is_file() else None}


def explain(store: ListingStore, listing_id: str) -> Explained:
    details = store.listing(listing_id)
    return {'id': listing_id, 'rank': details['rank'] if details['analysis_current'] else None, 'reason': details['reason'],
            'unranked_reason': details.get('unranked_reason'),
            'exploration': details['exploration'], 'explanation': details['explanation'] if details['analysis_current'] else None}


def compact_json(task: ReviewTask) -> str:
    """The task as JSON with one listing, preference or state entry per line, which keeps it short and readable."""
    lines = ['{"rerank": ' + json.dumps(task['rerank']) + ',']
    for key in ('listings', 'preferences', 'note_readings'):
        items = [f'  {json.dumps(item, ensure_ascii=False)}' for item in task[key]]
        lines += [f' "{key}": [', *[item + ',' for item in items[:-1]], *items[-1:], ' ],']
    state = [f'  {json.dumps(name)}: {json.dumps(value, ensure_ascii=False)}' for name, value in task['state'].items()]
    lines += [' "state": {', *[item + ',' for item in state[:-1]], *state[-1:], ' }', '}']
    return '\n'.join(lines)


def review_file(rerank_id: int) -> Path:
    return inbox_directory('review') / f'review-{rerank_id}.json'


def task_text(task: ReviewTask, rejection: str | None) -> str:
    file = review_file(task['rerank'])
    return Template(TEMPLATE.read_text(encoding='utf-8')).substitute(
        rerank_id=task['rerank'], task_json=compact_json(task), review_file=file.as_posix(),
        connecting_words=', '.join(sorted(CONNECTING_WORDS)), model_words=', '.join(sorted(MODEL_WORDS)),
        correction=correction(file, rejection))


def saved_line(name: str, result: ReviewResult) -> str:
    return f'Saved {name}: ' + {'kept': 'its moves kept.', 'reverted': 'its moves reverted.', 'no moves': 'no moves.'}[result['outcome']]


def review_run(store: ListingStore, workspace: Path) -> tuple[list[str], bool]:
    """One pass of the review job: saves the review the agent wrote into the inbox, and when the latest rerank's review
    still waits, writes its task file in workspace, with the error of a rejected review for the agent to correct, and
    removes the task files of earlier reranks. Returns the report's lines and whether to wake the agent. A review the
    store cannot take now, which is no rejection, stays in the inbox for the next run, and nothing is handed meanwhile."""
    inbox = inbox_directory('review')

    def save(value: object) -> ReviewResult:
        return save_review(store, value)

    try:
        saved, rejected = apply(inbox, {'review': save})
    except (OSError, sqlite3.OperationalError) as error:
        return [f'The review in the inbox could not be saved now, so it stays for the next run. {type(error).__name__}: {error}'], False
    lines = [saved_line(name, cast(ReviewResult, result)) for name, result in saved.items()]
    last = store.last_rerank()
    rejection = rejected.get(review_file(last['id']).name) if last else None
    task = pending_review(store, rejection)
    handed = [review_file(task['rerank']).name] if task else []
    lines += [f'Rejected {name}{", handed again for correction" if name in handed else ""}.' for name in rejected]
    discard(inbox, rejected, handed)
    if task is None:
        return [*lines, f'No review waits. The latest review is {last["review"]["state"]}.' if last and last['review'] else 'No review waits.'], False
    workspace.mkdir(parents=True, exist_ok=True)
    task_file = workspace / f'review-{task["rerank"]}.md'
    for earlier in workspace.glob('review-*.md'):
        if earlier != task_file:
            earlier.unlink()
    _ = task_file.write_text(task_text(task, rejected.get(handed[0])), encoding='utf-8')
    return [*lines, f'The review of rerank {task["rerank"]} waits, over its top {len(task["listings"])} listings.',
            f'Review task file: {task_file.as_posix()}'], True


def record_failure(store: ListingStore, message: str) -> bool:
    """Ends the latest rerank's review with message when it waits, and returns whether it did."""
    last = store.last_rerank()
    if not (last and last['review'] and last['review']['state'] in ('waiting', 'running')):
        return False
    store.fail_review(last['id'], message)
    return True


def run() -> int:
    """The cron job's command. A failure recorded on the waiting review ends quietly with {"wakeAgent": false}. A
    failure that cannot be recorded, because the store does not open, no review waits or recording fails, goes to
    stderr with exit code 1."""
    store: ListingStore | None = None
    try:
        store = ListingStore(store_path())
        lines, wake = review_run(store, store.path.parent / 'review')
    except Exception as error:
        message = f'The review could not be prepared. {type(error).__name__}: {error}'
        try:
            recorded = store is not None and record_failure(store, message)
        except Exception as record_error:
            message += f'\nThe failure could not be recorded on the review. {type(record_error).__name__}: {record_error}'
            recorded = False
        if not recorded:
            print(message, file=sys.stderr)
            return 1
        lines, wake = [message], False
    print('\n'.join([*lines, json.dumps({'wakeAgent': wake})]))
    return 0


class Arguments(argparse.Namespace):
    command: str = ''
    seed: int | None = None
    listing: str = ''
    count: int = 0
    page: Path | None = None
    picks: str | None = None


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description='Rerank the listings in the gallery and explain their ranks.')
    commands = parser.add_subparsers(dest='command', required=True)
    reranking = commands.add_parser('rerank')
    _ = reranking.add_argument('--seed', type=int, help='Draws the exploration listings. Without it, a seed is drawn and printed.')
    _ = commands.add_parser('status')
    explaining = commands.add_parser('explain')
    _ = explaining.add_argument('listing')
    asking = commands.add_parser('questions')
    _ = asking.add_argument('--count', type=int, default=8)
    _ = commands.add_parser('run')
    blind = commands.add_parser('blind-test')
    _ = blind.add_argument('--count', type=int, default=MINIMUM_LISTINGS, help=f'listings shown, at least {MINIMUM_LISTINGS}')
    _ = blind.add_argument('--page', type=Path, help='where to write the page of listings; by default beside the listing store')
    _ = blind.add_argument('--picks', help='the 10 numbers, instead of typing them when asked')
    _ = blind.add_argument('--seed', type=int, help='draws the listings; without it, a seed is drawn and recorded')
    arguments = parser.parse_args(argv, namespace=Arguments())
    if arguments.command == 'run':
        return run()
    if arguments.command == 'blind-test':
        store = ListingStore(store_path())
        try:
            _ = blind_test(store, arguments.page or store.path.parent / 'blind-test.html', count=arguments.count, seed=arguments.seed,
                           picks=arguments.picks)
            return 0
        except (BlindTestUnavailable, ValueError) as error:
            print(error, file=sys.stderr)
            return 1
    try:
        store = ListingStore(store_path())
        if arguments.command == 'rerank':
            report = rerank(store, seed=arguments.seed)
        elif arguments.command == 'status':
            report = status(store)
        elif arguments.command == 'explain':
            report = explain(store, arguments.listing)
        else:
            report = pairwise_questions(store, arguments.count)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    except (ValueError, KeyError, OSError, sqlite3.Error) as error:
        print(cast(object, error.args[0]) if isinstance(error, KeyError) else error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
