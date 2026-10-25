"""Stand-in stores for running measure_embeddings.py end to end, as the embedding research describes. They check the
pipeline and its costs, not the ranking of real listings.

  stand_in_stores.py listings SOURCE TARGET
      copies the listings of the store SOURCE into a new store TARGET, each with a placeholder feature record whose
      photos are its distinct photos, for timing the embedding of real photos
  stand_in_stores.py synthetic COUNT FAVORITES PHOTOS TARGET EXPERIMENT PORT
      COUNT listings with random feature records, each with 6 of the JPEG files in the directory PHOTOS as served at
      http://127.0.0.1:PORT/. Twenty of them, the garden units, draw their photos from the files of the source
      listing with the most photos (files named <listing>-<photo>.jpg), the others from the rest. Five garden units
      and FAVORITES - 5 other listings are favorited, and EXPERIMENT gets the five and the other garden units.
"""

import json
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'profile/skills/gallery/scripts'), str(ROOT / 'profile/skills/listing-features/scripts'), str(ROOT / 'tests')]

from photos import distinct_photos  # noqa: E402
from record import CATEGORIES, FLAGS, RATINGS, ROOM_TYPES, SEED_TAGS  # noqa: E402
from store import ListingStore  # noqa: E402
from test_feature_record import extraction  # noqa: E402
from test_gallery_store import listing  # noqa: E402
from test_rerank import SETTINGS, unit  # noqa: E402

GARDEN_UNITS = 20


def copied_listings(source, target):
    store = ListingStore(target)
    store.update_settings(**SETTINGS)
    with sqlite3.connect(f'{Path(source).resolve().as_uri()}?mode=ro', uri=True) as connection:
        rows = connection.execute('SELECT id, data, status FROM listings').fetchall()
    for identifier, data, status in rows:
        data = {**json.loads(data), 'id': identifier, 'status': status}
        saved = store.upsert_listing(data)
        photos = distinct_photos(data['photos'])
        store.save_extraction(identifier, saved['content_fingerprint'], extraction(len(photos)), photos=photos, phrases=[])


def synthetic(count, favorites, photo_directory, target, experiment, port):
    generator = random.Random(11)
    by_listing = {}
    for file in sorted(Path(photo_directory).glob('*.jpg')):
        by_listing.setdefault(file.name.split('-')[0], []).append(file.name)
    garden_pool = max(by_listing.values(), key=len)
    other_pool = [name for names in by_listing.values() if names is not garden_pool for name in names]
    store = ListingStore(target)
    store.update_settings(**SETTINGS)
    garden = []
    for number in range(count):
        identifier = f'zillow:{number:04}'
        is_garden = number % (count // GARDEN_UNITS) == 0 and len(garden) < GARDEN_UNITS
        photos = [f'http://127.0.0.1:{port}/{name}' for name in generator.sample(garden_pool if is_garden else other_pool, 6)]
        saved = store.upsert_listing(listing(id=identifier, title=f'{number} N Example Ave', price=generator.randrange(1500, 3000, 25), photos=photos,
                                             description=' '.join(generator.sample(SEED_TAGS, 30))))
        fingerprint = saved['content_fingerprint']
        label = '#G' if is_garden and generator.random() < 0.6 else None
        store.analyze(identifier, fingerprint, {'facts': {
            'currency': 'USD', 'areas': [generator.choice(['Logan Square', 'Avondale'])], 'stories': None,
            'latitude': 41.92 + generator.uniform(-0.04, 0.04), 'longitude': -87.71 + generator.uniform(-0.04, 0.04),
            'units': [unit(generator.randrange(1500, 3000, 25), generator.randrange(600, 1400, 25), label)]}, 'must_haves': {}})
        chosen = generator.sample(SEED_TAGS, 8)
        value = extraction(6, ['Lots of daylight', 'Carpet'],
                           photos=[{'index': index, 'room': generator.choice(ROOM_TYPES[:6])} for index in range(1, 7)],
                           ratings={name: {'value': generator.randint(1, 5), 'confidence': generator.choice((0.5, 0.8, 1.0))} for name in RATINGS},
                           **{name: generator.choice([*values, 'not shown']) for name, values in CATEGORIES.items()},
                           flags={flag: generator.choice((True, False, None)) for flag in FLAGS}, traits=chosen[:6], concerns=chosen[6:])
        value['preferences'] = {phrase: {'verdict': generator.choice(('has', 'rules out', 'does not say')), 'evidence': ''} for phrase in value['preferences']}
        store.save_extraction(identifier, fingerprint, value, photos=photos, phrases=['Lots of daylight', 'Carpet'])
        if is_garden:
            garden.append(identifier)
    others = [f'zillow:{number:04}' for number in range(count) if f'zillow:{number:04}' not in garden]
    for identifier in [*garden[:5], *others[:max(0, favorites - 5)]]:
        store.feedback(identifier, favorite=True)
    Path(experiment).write_text(json.dumps({'favorites': garden[:5], 'alike': garden[5:]}), encoding='utf-8')


if __name__ == '__main__':
    if sys.argv[1] == 'listings':
        copied_listings(*sys.argv[2:])
    else:
        count, favorites, photo_directory, target, experiment, port = sys.argv[2:]
        synthetic(int(count), int(favorites), photo_directory, target, experiment, int(port))
