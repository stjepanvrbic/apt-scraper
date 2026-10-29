"""Reduces a listing's photos to the unique ones and tiles them into numbered contact sheets.

A sheet is 3 by 3 tiles of 512 by 384 pixels on 1536 by 1152, so 8 sheets, the most images one delegated child
takes (Hermes tools/delegate_tool_tasks.py, _MAX_TASK_IMAGES), show 72 photos. Each tile carries the number of its
photo, which the extraction uses to give each photo its room type."""

import re

from PIL import Image, ImageDraw, ImageFont

TILE_SIZE = (512, 384)
COLUMNS, ROWS = 3, 3
SHEET_SIZE = (TILE_SIZE[0] * COLUMNS, TILE_SIZE[1] * ROWS)
NEAR_DUPLICATE_DISTANCE = 6
ZILLOW_PHOTO = re.compile(r'https://photos\.zillowstatic\.com/fp/([0-9A-Za-z]+)-[^/]*?(\d+)?\.\w+$')


def photo_key(url):
    """Zillow serves one photo at several sizes under one key, such as fp/<key>-cc_ft_1536.jpg and fp/<key>-cc_ft_384.jpg."""
    match = ZILLOW_PHOTO.match(url)
    return match.group(1) if match else url


def photo_width(url):
    match = ZILLOW_PHOTO.match(url)
    return int(match.group(2)) if match and match.group(2) else 0


def distinct_photos(urls):
    """One URL per photo key, the widest, in the position of the key's first appearance."""
    widest = {}
    for url in urls:
        key = photo_key(url)
        if key not in widest or photo_width(url) > photo_width(widest[key]):
            widest[key] = url
    return list(widest.values())


def difference_hash(image):
    """64-bit difference hash: whether each pixel of a 9 by 8 grayscale thumbnail is brighter than its right neighbor."""
    pixels = image.convert('L').resize((9, 8), Image.Resampling.LANCZOS).tobytes()
    bits = 0
    for row in range(8):
        for column in range(8):
            bits = bits << 1 | (pixels[row * 9 + column] > pixels[row * 9 + column + 1])
    return bits


def distance(first, second):
    return (first ^ second).bit_count()


def without_near_duplicates(photos):
    """Drops each (url, image) whose difference hash is within NEAR_DUPLICATE_DISTANCE bits of an earlier kept photo."""
    kept, hashes = [], []
    for url, image in photos:
        value = difference_hash(image)
        if all(distance(value, other) > NEAR_DUPLICATE_DISTANCE for other in hashes):
            kept.append((url, image))
            hashes.append(value)
    return kept


def tile(image, number, font):
    canvas = Image.new('RGB', TILE_SIZE, (24, 24, 24))
    fitted = image.convert('RGB')
    fitted.thumbnail(TILE_SIZE, Image.Resampling.LANCZOS)
    canvas.paste(fitted, ((TILE_SIZE[0] - fitted.width) // 2, (TILE_SIZE[1] - fitted.height) // 2))
    draw = ImageDraw.Draw(canvas)
    label = str(number)
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    draw.rectangle([0, 0, right - left + 16, bottom - top + 12], fill=(0, 0, 0))
    draw.text((8 - left, 6 - top), label, fill=(255, 255, 0), font=font)
    return canvas


def contact_sheets(images):
    """Sheets of the images in order, the first image numbered 1. Unused tiles of the last sheet stay black."""
    font = ImageFont.load_default(size=36)
    sheets = []
    for start in range(0, len(images), COLUMNS * ROWS):
        sheet = Image.new('RGB', SHEET_SIZE, (0, 0, 0))
        for position, image in enumerate(images[start:start + COLUMNS * ROWS]):
            sheet.paste(tile(image, start + position + 1, font), (position % COLUMNS * TILE_SIZE[0], position // COLUMNS * TILE_SIZE[1]))
        sheets.append(sheet)
    return sheets
