"""Serializable pre-action ranking snapshots for chronological evaluation."""

import json
import zlib


def encode(inputs):
    return json.dumps({**inputs, 'preference_readings': [
        [kind, phrase, reading] for (kind, phrase), reading in inputs['preference_readings'].items()
    ]}, ensure_ascii=False, allow_nan=False)


def decode(text):
    if isinstance(text, bytes):
        text = zlib.decompress(text).decode('utf-8')
    inputs = json.loads(text)
    inputs['preference_readings'] = {(kind, phrase): reading for kind, phrase, reading in inputs['preference_readings']}
    return inputs


def compress(text):
    return zlib.compress(text.encode('utf-8'))
