"""Serializable pre-action ranking snapshots for chronological evaluation."""

import json

from datetime import datetime, timezone


def encode(inputs):
    return json.dumps({**inputs, 'preference_readings': [
        [kind, phrase, reading] for (kind, phrase), reading in inputs['preference_readings'].items()
    ]}, ensure_ascii=False, allow_nan=False)


def decode(text):
    inputs = json.loads(text)
    inputs['preference_readings'] = {(kind, phrase): reading for kind, phrase, reading in inputs['preference_readings']}
    return inputs


def label_clock(connection, listings, pairs):
    def day(timestamp):
        return datetime.fromisoformat(timestamp).timestamp() / 86400

    dated = {(row['listing'], row['kind']): day(row['occurred_at']) for row in connection.execute(
        "SELECT listing, kind, occurred_at FROM learning_events WHERE kind IN ('favorite', 'dismiss', 'contact') ORDER BY id")}
    for row in connection.execute('SELECT listing_id, requested_at FROM contacts'):
        dated.setdefault((row['listing_id'], 'contact'), day(row['requested_at']))
    noted = {row['listing_id']: day(row['created_at']) for row in connection.execute(
        'SELECT n.listing_id, n.created_at FROM notes n JOIN note_readings r ON r.note_id = n.id ORDER BY n.id')}
    label_days = {}
    for listing in listings:
        kind = 'dismiss' if listing['dismissed'] else 'contact' if listing['contact'] else 'favorite' if listing['favorite'] else None
        timestamp = dated.get((listing['id'], kind)) if kind else noted.get(listing['id'])
        if timestamp is not None:
            label_days[listing['id']] = timestamp
    answered = {(row['chosen'], row['other']): day(row['answered_at']) for row in connection.execute('SELECT * FROM pairwise_answers')}
    return {'as_of_day': datetime.now(timezone.utc).timestamp() / 86400, 'label_days': label_days,
            'pair_days': [answered[tuple(pair)] for pair in pairs]}
