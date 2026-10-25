import sys
import unittest
from pathlib import Path

import numpy

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'profile/skills/gallery/scripts'), str(ROOT / 'profile/skills/ranking/scripts'), str(ROOT / 'tests')]

from embedding_models import (IMAGE_PARTS, ListingEmbeddings, best_checked, gaussian_process, order, posterior_mode,  # noqa: E402
                              with_similarity)
from scoring import design, fit_design, ranked_order, scores  # noqa: E402
from test_scoring import entry, inputs, neighborhood  # noqa: E402


def blob(values):
    return numpy.asarray(values, dtype='<f4').tobytes()


def labeled_neighborhood():
    listings = neighborhood(40, seed=3)
    for index in (0, 5, 9, 14, 20):
        listings[index]['favorite'] = True
    listings[30]['dismissed'] = True
    return inputs(listings, pairs=[(listings[11]['id'], listings[12]['id'])])


def alike_listings(count=24):
    """Listings the same in every record feature, price and place, so only their embeddings tell them apart. The
    first five, favorites, and 'listing:target' have photos and text near one direction, the others random ones."""
    generator = numpy.random.default_rng(5)
    listings = [entry(f'listing:{number:03}', latitude=41.92, longitude=-87.71, favorite=number < 5) for number in range(count)]
    listings.append(entry('listing:target', latitude=41.92, longitude=-87.71))
    listings.append(entry('listing:varied', latitude=41.92, longitude=-87.71, price=2400))
    direction = generator.normal(size=8)
    image, text = {}, {}
    for listing in listings:
        near = int(listing['id'].removeprefix('listing:')) < 5 if listing['id'][-1].isdigit() else listing['id'] == 'listing:target'
        values = direction + generator.normal(scale=0.3, size=8) if near else generator.normal(size=8)
        image[listing['id']] = {'photos': (3, blob(values)), 'photos:kitchen': (1, blob(values))}
        text[listing['id']] = {'text': (2, blob(values[::-1]))}
    return inputs(listings), image, text


class GaussianProcessTests(unittest.TestCase):
    def test_without_the_embedding_kernel_it_is_the_scoring_model(self):
        data = labeled_neighborhood()
        model_design = design(data)
        model = fit_design(model_design, data, (2.0, 0.1))
        expected = numpy.array(scores(model_design.likelihood_rows, model.fit.weights, model_design.dense_count))
        for process in (gaussian_process(model_design, data, numpy.eye(len(model_design.ids)), 0.0, 2.0, 0.1),
                        gaussian_process(model_design, data, None, 1.0, 2.0, 0.1)):
            self.assertLess(numpy.max(numpy.abs((process.scores - process.scores[0]) - (expected - expected[0]))), 1e-6)
            self.assertEqual(order(model_design, process.scores), [model_design.ids[index] for index in ranked_order(model_design, model.fit.weights)])
            self.assertEqual(sorted(process.held_out), sorted(model.held_out))

    def test_with_no_signal_a_held_out_favorite_is_not_ranked_first_for_lacking_its_label(self):
        listings = neighborhood(60, seed=4)
        for listing in listings[::7]:
            listing['favorite'] = True
        data = inputs(listings)
        model_design = design(data)
        noise = numpy.eye(len(model_design.ids))
        held_out = gaussian_process(model_design, data, noise, 2.0, 1.0, 0.05).held_out
        others = len(model_design.ids) - len(model_design.favorites)
        self.assertGreater(numpy.mean(list(held_out.values())), others / 2.5, held_out)
        self.assertLess(numpy.mean(list(held_out.values())), others * 0.9, held_out)

    def test_the_embedding_kernel_lifts_a_listing_whose_photos_and_text_resemble_the_favorites(self):
        data, image, text = alike_listings()
        model_design = design(data)
        kernels = ListingEmbeddings.of(model_design.ids, image, text).kernels()
        process = gaussian_process(model_design, data, (kernels['photos'] + kernels['text']) / 2, 1.0, 1.0, 0.05)
        ranked = [identifier for identifier in order(model_design, process.scores) if identifier not in model_design.favorites]
        self.assertEqual(ranked[0], 'listing:target')
        self.assertTrue(all(rank <= 3 for rank in process.held_out.values()), process.held_out)

    def test_a_pairwise_answer_ranks_the_chosen_listing_above_the_other(self):
        listings = neighborhood(12, seed=9)
        plain = inputs(listings)
        model_design = design(plain)
        before = order(model_design, gaussian_process(model_design, plain, numpy.eye(12), 1.0, 1.0, 0.05).scores)
        low, high = before[-1], before[0]
        answered = inputs(listings, pairs=[(low, high)])
        answered_design = design(answered)
        after = order(answered_design, gaussian_process(answered_design, answered, numpy.eye(12), 1.0, 1.0, 0.05).scores)
        self.assertLess(after.index(low), after.index(high))

    def test_the_mode_solves_a_single_listing_exactly(self):
        latent = posterior_mode(numpy.array([[1.0]]), numpy.array([0.0]), numpy.array([[1.0]]), numpy.array([1.0]), numpy.array([1.0]))
        self.assertAlmostEqual(latent[0], 1 / (1 + numpy.exp(latent[0])), places=7)

    def test_the_best_checked_fit_has_the_lowest_mean_held_out_rank_and_the_first_wins_a_tie(self):
        class Checked:
            def __init__(self, name, held_out):
                self.name, self.held_out = name, held_out
        fits = [Checked('a', {'x': 3, 'y': 5}), Checked('b', {'x': 2, 'y': 6}), Checked('c', {'x': 1, 'y': 9})]
        self.assertEqual(best_checked(fits).name, 'a')


class SimilarityTests(unittest.TestCase):
    def test_each_listing_gets_its_similarity_to_its_most_similar_other_favorite(self):
        data, image, text = alike_listings()
        model_design = design(data)
        similarities = ListingEmbeddings.of(model_design.ids, image, text).similarities()
        augmented = with_similarity(model_design, similarities)
        names = [column.name for column in augmented.columns]
        self.assertEqual(names[model_design.dense_count:augmented.dense_count], ['similar_favorite:photos', 'similar_favorite:text'])
        favorites = [model_design.ids.index(favorite) for favorite in model_design.favorites]
        for index in range(len(model_design.ids)):
            expected = max(similarities['photos'][index, favorite] for favorite in favorites if favorite != index)
            self.assertAlmostEqual(augmented.raw[index]['similar_favorite:photos'], expected)
        column = augmented.dense_count - 2
        target = model_design.ids.index('listing:target')
        for favorite in model_design.favorites:
            others = [index for index in favorites if model_design.ids[index] != favorite]
            value = max(similarities['photos'][target, other] for other in others)
            mean, scale = augmented.columns[column].mean, augmented.columns[column].scale
            self.assertAlmostEqual(augmented.rows_without[favorite][target].head[column + 1], (value - mean) / scale)
        model = fit_design(augmented, data)
        ranked = [augmented.ids[index] for index in ranked_order(augmented, model.fit.weights) if augmented.ids[index] not in augmented.favorites]
        self.assertEqual(ranked[0], 'listing:target')

    def test_a_room_without_photos_takes_all_photos_and_a_listing_without_embeddings_carries_no_information(self):
        image = {'a': {'photos': (2, blob([1, 0])), 'photos:kitchen': (1, blob([0, 1]))}, 'b': {'photos': (1, blob([0, 2]))},
                 'd': {'photos': (1, blob([1, 1]))}}
        text = {'a': {'text': (1, blob([3, 4]))}, 'b': {'text': (1, blob([4, 3]))}}
        embeddings = ListingEmbeddings.of(('a', 'b', 'c', 'd'), image, text)
        self.assertEqual((embeddings.missing_image, embeddings.missing_text), (1, 2))
        kitchen = IMAGE_PARTS.index('photos:kitchen')
        numpy.testing.assert_allclose(embeddings.image[0, kitchen], [0, 1])
        numpy.testing.assert_allclose(embeddings.image[1, kitchen], [0, 1])
        similarities, kernels = embeddings.similarities(), embeddings.kernels()
        self.assertTrue(numpy.isnan(similarities['photos'][2]).all() and numpy.isnan(similarities['text'][:, 3]).all())
        self.assertAlmostEqual(similarities['text'][0, 1], 0.96)
        for kernel in kernels.values():
            numpy.testing.assert_allclose(numpy.diag(kernel), 1.0)
        numpy.testing.assert_allclose(kernels['photos'][2], [0, 0, 1, 0])
        numpy.testing.assert_allclose(kernels['text'][[2, 3]][:, [0, 1, 3]], [[0, 0, 0], [0, 0, 1]])
        self.assertGreater(kernels['photos'][0, 3], 0)
        numpy.testing.assert_allclose(numpy.linalg.eigvalsh(kernels['photos']) >= -1e-12, True)

    def test_the_held_out_check_recomputes_the_similarity_and_the_nearness_to_the_favorites_without_the_favorite(self):
        listings = neighborhood(20, seed=6)
        for listing in listings[:4]:
            listing['favorite'] = True
        data = inputs(listings)
        model_design = design(data)
        generator = numpy.random.default_rng(2)
        image = {listing['id']: {'photos': (1, blob(generator.normal(size=6)))} for listing in listings}
        text = {listing['id']: {'text': (1, blob(generator.normal(size=6)))} for listing in listings}
        similarity = ListingEmbeddings.of(model_design.ids, image, text).similarities()['photos']
        augmented = with_similarity(model_design, {'photos': similarity})
        nearness, column = augmented.index('near_favorites'), augmented.index('similar_favorite:photos')
        favorites = [augmented.ids.index(favorite) for favorite in augmented.favorites]
        for favorite in augmented.favorites:
            plain = model_design.rows_without[favorite]
            others = [index for index in favorites if augmented.ids[index] != favorite]
            for index, row in enumerate(augmented.rows_without[favorite]):
                self.assertEqual(row.head[nearness + 1], plain[index].head[model_design.index('near_favorites') + 1])
                expected = max(similarity[index, other] for other in others if other != index)
                self.assertAlmostEqual(row.head[column + 1], (expected - augmented.columns[column].mean) / augmented.columns[column].scale)

    def test_a_favorite_without_embeddings_lends_no_similarity(self):
        data, image, text = alike_listings()
        model_design = design(data)
        del image['listing:000'], image['listing:target']
        similarities = ListingEmbeddings.of(model_design.ids, image, text).similarities()
        augmented = with_similarity(model_design, {'photos': similarities['photos']})
        target = model_design.ids.index('listing:target')
        self.assertNotIn('similar_favorite:photos', augmented.raw[target])
        self.assertEqual(augmented.rows[target][augmented.dense_count - 1], 0.0)

    def test_no_embeddings_at_all_is_an_error(self):
        with self.assertRaisesRegex(ValueError, 'embeddings.py embed'):
            ListingEmbeddings.of(('a',), {}, {})


if __name__ == '__main__':
    unittest.main()
