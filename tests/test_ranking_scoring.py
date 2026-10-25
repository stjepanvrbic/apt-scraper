import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/scripts'))

from scoring import (NOT_RANKED, UNANALYZED, VOCABULARY, rank, validate_preference_reading, validate_preferences, validate_profile,  # noqa: E402
                     validate_reading)

VINTAGE = {
    'photos': ['hardwood floors', 'exposed brick', 'classic vintage details', 'bay windows', 'bright natural light'],
    'description': ['vintage charm', 'owner managed'],
    'amenities': ['laundry in building', 'dishwasher', 'cats allowed'],
    'neighborhood': 'Logan Square',
    'location': ['near a park', 'quiet residential street'],
}
HIGH_RISE = {
    'photos': ['modern finishes', 'stainless steel appliances', 'city view', 'high-rise building', 'bright natural light'],
    'description': ['luxury', 'professionally managed'],
    'amenities': ['doorman', 'fitness center', 'in-unit laundry', 'elevator', 'dishwasher'],
    'neighborhood': 'Lake View',
    'location': ['near the lake', 'near shops and restaurants'],
}
GARDEN = {
    'photos': ['carpet', 'dated kitchen', 'small rooms', 'walk-up building'],
    'description': ['garden unit', 'utilities included'],
    'amenities': ['laundry in building', 'outdoor parking'],
    'neighborhood': 'Avondale',
    'location': ['busy street', 'near a bus line'],
}


def item(identifier, style, price=2200, *, extra=(), favorite=False, dismissed=False, status='available', matches=True,
         preferences=None, notes=(), profile=True):
    style = {**style, 'photos': [*style['photos'], *extra]}
    features = {'facts': {'currency': 'USD', 'units': [{'price': price, 'period': 'month', 'bedrooms': 2, 'size_sqft': 900, 'available_on': None}]},
                'must_haves': {}}
    if profile:
        features['profile'] = {'summary': f'{identifier} summary', **style}
        features['preferences'] = preferences or {}
    return {'id': identifier, 'title': f'Apartment {identifier}', 'address': f'{identifier} Example Street, Chicago',
            'price': price, 'currency': 'USD', 'rental_period': 'month', 'status': status, 'matches_criteria': matches,
            'favorite': favorite, 'dismissed': dismissed, 'first_seen': '2026-10-20', 'features': features, 'analysis_current': True,
            'notes': [{'id': note, 'role': 'user'} for note in notes]}


def clusters(prefix, style, count, start_price=2100):
    return [item(f'{prefix}{number}', style, start_price + 25 * number) for number in range(count)]


def ranks(result):
    return {identifier: position for identifier, (position, _) in result[0].items()}


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.listings = clusters('v', VINTAGE, 10) + clusters('h', HIGH_RISE, 10) + clusters('g', GARDEN, 6)
        self.preferences = {'likes': [], 'dislikes': []}

    def find(self, identifier):
        return next(listing for listing in self.listings if listing['id'] == identifier)

    def test_unseen_listings_alike_to_five_favorites_rank_above_the_rest_of_similar_price(self):
        for identifier in ('v0', 'v2', 'v4', 'v6', 'v8'):
            self.find(identifier)['favorite'] = True
        result = ranks(rank(self.listings, self.preferences, {}))
        unseen_alike = [result[f'v{number}'] for number in (1, 3, 5, 7, 9)]
        others = [result[identifier] for identifier in result if identifier[0] in 'hg']
        self.assertLess(max(unseen_alike), min(others))

    def test_favorites_of_one_style_lift_it_whichever_style_it_is(self):
        for identifier in ('h1', 'h3', 'h5', 'h7', 'h9'):
            self.find(identifier)['favorite'] = True
        result = ranks(rank(self.listings, self.preferences, {}))
        self.assertLess(max(result[f'h{number}'] for number in (0, 2, 4, 6, 8)),
                        min(result[identifier] for identifier in result if identifier[0] in 'vg'))

    def test_what_every_favorite_shares_lifts_listings_that_have_it_whatever_else_differs(self):
        corner = {'description': ['corner unit']}
        styles = [VINTAGE, HIGH_RISE, GARDEN]
        self.listings = []
        for number in range(12):
            style = styles[number % 3]
            shared = number < 6
            listing = item(f'{"in" if shared else "out"}{number}', {**style, 'description': [*style['description'], *(corner['description'] if shared else [])]},
                           2100 + 20 * number, preferences={'Lots of daylight': not shared})
            self.listings.append(listing)
        for identifier in ('in0', 'in1', 'in2'):
            self.find(identifier)['favorite'] = True
        result = rank(self.listings, {'likes': ['Lots of daylight'], 'dislikes': []}, {},
                      preference_readings={'Lots of daylight': {'terms': [], 'reason': 'Judged per listing in this test.'}})
        positions = ranks(result)
        self.assertLess(max(positions[f'in{number}'] for number in (3, 4, 5)), min(positions[f'out{number}'] for number in range(6, 12)))
        self.assertIn('corner unit', result[0]['in3'][1])

    def test_what_dismissed_listings_share_lowers_listings_that_have_it(self):
        for identifier, extra in (('v0', 'carpet'), ('h0', 'carpet'), ('v1', 'carpet')):
            listing = self.find(identifier)
            listing['features']['profile']['photos'].append(extra)
        for identifier in ('v0', 'h0'):
            self.find(identifier)['dismissed'] = True
        positions = ranks(rank(self.listings, self.preferences, {}))
        self.assertGreater(positions['v1'], max(positions[f'v{number}'] for number in range(2, 10)))

    def test_dismissals_lower_similar_listings(self):
        before = ranks(rank(self.listings, self.preferences, {}))
        for identifier in ('h0', 'h1', 'h2'):
            self.find(identifier)['dismissed'] = True
        after = ranks(rank(self.listings, self.preferences, {}))
        remaining = [f'h{number}' for number in range(3, 10)]
        self.assertGreater(min(after[identifier] for identifier in remaining), max(after[identifier] for identifier in after if identifier[0] == 'v'))
        self.assertGreater(sum(after[identifier] for identifier in remaining), sum(before[identifier] for identifier in remaining))

    def test_a_positive_note_raises_similar_listings_and_a_negative_note_lowers_them(self):
        self.find('g0')['notes'] = [{'id': 1, 'role': 'user'}]
        positive = ranks(rank(self.listings, self.preferences, {1: {'sentiment': 'positive', 'likes': [], 'dislikes': []}}))
        negative = ranks(rank(self.listings, self.preferences, {1: {'sentiment': 'negative', 'likes': [], 'dislikes': []}}))
        neutral = ranks(rank(self.listings, self.preferences, {}))
        for identifier in ('g1', 'g2', 'g3'):
            self.assertLess(positive[identifier], neutral[identifier])
            self.assertGreater(negative[identifier], neutral[identifier])

    def test_what_a_note_liked_or_disliked_moves_every_listing_that_has_it(self):
        self.listings = [item('a', VINTAGE), item('b', VINTAGE, extra=['fireplace']), item('c', VINTAGE, extra=['dim rooms']), item('d', GARDEN)]
        self.find('d')['notes'] = [{'id': 7, 'role': 'user'}]
        result = rank(self.listings, self.preferences, {7: {'sentiment': 'mixed', 'likes': ['fireplace'], 'dislikes': ['dim rooms']}})
        order = sorted(ranks(result), key=ranks(result).get)
        self.assertLess(order.index('b'), order.index('a'))
        self.assertLess(order.index('a'), order.index('c'))
        self.assertIn('fireplace', result[0]['b'][1])

    def test_stated_likes_and_dislikes_count_through_the_terms_they_map_to_without_any_listing_judgment(self):
        self.preferences = {'likes': ['Balcony'], 'dislikes': ['Tiny kitchen']}
        self.listings = [item('plain', VINTAGE), item('balcony', VINTAGE, extra=['balcony or terrace']),
                         item('cramped', VINTAGE, extra=['small kitchen'])]
        mapped = {'Balcony': {'terms': ['balcony or terrace'], 'reason': None}, 'Tiny kitchen': {'terms': ['small kitchen'], 'reason': None}}
        result = rank(self.listings, self.preferences, {}, preference_readings=mapped)
        self.assertEqual(sorted(ranks(result), key=ranks(result).get), ['balcony', 'plain', 'cramped'])
        self.assertIn('balcony', result[0]['balcony'][1].lower())
        self.assertIn('tiny kitchen', result[0]['cramped'][1].lower())

    def test_a_preference_the_vocabulary_cannot_express_counts_through_each_listings_judgment(self):
        self.preferences = {'likes': ['Near my office'], 'dislikes': []}
        self.listings = [item('far', VINTAGE, preferences={'Near my office': False}), item('near', VINTAGE, preferences={'Near my office': True})]
        unexpressed = {'Near my office': {'terms': [], 'reason': 'The vocabulary has no term for distance to a named place.'}}
        self.assertEqual(ranks(rank(self.listings, self.preferences, {}, preference_readings=unexpressed))['near'], 1)
        self.assertEqual(rank(self.listings, self.preferences, {})[0]['near'][1], 'near summary.')

    def test_a_preference_no_longer_stated_stops_counting(self):
        self.listings = [item('a', VINTAGE, 2200, extra=['fireplace']), item('b', VINTAGE, 2150)]
        result = rank(self.listings, self.preferences, {}, preference_readings={'Old wish': {'terms': ['fireplace'], 'reason': None}})
        self.assertNotIn('old wish', ' '.join(reason.lower() for _, reason in result[0].values()))


class ReasonAndCoverageTests(unittest.TestCase):
    def test_every_available_listing_gets_a_distinct_rank_and_a_one_line_reason(self):
        listings = clusters('v', VINTAGE, 4) + clusters('h', HIGH_RISE, 4)
        listings[0]['favorite'] = True
        ranked, unranked = rank(listings, {'likes': [], 'dislikes': []}, {})
        self.assertEqual(sorted(position for position, _ in ranked.values()), list(range(1, 9)))
        self.assertEqual(unranked, {})
        for _, reason in ranked.values():
            self.assertTrue(reason.strip())
            self.assertNotIn('\n', reason)
            self.assertLessEqual(len(reason), 200)
        self.assertIn('v0 Example Street', ranked['v1'][1])

    def test_listings_left_out_of_the_ranking_say_why_and_listings_awaiting_analysis_are_left_alone(self):
        listings = [item('shown', VINTAGE), item('dismissed', VINTAGE, dismissed=True), item('gone', VINTAGE, status='unavailable'),
                    item('outside', VINTAGE, matches=False), item('waiting', VINTAGE, profile=False)]
        ranked, unranked = rank(listings, {'likes': [], 'dislikes': []}, {})
        self.assertEqual(set(ranked), {'shown'})
        self.assertEqual(unranked, {'dismissed': NOT_RANKED['dismissed'], 'gone': NOT_RANKED['unavailable'], 'outside': NOT_RANKED['outside']})

    def test_a_listing_the_agent_could_not_analyze_is_ranked_by_price_size_and_rooms(self):
        listings = [item('close', VINTAGE, 2200, profile=False), item('far', VINTAGE, 2600, profile=False), item('liked', VINTAGE, 2190, favorite=True)]
        ranked, _ = rank(listings, {'likes': [], 'dislikes': []}, {}, unanalyzable={'close', 'far'})
        self.assertEqual(set(ranked), {'close', 'far', 'liked'})
        self.assertLess(ranked['close'][0], ranked['far'][0])
        self.assertEqual({ranked['close'][1], ranked['far'][1]}, {UNANALYZED})
        self.assertEqual(rank(listings, {'likes': [], 'dislikes': []}, {})[0].keys(), {'liked'})

    def test_without_feedback_or_preferences_the_reason_describes_the_listing(self):
        ranked, _ = rank([item('only', VINTAGE)], {'likes': [], 'dislikes': []}, {})
        self.assertEqual(ranked['only'], (1, 'only summary.'))


class ValidationTests(unittest.TestCase):
    def profile(self, **changes):
        return {'summary': 'Bright two-bedroom with hardwood floors', **VINTAGE, **changes}

    def test_a_profile_uses_only_the_vocabulary(self):
        validate_profile(self.profile())
        for changes in ({'photos': ['marble everywhere']}, {'amenities': ['exposed brick']}, {'summary': ''}, {'summary': 'x' * 201},
                        {'neighborhood': 5}, {'colour': []}, {'location': 'near the lake'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_profile(self.profile(**changes))
        with self.assertRaises(ValueError):
            validate_profile({key: value for key, value in self.profile().items() if key != 'photos'})

    def test_a_reading_names_a_sentiment_and_vocabulary_terms(self):
        validate_reading({'sentiment': 'mixed', 'likes': ['fireplace'], 'dislikes': ['carpet']})
        for reading in ({'sentiment': 'great', 'likes': [], 'dislikes': []}, {'sentiment': 'positive', 'likes': ['sunsets'], 'dislikes': []},
                        {'sentiment': 'positive', 'likes': []}):
            with self.subTest(reading=reading), self.assertRaises(ValueError):
                validate_reading(reading)

    def test_a_preference_reading_names_vocabulary_terms_or_says_why_none_fits(self):
        validate_preference_reading({'terms': ['balcony or terrace'], 'reason': None})
        validate_preference_reading({'terms': [], 'reason': 'No term covers distance to a named place.'})
        for reading in ({'terms': ['sunsets'], 'reason': None}, {'terms': [], 'reason': None}, {'terms': [], 'reason': ' '},
                        {'terms': ['carpet'], 'reason': 'Both given.'}, {'terms': ['carpet']}):
            with self.subTest(reading=reading), self.assertRaises(ValueError):
                validate_preference_reading(reading)

    def test_preference_judgments_are_true_false_or_null(self):
        validate_preferences({'Balcony': True, 'Carpet': None})
        for value in ({'Balcony': 1}, {'Balcony': 'yes'}, ['Balcony']):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_preferences(value)

    def test_the_vocabulary_has_no_duplicates_within_a_dimension(self):
        for dimension, terms in VOCABULARY.items():
            self.assertEqual(len(terms), len(set(terms)), dimension)


if __name__ == '__main__':
    unittest.main()
