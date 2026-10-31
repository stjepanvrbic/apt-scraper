"""Named preference hypotheses and their chronological admission check.

Proposal preparation is explicit. The scheduled review does not call it until the measurement gate is met.
"""

import copy
import hashlib
import json
import math
import re
import sqlite3
from contextlib import AbstractContextManager
from statistics import fmean, median
from typing import Literal, NotRequired, Protocol, TypedDict, cast

from learning import LearningEvent, decode, prediction
from photo_catalog import PROBE_PROMPTS
from record import CATEGORIES, RATINGS, Verdict, validate_judgments
from rerank import RerankStore
from review import ReviewResult, ReviewTask, review_outcome, review_task, validated_review
from scoring import Direction, Label, Pair, RankingListing, design, fit_model, label, labeled
from shapes import Listing, Preferences, is_array, is_object, loads

MAX_LIVE = 6
MAX_PROPOSALS = 3
GRACE_LABELS = 20
STOP_LABELS = 40
MIN_WEIGHT = 0.1
LABEL_EVENT_FILTER = "(kind IN ('favorite', 'dismiss', 'contact', 'pair') OR (kind = 'note' AND other IN ('positive', 'negative')))"
HYPOTHESIS_KINDS = ('textual', 'visual')

HypothesisKind = Literal['textual', 'visual']
HypothesisState = Literal['active', 'retired', 'confirmed']


class Citation(TypedDict):
    """A quote of a listing's summary, trait or concern, as evidence for a hypothesis."""
    id: str
    text: str


class Proposal(TypedDict):
    """A proposed hypothesis as checked_proposals accepts it, with its id, the hash of its phrase."""
    phrase: str
    kind: HypothesisKind
    evidence: list[Citation]
    probe: NotRequired[str]
    id: str


class Comparisons(TypedDict):
    """Each later label's prediction with and without the hypothesis's column: the rank of a liked listing, and the
    pair credit of a pairwise answer."""
    rank: list[list[float]]
    pair: list[list[float]]


class Assessment(TypedDict):
    """One hypothesis's chronological admission check, as assess saves it."""
    id: str
    phrase: str
    state: str
    labels: int
    weight: float
    weight_interval: list[float] | None
    weight_interval_method: str
    survives: bool
    comparisons: Comparisons
    pair_scoring: str
    applied: NotRequired[bool]
    not_applied_reason: NotRequired[str]


class NoAssessment(TypedDict):
    """The assessment of a hypothesis before its first assessment: an empty object."""


class Hypothesis(TypedDict):
    """A row of the hypotheses table."""
    id: str
    phrase: str
    kind: HypothesisKind
    state: HypothesisState
    evidence: list[Citation]
    start_event: int
    weight: float
    assessment: Assessment | NoAssessment
    probe: str | None


class ActiveHypothesis(Hypothesis):
    """An active hypothesis with the direction its learned weight gives it, None while it has no weight."""
    direction: Direction | None


class LabelEvidence(TypedDict):
    """The labels the hypotheses explain: each labeled listing's label, and the pairwise answers."""
    actions: dict[str, Label]
    pairs: list[Pair]


class HypothesisTask(ReviewTask):
    """The review task with what proposing hypotheses adds: the labeled and top listings with their records, the
    model's columns, how many hypotheses may be proposed, the live and retired phrases, the probes a visual
    hypothesis may name, and the label evidence."""
    records: list[RankingListing]
    columns: list[str]
    max_hypotheses: int
    live: list[str]
    retired: list[str]
    probes: NotRequired[list[str]]
    label_evidence: LabelEvidence


class SavedHypothesisReview(ReviewResult):
    hypotheses: int


class JudgmentListing(RankingListing):
    """A listing to judge, with its content."""
    listing: Listing


class UnjudgedListing(JudgmentListing):
    """A listing with the hypotheses not yet judged on its current content, their ids by phrase."""
    unjudged: dict[str, str]


class JudgmentTask(TypedDict):
    kind: HypothesisKind
    listings: list[UnjudgedListing]
    photos_required: bool


class HypothesisStore(RerankStore, Protocol):
    """What the hypotheses use of ListingStore beyond what a rerank uses."""

    def connect(self, write: bool = ...) -> AbstractContextManager[sqlite3.Connection]: ...

    def require_fingerprint(self, connection: sqlite3.Connection, listing_id: str, fingerprint: str) -> None: ...

    def revision(self) -> int: ...


def single(connection: sqlite3.Connection, query: str, parameters: tuple[object, ...] = ()) -> sqlite3.Row:
    """The one row of a query that always has one, such as an aggregate."""
    return cast(sqlite3.Row, connection.execute(query, parameters).fetchone())


def hypothesis_row(row: sqlite3.Row) -> Hypothesis:
    return {'id': cast(str, row['id']), 'phrase': cast(str, row['phrase']), 'kind': cast(HypothesisKind, row['kind']),
            'state': cast(HypothesisState, row['state']), 'evidence': cast(list[Citation], loads(cast(str, row['evidence']))),
            'start_event': cast(int, row['start_event']), 'weight': cast(float, row['weight']),
            'assessment': cast(Assessment | NoAssessment, loads(cast(str, row['assessment']))), 'probe': cast(str | None, row['probe'])}


def active(store: HypothesisStore) -> list[ActiveHypothesis]:
    with store.connect() as connection:
        return [{**hypothesis_row(row), 'direction': ('like' if row['weight'] > 0 else 'dislike') if row['weight'] else None}
                for row in cast(list[sqlite3.Row], connection.execute("SELECT * FROM hypotheses WHERE state = 'active' ORDER BY rowid").fetchall())]


def event_number(connection: sqlite3.Connection) -> int:
    return cast(int, single(connection, f'SELECT COALESCE(MAX(id), 0) FROM learning_events WHERE {LABEL_EVENT_FILTER}')[0])


def stopped(connection: sqlite3.Connection) -> bool:
    first = cast(int | None, single(connection, 'SELECT MIN(start_event) FROM hypotheses')[0])
    surviving = cast(sqlite3.Row | None, connection.execute("SELECT 1 FROM hypotheses WHERE state = 'active' AND json_extract(assessment, '$.survives') = 1").fetchone())
    count = cast(int, single(connection, f'SELECT COUNT(*) FROM learning_events WHERE id > ? AND {LABEL_EVENT_FILTER}', (first,))[0])
    return first is not None and count >= STOP_LABELS and not surviving


def has_new_labels(store: HypothesisStore) -> bool:
    inputs = store.ranking_inputs()
    if not inputs['pairs'] and not any(label(listing, inputs['sentiments'].get(listing['id'])) is not None for listing in inputs['listings']):
        return False
    with store.connect() as connection:
        return cast(sqlite3.Row | None, connection.execute('SELECT 1 FROM hypothesis_passes WHERE label_event = ?', (event_number(connection),)).fetchone()) is None


def prepare(store: HypothesisStore) -> HypothesisTask | None:
    last = store.last_rerank()
    if not last or not last['review'] or last['review']['state'] not in ('waiting', 'running'):
        return None
    inputs = store.ranking_inputs()
    if not inputs['pairs'] and not any(label(listing, inputs['sentiments'].get(listing['id'])) is not None for listing in inputs['listings']):
        return None
    with store.connect(write=True) as connection:
        number = event_number(connection)
        if cast(sqlite3.Row | None, connection.execute('SELECT 1 FROM hypothesis_passes WHERE label_event = ?', (number,)).fetchone()):
            return None
        existing = [hypothesis_row(row) for row in cast(list[sqlite3.Row], connection.execute('SELECT * FROM hypotheses').fetchall())]
        allowed = 0 if stopped(connection) else min(MAX_PROPOSALS, MAX_LIVE - sum(item['state'] == 'active' for item in existing))
        snapshot = store.review_snapshot(last['id'])
        if snapshot is None:
            return None
        task: HypothesisTask = {
            **review_task(last, snapshot, set(store.ranked_listings())),
            'records': [listing for listing in inputs['listings'] if labeled(listing) or (rank := listing.get('rank')) is not None and rank <= 50],
            'columns': [column.name for column in design(inputs).columns], 'max_hypotheses': allowed,
            'live': [item['phrase'] for item in existing if item['state'] == 'active'],
            'retired': [item['phrase'] for item in existing if item['state'] != 'active'],
            'probes': sorted(PROBE_PROMPTS) if inputs.get('photos') else [],
            'label_evidence': {'actions': {listing['id']: labeled_as for listing in inputs['listings']
                                           if (labeled_as := label(listing, inputs['sentiments'].get(listing['id']))) is not None},
                               'pairs': inputs['pairs']},
        }
        _ = connection.execute('INSERT INTO hypothesis_passes (label_event, rerank_id, task) VALUES (?, ?, ?)',
                               (number, last['id'], json.dumps(task)))
    return task


def checked_proposals(proposals: object, task: HypothesisTask) -> list[Proposal]:
    if not is_array(proposals) or len(proposals) > task['max_hypotheses']:
        raise ValueError('Too many proposed hypotheses.')
    known = {name.replace('_', ' ') for name in RATINGS} | {value for values in CATEGORIES.values() for value in values}
    known |= {name.split(':', 1)[-1].replace('_', ' ') for name in task['columns']}
    records = {item['id']: item for item in task['records']}
    result: list[Proposal] = []
    seen = set(task['live'] + task['retired'])
    for value in proposals:
        if not is_object(value) or set(value) - {'probe'} != {'phrase', 'kind', 'evidence'}:
            raise ValueError('Each hypothesis needs phrase, kind and evidence, and may name a probe.')
        kind = value['kind']
        probe = None
        if 'probe' in value:
            named = value['probe']
            if kind != 'visual' or not isinstance(named, str) or named not in task.get('probes', []):
                raise ValueError('Only a visual hypothesis names a probe, and only one of the probes the task lists.')
            probe = named
        given = value['phrase']
        phrase = ' '.join(given.lower().split()) if isinstance(given, str) else ''
        if not re.fullmatch(r"[a-z][a-z0-9 '&/-]{1,78}[a-z0-9]", phrase) or phrase in seen or phrase in known:
            raise ValueError('A hypothesis must be a new phrase not already represented or retired.')
        if kind not in HYPOTHESIS_KINDS:
            raise ValueError('Hypothesis kind must be textual or visual.')
        evidence = value['evidence']
        if not is_array(evidence) or not 1 <= len(evidence) <= 5:
            raise ValueError('A hypothesis needs one to five evidence citations.')
        citations: list[Citation] = []
        for citation in evidence:
            if not is_object(citation) or set(citation) != {'id', 'text'}:
                raise ValueError('Evidence must name a listing in the task.')
            identifier, text = citation['id'], citation['text']
            if not isinstance(identifier, str) or identifier not in records:
                raise ValueError('Evidence must name a listing in the task.')
            record = records[identifier]['record']
            allowed = [record['summary'], *record['traits'], *record['concerns']] if record else []
            if not isinstance(text, str) or text not in allowed:
                raise ValueError('Evidence must quote a supplied summary, trait or concern exactly.')
            citations.append({'id': identifier, 'text': text})
        seen.add(phrase)
        proposal: Proposal = {'phrase': phrase, 'kind': kind, 'evidence': citations, 'id': hashlib.sha256(phrase.encode()).hexdigest()}
        if probe is not None:
            proposal['probe'] = probe
        result.append(proposal)
    return result


def save(store: HypothesisStore, value: object) -> SavedHypothesisReview:
    if not is_object(value) or set(value) != {'rerank', 'reasons', 'flags', 'findings', 'hypotheses'}:
        raise ValueError('A hypothesis review has rerank, reasons, flags, findings and hypotheses; no moves.')
    last = store.last_rerank()
    if not last or value['rerank'] != last['id']:
        raise ValueError('The hypothesis review no longer belongs to the latest rerank.')
    with store.connect() as connection:
        request = cast(sqlite3.Row | None, connection.execute('SELECT * FROM hypothesis_passes WHERE rerank_id = ? AND saved = 0', (last['id'],)).fetchone())
    if request is None:
        raise ValueError('No hypothesis pass is pending.')
    task = cast(HypothesisTask, loads(cast(str, request['task'])))
    proposals = checked_proposals(value['hypotheses'], task)
    snapshot = store.review_snapshot(last['id'])
    if snapshot is None:
        raise ValueError(f'The review of rerank {last["id"]} no longer waits: a later rerank replaced it or it ended.')
    fields: dict[str, object] = {key: value[key] for key in ('rerank', 'reasons', 'flags', 'findings')}
    fields['moves'] = []
    review = validated_review(fields, last, snapshot, set(store.ranked_listings()))
    ranks, reasons, result = review_outcome(snapshot, review)
    with store.connect(write=True) as connection:
        if cast(int, single(connection, "SELECT COUNT(*) FROM hypotheses WHERE state = 'active'")[0]) + len(proposals) > MAX_LIVE:
            raise ValueError('At most six hypotheses may be live.')
        number = event_number(connection)
        for proposal in proposals:
            _ = connection.execute("INSERT INTO hypotheses (id, phrase, kind, state, evidence, start_event, probe) VALUES (?, ?, ?, 'active', ?, ?, ?)",
                                   (proposal['id'], proposal['phrase'], proposal['kind'], json.dumps(proposal['evidence']), number, proposal.get('probe')))
        store.save_review(last['id'], ranks, reasons, result, connection=connection)
        _ = connection.execute('UPDATE hypothesis_passes SET saved = 1 WHERE label_event = ?', (cast(int, request['label_event']),))
    return {**result, 'hypotheses': len(proposals)}


def pending_judgments(store: HypothesisStore) -> list[JudgmentTask]:
    inputs = store.ranking_inputs()
    with store.connect() as connection:
        content = {cast(str, row['id']): cast(Listing, loads(cast(str, row['data'])))
                   for row in cast(list[sqlite3.Row], connection.execute('SELECT id, data FROM listings').fetchall())}
    records: dict[str, JudgmentListing] = {listing['id']: {**listing, 'listing': content[listing['id']]} for listing in inputs['listings']}
    visual = {identifier for identifier, listing in records.items() if labeled(listing) or (rank := listing.get('rank')) is not None and rank <= 50}
    with store.connect() as connection:
        judged = {(cast(str, row['hypothesis']), cast(str, row['listing_id']), cast(str, row['fingerprint']))
                  for row in cast(list[sqlite3.Row], connection.execute('SELECT * FROM hypothesis_judgments').fetchall())}
    tasks: list[JudgmentTask] = []
    hypotheses = active(store)
    for kind in HYPOTHESIS_KINDS:
        selected: list[UnjudgedListing] = []
        for identifier, listing in records.items():
            missing = {item['phrase']: item['id'] for item in hypotheses if item['kind'] == kind and not item['probe']
                       and (kind == 'textual' or identifier in visual)
                       and (item['id'], identifier, listing['fingerprint']) not in judged}
            if missing:
                selected.append({**listing, 'unjudged': missing})
        size = 25 if kind == 'textual' else 1
        for position in range(0, len(selected), size):
            tasks.append({'kind': kind, 'listings': selected[position:position + size], 'photos_required': kind == 'visual'})
    return tasks


def save_judgments(store: HypothesisStore, entries: object) -> None:
    if not is_array(entries):
        raise ValueError('Hypothesis judgments must be a list.')
    pending = {(identifier, listing['id'], listing['fingerprint']) for task in pending_judgments(store)
               for listing in task['listings'] for identifier in listing['unjudged'].values()}
    hypotheses = {item['id']: item for item in active(store)}
    rows: list[tuple[str, str, str, Verdict, str]] = []
    for entry in entries:
        if not is_object(entry) or set(entry) != {'hypothesis', 'id', 'fingerprint', 'verdict', 'evidence'}:
            raise ValueError('A judgment needs hypothesis, id, fingerprint, verdict and evidence.')
        hypothesis, identifier, fingerprint = entry['hypothesis'], entry['id'], entry['fingerprint']
        if not isinstance(hypothesis, str) or not isinstance(identifier, str) or not isinstance(fingerprint, str):
            raise ValueError('Judgment identities must be strings.')
        key = (hypothesis, identifier, fingerprint)
        if key not in pending:
            raise ValueError('This hypothesis judgment is not pending or is outside its photo budget.')
        phrase = hypotheses[hypothesis]['phrase']
        checked = validate_judgments({phrase: {'verdict': entry['verdict'], 'evidence': entry['evidence']}}, [phrase])[phrase]
        rows.append((*key, checked['verdict'], checked['evidence']))
        pending.remove(key)
    with store.connect(write=True) as connection:
        for row in rows:
            store.require_fingerprint(connection, row[1], row[2])
        _ = connection.executemany('INSERT OR REPLACE INTO hypothesis_judgments VALUES (?, ?, ?, ?, ?)', rows)


def decide(store: HypothesisStore, identifier: str, decision: str) -> None:
    if decision not in ('confirm', 'reject'):
        raise ValueError('Choose confirm or reject.')
    with store.connect(write=True) as connection:
        hypothesis = cast(sqlite3.Row | None, connection.execute('SELECT * FROM hypotheses WHERE id = ?', (identifier,)).fetchone())
        target = 'confirmed' if decision == 'confirm' else 'retired'
        if hypothesis is not None and hypothesis['state'] == target:
            return
        if hypothesis is None or hypothesis['state'] != 'active':
            raise ValueError('This hypothesis is no longer active.')
        if decision == 'confirm':
            weight, phrase = cast(float, hypothesis['weight']), cast(str, hypothesis['phrase'])
            if not weight:
                raise ValueError('This hypothesis has no learned direction yet.')
            row = cast(sqlite3.Row | None, connection.execute("SELECT value FROM settings WHERE name = 'preferences'").fetchone())
            preferences = cast(Preferences, loads(cast(str, row['value']))) if row else Preferences(likes=[], dislikes=[])
            direction = 'likes' if weight >= 0 else 'dislikes'
            for kind in ('likes', 'dislikes'):
                preferences[kind] = [candidate for candidate in preferences.get(kind, []) if candidate != phrase]
            preferences.setdefault(direction, []).append(phrase)
            _ = connection.execute("INSERT INTO settings VALUES ('preferences', ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value", (json.dumps(preferences),))
            for judgment in cast(list[sqlite3.Row], connection.execute('''SELECT j.*, f.preferences FROM hypothesis_judgments j JOIN feature_records f
                    ON f.listing_id = j.listing_id AND f.fingerprint = j.fingerprint WHERE j.hypothesis = ?''', (identifier,)).fetchall()):
                values = cast(dict[str, object], loads(cast(str, judgment['preferences'])))
                values[phrase] = {'verdict': cast(str, judgment['verdict']), 'evidence': cast(str, judgment['evidence'])}
                _ = connection.execute('UPDATE feature_records SET preferences = ? WHERE listing_id = ?', (json.dumps(values), cast(str, judgment['listing_id'])))
        _ = connection.execute('UPDATE hypotheses SET state = ? WHERE id = ?', (target, identifier))


class LabelEvent(LearningEvent):
    """A learning_events row of a label: its id, when it was given, and the ranking inputs of the time, encoded."""
    id: int
    occurred_at: str
    inputs: str


def assess(store: HypothesisStore) -> list[Assessment]:
    revision = store.revision()
    inputs = store.ranking_inputs()
    model = fit_model(inputs)
    weights = dict(zip((column.name for column in model.design.columns), model.fit.weights[1:]))
    results: list[Assessment] = []
    with store.connect() as connection:
        events: list[LabelEvent] = [
            {'id': cast(int, row['id']), 'occurred_at': cast(str, row['occurred_at']), 'kind': cast(str, row['kind']),
             'listing': cast(str, row['listing']), 'other': cast(str | None, row['other']), 'inputs': cast(str, row['inputs'])}
            for row in cast(list[sqlite3.Row], connection.execute(f'SELECT * FROM learning_events WHERE {LABEL_EVENT_FILTER} ORDER BY id').fetchall())]
    for hypothesis in active(store):
        later = [event for event in events if event['id'] > hypothesis['start_event']]
        weight = weights.get(f'hypothesis:{hypothesis["phrase"]}', 0)
        column = model.design.index(f'hypothesis:{hypothesis["phrase"]}')
        interval: list[float] | None = None
        if column is not None:
            direction = [float(index == column + 1) for index in range(len(model.fit.weights))]
            transformed = model.fit.factor.forward(direction)
            deviation = math.sqrt(sum(value * value for value in transformed))
            interval = [weight - 1.96 * deviation, weight + 1.96 * deviation]
        comparisons: Comparisons = {'rank': [], 'pair': []}
        if len(later) >= GRACE_LABELS and abs(weight) >= MIN_WEIGHT:
            for event in later:
                if event['kind'] not in ('favorite', 'contact', 'pair', 'note'):
                    continue
                snapshot = decode(event['inputs'])
                with_column = prediction(snapshot, event, required_column=f'hypothesis:{hypothesis["phrase"]}')
                if with_column is None or with_column['kind'] == 'rank' and with_column['already_positive']:
                    continue
                without = copy.deepcopy(snapshot)
                for listing in without['listings']:
                    _ = listing.get('hypotheses', {}).pop(hypothesis['phrase'], None)
                without_column = prediction(without, event)
                if without_column:
                    comparisons[with_column['kind']].append([with_column['value'], without_column['value']])
        ranks, pairs = comparisons['rank'], comparisons['pair']
        survives = bool(ranks or pairs) and abs(weight) >= MIN_WEIGHT
        survives = survives and (not ranks or median(a for a, _ in ranks) <= median(b for _, b in ranks))
        survives = survives and (not pairs or fmean(a for a, _ in pairs) >= fmean(b for _, b in pairs))
        state = 'retired' if len(later) >= GRACE_LABELS and not survives else 'active'
        result: Assessment = {'id': hypothesis['id'], 'phrase': hypothesis['phrase'], 'state': state, 'labels': len(later),
                              'weight': weight, 'weight_interval': interval, 'weight_interval_method': '95% Laplace normal interval when a column exists',
                              'survives': bool(survives), 'comparisons': comparisons,
                              'pair_scoring': 'Chance credit for tied scores; paired prequential accuracy, not a binomial Wilson estimate.'}
        results.append(result)
    with store.connect(write=True) as connection:
        unchanged = cast(int, single(connection, 'SELECT revision FROM changes WHERE id = 1')[0]) == revision
        for result in results:
            result['applied'] = True
            updated = unchanged and connection.execute(
                "UPDATE hypotheses SET state = ?, weight = ?, assessment = ? WHERE id = ? AND state = 'active'",
                (result['state'], result['weight'], json.dumps(result), result['id'])).rowcount
            if not updated:
                state = cast(str, single(connection, 'SELECT state FROM hypotheses WHERE id = ?', (result['id'],))[0])
                result.update(state=state, survives=False, applied=False,
                              not_applied_reason='The store changed before this assessment was saved.')
    return results
