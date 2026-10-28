"""Measures what the chosen image tower costs a user, as information, never as a gate. A developer tool, not shipped.

  encoder_resources.py <sweep-work> <models-directory> <variant> --photos N [--threads 4]

It loads the tower the way the shipped encoder.py does, embeds the first N photos of the sweep's manifest one batch of
8 at a time on the given threads, and reports the load time, the seconds per photo, the time scaled to 3,000 photos,
the peak resident memory of the process and the bytes a user downloads.
"""

import argparse
import ctypes
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'profile/skills/ranking/encoder'), str(ROOT / 'tests')]


def peak_resident_bytes():
    if os.name == 'nt':
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD),
                        *[(name, ctypes.c_size_t) for name in ('peak_working', 'working', 'peak_paged', 'paged',
                                                              'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.WinDLL('kernel32').GetCurrentProcess
        process.restype = ctypes.c_void_p
        info = ctypes.WinDLL('psapi').GetProcessMemoryInfo
        info.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), wintypes.DWORD]
        if not info(process(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError()
        return counters.peak_working
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == 'darwin' else value * 1024


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('work', type=Path)
    parser.add_argument('models', type=Path)
    parser.add_argument('variant')
    parser.add_argument('--photos', type=int, default=300)
    parser.add_argument('--threads', type=int, default=4)
    arguments = parser.parse_args(argv)
    import encoder
    import numpy as np
    from encoder_sweep import variants
    from PIL import Image, ImageOps

    spec = variants(arguments.models)[arguments.variant]
    directory = arguments.models / spec['model']
    model = {'graph': spec['graph'], 'output': 'pooler_output' if spec['kind'] == 'siglip' else 'image_embeddings',
             'dimensions': None, 'preprocessing': spec['preprocessing']}
    started = time.perf_counter()
    tower = encoder.Tower(directory, model, arguments.threads)
    load = time.perf_counter() - started
    photos = json.loads((arguments.work / 'manifest.json').read_text(encoding='utf-8'))['photos'][:arguments.photos]
    started = time.perf_counter()
    for start in range(0, len(photos), encoder.BATCH):
        images = []
        for photo in photos[start:start + encoder.BATCH]:
            with Image.open(photo['path']) as source:
                images.append(ImageOps.exif_transpose(source).convert('RGB'))
        pixels = [encoder.preprocess(image, model['preprocessing']) for image in images]
        tower.session.run([model['output']], {tower.input.name: np.stack(pixels).astype(tower.dtype)})
    seconds = time.perf_counter() - started
    graph = directory / spec['graph']
    download = sum(path.stat().st_size for path in graph.parent.glob(graph.name + '*'))
    print(json.dumps({'variant': arguments.variant, 'threads': arguments.threads, 'photos': len(photos), 'load_seconds': round(load, 2),
                      'seconds': round(seconds, 1), 'seconds_per_photo': round(seconds / len(photos), 3),
                      'seconds_for_3000_photos': round(3000 * seconds / len(photos)), 'peak_resident_bytes': peak_resident_bytes(),
                      'download_bytes': download}))


if __name__ == '__main__':
    main(sys.argv[1:])
