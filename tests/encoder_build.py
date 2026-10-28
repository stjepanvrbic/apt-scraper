"""Builds the photo encoder that ships: model.json and probes.npy in profile/skills/ranking/encoder. A developer tool,
not shipped; it runs in the build environment of encoder_sweep.py, after the sweep chose the candidate.

  encoder_build.py <variant> <models-directory> <sweep-report.json> <output-directory>

The image tower is a published ONNX export of the chosen model, pinned by repository revision and by the SHA-256 of
every file the user downloads. The text tower runs here only: it turns every phrasing of photo_catalog.py into a unit
vector, saved as probes.npy and pinned in model.json by its hash, so no text model ships. model.json also carries the
quality the sweep measured, and the gate the store reads: room types and the five probes passed or not.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'profile/skills/gallery/scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

from photo_catalog import GATE_PROBES, entries  # noqa: E402
from encoder_sweep import variants  # noqa: E402

ROOM_BAR, PROBE_BAR, ROOM_PHOTOS = 0.85, 0.85, 1000
WEIGHTS = {'siglip2-base-224': 'google/siglip2-base-patch16-224', 'siglip2-large-384': 'google/siglip2-large-patch16-384',
           'siglip2-so400m-512': 'google/siglip2-so400m-patch16-512', 'siglip2-giant-384': 'google/siglip2-giant-opt-patch16-384'}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def text_vectors(directory, graph):
    import numpy as np
    import onnxruntime as ort
    from transformers import AutoTokenizer

    prompts = [text.lower() for _, _, text in entries()]
    tokenizer = AutoTokenizer.from_pretrained(str(directory), local_files_only=True)
    ids = tokenizer(prompts, padding='max_length', max_length=64, truncation=False, return_tensors='np')['input_ids']
    if ids.shape[1] != 64:
        raise ValueError('A phrasing exceeds the SigLIP text context; shorten it instead of truncating.')
    session = ort.InferenceSession(str(directory / graph), providers=['CPUExecutionProvider'])
    vectors = session.run(['pooler_output'], {'input_ids': ids.astype(np.int64)})[0].astype(np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def quality(report):
    room = report['room']['combined_test']
    room_passed = room['photos'] >= ROOM_PHOTOS and room['agreement'] >= ROOM_BAR
    probes = {name: report['probes'][name] for name in GATE_PROBES}
    probes_passed = all(value['auc'] is not None and value['auc'] >= PROBE_BAR for value in probes.values())
    return {'room': report['room'], 'probes': probes, 'probe_rule': report['probes']['rule'],
            'room_passed': room_passed, 'probes_passed': probes_passed, 'passed': room_passed and probes_passed,
            'bars': {'room_agreement': ROOM_BAR, 'room_photos': ROOM_PHOTOS, 'probe_auc': PROBE_BAR}}


def main(argv):
    import numpy as np

    variant, models, sweep, output = argv[0], Path(argv[1]), Path(argv[2]), Path(argv[3])
    spec = variants(models)[variant]
    if spec['kind'] != 'siglip':
        raise ValueError('The build supports the SigLIP 2 family, whose text tower and tokenizer it runs.')
    directory = models / spec['model']
    lock = json.loads((directory / 'files.lock.json').read_text())
    report = next(item for item in json.loads(sweep.read_text())['reports'] if item['variant'] == variant)
    graph = spec['graph']
    files = [{key: item[key] for key in ('filename', 'bytes', 'sha256', 'url')} for item in lock['files']
             if item['filename'] == graph or item['filename'] == graph + '_data']
    for item in files:
        if sha256(directory / item['filename']) != item['sha256']:
            raise ValueError(f'{item["filename"]} does not match its pinned hash.')
    vectors = text_vectors(directory, spec['text'])
    output.mkdir(parents=True, exist_ok=True)
    np.save(output / 'probes.npy', vectors, allow_pickle=False)
    preprocessing = spec['preprocessing']
    probes = {'file': 'probes.npy', 'sha256': sha256(output / 'probes.npy'), 'rows': int(vectors.shape[0]),
              'text_graph': {key: item[key] for item in lock['files'] if item['filename'] == spec['text'] for key in ('filename', 'sha256')}}
    gate = quality(report)
    identity = hashlib.sha256(json.dumps({'files': [item['sha256'] for item in files], 'preprocessing': preprocessing,
                                          'pooling': report['choice']['pooling'], 'probe_rule': gate['probe_rule'],
                                          'probes': probes['sha256']}, sort_keys=True).encode()).hexdigest()
    manifest = {
        'name': variant, 'identity': identity,
        'source': {'repository': lock['repository'], 'revision': lock['revision'], 'weights': WEIGHTS[spec['model']],
                   'license': 'Apache-2.0'},
        'graph': graph, 'output': 'pooler_output', 'dimensions': int(vectors.shape[1]), 'preprocessing': preprocessing,
        'pooling': report['choice']['pooling'], 'probe_rule': gate['probe_rule'], 'files': files, 'probes': probes,
        'quality': gate, 'resources': report.get('resources'),
    }
    (output / 'model.json').write_text(json.dumps(manifest, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'identity': identity, 'download_bytes': sum(item['bytes'] for item in files), 'passed': gate['passed']}))


if __name__ == '__main__':
    main(sys.argv[1:])
