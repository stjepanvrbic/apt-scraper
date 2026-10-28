"""Downloads the encoder candidates of the sweep, pinned by revision and hash. A developer tool, not shipped; resources do not decide admission."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from encoder_assets import artifact, fetch, metadata, model_files

MODELS = {
    'siglip2-base-224': {'repository': 'onnx-community/siglip2-base-patch16-224-ONNX', 'kind': 'siglip'},
    'siglip2-large-384': {'repository': 'onnx-community/siglip2-large-patch16-384-ONNX', 'kind': 'siglip'},
    'siglip2-so400m-512': {'repository': 'onnx-community/siglip2-so400m-patch16-512-ONNX', 'kind': 'siglip'},
    'siglip2-giant-384': {'repository': 'onnx-community/siglip2-giant-opt-patch16-384-ONNX', 'kind': 'siglip'},
    'siglip2-naflex': {'repository': 'onnx-community/siglip2-base-patch16-naflex-ONNX', 'kind': 'siglip2',
                       'text_repository': 'google/siglip2-base-patch16-naflex'},
    'mobileclip2-s2': {'repository': 'RuteNL/MobileCLIP2-S2-OpenCLIP-ONNX', 'kind': 'openclip'},
    'mobileclip2-s4': {'repository': 'RuteNL/MobileCLIP2-S4-OpenCLIP-ONNX', 'kind': 'openclip'},
    'dfn5b-h14-378': {'repository': 'RuteNL/DFN5B-CLIP-ViT-H-14-378-ONNX', 'kind': 'openclip'},
}


def prepare(name, workspace):
    specification = MODELS[name]
    workspace = Path(workspace)
    info = metadata(specification['repository'], workspace / 'metadata')
    directory = workspace / 'models' / name
    names = {item['rfilename'] for item in info['siblings']}
    selected = [artifact(info, filename) for filename in sorted(names)
                if '/' not in filename and filename.endswith(('.json', '.txt', '.model'))]
    graphs = {}
    for precision, graph in [('fp32', 'visual.onnx' if specification['kind'] == 'openclip' else 'onnx/vision_model.onnx'),
                             ('fp16', 'onnx/vision_model_fp16.onnx'), ('int8', 'onnx/vision_model_int8.onnx')]:
        if graph in names:
            graphs[precision] = graph
            selected.extend(model_files(info, graph))
    text_graph = 'text.onnx' if specification['kind'] == 'openclip' else 'onnx/text_model.onnx'
    if text_graph in names:
        selected.extend(model_files(info, text_graph))
    else:
        text_graph = None

    def downloaded(item):
        result, count = fetch(item, directory)
        print(f'{name}: {item["filename"]} ({count} downloaded bytes)', flush=True)
        return result

    with ThreadPoolExecutor(max_workers=3) as pool:
        locked = list(pool.map(downloaded, selected))
    manifest = {'name': name, 'embedding_space': name, 'repository': info['id'], 'revision': info['sha'],
                'kind': specification['kind'], 'graphs': graphs, 'text_graph': text_graph, 'files': locked}
    (directory / 'files.lock.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path)
    parser.add_argument('models', nargs='+', choices=MODELS)
    args = parser.parse_args()
    for name in args.models:
        prepare(name, args.workspace)


if __name__ == '__main__':
    main()
