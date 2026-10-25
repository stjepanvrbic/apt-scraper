import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/gallery/scripts'))

from record import CATEGORIES, FLAGS, RATINGS  # noqa: E402
from scoring import (DEFAULT_HYPERPARAMETERS, EXPLORATION_RANKS, expand, gradient, hessian, TOP, design, fit, fit_model, held_out_fit,  # noqa: E402
                     observations, prior, questions, rank_listings, reading_vocabulary, validate_note_reading,
                     validate_preference_reading)

SETTINGS = {'criteria': {'location': ['Logan Square', 'Avondale']}, 'preferences': {'likes': [], 'dislikes': []},
            'location_priorities': {'rapid_transit': 'low', 'areas': {'Logan Square': 'medium', 'Avondale': 'medium'}}}


def record(*, floor='hardwood', traits=(), concerns=(), ratings=None, unit_level='not shown', flags=None, **categories):
    return {
        'photos': [{'index': 1, 'room': 'living', 'url': 'https://example.com/1.jpg'}],
        'ratings': {name: (ratings or {}).get(name, {'value': 3, 'confidence': 0.8}) for name in RATINGS},
        'floor': floor, **{name: categories.get(name, 'not shown') for name in CATEGORIES if name != 'floor'},
        'unit_level': {'value': unit_level, 'evidence': '' if unit_level == 'not shown' else 'Photos.'},
        'flags': {flag: (flags or {}).get(flag) for flag in FLAGS},
        'traits': list(traits), 'concerns': list(concerns), 'summary': 'A listing.',
    }


def entry(identifier, *, price=2000, size=900, bedrooms=2, latitude=None, longitude=None, areas=('Logan Square',), unit_label=None,
          code_level=None, station=None, places=None, rankable=True, favorite=False, dismissed=False, contact=False, user_notes=False,
          judgments=None, given_up=False, **record_fields):
    """A listing as ranking_inputs gives it. station is (name, distance in km) to the nearest station, places the
    distance to each place by name."""
    return {
        'id': identifier, 'fingerprint': f'fingerprint-{identifier}', 'title': f'Listing {identifier}', 'address': None,
        'rankable': rankable and not dismissed, 'record': None if given_up else record(**record_fields), 'judgments': judgments or {},
        'code': {'monthly_price': price, 'currency': 'USD', 'size_sqft': size, 'price_per_sqft': price / size if size else None,
                 'bedrooms': bedrooms, 'bathrooms': 1, 'unit_label': unit_label, 'unit_floor': None, 'unit_level': code_level,
                 'photo_count': 10, 'latitude': latitude, 'longitude': longitude,
                 'nearest_station': station[0] if station else None, 'station_distance_km': station[1] if station else None,
                 'place_distances_km': places or {}},
        'areas': list(areas), 'given_up': given_up, 'favorite': favorite, 'dismissed': dismissed, 'contact': contact,
        'user_notes': user_notes, 'paired': False,
    }


def inputs(listings, *, settings=None, readings=None, note_readings=None, sentiments=None, pairs=()):
    paired = {identifier for pair in pairs for identifier in pair}
    return {'listings': [{**listing, 'paired': listing['id'] in paired, 'rankable': listing['rankable'] and not listing['dismissed']}
                         for listing in listings], 'settings': settings or SETTINGS,
            'preference_readings': readings or {}, 'note_readings': note_readings or {}, 'sentiments': sentiments or {}, 'pairs': list(pairs)}


def neighborhood(count, seed=1, **changes):
    """count listings of random floors, traits, ratings and prices."""
    generator = random.Random(seed)
    tags = ['exposed brick', 'bay window', 'skylight', 'fireplace', 'deck', 'patio', 'walk-in closet', 'pantry', 'street noise', 'walk-up']
    listings = []
    for number in range(count):
        listings.append(entry(
            f'listing:{number:03}', price=generator.randrange(1500, 2600, 50), size=generator.randrange(600, 1300, 25),
            floor=generator.choice(CATEGORIES['floor']), traits=generator.sample(tags, 3),
            ratings={name: {'value': generator.randint(1, 5), 'confidence': generator.choice((0.5, 0.8, 1.0))} for name in RATINGS},
            flags={flag: generator.choice((True, False, None)) for flag in FLAGS},
            latitude=41.92 + generator.uniform(-0.03, 0.03), longitude=-87.71 + generator.uniform(-0.03, 0.03),
            areas=(generator.choice(['Logan Square', 'Avondale']),), **changes))
    return listings


def contribution(result, identifier, feature):
    return next(part['contribution'] for part in result['ranked'][identifier]['explanation']['contributions'] if part['feature'] == feature)


def ranks(result):
    return {identifier: entry['rank'] for identifier, entry in result['ranked'].items()}


class OverruledPreferenceTests(unittest.TestCase):
    """Spec › Acceptance "Overruled preference": with the stated dislike "Carpet" and five carpeted favorites, carpet does
    not count against an unseen carpeted listing in its explanation."""

    def setUp(self):
        self.settings = {**SETTINGS, 'preferences': {'likes': [], 'dislikes': ['Carpet']}}
        self.readings = {('dislike', 'Carpet'): [{'feature': 'floor:carpet', 'direction': 'dislike'}]}
        self.listings = [entry(f'listing:{number:02}', floor='carpet' if number % 2 else 'hardwood', price=1800 + 20 * number)
                         for number in range(40)]

    def test_the_stated_dislike_counts_against_carpet_before_any_label(self):
        result = rank_listings(inputs(self.listings, settings=self.settings, readings=self.readings), seed=1)
        self.assertLess(contribution(result, 'listing:39', 'floor:carpet'), 0)

    def test_five_carpeted_favorites_overrule_it_for_an_unseen_carpeted_listing(self):
        favorites = {'listing:01', 'listing:03', 'listing:05', 'listing:07', 'listing:09'}
        listings = [{**listing, 'favorite': listing['id'] in favorites} for listing in self.listings]
        result = rank_listings(inputs(listings, settings=self.settings, readings=self.readings), seed=1)
        self.assertGreaterEqual(contribution(result, 'listing:39', 'floor:carpet'), 0)
        self.assertGreater(result['summary']['weights']['floor:carpet'], 0)
        self.assertNotIn('carpet floor', result['ranked']['listing:39']['reason'].split('Against it:')[-1])


class LabelTests(unittest.TestCase):
    def test_favorites_raise_the_listings_that_share_their_features_and_dismissals_lower_them(self):
        listings = neighborhood(30)
        alike = [listing['id'] for listing in listings if listing['record']['floor'] == 'tile or vinyl']
        before = ranks(rank_listings(inputs(listings), seed=1))
        favorites = set(alike[:3])
        favored = [{**listing, 'favorite': listing['id'] in favorites} for listing in listings]
        after = ranks(rank_listings(inputs(favored), seed=1))
        unseen = alike[3:]
        self.assertLess(sum(after[identifier] for identifier in unseen), sum(before[identifier] for identifier in unseen))
        dismissed = [{**listing, 'dismissed': listing['id'] in favorites} for listing in listings]
        lowered = ranks(rank_listings(inputs(dismissed), seed=1))
        self.assertGreater(sum(lowered[identifier] for identifier in unseen), sum(before[identifier] for identifier in unseen))
        self.assertFalse(favorites & set(lowered))

    def test_a_positive_note_labels_its_listing_like_a_favorite_and_a_negative_one_like_a_dismissal(self):
        listings = neighborhood(30)
        alike = [listing['id'] for listing in listings if listing['record']['floor'] == 'carpet']
        sentiments = dict.fromkeys(alike[:3], 'positive')
        noted = [{**listing, 'user_notes': listing['id'] in sentiments} for listing in listings]
        favored = [{**listing, 'favorite': listing['id'] in sentiments} for listing in listings]
        plain = rank_listings(inputs(noted), seed=1)['summary']['weights']['floor:carpet']
        by_note = rank_listings(inputs(noted, sentiments=sentiments), seed=1)['summary']['weights']['floor:carpet']
        by_favorite = rank_listings(inputs(favored), seed=1)['summary']['weights']['floor:carpet']
        self.assertGreater(by_note, plain)
        self.assertGreater(by_favorite, plain)
        negative = rank_listings(inputs(noted, sentiments=dict.fromkeys(alike[:3], 'negative')), seed=1)['summary']['weights']['floor:carpet']
        self.assertLess(negative, plain)
        for sentiment in ('mixed', 'neutral'):
            self.assertEqual(rank_listings(inputs(noted, sentiments=dict.fromkeys(alike[:3], sentiment)), seed=1)['summary']['weights']['floor:carpet'], plain)

    def test_a_contact_request_counts_more_than_a_favorite(self):
        listings = neighborhood(30)
        alike = [listing['id'] for listing in listings if listing['record']['floor'] == 'mixed'][:2]
        favored = rank_listings(inputs([{**listing, 'favorite': listing['id'] in alike} for listing in listings]), seed=1)
        contacted = rank_listings(inputs([{**listing, 'contact': listing['id'] in alike} for listing in listings]), seed=1)
        self.assertGreater(contacted['summary']['weights']['floor:mixed'], favored['summary']['weights']['floor:mixed'])

    def test_a_note_reading_sets_the_starting_weight_of_the_features_it_names(self):
        listings = neighborhood(30)
        reading = {7: {'sentiment': 'neutral', 'likes': ['tag:skylight'], 'dislikes': ['flag:parking']}}
        weights = rank_listings(inputs(listings, note_readings=reading), seed=1)['summary']['weights']
        plain = rank_listings(inputs(listings), seed=1)['summary']['weights']
        self.assertGreater(weights['tag:skylight'], plain['tag:skylight'])
        self.assertLess(weights['flag:parking'], plain['flag:parking'])


class PairwiseTests(unittest.TestCase):
    """Spec › Acceptance "Pairwise questions": a single answer ranks the chosen listing above the other at the next rerank."""

    def test_a_single_answer_ranks_the_chosen_listing_above_the_other(self):
        listings = neighborhood(30)
        before = ranks(rank_listings(inputs(listings), seed=1))
        lower, higher = max(before, key=before.get), min(before, key=before.get)
        after = ranks(rank_listings(inputs(listings, pairs=[(lower, higher)]), seed=1))
        self.assertLess(after[lower], after[higher])

    def test_questions_pair_the_listings_whose_order_is_least_sure_without_repeating_a_listing_or_an_answered_pair(self):
        listings = neighborhood(30)
        asked = questions(inputs(listings), DEFAULT_HYPERPARAMETERS, 5, [])
        self.assertEqual(len(asked), 5)
        named = [identifier for question in asked for identifier in question['listings']]
        self.assertEqual(len(named), len(set(named)))
        certainties = [abs(question['probability_first_above'] - 0.5) for question in asked]
        self.assertEqual(certainties, sorted(certainties))
        again = questions(inputs(listings), DEFAULT_HYPERPARAMETERS, 5, [tuple(reversed(asked[0]['listings']))])
        self.assertNotIn(set(asked[0]['listings']), [set(question['listings']) for question in again])

    def test_before_any_label_the_questions_come_from_the_stated_preferences(self):
        settings = {**SETTINGS, 'preferences': {'likes': ['Skylight'], 'dislikes': []}}
        readings = {('like', 'Skylight'): [{'feature': 'tag:skylight', 'direction': 'like'}]}
        self.assertEqual(len(questions(inputs(neighborhood(30), settings=settings, readings=readings), DEFAULT_HYPERPARAMETERS, 6, [])), 6)


class ExplanationTests(unittest.TestCase):
    def setUp(self):
        listings = neighborhood(30)
        favorites = {listing['id'] for listing in listings if listing['record']['floor'] == 'carpet'}
        self.listings = [{**listing, 'favorite': listing['id'] in list(favorites)[:4]} for listing in listings]
        self.result = rank_listings(inputs(self.listings), seed=1)

    def test_the_contributions_add_up_to_the_score_and_name_every_feature(self):
        columns = [column.name for column in design(inputs(self.listings)).columns]
        for identifier, ranked in self.result['ranked'].items():
            details = ranked['explanation']
            self.assertAlmostEqual(sum(part['contribution'] for part in details['contributions']), details['score'], places=9)
            self.assertEqual(sorted(part['feature'] for part in details['contributions']), sorted(columns))
        scores = [self.result['ranked'][identifier]['explanation']['score'] for identifier in sorted(self.result['ranked'], key=lambda key: self.result['ranked'][key]['rank'])]
        exploration = set(self.result['summary']['exploration'])
        ordered = [score for identifier, score in zip(sorted(self.result['ranked'], key=lambda key: self.result['ranked'][key]['rank']), scores) if identifier not in exploration]
        self.assertEqual(ordered, sorted(ordered, reverse=True))

    def test_the_reason_names_the_largest_contributions_from_the_record_and_the_most_similar_favorite(self):
        for identifier, ranked in self.result['ranked'].items():
            listing = next(listing for listing in self.listings if listing['id'] == identifier)
            similar = ranked['explanation']['similar_favorite']
            self.assertIsNotNone(similar)
            self.assertNotEqual(similar['id'], identifier)
            self.assertIn(f'Most like your favorite {similar["title"]}.', ranked['reason'])
            cited = [part for part in ranked['explanation']['contributions'] if part['value'] and part['contribution'] >= 0.05][:3]
            if cited:
                self.assertIn('For it: ' + ', '.join(part['value'] for part in cited) + '.', ranked['reason'])
            for part in ranked['explanation']['contributions']:
                if part['feature'].startswith('tag:') and part['value']:
                    self.assertIn(part['value'], listing['record']['traits'] + listing['record']['concerns'])
                if part['feature'].startswith('floor:') and part['value']:
                    self.assertEqual(part['value'], f'{listing["record"]["floor"]} floor')

    def test_the_most_similar_favorite_is_the_nearest_under_the_learned_weights(self):
        model = fit_model(inputs(self.listings))
        rows = model.design.rows
        weights = model.fit.weights[1:]
        for identifier, ranked in self.result['ranked'].items():
            index = model.design.ids.index(identifier)
            distances = {favorite: math.dist([w * x for w, x in zip(weights, rows[index])],
                                             [w * x for w, x in zip(weights, rows[model.design.ids.index(favorite)])])
                         for favorite in model.design.favorites if favorite != identifier}
            self.assertEqual(ranked['explanation']['similar_favorite']['id'], min(distances, key=distances.get))

    def test_a_listing_extraction_gave_up_on_is_ranked_from_its_code_features_and_its_reason_says_so(self):
        listings = [*self.listings, entry('listing:given-up', given_up=True, price=1500)]
        result = rank_listings(inputs(listings), seed=1)
        ranked = result['ranked']['listing:given-up']
        self.assertIn('Ranked from code features only, because extraction failed 3 times.', ranked['reason'])
        named = {part['feature'] for part in ranked['explanation']['contributions'] if part['value'] is not None}
        self.assertTrue(named)
        self.assertTrue(all(name in ('price', 'size', 'price_per_sqft', 'bedrooms', 'bathrooms', 'photos', 'near_favorites') or name.startswith('area:')
                            for name in named))

    def test_a_listing_waiting_for_its_record_is_not_ranked_and_says_why(self):
        waiting = {**entry('listing:waiting'), 'record': None}
        result = rank_listings(inputs([*self.listings, waiting]), seed=1)
        self.assertNotIn('listing:waiting', result['ranked'])
        self.assertEqual(result['unranked']['listing:waiting']['reason'], 'Not ranked yet: waiting for its feature record.')


class FeatureTests(unittest.TestCase):
    def test_a_unit_level_read_from_a_label_beats_the_one_extraction_read(self):
        listings = [entry('listing:1', code_level='garden', unit_label='#G', unit_level='ground'),
                    entry('listing:2', unit_level='ground'), entry('listing:3', unit_level='middle')]
        model_design = design(inputs(listings))
        self.assertEqual(model_design.raw[0]['unit_level:garden'], 1.0)
        self.assertEqual(model_design.raw[0]['unit_level:ground'], 0.0)
        self.assertEqual(model_design.raw[1]['unit_level:ground'], 1.0)

    def test_a_value_the_listing_does_not_state_counts_neither_way(self):
        listings = neighborhood(12)
        listings[0]['record']['ratings']['natural_light'] = 'not shown'
        listings[0]['record']['flags']['dishwasher'] = None
        listings[0]['record']['floor'] = 'not shown'
        listings[0]['code']['size_sqft'] = None
        result = rank_listings(inputs([{**listing, 'favorite': number in (3, 5)} for number, listing in enumerate(listings)]), seed=1)
        parts = {part['feature']: part for part in result['ranked'][listings[0]['id']]['explanation']['contributions']}
        for name in ('rating:natural_light', 'flag:dishwasher', 'floor:carpet', 'floor:hardwood', 'size'):
            self.assertEqual(parts[name]['contribution'], 0.0, name)
            self.assertIsNone(parts[name]['value'], name)

    def test_ratings_count_by_their_confidence(self):
        listings = [entry('listing:1', ratings={'natural_light': {'value': 5, 'confidence': 1.0}}),
                    entry('listing:2', ratings={'natural_light': {'value': 5, 'confidence': 0.2}}),
                    entry('listing:3', ratings={'natural_light': {'value': 1, 'confidence': 1.0}})]
        model_design = design(inputs(listings))
        column = model_design.index('rating:natural_light')
        self.assertGreater(model_design.rows[0][column], model_design.rows[1][column])
        self.assertGreater(model_design.rows[1][column], model_design.rows[2][column])

    def test_a_preference_no_feature_expresses_counts_through_its_judgments(self):
        settings = {**SETTINGS, 'preferences': {'likes': ['Quiet street'], 'dislikes': []}}
        listings = [entry(f'listing:{number}', judgments={'Quiet street': {'verdict': ('has', 'rules out', 'does not say')[number % 3], 'evidence': ''}})
                    for number in range(9)]
        result = rank_listings(inputs(listings, settings=settings, readings={('like', 'Quiet street'): []}), seed=1)
        self.assertGreater(contribution(result, 'listing:0', 'preference:Quiet street'), 0)
        self.assertLess(contribution(result, 'listing:1', 'preference:Quiet street'), 0)
        self.assertEqual(contribution(result, 'listing:2', 'preference:Quiet street'), 0)
        expressed = rank_listings(inputs(listings, settings={**settings, 'preferences': {'likes': ['Quiet street'], 'dislikes': []}},
                                         readings={('like', 'Quiet street'): [{'feature': 'tag:street noise', 'direction': 'dislike'}]}), seed=1)
        self.assertNotIn('preference:Quiet street', expressed['summary']['weights'])


class LocationTests(unittest.TestCase):
    def test_an_area_that_matters_more_counts_more(self):
        settings = {**SETTINGS, 'location_priorities': {'rapid_transit': 'low', 'areas': {'Logan Square': 'high', 'Avondale': 'low'}}}
        listings = [entry('listing:1', areas=('Logan Square',)), entry('listing:2', areas=('Avondale',))]
        self.assertEqual(ranks(rank_listings(inputs(listings, settings=settings), seed=1)), {'listing:1': 1, 'listing:2': 2})
        settings['location_priorities']['areas'] = {'Logan Square': 'low', 'Avondale': 'high'}
        self.assertEqual(ranks(rank_listings(inputs(listings, settings=settings), seed=1)), {'listing:2': 1, 'listing:1': 2})

    def test_a_favorite_is_as_near_the_favorites_as_another_listing_at_the_same_place(self):
        listings = [entry('favorite:1', favorite=True, latitude=41.93, longitude=-87.70), entry('favorite:2', favorite=True, latitude=41.93, longitude=-87.70),
                    entry('listing:same', latitude=41.93, longitude=-87.70), entry('listing:far', latitude=41.96, longitude=-87.75)]
        model_design = design(inputs(listings))
        values = {identifier: raw['near_favorites'] for identifier, raw in zip(model_design.ids, model_design.raw)}
        self.assertAlmostEqual(values['favorite:1'], values['listing:same'])
        self.assertGreater(values['listing:same'], values['listing:far'])
        column = model_design.index('near_favorites')
        without = {identifier: row.head[column + 1] for identifier, row in zip(model_design.ids, model_design.rows_without['favorite:1'])}
        scale, center = model_design.columns[column].scale, model_design.columns[column].mean
        self.assertAlmostEqual(without['listing:same'] * scale + center, 1.0)
        self.assertAlmostEqual(without['favorite:2'], 0.0)

    def test_listings_near_the_favorites_rank_higher_than_alike_listings_far_from_them(self):
        favorites = [entry(f'favorite:{number}', favorite=True, latitude=41.93 + 0.001 * number, longitude=-87.70) for number in range(4)]
        near = entry('listing:near', latitude=41.931, longitude=-87.701)
        far = entry('listing:far', latitude=41.99, longitude=-87.80)
        others = [entry(f'listing:{number}', latitude=41.95 + 0.004 * number, longitude=-87.75) for number in range(10)]
        result = rank_listings(inputs([*favorites, near, far, *others]), seed=1)
        self.assertLess(result['ranked']['listing:near']['rank'], result['ranked']['listing:far']['rank'])
        self.assertIn('km from the nearest favorite', result['ranked']['listing:near']['reason'])


class DistanceTests(unittest.TestCase):
    """Spec › Acceptance "Location": of two test listings alike except for the distance to a place, the closer one ranks
    higher."""

    SETTINGS = {**SETTINGS, 'places': [{'name': 'Work', 'address': '233 S Wacker Dr, Chicago, IL 60606'}],
                'location_priorities': {**SETTINGS['location_priorities'], 'places': 'high'}}

    def surroundings(self, count, seed=3, **changes):
        generator = random.Random(seed)
        return [{**listing, 'code': {**listing['code'], 'nearest_station': 'Logan Square', 'station_distance_km': generator.uniform(0.1, 2.5),
                                     'place_distances_km': {'Work': generator.uniform(2, 12)}}}
                for listing in neighborhood(count, seed, **changes)]

    def test_the_listing_closer_to_a_place_ranks_higher_than_one_alike_but_farther(self):
        closer = entry('listing:closer', station=('Logan Square', 0.8), places={'Work': 4.0}, latitude=41.92, longitude=-87.71)
        farther = entry('listing:farther', station=('Logan Square', 0.8), places={'Work': 9.0}, latitude=41.92, longitude=-87.71)
        for name, others in [('without labels', self.surroundings(30)),
                             ('with favorites', [{**listing, 'favorite': index < 5} for index, listing in enumerate(self.surroundings(30))])]:
            with self.subTest(name):
                result = rank_listings(inputs([*others, closer, farther], settings=self.SETTINGS), seed=1)
                self.assertLess(result['ranked']['listing:closer']['rank'], result['ranked']['listing:farther']['rank'])
                self.assertLess(contribution(result, 'listing:farther', 'place:Work'), contribution(result, 'listing:closer', 'place:Work'))

    def test_the_listing_closer_to_a_station_ranks_higher_than_one_alike_but_farther(self):
        listings = [entry('listing:near', station=('Logan Square', 0.3)), entry('listing:far', station=('California', 1.9))]
        self.assertEqual(ranks(rank_listings(inputs(listings), seed=1)), {'listing:near': 1, 'listing:far': 2})

    def test_the_location_priorities_set_the_starting_weights_of_the_distances(self):
        listings = [entry('listing:1', station=('A', 0.3), places={'Work': 3.0}), entry('listing:2', station=('B', 1.9), places={'Work': 7.0})]
        for places, rapid_transit, expected in [('high', 'low', {'place:Work': -1.0, 'station_distance': -0.25}),
                                                ('low', 'medium', {'place:Work': -0.25, 'station_distance': -0.5})]:
            settings = {**self.SETTINGS, 'location_priorities': {**self.SETTINGS['location_priorities'], 'places': places, 'rapid_transit': rapid_transit}}
            model_design = design(inputs(listings, settings=settings))
            self.assertEqual({name: model_design.prior_mean[model_design.index(name)] for name in expected}, expected)

    def test_a_listing_without_coordinates_has_unknown_distances_that_count_neither_way(self):
        listings = [*self.surroundings(10), entry('listing:unknown', areas=('Logan Square',))]
        result = rank_listings(inputs(listings, settings=self.SETTINGS), seed=1)
        parts = {part['feature']: part for part in result['ranked']['listing:unknown']['explanation']['contributions']}
        for name in ('station_distance', 'place:Work'):
            self.assertEqual((parts[name]['contribution'], parts[name]['value']), (0, None))

    def test_explanations_state_the_distances_in_kilometers(self):
        listings = [entry('listing:1', station=('Logan Square', 0.34), places={'Work': 2.04}),
                    entry('listing:2', station=('California', 1.9), places={'Work': 9.0})]
        result = rank_listings(inputs(listings, settings=self.SETTINGS), seed=1)
        parts = {part['feature']: part['value'] for part in result['ranked']['listing:1']['explanation']['contributions']}
        self.assertEqual((parts['station_distance'], parts['place:Work']), ('0.3 km to the nearest station, Logan Square', '2.0 km from Work'))
        self.assertIn('2.0 km from Work', result['ranked']['listing:1']['reason'])


class ExplorationTests(unittest.TestCase):
    """Spec › Acceptance "Exploration": with more than 20 ranked listings, exactly two of the top 20 are marked as
    exploration listings, and neither has a label from the user."""

    def test_two_unlabeled_listings_from_the_posterior_are_in_the_top_20(self):
        listings = neighborhood(40)
        for number in (2, 9, 17, 23, 31):
            listings[number]['favorite'] = True
        listings[12]['user_notes'] = True
        result = rank_listings(inputs(listings), seed=3)
        marked = [identifier for identifier, ranked in result['ranked'].items() if ranked['exploration']]
        self.assertEqual(sorted(marked), sorted(result['summary']['exploration']))
        self.assertEqual(len(marked), 2)
        self.assertEqual(sorted(result['ranked'][identifier]['rank'] for identifier in marked), list(EXPLORATION_RANKS))
        labeled = {listing['id'] for listing in listings if listing['favorite'] or listing['user_notes']}
        self.assertFalse(labeled & set(marked))
        mean_order = sorted(result['ranked'], key=lambda identifier: -result['ranked'][identifier]['explanation']['score'])
        for identifier in marked:
            self.assertGreaterEqual(mean_order.index(identifier), TOP - 2)
            self.assertTrue(result['ranked'][identifier]['reason'].startswith('Shown to learn your taste.'))

    def test_no_listing_explores_with_20_or_fewer_ranked(self):
        result = rank_listings(inputs(neighborhood(20)), seed=3)
        self.assertEqual(result['summary']['exploration'], [])
        self.assertFalse(any(ranked['exploration'] for ranked in result['ranked'].values()))


class ReproducibilityTests(unittest.TestCase):
    """Spec › Contracts and invariants: apart from the exploration listings, the same records, labels and settings give
    the same ranks and reasons."""

    def setUp(self):
        self.listings = neighborhood(40)
        for number in (1, 4, 8):
            self.listings[number]['favorite'] = True

    def test_a_fixed_seed_gives_the_same_result(self):
        self.assertEqual(rank_listings(inputs(self.listings), seed=11), rank_listings(inputs(self.listings), seed=11))

    def test_another_seed_changes_only_the_exploration_listings(self):
        first, second = (rank_listings(inputs(self.listings), seed=seed) for seed in (11, 12))
        for result in (first, second):
            exploring = set(result['summary']['exploration'])
            result['order'] = [identifier for identifier in sorted(result['ranked'], key=lambda key: result['ranked'][key]['rank'])
                               if identifier not in exploring and identifier not in first['summary']['exploration'] + second['summary']['exploration']]
        self.assertEqual(first['order'], second['order'])
        for identifier in first['order']:
            self.assertEqual(first['ranked'][identifier]['reason'], second['ranked'][identifier]['reason'])
            self.assertEqual(first['ranked'][identifier]['explanation'], second['ranked'][identifier]['explanation'])
        self.assertEqual(first['summary']['held_out'], second['summary']['held_out'])

    def test_without_exploration_any_seed_gives_the_same_result(self):
        listings = self.listings[:20]
        first, second = (rank_listings(inputs(listings), seed=seed) for seed in (11, 12))
        self.assertEqual(first['ranked'], second['ranked'])


class HeldOutTests(unittest.TestCase):
    def test_each_favorite_is_ranked_among_the_other_listings_by_a_fit_without_it(self):
        listings = neighborhood(40)
        garden = [3, 8, 14, 21, 27, 33]
        for number in garden:
            listings[number]['record']['traits'] = ['garden', 'patio']
            listings[number]['record']['unit_level'] = {'value': 'garden or basement', 'evidence': 'Photos.'}
        for number in garden[:5]:
            listings[number]['favorite'] = True
        result = rank_listings(inputs(listings), seed=1)
        held_out = {item['id']: item['rank'] for item in result['summary']['held_out']}
        self.assertEqual(set(held_out), {listings[number]['id'] for number in garden[:5]})
        self.assertTrue(all(rank <= 3 for rank in held_out.values()), held_out)
        self.assertEqual(result['summary']['held_out_median'], sorted(held_out.values())[2])
        unseen = min((ranked['rank'], identifier) for identifier, ranked in result['ranked'].items() if identifier not in held_out)
        self.assertEqual(unseen, (6, listings[33]['id']))

    def test_the_held_out_fit_is_the_fit_without_the_favorite(self):
        listings = neighborhood(30)
        for number in (2, 5, 11, 19):
            listings[number]['favorite'] = True
        problem = inputs(listings)
        model_design = design(problem)
        scale, unlabeled_weight = DEFAULT_HYPERPARAMETERS
        mean, precision = prior(model_design, scale)
        data = observations(model_design, problem, unlabeled_weight)
        full = fit(data, mean, precision, model_design.dense_count)
        for number in (2, 5, 11, 19):
            favorite = listings[number]['id']
            rows, weights = held_out_fit(model_design, problem, full, data, unlabeled_weight, mean, precision, favorite)
            exact = fit(observations(model_design, problem, unlabeled_weight, model_design.rows_without[favorite], leave_out=favorite), mean, precision, model_design.dense_count)
            self.assertLess(max(abs(left - right) for left, right in zip(weights, exact.weights)), 1e-5)
            self.assertNotEqual(rows, model_design.likelihood_rows)


class LikelihoodTests(unittest.TestCase):
    def test_the_sparse_gradient_and_hessian_equal_the_dense_ones(self):
        listings = neighborhood(25)
        listings[3] = {**listings[3], 'record': None, 'given_up': True}
        for number in (1, 6, 9):
            listings[number]['favorite'] = True
        problem = inputs(listings, pairs=[(listings[2]['id'], listings[4]['id']), (listings[3]['id'], listings[8]['id'])])
        model_design = design(problem)
        mean, precision = prior(model_design, 1.0)
        data = observations(model_design, problem, 0.1)
        size = len(mean)
        weights = [0.1 * ((index * 7) % 5 - 2) for index in range(size)]
        rows = [expand(row, size) for row in data.rows]
        probabilities = [1 / (1 + math.exp(-sum(a * b for a, b in zip(row, weights)))) for row in rows]
        expected_gradient = [sum(weight * (probability - label) * row[column] for weight, probability, label, row
                                 in zip(data.weights, probabilities, data.labels, rows)) + precision[column] * (weights[column] - mean[column])
                             for column in range(size)]
        curvature = [weight * probability * (1 - probability) for weight, probability in zip(data.weights, probabilities)]
        expected_hessian = [[sum(c * row[first] * row[second] for c, row in zip(curvature, rows)) + (precision[first] if first == second else 0.0)
                             for second in range(size)] for first in range(size)]
        actual_gradient, actual_curvature = gradient(data, weights, mean, precision, model_design.dense_count)
        actual_hessian = hessian(data, actual_curvature, precision, model_design.dense_count)
        self.assertLess(max(abs(a - b) for a, b in zip(actual_gradient, expected_gradient)), 1e-9)
        self.assertLess(max(abs(a - b) for left, right in zip(actual_hessian, expected_hessian) for a, b in zip(left, right)), 1e-9)

    def test_ranking_from_uncentered_sparse_columns_matches_the_centered_scores(self):
        listings = neighborhood(25)
        result = rank_listings(inputs(listings), seed=1)
        order = sorted(result['ranked'], key=lambda identifier: result['ranked'][identifier]['rank'])
        exploring = set(result['summary']['exploration'])
        by_score = sorted((identifier for identifier in result['ranked'] if identifier not in exploring),
                          key=lambda identifier: (-result['ranked'][identifier]['explanation']['score'], identifier))
        self.assertEqual([identifier for identifier in order if identifier not in exploring], by_score)


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.vocabulary = reading_vocabulary(['exposed brick'], ['Logan Square'])

    def test_readings_may_name_the_distances(self):
        vocabulary = reading_vocabulary([], ['Logan Square'], ['Work'])
        self.assertEqual((vocabulary['station_distance'], vocabulary['place:Work']),
                         ('a longer distance to the nearest rapid-transit station', 'a longer distance to Work'))
        self.assertEqual(validate_preference_reading([{'feature': 'station_distance', 'direction': 'dislike'}], vocabulary),
                         [{'feature': 'station_distance', 'direction': 'dislike'}])

    def test_a_preference_reading_names_vocabulary_features_with_a_direction(self):
        self.assertEqual(validate_preference_reading([{'feature': 'floor:carpet', 'direction': 'dislike'}], self.vocabulary),
                         [{'feature': 'floor:carpet', 'direction': 'dislike'}])
        self.assertEqual(validate_preference_reading([], self.vocabulary), [])
        for features, message in [([{'feature': 'floor:shag', 'direction': 'dislike'}], 'not a feature'),
                                  ([{'feature': 'tag:exposed brick', 'direction': 'love'}], 'like or dislike'),
                                  ([{'feature': 'price', 'direction': 'like'}, {'feature': 'price', 'direction': 'dislike'}], 'twice')]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                validate_preference_reading(features, self.vocabulary)

    def test_a_note_reading_has_a_sentiment_and_vocabulary_likes_and_dislikes(self):
        reading = {'sentiment': 'mixed', 'likes': ['rating:natural_light'], 'dislikes': ['area:Logan Square']}
        self.assertEqual(validate_note_reading(reading, self.vocabulary), reading)
        with self.assertRaisesRegex(ValueError, 'sentiment'):
            validate_note_reading({**reading, 'sentiment': 'happy'}, self.vocabulary)
        with self.assertRaisesRegex(ValueError, 'either liked or disliked'):
            validate_note_reading({**reading, 'likes': ['area:Logan Square']}, self.vocabulary)


if __name__ == '__main__':
    unittest.main()
