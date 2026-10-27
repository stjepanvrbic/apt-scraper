"""Serializable pre-action ranking snapshots for chronological evaluation."""

import json


def encode(inputs):
    return json.dumps({**inputs, 'preference_readings': [
        [kind, phrase, reading] for (kind, phrase), reading in inputs['preference_readings'].items()
    ]}, ensure_ascii=False, allow_nan=False)


def decode(text):
    inputs = json.loads(text)
    inputs['preference_readings'] = {(kind, phrase): reading for kind, phrase, reading in inputs['preference_readings']}
    return inputs
