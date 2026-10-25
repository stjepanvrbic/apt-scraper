import json
import os
from pathlib import Path


def read_scratch_object(file, name):
    """Agents pass structured input in a file they write to the profile's scratch directory, because Hermes
    asks the user to approve every heredoc into Python and shell escaping mangles quotes."""
    scratch = (Path(os.environ['HERMES_HOME']) / 'cache' / 'scratch').resolve()
    path = Path(file).resolve()
    if not path.is_relative_to(scratch):
        raise ValueError(f'{name} files must be in the scratch directory {scratch}.')
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as error:
        raise ValueError(f'{name} must be a JSON object: {error}') from None
    if not isinstance(value, dict):
        raise ValueError(f'{name} must be a JSON object.')
    return path, value
