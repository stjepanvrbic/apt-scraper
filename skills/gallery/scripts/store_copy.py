"""Copy database files without opening the source through SQLite."""

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast


def copy_database(source: str | Path, destination: str | Path) -> Path:
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if any(Path(str(destination) + suffix).exists() for suffix in ('', '-wal', '-shm', '-journal')) or destination == source:
        raise ValueError('Choose a new destination for the database copy.')
    if Path(str(source) + '-journal').exists():
        raise ValueError('The source has a rollback journal. Copy it after its writer finishes.')
    files = [source]
    journal = Path(str(source) + '-wal')
    if journal.exists():
        files.append(journal)
    before = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in files}
    destination.parent.mkdir(parents=True, exist_ok=True)
    for path in files:
        target = destination if path == source else Path(str(destination) + '-wal')
        _ = shutil.copyfile(path, target)
    after = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in files}
    if before != after or journal.exists() != (journal in files) or Path(str(source) + '-journal').exists():
        raise ValueError('The source changed during copying. This copy is not usable; choose a fresh destination and retry.')
    with closing(sqlite3.connect(destination)) as connection:
        result = cast(list[tuple[str]], connection.execute('PRAGMA quick_check').fetchall())
    if result != [('ok',)]:
        raise ValueError(f'The database copy failed its integrity check: {result}')
    return destination
