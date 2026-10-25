"""Measures the embedding models of embedding_models.py against the scoring model alone, on a copy of a listing store.

Usage:
  measure_embeddings.py STORE WORK [--experiment FILE] [--models DIR] [--threads N]

STORE is only read. The first run copies it to WORK/listings.sqlite3 and embeds the copy's listings there, downloading
the photos their feature records name, so a later run with the same WORK embeds nothing again. The report, printed
as JSON, has:

- costs: per listing the seconds to download its photos, to embed them and its text, and the CPU seconds of
  embedding; the photos per second of the image model alone; the seconds to load the models; the peak memory after embedding and at the end;
  the size of this Python environment and of the models; and how often CLIP's zero-shot room type agrees with the
  record's.
- labels: each model's leave-one-out check on the copy's own labels, with the seconds its fit and check took. Every
  model is checked the same way, by gaussian_process: the refit labels the held-out favorite as a weak negative, as
  any unlabeled ranked listing. The scoring model and the similarity terms are the Gaussian-process model without an
  embedding kernel, at the prior scale and weak-negative weight their own tuning chose. The scoring model also
  reports the rerank's own check, which leaves the held-out favorite's row out, as rerank_check.
- five_favorites, with --experiment: FILE holds {"favorites": [five ids], "alike": [ids]}, the unseen listings the
  tester judges alike to the five. The copy's favorites are replaced by the five, the other labels kept, and each
  model is fitted before and after. For each unseen alike listing, the rest of similar price are the ranked listings
  neither alike nor favorites within 10% of its monthly price, and the report counts its comparisons won against
  them, the unseen listings above all of them, and the mean ranks.

The models: the scoring model, the scoring model with a similarity term for the most similar favorite in photos,
text or both, and the Gaussian-process model with an embedding kernel of photos, text or both at each relative
amplitude of RELATIVE_AMPLITUDES and at the one its leave-one-out check selects. The selected row's check is the
one it was selected by, so it is optimistic by the choice among three. Ranks are the models' own order,
without exploration listings.

Exit codes: 0 done, 1 a step failed, 2 usage error.
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from statistics import fmean, median

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'gallery' / 'scripts'))

from embedding_models import RELATIVE_AMPLITUDES, ListingEmbeddings, best_checked, gaussian_process, order, with_similarity  # noqa: E402
from embeddings import IMAGE_MODEL, TEXT_MODEL, Encoders, download_models, embed_store, models_directory  # noqa: E402
from scoring import design, fit_design, ranked_order  # noqa: E402
from store import ListingStore  # noqa: E402

SIMILAR_PRICE = 0.1
EMBEDDINGS = {'photos': ('photos',), 'text': ('text',), 'photos and text': ('photos', 'text')}


def peak_memory_bytes():
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('page_faults', wintypes.DWORD), ('peak_working_set', ctypes.c_size_t),
                        *((name, ctypes.c_size_t) for name in ('working_set', 'peak_paged_pool', 'paged_pool', 'peak_nonpaged_pool',
                                                               'nonpaged_pool', 'pagefile', 'peak_pagefile'))]

        counters = Counters(cb=ctypes.sizeof(Counters))
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = wintypes.HANDLE
        query = ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
        if not query(process(), ctypes.byref(counters), counters.cb):
            raise OSError('GetProcessMemoryInfo failed.')
        return counters.peak_working_set
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def directory_bytes(path):
    return sum(file.stat().st_size for file in Path(path).rglob('*') if file.is_file())


def megabytes(count):
    return round(count / 2 ** 20, 1)


def working_copy(store, work):
    """The store's copy in work, made with SQLite's backup from a read-only connection when work has none."""
    copy = Path(work) / 'listings.sqlite3'
    if not copy.exists():
        copy.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(f'{Path(store).resolve().as_uri()}?mode=ro', uri=True) as source, sqlite3.connect(copy) as target:
            source.backup(target)
    return ListingStore(copy)


def checked(order_ids, held_out, started, **details):
    values = list(held_out.values())
    return {'order': order_ids, 'held_out': held_out, 'held_out_median': median(values) if values else None,
            'held_out_mean': round(fmean(values), 2) if values else None, 'seconds': round(time.perf_counter() - started, 3), **details}


def compare(inputs, embeddings_by_model):
    """Each model's ranked order and leave-one-out check on the inputs, and the seconds each took."""
    model_design = design(inputs)
    embeddings = ListingEmbeddings.of(model_design.ids, *embeddings_by_model)
    similarities, kernels = embeddings.similarities(), embeddings.kernels()
    results = {}
    started = time.perf_counter()
    scoring = fit_design(model_design, inputs)
    rerank_check = list(scoring.held_out.values())
    results['scoring'] = checked([model_design.ids[index] for index in ranked_order(model_design, scoring.fit.weights)],
                                 gaussian_process(model_design, inputs, None, 0.0, scoring.scale, scoring.unlabeled_weight).held_out, started,
                                 prior_scale=scoring.scale, unlabeled_weight=scoring.unlabeled_weight,
                                 rerank_check={'held_out': scoring.held_out, 'median': median(rerank_check) if rerank_check else None,
                                               'mean': round(fmean(rerank_check), 2) if rerank_check else None})
    for name, parts in EMBEDDINGS.items():
        started = time.perf_counter()
        augmented = with_similarity(model_design, {part: similarities[part] for part in parts})
        model = fit_design(augmented, inputs)
        weights = {column.name: round(weight, 3) for column, weight in zip(augmented.columns, model.fit.weights[1:])
                   if column.name.startswith('similar_favorite:')}
        results[f'scoring + similar favorite ({name})'] = checked(
            [augmented.ids[index] for index in ranked_order(augmented, model.fit.weights)],
            gaussian_process(augmented, inputs, None, 0.0, model.scale, model.unlabeled_weight).held_out, started,
            prior_scale=model.scale, unlabeled_weight=model.unlabeled_weight, similarity_weights=weights)
    for name, parts in EMBEDDINGS.items():
        kernel = sum(kernels[part] for part in parts) / len(parts)
        fits = []
        for amplitude in RELATIVE_AMPLITUDES:
            started = time.perf_counter()
            process = gaussian_process(model_design, inputs, kernel, amplitude, scoring.scale, scoring.unlabeled_weight)
            fits.append(process)
            results[f'gaussian process ({name}, amplitude {amplitude})'] = checked(order(model_design, process.scores),
                                                                                   process.held_out, started)
        best = best_checked(fits)
        results[f'gaussian process ({name}, selected)'] = {**results[f'gaussian process ({name}, amplitude {best.relative_amplitude})'],
                                                           'relative_amplitude': best.relative_amplitude}
    return results, embeddings


def with_favorites(inputs, favorites):
    return {**inputs, 'listings': [{**listing, 'favorite': listing['id'] in favorites} for listing in inputs['listings']]}


def placement(ranked, favorites, alike, prices):
    """Where the unseen alike listings rank against the rest of similar price."""
    rank = {identifier: position for position, identifier in enumerate(ranked, 1)}
    unseen = [identifier for identifier in alike if identifier in rank]
    rest = [identifier for identifier in ranked if identifier not in alike and identifier not in favorites]
    won = compared = above_every = 0
    for identifier in unseen:
        price = prices.get(identifier)
        similar = [other for other in rest if price and prices.get(other) and abs(prices[other] - price) <= SIMILAR_PRICE * price]
        wins = sum(rank[identifier] < rank[other] for other in similar)
        won, compared, above_every = won + wins, compared + len(similar), above_every + (wins == len(similar))
    return {'ranked': len(ranked), 'won': won, 'compared': compared, 'above_every_similar': above_every, 'unseen': len(unseen),
            'unseen_mean_rank': round(fmean(rank[identifier] for identifier in unseen), 1) if unseen else None,
            'rest_mean_rank': round(fmean(rank[identifier] for identifier in rest), 1) if rest else None,
            'unseen_ranks': {identifier: rank[identifier] for identifier in unseen},
            'favorite_ranks': {identifier: rank.get(identifier) for identifier in favorites}}


def five_favorites(inputs, embeddings_by_model, favorites, alike):
    """Each model before and after the five favorites. A selected Gaussian process is compared with the same
    amplitude before, since without favorites there is nothing to select by."""
    prices = {listing['id']: listing['code'].get('monthly_price') for listing in inputs['listings']}
    before, _ = compare(with_favorites(inputs, ()), embeddings_by_model)
    after, _ = compare(with_favorites(inputs, favorites), embeddings_by_model)
    report = {}
    for name, result in after.items():
        same = name.replace('selected', f'amplitude {result["relative_amplitude"]}') if 'relative_amplitude' in result else name
        report[name] = {'before': placement(before[same]['order'], favorites, alike, prices), 'after': placement(result['order'], favorites, alike, prices),
                        **{key: value for key, value in result.items() if key != 'order'}}
    return report


def validated_experiment(inputs, experiment):
    """The experiment's favorites and alike listings, each a ranked listing of the store, five distinct favorites and
    no listing in both."""
    if not isinstance(experiment, dict) or not isinstance(experiment.get('favorites'), list) or not isinstance(experiment.get('alike'), list):
        raise ValueError('The experiment file is {"favorites": [five listing ids], "alike": [listing ids]}.')
    favorites, alike = experiment['favorites'], experiment['alike']
    model_design = design(inputs)
    ranked = {identifier for identifier in model_design.ids if model_design.listings[identifier]['rankable']}
    unknown = [identifier for identifier in (*favorites, *alike) if identifier not in ranked]
    if unknown:
        raise ValueError(f'Not ranked listings of the store: {", ".join(map(str, unknown))}.')
    if len(set(favorites)) != 5 or len(set(alike)) != len(alike) or not alike or set(favorites) & set(alike):
        raise ValueError('The experiment needs five distinct favorites and at least one alike listing, none of them repeated or in both.')
    return favorites, alike


def embedding_costs(report, load_seconds):
    embedded = [entry for entry in report if 'error' not in entry]
    photos = sum(entry['photos'] for entry in embedded)
    per_listing = {key: round(fmean(entry[key] for entry in embedded), 3) if embedded else None
                   for key in ('download_seconds', 'embed_seconds', 'embed_cpu_seconds', 'photos')}
    seconds = sum(entry.get('image_seconds', 0) for entry in embedded)
    return {'listings_embedded': len(embedded), 'failed': [entry for entry in report if 'error' in entry], 'photos': photos,
            'per_listing': per_listing, 'photos_per_second': round(photos / seconds, 2) if seconds else None,
            'model_load_seconds': round(load_seconds, 2),
            'room_type_agreement': round(sum(entry.get('room_types_agreeing', 0) for entry in embedded) / photos, 3) if photos else None}


def measure(store, work, models, experiment, threads):
    import torch

    if threads:
        torch.set_num_threads(threads)
    copy = working_copy(store, work)
    if experiment:
        validated_experiment(copy.ranking_inputs(), experiment)
    download_models(models)
    started = time.perf_counter()
    encoders = Encoders(models)
    load_seconds = time.perf_counter() - started
    costs = embedding_costs(embed_store(copy, encoders), load_seconds)
    costs['threads'] = torch.get_num_threads()
    costs['peak_memory_after_embedding_mb'] = megabytes(peak_memory_bytes())
    del encoders
    inputs = copy.ranking_inputs()
    embeddings_by_model = (copy.embeddings(IMAGE_MODEL.name), copy.embeddings(TEXT_MODEL.name))
    labels, embeddings = compare(inputs, embeddings_by_model)
    report = {'costs': costs, 'labels': {name: {key: value for key, value in result.items() if key != 'order'} for name, result in labels.items()},
              'listings': {'ranked': len(labels['scoring']['order']), 'without_photo_embeddings': embeddings.missing_image,
                           'without_text_embeddings': embeddings.missing_text}}
    if experiment:
        report['five_favorites'] = five_favorites(inputs, embeddings_by_model, *validated_experiment(inputs, experiment))
    costs['peak_memory_mb'] = megabytes(peak_memory_bytes())
    costs['environment_mb'] = megabytes(directory_bytes(sys.prefix))
    costs['models_mb'] = megabytes(directory_bytes(models))
    return report


def main(argv):
    parser = argparse.ArgumentParser(description='Measure the embedding models against the scoring model on a copy of a listing store.')
    parser.add_argument('store')
    parser.add_argument('work')
    parser.add_argument('--experiment', type=Path, help='JSON file with "favorites", five listing ids, and "alike", the unseen alike ids.')
    parser.add_argument('--models', type=Path, help='Directory of the models; HERMES_HOME/workspace/embeddings/models by default.')
    parser.add_argument('--threads', type=int, help="PyTorch's CPU threads; its default by default.")
    arguments = parser.parse_args(argv)
    try:
        experiment = json.loads(arguments.experiment.read_text(encoding='utf-8')) if arguments.experiment else None
        models = arguments.models or models_directory()
        print(json.dumps(measure(arguments.store, arguments.work, models, experiment, arguments.threads), ensure_ascii=False, indent=1))
        return 0
    except (ValueError, KeyError, OSError, sqlite3.Error, ArithmeticError) as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
