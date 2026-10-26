"""Reranks, their review passes and pairwise questions: the scoring model of scoring.py and the review of review.py
on what the listing store holds.

The gallery's Score and rerank and the ranking skill's `ranking.py rerank` both call rerank, so ranks change only
when the user asks for them. Neither calls a model. Each rerank leaves its review pass waiting, which the ranking
review job hands to the agent with pending_review and the agent saves with save_review."""

import random
import time

from implicit_signals import diagnostic
from review import citable_values, review_outcome, review_task, review_terms, validated_review
from scoring import DEFAULT_HYPERPARAMETERS, fit_model, questions, rank_listings, stated_phrases

REVIEW_ATTEMPTS = 3


def rerank(store, *, seed=None):
    """Fits the scoring model, writes every rank, reason, explanation and exploration mark in one transaction, and
    returns the summary with each favorite's held-out rank and the waiting review. Apart from the exploration listings,
    which seed draws, the same store gives the same result. Without a seed, one is drawn and recorded in the summary."""
    started = time.perf_counter()
    seed = random.SystemRandom().randrange(2 ** 32) if seed is None else seed
    inputs = store.ranking_inputs(include_signals=True)
    model = fit_model(inputs)
    result = rank_listings(inputs, seed, model=model)
    result['summary']['implicit_signals'] = diagnostic(inputs, result['summary'], model)
    rerank_id = store.save_rerank(result)
    return {**store.rerank(rerank_id), 'seconds': round(time.perf_counter() - started, 3)}


def waiting_review(store):
    """The latest rerank and the scores its review's check reads, while that review waits, otherwise None."""
    last = store.last_rerank()
    if not last or not last['review'] or last['review']['state'] not in ('waiting', 'running'):
        return None
    return last, store.review_snapshot(last['id'])


def pending_review(store):
    """The task of the review the agent should do now, or None. Each call hands the review to the agent once more, and
    a review handed REVIEW_ATTEMPTS times without being saved fails."""
    waiting = waiting_review(store)
    if waiting is None:
        return None
    last, snapshot = waiting
    if last['review']['attempts'] >= REVIEW_ATTEMPTS:
        store.fail_review(last['id'], f'The review was not saved in {REVIEW_ATTEMPTS} attempts.')
        return None
    if store.hand_review(last['id']) is None:
        return None
    return review_task(store.ranking_inputs(), last, snapshot, store.ranked_listings(), store.tags())


def save_review(store, value):
    """Checks and applies the agent's review of the latest rerank, and returns its result. The leave-one-out check runs
    on the scores the rerank stored, before and after the moves, so labels and listings that change after the rerank
    leave the check as the review saw it. Raises ValueError for a review that breaks the review contract or no longer
    waits."""
    waiting = waiting_review(store)
    if waiting is None:
        last = store.last_rerank()
        state = last['review']['state'] if last and last['review'] else None
        raise ValueError(f'No review waits{f": the latest review is {state}" if state else ""}. Stop.')
    last, snapshot = waiting
    inputs, ranked = store.ranking_inputs(), store.ranked_listings()
    records = {listing['id']: listing['record'] for listing in inputs['listings']}
    citable = {identifier: citable_values(entry['explanation'], records[identifier]) for identifier, entry in ranked.items()}
    review = validated_review(value, last['id'], snapshot, citable, review_terms(store.tags(), last['weights']),
                              set(stated_phrases(inputs['settings'])))
    ranks, reasons, result = review_outcome(inputs, snapshot, ranked, review)
    store.save_review(last['id'], ranks, reasons, result)
    return result


def pairwise_questions(store, count):
    """Up to count pairwise questions on the listings whose order the model is least sure of, fitted with the prior
    scale and weak-negative weight of the latest rerank."""
    last = store.last_rerank()
    hyperparameters = (last['prior_scale'], last['unlabeled_weight']) if last else DEFAULT_HYPERPARAMETERS
    inputs = store.ranking_inputs()
    return questions(inputs, hyperparameters, count, [*inputs['pairs'], *store.skipped_pairs()])
