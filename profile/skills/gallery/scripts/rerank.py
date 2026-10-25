"""Reranks and pairwise questions: the scoring model of scoring.py on what the listing store holds.

The gallery's Score and rerank and the ranking skill's `ranking.py rerank` both call rerank, so ranks change only
when the user asks for them. Neither calls a model."""

import random
import time

from scoring import DEFAULT_HYPERPARAMETERS, questions, rank_listings


def rerank(store, *, seed=None):
    """Fits the scoring model, writes every rank, reason, explanation and exploration mark in one transaction, and
    returns the summary with each favorite's held-out rank. Apart from the exploration listings, which seed draws, the
    same store gives the same result. Without a seed, one is drawn and recorded in the summary."""
    started = time.perf_counter()
    seed = random.SystemRandom().randrange(2 ** 32) if seed is None else seed
    result = rank_listings(store.ranking_inputs(), seed)
    stale = store.save_rerank(result)
    return {**result['summary'], 'stale': stale, 'seconds': round(time.perf_counter() - started, 3)}


def pairwise_questions(store, count):
    """Up to count pairwise questions on the listings whose order the model is least sure of, fitted with the prior
    scale and weak-negative weight of the latest rerank."""
    last = store.last_rerank()
    hyperparameters = (last['prior_scale'], last['unlabeled_weight']) if last else DEFAULT_HYPERPARAMETERS
    inputs = store.ranking_inputs()
    return questions(inputs, hyperparameters, count, [*inputs['pairs'], *store.skipped_pairs()])


def comparison(store):
    pairs = pairwise_questions(store, 1)
    question = None
    if pairs:
        question = {'listings': [store.listing(identifier, visible_only=True) for identifier in pairs[0]['listings']]}
    return {'question': question, 'answered': len(store.pairwise_answers())}
