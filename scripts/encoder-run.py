"""Runs the photo encoder for the Hermes cron job apt-scraper-encoder.

Commands:
  encoder-run.py           one pass, as the cron job runs it: embeds the photos of new listing contents for at most
                           9 minutes and derives the ranking's photo columns (skills/ranking/encoder/encoder.py)
  encoder-run.py install   build the encoder's Python environment and download and verify its image tower by hand,
                           with Hermes's Python. The first cron run does the same.

The encoder needs onnxruntime and numpy, which Hermes's Python lacks, so it runs in its own environment under
HERMES_HOME/workspace/encoder/environment, built from the hash-pinned wheels in the encoder's requirements.txt. Its
image tower is downloaded into HERMES_HOME/workspace/encoder/model and verified against the hashes model.json pins.
The first cron run builds and downloads; later runs rebuild the environment only when Hermes's Python or the
requirements changed. A model whose quality
measurements in model.json did not pass can never turn on, so neither command builds or downloads anything for it.

Hermes wakes the model whenever a cron script fails (cron/scheduler_prompt.py, "Script Error"), and the encoder has
nothing for the model to do. So the cron run never fails: it records any problem in status.json, which
ranking.py status reports, and ends with {"wakeAgent": false}.

Exit codes of install: 0 ready, 5 the environment could not be built, 6 the image tower could not be downloaded or
verified, 7 the environment could not load the image tower.
"""

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, TextIO, TypedDict, cast

SKILLS = Path(__file__).resolve().parents[1] / 'skills'
ENCODER = SKILLS / 'ranking' / 'encoder'
REQUIREMENTS = ENCODER / 'requirements.txt'
MANIFEST = ENCODER / 'model.json'
BUDGET_SECONDS = 540
ENVIRONMENT_FAILED, MODEL_FAILED, CHECK_FAILED = 5, 6, 7
NO_WAKE = json.dumps({'wakeAgent': False})

sys.path.insert(0, str(ENCODER))

from assets import Download, Manifest, prepare, ready, urlopen  # noqa: E402


class Quality(TypedDict):
    passed: bool


class ModelManifest(Manifest):
    """The part of model.json the installer and the cron run read beyond the pinned files: the gate's verdict."""
    name: str
    quality: Quality


class Run(Protocol):
    """subprocess.run as this script calls it; the tests replace it with a recorder that returns a CompletedProcess."""
    def __call__(self, args: list[str], *, check: bool = ..., env: Mapping[str, str] = ..., stdout: int | TextIO = ...,
                 stderr: int = ..., text: bool = ..., encoding: str = ..., errors: str = ...) -> subprocess.CompletedProcess[str]: ...


def environment_python(directory: Path) -> Path:
    return directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def isolated() -> dict[str, str]:
    """Hermes may point PYTHONPATH at its own packages, which must not load into the encoder's Python."""
    return {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME')}


def stamp() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest() + '\n' + sys.version


def build(directory: Path, run: Run) -> None:
    stamp_file = directory / 'installed.txt'
    stamp_file.unlink(missing_ok=True)
    _ = run([sys.executable, '-m', 'venv', '--clear', str(directory)], check=True, env=isolated(), stdout=sys.stderr)
    _ = run([str(environment_python(directory)), '-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '--require-hashes',
             '--only-binary', ':all:', '--no-deps', '-r', str(REQUIREMENTS)], check=True, env=isolated(), stdout=sys.stderr)
    _ = stamp_file.write_text(stamp(), encoding='utf-8')


def current(directory: Path) -> bool:
    stamp_file = directory / 'installed.txt'
    return stamp_file.exists() and stamp_file.read_text(encoding='utf-8') == stamp()


def paths(home: Path) -> tuple[Path, Path, Path]:
    workspace = home / 'workspace' / 'encoder'
    return workspace / 'environment', workspace / 'model', workspace / 'status.json'


def write_status(path: Path, **values: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps({'checked_at': datetime.now(timezone.utc).isoformat(), **values}) + '\n', encoding='utf-8')


def failed_gate(manifest: ModelManifest) -> str | None:
    """Why the shipped model stays off for good, or None when its quality measurements passed."""
    if manifest['quality']['passed']:
        return None
    return (f'The photo encoder {manifest["name"]} did not pass its quality measurements, so it is not installed and the '
            'ranking uses no photo embedding.')


def install(home: Path, run: Run, opener: Callable[[str], Download]) -> int:
    environment, model, status = paths(home)
    manifest = cast(ModelManifest, json.loads(MANIFEST.read_text(encoding='utf-8')))
    if reason := failed_gate(manifest):
        write_status(status, problem=None, off=reason)
        print(reason)
        return 0
    try:
        build(environment, run)
    except (subprocess.CalledProcessError, OSError) as error:
        print(f'The encoder environment could not be built: {error}', file=sys.stderr)
        return ENVIRONMENT_FAILED
    size = sum(item['bytes'] for item in manifest['files'])
    print(f'Downloading the photo encoder {manifest["name"]} ({size / 1e6:,.0f} MB) unless it is already here.', file=sys.stderr)
    try:
        _ = prepare(manifest, model, opener=opener)
    except (OSError, ValueError) as error:
        print(f'The photo encoder could not be downloaded: {error}', file=sys.stderr)
        return MODEL_FAILED
    result = run([str(environment_python(environment)), str(ENCODER / 'encoder.py'), 'check', str(model)], env=isolated(),
                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        print(f'The encoder environment could not load the image tower: {(result.stderr or "").strip()}', file=sys.stderr)
        return CHECK_FAILED
    write_status(status, problem=None)
    print(f'Photo encoder ready in {model}.')
    return 0


def encode_once(home, run, opener):
    environment, model, status = paths(home)
    manifest = cast(ModelManifest, json.loads(MANIFEST.read_text(encoding='utf-8')))
    if reason := failed_gate(manifest):
        write_status(status, problem=None, off=reason)
        print(NO_WAKE)
        return
    problem, summary = None, None
    # The first run builds the environment and downloads the image tower, as `install` does by hand; later runs
    # rebuild the environment when Hermes's Python or the requirements changed.
    if not environment_python(environment).exists() or not ready(manifest, model) or not current(environment):
        if (code := install(home, run, opener)) != 0:
            problem = f"The photo encoder could not be set up (exit code {code}). Run encoder-run.py install with Hermes's Python to see why."
    if problem is None:
        result = run([str(environment_python(environment)), str(ENCODER / 'encoder.py'), 'run', str(model), '--budget', str(BUDGET_SECONDS)],
                     env=isolated(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        if result.returncode:
            last = (result.stderr or '').strip().splitlines()[-1:] or ['no error output']
            problem = f'The encoder stopped with exit code {result.returncode}: {last[0]}'
        else:
            summary = cast(object, json.loads(result.stdout.strip().splitlines()[-1]))
    write_status(status, problem=problem, last=summary)
    if problem:
        print(problem)
    print(NO_WAKE)


def main(arguments: list[str], run: Run = subprocess.run, opener: Callable[[str], Download] = urlopen) -> int:
    home = Path(os.environ['HERMES_HOME'])
    if arguments == ['install']:
        return install(home, run, opener)
    try:
        encode_once(home, run, opener)
    except Exception as error:
        print(f'The encoder could not start: {type(error).__name__}: {error}')
        print(NO_WAKE)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
