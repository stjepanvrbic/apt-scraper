"""Diagnostic labels from completed gallery visits. These never produce the displayed ranking."""

from collections import defaultdict
from statistics import median

from scoring import fit_model, in_fit, labeled, summary_of, title


def weak_labels(inputs):
    eligible = {listing['id'] for listing in inputs['listings'] if in_fit(listing) and not labeled(listing)}
    durations = defaultdict(list)
    for visit in inputs['implicit_visits']:
        if visit['listing_id'] in eligible:
            durations[visit['listing_id']].append(visit['duration_ms'])
    typical = {identifier: median(values) for identifier, values in durations.items()}
    threshold = max(30000, 2 * median(typical.values())) if typical else 30000
    return {identifier: (int(duration >= threshold), 0.1) for identifier, duration in typical.items()
            if duration <= 3000 or duration >= threshold}


def diagnostic(inputs, baseline, ordinary_model):
    labels = weak_labels(inputs)
    if labels:
        model = fit_model(inputs, (baseline['prior_scale'], baseline['unlabeled_weight']), weak_labels=labels, warm_start=ordinary_model)
        held_out = [{'id': identifier, 'title': title(model.design.listings[identifier]), 'rank': rank}
                    for identifier, rank in model.held_out.items()]
        summary = {f'held_out_{key}': value for key, value in summary_of(model.held_out).items()}
    else:
        held_out = baseline['held_out']
        summary = {key: baseline[key] for key in ('held_out_mean', 'held_out_median')}
    improvement = baseline['held_out_mean'] - summary['held_out_mean'] if baseline['held_out_mean'] is not None else None
    return {'policy': 'completed_visible_dwell', 'weak_labels': len(labels), 'held_out': held_out, **summary,
            'mean_rank_improvement': improvement, 'improves': improvement > 0 if improvement is not None else None}
