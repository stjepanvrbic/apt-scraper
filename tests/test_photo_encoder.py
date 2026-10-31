"""The photo encoder's job in its own environment (onnxruntime, numpy, Pillow): the derivation of the photo columns,
a whole run against a real listing store with a tiny ONNX tower, the cache, the pinned downloads and the launcher."""

import hashlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast, override
from unittest import mock

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ENCODER = ROOT / 'skills/ranking/encoder'
sys.path[:0] = [str(ENCODER), str(ROOT / 'skills/gallery/scripts'), str(ROOT / 'skills/listing-features/scripts')]

import assets  # noqa: E402
import encoder  # noqa: E402
import features  # noqa: E402
from photo_catalog import entries  # noqa: E402
from store import ListingStore  # noqa: E402
from test_feature_record import extraction  # noqa: E402
from test_gallery_store import listing  # noqa: E402


class Run(Protocol):
    """subprocess.run as the launcher calls it and the tests replace it."""
    def __call__(self, command: list[str], **options: object) -> subprocess.CompletedProcess[str]: ...


class EncoderRun(Protocol):
    """scripts/encoder-run.py as the tests drive it."""
    MANIFEST: Path
    CHECK_FAILED: int
    MODEL_FAILED: int
    def environment_python(self, directory: Path) -> Path: ...
    def main(self, arguments: list[str], run: Run = ..., opener: Callable[[str], assets.Download] = ...) -> int: ...


def load_launcher() -> EncoderRun:
    specification = importlib.util.spec_from_file_location('encoder_run', ROOT / 'scripts/encoder-run.py')
    if specification is None or specification.loader is None:
        raise ImportError('encoder-run.py could not be loaded.')
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return cast(EncoderRun, cast(object, module))


encoder_run = load_launcher()
TOWER = ROOT / 'tests/fixtures/tiny_tower.onnx'
MODEL: encoder.Rules = {'pooling': 'max', 'probe_rule': 'raw'}
Color = tuple[int, int, int]


class Quality(TypedDict):
    passed: bool


class LauncherManifest(TypedDict):
    """model.json as the launcher tests write it: the pinned files, the identity and the quality gate's verdict."""
    name: str
    identity: str
    quality: Quality
    files: list[assets.PinnedFile]


class Status(TypedDict):
    """status.json as the launcher writes it: the problem of the last run, or why the model stays off."""
    checked_at: str
    problem: str | None
    off: NotRequired[str]
    last: NotRequired[object]


class Job(TypedDict):
    """The part of a shipped cron job (cron/jobs.json) these tests read."""
    id: str
    script: str
    schedule: str


class Jobs(TypedDict):
    jobs: list[Job]


def target(identifier: str, urls: list[str], rooms: dict[str, str] | None = None) -> encoder.Target:
    return {'id': identifier, 'fingerprint': 'f' * 64, 'photos': urls, 'rooms': rooms}


class DeriveTests(unittest.TestCase):
    phrasings: list[encoder.Phrasing] = [('room', 'kitchen', 'k'), ('room', 'bathroom', 'b'), ('probe', 'carpet', 'c')]
    texts: encoder.Vector = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32)

    def test_a_probe_match_is_the_best_photo_and_rooms_come_from_the_record_before_the_encoder(self) -> None:
        complete = [(target('a', ['u1', 'u2'], {'u1': 'bathroom'}), [('u1', 'c1'), ('u2', 'c2')]),
                    (target('b', ['u3']), [('u3', 'c3')])]
        vectors = {'c1': np.array([1, 0, 0.2]), 'c2': np.array([0.2, 0, 1]), 'c3': np.array([0, 1, 0])}
        features, _ = encoder.derive(complete, vectors, self.texts, self.phrasings, MODEL)
        self.assertEqual(features['a']['rooms'], ['kitchen', 'kitchen'])
        self.assertAlmostEqual(features['a']['probes']['carpet'], float(cast(np.float32, encoder.unit(vectors['c2'])[2])), places=5)
        self.assertEqual(features['b']['rooms'], ['bathroom'])
        self.assertTrue(features['a']['reread'])
        self.assertFalse(features['b']['reread'])

    def test_similarity_is_the_best_pair_of_centered_photos_of_one_room_never_a_mean(self) -> None:
        kitchen = {'u1': 'kitchen', 'u2': 'kitchen', 'u3': 'kitchen', 'u4': 'kitchen'}
        complete = [(target('a', ['u1', 'u2'], kitchen), [('u1', 'c1'), ('u2', 'c2')]),
                    (target('b', ['u3'], kitchen), [('u3', 'c3')]), (target('c', ['u4'], kitchen), [('u4', 'c4')])]
        vectors = {'c1': np.array([1, 0.1, 0]), 'c2': np.array([1, -0.9, 0]), 'c3': np.array([1, 0.12, 0]), 'c4': np.array([1, -0.5, 0.3])}
        _, similarities = encoder.derive(complete, vectors, self.texts, self.phrasings, MODEL)
        values = {(first, second): value for room, first, second, value in similarities if room == 'kitchen'}
        self.assertEqual(set(values), {(a, b) for a in 'abc' for b in 'abc' if a != b})
        matrix = encoder.unit(np.stack([vectors[name] for name in ('c1', 'c2', 'c3', 'c4')]))
        centered = encoder.unit(matrix - matrix.mean(axis=0))
        first, second, third = (cast(encoder.Vector, centered[index]) for index in (0, 1, 2))
        self.assertAlmostEqual(values[('a', 'b')], max(float(first @ third), float(second @ third)), places=5)
        self.assertAlmostEqual(values[('a', 'b')], values[('b', 'a')], places=6)
        self.assertGreater(values[('a', 'b')], 0.9)


def png(color: Color) -> bytes:
    stream = io.BytesIO()
    Image.new('RGB', (40, 30), color).save(stream, format='PNG')
    return stream.getvalue()


class RunTests(unittest.TestCase):
    home: Path
    model: Path
    store: ListingStore
    downloads: list[str]
    colors: dict[str, Color]
    failing: set[str]

    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        self.model = self.home / 'model'
        self.model.mkdir()
        _ = shutil.copy(TOWER, self.model / 'tower.onnx')
        vectors = np.random.default_rng(1).normal(size=(len(entries()), 8)).astype(np.float32)
        np.save(self.home / 'probes.npy', vectors, allow_pickle=False)
        manifest: encoder.Model = {'name': 'tiny', 'identity': 'tiny-1', 'graph': 'tower.onnx', 'output': 'pooler_output', 'dimensions': 8,
                                   'preprocessing': {'resize': 'squash', 'size': 32, 'resample': 'bilinear', 'mean': [0.5] * 3, 'std': [0.5] * 3},
                                   'pooling': 'max',
                                   'probe_rule': 'raw', 'probes': {'sha256': encoder.sha256(self.home / 'probes.npy')}, 'files': []}
        _ = (self.home / 'model.json').write_text(json.dumps(manifest))
        _ = (self.model / 'verified.json').write_text(json.dumps({'identity': 'tiny-1'}))
        for name, value in (('MANIFEST', self.home / 'model.json'), ('PROBES', self.home / 'probes.npy')):
            patcher = mock.patch.object(encoder, name, value)
            _ = patcher.start()
            self.addCleanup(patcher.stop)
        _ = cast(object, self.enterContext(mock.patch.dict(os.environ, {'HERMES_HOME': str(self.home)})))
        self.store = ListingStore(self.home / 'workspace' / 'gallery' / 'listings.sqlite3')
        _ = self.store.update_settings(criteria={'location': ['Logan Square'], 'price': {'min': None, 'max': 3000, 'currency': 'USD', 'period': 'month'}})
        self.downloads = []
        self.colors = {}
        self.failing = set()

    def add(self, identifier: str, colors: list[Color]) -> list[str]:
        urls = [f'https://photos.example.com/{identifier}/{index}.jpg' for index in range(len(colors))]
        self.colors.update(zip(urls, colors))
        saved = self.store.upsert_listing(listing(id=identifier, photos=urls))
        _ = self.store.save_extraction(identifier, saved['content_fingerprint'], extraction(len(urls)), photos=urls, phrases=[])
        return urls

    def download(self, url: str) -> bytes | None:
        self.downloads.append(url)
        if url in self.failing:
            raise features.PhotosUnavailable('timed out')
        return png(self.colors[url]) if url in self.colors else None

    def run_encoder(self, budget: float = 60.0) -> encoder.RunReport:
        lines: list[str] = []
        with mock.patch.object(features, 'download', self.download), mock.patch('builtins.print', lines.append):
            _ = encoder.main(['run', str(self.model), '--budget', str(budget), '--threads', '1'])
        return cast(encoder.RunReport, json.loads(lines[-1]))

    def test_a_run_embeds_each_photo_once_and_saves_the_photo_columns(self) -> None:
        _ = self.add('zillow:1', [(200, 30, 30), (30, 200, 30), (30, 30, 200)])
        _ = self.add('zillow:2', [(200, 30, 30), (10, 10, 10)])
        report = self.run_encoder()
        self.assertEqual((report['embedded'], report['reused'], report['waiting'], report['complete'], report['derived']), (4, 1, 0, 2, 2))
        with self.store.connect() as connection:
            rows = {cast(str, row['listing_id']): row for row in cast(Iterable[sqlite3.Row], connection.execute('SELECT * FROM photo_features'))}
            count = cast(sqlite3.Row, connection.execute('SELECT COUNT(*) FROM photo_similarity').fetchone())
            similarity = cast(int, count[0])
        self.assertEqual(set(rows), {'zillow:1', 'zillow:2'})
        probes = cast(dict[str, float], json.loads(cast(str, rows['zillow:1']['probes'])))
        self.assertEqual(len(probes), len({name for kind, name, _ in entries() if kind == 'probe'}))
        self.assertGreater(similarity, 0)
        self.assertEqual(len(self.downloads), 5)
        again = self.run_encoder()
        self.assertEqual((again['embedded'], again['derived']), (0, None))
        self.assertEqual(len(self.downloads), 5)
        _ = self.add('zillow:3', [(31, 200, 30)])
        third = self.run_encoder()
        self.assertEqual((third['embedded'], third['derived']), (1, 3))

    def test_a_failing_photo_is_tried_three_times_and_a_spent_budget_leaves_photos_waiting(self) -> None:
        urls = self.add('zillow:1', [(200, 30, 30), (30, 200, 30)])
        self.failing.add(urls[1])
        for expected in (0, 0, 1):
            self.assertEqual(self.run_encoder()['complete'], expected)
        self.assertEqual(self.downloads.count(urls[1]), 3)
        _ = self.run_encoder()
        self.assertEqual(self.downloads.count(urls[1]), 3)
        _ = self.add('zillow:2', [(1, 2, 3)])
        self.assertEqual(self.run_encoder(budget=-1)['waiting'], 1)

    def test_an_unverified_model_stops_the_run_and_check_embeds_a_generated_image(self) -> None:
        lines: list[str] = []
        with mock.patch('builtins.print', lines.append):
            _ = encoder.main(['check', str(self.model)])
        self.assertEqual(json.loads(lines[-1]), {'model': 'tiny', 'dimensions': 8})
        _ = (self.model / 'verified.json').write_text(json.dumps({'identity': 'other'}))
        with self.assertRaisesRegex(ValueError, 'not downloaded and verified'):
            _ = self.run_encoder()

    def test_preprocessing_squashes_or_crops_and_normalizes_each_channel(self) -> None:
        image = Image.new('RGB', (300, 200), (255, 0, 51))
        image.paste((0, 0, 0), (0, 0, 50, 200))
        squashed = encoder.preprocess(image, {'resize': 'squash', 'size': 30, 'resample': 'bilinear', 'mean': [0.5] * 3, 'std': [0.5] * 3})
        self.assertEqual(squashed.shape, (3, 30, 30))
        np.testing.assert_allclose(squashed[:, 15, 29], [1.0, -1.0, -0.6], atol=1e-6)
        self.assertAlmostEqual(float(cast(np.float32, squashed[0, 15, 0])), -1.0, places=6)
        cropped = encoder.preprocess(image, {'resize': 'shortest', 'size': 20, 'resample': 'bicubic', 'mean': [0.0] * 3, 'std': [2.0] * 3})
        self.assertEqual(cropped.shape, (3, 20, 20))
        np.testing.assert_allclose(cropped[:, 10, 10], [0.5, 0.0, 0.1], atol=1e-6)


class AssetTests(unittest.TestCase):
    directory: Path
    data: bytes
    item: assets.PinnedFile

    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.data = b'weights'
        self.item = {'filename': 'onnx/tower.onnx', 'bytes': len(self.data), 'sha256': hashlib.sha256(self.data).hexdigest(),
                     'url': 'https://models.example/tower.onnx'}

    def test_files_are_verified_downloaded_once_and_kept_inside_the_model_directory(self) -> None:
        opened: list[str] = []

        def opener(url: str) -> io.BytesIO:
            opened.append(url)
            return io.BytesIO(self.data)
        manifest: assets.Manifest = {'identity': 'm', 'files': [self.item]}
        self.assertEqual(assets.prepare(manifest, self.directory, opener=opener), len(self.data))
        self.assertTrue(assets.ready(manifest, self.directory))
        self.assertEqual(assets.prepare(manifest, self.directory, opener=opener), 0)
        self.assertEqual(len(opened), 1)
        with self.assertRaisesRegex(ValueError, 'outside'):
            _ = assets.fetch({**self.item, 'filename': '../escape'}, self.directory, opener=opener)

    def test_a_wrong_download_is_never_kept(self) -> None:
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            _ = assets.fetch(self.item, self.directory, opener=lambda url: io.BytesIO(b'WEIGHTS'))
        self.assertEqual(list(self.directory.rglob('*.*')), [])
        with self.assertRaisesRegex(ValueError, 'longer'):
            _ = assets.fetch(self.item, self.directory, opener=lambda url: io.BytesIO(self.data + b'!'))


class LauncherTests(unittest.TestCase):
    home: Path
    manifest: LauncherManifest
    data: bytes
    calls: list[list[str]]
    check_code: int

    @override
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name)
        data = b'weights'
        self.manifest = {'name': 'tiny', 'identity': 'tiny-1', 'quality': {'passed': True}, 'files': [
            {'filename': 'tower.onnx', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'url': 'https://models.example/t'}]}
        _ = (self.home / 'model.json').write_text(json.dumps(self.manifest))
        self.data = data
        self.calls = []
        self.check_code = 0
        for patcher in (mock.patch.object(encoder_run, 'MANIFEST', self.home / 'model.json'),
                        mock.patch.dict(os.environ, {'HERMES_HOME': str(self.home)})):
            _ = patcher.start()
            self.addCleanup(patcher.stop)

    def fake_run(self, command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if command[1:3] == ['-m', 'venv']:
            python = encoder_run.environment_python(Path(command[-1]))
            python.parent.mkdir(parents=True, exist_ok=True)
            _ = python.write_text('')
        if 'check' in command:
            return subprocess.CompletedProcess(command, self.check_code, '', 'no tower')
        if 'run' in command:
            return subprocess.CompletedProcess(command, 0, json.dumps({'embedded': 3}) + '\n', '')
        return subprocess.CompletedProcess(command, 0)

    def launch(self, arguments: list[str], opener: Callable[[str], assets.Download] | None = None) -> tuple[int, list[str]]:
        lines: list[str] = []

        def record(*values: object, **_options: object) -> None:
            lines.append(' '.join(map(str, values)))
        with mock.patch('builtins.print', record):
            code = encoder_run.main(arguments, run=self.fake_run, opener=opener or (lambda url: io.BytesIO(self.data)))
        return code, lines

    def status(self) -> Status:
        return cast(Status, json.loads((self.home / 'workspace' / 'encoder' / 'status.json').read_text()))

    def test_install_builds_the_environment_downloads_the_tower_and_checks_it(self) -> None:
        self.assertEqual(self.launch(['install'])[0], 0)
        self.assertTrue(assets.ready(self.manifest, self.home / 'workspace' / 'encoder' / 'model'))
        pip = next(command for command in self.calls if 'pip' in command)
        self.assertIn('--require-hashes', pip)
        self.check_code = 1
        self.assertEqual(self.launch(['install'])[0], encoder_run.CHECK_FAILED)
        (self.home / 'workspace' / 'encoder' / 'model' / 'tower.onnx').unlink()
        self.assertEqual(self.launch(['install'], opener=lambda url: io.BytesIO(b'other!!'))[0], encoder_run.MODEL_FAILED)

    def test_the_cron_run_never_fails_and_records_its_problem(self) -> None:
        code, lines = self.launch([], opener=lambda url: io.BytesIO(b'other!!'))
        self.assertEqual((code, lines[-1]), (0, '{"wakeAgent": false}'))
        self.assertIn('could not be set up', self.status()['problem'] or '')
        code, lines = self.launch([])
        self.assertEqual((code, lines[-1]), (0, '{"wakeAgent": false}'))
        self.assertEqual(self.status(), {**self.status(), 'problem': None, 'last': {'embedded': 3}})

    def test_the_first_run_builds_the_environment_and_downloads_the_tower_itself(self) -> None:
        code, lines = self.launch([])
        self.assertEqual((code, lines[-1]), (0, '{"wakeAgent": false}'))
        self.assertTrue(assets.ready(self.manifest, self.home / 'workspace' / 'encoder' / 'model'))
        self.assertEqual([command[1:3] for command in self.calls][:2], [['-m', 'venv'], ['-m', 'pip']])
        self.assertEqual(self.status().get('last'), {'embedded': 3})

    def test_a_model_that_failed_its_quality_measurements_is_never_built_or_downloaded(self) -> None:
        _ = (self.home / 'model.json').write_text(json.dumps({**self.manifest, 'quality': {'passed': False}}))
        code, lines = self.launch(['install'], opener=lambda url: self.fail('downloaded'))
        self.assertEqual(code, 0)
        self.assertIn('did not pass its quality measurements', lines[-1])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.launch([]), (0, ['{"wakeAgent": false}']))
        status = self.status()
        assert 'off' in status
        self.assertIn('did not pass', status['off'])

    def test_the_shipped_job_runs_the_launcher_and_the_requirements_are_pinned(self) -> None:
        jobs = cast(Jobs, json.loads((ROOT / 'cron' / 'jobs.json').read_text(encoding='utf-8')))['jobs']
        [job] = [job for job in jobs if job['id'] == 'apt-scraper-encoder']
        self.assertEqual((job['script'], job['schedule']), ('encoder-run.py', 'every 10m'))
        self.assertIn("cron', 'resume', 'apt-scraper-encoder'", (ROOT / 'install.ps1').read_text(encoding='utf-8'))
        self.assertIn('cron resume apt-scraper-encoder', (ROOT / 'install.sh').read_text(encoding='utf-8'))
        for line in (ENCODER / 'requirements.txt').read_text(encoding='utf-8').splitlines():
            if line and not line.startswith((' ', '#')):
                self.assertRegex(line, r'^[a-z0-9-]+==[0-9.]+ ')


if __name__ == '__main__':
    _ = unittest.main()
