import io
import random
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/listing-features/scripts'))

from photos import (SHEET_SIZE, TILE_SIZE, contact_sheets, difference_hash, distance, distinct_photos,  # noqa: E402
                    photo_key, without_near_duplicates)


def zillow(key, width=1536):
    return f'https://photos.zillowstatic.com/fp/{key}-cc_ft_{width}.jpg'


def picture(seed, size=(640, 480)):
    """A photo-like image whose layout of colored blocks differs for every seed."""
    generator = random.Random(seed)
    image = Image.new('RGB', size, tuple(generator.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(image)
    for _ in range(12):
        left, top = generator.randrange(size[0]), generator.randrange(size[1])
        draw.rectangle([left, top, left + generator.randrange(40, 300), top + generator.randrange(40, 240)],
                       fill=tuple(generator.randrange(256) for _ in range(3)))
    return image


def recompressed(image, size=(512, 384), quality=55):
    """The same photo as a listing site serves it again: resized and saved at another JPEG quality."""
    buffer = io.BytesIO()
    image.resize(size).save(buffer, 'JPEG', quality=quality)
    return Image.open(io.BytesIO(buffer.getvalue())).convert('RGB')


def fixture_listing():
    """40 photo URLs of one listing: 34 unique photos, 3 repeated under their key at another size and 3 near duplicates
    under keys of their own. Returns the URLs, the image each URL serves, and the URLs expected to remain, in order."""
    urls, images, expected = [], {}, []
    for number in range(34):
        url = zillow(f'k{number:02}')
        urls.append(url)
        images[url] = picture(number)
        expected.append(url)
    for number in (3, 17, 30):
        smaller = zillow(f'k{number:02}', 384)
        urls.insert(urls.index(zillow(f'k{number:02}')) + 1, smaller)
        images[smaller] = recompressed(images[zillow(f'k{number:02}')])
    for number in (5, 12, 25):
        copy = zillow(f'copy{number:02}')
        urls.append(copy)
        images[copy] = recompressed(images[zillow(f'k{number:02}')], (800, 600), 70)
    return urls, images, expected


class DistinctPhotoTests(unittest.TestCase):
    def test_the_photo_key_names_a_zillow_photo_at_every_size(self):
        self.assertEqual(photo_key(zillow('abc123', 1536)), photo_key(zillow('abc123', 384)))
        self.assertNotEqual(photo_key(zillow('abc123')), photo_key(zillow('abc124')))
        self.assertEqual(photo_key('https://example.com/a.jpg?x=1'), 'https://example.com/a.jpg?x=1')

    def test_the_same_key_at_another_size_keeps_the_largest_in_the_first_place(self):
        urls = [zillow('a', 384), zillow('b'), zillow('a', 1536), zillow('c')]
        self.assertEqual(distinct_photos(urls), [zillow('a', 1536), zillow('b'), zillow('c')])

    def test_near_duplicates_are_removed_and_different_photos_kept(self):
        urls, images, expected = fixture_listing()
        kept = without_near_duplicates([(url, images[url]) for url in distinct_photos(urls)])
        self.assertEqual([url for url, _ in kept], expected)

    def test_the_difference_hash_survives_resizing_and_recompression(self):
        image = picture(7)
        self.assertLessEqual(distance(difference_hash(image), difference_hash(recompressed(image))), 4)
        self.assertGreater(distance(difference_hash(image), difference_hash(picture(8))), 12)


class ContactSheetTests(unittest.TestCase):
    def test_sheets_show_every_unique_photo_of_a_40_photo_listing_once_in_order(self):
        urls, images, expected = fixture_listing()
        unique = [image for _, image in without_near_duplicates([(url, images[url]) for url in distinct_photos(urls)])]
        sheets = contact_sheets(unique)
        self.assertEqual(len(sheets), 4)
        self.assertTrue(all(sheet.size == SHEET_SIZE for sheet in sheets))
        hashes = [difference_hash(image) for image in unique]
        shown = []
        for sheet in sheets:
            for position in range(9):
                left, top = position % 3 * TILE_SIZE[0], position // 3 * TILE_SIZE[1]
                tile = sheet.crop((left, top, left + TILE_SIZE[0], top + TILE_SIZE[1]))
                if tile.getextrema() == ((0, 0), (0, 0), (0, 0)):
                    continue
                tile_hash = difference_hash(tile)
                shown.append(min(range(len(unique)), key=lambda index: distance(hashes[index], tile_hash)))
        self.assertEqual(shown, list(range(len(unique))))

    def test_a_tall_photo_fits_its_tile_without_cropping(self):
        tall = Image.new('RGB', (300, 900), (200, 30, 30))
        [sheet] = contact_sheets([tall])
        tile = sheet.crop((0, 0, *TILE_SIZE))
        self.assertEqual(tile.getpixel((TILE_SIZE[0] // 2, TILE_SIZE[1] // 2)), (200, 30, 30))
        self.assertNotEqual(tile.getpixel((TILE_SIZE[0] - 5, TILE_SIZE[1] // 2)), (200, 30, 30))

    def test_no_photos_make_no_sheets(self):
        self.assertEqual(contact_sheets([]), [])


if __name__ == '__main__':
    unittest.main()
