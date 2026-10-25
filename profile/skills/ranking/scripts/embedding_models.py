"""Ranking models over the listings' embeddings, to measure against the scoring model alone.

Both extend the scoring model of scoring.py and fit on its labels, weights, prior and leave-one-out check:

- The similarity terms: two more columns of the scoring model's design, each listing's cosine similarity to its most
  similar favorite other than itself, in CLIP photo embeddings and in sentence embeddings of its text.
- A Gaussian-process preference model: a latent score per listing with the scoring model's likelihood. Its prior is
  the scoring model's prior carried into function space, a linear kernel over the design with the stated preferences'
  prior means as its mean function, plus a squared-exponential kernel over the embeddings. Without the embedding
  kernel it is the scoring model. Newton iterations on the latent scores find the posterior mode, with the likelihood
  on linear combinations of the latent scores so that a pairwise answer is a term on the two listings' difference.
"""

from dataclasses import dataclass, fields
from functools import cached_property
from statistics import fmean, pstdev

import numpy

from scoring import Column, Design, Row, expand, observations, prior

IMAGE_PARTS = ('photos', 'photos:kitchen', 'photos:bathroom', 'photos:bedroom', 'photos:living', 'photos:exterior')
RELATIVE_AMPLITUDES = (0.5, 1.0, 2.0)
NEWTON_TOLERANCE = 1e-8
NEWTON_ITERATIONS = 100


def unit_rows(matrix):
    norms = numpy.linalg.norm(matrix, axis=-1, keepdims=True)
    return matrix / numpy.where(norms == 0, 1, norms)


def vector(blob):
    return numpy.frombuffer(blob, dtype='<f4').astype(float)


@dataclass(frozen=True)
class ListingEmbeddings:
    """The unit embeddings of a design's listings, in its order: image[i, r] listing i's mean photo vector for
    IMAGE_PARTS[r], where a room type without photos takes the mean over all its photos, and text[i] its text vector.
    A listing without embeddings takes the mean of the others, so it is as near to every listing as the average."""
    image: numpy.ndarray
    text: numpy.ndarray
    missing_image: int
    missing_text: int

    @classmethod
    def of(cls, ids, image_parts, text_parts):
        """From the store's embeddings by listing id, ListingStore.embeddings of the image and the text model."""
        if not any(identifier in image_parts for identifier in ids) or not any(identifier in text_parts for identifier in ids):
            raise ValueError('No listing has embeddings. Run embeddings.py embed first.')
        image = [[vector(parts.get(part, parts['photos'])[1]) for part in IMAGE_PARTS] if (parts := image_parts.get(identifier)) else None
                 for identifier in ids]
        text = [vector(text_parts[identifier]['text'][1]) if identifier in text_parts else None for identifier in ids]
        return cls(filled(image), filled(text), sum(row is None for row in image), sum(row is None for row in text))

    def similarities(self):
        """The cosine similarity of every pair of listings, averaged over IMAGE_PARTS for photos."""
        return {'photos': numpy.einsum('irk,jrk->ij', self.image, self.image) / len(IMAGE_PARTS), 'text': self.text @ self.text.T}

    def kernels(self):
        """Squared-exponential kernels exp(-d² / 2l²) of the unit vectors, l² the median squared distance between two
        listings, averaged over IMAGE_PARTS for photos."""
        image = numpy.mean([squared_exponential(self.image[:, part]) for part in range(len(IMAGE_PARTS))], axis=0)
        return {'photos': image, 'text': squared_exponential(self.text)}


def filled(rows):
    present = unit_rows(numpy.array([row for row in rows if row is not None]))
    average = unit_rows(present.mean(axis=0))
    return unit_rows(numpy.array([average if row is None else unit_rows(numpy.asarray(row)) for row in rows]))


def squared_exponential(vectors):
    squared = numpy.clip(2 - 2 * vectors @ vectors.T, 0, None)
    pairs = squared[numpy.triu_indices(len(vectors), 1)]
    length = numpy.median(pairs) if pairs.size and numpy.median(pairs) > 0 else 1.0
    return numpy.exp(-squared / (2 * length))


def most_similar(similarity, index, favorites):
    values = [float(similarity[index, favorite]) for favorite in favorites if favorite != index]
    return max(values) if values else None


@dataclass(frozen=True)
class SimilarityDesign(Design):
    """The scoring model's design with similarity columns: for each, the listing's similarity to its most similar
    favorite other than itself. The leave-one-out check computes it without the favorite left out, as it does the
    nearness to the favorites. similarity holds each such column's position and its similarity matrix."""
    similarity: tuple = ()

    @cached_property
    def rows_without(self):
        rows_without = Design.rows_without.func(self)
        position = {identifier: index for index, identifier in enumerate(self.ids)}
        for favorite in self.favorites:
            others = [position[other] for other in self.favorites if other != favorite]
            rows = list(rows_without[favorite])
            for index, row in enumerate(rows):
                head = list(row.head)
                for column, similarity in self.similarity:
                    value = most_similar(similarity, index, others)
                    head[column + 1] = 0.0 if value is None else (value - self.columns[column].mean) / self.columns[column].scale
                rows[index] = Row(head, row.positions, row.values)
            rows_without[favorite] = rows
        return rows_without


def with_similarity(model_design, similarities):
    """The design with a column 'similar_favorite:<name>' for each similarity matrix in the design's order, numeric
    like near_favorites, with prior mean zero, and left out when it has one value across the listings."""
    favorites = [model_design.ids.index(favorite) for favorite in model_design.favorites]
    columns, prior_mean = list(model_design.columns), list(model_design.prior_mean)
    rows, raw = [list(row) for row in model_design.rows], [dict(values) for values in model_design.raw]
    heads = [list(row.head) for row in model_design.likelihood_rows]
    added = []
    for name, similarity in similarities.items():
        values = [most_similar(similarity, index, favorites) for index in range(len(model_design.ids))]
        known = [value for value in values if value is not None]
        if len(known) < 2 or pstdev(known) == 0:
            continue
        column, name = model_design.dense_count + len(added), f'similar_favorite:{name}'
        columns.insert(column, Column(name, True, False, fmean(known), 2 * pstdev(known)))
        prior_mean.insert(column, 0.0)
        for index, value in enumerate(values):
            scaled = 0.0 if value is None else (value - columns[column].mean) / columns[column].scale
            rows[index].insert(column, scaled)
            heads[index].insert(column + 1, scaled)
            if value is not None:
                raw[index][name] = value
        added.append((column, similarity))
    base = {field.name: getattr(model_design, field.name) for field in fields(Design)}
    return SimilarityDesign(**{**base, 'columns': tuple(columns), 'dense_count': model_design.dense_count + len(added), 'rows': tuple(rows),
                               'likelihood_rows': tuple(Row(head, row.positions, row.values) for head, row in zip(heads, model_design.likelihood_rows)),
                               'raw': tuple(raw), 'prior_mean': tuple(prior_mean)}, similarity=tuple(added))


def observation_matrix(model_design, data):
    """Row k of the likelihood as a combination of the listings' latent scores: one listing's, or a pair's difference."""
    position = {identifier: index for index, identifier in enumerate(model_design.ids)}
    matrix = numpy.zeros((len(data.rows), len(model_design.ids)))
    for row, (listing, pair) in enumerate(zip(data.listings, data.pairs)):
        if listing is not None:
            matrix[row, position[listing]] = 1.0
        else:
            matrix[row, position[pair[0]]] += 1.0
            matrix[row, position[pair[1]]] -= 1.0
    return matrix


def sigmoid(values):
    return 0.5 * (1 + numpy.tanh(values / 2))


def posterior_mode(kernel, mean, matrix, labels, weights):
    """The latent scores at the posterior mode: f = mean + K a, minimizing the weighted logistic loss of matrix f plus
    ½ aᵀKa. Each Newton step solves through B = I + S K Sᵀ, S the rows of matrix scaled by the root of their curvature,
    which is positive definite whatever the rank of K, with step halving."""
    alpha, latent = numpy.zeros(len(mean)), mean.copy()

    def objective(alpha, latent):
        values = matrix @ latent
        return weights @ (numpy.logaddexp(0, values) - labels * values) + alpha @ (kernel @ alpha) / 2

    current = objective(alpha, latent)
    for _ in range(NEWTON_ITERATIONS):
        probabilities = sigmoid(matrix @ latent)
        gradient = matrix.T @ (weights * (labels - probabilities))
        scaled = numpy.sqrt(weights * probabilities * (1 - probabilities))[:, None] * matrix
        target = scaled.T @ (scaled @ (latent - mean)) + gradient
        inner = numpy.eye(len(scaled)) + scaled @ kernel @ scaled.T
        step = target - scaled.T @ numpy.linalg.solve(inner, scaled @ (kernel @ target)) - alpha
        length = 1.0
        while True:
            candidate = alpha + length * step
            candidate_latent = mean + kernel @ candidate
            value = objective(candidate, candidate_latent)
            if value <= current or length < 1e-10:
                break
            length /= 2
        change = numpy.max(numpy.abs(candidate_latent - latent))
        alpha, latent, current = candidate, candidate_latent, value
        if change < NEWTON_TOLERANCE:
            return latent
    raise ArithmeticError('The Gaussian-process fit did not converge.')


def held_out_rank(model_design, scores, favorite):
    """One plus the number of ranked listings that are not favorites and score above the favorite."""
    favorites = set(model_design.favorites)
    own = scores[model_design.ids.index(favorite)]
    return 1 + sum(bool(score > own) for identifier, score in zip(model_design.ids, scores)
                   if identifier not in favorites and model_design.listings[identifier]['rankable'])


@dataclass(frozen=True)
class GaussianProcess:
    scores: numpy.ndarray
    held_out: dict
    relative_amplitude: float


def gaussian_process(model_design, inputs, embedding_kernel, relative_amplitude, scale, unlabeled_weight):
    """The Gaussian-process model fitted with the given prior scale and weak-negative weight, and each favorite's
    held-out rank. The embedding kernel's variance is relative_amplitude² times the mean prior variance of the linear
    part's scores about their mean, so at 1 the embeddings and the structured features have the same prior weight;
    at 0, or without a kernel, the model is the scoring model of the design.

    The refit for a held-out favorite drops its pairwise answers and labels it as any ranked listing without a label
    is labeled, a weak negative. Dropping its row instead, as the scoring model's own check does, lets a kernel that
    gives each listing room of its own leave the held-out favorite at the prior mean while every other unlabeled
    listing is pulled below it, which ranks it first with no signal at all."""
    mean_weights, precision = (numpy.array(values) for values in prior(model_design, scale))
    variance = 1 / precision
    data = observations(model_design, inputs, unlabeled_weight)
    matrix, labels, weights = observation_matrix(model_design, data), numpy.array(data.labels, float), numpy.array(data.weights)
    size = len(variance)
    features = numpy.array([expand(row, size) for row in model_design.likelihood_rows])
    centered = features[:, 1:] - features[:, 1:].mean(axis=0)
    embedding = 0.0 if embedding_kernel is None else relative_amplitude ** 2 * float(numpy.mean(centered ** 2 @ variance[1:])) * embedding_kernel

    def fit(rows, row_labels, row_weights):
        basis = features if rows is model_design.likelihood_rows else numpy.array([expand(row, size) for row in rows])
        return posterior_mode(basis @ (basis * variance).T + embedding, basis @ mean_weights, matrix, row_labels, row_weights)

    scores = fit(model_design.likelihood_rows, labels, weights)
    held_out = {}
    for favorite in model_design.favorites:
        unlabeled = unlabeled_weight if model_design.listings[favorite]['rankable'] else 0.0
        reduced_labels = numpy.array([0.0 if listing == favorite else label for label, listing in zip(labels, data.listings)])
        reduced_weights = numpy.array([unlabeled if listing == favorite else 0.0 if pair and favorite in pair else weight
                                       for weight, listing, pair in zip(data.weights, data.listings, data.pairs)])
        held_out[favorite] = held_out_rank(model_design, fit(model_design.rows_without[favorite], reduced_labels, reduced_weights), favorite)
    return GaussianProcess(scores, held_out, relative_amplitude)


def best_checked(fits):
    """The fit whose favorites have the best mean held-out rank, the first on a tie."""
    return min(fits, key=lambda fit: fmean(fit.held_out.values()) if fit.held_out else 0.0)


def order(model_design, scores):
    """The ranked listings' ids, best first, the listing id deciding ties."""
    return [model_design.ids[index] for index in sorted((index for index, identifier in enumerate(model_design.ids)
                                                         if model_design.listings[identifier]['rankable']),
                                                        key=lambda index: (-scores[index], model_design.ids[index]))]
