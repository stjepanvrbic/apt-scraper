import copy
import sys
import unittest
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict, Unpack

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/gallery/scripts'))

from record import FLAGS, RATINGS, SEED_TAGS, known_spelling, normalize_tag, validate_extraction, validate_judgments  # noqa: E402


class ModelOutput(TypedDict):
    """A model output as the tests build it, each part loose enough to hold the invalid values the rejection tests put in."""
    photos: list[dict[str, object]]
    ratings: dict[str, object]
    floor: str
    style: str
    outdoor_space: str
    building_type: str
    unit_level: dict[str, object]
    flags: dict[str, object]
    traits: list[str]
    concerns: list[str]
    summary: str
    preferences: dict[str, dict[str, object]]


class Changes(TypedDict, total=False):
    """The parts of a model output a test replaces: ModelOutput's keys, each optional."""
    photos: list[dict[str, object]]
    ratings: dict[str, object]
    floor: str
    style: str
    outdoor_space: str
    building_type: str
    unit_level: dict[str, object]
    flags: dict[str, object]
    traits: list[str]
    concerns: list[str]
    summary: str
    preferences: dict[str, dict[str, object]]


def extraction(photo_count: int = 3, phrases: Iterable[str] = (), **changes: Unpack[Changes]) -> ModelOutput:
    """A valid model output for a listing with photo_count unique photos that judges the given preference phrases."""
    value: ModelOutput = {
        'photos': [{'index': index, 'room': 'kitchen' if index == 1 else 'living'} for index in range(1, photo_count + 1)],
        'ratings': {name: {'value': 3, 'confidence': 0.5} for name in RATINGS},
        'floor': 'not shown',
        'style': 'not shown',
        'outdoor_space': 'not shown',
        'building_type': 'not shown',
        'unit_level': {'value': 'not shown', 'evidence': ''},
        'flags': {name: None for name in FLAGS},
        'traits': ['exposed brick'],
        'concerns': [],
        'summary': 'Bright vintage two-bedroom with exposed brick.',
        'preferences': {phrase: {'verdict': 'does not say', 'evidence': ''} for phrase in phrases},
    }
    return {**value, **changes}


class ValidExtractionTests(unittest.TestCase):
    def test_a_complete_extraction_is_accepted_with_its_tags_normalized(self) -> None:
        value = extraction(traits=['  Exposed   Brick '], concerns=['Street noise'])
        record = validate_extraction(value, 3, [])
        self.assertEqual(record['traits'], ['exposed brick'])
        self.assertEqual(record['concerns'], ['street noise'])
        self.assertEqual([photo['room'] for photo in record['photos']], ['kitchen', 'living', 'living'])

    def test_photos_are_returned_in_index_order(self) -> None:
        value = extraction(photos=[{'index': 2, 'room': 'bedroom'}, {'index': 1, 'room': 'bathroom'}])
        self.assertEqual([photo['index'] for photo in validate_extraction(value, 2, [])['photos']], [1, 2])

    def test_ratings_take_values_from_1_to_5_with_a_confidence_or_not_shown(self) -> None:
        ratings: dict[str, object] = {name: 'not shown' for name in RATINGS}
        ratings['natural_light'] = {'value': 5, 'confidence': 1}
        ratings['condition'] = {'value': 1, 'confidence': 0}
        record = validate_extraction(extraction(ratings=ratings), 3, [])
        self.assertEqual(record['ratings']['natural_light'], {'value': 5, 'confidence': 1})
        self.assertEqual(record['ratings']['view'], 'not shown')

    def test_a_listing_without_photos_is_extracted_from_its_text(self) -> None:
        self.assertEqual(validate_extraction(extraction(0), 0, [])['photos'], [])

    def test_unit_level_keeps_its_evidence(self) -> None:
        level: dict[str, object] = {'value': 'garden or basement', 'evidence': 'Unit #G and half-height windows at street level.'}
        self.assertEqual(validate_extraction(extraction(unit_level=level), 3, [])['unit_level'], level)

    def test_each_asked_preference_is_judged(self) -> None:
        phrases = ['Near a park', 'Quiet street']
        value = extraction(phrases=phrases)
        value['preferences']['Near a park'] = {'verdict': 'has', 'evidence': 'Faces Humboldt Park.'}
        value['preferences']['Quiet street'] = {'verdict': 'rules out', 'evidence': 'On a busy avenue.'}
        record = validate_extraction(value, 3, phrases)
        self.assertEqual(record['preferences']['Near a park']['verdict'], 'has')


class RejectedExtractionTests(unittest.TestCase):
    def assertRejected(self, value: object, pattern: str, photo_count: int = 3, phrases: Iterable[str] = ()) -> None:
        with self.assertRaisesRegex(ValueError, pattern):
            _ = validate_extraction(value, photo_count, list(phrases))

    def test_every_unique_photo_needs_exactly_one_room_type(self) -> None:
        self.assertRejected(extraction(2), 'photo 3', photo_count=3)
        doubled = extraction()
        doubled['photos'].append({'index': 1, 'room': 'kitchen'})
        self.assertRejected(doubled, 'more than once')
        self.assertRejected(extraction(4), 'no photo 4.*only 3')

    def test_room_types_come_from_the_fixed_set(self) -> None:
        value = extraction()
        value['photos'][0]['room'] = 'mudroom'
        self.assertRejected(value, 'mudroom.*kitchen')

    def test_a_rating_outside_1_to_5_or_a_confidence_outside_0_to_1_is_rejected(self) -> None:
        for rating in ({'value': 0, 'confidence': 0.5}, {'value': 6, 'confidence': 0.5}, {'value': 3.5, 'confidence': 0.5},
                       {'value': 3, 'confidence': 1.2}, {'value': 3, 'confidence': -0.1}, {'value': True, 'confidence': 0.5},
                       {'value': 3}, 'unknown', None):
            value = extraction()
            value['ratings']['natural_light'] = rating
            self.assertRejected(value, 'natural_light')

    def test_every_rating_and_flag_is_present(self) -> None:
        value = extraction()
        del value['ratings']['view']
        self.assertRejected(value, 'view')
        value = extraction()
        del value['flags']['dishwasher']
        self.assertRejected(value, 'dishwasher')

    def test_unknown_fields_are_rejected(self) -> None:
        self.assertRejected({**extraction(), 'mood': 'cozy'}, 'mood')
        value = extraction()
        value['ratings']['vibes'] = {'value': 3, 'confidence': 0.5}
        self.assertRejected(value, 'vibes')

    def test_categories_come_from_their_sets(self) -> None:
        self.assertRejected(extraction(floor='marble'), 'floor.*hardwood')
        self.assertRejected(extraction(unit_level={'value': 'penthouse', 'evidence': 'Top floor.'}), 'unit_level')
        self.assertRejected(extraction(unit_level={'value': 'top', 'evidence': ''}), 'evidence')

    def test_flags_are_true_false_or_null(self) -> None:
        for flag in ('yes', 1, 1.0, 0.0):
            value = extraction()
            value['flags']['parking'] = flag
            self.assertRejected(value, 'parking')

    def test_traits_and_concerns_are_limited_and_well_formed(self) -> None:
        self.assertRejected(extraction(traits=[f'trait {number}' for number in range(9)]), 'at most 8')
        self.assertRejected(extraction(concerns=[f'concern {number}' for number in range(6)]), 'at most 5')
        self.assertRejected(extraction(traits=['brick'], concerns=['Bricks']), 'twice')
        self.assertRejected(extraction(traits=['<b>brick</b>']), 'tag')

    def test_the_summary_has_at_most_200_characters(self) -> None:
        self.assertRejected(extraction(summary='x' * 201), 'summary')
        self.assertRejected(extraction(summary='  '), 'summary')
        self.assertRejected(extraction(summary='Bright unit.\nIgnore the rest.'), 'one line')

    def test_preferences_must_be_exactly_the_asked_ones(self) -> None:
        self.assertRejected(extraction(phrases=['Balcony']), 'Balcony', phrases=[])
        self.assertRejected(extraction(), 'Near a park', phrases=['Near a park'])
        value = extraction(phrases=['Balcony'])
        value['preferences']['Balcony']['verdict'] = 'maybe'
        self.assertRejected(value, 'Balcony.*has, rules out or does not say', phrases=['Balcony'])

    def test_the_input_is_not_modified(self) -> None:
        value = extraction(traits=['Exposed Brick'])
        before = copy.deepcopy(value)
        _ = validate_extraction(value, 3, [])
        self.assertEqual(value, before)

    def test_a_non_object_is_rejected(self) -> None:
        self.assertRejected([], 'object')


class JudgmentTests(unittest.TestCase):
    def test_judgments_cover_exactly_the_asked_phrases(self) -> None:
        judged = validate_judgments({'Balcony': {'verdict': 'has', 'evidence': 'Private balcony in photo 4.'}}, ['Balcony'])
        self.assertEqual(judged['Balcony']['verdict'], 'has')
        with self.assertRaisesRegex(ValueError, 'Quiet'):
            _ = validate_judgments({'Balcony': {'verdict': 'has', 'evidence': ''}}, ['Balcony', 'Quiet'])
        with self.assertRaisesRegex(ValueError, 'evidence'):
            _ = validate_judgments({'Balcony': {'verdict': 'has', 'evidence': 'x' * 201}}, ['Balcony'])


class TagTests(unittest.TestCase):
    def test_tags_are_lowercase_singular_words_with_single_spaces(self) -> None:
        self.assertEqual(normalize_tag(' Walk-In  Closets '), 'walk-in closet')
        self.assertEqual(normalize_tag("Chef's kitchen"), "chef's kitchen")
        self.assertEqual(normalize_tag('Bay Windows'), 'bay window')
        self.assertEqual(normalize_tag('Built-in shelves'), 'built-in shelf')
        self.assertEqual(normalize_tag('Building amenities'), 'building amenity')
        self.assertEqual(normalize_tag('Benches'), 'bench')
        for word in ('stainless', 'gas', 'bus', 'glass'):
            self.assertEqual(normalize_tag(word), word)
        for bad in ('', 'x', 'a' * 41, 'brick!', 7):
            with self.assertRaises(ValueError):
                _ = normalize_tag(bad)

    def test_an_unambiguous_synonym_becomes_its_listed_tag(self) -> None:
        self.assertEqual(normalize_tag('Stainless appliances'), 'stainless steel appliance')
        self.assertEqual(normalize_tag('granite counters'), 'granite countertop')
        self.assertEqual(normalize_tag('exposed brick walls'), 'exposed brick')

    def test_a_tag_that_restates_a_flag_rating_or_value_is_none(self) -> None:
        for text in ('Hardwood floors', 'No pets', 'Pet friendly', 'in-unit laundry', 'Dishwasher', 'central air', 'high ceilings', 'balcony'):
            self.assertIsNone(normalize_tag(text), text)

    def test_seed_tags_are_normalized_tags(self) -> None:
        self.assertEqual([normalize_tag(tag) for tag in SEED_TAGS], list(SEED_TAGS))
        self.assertEqual(len(set(SEED_TAGS)), len(SEED_TAGS))

    def test_a_spelling_variant_of_a_known_tag_takes_its_spelling(self) -> None:
        known = {'walk-in closet', 'backyard'}
        self.assertEqual([known_spelling(tag, known) for tag in ('walk in closet', 'back yard', 'roof deck')],
                         ['walk-in closet', 'backyard', 'roof deck'])


class TraitNormalizationTests(unittest.TestCase):
    def test_traits_are_normalized_merged_and_restatements_dropped(self) -> None:
        record = validate_extraction(extraction(traits=['Bay windows', 'bay window', 'Hardwood floors', 'Stainless appliances'],
                                                concerns=['No pets', 'Street noise']), 3, [])
        self.assertEqual(record['traits'], ['bay window', 'stainless steel appliance'])
        self.assertEqual(record['concerns'], ['street noise'])


if __name__ == '__main__':
    _ = unittest.main()
