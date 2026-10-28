"""Contact sheets and instructions for the reference labels of the encoder's probe check. A developer tool, not shipped.

  encoder_label_sheets.py <manifest.json> <output-directory>

Each sheet shows whole listings, every photo numbered, so a reader labels a listing from all its photos, as extraction
does. The reader never sees an encoder score. Owner listings get one sheet each (at most 20 photos, the first ones);
houses of the public dataset four to a sheet, one row of four photos each.
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

TILE = 300
DEFINITIONS = """For each listing on a sheet, answer five questions from its photos only, each 1 (yes), 0 (no) or null (the photos
cannot tell):

- carpet: wall-to-wall carpet is visible on the floor of a room. A rug on a hard floor is not carpet. Null when no
  floor of a room is visible.
- exposed brick: an interior wall of bare brick is visible inside the home. Brick on the outside of the building, or
  only a brick fireplace surround, is 0.
- balcony: the home has a private balcony visible in a photo: a railed platform outside a door on an upper floor. A
  ground-level patio or deck, a yard, a shared roof deck and a railing flush with a window are 0.
- bathtub: a bathtub, including a tub with a shower, is visible. Null when no bathroom is visible.
- open kitchen: the kitchen is open to a living or dining area, which shows in the same photo with no wall or door
  between them. Null when no kitchen is visible.

Answer only from what the photos show, never from what is typical."""
# The brief each labeling subagent (Sonnet) read, with its group of sheets and its output file in its task.
BRIEF_HEAD = """# Labeling photos of home listings

You label listings from contact sheets, images that show a listing's photos as tiles. Each tile has a black tag in its
top-left corner such as "L5 #3" (listing L5, photo 3) or "H100 #2" (listing H100, photo 2). Owner sheets (owner-NN.jpg)
show one listing on up to four rows. Houses sheets (houses-NNN.jpg) show four listings, one row of four photos each.

Open each sheet of your group with the Read tool, one after another, look at it, and record the answers for every
listing on it. Open nothing else: no other file, no web page. Do not write code. Work through every sheet in your group.

"""
BRIEF_TAIL = """

## Output

When every sheet is done, write one JSON file with the Write tool, to the path your task gives, mapping each listing tag
to its five answers, for example:

{"L5": {"carpet": 0, "exposed brick": 0, "balcony": 0, "bathtub": null, "open kitchen": 0},
 "H100": {"carpet": 1, "exposed brick": 0, "balcony": 0, "bathtub": 1, "open kitchen": null}}

Every listing on your sheets must be in the file, with exactly these five keys, each 1, 0 or null. Then end with one
line: how many sheets and listings you labeled.
"""


def tile(path, label):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
    image.thumbnail((TILE, TILE))
    canvas = Image.new('RGB', (TILE, TILE), 'white')
    canvas.paste(image, ((TILE - image.width) // 2, (TILE - image.height) // 2))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 8 + 9 * len(label), 18), fill='black')
    draw.text((4, 3), label, fill='white')
    return canvas


def sheet(rows, path):
    width = max(len(row) for row in rows)
    canvas = Image.new('RGB', (width * TILE, len(rows) * TILE), 'white')
    for y, row in enumerate(rows):
        for x, (photo, label) in enumerate(row):
            canvas.paste(tile(photo, label), (x * TILE, y * TILE))
    canvas.save(path, quality=85)


def main(argv):
    photos = json.loads(Path(argv[0]).read_text(encoding='utf-8'))['photos']
    output = Path(argv[1])
    output.mkdir(parents=True, exist_ok=True)
    listings = {}
    for photo in photos:
        listings.setdefault((photo['source'], photo['listing']), []).append(photo['path'])
    index = []
    owner = [key for key in listings if key[0] == 'owner']
    for number, key in enumerate(owner, 1):
        paths = listings[key][:20]
        name = f'owner-{number:02d}.jpg'
        rows = [[(path, f'L{number} #{i + 1}') for i, path in enumerate(paths[start:start + 5], start)]
                for start in range(0, len(paths), 5)]
        sheet(rows, output / name)
        index.append({'sheet': name, 'listings': {f'L{number}': key[1]}})
    houses = [key for key in listings if key[0] == 'houses']
    for start in range(0, len(houses), 4):
        group = houses[start:start + 4]
        name = f'houses-{start // 4 + 1:03d}.jpg'
        rows = [[(path, f'H{key[1].split(":")[1]} #{i + 1}') for i, path in enumerate(listings[key])] for key in group]
        sheet(rows, output / name)
        index.append({'sheet': name, 'listings': {f'H{key[1].split(":")[1]}': key[1] for key in group}})
    (output / 'index.json').write_text(json.dumps(index, indent=1) + '\n', encoding='utf-8')
    (output / 'definitions.txt').write_text(DEFINITIONS + '\n', encoding='utf-8')
    (output.parent / 'brief.md').write_text(BRIEF_HEAD + DEFINITIONS + BRIEF_TAIL, encoding='utf-8')
    print(json.dumps({'sheets': len(index), 'owner': len(owner), 'houses': len(houses)}))


if __name__ == '__main__':
    main(sys.argv[1:])
