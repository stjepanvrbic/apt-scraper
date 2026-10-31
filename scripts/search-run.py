"""Runs the apartment search for the Hermes cron job apt-scraper-search.

Commands:
  search-run.py           one search pass, as the cron job runs it
  search-run.py install   build the search's Python environment by hand, with Hermes's Python.

The search needs curl_cffi, which Hermes's Python lacks, so it runs in its own environment under
HERMES_HOME/workspace/search/environment, built from the hash-pinned wheels in the search skill's
requirements.txt. The first eligible cron run builds it, and later runs rebuild it only when Hermes's Python or
the requirements changed.

Hermes wakes the model whenever a cron script fails (cron/scheduler_prompt.py, "Script Error"). So the cron
run never fails: it records any problem for search.py status and ends with {"wakeAgent": false}.

Exit codes of install: 0 built, 5 the environment could not be built.
"""

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, TextIO

SKILLS = Path(__file__).resolve().parents[1] / 'skills'
REQUIREMENTS = SKILLS / 'search' / 'requirements.txt'
SEARCH = SKILLS / 'search' / 'scripts' / 'search.py'
ENVIRONMENT_FAILED = 5
NO_WAKE = json.dumps({'wakeAgent': False})


class Runner(Protocol):
    """subprocess.run, as this script calls it: a command with the environment to run it in."""

    def __call__(self, command: list[str], /, *, env: Mapping[str, str], check: bool = False, stdout: TextIO | None = None,
                 stderr: int | None = None, text: bool = False, encoding: str | None = None,
                 errors: str | None = None) -> subprocess.CompletedProcess[str]: ...


def environment_python(directory: Path) -> Path:
    return directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def isolated() -> dict[str, str]:
    """Hermes may point PYTHONPATH at its own packages, which must not load into the search's Python."""
    return {key: value for key, value in os.environ.items() if key not in ('PYTHONPATH', 'PYTHONHOME')}


def stamp() -> str:
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest() + '\n' + sys.version


def build(directory: Path, run: Runner) -> None:
    stamp_file = directory / 'installed.txt'
    stamp_file.unlink(missing_ok=True)
    _ = run([sys.executable, '-m', 'venv', '--clear', str(directory)], check=True, env=isolated(), stdout=sys.stderr)
    _ = run([str(environment_python(directory)), '-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '--require-hashes',
             '--only-binary', ':all:', '--no-deps', '-r', str(REQUIREMENTS)], check=True, env=isolated(), stdout=sys.stderr)
    _ = stamp_file.write_text(stamp(), encoding='utf-8')


def current(directory: Path) -> bool:
    stamp_file = directory / 'installed.txt'
    return stamp_file.exists() and stamp_file.read_text(encoding='utf-8') == stamp()


def search_once(directory: Path, run: Runner) -> None:
    sys.path[:0] = [str(SKILLS / 'gallery' / 'scripts'), str(SKILLS / 'search' / 'scripts')]
    from state import SearchState
    from store import ListingStore, store_path

    path = store_path()
    state = SearchState(path.parent / 'search.sqlite3')
    if state.missing_settings(ListingStore(path)):
        print('The interview is not complete, so nothing was searched.')
        print(NO_WAKE)
        return
    problem = None
    # The first eligible run builds the environment; later runs rebuild it when Hermes's Python or the requirements changed.
    if not environment_python(directory).exists() or not current(directory):
        try:
            build(directory, run)
        except (subprocess.CalledProcessError, OSError) as error:
            problem = f'The search environment could not be built for Python {sys.version.split()[0]}: {error}'
    if problem is None:
        result = run([str(environment_python(directory)), str(SEARCH), 'run'], env=isolated(), stderr=subprocess.PIPE, text=True,
                     encoding='utf-8', errors='replace')
        if result.returncode:
            last = (result.stderr or '').strip().splitlines()[-1:] or ['no error output']
            problem = f'The search stopped with exit code {result.returncode}: {last[0]}'
    state.set_problem(problem)
    if problem:
        print(problem)
        print(NO_WAKE)


def main(arguments: list[str], run: Runner = subprocess.run) -> int:
    home = Path(os.environ['HERMES_HOME'])
    directory = home / 'workspace' / 'search' / 'environment'
    if arguments == ['install']:
        try:
            build(directory, run)
        except (subprocess.CalledProcessError, OSError) as error:
            print(f'The search environment could not be built: {error}', file=sys.stderr)
            return ENVIRONMENT_FAILED
        print(f'Search environment ready in {directory}.')
        return 0
    try:
        search_once(directory, run)
    except Exception as error:
        print(f'The search could not start: {type(error).__name__}: {error}')
        print(NO_WAKE)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
