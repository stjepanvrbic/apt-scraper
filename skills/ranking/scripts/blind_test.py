"""The photo encoder's blind test, which the owner runs once with `ranking.py blind-test`.

It draws at least 30 ranked listings the user has never labeled and that have photo features, records the criterion
and the listings in the store before anything is shown, writes them in random order to a page with their photos and
facts and no rank or score, takes the 10 the user would view, and reports precision at 10 of the scoring model with and
without the encoder's columns against those picks. The encoder is on only when the latest blind test of the shipped
model passed: precision at 10 with the encoder at least 0.5 and above the model without it."""

import html
import json
import random
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import TypedDict

from scoring import RankingInputs, fit_model, in_fit, labeled, scores
from shapes import Listing
from store import ListingStore, without_photo_features

MINIMUM_LISTINGS = 30
PICKS = 10
BAR = 0.5
CRITERION = ('Pre-registered criterion: of the 10 listings you pick as the ones you would view, at least 5 must be among '
             + 'the 10 the model with the photo encoder ranks highest of the {count} shown (precision at 10 of at least 0.5), '
             + 'and more of them than among the 10 the model without the encoder ranks highest. The encoder is turned on '
             + 'only when both hold. Your picks are not labels: they change no rank.')


class BlindTestReport(TypedDict):
    """A blind test's stored result: the model, the picks, each model's top PICKS and their precision."""
    model: str
    shown: int
    labels: int
    picks: list[str]
    top_with_encoder: list[str]
    top_without_encoder: list[str]
    precision_with_encoder: float
    precision_without_encoder: float
    passed: bool


class BlindTestUnavailable(Exception):
    """The store cannot support a blind test yet; the message says why."""


def candidates(store: ListingStore, count: int, rng: random.Random) -> tuple[str, list[str], RankingInputs, int]:
    encoder = store.encoder_state()
    model = encoder['model']
    if not model:
        raise BlindTestUnavailable('This profile ships no photo encoder.')
    if not encoder['quality_passed']:
        raise BlindTestUnavailable('The shipped photo encoder did not pass its quality measurements, so no blind test can turn it on.')
    with_photos = store.ranking_inputs(photos=True)
    photos = with_photos.get('photos')
    if not photos:
        raise BlindTestUnavailable('No listing has photo features yet. The encoder job adds them every 10 minutes.')
    listings = with_photos['listings']
    labels = sum(labeled(listing) for listing in listings)
    if not labels:
        raise BlindTestUnavailable('The blind test compares models fitted on your labels, and there are none yet: '
                                   + 'favorite, dismiss or compare some listings first.')
    pool = [listing['id'] for listing in listings if listing['rankable'] and in_fit(listing) and not labeled(listing)
            and photos['probes'].get(listing['id'])]
    if len(pool) < count:
        raise BlindTestUnavailable(f'{len(pool)} ranked listings without a label have photo features; the blind test needs {count}.')
    chosen = rng.sample(sorted(pool), count)
    return model, chosen, with_photos, labels


def top(inputs: RankingInputs, shown: Iterable[str]) -> list[str]:
    """The ids of the PICKS listings among shown the model fitted to inputs scores highest, ties by id."""
    model = fit_model(inputs)
    values = dict(zip(model.design.ids, scores(model.design.likelihood_rows, model.fit.weights, model.design.dense_count)))
    return sorted(shown, key=lambda identifier: (-values[identifier], identifier))[:PICKS]


def page(listings: Sequence[Listing], path: Path) -> None:
    """The shown listings, numbered in their random order, with photos and facts and no rank or score."""
    parts = ['<!doctype html><meta charset="utf-8"><title>Blind test</title>',
             '<style>body{font-family:sans-serif;max-width:1100px;margin:auto}section{border-top:1px solid #ccc;padding:12px 0}'
             + 'img{height:160px;margin:2px}</style>', '<h1>Blind test</h1>',
             '<p>Pick the 10 listings you would view. Type their numbers in the terminal.</p>']
    for number, listing in enumerate(listings, 1):
        facts = [listing.get('address'), listing.get('rooms'), listing.get('size')]
        price = f'{listing["price"]:,} {listing.get("currency") or ""}'.strip() if listing['price'] is not None else None
        parts.append(f'<section><h2>{number}. {html.escape(listing.get("title") or listing.get("address") or "")}</h2>')
        parts.append('<p>' + html.escape(' · '.join(str(fact) for fact in [price, *facts] if fact)) + '</p>')
        parts.append(''.join(f'<img loading="lazy" src="{html.escape(url, quote=True)}" alt="">' for url in listing.get('photos', [])))
        if listing.get('amenities'):
            parts.append('<p>' + html.escape(', '.join(listing['amenities'])) + '</p>')
        parts.append('<p>' + html.escape(listing.get('description') or '') + '</p></section>')
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text('\n'.join(parts) + '\n', encoding='utf-8')


def parse_picks(text: str, count: int) -> list[int]:
    numbers = [part for part in text.replace(',', ' ').split() if part]
    if len(numbers) != PICKS or not all(number.isdigit() for number in numbers):
        raise ValueError(f'Give exactly {PICKS} listing numbers.')
    picks = [int(number) for number in numbers]
    if len(set(picks)) != PICKS or not all(1 <= number <= count for number in picks):
        raise ValueError(f'Give {PICKS} different numbers from 1 to {count}.')
    return picks


def blind_test(store: ListingStore, page_path: str | Path, *, count: int = MINIMUM_LISTINGS, seed: int | None = None, picks: str | None = None,
               ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> BlindTestReport:
    """Runs the blind test end to end and returns its stored result. picks, when given, are the numbers as typed;
    otherwise ask prompts for them until they are valid."""
    if count < MINIMUM_LISTINGS:
        raise ValueError(f'The blind test shows at least {MINIMUM_LISTINGS} listings.')
    seed = random.SystemRandom().randrange(2 ** 32) if seed is None else seed
    model, shown, with_photos, labels = candidates(store, count, random.Random(seed))
    criterion = CRITERION.format(count=count)
    test = store.begin_blind_test(model, criterion, {'listings': shown, 'seed': seed, 'labels': labels})
    say(criterion)
    say('')
    data = {listing['id']: store.listing(listing['id']) for listing in with_photos['listings'] if listing['id'] in shown}
    page([data[identifier] for identifier in shown], Path(page_path))
    say(f'The {count} listings are numbered in {Path(page_path).resolve()}. Open it in your browser.')
    for number, identifier in enumerate(shown, 1):
        listing = data[identifier]
        say(f'{number:>3}. {listing.get("title") or listing.get("address")}')
    while True:
        try:
            chosen = parse_picks(picks if picks is not None else ask(f'The numbers of the {PICKS} you would view: '), count)
            break
        except ValueError as error:
            if picks is not None:
                raise
            say(str(error))
    picked = [shown[number - 1] for number in chosen]
    with_encoder = top(with_photos, shown)
    baseline: RankingInputs = {**with_photos}
    _ = baseline.pop('photos', None)
    baseline['preference_readings'] = without_photo_features(with_photos['preference_readings'])
    without_encoder = top(baseline, shown)
    precision_with = len(set(with_encoder) & set(picked)) / PICKS
    precision_without = len(set(without_encoder) & set(picked)) / PICKS
    result: BlindTestReport = {'model': model, 'shown': count, 'labels': labels, 'picks': picked,
                               'top_with_encoder': with_encoder, 'top_without_encoder': without_encoder,
                               'precision_with_encoder': precision_with, 'precision_without_encoder': precision_without,
                               'passed': precision_with >= BAR and precision_with > precision_without}
    store.finish_blind_test(test, chosen, result)
    say(json.dumps({key: result[key] for key in ('precision_with_encoder', 'precision_without_encoder', 'passed')}))
    say('The photo encoder is now on.' if result['passed'] else 'The photo encoder stays off.')
    return result
