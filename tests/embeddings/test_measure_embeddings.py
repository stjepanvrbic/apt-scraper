import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'profile/skills/gallery/scripts'), str(ROOT / 'profile/skills/ranking/scripts'), str(ROOT / 'tests'),
                str(Path(__file__).resolve().parent)]

from measure_embeddings import five_favorites, placement, validated_experiment  # noqa: E402
from test_embedding_models import alike_listings  # noqa: E402


class ExperimentTests(unittest.TestCase):
    def test_placement_counts_the_comparisons_with_the_rest_of_similar_price(self):
        ranked = ['favorite', 'alike-1', 'other-1', 'alike-2', 'other-2', 'other-far']
        prices = {'alike-1': 2000, 'alike-2': 2000, 'other-1': 2100, 'other-2': 1900, 'other-far': 3000, 'favorite': 2000}
        result = placement(ranked, ['favorite'], ['alike-1', 'alike-2'], prices)
        self.assertEqual((result['won'], result['compared'], result['above_every_similar'], result['unseen']), (3, 4, 1, 2))
        self.assertEqual((result['unseen_mean_rank'], result['rest_mean_rank']), (3.0, 4.7))

    def test_a_selected_gaussian_process_is_compared_with_the_same_amplitude_before_the_favorites(self):
        data, image, text = alike_listings()
        favorites = [listing['id'] for listing in data['listings'] if listing['favorite']]
        report = five_favorites(data, (image, text), favorites, ['listing:target'])
        for name in ('photos', 'text', 'photos and text'):
            selected = report[f'gaussian process ({name}, selected)']
            self.assertEqual(selected['before'], report[f'gaussian process ({name}, amplitude {selected["relative_amplitude"]})']['before'])
        self.assertEqual(report['gaussian process (photos, selected)']['after']['unseen_ranks'], {'listing:target': 6})

    def test_an_experiment_names_five_distinct_ranked_favorites_and_alike_listings_apart_from_them(self):
        data, _, _ = alike_listings()
        favorites = [f'listing:{number:03}' for number in range(5)]
        self.assertEqual(validated_experiment(data, {'favorites': favorites, 'alike': ['listing:target']}), (favorites, ['listing:target']))
        for experiment, message in (({'favorites': favorites, 'alike': ['zillow:unknown']}, 'zillow:unknown'),
                                    ({'favorites': favorites[:4] + favorites[:1], 'alike': ['listing:target']}, 'five distinct'),
                                    ({'favorites': favorites, 'alike': [favorites[0]]}, 'in both'),
                                    ({'favorites': favorites, 'alike': []}, 'at least one'),
                                    ({'favorites': 'listing:000'}, 'experiment file')):
            with self.subTest(experiment=experiment), self.assertRaisesRegex(ValueError, message):
                validated_experiment(data, experiment)


if __name__ == '__main__':
    unittest.main()
