"""Compare image encoders for the ranking's photo columns on measured quality.

A developer tool, not shipped. It runs in a build environment with onnxruntime, numpy, Pillow and transformers
(for the text towers' tokenizers only) and never touches a listing store:

  encoder_sweep.py manifest <work> --owner-dataset <dataset.json> --owner-photos <photo-cache.json> --houses <dir>
  encoder_sweep.py text <work> <variant>            text vectors for the pre-registered prompts, once per model
  encoder_sweep.py images <work> <variant> [--threads N]   one embedding per photo, resumable
  encoder_sweep.py evaluate <work> <variant>... [--labels <labels.json>] [--output report.json]

The prompts, the scoring rules and the dev/test split below were fixed before any candidate but SigLIP 2 B/16-224 was
measured. Choices between pre-registered rules (prompt pooling, MobileCLIP's padding) are made on the dev split only.
"""

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from pathlib import Path

HOUSE_ROOMS = {'bathroom': 'bathroom', 'bedroom': 'bedroom', 'kitchen': 'kitchen', 'frontal': 'exterior'}

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/gallery/scripts'))
from photo_catalog import GATE_PROBES, PROBE_PROMPTS as CATALOG_PROBES, ROOM_PROMPTS  # noqa: E402

# Several phrasings per class, as shipped; a class scores by its best phrasing (max) or the mean of its unit text
# vectors (mean). Only the gate's probes have reference labels.
PROBE_PROMPTS = {name: CATALOG_PROBES[name] for name in GATE_PROBES}
# Contrasts, fixed after the first candidate's raw scores and before any other candidate: what a photo shows instead.
CONTRAST_PROMPTS = {
    'carpet': ['a room with hardwood floors', 'a room with tile floors', 'a room with laminate floors'],
    'exposed brick': ['a room with smooth painted walls', 'a painted drywall wall'],
    'balcony': ['the facade of a building without balconies', 'a ground-level patio', 'a window seen from inside a room'],
    'bathtub': ['a bathroom with a walk-in shower and no bathtub', 'a glass shower stall'],
    'open kitchen': ['a closed kitchen separated from the other rooms by walls', 'a small galley kitchen'],
}
PROBES = tuple(PROBE_PROMPTS)
PROBE_RULES = ('raw', 'contrast', 'centered')


CLIP_MEAN, CLIP_STD = [0.48145466, 0.4578275, 0.40821073], [0.26862954, 0.26130258, 0.27577711]


def variants(models):
    """Candidate graphs and their published preprocessing. Each SigLIP 2 size is published as fp32, fp16 and int8;
    the OpenCLIP exports' fp16 and int8 graphs are converted locally (encoder_convert.py)."""
    result = {}
    for name, size in [('siglip2-base-224', 224), ('siglip2-large-384', 384), ('siglip2-so400m-512', 512),
                       ('siglip2-giant-384', 384)]:
        for precision, suffix in [('fp32', ''), ('fp16', '_fp16'), ('int8', '_int8')]:
            result[f'{name}-{precision}'] = {
                'model': name, 'kind': 'siglip', 'graph': f'onnx/vision_model{suffix}.onnx', 'text': 'onnx/text_model.onnx',
                'preprocessing': {'resize': 'squash', 'size': size, 'resample': 'bilinear', 'mean': [0.5] * 3, 'std': [0.5] * 3}}
    for name, preprocessing in [
            ('mobileclip2-s2', {'resize': 'shortest', 'size': 256, 'resample': 'bilinear', 'mean': [0.0] * 3, 'std': [1.0] * 3}),
            ('mobileclip2-s4', {'resize': 'shortest', 'size': 256, 'resample': 'bilinear', 'mean': [0.0] * 3, 'std': [1.0] * 3}),
            ('dfn5b-h14-378', {'resize': 'squash', 'size': 378, 'resample': 'bicubic', 'mean': CLIP_MEAN, 'std': CLIP_STD})]:
        for precision, graph in [('fp32', 'visual.onnx'), ('fp16', 'visual_fp16.onnx'), ('int8', 'visual_int8.onnx')]:
            result[f'{name}-{precision}'] = {'model': name, 'kind': 'openclip', 'graph': graph, 'text': 'text.onnx',
                                             'preprocessing': preprocessing}
    return result


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(args):
    """Every photo once, with its reference room where one exists. Houses are split 20/80 into dev and test by a
    hash of the house number, fixed before measuring; the owner's photos are test only."""
    dataset = json.loads(Path(args.owner_dataset).read_text(encoding='utf-8'))
    cache = json.loads(Path(args.owner_photos).read_text(encoding='utf-8'))
    photos, seen = [], set()
    for photo in dataset['photos']:
        cached = cache.get(photo['url'])
        if not cached or cached['status'] != 'ok' or cached['sha256'] in seen:
            continue
        seen.add(cached['sha256'])
        photos.append({'source': 'owner', 'listing': photo['listing'], 'room': photo['room'], 'path': cached['path'],
                       'sha256': cached['sha256'], 'split': 'test'})
    for path in sorted(Path(args.houses).glob('*.jpg')):
        house, room = path.stem.split('_')
        split = 'dev' if int(hashlib.sha256(house.encode()).hexdigest(), 16) % 5 == 0 else 'test'
        photos.append({'source': 'houses', 'listing': f'house:{house}', 'room': HOUSE_ROOMS[room], 'path': str(path),
                       'sha256': sha256(path), 'split': split})
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    (work / 'manifest.json').write_text(json.dumps({'photos': photos}, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'photos': len(photos), 'owner': sum(p['source'] == 'owner' for p in photos),
                      'houses': sum(p['source'] == 'houses' for p in photos)}))


def prompt_list():
    entries = [('room', name, text) for name, texts in ROOM_PROMPTS.items() for text in texts]
    entries += [('probe', name, text) for name, texts in PROBE_PROMPTS.items() for text in texts]
    entries += [('contrast', name, text) for name, texts in CONTRAST_PROMPTS.items() for text in texts]
    return entries


def text(args):
    import numpy as np
    import onnxruntime as ort
    from transformers import AutoTokenizer

    spec = variants(args.models)[args.variant]
    directory = Path(args.models) / spec['model']
    entries = prompt_list()
    prompts = [entry[2].lower() for entry in entries]
    tokenizer = AutoTokenizer.from_pretrained(str(directory), local_files_only=True)
    session = ort.InferenceSession(str(directory / spec['text']), providers=['CPUExecutionProvider'])
    out = Path(args.work) / spec['model']
    out.mkdir(parents=True, exist_ok=True)
    if spec['kind'] == 'siglip':
        ids = tokenizer(prompts, padding='max_length', max_length=64, truncation=False, return_tensors='np')['input_ids']
        if ids.shape[1] != 64:
            raise ValueError('A prompt exceeds the SigLIP text context.')
        result = {'pad': session.run(['pooler_output'], {'input_ids': ids.astype(np.int64)})[0]}
    else:
        result = {}
        for pad in (0, tokenizer.pad_token_id):
            rows = []
            for prompt in prompts:
                tokens = tokenizer(prompt)['input_ids']
                if len(tokens) > 77:
                    raise ValueError('A prompt exceeds the CLIP text context.')
                rows.append(tokens + [pad] * (77 - len(tokens)))
            result[f'pad{pad}'] = session.run(None, {'input_ids': np.array(rows, dtype=np.int64)})[0]
    for key, value in result.items():
        np.save(out / f'text-{key}.npy', value.astype(np.float32), allow_pickle=False)
    (out / 'prompts.json').write_text(json.dumps(entries, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'variant': args.variant, 'prompts': len(prompts), 'paddings': sorted(result)}))


def preprocess(path, spec):
    """The shipped encoder's preprocessing, so the measured and the shipped inputs are one."""
    from PIL import Image, ImageOps
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/encoder'))
    from encoder import preprocess as shipped

    with Image.open(path) as source:
        return shipped(ImageOps.exif_transpose(source).convert('RGB'), spec['preprocessing'])


def images(args):
    import numpy as np
    import onnxruntime as ort

    spec = variants(args.models)[args.variant]
    photos = json.loads((Path(args.work) / 'manifest.json').read_text(encoding='utf-8'))['photos']
    out = Path(args.work) / args.variant
    out.mkdir(parents=True, exist_ok=True)
    options = ort.SessionOptions()
    options.intra_op_num_threads = args.threads
    options.inter_op_num_threads = 1
    graph = Path(args.models) / spec['model'] / spec['graph']
    started = time.perf_counter()
    session = ort.InferenceSession(str(graph), sess_options=options, providers=['CPUExecutionProvider'])
    load = time.perf_counter() - started
    feed = session.get_inputs()[0]
    dtype = np.float16 if feed.type == 'tensor(float16)' else np.float32
    names = [output.name for output in session.get_outputs()]
    output = 'pooler_output' if 'pooler_output' in names else names[0]
    chunk_size = 64
    seconds = 0.0
    starts = list(range(0, len(photos), chunk_size))
    starts = starts[args.skip:] + starts[:args.skip]
    for start in reversed(starts) if args.reverse else starts:
        part = out / f'chunk-{start:05d}.npy'
        if part.exists():
            continue
        batch = photos[start:start + chunk_size]
        before = time.perf_counter()
        rows = []
        for offset in range(0, len(batch), args.batch):
            pixels = np.stack([preprocess(item['path'], spec) for item in batch[offset:offset + args.batch]]).astype(dtype)
            rows.append(np.asarray(session.run([output], {feed.name: pixels})[0], dtype=np.float32))
        seconds += time.perf_counter() - before
        temporary = part.with_suffix(f'.{os.getpid()}.tmp')
        with temporary.open('wb') as stream:
            np.save(stream, np.concatenate(rows), allow_pickle=False)
        if part.exists():
            temporary.unlink()
        else:
            temporary.replace(part)
        print(f'{args.variant}: {min(start + chunk_size, len(photos))}/{len(photos)}', flush=True)
    vectors = np.concatenate([np.load(out / f'chunk-{start:05d}.npy') for start in range(0, len(photos), chunk_size)])
    if vectors.shape[0] != len(photos) or not np.isfinite(vectors).all():
        raise ValueError('The embeddings do not cover the manifest.')
    np.save(out / 'vectors.npy', vectors, allow_pickle=False)
    timing = out / 'timing.json'
    previous = json.loads(timing.read_text()) if timing.exists() else {'seconds': 0.0}
    timing.write_text(json.dumps({'threads': args.threads, 'batch': args.batch, 'load_seconds': load,
                                  'seconds': previous['seconds'] + seconds, 'photos': len(photos),
                                  'graph_bytes': sum(path.stat().st_size for path in graph.parent.glob(graph.name + '*'))}))


def unit(values):
    import numpy as np
    values = np.asarray(values, dtype=np.float32)
    return values / np.linalg.norm(values, axis=-1, keepdims=True)


def class_scores(vectors, texts, entries, kind, pooling):
    """Cosine of each photo with each class: the class's mean unit text vector, or its best phrasing."""
    import numpy as np
    names = list(dict.fromkeys(name for entry_kind, name, _ in entries if entry_kind == kind))
    columns = []
    for name in names:
        rows = [index for index, (entry_kind, entry_name, _) in enumerate(entries) if entry_kind == kind and entry_name == name]
        if pooling == 'mean':
            columns.append(unit(vectors) @ unit(unit(texts[rows]).mean(axis=0)))
        else:
            columns.append((unit(vectors) @ unit(texts[rows]).T).max(axis=1))
    return names, np.stack(columns, axis=1)


def agreement(photos, predicted, select):
    rows = [(photo['listing'], photo['room'] == label) for photo, label in zip(photos, predicted) if select(photo)]
    groups = {}
    for listing, hit in rows:
        groups.setdefault(listing, []).append(hit)
    if not rows:
        return None
    clusters = list(groups.values())
    generator = random.Random(0)
    estimates = sorted(sum(sum(group) for group in sample) / sum(len(group) for group in sample)
                       for sample in (generator.choices(clusters, k=len(clusters)) for _ in range(2000)))
    return {'photos': len(rows), 'listings': len(groups), 'agreement': sum(hit for _, hit in rows) / len(rows),
            'interval': [estimates[49], estimates[1950]]}


def auc(labels, scores):
    """The Mann-Whitney form: the chance a positive outscores a negative, ties counting half."""
    order = sorted(range(len(scores)), key=scores.__getitem__)
    ranks = [0.0] * len(scores)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and scores[order[end + 1]] == scores[order[start]]:
            end += 1
        for position in range(start, end + 1):
            ranks[order[position]] = (start + end) / 2 + 1
        start = end + 1
    positives = sum(label == 1 for label in labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        return None
    total = sum(rank for rank, label in zip(ranks, labels) if label == 1)
    return (total - positives * (positives + 1) / 2) / (positives * negatives)


def auc_report(labels, scores):
    point = auc(labels, scores)
    positive = [score for label, score in zip(labels, scores) if label == 1]
    negative = [score for label, score in zip(labels, scores) if label == 0]
    interval = None
    if point is not None:
        generator = random.Random(0)
        estimates = sorted(auc([1] * len(positive) + [0] * len(negative),
                               generator.choices(positive, k=len(positive)) + generator.choices(negative, k=len(negative)))
                           for _ in range(2000))
        interval = [estimates[49], estimates[1950]]
    return {'auc': point, 'interval': interval, 'positive': len(positive), 'negative': len(negative)}


def probe_scores(photos, vectors, texts, entries, pooling, rule):
    """Each listing's score per probe: the best of its photos, by the rule."""
    import numpy as np
    if rule == 'centered':
        vectors = unit(vectors)
        for source in {photo['source'] for photo in photos}:
            rows = [index for index, photo in enumerate(photos) if photo['source'] == source]
            vectors[rows] = vectors[rows] - vectors[rows].mean(axis=0)
    names, scores = class_scores(vectors, texts, entries, 'probe', pooling)
    if rule == 'contrast':
        contrast_names, contrast = class_scores(vectors, texts, entries, 'contrast', 'max')
        scores = scores - contrast[:, [contrast_names.index(name) for name in names]]
    listings = {}
    for photo, row in zip(photos, np.asarray(scores)):
        current = listings.setdefault(photo['listing'], [-math.inf] * len(names))
        listings[photo['listing']] = [max(a, float(b)) for a, b in zip(current, row)]
    return names, listings


def probe_report(photos, vectors, texts, entries, pooling, labels):
    """One rule for all probes, chosen by the mean AUC on the dev houses; AUCs reported on every other listing."""
    dev = {photo['listing'] for photo in photos if photo['split'] == 'dev'}
    results = {}
    for rule in PROBE_RULES:
        names, listings = probe_scores(photos, vectors, texts, entries, pooling, rule)
        def measured(select):
            values = {}
            for index, name in enumerate(names):
                known = [(value[name], listings[listing][index]) for listing, value in labels.items()
                         if value.get(name) is not None and listing in listings and select(listing)]
                values[name] = auc_report([label for label, _ in known], [score for _, score in known])
            return values
        dev_values = measured(lambda listing: listing in dev)
        usable = [value['auc'] for value in dev_values.values() if value['auc'] is not None]
        results[rule] = {'dev_mean_auc': sum(usable) / len(usable) if usable else None,
                         'test': measured(lambda listing: listing not in dev),
                         'owner': measured(lambda listing: not listing.startswith('house:'))}
    chosen = max(PROBE_RULES, key=lambda rule: results[rule]['dev_mean_auc'] or 0)
    return {'rule': chosen, 'rules': results, **results[chosen]['test']}


def evaluate_variant(work, variant, models, labels):
    import numpy as np

    spec = variants(models)[variant]
    photos = json.loads((work / 'manifest.json').read_text(encoding='utf-8'))['photos']
    vectors = np.load(work / variant / 'vectors.npy')
    entries = [tuple(entry) for entry in json.loads((work / spec['model'] / 'prompts.json').read_text(encoding='utf-8'))]
    paddings = sorted(path.stem.removeprefix('text-') for path in (work / spec['model']).glob('text-*.npy'))
    dev = lambda photo: photo['split'] == 'dev'  # noqa: E731
    choices = []
    for padding in paddings:
        texts = np.load(work / spec['model'] / f'text-{padding}.npy')
        for pooling in ('mean', 'max'):
            names, scores = class_scores(vectors, texts, entries, 'room', pooling)
            predicted = [names[index] for index in scores.argmax(axis=1)]
            choices.append((agreement(photos, predicted, dev)['agreement'], padding, pooling, predicted, texts))
    choices.sort(key=lambda item: -item[0])
    dev_value, padding, pooling, predicted, texts = choices[0]
    report = {'variant': variant, 'choice': {'padding': padding, 'pooling': pooling, 'dev_agreement': dev_value,
                                             'alternatives': [[c[1], c[2], c[0]] for c in choices]}}
    report['room'] = {
        'owner_extraction': agreement(photos, predicted, lambda p: p['source'] == 'owner' and p['room']),
        'houses_test': agreement(photos, predicted, lambda p: p['source'] == 'houses' and p['split'] == 'test'),
        'combined_test': agreement(photos, predicted, lambda p: p['split'] == 'test' and p['room']),
    }
    report['probes'] = probe_report(photos, vectors, texts, entries, pooling, labels)
    timing = work / variant / 'timing.json'
    report['timing'] = json.loads(timing.read_text()) if timing.exists() else None
    return report


def evaluate(args):
    work = Path(args.work)
    labels = json.loads(Path(args.labels).read_text(encoding='utf-8')) if args.labels else {}
    reports = [evaluate_variant(work, variant, args.models, labels) for variant in args.variants]
    text_ = json.dumps({'reports': reports}, indent=1, allow_nan=False) + '\n'
    if args.output:
        Path(args.output).write_text(text_, encoding='utf-8')
    for report in reports:
        room = report['room']
        print(report['variant'], report['choice']['padding'], report['choice']['pooling'],
              'dev', round(report['choice']['dev_agreement'], 4),
              'owner', round(room['owner_extraction']['agreement'], 4),
              'houses', round(room['houses_test']['agreement'], 4),
              'all', room['combined_test']['photos'], round(room['combined_test']['agreement'], 4),
              report['probes']['rule'],
              {name: (None if value['auc'] is None else round(value['auc'], 3), value['positive'])
               for name, value in report['probes'].items() if name in PROBES})


def compare(args):
    """A lower-precision graph against the full-precision embeddings of the same model, on a fixed sample of photos,
    with the text padding and pooling the full-precision graph's evaluation chose (--output names that report):
    how often it predicts the same room, its agreement with the reference rooms next to the full-precision one's, how
    close its embeddings are, and how much each gate probe's score moves."""
    import numpy as np
    import onnxruntime as ort

    reference, variant = args.variants
    if not args.output or not Path(args.output).is_file():
        raise SystemExit('compare needs --output, the evaluate report that chose the reference variant\'s padding and pooling.')
    specs = variants(args.models)
    spec = specs[variant]
    work = Path(args.work)
    photos = json.loads((work / 'manifest.json').read_text(encoding='utf-8'))['photos']
    sample = sorted(random.Random(0).sample(range(len(photos)), args.sample))
    full = unit(np.load(work / reference / 'vectors.npy')[sample])
    options = ort.SessionOptions()
    options.intra_op_num_threads = args.threads
    session = ort.InferenceSession(str(Path(args.models) / spec['model'] / spec['graph']), sess_options=options,
                                   providers=['CPUExecutionProvider'])
    feed = session.get_inputs()[0]
    dtype = np.float16 if feed.type == 'tensor(float16)' else np.float32
    names = [output.name for output in session.get_outputs()]
    output = 'pooler_output' if 'pooler_output' in names else names[0]
    started, rows = time.perf_counter(), []
    for start in range(0, len(sample), args.batch):
        pixels = np.stack([preprocess(photos[index]['path'], spec) for index in sample[start:start + args.batch]]).astype(dtype)
        rows.append(np.asarray(session.run([output], {feed.name: pixels})[0], dtype=np.float32))
    seconds = time.perf_counter() - started
    lower = unit(np.concatenate(rows))
    entries = [tuple(entry) for entry in json.loads((work / spec['model'] / 'prompts.json').read_text(encoding='utf-8'))]
    choice = next(item for item in json.loads(Path(args.output).read_text(encoding='utf-8'))['reports']
                  if item['variant'] == reference)['choice']
    pooling = choice['pooling']
    texts = np.load(work / spec['model'] / f'text-{choice["padding"]}.npy')
    room_names, full_rooms = class_scores(full, texts, entries, 'room', pooling)
    _, lower_rooms = class_scores(lower, texts, entries, 'room', pooling)
    full_predicted = [room_names[index] for index in full_rooms.argmax(axis=1)]
    lower_predicted = [room_names[index] for index in lower_rooms.argmax(axis=1)]
    labeled = [position for position, index in enumerate(sample) if photos[index]['room']]
    probe_names, full_probes = class_scores(full, texts, entries, 'probe', pooling)
    _, lower_probes = class_scores(lower, texts, entries, 'probe', pooling)
    result = {'reference': reference, 'variant': variant, 'photos': len(sample), 'seconds': round(seconds, 1),
              'same_room_as_reference': sum(a == b for a, b in zip(full_predicted, lower_predicted)) / len(sample),
              'room_agreement_reference': sum(full_predicted[p] == photos[sample[p]]['room'] for p in labeled) / len(labeled),
              'room_agreement_variant': sum(lower_predicted[p] == photos[sample[p]]['room'] for p in labeled) / len(labeled),
              'labeled_photos': len(labeled),
              'cosine_to_reference': {'mean': float((full * lower).sum(axis=1).mean()), 'min': float((full * lower).sum(axis=1).min())},
              'probe_score_shift': {name: float(np.abs(full_probes[:, column] - lower_probes[:, column]).max())
                                    for column, name in enumerate(probe_names)},
              'probe_score_spread': {name: float(full_probes[:, column].std()) for column, name in enumerate(probe_names)}}
    print(json.dumps(result, allow_nan=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['manifest', 'text', 'images', 'evaluate', 'compare'])
    parser.add_argument('work')
    parser.add_argument('variants', nargs='*')
    parser.add_argument('--models')
    parser.add_argument('--owner-dataset')
    parser.add_argument('--owner-photos')
    parser.add_argument('--houses')
    parser.add_argument('--labels')
    parser.add_argument('--output')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--batch', type=int, default=8)
    parser.add_argument('--reverse', action='store_true', help='embed the chunks from the last, for a second process')
    parser.add_argument('--skip', type=int, default=0, help='start at this chunk, for a third process')
    parser.add_argument('--sample', type=int, default=512, help='photos compare embeds')
    args = parser.parse_args(argv)
    if args.command == 'manifest':
        manifest(args)
    elif args.command in ('text', 'images'):
        for variant in args.variants:
            args.variant = variant
            (text if args.command == 'text' else images)(args)
    elif args.command == 'compare':
        compare(args)
    else:
        evaluate(args)


if __name__ == '__main__':
    sys.exit(main())
