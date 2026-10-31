"""Embeds every listing photo once with the shipped image tower and derives the ranking's photo columns from the
embeddings. It runs in the encoder's own environment (onnxruntime, numpy, Pillow), which scripts/encoder-run.py
builds and starts; nothing here runs during a rerank.

Commands:
  encoder.py run <model-directory> [--budget SECONDS] [--threads N]
      embeds the photos of the listings the ranking ranks or learns from that have no embedding yet, labeled listings
      first, until the budget is spent, then, when the listings, their photos or their records changed, derives every
      listing's probe matches, room types and reread mark, and the best-match similarity of each two listings' photos
      of each room, and saves them to the listing store. Prints one JSON line with what it did.
  encoder.py check <model-directory>
      loads the image tower and embeds a generated image, exit 0 when the embedding has the manifest's size.

model.json beside this file names the image tower's files, their hashes, its preprocessing and the quality measured
before it shipped; probes.npy holds the text vectors of photo_catalog.py's phrasings, computed once at build time by
the text tower, which never ships. An embedding is cached by model and photo content, and each photo URL by the
content it had, so a photo is downloaded and embedded once, however many listings or contents show it.
"""

import argparse
import hashlib
import io
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, cast

import numpy as np
import onnxruntime
from numpy.typing import ArrayLike, NDArray
from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
SKILLS = HERE.parents[1]
sys.path[:0] = [str(SKILLS / 'gallery' / 'scripts'), str(SKILLS / 'listing-features' / 'scripts')]

from assets import Manifest, Stamp  # noqa: E402
from photo_catalog import MATCH_ROOMS, entries  # noqa: E402
from photos import distinct_photos  # noqa: E402

if TYPE_CHECKING:
    from store import PhotoFeatures, PhotoSimilarity

MANIFEST = HERE / 'model.json'
PROBES = HERE / 'probes.npy'
DOWNLOAD_ATTEMPTS = 3
BATCH = 8
REREAD_SHARE = 0.5

Vector = NDArray[np.float32]
Phrasing = tuple[str, str, str]
"""One row of probes.npy: the kind of class (room or probe), the class and the phrasing, as photo_catalog.entries gives them."""
Pooling = Literal['mean', 'max']
ProbeRule = Literal['raw', 'centered']
Resample = Literal['bilinear', 'bicubic']
PhotoState = Literal['ok', 'missing', 'failed']
Download = Callable[[str], bytes | None]
Contents = list[tuple[str, str]]
"""A listing's embedded photos: each URL with the hash of the content it had."""


class Preprocessing(TypedDict):
    resize: Literal['squash', 'shortest']
    size: int
    resample: Resample
    mean: list[float]
    std: list[float]


class TowerModel(TypedDict):
    """The part of model.json that loads the image tower and makes its input."""
    graph: str
    output: str
    dimensions: int
    preprocessing: Preprocessing


class Rules(TypedDict):
    """The part of model.json that scores the embeddings: how a class's phrasings pool and how a probe is read."""
    pooling: Pooling
    probe_rule: ProbeRule


class ProbeFile(TypedDict):
    sha256: str


class Model(Manifest, TowerModel, Rules):
    """model.json as the encoder reads it; assets.py reads the pinned files and encoder-run.py the quality gate."""
    name: str
    probes: ProbeFile


class Target(TypedDict):
    """What the encoder reads of a listing the store gives it to embed (store.PhotoTarget): its id, the content its
    photos belong to, their URLs and its record's room type by URL."""
    id: str
    fingerprint: str
    photos: list[str]
    rooms: dict[str, str] | None


class RunReport(TypedDict):
    """The JSON line a run prints: what it embedded, reused and left waiting, how many listings it works for and has
    every photo of, how many it saved photo features for (None when nothing changed) and the seconds spent encoding."""
    embedded: int
    reused: int
    waiting: int
    listings: int
    complete: int
    derived: int | None
    encoding_seconds: float


class PhotoRow(TypedDict):
    """A row of the cache's photos table: the content a URL had, or None while it is missing or failed."""
    model: str
    url: str
    content: str | None
    state: PhotoState
    attempts: int


class Arguments(argparse.Namespace):
    command: Literal['run', 'check'] = 'run'
    model: str = ''
    budget: float = 540.0
    threads: int = 4


def manifest() -> Model:
    return cast(Model, json.loads(MANIFEST.read_text(encoding='utf-8')))


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def text_vectors(model: Model) -> tuple[list[Phrasing], Vector]:
    """The probe and room phrasings' unit text vectors, checked against the hash the manifest pins."""
    if sha256(PROBES) != model['probes']['sha256']:
        raise ValueError('probes.npy does not match the hash in model.json.')
    vectors = cast(Vector, np.load(PROBES, allow_pickle=False))
    phrasings = entries()
    if vectors.shape != (len(phrasings), model['dimensions']):
        raise ValueError('probes.npy does not hold one vector per phrasing of photo_catalog.py.')
    return phrasings, unit(vectors)


def unit(values: ArrayLike) -> Vector:
    values = np.asarray(values, dtype=np.float32)
    norms = cast(Vector, np.linalg.norm(values, axis=-1, keepdims=True))
    return np.divide(values, norms, out=np.zeros_like(values), where=norms > 0)


RESAMPLING: dict[Resample, Image.Resampling] = {'bilinear': Image.Resampling.BILINEAR, 'bicubic': Image.Resampling.BICUBIC}


def preprocess(image: Image.Image, preprocessing: Preprocessing) -> Vector:
    """The tower's input for one RGB image, as its published processor makes it: resize "squash" stretches it to the
    square, "shortest" scales its short side to the size and crops the center; then the pixels, scaled to 0 to 1, are
    normalized by the mean and standard deviation of each channel."""
    size, resample = preprocessing['size'], RESAMPLING[preprocessing['resample']]
    if preprocessing['resize'] == 'squash':
        image = image.resize((size, size), resample)
    else:
        width, height = image.size
        resized = int(size * max(width, height) / min(width, height))
        image = image.resize((size, resized) if width <= height else (resized, size), resample)
        left, top = int(round((image.width - size) / 2.0)), int(round((image.height - size) / 2.0))
        image = image.crop((left, top, left + size, top + size))
    values = np.asarray(image, dtype=np.float32) / 255.0
    values = (values - np.asarray(preprocessing['mean'], dtype=np.float32)) / np.asarray(preprocessing['std'], dtype=np.float32)
    return values.transpose(2, 0, 1)


def decoded(content: bytes) -> Image.Image:
    with Image.open(io.BytesIO(content)) as source:
        return ImageOps.exif_transpose(source).convert('RGB')


class Tower:
    session: onnxruntime.InferenceSession
    input: onnxruntime.NodeArg
    dtype: type[np.float16] | type[np.float32]
    output: str
    preprocessing: Preprocessing
    dimensions: int

    def __init__(self, directory: str | Path, model: TowerModel, threads: int) -> None:
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(str(Path(directory) / model['graph']), sess_options=options,
                                                    providers=['CPUExecutionProvider'])
        self.input = self.session.get_inputs()[0]
        self.dtype = np.float16 if self.input.type == 'tensor(float16)' else np.float32
        self.output, self.preprocessing, self.dimensions = model['output'], model['preprocessing'], model['dimensions']

    def embed(self, images: Sequence[Image.Image]) -> Vector:
        pixels = np.stack([preprocess(image, self.preprocessing) for image in images]).astype(self.dtype)
        vectors = np.asarray(self.session.run([self.output], {self.input.name: pixels})[0], dtype=np.float32)
        if vectors.shape != (len(images), self.dimensions) or not np.isfinite(vectors).all():
            raise ValueError('The image tower returned an embedding of the wrong size or a value that is not finite.')
        return unit(vectors)


class Cache:
    """Each photo URL's content and download state, and each content's embedding, by model."""
    path: Path

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            _ = connection.executescript('''
                CREATE TABLE IF NOT EXISTS photos (model TEXT NOT NULL, url TEXT NOT NULL, content TEXT,
                    state TEXT NOT NULL CHECK (state IN ('ok', 'missing', 'failed')), attempts INTEGER NOT NULL,
                    PRIMARY KEY (model, url));
                CREATE TABLE IF NOT EXISTS vectors (model TEXT NOT NULL, content TEXT NOT NULL, vector BLOB NOT NULL,
                    PRIMARY KEY (model, content));
                CREATE TABLE IF NOT EXISTS derivations (model TEXT PRIMARY KEY, stamp TEXT NOT NULL);''')

    def connect(self) -> closing[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return closing(connection)

    def photos(self, model: str) -> dict[str, PhotoRow]:
        with self.connect() as connection:
            rows = cast(Iterable[sqlite3.Row], connection.execute('SELECT * FROM photos WHERE model = ?', (model,)))
            return {cast(str, row['url']): {'model': cast(str, row['model']), 'url': cast(str, row['url']),
                                            'content': cast(str | None, row['content']), 'state': cast(PhotoState, row['state']),
                                            'attempts': cast(int, row['attempts'])} for row in rows}

    def vector(self, model: str, content: str) -> Vector | None:
        with self.connect() as connection:
            row = cast(sqlite3.Row | None,
                       connection.execute('SELECT vector FROM vectors WHERE model = ? AND content = ?', (model, content)).fetchone())
        return None if row is None else np.frombuffer(cast(bytes, row['vector']), dtype=np.dtype('<f4'))

    def vectors(self, model: str, contents: Sequence[str]) -> dict[str, Vector]:
        with self.connect() as connection:
            rows = cast(list[sqlite3.Row],
                        connection.execute(f'SELECT content, vector FROM vectors WHERE model = ? AND content IN ({", ".join("?" * len(contents))})',
                                           (model, *contents)).fetchall()) if contents else []
        return {cast(str, row['content']): np.frombuffer(cast(bytes, row['vector']), dtype=np.dtype('<f4')) for row in rows}

    def save(self, model: str, url: str, state: PhotoState, attempts: int, content: str | None = None, vector: ArrayLike | None = None) -> None:
        with self.connect() as connection, connection:
            if vector is not None:
                _ = connection.execute('INSERT OR IGNORE INTO vectors VALUES (?, ?, ?)', (model, content, np.asarray(vector, dtype='<f4').tobytes()))
            _ = connection.execute('INSERT OR REPLACE INTO photos VALUES (?, ?, ?, ?, ?)', (model, url, content, state, attempts))

    def stamp(self, model: str) -> str | None:
        with self.connect() as connection:
            row = cast(sqlite3.Row | None, connection.execute('SELECT stamp FROM derivations WHERE model = ?', (model,)).fetchone())
        return None if row is None else cast(str, row['stamp'])

    def set_stamp(self, model: str, stamp: str) -> None:
        with self.connect() as connection, connection:
            _ = connection.execute('INSERT OR REPLACE INTO derivations VALUES (?, ?)', (model, stamp))


def embed_photos(targets: Iterable[Target], cache: Cache, tower: Tower, model: str, download: Download, deadline: float) -> tuple[int, int, int]:
    """Downloads and embeds the photos without an embedding, labeled listings first, until the deadline. A photo the
    site no longer has is missing; one that fails DOWNLOAD_ATTEMPTS times, or that does not decode, has failed. Returns
    the number embedded now, the number reused from another URL with the same content, and the number still waiting."""
    from features import PhotosUnavailable

    known = cache.photos(model)
    waiting = [url for target in targets for url in distinct_photos(target['photos'])
               if url not in known or known[url]['state'] == 'failed' and known[url]['attempts'] < DOWNLOAD_ATTEMPTS]
    waiting = list(dict.fromkeys(waiting))
    embedded = reused = done = 0
    batch: dict[str, tuple[Image.Image, list[tuple[str, int]]]] = {}

    def flush() -> None:
        nonlocal embedded, reused
        if batch:
            for (content, (_, urls)), vector in zip(batch.items(), cast(Iterable[Vector], tower.embed([image for image, _ in batch.values()]))):
                for url, attempts in urls:
                    cache.save(model, url, 'ok', attempts, content, vector)
                reused += len(urls) - 1
            embedded += len(batch)
            batch.clear()

    for url in waiting:
        if time.monotonic() > deadline:
            break
        attempts = (known[url]['attempts'] if url in known else 0) + 1
        done += 1
        try:
            content = download(url)
        except PhotosUnavailable:
            cache.save(model, url, 'failed', attempts)
            continue
        if content is None:
            cache.save(model, url, 'missing', attempts)
            continue
        digest = hashlib.sha256(content).hexdigest()
        if digest in batch:
            batch[digest][1].append((url, attempts))
            continue
        if cache.vector(model, digest) is not None:
            cache.save(model, url, 'ok', attempts, digest)
            reused += 1
            continue
        try:
            image = decoded(content)
        except (OSError, ValueError):
            cache.save(model, url, 'failed', DOWNLOAD_ATTEMPTS)
            continue
        batch[digest] = (image, [(url, attempts)])
        if len(batch) == BATCH:
            flush()
    flush()
    return embedded, reused, len(waiting) - done


def stamp_of(model: Model, complete: Iterable[tuple[Target, list[str]]]) -> str:
    payload = [[target['id'], target['fingerprint'], contents, target['rooms']] for target, contents in complete]
    return hashlib.sha256(json.dumps([model['identity'], payload], sort_keys=True).encode()).hexdigest()


def complete_targets(targets: Iterable[Target], cache: Cache, model: Model) -> list[tuple[Target, Contents]]:
    """The listings whose every photo is embedded, missing or failed for good, each with its photos' contents."""
    known = cache.photos(model['identity'])
    complete: list[tuple[Target, Contents]] = []
    for target in targets:
        urls = distinct_photos(target['photos'])
        if all(url in known and (known[url]['state'] != 'failed' or known[url]['attempts'] >= DOWNLOAD_ATTEMPTS) for url in urls):
            complete.append((target, [(url, content) for url in urls if known[url]['state'] == 'ok' and (content := known[url]['content']) is not None]))
    return complete


def class_scores(vectors: Vector, texts: Vector, phrasings: Sequence[Phrasing], kind: str, pooling: Pooling) -> tuple[list[str], Vector]:
    """The cosine of each photo with each class of one kind, by its best phrasing (max) or by the mean of its
    phrasings' vectors (mean), and the class names in order."""
    names = list(dict.fromkeys(name for entry_kind, name, _ in phrasings if entry_kind == kind))
    columns: list[Vector] = []
    for name in names:
        rows = [index for index, (entry_kind, entry_name, _) in enumerate(phrasings) if entry_kind == kind and entry_name == name]
        columns.append(vectors @ unit(cast(Vector, texts[rows].mean(axis=0))) if pooling == 'mean'
                       else cast(Vector, (vectors @ texts[rows].T).max(axis=1)))
    return names, np.stack(columns, axis=1) if columns else np.zeros((len(vectors), 0), dtype=np.float32)


def derive(complete: Sequence[tuple[Target, Sequence[tuple[str, str]]]], vectors_by_content: Mapping[str, ArrayLike], texts: Vector,
           phrasings: Sequence[Phrasing], model: Rules) -> tuple[dict[str, 'PhotoFeatures'], list['PhotoSimilarity']]:
    """Every listing's probe matches, room types and reread mark, and each two listings' best-match similarity by room.

    A photo's room is its record's when extraction typed it, the encoder's otherwise. Before photos of one room are
    compared, the mean of the store's photos of that room is subtracted, since raw embeddings of interiors sit in a
    narrow cone. A listing's probe match is its best photo's; its similarity to another listing for a room is the best
    pair of their photos of that room, never a mean."""
    photos = [(target, url, content) for target, contents in complete for url, content in contents]
    features: dict[str, PhotoFeatures] = {target['id']: {'fingerprint': target['fingerprint'], 'probes': {}, 'rooms': [], 'reread': False}
                                          for target, _ in complete}
    similarities: list[PhotoSimilarity] = []
    if not photos:
        return features, similarities
    matrix = unit(np.stack([vectors_by_content[content] for _, _, content in photos]))
    room_names, room_scores = class_scores(matrix, texts, phrasings, 'room', model['pooling'])
    predicted = [room_names[index] for index in cast(Iterable[np.intp], room_scores.argmax(axis=1))]
    probe_matrix = matrix - cast(Vector, matrix.mean(axis=0)) if model['probe_rule'] == 'centered' else matrix
    probe_names, probe_scores = class_scores(unit(probe_matrix), texts, phrasings, 'probe', model['pooling'])
    effective: list[str] = []
    for (target, url, _), prediction in zip(photos, predicted):
        effective.append((target['rooms'] or {}).get(url) or prediction)
    for index, (target, url, _) in enumerate(photos):
        entry = features[target['id']]
        entry['rooms'].append(predicted[index])
        for name, value in zip(probe_names, cast(Iterable[np.float32], probe_scores[index])):
            entry['probes'][name] = max(entry['probes'].get(name, -1.0), round(float(value), 6))
    for target, _ in complete:
        if target['rooms']:
            typed = [(effective[index], predicted[index]) for index, (owner, url, _) in enumerate(photos)
                     if owner is target and url in target['rooms']]
            features[target['id']]['reread'] = bool(typed) and sum(first != second for first, second in typed) > REREAD_SHARE * len(typed)
    for room in MATCH_ROOMS:
        indices = [index for index, value in enumerate(effective) if value == room]
        if not indices:
            continue
        centered = unit(matrix[indices] - cast(Vector, matrix[indices].mean(axis=0)))
        owners = [photos[index][0]['id'] for index in indices]
        order = sorted(range(len(indices)), key=lambda position: owners[position])
        centered = centered[order]
        owners = [owners[position] for position in order]
        starts = [position for position in range(len(owners)) if position == 0 or owners[position] != owners[position - 1]]
        values = centered @ centered.T
        best = cast(Vector, np.maximum.reduceat(np.maximum.reduceat(values, starts, axis=0), starts, axis=1))
        listings = [owners[start] for start in starts]
        for row, first in enumerate(listings):
            for column, second in enumerate(listings):
                if first != second:
                    similarities.append((room, first, second, round(float(cast(np.float32, best[row, column])), 6)))
    return features, similarities


def run(arguments: Arguments) -> None:
    from features import download
    from store import ListingStore, store_path

    model = manifest()
    verified = Path(arguments.model) / 'verified.json'
    if not verified.is_file() or cast(Stamp, json.loads(verified.read_text()))['identity'] != model['identity']:
        raise ValueError('The encoder model is not downloaded and verified. Rerun the apt-scraper installer.')
    started = time.monotonic()
    phrasings, texts = text_vectors(model)
    store = ListingStore(store_path())
    cache = Cache(Path(os.environ['HERMES_HOME']) / 'workspace' / 'encoder' / 'embeddings.sqlite3')
    targets = store.photo_targets()
    tower = Tower(arguments.model, model, arguments.threads)
    embedded, reused, waiting = embed_photos(targets, cache, tower, model['identity'], download, started + arguments.budget)
    encoding_seconds = time.monotonic() - started
    complete = complete_targets(targets, cache, model)
    stamp = stamp_of(model, [(target, [content for _, content in contents]) for target, contents in complete])
    saved = None
    if stamp != cache.stamp(model['identity']):
        contents = sorted({content for _, items in complete for _, content in items})
        features, similarities = derive(complete, cache.vectors(model['identity'], contents), texts, phrasings, model)
        saved = store.save_photo_features(model['identity'], features, similarities)
        cache.set_stamp(model['identity'], stamp)
    report: RunReport = {'embedded': embedded, 'reused': reused, 'waiting': waiting, 'listings': len(targets),
                         'complete': len(complete), 'derived': saved, 'encoding_seconds': round(encoding_seconds, 3)}
    print(json.dumps(report))


def check(arguments: Arguments) -> None:
    model = manifest()
    _ = text_vectors(model)
    tower = Tower(arguments.model, model, 1)
    image = Image.new('RGB', (320, 240), (180, 150, 120))
    vector = cast(Vector, tower.embed([image])[0])
    if abs(float(np.linalg.norm(vector)) - 1) > 1e-4:
        raise ValueError('The image tower did not return a usable embedding.')
    print(json.dumps({'model': model['name'], 'dimensions': int(vector.shape[0])}))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    _ = parser.add_argument('command', choices=['run', 'check'])
    _ = parser.add_argument('model', help='the directory holding the downloaded image tower')
    _ = parser.add_argument('--budget', type=float, default=540.0, help='seconds of downloading and embedding per run')
    _ = parser.add_argument('--threads', type=int, default=4)
    arguments = parser.parse_args(argv, namespace=Arguments())
    (run if arguments.command == 'run' else check)(arguments)
    return 0


if __name__ == '__main__':
    sys.exit(main())
