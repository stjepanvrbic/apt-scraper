"""Downloads the image tower's files that model.json pins and verifies them. Standard library only, so the installers
and the cron job run it on Hermes's own Python before the encoder's environment exists."""

import hashlib
import json
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Protocol, TypedDict, cast

CHUNK = 1 << 20
SOCKET_TIMEOUT = 60


class PinnedFile(TypedDict):
    """One entry of the manifest's `files`: where the file comes from and what it must be."""
    filename: str
    bytes: int
    sha256: str
    url: str


class Manifest(TypedDict):
    """The part of model.json this module reads; the encoder reads more of it."""
    identity: str
    files: list[PinnedFile]


class Stamp(TypedDict):
    """verified.json: the identity of the model whose files were verified in its directory."""
    identity: str


class Download(Protocol):
    """A download in progress, as urlopen's response and the tests' in-memory replacements are: read in chunks and
    closed at the end of the with block."""
    def __enter__(self) -> 'Download': ...
    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None, /) -> None: ...
    def read(self, size: int = ..., /) -> bytes: ...


def urlopen(url: str) -> Download:
    """The download's response. The timeout bounds each wait on the socket, so a stalled server ends the install's
    encoder step instead of hanging it; it does not bound the whole download."""
    return cast(Download, urllib.request.urlopen(url, timeout=SOCKET_TIMEOUT))


def verify(path: str | Path, item: PinnedFile) -> None:
    """Raises when the file is not the pinned one: its length and SHA-256 must match."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size != item['bytes']:
        raise ValueError(f'{item["filename"]} is missing or has the wrong length.')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    if digest.hexdigest() != item['sha256']:
        raise ValueError(f'{item["filename"]} does not match its pinned SHA-256.')


def fetch(item: PinnedFile, directory: str | Path, *, opener: Callable[[str], Download] = urlopen) -> int:
    """Downloads one pinned file into directory unless a verified copy is there, through a temporary file that replaces
    the target only once its length and hash match. Returns the bytes downloaded."""
    directory = Path(directory).resolve()
    target = (directory / item['filename']).resolve()
    if not target.is_relative_to(directory):
        raise ValueError(f'{item["filename"]} would be stored outside the model directory.')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size == item['bytes']:
        try:
            verify(target, item)
            return 0
        except ValueError:
            pass
    count = 0
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.part', delete=False) as output:
        temporary = Path(output.name)
    try:
        with opener(item['url']) as response, temporary.open('wb') as output:
            while chunk := response.read(CHUNK):
                count += len(chunk)
                if count > item['bytes']:
                    raise ValueError(f'{item["filename"]} is longer than its pinned length.')
                _ = output.write(chunk)
        verify(temporary, item)
        _ = temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return count


def prepare(manifest: Manifest, directory: str | Path, *, opener: Callable[[str], Download] = urlopen) -> int:
    """Every file of the manifest downloaded and verified, then verified.json written with the model's identity, which
    the encoder requires before it loads anything. Returns the bytes downloaded."""
    directory = Path(directory)
    stamp = directory / 'verified.json'
    stamp.unlink(missing_ok=True)
    count = sum(fetch(item, directory, opener=opener) for item in manifest['files'])
    _ = stamp.write_text(json.dumps({'identity': manifest['identity']}) + '\n', encoding='utf-8')
    return count


def ready(manifest: Manifest, directory: str | Path) -> bool:
    """Whether the manifest's model is downloaded and was verified, without hashing its files again."""
    stamp = Path(directory) / 'verified.json'
    return (stamp.is_file() and cast(Stamp, json.loads(stamp.read_text(encoding='utf-8'))).get('identity') == manifest['identity']
            and all((Path(directory) / item['filename']).is_file() and (Path(directory) / item['filename']).stat().st_size == item['bytes']
                    for item in manifest['files']))
