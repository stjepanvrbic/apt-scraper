"""Diagnostic labels from completed gallery visits. These never produce the displayed ranking."""

from collections import defaultdict
from statistics import median
from typing import Literal, TypedDict

from scoring import HeldOutRank, HeldOutSummary, Label, Model, RankingInputs, RankingSummary, fit_model, in_fit, labeled, leave_one_out, summary_of, title


class Diagnostic(TypedDict):
    """The rerank summary's implicit_signals entry: the held-out check with the weak labels, against the ordinary one."""
    policy: Literal['completed_visible_dwell']
    weak_labels: int
    held_out: list[HeldOutRank]
    held_out_median: float | None
    held_out_mean: float | None
    mean_rank_improvement: float | None
    improves: bool | None


def weak_labels(inputs: RankingInputs) -> dict[str, Label]:
    eligible = {listing['id'] for listing in inputs['listings'] if in_fit(listing) and not labeled(listing)}
    durations: defaultdict[str, list[int]] = defaultdict(list)
    for visit in inputs.get('implicit_visits', []):
        if visit['listing_id'] in eligible:
            durations[visit['listing_id']].append(visit['duration_ms'])
    typical = {identifier: median(values) for identifier, values in durations.items()}
    threshold = max(30000, 2 * median(typical.values())) if typical else 30000
    return {identifier: (int(duration >= threshold), 0.1) for identifier, duration in typical.items()
            if duration <= 3000 or duration >= threshold}


def diagnostic(inputs: RankingInputs, baseline: RankingSummary, ordinary_model: Model) -> Diagnostic:
    """The leave-one-out check of the model fitted with the weak labels as well, against baseline, the rerank's summary
    of the ordinary check. ordinary_model is the rerank's fit, which the diagnostic starts from."""
    labels = weak_labels(inputs)
    if labels:
        model = fit_model(inputs, (baseline['prior_scale'], baseline['unlabeled_weight']), weak_labels=labels, warm_start=ordinary_model)
        ranks = leave_one_out(model).ranks
        held_out: list[HeldOutRank] = [{'id': identifier, 'title': title(model.design.listings[identifier]), 'rank': rank}
                                       for identifier, rank in ranks.items()]
        summary = summary_of(ranks)
    else:
        held_out = baseline['held_out']
        summary: HeldOutSummary = {'median': baseline['held_out_median'], 'mean': baseline['held_out_mean']}
    baseline_mean, mean = baseline['held_out_mean'], summary['mean']
    improvement = baseline_mean - mean if baseline_mean is not None and mean is not None else None
    return {'policy': 'completed_visible_dwell', 'weak_labels': len(labels), 'held_out': held_out,
            'held_out_median': summary['median'], 'held_out_mean': summary['mean'],
            'mean_rank_improvement': improvement, 'improves': improvement > 0 if improvement is not None else None}
