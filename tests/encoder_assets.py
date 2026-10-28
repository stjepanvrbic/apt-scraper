"""Pinned public model artifacts, verified before local inference."""

import hashlib
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen


def metadata(repository, cache):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*', repository):
        raise ValueError('Use a model repository in owner/name form.')
    path = Path(cache) / (repository.replace('/', '--') + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    with urlopen(f'https://huggingface.co/api/models/{repository}?blobs=true') as response:
        result = json.load(response)
    if not re.fullmatch('[0-9a-f]{40}', result['sha']):
        raise ValueError('The model repository did not supply a pinned revision.')
    path.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def artifact(info, filename):
    item = next(item for item in info['siblings'] if item['rfilename'] == filename)
    return {'filename': filename, 'bytes': item['size'],
            'url': f'https://huggingface.co/{info["id"]}/resolve/{info["sha"]}/{quote(filename, safe="/")}',
            **({'sha256': item['lfs']['sha256']} if 'lfs' in item else {'git_blob': item['blobId']})}


def verify(path, item):
    if path.stat().st_size != item['bytes']:
        raise ValueError(f'Wrong artifact length: {item["filename"]}')
    sha = hashlib.sha256()
    blob = hashlib.sha1(f'blob {item["bytes"]}\0'.encode())
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            sha.update(chunk)
            blob.update(chunk)
    if ('sha256' in item and sha.hexdigest() != item['sha256']) or ('git_blob' in item and blob.hexdigest() != item['git_blob']):
        raise ValueError(f'Wrong artifact checksum: {item["filename"]}')
    return sha.hexdigest()


def fetch(item, directory, *, opener=urlopen):
    directory = Path(directory).resolve()
    target = (directory / item['filename']).resolve()
    if not target.is_relative_to(directory):
        raise ValueError(f'The artifact must stay inside its model directory: {item["filename"]!r} resolved to {target}, outside {directory}.')
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return {**item, 'sha256': verify(target, item)}, 0
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.part', delete=False) as output:
        temporary = Path(output.name)
        try:
            count = 0
            with opener(item['url']) as response:
                while chunk := response.read(min(1024 * 1024, item['bytes'] - count + 1)):
                    count += len(chunk)
                    if count > item['bytes']:
                        raise ValueError('The artifact exceeds its declared length.')
                    output.write(chunk)
            output.close()
            checksum = verify(temporary, item)
            temporary.replace(target)
        finally:
            output.close()
            temporary.unlink(missing_ok=True)
    return {**item, 'sha256': checksum}, count


def model_files(info, graph):
    prefix = graph.removesuffix('.onnx')
    return [artifact(info, item['rfilename']) for item in info['siblings']
            if item['rfilename'] == graph or item['rfilename'].startswith(graph + '_')
            or item['rfilename'] == graph + '.data' or item['rfilename'] == prefix + '.data']


def checked_graph(directory, graph):
    directory = Path(directory).resolve()
    lock = json.loads((directory / 'files.lock.json').read_text())
    selected = graph_assets(lock, graph)
    if not any(item['filename'] == graph for item in selected):
        raise ValueError('The model graph is not in the verified artifact manifest.')
    for item in selected:
        path = (directory / item['filename']).resolve()
        if not path.is_relative_to(directory):
            raise ValueError('The artifact must stay inside its model directory.')
        verify(path, item)
    return selected


def graph_assets(lock, graph):
    prefix = graph.removesuffix('.onnx')
    return [item for item in lock['files'] if item['filename'] == graph or item['filename'].startswith(graph + '_')
            or item['filename'] in (graph + '.data', prefix + '.data')]
