"""Reranks the listings and explains their ranks, with the scoring model in code and no model call.

Commands:
  ranking.py rerank [--seed N]       fit the scoring model, write every rank, reason and explanation, and print the
                                     summary with each favorite's held-out rank
  ranking.py status                  the latest rerank's summary and the shown listings not ranked since
  ranking.py explain ID              a listing's rank, reason and full explanation
  ranking.py questions [--count N]   the pairwise questions the model is least sure of

Exit codes: 0 done, 1 the input or a step failed, 2 usage error.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gallery' / 'scripts'))

from rerank import pairwise_questions, rerank  # noqa: E402
from store import ListingStore, store_path  # noqa: E402


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
    arguments = parser.parse_args(argv)
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
        print(error.args[0] if isinstance(error, KeyError) else error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
