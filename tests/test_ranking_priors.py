import copy
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/ranking/scripts'))

from test_scoring import inputs, neighborhood  # noqa: E402
from ranking_priors import (AGE_FLOOR, aged, choose_half_life, composite_design, composite_reason, evidence,
                            fit_candidate, prior_variances)  # noqa: E402
from scoring import design, dot, observations  # noqa: E402
from test_rerank import StoreTestCase, fill  # noqa: E402


class GroupedPriorTests(unittest.TestCase):
    def test_a_listing_with_no_shown_composite_components_has_no_composite_value(self):
        problem = inputs(neighborhood(10))
        for name in ('natural_light', 'window_size'):
            problem['listings'][0]['record']['ratings'][name] = 'not shown'
        model = fit_candidate(problem)
        self.assertNotIn('composite:light', model.design.raw[0])
        self.assertIsNone(composite_reason(model, 'listing:000', 'light'))

    def test_composites_and_deviations_preserve_the_stated_prior_score(self):
        problem = inputs(neighborhood(20), readings={('like', 'Finish'): [{'feature': 'rating:kitchen_finish', 'direction': 'like'}]},
                         settings={'preferences': {'likes': ['Finish']}})
        before, after = design(problem), composite_design(problem)
        for original, transformed in zip(before.rows, after.rows):
            self.assertAlmostEqual(dot(original, before.prior_mean), dot(transformed, after.prior_mean))
        self.assertIsNotNone(after.index('composite:finish'))
        variances = prior_variances(after)
        self.assertLess(variances[after.index('rating:kitchen_finish') + 1], variances[after.index('composite:finish') + 1])

    def test_evidence_optimization_never_lowers_the_laplace_evidence(self):
        problem = inputs(neighborhood(30), pairs=[(f'listing:{index:03}', f'listing:{index + 10:03}') for index in range(10)])
        base, candidate = fit_candidate(problem, evidence_steps=0), fit_candidate(problem)
        def score(model):
            return evidence(model.observations, model.fit, *model.prior, model.design.dense_count)
        self.assertGreaterEqual(score(candidate), score(base))
        self.assertTrue(all(1 / value >= 0.01 - 1e-9 for value in candidate.prior[1]))

    def test_nine_labels_keep_defaults_and_half_life_uses_no_future_event(self):
        problem = inputs(neighborhood(20), pairs=[(f'listing:{index:03}', f'listing:{index + 10:03}') for index in range(9)])
        self.assertEqual(fit_candidate(problem).prior, fit_candidate(problem, evidence_steps=0).prior)
        self.assertEqual(choose_half_life([{}] * 9), math.inf)

    def test_age_weights_have_a_floor_and_do_not_age_unlabeled_rows(self):
        problem = inputs(neighborhood(10), pairs=[('listing:000', 'listing:001')])
        problem.update(pair_days=[0], as_of_day=1000)
        data = observations(design(problem), problem, 0.05)
        result = aged(data, problem, 7)
        self.assertEqual(result.weights[-1], AGE_FLOOR)
        self.assertEqual(result.weights[:-1], data.weights[:-1])
        self.assertEqual(aged(data, problem, math.inf).weights, data.weights)

    def test_composite_explanation_names_the_largest_component_deviation(self):
        problem = inputs(neighborhood(10))
        problem = copy.deepcopy(problem)
        record = problem['listings'][0]['record']
        for name in ('kitchen_finish', 'bathroom_finish', 'condition', 'renovation_recency'):
            record['ratings'][name] = {'value': 5 if name == 'kitchen_finish' else 3, 'confidence': 1}
        reason = composite_reason(fit_candidate(problem), 'listing:000', 'finish')
        self.assertEqual(reason, 'finish 3.5 of 5, kitchen finish above the rest')


class LabelClockTests(StoreTestCase):
    def test_snapshots_keep_action_dates_and_unknown_history_is_not_invented(self):
        identifiers = fill(self.store, 4, 0)
        self.store.feedback(identifiers[0], favorite=True)
        self.store.answer_pair(identifiers[1], identifiers[2])
        with self.store.connect(write=True) as connection:
            connection.execute("UPDATE learning_events SET occurred_at = '2026-01-01T00:00:00+00:00' WHERE kind = 'favorite'")
            connection.execute('INSERT INTO feedback (listing_id, favorite) VALUES (?, 1)', (identifiers[3],))
        snapshot = self.store.ranking_inputs()
        self.assertEqual(snapshot['label_days'][identifiers[0]], 20454)
        self.assertNotIn(identifiers[3], snapshot['label_days'])
        self.assertEqual(len(snapshot['pair_days']), 1)
        self.assertLessEqual(snapshot['pair_days'][0], snapshot['as_of_day'])


if __name__ == '__main__':
    unittest.main()
