"""Reranks, their review passes and pairwise questions: the scoring model of scoring.py and the review of review.py
on what the listing store holds.

The gallery's Score and rerank and the ranking skill's `ranking.py rerank` both call rerank, so ranks change only
when the user asks for them. Neither calls a model. Each rerank leaves its review pass waiting, which the ranking
review job hands to the agent with pending_review and saves from the job's inbox with save_review."""

import random
import sqlite3
import time
from collections.abc import Collection, Iterable, Mapping
from typing import Protocol

from implicit_signals import diagnostic
from review import Rerank, ReviewResult, ReviewSnapshot, ReviewState, ReviewTask, review_inputs, review_outcome, review_task, validated_review
from scoring import DEFAULT_HYPERPARAMETERS, Pair, Question, RankingInputs, RankingResult, Reason, fit_model, questions, rank_listings

REVIEW_ATTEMPTS = 3


class RerankStore(Protocol):
    """What a rerank, its review pass and the pairwise questions use of ListingStore."""

    def ranking_inputs(self, *, include_signals: bool = ...) -> RankingInputs: ...

    def save_rerank(self, result: RankingResult) -> int: ...

    def rerank(self, rerank_id: int) -> Rerank | None: ...

    def last_rerank(self) -> Rerank | None: ...

    def review_snapshot(self, rerank_id: int) -> ReviewSnapshot | None: ...

    def ranked_listings(self) -> Collection[str]:
        """The listings the latest rerank ranked whose content has not changed since, by id."""
        ...

    def hand_review(self, rerank_id: int) -> int | None: ...

    def fail_review(self, rerank_id: int, error: str) -> None: ...

    def save_review(self, rerank_id: int, ranks: Mapping[str, int], reasons: Mapping[str, Reason], result: ReviewResult, *,
                    connection: sqlite3.Connection | None = ...) -> None: ...

    def skipped_pairs(self) -> Iterable[Pair]: ...


class RerankReport(Rerank):
    """What rerank returns: the saved rerank with the seconds it took."""
    seconds: float


def rerank(store: RerankStore, *, seed: int | None = None) -> RerankReport:
    """Fits the scoring model, writes every rank, reason, explanation and exploration mark in one transaction, and
    returns the summary with each favorite's held-out rank and the waiting review. Apart from the exploration listings,
    which seed draws, the same store gives the same result. Without a seed, one is drawn and recorded in the summary."""
    started = time.perf_counter()
    seed = random.SystemRandom().randrange(2 ** 32) if seed is None else seed
    inputs = store.ranking_inputs(include_signals=True)
    model = fit_model(inputs)
    result = rank_listings(inputs, seed, model=model)
    snapshot: ReviewSnapshot = {**result['check'], 'inputs': review_inputs(inputs, result)}
    result['check'] = snapshot
    result['summary']['implicit_signals'] = diagnostic(inputs, result['summary'], model)
    rerank_id = store.save_rerank(result)
    saved = store.rerank(rerank_id)
    if saved is None:
        raise ValueError(f'Rerank {rerank_id} was not saved.')
    return {**saved, 'seconds': round(time.perf_counter() - started, 3)}


def waiting_review(store: RerankStore) -> tuple[Rerank, ReviewState, ReviewSnapshot] | None:
    """The latest rerank, its review pass and what it stored for the review, while that review waits, otherwise None."""
    last = store.last_rerank()
    if not last or not last['review'] or last['review']['state'] not in ('waiting', 'running'):
        return None
    snapshot = store.review_snapshot(last['id'])
    if snapshot is None:
        return None
    return last, last['review'], snapshot


def pending_review(store: RerankStore, rejection: str | None = None) -> ReviewTask | None:
    """The task of the review the agent should do now, or None. Each call hands the review to the agent once more, and
    a review handed REVIEW_ATTEMPTS times without being saved fails, with rejection, the error of the last review the
    agent wrote, when it was rejected."""
    waiting = waiting_review(store)
    if waiting is None:
        return None
    last, review, snapshot = waiting
    if review['attempts'] >= REVIEW_ATTEMPTS:
        store.fail_review(last['id'], f'The review was not saved in {REVIEW_ATTEMPTS} attempts.'
                                      + (f' The last one was rejected: {rejection}' if rejection else ''))
        return None
    if store.hand_review(last['id']) is None:
        return None
    return review_task(last, snapshot, set(store.ranked_listings()))


def save_review(store: RerankStore, value: object) -> ReviewResult:
    """Checks and applies the agent's review of the latest rerank, and returns its result. The leave-one-out check runs
    on the scores the rerank stored, before and after the moves, and the review is checked and its reasons written
    from what the rerank stored too, so labels, records and listings that change after the rerank leave the review as
    its task described it. Raises ValueError for a review that breaks the review contract or no longer
    waits."""
    waiting = waiting_review(store)
    if waiting is None:
        last = store.last_rerank()
        state = last['review']['state'] if last and last['review'] else None
        raise ValueError(f'No review waits{f": the latest review is {state}" if state else ""}. Stop.')
    last, _, snapshot = waiting
    review = validated_review(value, last, snapshot, set(store.ranked_listings()))
    ranks, reasons, result = review_outcome(snapshot, review)
    store.save_review(last['id'], ranks, reasons, result)
    return result


def pairwise_questions(store: RerankStore, count: int) -> list[Question]:
    """Up to count pairwise questions on the listings with the largest posterior difference variance, fitted with the prior
    scale and weak-negative weight of the latest rerank."""
    last = store.last_rerank()
    hyperparameters = (last['prior_scale'], last['unlabeled_weight']) if last else DEFAULT_HYPERPARAMETERS
    inputs = store.ranking_inputs()
    return questions(inputs, hyperparameters, count, [*inputs['pairs'], *store.skipped_pairs()])
