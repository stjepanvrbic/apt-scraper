import sys
import unittest
from pathlib import Path
from typing import Unpack

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/search/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/gallery/scripts'))

from code_features import RankingFeatures, UnitFacts  # noqa: E402
from matching import INSIDE, OUTSIDE, PENDING, MustHaves, criterion_phrases, evaluate, unjudged  # noqa: E402
from shapes import Criteria  # noqa: E402

CRITERIA: Criteria = {
    'location': ['Lakeview, Chicago, IL', 'Logan Square, Chicago, IL'],
    'price': {'min': None, 'max': 2400, 'currency': 'USD', 'period': 'month'},
    'size': {'min': 700, 'max': None, 'unit': 'sqft'},
    'rooms': {'min': 2, 'max': None},
    'move_in': '2026-12-01',
    'other': ['Dogs allowed'],
}


def unit(**changes: Unpack[UnitFacts]) -> UnitFacts:
    base: UnitFacts = {'price': 2100, 'period': 'month', 'bedrooms': 2, 'size_sqft': 850, 'available_on': '2026-11-15'}
    return {**base, **changes}


def features(*units: UnitFacts, areas: tuple[str, ...] = ('Lakeview, Chicago, IL',), currency: str = 'USD',
             must_haves: MustHaves | None = None) -> RankingFeatures:
    return {'facts': {'areas': list(areas), 'currency': currency, 'units': list(units)},
            'must_haves': {'Dogs allowed': True} if must_haves is None else must_haves}


def criteria(**changes: Unpack[Criteria]) -> Criteria:
    return {**CRITERIA, **changes}


class EvaluateTests(unittest.TestCase):
    def test_listing_within_every_criterion_is_inside(self) -> None:
        self.assertEqual(evaluate(CRITERIA, features(unit())), INSIDE)

    def test_each_stated_fact_outside_its_criterion_puts_the_listing_outside(self) -> None:
        outside: list[UnitFacts] = [{'price': 2500}, {'bedrooms': 1}, {'size_sqft': 600}, {'available_on': '2027-01-01'}]
        for changes in outside:
            with self.subTest(changes=changes):
                self.assertEqual(evaluate(CRITERIA, features(unit(**changes))), OUTSIDE)

    def test_a_fact_the_listing_does_not_state_never_puts_it_outside(self) -> None:
        unknown = unit(price=None, bedrooms=None, size_sqft=None, available_on=None)
        self.assertEqual(evaluate(CRITERIA, features(unknown)), INSIDE)
        self.assertEqual(evaluate(CRITERIA, features()), INSIDE)
        self.assertEqual(evaluate(CRITERIA, features(unit(), areas=())), INSIDE)

    def test_a_building_is_inside_when_one_of_its_units_meets_every_criterion(self) -> None:
        cheap_studio = unit(price=1500, bedrooms=0)
        large_and_expensive = unit(price=3200, bedrooms=3)
        self.assertEqual(evaluate(CRITERIA, features(cheap_studio, large_and_expensive)), OUTSIDE)
        self.assertEqual(evaluate(CRITERIA, features(cheap_studio, large_and_expensive, unit())), INSIDE)

    def test_listings_found_only_under_areas_no_longer_searched_are_outside(self) -> None:
        self.assertEqual(evaluate(criteria(location=['Logan Square, Chicago, IL']), features(unit())), OUTSIDE)
        both = features(unit(), areas=('Lakeview, Chicago, IL', 'Logan Square, Chicago, IL'))
        self.assertEqual(evaluate(criteria(location=['Logan Square, Chicago, IL']), both), INSIDE)

    def test_prices_are_compared_per_criterion_period(self) -> None:
        self.assertEqual(evaluate(CRITERIA, features(unit(price=500, period='week'))), INSIDE)
        self.assertEqual(evaluate(CRITERIA, features(unit(price=600, period='week'))), OUTSIDE)
        yearly = criteria(price={'min': None, 'max': 24000, 'currency': 'USD', 'period': 'year'})
        self.assertEqual(evaluate(yearly, features(unit(price=2100))), OUTSIDE)
        self.assertEqual(evaluate(yearly, features(unit(price=1900))), INSIDE)

    def test_a_price_in_another_currency_or_for_the_whole_stay_is_not_compared(self) -> None:
        self.assertEqual(evaluate(CRITERIA, features(unit(price=9000), currency='EUR')), INSIDE)
        self.assertEqual(evaluate(CRITERIA, features(unit(price=9000, period='total'))), INSIDE)

    def test_square_meters_are_compared_with_square_feet(self) -> None:
        metric = criteria(size={'min': 70, 'max': None, 'unit': 'm2'})
        self.assertEqual(evaluate(metric, features(unit(size_sqft=760))), INSIDE)
        self.assertEqual(evaluate(metric, features(unit(size_sqft=740))), OUTSIDE)

    def test_limits_are_inclusive_and_a_missing_limit_is_no_limit(self) -> None:
        self.assertEqual(evaluate(CRITERIA, features(unit(price=2400, size_sqft=700))), INSIDE)
        open_criteria = criteria(price={'min': None, 'max': None}, size={'min': None, 'max': None}, rooms={'min': None, 'max': None}, move_in=None)
        self.assertEqual(evaluate(open_criteria, features(unit(price=9000, bedrooms=0, size_sqft=100, available_on='2030-01-01'))), INSIDE)

    def test_unjudged_must_haves_leave_the_listing_pending(self) -> None:
        self.assertEqual(evaluate(CRITERIA, features(unit(), must_haves={})), PENDING)
        self.assertEqual(unjudged(CRITERIA, features(unit(), must_haves={'Elevator': True})), ['Dogs allowed'])
        self.assertEqual(unjudged(criteria(other=[]), features(unit(), must_haves={})), [])

    def test_a_listing_without_search_facts_is_judged_on_every_criterion(self) -> None:
        phrases = criterion_phrases(CRITERIA)
        self.assertEqual(phrases, ['In one of these areas: Lakeview, Chicago, IL; Logan Square, Chicago, IL', 'Price at most 2,400 USD a month',
                                   'Size at least 700 sqft', 'Rooms at least 2', 'Can be moved into by 2026-12-01'])
        self.assertEqual(unjudged(CRITERIA, None), phrases + ['Dogs allowed'])
        self.assertEqual(evaluate(CRITERIA, None), PENDING)
        judged: MustHaves = {phrase: True for phrase in phrases + ['Dogs allowed']}
        self.assertEqual(evaluate(CRITERIA, {'must_haves': judged}), INSIDE)
        self.assertEqual(evaluate(CRITERIA, {'must_haves': {**judged, 'Price at most 2,400 USD a month': False}}), OUTSIDE)
        self.assertEqual(evaluate(CRITERIA, {'must_haves': {**judged, 'Rooms at least 2': None}}), INSIDE)
        open_criteria = criteria(price={'min': 1000, 'max': 2000, 'currency': 'EUR', 'period': 'total'}, size={'min': None, 'max': None},
                                 rooms={'min': 2, 'max': 3}, move_in=None, other=[])
        self.assertEqual(criterion_phrases(open_criteria), ['In one of these areas: Lakeview, Chicago, IL; Logan Square, Chicago, IL',
                                                            'Price from 1,000 to 2,000 EUR in total', 'Rooms from 2 to 3'])

    def test_a_must_have_the_listing_rules_out_puts_it_outside_and_an_unstated_one_does_not(self) -> None:
        self.assertEqual(evaluate(CRITERIA, features(unit(), must_haves={'Dogs allowed': False})), OUTSIDE)
        self.assertEqual(evaluate(CRITERIA, features(unit(), must_haves={'Dogs allowed': None})), INSIDE)

    def test_a_listing_outside_by_its_facts_needs_no_judgment(self) -> None:
        self.assertEqual(evaluate(CRITERIA, features(unit(price=5000), must_haves={})), OUTSIDE)


if __name__ == '__main__':
    _ = unittest.main()
