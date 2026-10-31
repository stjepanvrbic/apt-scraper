"""Measure journal payload and lock duration on a disposable backup of a supplied store copy."""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/ranking/scripts'))

from measurement import Interval, bootstrap, read_copy
from learning import encode
from scoring import Impression
from store import ListingStore, SignalRow, select, single


class SampleCost(TypedDict):
    """Medians and intervals over samples of one favorite's journal write: the snapshot read, its size and the write."""
    samples: int
    snapshot_bytes: float
    snapshot_bytes_interval: Interval | None
    impression_rows: int
    read_ms: float
    read_interval: Interval | None
    journal_write_ms: float
    journal_write_interval: Interval | None


class SnapshotCostReport(TypedDict):
    listings: int
    original_store: SampleCost
    stress: SampleCost
    synthetic_impressions_added: int
    all_history_snapshot_raw_bytes: int
    method: str


def sample_cost(store: ListingStore, listing: str, samples: int) -> SampleCost:
    reads: list[float] = []
    writes: list[float] = []
    sizes: list[int] = []
    impressions: list[int] = []
    for _ in range(samples):
        with store.connect() as connection:
            _ = connection.execute('BEGIN IMMEDIATE')
            started = time.perf_counter()
            inputs = store.ranking_inputs(connection=connection)
            inputs['impressions'] = store.favorite_impressions(connection, listing, datetime.now(timezone.utc).isoformat())
            reads.append((time.perf_counter() - started) * 1000)
            impressions.append(len(inputs['impressions']))
            started = time.perf_counter()
            store.record_learning_event(connection, 'favorite', listing)
            writes.append((time.perf_counter() - started) * 1000)
            sizes.append(cast(int, single(connection, 'SELECT length(CAST(inputs AS BLOB)) FROM learning_events ORDER BY id DESC LIMIT 1')[0]))
            connection.rollback()
    return {'samples': samples, 'snapshot_bytes': median(sizes),
            'snapshot_bytes_interval': bootstrap(sizes), 'impression_rows': max(impressions),
            'read_ms': median(reads), 'read_interval': bootstrap(reads),
            'journal_write_ms': median(writes), 'journal_write_interval': bootstrap(writes)}


def measure(source: Path, *, visits: int = 100, samples: int = 20) -> SnapshotCostReport:
    def run(store: ListingStore) -> SnapshotCostReport:
        inputs = store.ranking_inputs()
        listings = inputs['listings']
        if not listings:
            raise ValueError('Snapshot measurement needs at least one listing.')
        chosen = listings[-1]['id']
        original = sample_cost(store, chosen, samples)
        now = datetime.now(timezone.utc)
        rows: list[SignalRow] = [(f'stress-{visit}-{rank}', f'stress-{visit}', listing['id'], listing['fingerprint'], 'impression',
                                  (now - timedelta(seconds=visits - visit)).isoformat(), None, None, rank)
                                 for visit in range(visits) for rank, listing in enumerate(listings, 1)]
        with store.connect() as connection:
            _ = connection.executemany('INSERT INTO implicit_signals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
        stressed = sample_cost(store, chosen, samples)
        with store.connect() as connection:
            unbounded: list[Impression] = [
                {'listing_id': cast(str, row['listing_id']), 'fingerprint': cast(str, row['fingerprint']), 'visit_id': cast(str, row['visit_id']),
                 'shown_rank': cast(int, row['shown_rank']), 'occurred_at': cast(str, row['occurred_at'])}
                for row in select(connection, "SELECT listing_id, fingerprint, visit_id, shown_rank, occurred_at FROM implicit_signals WHERE kind = 'impression'")]
        legacy = encode({**inputs, 'impressions': unbounded})
        return {'listings': len(listings), 'original_store': original, 'stress': stressed, 'synthetic_impressions_added': len(rows),
                'all_history_snapshot_raw_bytes': len(legacy.encode('utf-8')),
                'method': 'Wall time inside an IMMEDIATE transaction; inserts rolled back; 95% bootstrap over samples. Stress impressions are synthetic and exist only in the disposable backup.'}
    return read_copy(source, run)


class Arguments(argparse.Namespace):
    store: Path = Path()
    output: Path = Path()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument('store', type=Path)
    _ = parser.add_argument('output', type=Path)
    args = parser.parse_args(namespace=Arguments())
    _ = args.output.write_text(json.dumps(measure(args.store), indent=2) + '\n')
