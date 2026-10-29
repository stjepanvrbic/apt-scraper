import json
import os
from pathlib import Path

from shapes import is_object, loads


def scratch_directory() -> Path:
    return (Path(os.environ['HERMES_HOME']) / 'cache' / 'scratch').resolve()


def read_object(path: str | Path, name: str) -> dict[str, object]:
    """The JSON object in a file an agent wrote."""
    try:
        value = loads(Path(path).read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as error:
        raise ValueError(f'{name} must be a JSON object: {error}') from None
    if not is_object(value):
        raise ValueError(f'{name} must be a JSON object.')
    return value


def read_scratch_object(file: str | Path, name: str) -> tuple[Path, dict[str, object]]:
    """Agents pass structured input in a file they write to the profile's scratch directory, because Hermes
    asks the user to approve every heredoc into Python and shell escaping mangles quotes."""
    scratch = scratch_directory()
    path = Path(file).resolve()
    if not path.is_relative_to(scratch):
        raise ValueError(f'{name} files must be in the scratch directory {scratch}.')
    return path, read_object(path, name)
