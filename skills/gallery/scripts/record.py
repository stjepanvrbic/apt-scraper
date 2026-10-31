"""The feature record contract: what an extraction may store about one listing content.

The model's part of a record is checked here before the store keeps it, so a stored record always has a room type
from ROOM_TYPES for every unique photo, ratings from 1 to 5 with a confidence from 0 to 1 or "not shown", values
from the fixed sets below, and well-formed traits, which the store adds to the tag list."""

import math
import re
from collections.abc import Iterable, Sequence
from typing import Literal, TypedDict

from shapes import is_array, is_object

NotShown = Literal['not shown']
Verdict = Literal['has', 'rules out', 'does not say']
NOT_SHOWN: NotShown = 'not shown'
ROOM_TYPES = ('kitchen', 'bathroom', 'bedroom', 'living', 'dining', 'exterior', 'view', 'outdoor space', 'laundry',
              'floor plan', 'building amenity', 'other')
RATINGS = {
    'natural_light': ('dark rooms', 'very bright rooms'),
    'window_size': ('small windows', 'floor-to-ceiling windows'),
    'ceiling_height': ('low ceilings', 'very high ceilings'),
    'room_size': ('cramped rooms', 'spacious rooms'),
    'layout_openness': ('closed separate rooms', 'fully open plan'),
    'kitchen_finish': ('dated or worn kitchen', 'new high-end kitchen'),
    'kitchen_size': ('tiny kitchen', 'large kitchen'),
    'bathroom_finish': ('dated or worn bathroom', 'new high-end bathroom'),
    'condition': ('poor condition', 'pristine condition'),
    'renovation_recency': ('not renovated in decades', 'just renovated'),
    'storage': ('no visible storage', 'ample storage'),
    'view': ('blocked view or a wall', 'open view of skyline, water or park'),
}
CATEGORIES = {
    'floor': ('hardwood', 'carpet', 'tile or vinyl', 'mixed'),
    'style': ('modern', 'vintage', 'industrial', 'plain'),
    'outdoor_space': ('none', 'shared', 'private balcony', 'private yard'),
    'building_type': ('house', 'townhouse', 'low-rise', 'mid-rise', 'high-rise'),
}
UNIT_LEVELS = ('garden or basement', 'ground', 'middle', 'top')
FLAGS = ('in_unit_laundry', 'laundry_in_building', 'dishwasher', 'air_conditioning', 'central_air', 'parking',
         'pets_allowed', 'utilities_included', 'elevator', 'furnished')
VERDICTS: tuple[Verdict, ...] = ('has', 'rules out', 'does not say')
TRAIT_LIMIT = 8
CONCERN_LIMIT = 5
SUMMARY_LIMIT = 200
EVIDENCE_LIMIT = 200
FIELDS = ('photos', 'ratings', *CATEGORIES, 'unit_level', 'flags', 'traits', 'concerns', 'summary', 'preferences')
TAG = re.compile(r"[a-z0-9][a-z0-9 '&/-]*[a-z0-9]")
IRREGULAR_SINGULARS = {'shelves': 'shelf', 'stairs': 'stairs', 'downstairs': 'downstairs', 'upstairs': 'upstairs',
                       'series': 'series', 'premises': 'premises'}
SEED_TAGS = (
    'exposed brick', 'exposed beam', 'bay window', 'skylight', 'vaulted ceiling', 'sloped ceiling', 'crown molding', 'fireplace',
    'built-in shelf', 'walk-in closet', 'linen closet', 'pantry', 'kitchen island', 'breakfast bar', 'galley kitchen',
    'stainless steel appliance', 'granite countertop', 'quartz countertop', 'gas range', 'tile backsplash', 'double vanity',
    'soaking tub', 'clawfoot tub', 'walk-in shower', 'subway tile', 'ceiling fan', 'recessed lighting', 'track lighting',
    'radiator heat', 'formal dining room', 'office nook', 'den', 'loft', 'duplex layout', 'railroad layout', 'private entrance',
    'front porch', 'back porch', 'deck', 'patio', 'backyard', 'roof deck', 'courtyard', 'garden', 'storage unit',
    'bike storage', 'fitness center', 'package room', 'doorman', 'gated entry', 'coach house', 'two-flat', 'greystone',
    'new construction', 'tree-lined street', 'near park', 'near train',
    'street noise', 'train noise', 'busy street', 'small bedroom', 'small bathroom', 'no bedroom closet', 'bedroom without window',
    'dated kitchen', 'dated bathroom', 'worn floor', 'walk-up', 'shared entrance', 'move-in fee', 'application fee',
    'credit requirement',
)


class PhotoRoom(TypedDict):
    index: int
    room: str


class Rating(TypedDict):
    value: int
    confidence: float


class UnitLevel(TypedDict):
    value: str
    evidence: str


class Judgment(TypedDict):
    verdict: Verdict
    evidence: str


class Tags(TypedDict):
    traits: list[str]
    concerns: list[str]


class RecordValues(Tags):
    """What an extraction states about the listing itself: every rating, category and flag, its unit level, its tags
    and its summary. The photos' room types and the judgments of the stated preferences come with the record's
    shapes, which keep them differently."""
    ratings: dict[str, Rating | NotShown]
    floor: str
    style: str
    outdoor_space: str
    building_type: str
    unit_level: UnitLevel
    flags: dict[str, bool | None]
    summary: str


class Extraction(RecordValues):
    """A feature record as validate_extraction keeps it: a room type for every unique photo, every rating, category and
    flag present, and the judgments of the stated preferences by phrase."""
    photos: list[PhotoRoom]
    preferences: dict[str, Judgment]


def choices(values: Sequence[str]) -> str:
    return ', '.join(values[:-1]) + ' or ' + values[-1]


def require_keys(value: object, name: str, keys: Iterable[str], *, exact: bool) -> dict[str, object]:
    if not is_object(value):
        raise ValueError(f'{name} must be an object.')
    keys = tuple(keys)
    unknown = sorted(set(value) - set(keys))
    if unknown:
        raise ValueError(f'{name} has unknown fields {", ".join(unknown)}. Its fields are {", ".join(keys)}.')
    missing = [key for key in keys if key not in value]
    if exact and missing:
        raise ValueError(f'{name} is missing {", ".join(missing)}.')
    return value


def require_text(value: object, name: str, limit: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value.strip()) > limit or not (empty or value.strip()):
        raise ValueError(f'{name} must be text of {"at most" if empty else "1 to"} {limit} characters.')
    return value.strip()


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and type(value) in (int, float) and math.isfinite(value)


SYNONYMS_BY_VARIANT = {
    'stainless appliances': 'stainless steel appliance', 'ss appliances': 'stainless steel appliance',
    'granite counters': 'granite countertop', 'granite counter tops': 'granite countertop',
    'quartz counters': 'quartz countertop', 'quartz counter tops': 'quartz countertop',
    'exposed brick walls': 'exposed brick', 'built-in shelving': 'built-in shelf', 'built-in bookshelves': 'built-in shelf',
    'built-in bookcases': 'built-in shelf', 'rooftop deck': 'roof deck', 'roof top deck': 'roof deck', 'gym': 'fitness center',
    'fitness room': 'fitness center', 'back yard': 'backyard',
}
RESTATEMENTS = (
    'hardwood', 'hardwood floors', 'hardwood flooring', 'carpet', 'carpeting', 'carpeted floors', 'tile floors',
    'in-unit laundry', 'in unit laundry', 'in-unit washer/dryer', 'washer/dryer in unit', 'in-unit washer and dryer',
    'washer and dryer', 'washer/dryer', 'laundry in building', 'shared laundry', 'coin laundry', 'dishwasher', 'no dishwasher',
    'air conditioning', 'central air', 'central air conditioning', 'central ac', 'a/c', 'parking', 'no parking',
    'pet friendly', 'pets allowed', 'no pets', 'utilities included', 'tenant pays utilities', 'elevator', 'no elevator',
    'elevator building', 'furnished', 'unfurnished', 'balcony', 'private balcony', 'private yard', 'shared yard',
    'outdoor space', 'private outdoor space', 'high ceilings', 'low ceilings', 'large windows', 'natural light', 'bright',
    'spacious', 'open floor plan', 'open layout', 'open concept', 'updated kitchen', 'renovated', 'newly renovated',
    'modern', 'vintage', 'industrial', 'garden unit', 'basement unit', 'garden level', 'top floor', 'ground floor',
    'high-rise', 'mid-rise', 'low-rise', 'house', 'townhouse',
)


def singular(tag: str) -> str:
    """The tag with its last word in the singular, by the regular English endings."""
    *head, last = tag.split(' ')
    if last in IRREGULAR_SINGULARS:
        last = IRREGULAR_SINGULARS[last]
    elif last.endswith('ies') and len(last) > 4:
        last = last[:-3] + 'y'
    elif last.endswith(('ches', 'shes', 'sses', 'xes', 'zes')):
        last = last[:-2]
    elif last.endswith('s') and len(last) > 3 and not last.endswith(('ss', 'us', 'is')):
        last = last[:-1]
    return ' '.join([*head, last])


SYNONYMS = {singular(variant): tag for variant, tag in SYNONYMS_BY_VARIANT.items()}
RESTATED = frozenset(map(singular, RESTATEMENTS))


def normalize_tag(text: object) -> str | None:
    """The tag list's form of a trait or concern: lowercase, single spaces, its last word singular and an unambiguous
    synonym replaced by its listed tag. None when the tag only restates what a rating, value or flag records."""
    tag = ' '.join(text.lower().split()) if isinstance(text, str) else None
    if not tag or len(tag) > 40 or not TAG.fullmatch(tag):
        raise ValueError(f'"{text}" is not a tag: use 2 to 40 lowercase letters, digits, spaces, hyphens, apostrophes, & or /.')
    tag = singular(tag)
    tag = SYNONYMS.get(tag, tag)
    return None if tag in RESTATED else tag


def known_spelling(tag: str, known: Iterable[str]) -> str:
    """The spelling of a known tag that differs from tag only by hyphens and spaces, such as "walk-in closet" for
    "walk in closet", or tag itself."""
    key = tag.replace('-', '').replace(' ', '')
    return next((other for other in sorted(known) if other.replace('-', '').replace(' ', '') == key), tag)


def validate_photos(photos: object, photo_count: int) -> list[PhotoRoom]:
    if not is_array(photos):
        raise ValueError('photos must be an array with one entry per unique photo.')
    rooms: dict[int, str] = {}
    for photo in photos:
        entry = require_keys(photo, 'Each photo', ('index', 'room'), exact=True)
        index, room = entry['index'], entry['room']
        if type(index) is not int or not 1 <= index <= photo_count:
            raise ValueError(f'There is no photo {index}: the listing has only {photo_count} unique photos.')
        if index in rooms:
            raise ValueError(f'Photo {index} has a room type more than once.')
        if not isinstance(room, str) or room not in ROOM_TYPES:
            raise ValueError(f'"{room}" is not a room type. Room types are {choices(ROOM_TYPES)}.')
        rooms[index] = room
    missing = [str(index) for index in range(1, photo_count + 1) if index not in rooms]
    if missing:
        raise ValueError(f'Every unique photo needs a room type, and photo {", photo ".join(missing)} has none.')
    return [{'index': index, 'room': rooms[index]} for index in sorted(rooms)]


def validate_rating(name: str, rating: object) -> Rating | NotShown:
    if rating == NOT_SHOWN:
        return NOT_SHOWN
    if not is_object(rating) or set(rating) != {'value', 'confidence'}:
        raise ValueError(f'The rating {name} must be "{NOT_SHOWN}" or {{"value": 1 to 5, "confidence": 0 to 1}}.')
    value, confidence = rating['value'], rating['confidence']
    if (type(value) is not int or not 1 <= value <= 5 or not isinstance(confidence, (int, float)) or not is_number(confidence)
            or not 0 <= confidence <= 1):
        raise ValueError(f'The rating {name} must be "{NOT_SHOWN}" or {{"value": 1 to 5, "confidence": 0 to 1}}.')
    return {'value': value, 'confidence': confidence}


def validate_judgments(judgments: object, phrases: Iterable[str]) -> dict[str, Judgment]:
    """Each phrase in phrases judged once, as has, rules out or does not say, with evidence of at most 200 characters."""
    judged: dict[str, Judgment] = {}
    for phrase, judgment in require_keys(judgments, 'preferences', phrases, exact=True).items():
        entry = require_keys(judgment, f'The judgment of "{phrase}"', ('verdict', 'evidence'), exact=True)
        verdict = entry['verdict']
        if verdict not in VERDICTS:
            raise ValueError(f'The judgment of "{phrase}" must be {choices(VERDICTS)}.')
        judged[phrase] = {'verdict': verdict,
                          'evidence': require_text(entry['evidence'], f'The evidence for "{phrase}"', EVIDENCE_LIMIT, empty=True)}
    return judged


def validate_tags(record: dict[str, object]) -> Tags:
    """Normalized traits and concerns. Tags that become the same tag merge, and tags that restate a rating, value or flag
    are dropped."""
    tags: dict[str, str] = {}
    for field, limit in (('traits', TRAIT_LIMIT), ('concerns', CONCERN_LIMIT)):
        texts = record[field]
        if not is_array(texts) or len(texts) > limit:
            raise ValueError(f'{field} must be an array of at most {limit} tags.')
        for text in texts:
            tag = normalize_tag(text)
            if tag is not None and tags.setdefault(tag, field) != field:
                raise ValueError(f'The tag "{tag}" is used twice, as a trait and as a concern.')
    return {'traits': [tag for tag, owner in tags.items() if owner == 'traits'],
            'concerns': [tag for tag, owner in tags.items() if owner == 'concerns']}


def validate_category(name: str, value: object) -> str:
    values = CATEGORIES[name]
    if not isinstance(value, str) or value not in (*values, NOT_SHOWN):
        raise ValueError(f'{name} must be {choices((*values, NOT_SHOWN))}.')
    return value


def validate_flags(flags: object) -> dict[str, bool | None]:
    validated: dict[str, bool | None] = {}
    for flag, value in require_keys(flags, 'flags', FLAGS, exact=True).items():
        if value is not None and type(value) is not bool:
            raise ValueError(f'The flag {flag} must be true, false or null.')
        validated[flag] = value
    return {flag: validated[flag] for flag in FLAGS}


def validate_extraction(record: object, photo_count: int, phrases: Iterable[str]) -> Extraction:
    """The normalized copy of the model's output for a listing with photo_count unique photos that was asked to judge
    the stated preferences in phrases. Raises ValueError naming the first field that breaks the contract."""
    fields = require_keys(record, 'The record', FIELDS, exact=True)
    ratings = require_keys(fields['ratings'], 'ratings', tuple(RATINGS), exact=True)
    flags = require_keys(fields['flags'], 'flags', FLAGS, exact=True)
    level = require_keys(fields['unit_level'], 'unit_level', ('value', 'evidence'), exact=True)
    categories = {name: validate_category(name, fields[name]) for name in CATEGORIES}
    level_value = level['value']
    if not isinstance(level_value, str) or level_value not in (*UNIT_LEVELS, NOT_SHOWN):
        raise ValueError(f'unit_level.value must be {choices((*UNIT_LEVELS, NOT_SHOWN))}.')
    evidence = require_text(level['evidence'], 'unit_level.evidence', EVIDENCE_LIMIT, empty=level_value == NOT_SHOWN)
    summary = require_text(fields['summary'], 'summary', SUMMARY_LIMIT)
    if len(summary.splitlines()) > 1:
        raise ValueError('summary must be one line.')
    flag_values = validate_flags(flags)
    photos = validate_photos(fields['photos'], photo_count)
    rating_values: dict[str, Rating | NotShown] = {name: validate_rating(name, ratings[name]) for name in RATINGS}
    tags = validate_tags(fields)
    return {
        'photos': photos,
        'ratings': rating_values,
        'floor': categories['floor'],
        'style': categories['style'],
        'outdoor_space': categories['outdoor_space'],
        'building_type': categories['building_type'],
        'unit_level': {'value': level_value, 'evidence': evidence},
        'flags': flag_values,
        'traits': tags['traits'],
        'concerns': tags['concerns'],
        'summary': summary,
        'preferences': validate_judgments(fields['preferences'], phrases),
    }
