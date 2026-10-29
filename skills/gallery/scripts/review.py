"""The review pass: a model's text-only review of a rerank's top listings, checked and applied in code.

The review reads a task that review_task builds from the rerank: the top REVIEWED listings with what their feature
records state and what moved their scores, the stated preferences, the note readings and the state of the whole
ranking. It sees no label: favorites are not marked, held-out ranks are given without their listings, nearness to the
favorites is left out, and the favorite part of each reason is added by code after the review, so the favorites test
its moves independently.

The review may rewrite the reasons, flag listings whose rank conflicts with a stated preference, report what looks
wrong in the ranking state, and move listings, each move with a reason and a verbalized confidence. A rewritten reason
cites the listing's record in square brackets, each citation exactly one of the values its record states, and outside
the brackets uses only CONNECTING_WORDS, so every feature it names is one the record has. A move's reason cites the
same way and a finding may, each citation a value of the task's listings, a tag or a feature's column name, and outside
the brackets they use only CONNECTING_WORDS and MODEL_WORDS. A flag is a listing and a stated preference, without
text. The review is written after reading listing text, so it holds no free text that could carry an instruction.

Moves become per-listing score adjustments that place each moved listing at its new rank, so they apply to the full
fit and to every held-out fit alike. The leave-one-out check reads the scores the rerank stored and runs before and
after the moves, and when the favorites' mean held-out rank gets worse, or there is no favorite to check against,
every move is reverted. Language-model rankers show position and popularity bias, so a move stands only when the check
confirms it.

Everything here is a pure function of its inputs.
"""

import json
import re
from collections import Counter

from scoring import compose_reason, expressed, feature_reason, prior_means, stated_phrases, summary_of

REVIEWED = 50
TEXT_LIMIT = 300
FINDINGS_LIMIT = 10
CONTRIBUTIONS_SHOWN = 12
WEIGHTS_SHOWN = 20
HIDDEN_FEATURES = ('near_favorites',)
REVIEW_FIELDS = ('rerank', 'reasons', 'moves', 'flags', 'findings')
CITATION = re.compile(r'\[([^\[\]]+)\]')
TOKEN = re.compile(r"[^\W_]+(?:'[^\W_]+)*|\S")
PUNCTUATION = frozenset('.,;:()-')
CONNECTING_WORDS = frozenset('''
    a all also although an and apartment are as at be because best better both brings but by clear clearly decent
    despite drawback drawbacks each even fair fine for from gives good great has have here high higher home in is it its
    just key keep keeps least less listing low lower main makes many mixed more most much no nor not of offers on one
    only or other outweigh outweighs over overall place plus poor quite rather real side so solid some still strong than
    that the then there these this those though to too top two under unit upside upsides very weak well while with
    without worse worst worth yet
'''.split())
MODEL_WORDS = frozenset('''
    above against below count counts dislike dislikes extraction favorites feature features held label labels like
    likes little listings mean means model out overrules poorly preference rank ranked ranks rests run runs same should
    single stated tag tags thing user wait waiting weighs weight weights when wrong
'''.split())
REVIEW_WORDS = CONNECTING_WORDS | MODEL_WORDS


def citable_values(explanation, record):
    """What a reason may cite for a listing: each value its explanation states, and the traits and concerns of its
    record."""
    values = [part['value'] for part in shown_contributions(explanation) if part['value'] is not None]
    if record is not None:
        values += [*record['traits'], *record['concerns']]
    return list(dict.fromkeys(values))


def checked_text(text, values, words=CONNECTING_WORDS, *, source='the record', require_citation=True):
    """The text as the user sees it, with its citations unbracketed, or a ValueError saying why it is rejected: no
    citation when one is required, a citation that is not one of values, which source states, or outside the
    citations a word that is not one of words, a number, or a sign other than plain punctuation."""
    cited = CITATION.findall(text)
    if require_citation and not cited:
        raise ValueError('it cites nothing')
    unknown = [citation for citation in cited if citation not in values]
    if unknown:
        raise ValueError(f'{source} does not state {", ".join(json.dumps(citation, ensure_ascii=False) for citation in unknown)}')
    rest = CITATION.sub(' ', text).lower()
    if '[' in rest or ']' in rest:
        raise ValueError('it has an unmatched bracket')
    uncited = sorted({token for token in TOKEN.findall(rest) if token not in words and token not in PUNCTUATION})
    if uncited:
        raise ValueError(f'it uses {", ".join(json.dumps(word, ensure_ascii=False) for word in uncited)} outside a citation')
    return ' '.join(CITATION.sub(lambda match: match.group(1), text).split())


def tag_counts(inputs):
    """Every tag of the inputs' tag list with the number of listings whose current record uses it, most used first."""
    used = Counter(tag for listing in inputs['listings'] if listing['record'] for tag in {*listing['record']['traits'], *listing['record']['concerns']})
    return dict(sorted(((tag, used[tag]) for tag in inputs['tags']), key=lambda item: (-item[1], item[0])))


def record_digest(record):
    if record is None:
        return None
    return {'summary': record['summary'], 'traits': record['traits'], 'concerns': record['concerns']}


def shown_contributions(explanation):
    return [part for part in explanation['contributions'] if part['feature'] not in HIDDEN_FEATURES]


def reviewed_listing(listing, entry):
    """What the review of a rerank reads about one of its top listings, from the rerank's inputs, listing, and its
    result, entry: its title, what its record stated, what moved its score, the values a rewritten reason may cite,
    whether the rerank ranked it from code features only, and its most similar favorite."""
    shown = shown_contributions(entry['explanation'])
    return {
        'id': listing['id'], 'title': listing['title'] or listing['address'], 'record': record_digest(listing['record']),
        'contributions': [[part['value'], round(part['contribution'], 2)] for part in shown if part['value'] is not None][:CONTRIBUTIONS_SHOWN],
        'reason': feature_reason(shown), 'citable': citable_values(entry['explanation'], listing['record']),
        'code_only': entry['reason']['code_only'], 'similar_favorite': entry['explanation']['similar_favorite'],
    }


def review_inputs(inputs, result):
    """Everything the review of a rerank reads besides its scores, which the rerank stores with its waiting review, so
    a record, a reading, a preference or a label that arrives after the rerank cannot make the task describe a ranking
    the rerank did not make, nor the review cite or mark what the rerank did not use: the stated preferences with their
    readings, the likes and dislikes of the note readings, each feature's starting weight and the preferences that name
    it, the counts of the labels other than favorites, reviewed_listing of each of the top REVIEWED listings of result,
    the rerank's result on these inputs, how many ranked listings waited for a feature record and which ones extraction
    gave up on, and the tag counts."""
    readings = inputs['preference_readings']
    phrases = stated_phrases(inputs['settings'])
    stated_by = {}
    for kind, phrase in phrases:
        for item in readings.get((kind, phrase)) or []:
            stated_by.setdefault(item['feature'], []).append(f'{kind} "{phrase}"')
        if readings.get((kind, phrase)) == []:
            stated_by.setdefault(f'preference:{phrase}', []).append(f'{kind} "{phrase}"')
    listings = {listing['id']: listing for listing in inputs['listings']}
    rankable = [listing for listing in inputs['listings'] if listing['rankable']]
    return {
        'preferences': [{'kind': kind, 'phrase': phrase, 'reading': readings.get((kind, phrase))} for kind, phrase in phrases],
        'note_readings': [{'likes': reading['likes'], 'dislikes': reading['dislikes']} for reading in inputs['note_readings'].values()
                          if reading['likes'] or reading['dislikes']],
        'starting_weights': prior_means(inputs, [(kind, phrase) for kind, phrase in phrases if not expressed(readings, kind, phrase)]),
        'stated_by': stated_by,
        'labels': {
            'contact_requests': sum(listing['contact'] for listing in inputs['listings']),
            'dismissals': sum(listing['dismissed'] for listing in inputs['listings']),
            'positive_notes': sum(sentiment == 'positive' for sentiment in inputs['sentiments'].values()),
            'negative_notes': sum(sentiment == 'negative' for sentiment in inputs['sentiments'].values()),
            'pairwise_answers': len(inputs['pairs']),
        },
        'listings': [reviewed_listing(listings[identifier], result['ranked'][identifier]) for identifier in result['check']['order'][:REVIEWED]],
        'waiting_for_features': sum(listing['record'] is None and not listing['given_up'] for listing in rankable),
        'extraction_given_up': [listing['title'] or listing['id'] for listing in rankable if listing['given_up']],
        'tags': tag_counts(inputs),
    }


def stated_preferences(snapshot):
    """The stated preferences of the rerank a snapshot belongs to, as (kind, phrase)."""
    return {(item['kind'], item['phrase']) for item in snapshot['inputs']['preferences']}


def reviewed_listings(snapshot, current):
    """The top listings of a rerank's snapshot, as reviewed_listing describes them, that current holds, by id in the
    rerank's order. current is the listings still ranked whose content has not changed since the rerank."""
    return {entry['id']: entry for entry in snapshot['inputs']['listings'] if entry['id'] in current}


def review_task(last, snapshot, current):
    """The review's input, all of it what the latest rerank, last, read and stored in its snapshot: its top REVIEWED
    listings in its order that current holds, the listings still ranked whose content has not changed since, each
    with what its record stated and what moved its score; the stated preferences with their readings, the note
    readings, the starting weights and the label counts; and the state of the ranking with the tag list, each tag with
    the number of listings whose record used it. It holds no label and no favorite's identity."""
    used = snapshot['inputs']
    starting, stated_by = used['starting_weights'], used['stated_by']
    weights = {name: weight for name, weight in last['weights'].items() if name not in HIDDEN_FEATURES}

    def weight_entry(name):
        entry = {'feature': name, 'weight': round(weights[name], 3), 'starting_weight': starting.get(name, 0.0)}
        return {**entry, 'stated': stated_by[name]} if name in stated_by else entry

    return {
        'rerank': last['id'],
        'listings': [{
            'rank': snapshot['order'].index(identifier) + 1, 'id': identifier, 'title': entry['title'],
            'exploration': identifier in snapshot['exploration'], 'record': entry['record'],
            'contributions': entry['contributions'], 'reason': entry['reason'],
        } for identifier, entry in reviewed_listings(snapshot, current).items()],
        'preferences': used['preferences'],
        'note_readings': used['note_readings'],
        'state': {
            'ranked': len(snapshot['order']),
            'labels': {'favorites': len(last['held_out']), **used['labels']},
            'held_out_ranks': {'ranks': sorted(item['rank'] for item in last['held_out']), 'median': last['held_out_median'],
                               'mean': last['held_out_mean'],
                               'ranked_listings_that_are_not_favorites': len(snapshot['order']) - len(set(snapshot['order']) & set(snapshot['held_out']))},
            'weights_against_starting_weight': [weight_entry(name) for name in sorted(weights) if starting.get(name, 0.0) * weights[name] < 0],
            'largest_weights': [weight_entry(name) for name in sorted(weights, key=lambda name: (-abs(weights[name]), name))[:WEIGHTS_SHOWN]],
            'waiting_for_features': used['waiting_for_features'],
            'extraction_given_up': used['extraction_given_up'],
            'tags': used['tags'],
        },
    }


def require_review_text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > TEXT_LIMIT:
        raise ValueError(f'{name} must be text of 1 to {TEXT_LIMIT} characters.')
    return value.strip()


def require_entries(value, name, keys):
    if not isinstance(value, list):
        raise ValueError(f'{name} must be an array.')
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != set(keys):
            raise ValueError(f'Each entry of {name} has exactly {", ".join(keys)}.')
    return value


def validated_review(value, last, snapshot, current):
    """The review's file, checked against the rerank it reviews, last, from what that rerank stored in snapshot:
    reasons, moves and flags only for the top REVIEWED of the rerank's order, each reason citing only the values its
    listing's record and explanation stated at the rerank, for the listings current holds, those still ranked whose
    content has not changed since; each move's reason and each finding citing only the values of those listings and
    review_terms of the rerank's tags and weights; no exploration listing moved and none moved onto an exploration
    listing's rank; each listing moved at most once and each to a rank of its own; and flags only for the rerank's
    stated preferences. Raises ValueError naming every rejected text, or the first other problem."""
    rerank_id = last['id']
    citable = {identifier: entry['citable'] for identifier, entry in reviewed_listings(snapshot, current).items()}
    terms, stated = review_terms(snapshot['inputs']['tags'], last['weights']), stated_preferences(snapshot)
    if not isinstance(value, dict) or set(value) != set(REVIEW_FIELDS):
        raise ValueError(f'The review is a JSON object with exactly {", ".join(REVIEW_FIELDS)}.')
    if type(value['rerank']) is int and value['rerank'] < rerank_id:
        raise ValueError(f'Rerank {value["rerank"]} was replaced by rerank {rerank_id}, which gets a review of its own. Stop: there is nothing to fix.')
    if type(value['rerank']) is not int or value['rerank'] != rerank_id:
        raise ValueError(f'rerank must be {rerank_id}, the rerank that waits for its review.')
    order = snapshot['order']
    rank_of = {identifier: position for position, identifier in enumerate(order, 1)}
    top = set(order[:REVIEWED])
    exploration = set(snapshot['exploration'])
    task_values = {*terms, *(cited for identifier in top for cited in citable.get(identifier, ()))}
    problems = []

    def require_top(identifier, name):
        if not isinstance(identifier, str) or identifier not in top:
            raise ValueError(f'{name}: {json.dumps(identifier, ensure_ascii=False)} is not one of the listings of the task.')
        return identifier

    def checked(text, name, values, words, source, *, require_citation=True):
        text = require_review_text(text, name)
        try:
            return checked_text(text, values, words, source=source, require_citation=require_citation)
        except ValueError as error:
            problems.append(f'{name} is rejected: {error}.')
            return None

    reasons, seen = {}, set()
    for entry in require_entries(value['reasons'], 'reasons', ('id', 'reason')):
        identifier = require_top(entry['id'], 'reasons')
        if identifier in seen:
            raise ValueError(f'reasons: {identifier} has two reasons.')
        seen.add(identifier)
        if identifier not in citable:
            problems.append(f'{identifier} changed since the rerank, so leave its reason out.')
            continue
        reasons[identifier] = checked(entry['reason'], f'The reason of {identifier}', citable[identifier], CONNECTING_WORDS, 'the record')
    moves, targets = {}, set()
    for entry in require_entries(value['moves'], 'moves', ('id', 'to_rank', 'reason', 'confidence')):
        identifier = require_top(entry['id'], 'moves')
        target = entry['to_rank']
        if identifier in moves:
            raise ValueError(f'moves: {identifier} is moved twice.')
        if identifier in exploration:
            raise ValueError(f'moves: {identifier} is an exploration listing, which stays at its rank.')
        if type(target) is not int or not 1 <= target <= len(order):
            raise ValueError(f'moves: the to_rank of {identifier} must be a whole number from 1 to {len(order)}.')
        if target == rank_of[identifier]:
            raise ValueError(f'moves: {identifier} is at rank {target} already.')
        if order[target - 1] in exploration:
            raise ValueError(f'moves: rank {target} holds an exploration listing, which stays there.')
        if target in targets:
            raise ValueError(f'moves: two listings are moved to rank {target}.')
        confidence = entry['confidence']
        if type(confidence) not in (int, float) or not 0 <= confidence <= 1:
            raise ValueError(f'moves: the confidence of {identifier} must be a number from 0 to 1.')
        targets.add(target)
        moves[identifier] = {'to': target, 'reason': checked(entry['reason'], f'The reason for moving {identifier}', task_values, REVIEW_WORDS, 'the task'),
                             'confidence': float(confidence)}
    flags = []
    for entry in require_entries(value['flags'], 'flags', ('id', 'kind', 'preference')):
        identifier = require_top(entry['id'], 'flags')
        if not isinstance(entry['kind'], str) or not isinstance(entry['preference'], str) or (entry['kind'], entry['preference']) not in stated:
            raise ValueError(f'flags: {json.dumps(entry["preference"], ensure_ascii=False)} is not a stated {entry["kind"]}. '
                             'Use a kind and phrase from the task\'s preferences.')
        flags.append({'id': identifier, 'kind': entry['kind'], 'preference': entry['preference']})
    if not isinstance(value['findings'], list) or len(value['findings']) > FINDINGS_LIMIT:
        raise ValueError(f'findings must be an array of at most {FINDINGS_LIMIT} texts.')
    findings = [checked(finding, f'Finding {number}', task_values, REVIEW_WORDS, 'the task', require_citation=False)
                for number, finding in enumerate(value['findings'], 1)]
    if problems:
        raise ValueError(' '.join(problems) + ' Correct these texts or leave them out.')
    return {'reasons': reasons, 'moves': moves, 'flags': flags, 'findings': findings}


def review_terms(tags, weights):
    """What a move's reason or a finding may cite besides the values of the task's listings: every tag of the tag list
    and the column name of every feature the task weighs."""
    return {*tags, *(name for name in weights if name not in HIDDEN_FEATURES)}


def reviewed_order(snapshot, moves):
    """Every listing the rerank ranked, in its order after the moves: each exploration listing at its rank, each moved
    listing at the rank it was moved to, and the others in their order around them."""
    order, exploration = snapshot['order'], set(snapshot['exploration'])
    slots = [identifier if identifier in exploration else None for identifier in order]
    for identifier, move in moves.items():
        slots[move['to'] - 1] = identifier
    rest = iter(identifier for identifier in order if identifier not in moves and identifier not in exploration)
    return [slot if slot is not None else next(rest) for slot in slots]


def adjustments(order, moved, score):
    """The score adjustment of each moved listing that puts it between the unmoved listings around it in order, the
    reviewed order of the listings ranked by score, evenly spaced when several moved listings sit between the same
    two. score is each listing's score in the full fit."""
    result = {}
    position = 0
    while position < len(order):
        if order[position] not in moved:
            position += 1
            continue
        end = position
        while end < len(order) and order[end] in moved:
            end += 1
        high = score[order[position - 1]] if position else max(score.values()) + 1
        low = score[order[end]] if end < len(order) else min(score.values()) - 1
        for step, identifier in enumerate(order[position:end], 1):
            result[identifier] = high - step * (high - low) / (end - position + 1) - score[identifier]
        position = end
    return result


def held_out_ranks(snapshot, shifts):
    """Each favorite's rank among the ranked listings that are not favorites in the fit without it, from the scores
    the rerank stored, each listing's score moved by its shift."""
    favorites = set(snapshot['held_out'])
    ranks = {}
    for favorite, held in snapshot['held_out'].items():
        own = held['own'] + shifts.get(favorite, 0.0)
        ranks[favorite] = 1 + sum(score + shifts.get(identifier, 0.0) > own
                                  for identifier, score in zip(snapshot['order'], held['scores']) if identifier not in favorites)
    return ranks


def review_outcome(snapshot, review):
    """What a validated review changes: each listing's rank, each rewritten reason, and the review's result. snapshot
    is what the rerank stored for its review, so a rewritten reason keeps the rerank's code-only mark and favorite."""
    listings = {entry['id']: entry for entry in snapshot['inputs']['listings']}
    reasons = {identifier: compose_reason({'text': text}, code_only=listings[identifier]['code_only'],
                                          similar_favorite=listings[identifier]['similar_favorite'])
               for identifier, text in review['reasons'].items()}
    order = reviewed_order(snapshot, review['moves'])
    exploration = set(snapshot['exploration'])
    score = dict(zip(snapshot['order'], snapshot['scores']))
    by_score = [identifier for identifier in order if identifier not in exploration]
    shifts = adjustments(by_score, set(review['moves']), {identifier: score[identifier] for identifier in by_score})
    before, after = summary_of(held_out_ranks(snapshot, {})), summary_of(held_out_ranks(snapshot, shifts))
    if not review['moves']:
        outcome, why = 'no moves', None
    elif before['mean'] is None:
        outcome, why = 'reverted', 'No favorite to check the moves against.'
    elif after['mean'] > before['mean']:
        outcome, why = 'reverted', 'The moves made the favorites\' mean held-out rank worse.'
    else:
        outcome, why = 'kept', None
    ranks = {identifier: position for position, identifier in enumerate(order if outcome == 'kept' else snapshot['order'], 1)}
    return ranks, reasons, {
        'outcome': outcome, 'reverted_because': why,
        'check': {'before': before, 'after': after},
        'moves': [{'id': identifier, 'title': listings[identifier]['title'], 'from': snapshot['order'].index(identifier) + 1, 'to': move['to'],
                   'reason': move['reason'], 'confidence': move['confidence']} for identifier, move in review['moves'].items()],
        'reasons': len(reasons),
        'flags': [{**flag, 'title': listings[flag['id']]['title']} for flag in review['flags']],
        'findings': review['findings'],
    }
