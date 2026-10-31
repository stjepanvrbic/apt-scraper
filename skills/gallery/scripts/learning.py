"""Serializable pre-action ranking snapshots for chronological evaluation."""

import json
import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Literal, TypedDict, cast

from queries import rows
from scoring import Direction, FeatureReading, Model, Pair, RankingInputs, RankingListing, fit_model, label, scores
from shapes import loads


class EncodedInputs(TypedDict):
    """A snapshot as encode writes it: the preference readings as [kind, phrase, reading] triples, since JSON keys are text."""
    preference_readings: list[tuple[Direction, str, list[FeatureReading]]]


class Clock(TypedDict):
    """When the snapshot was taken and when each label and pairwise answer in it was given, in days."""
    as_of_day: float
    label_days: dict[str, float]
    pair_days: list[float]


class LearningEvent(TypedDict):
    """A user action the learning_events table records: its kind, its listing and, for a pair, the other listing or,
    for a note, its sentiment."""
    kind: str
    listing: str
    other: str | None


class PairPrediction(TypedDict):
    """Whether the snapshot's model scored the chosen listing above the other: 1, 0, or 0.5 when tied."""
    kind: Literal['pair']
    value: float
    tied: bool


class RankPrediction(TypedDict):
    """Where the snapshot's model ranked the listing among the ranked listings it had scores for."""
    kind: Literal['rank']
    value: int
    candidates: int
    displayed_rank: int | None
    already_positive: bool


def encode(inputs: RankingInputs) -> str:
    return json.dumps({**inputs, 'preference_readings': [
        [kind, phrase, reading] for (kind, phrase), reading in inputs['preference_readings'].items()
    ]}, ensure_ascii=False, allow_nan=False)


def decode(text: str) -> RankingInputs:
    snapshot = loads(text)
    encoded = cast(EncodedInputs, snapshot)
    readings: dict[tuple[Direction, str], list[FeatureReading]] = {(kind, phrase): reading for kind, phrase, reading in encoded['preference_readings']}
    inputs = cast(RankingInputs, snapshot)
    inputs['preference_readings'] = readings
    return inputs


def label_clock(connection: sqlite3.Connection, listings: Sequence[RankingListing], pairs: Sequence[Pair]) -> Clock:
    def day(timestamp: str) -> float:
        return datetime.fromisoformat(timestamp).timestamp() / 86400

    dated = {(cast(str, row['listing']), cast(str, row['kind'])): day(cast(str, row['occurred_at'])) for row in rows(
        connection, "SELECT listing, kind, occurred_at FROM learning_events WHERE kind IN ('favorite', 'dismiss', 'contact') ORDER BY id")}
    for row in rows(connection, 'SELECT listing_id, requested_at FROM contacts'):
        _ = dated.setdefault((cast(str, row['listing_id']), 'contact'), day(cast(str, row['requested_at'])))
    noted = {cast(str, row['listing_id']): day(cast(str, row['created_at'])) for row in rows(
        connection, 'SELECT n.listing_id, n.created_at FROM notes n JOIN note_readings r ON r.note_id = n.id ORDER BY n.id')}
    label_days: dict[str, float] = {}
    for listing in listings:
        kind = 'dismiss' if listing['dismissed'] else 'contact' if listing['contact'] else 'favorite' if listing['favorite'] else None
        timestamp = dated.get((listing['id'], kind)) if kind else noted.get(listing['id'])
        if timestamp is not None:
            label_days[listing['id']] = timestamp
    answered: dict[tuple[str, ...], float] = {(cast(str, row['chosen']), cast(str, row['other'])): day(cast(str, row['answered_at']))
                                              for row in rows(connection, 'SELECT * FROM pairwise_answers')}
    return {'as_of_day': datetime.now(timezone.utc).timestamp() / 86400, 'label_days': label_days,
            'pair_days': [answered[tuple(pair)] for pair in pairs]}


def prediction[I: RankingInputs](inputs: I, event: LearningEvent, fitter: Callable[[I], Model] = fit_model, *,
                                 required_column: str | None = None) -> PairPrediction | RankPrediction | None:
    model = fitter(inputs)
    if required_column is not None and model.design.index(required_column) is None:
        return None
    values = dict(zip(model.design.ids, scores(model.design.likelihood_rows, model.fit.weights, model.design.dense_count)))
    if event['listing'] not in values:
        return None
    if event['kind'] == 'pair':
        other = event['other']
        if other is None or other not in values:
            return None
        difference = values[event['listing']] - values[other]
        return {'kind': 'pair', 'value': 0.5 if difference == 0 else int(difference > 0), 'tied': difference == 0}
    if event['kind'] in ('favorite', 'contact') or event['kind'] == 'note' and event['other'] == 'positive':
        candidates = [listing['id'] for listing in inputs['listings'] if listing['rankable'] and listing.get('rank') is not None and listing['id'] in values]
        if event['listing'] in candidates:
            listing = model.design.listings[event['listing']]
            previous = label(listing, inputs['sentiments'].get(event['listing']))
            return {'kind': 'rank', 'value': sorted(candidates, key=lambda identifier: (-values[identifier], identifier)).index(event['listing']) + 1,
                    'candidates': len(candidates), 'displayed_rank': listing.get('rank'),
                    'already_positive': previous is not None and previous[0] == 1}
    return None
