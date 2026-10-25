"""Image and text embeddings of the listings, computed locally on the CPU once per listing content and cached in the
listing store.

Commands:
  embeddings.py download   download the pinned models from Hugging Face and check their weights
  embeddings.py embed      embed every listing whose current content has a feature record and no embeddings yet

Exit codes: 0 done, 1 a step failed, 2 usage error.

Runs in the embedding environment built from requirements-embeddings.txt, which has PyTorch, OpenCLIP and
sentence-transformers. Every photo of the feature record is embedded with CLIP ViT-B/32 and the unit vectors are
averaged over all photos ("photos") and over each room type the record gives ("photos:<room type>"). The
description and the record's traits and concerns are embedded in chunks with all-MiniLM-L6-v2 and averaged ("text").
"""

import argparse
import hashlib
import io
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(SKILLS / 'gallery' / 'scripts'), str(SKILLS / 'listing-features' / 'scripts')]

from features import DOWNLOADS, PhotosUnavailable, download  # noqa: E402
from record import ROOM_TYPES  # noqa: E402
from store import ListingStore, store_path  # noqa: E402

CHUNK_WORDS = 150
BATCH = 32
ROOM_PROMPTS = {
    'kitchen': 'a photo of a kitchen', 'bathroom': 'a photo of a bathroom', 'bedroom': 'a photo of a bedroom',
    'living': 'a photo of a living room', 'dining': 'a photo of a dining room', 'exterior': 'a photo of the outside of a building',
    'view': 'a photo of the view from a window', 'outdoor space': 'a photo of a balcony, patio or yard',
    'laundry': 'a photo of a washer and dryer', 'floor plan': 'a floor plan drawing',
    'building amenity': 'a photo of a building lobby, gym or amenity room', 'other': 'a photo of a hallway or closet',
}


@dataclass(frozen=True)
class Pin:
    """A model at one Hugging Face revision: the files it needs, each weight file with its SHA-256."""
    repository: str
    revision: str
    files: tuple
    weights: dict

    @property
    def name(self):
        return f'{self.repository}@{self.revision}'

    def directory(self, models):
        return Path(models) / self.repository.replace('/', '--') / self.revision


IMAGE_MODEL = Pin('timm/vit_base_patch32_clip_224.openai', 'a6f597a30f7b82c51704746581f9a4e41421e878',
                  ('open_clip_config.json',),
                  {'open_clip_model.safetensors': 'e6d1bd7789aa45192b3bf90570a789b478bae1b74ebcce7eddd908e83a2b7c31'})
TEXT_MODEL = Pin('sentence-transformers/all-MiniLM-L6-v2', '1110a243fdf4706b3f48f1d95db1a4f5529b4d41',
                 ('modules.json', 'config.json', 'config_sentence_transformers.json', 'sentence_bert_config.json', '1_Pooling/config.json',
                  'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt'),
                 {'model.safetensors': '53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db'})
MODELS = (IMAGE_MODEL, TEXT_MODEL)


def models_directory():
    if 'HERMES_HOME' not in os.environ:
        raise ValueError('HERMES_HOME is not set, so the models directory is unknown.')
    return Path(os.environ['HERMES_HOME']) / 'workspace' / 'embeddings' / 'models'


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        for block in iter(lambda: file.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def download_models(models):
    """Downloads each pinned model's files at its revision into models and checks every weight file's SHA-256. A file
    already there is checked, not downloaded again."""
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    from huggingface_hub import hf_hub_download

    for pin in MODELS:
        target = pin.directory(models)
        for name in (*pin.files, *pin.weights):
            if not (target / name).is_file():
                hf_hub_download(pin.repository, name, revision=pin.revision, local_dir=target)
        for name, expected in pin.weights.items():
            if sha256(target / name) != expected:
                raise ValueError(f'{target / name} does not have the pinned SHA-256 {expected}. Delete it and download again.')


class Encoders:
    """The pinned CLIP and sentence models on the CPU."""

    def __init__(self, models):
        import open_clip
        import torch
        from sentence_transformers import SentenceTransformer

        self.torch = torch
        clip_directory = IMAGE_MODEL.directory(models)
        configuration = json.loads((clip_directory / 'open_clip_config.json').read_text(encoding='utf-8'))
        self.clip, _, self.preprocess = open_clip.create_model_and_transforms(
            'ViT-B-32-quickgelu' if configuration['model_cfg'].get('quick_gelu') else 'ViT-B-32',
            pretrained=str(clip_directory / 'open_clip_model.safetensors'), device='cpu')
        self.clip.eval()
        self.tokenizer = open_clip.get_tokenizer('ViT-B-32')
        self.sentences = SentenceTransformer(str(TEXT_MODEL.directory(models)), device='cpu')
        self.room_prompts = self.clip_texts([ROOM_PROMPTS[room] for room in ROOM_TYPES])

    def images(self, images):
        """The unit CLIP embedding of each PIL image, as rows of a float32 array."""
        rows = []
        with self.torch.inference_mode():
            for start in range(0, len(images), BATCH):
                batch = self.torch.stack([self.preprocess(image.convert('RGB')) for image in images[start:start + BATCH]])
                features = self.clip.encode_image(batch)
                rows.append(self.torch.nn.functional.normalize(features, dim=-1).numpy())
        import numpy
        return numpy.concatenate(rows).astype('float32') if rows else numpy.zeros((0, 512), 'float32')

    def clip_texts(self, texts):
        with self.torch.inference_mode():
            features = self.clip.encode_text(self.tokenizer(texts))
            return self.torch.nn.functional.normalize(features, dim=-1).numpy().astype('float32')

    def texts(self, texts):
        """The unit sentence embedding of each text, as rows of a float32 array."""
        return self.sentences.encode(texts, batch_size=BATCH, normalize_embeddings=True, convert_to_numpy=True).astype('float32')

    def room_types(self, vectors):
        """CLIP's zero-shot room type of each image embedding, from the room types of the feature record."""
        return [ROOM_TYPES[index] for index in (vectors @ self.room_prompts.T).argmax(axis=1)]


def text_chunks(listing):
    """The listing's text as the embedding model reads it: its traits and concerns, then its title and description in
    chunks of at most CHUNK_WORDS words, since all-MiniLM-L6-v2 reads at most 256 word pieces."""
    chunks = []
    if listing['traits'] or listing['concerns']:
        chunks.append('. '.join(part for part in (', '.join(listing['traits']), ', '.join(listing['concerns'])) if part) + '.')
    words = ' '.join(part for part in (listing['title'], listing['description']) if part).split()
    chunks += [' '.join(words[start:start + CHUNK_WORDS]) for start in range(0, len(words), CHUNK_WORDS)]
    return chunks


def photo_parts(vectors, rooms):
    """The mean of the photos' unit vectors over all photos and over each room type, with how many photos each holds."""
    groups = {'photos': list(range(len(rooms)))}
    for index, room in enumerate(rooms):
        groups.setdefault(f'photos:{room}', []).append(index)
    return {part: (len(indices), vectors[indices].mean(axis=0).astype('float32').tobytes()) for part, indices in groups.items()}


def text_part(vectors):
    return {'text': (len(vectors), vectors.mean(axis=0).astype('float32').tobytes())}


def load_photos(photos, fetch):
    """Each photo of the record that loads, as (PIL image, room type). A photo the site no longer has, or that Pillow
    cannot read, is left out. Raises PhotosUnavailable when one cannot be downloaded now."""
    from PIL import Image

    with ThreadPoolExecutor(DOWNLOADS) as pool:
        contents = list(pool.map(lambda photo: fetch(photo[0]), photos))
    loaded = []
    for (_, room), content in zip(photos, contents):
        if content is None:
            continue
        try:
            image = Image.open(io.BytesIO(content))
            image.load()
        except (OSError, SyntaxError, ValueError, Image.DecompressionBombError):
            continue
        loaded.append((image, room))
    return loaded


def embed_store(store, encoders, *, fetch=download):
    """Embeds every listing that lacks embeddings of its current content and saves them, one listing at a time.
    Returns, per listing, the photos embedded, the seconds spent downloading photos, embedding them and embedding in
    all, the CPU seconds of embedding, how many photos CLIP's zero-shot room type agrees with the record on, and what
    could not be embedded. A listing with nothing to embed for a model stays waiting and is reported every time."""
    report = []
    for listing in store.embedding_inputs([pin.name for pin in MODELS]):
        entry, errors = {'id': listing['id']}, []
        started = time.perf_counter()
        try:
            photos = load_photos(listing['photos'], fetch) if IMAGE_MODEL.name in listing['missing'] else []
        except PhotosUnavailable as error:
            report.append({**entry, 'error': str(error)})
            continue
        entry['download_seconds'] = time.perf_counter() - started
        started, cpu = time.perf_counter(), time.process_time()
        if IMAGE_MODEL.name in listing['missing']:
            if photos:
                vectors = encoders.images([image for image, _ in photos])
                entry['image_seconds'] = time.perf_counter() - started
                rooms = [room for _, room in photos]
                store.save_embeddings(listing['id'], listing['fingerprint'], IMAGE_MODEL.name, photo_parts(vectors, rooms))
                entry['room_types_agreeing'] = sum(map(str.__eq__, encoders.room_types(vectors), rooms))
            else:
                errors.append('No photo of its record loads.')
        if TEXT_MODEL.name in listing['missing']:
            chunks = text_chunks(listing)
            if chunks:
                store.save_embeddings(listing['id'], listing['fingerprint'], TEXT_MODEL.name, text_part(encoders.texts(chunks)))
            else:
                errors.append('It has no title, description, traits or concerns.')
        entry.update(photos=len(photos), embed_seconds=time.perf_counter() - started, embed_cpu_seconds=time.process_time() - cpu)
        report.append({**entry, 'error': ' '.join(errors)} if errors else entry)
    return report


def main(argv):
    parser = argparse.ArgumentParser(description='Embed the listings with local image and text models.')
    parser.add_argument('command', choices=('download', 'embed'))
    arguments = parser.parse_args(argv)
    try:
        download_models(models_directory())
        if arguments.command == 'embed':
            report = embed_store(ListingStore(store_path()), Encoders(models_directory()))
            print(json.dumps({'embedded': sum('error' not in entry for entry in report), 'failed': [entry for entry in report if 'error' in entry]},
                             ensure_ascii=False, indent=1))
        return 0
    except (ValueError, KeyError, OSError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
