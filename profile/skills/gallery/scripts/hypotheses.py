"""Named preference hypotheses and their chronological admission check.

Proposal preparation is explicit. The scheduled review does not call it until the measurement gate is met.
"""

import copy
import hashlib
import json
import math
import re
from statistics import fmean, median

from learning import decode, prediction
from record import CATEGORIES, RATINGS, validate_judgments
from review import review_outcome, review_task, validated_review
from scoring import design, fit_model, label, labeled

MAX_LIVE = 6
MAX_PROPOSALS = 3
GRACE_LABELS = 20
STOP_LABELS = 40
MIN_WEIGHT = 0.1
LABEL_EVENT_FILTER = "(kind IN ('favorite', 'dismiss', 'contact', 'pair') OR (kind = 'note' AND other IN ('positive', 'negative')))"


def active(store):
    with store.connect() as connection:
        return [{**dict(row), 'evidence': json.loads(row['evidence']), 'assessment': json.loads(row['assessment']),
                 'direction': ('like' if row['weight'] > 0 else 'dislike') if row['weight'] else None}
                for row in connection.execute("SELECT * FROM hypotheses WHERE state = 'active' ORDER BY rowid")]


def event_number(connection):
    return connection.execute(f'SELECT COALESCE(MAX(id), 0) FROM learning_events WHERE {LABEL_EVENT_FILTER}').fetchone()[0]


def stopped(connection):
    first = connection.execute('SELECT MIN(start_event) FROM hypotheses').fetchone()[0]
    surviving = connection.execute("SELECT 1 FROM hypotheses WHERE state = 'active' AND json_extract(assessment, '$.survives') = 1").fetchone()
    count = connection.execute(f'SELECT COUNT(*) FROM learning_events WHERE id > ? AND {LABEL_EVENT_FILTER}', (first,)).fetchone()[0]
    return first is not None and count >= STOP_LABELS and not surviving


def has_new_labels(store):
    inputs = store.ranking_inputs()
    if not inputs['pairs'] and not any(label(listing, inputs['sentiments'].get(listing['id'])) is not None for listing in inputs['listings']):
        return False
    with store.connect() as connection:
        return connection.execute('SELECT 1 FROM hypothesis_passes WHERE label_event = ?', (event_number(connection),)).fetchone() is None


def prepare(store):
    last = store.last_rerank()
    if not last or not last['review'] or last['review']['state'] not in ('waiting', 'running'):
        return None
    inputs = store.ranking_inputs()
    if not inputs['pairs'] and not any(label(listing, inputs['sentiments'].get(listing['id'])) is not None for listing in inputs['listings']):
        return None
    with store.connect(write=True) as connection:
        number = event_number(connection)
        if connection.execute('SELECT 1 FROM hypothesis_passes WHERE label_event = ?', (number,)).fetchone():
            return None
        existing = [dict(row) for row in connection.execute('SELECT * FROM hypotheses')]
        allowed = 0 if stopped(connection) else min(MAX_PROPOSALS, MAX_LIVE - sum(item['state'] == 'active' for item in existing))
        snapshot = store.review_snapshot(last['id'])
        task = review_task(last, snapshot, set(store.ranked_listings()))
        task.update(records=[listing for listing in inputs['listings'] if labeled(listing) or listing.get('rank') is not None and listing['rank'] <= 50],
                    columns=[column.name for column in design(inputs).columns], max_hypotheses=allowed,
                    live=[item['phrase'] for item in existing if item['state'] == 'active'],
                    retired=[item['phrase'] for item in existing if item['state'] != 'active'])
        task['label_evidence'] = {'actions': {listing['id']: label(listing, inputs['sentiments'].get(listing['id']))
                                             for listing in inputs['listings'] if label(listing, inputs['sentiments'].get(listing['id'])) is not None},
                                  'pairs': inputs['pairs']}
        connection.execute('INSERT INTO hypothesis_passes (label_event, rerank_id, task) VALUES (?, ?, ?)',
                           (number, last['id'], json.dumps(task)))
    return task


def checked_proposals(proposals, task):
    if not isinstance(proposals, list) or len(proposals) > task['max_hypotheses']:
        raise ValueError('Too many proposed hypotheses.')
    known = {name.replace('_', ' ') for name in RATINGS} | {value for values in CATEGORIES.values() for value in values}
    known |= {name.split(':', 1)[-1].replace('_', ' ') for name in task['columns']}
    records = {item['id']: item for item in task['records']}
    result, seen = [], set(task['live'] + task['retired'])
    for value in proposals:
        if not isinstance(value, dict) or set(value) != {'phrase', 'kind', 'evidence'}:
            raise ValueError('Each hypothesis needs phrase, kind and evidence.')
        phrase = ' '.join(value['phrase'].lower().split()) if isinstance(value['phrase'], str) else ''
        if not re.fullmatch(r"[a-z][a-z0-9 '&/-]{1,78}[a-z0-9]", phrase) or phrase in seen or phrase in known:
            raise ValueError('A hypothesis must be a new phrase not already represented or retired.')
        if value['kind'] not in ('textual', 'visual'):
            raise ValueError('Hypothesis kind must be textual or visual.')
        evidence = value['evidence']
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 5:
            raise ValueError('A hypothesis needs one to five evidence citations.')
        for citation in evidence:
            if not isinstance(citation, dict) or set(citation) != {'id', 'text'} or not isinstance(citation['id'], str) or citation['id'] not in records:
                raise ValueError('Evidence must name a listing in the task.')
            record = records[citation['id']]['record']
            allowed = [record['summary'], *record['traits'], *record['concerns']] if record else []
            if not isinstance(citation['text'], str) or citation['text'] not in allowed:
                raise ValueError('Evidence must quote a supplied summary, trait or concern exactly.')
        seen.add(phrase)
        result.append({**value, 'phrase': phrase, 'id': hashlib.sha256(phrase.encode()).hexdigest()})
    return result


def save(store, value):
    if not isinstance(value, dict) or set(value) != {'rerank', 'reasons', 'flags', 'findings', 'hypotheses'}:
        raise ValueError('A hypothesis review has rerank, reasons, flags, findings and hypotheses; no moves.')
    last = store.last_rerank()
    if not last or value['rerank'] != last['id']:
        raise ValueError('The hypothesis review no longer belongs to the latest rerank.')
    with store.connect() as connection:
        request = connection.execute('SELECT * FROM hypothesis_passes WHERE rerank_id = ? AND saved = 0', (last['id'],)).fetchone()
    if request is None:
        raise ValueError('No hypothesis pass is pending.')
    task = json.loads(request['task'])
    proposals = checked_proposals(value['hypotheses'], task)
    snapshot = store.review_snapshot(last['id'])
    review = validated_review({key: value[key] for key in ('rerank', 'reasons', 'flags', 'findings')} | {'moves': []},
                              last, snapshot, set(store.ranked_listings()))
    ranks, reasons, result = review_outcome(snapshot, review)
    with store.connect(write=True) as connection:
        if connection.execute("SELECT COUNT(*) FROM hypotheses WHERE state = 'active'").fetchone()[0] + len(proposals) > MAX_LIVE:
            raise ValueError('At most six hypotheses may be live.')
        number = event_number(connection)
        for proposal in proposals:
            connection.execute("INSERT INTO hypotheses (id, phrase, kind, state, evidence, start_event) VALUES (?, ?, ?, 'active', ?, ?)",
                               (proposal['id'], proposal['phrase'], proposal['kind'], json.dumps(proposal['evidence']), number))
        store.save_review(last['id'], ranks, reasons, result, connection=connection)
        connection.execute('UPDATE hypothesis_passes SET saved = 1 WHERE label_event = ?', (request['label_event'],))
    return {**result, 'hypotheses': len(proposals)}


def pending_judgments(store):
    inputs = store.ranking_inputs()
    with store.connect() as connection:
        content = {row['id']: json.loads(row['data']) for row in connection.execute('SELECT id, data FROM listings')}
    records = {listing['id']: {**listing, 'listing': content[listing['id']]} for listing in inputs['listings']}
    visual = {identifier for identifier, listing in records.items() if labeled(listing) or listing.get('rank') is not None and listing['rank'] <= 50}
    with store.connect() as connection:
        judged = {(row['hypothesis'], row['listing_id'], row['fingerprint']) for row in connection.execute('SELECT * FROM hypothesis_judgments')}
    tasks = []
    hypotheses = active(store)
    for kind in ('textual', 'visual'):
        selected = []
        for identifier, listing in records.items():
            missing = {item['phrase']: item['id'] for item in hypotheses if item['kind'] == kind
                       and (kind == 'textual' or identifier in visual)
                       and (item['id'], identifier, listing['fingerprint']) not in judged}
            if missing:
                selected.append({**listing, 'unjudged': missing})
        size = 25 if kind == 'textual' else 1
        for position in range(0, len(selected), size):
            tasks.append({'kind': kind, 'listings': selected[position:position + size], 'photos_required': kind == 'visual'})
    return tasks


def save_judgments(store, entries):
    if not isinstance(entries, list):
        raise ValueError('Hypothesis judgments must be a list.')
    pending = {(identifier, listing['id'], listing['fingerprint']) for task in pending_judgments(store)
               for listing in task['listings'] for identifier in listing['unjudged'].values()}
    hypotheses = {item['id']: item for item in active(store)}
    rows = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'hypothesis', 'id', 'fingerprint', 'verdict', 'evidence'}:
            raise ValueError('A judgment needs hypothesis, id, fingerprint, verdict and evidence.')
        if not all(isinstance(entry[name], str) for name in ('hypothesis', 'id', 'fingerprint')):
            raise ValueError('Judgment identities must be strings.')
        key = (entry['hypothesis'], entry['id'], entry['fingerprint'])
        if key not in pending:
            raise ValueError('This hypothesis judgment is not pending or is outside its photo budget.')
        phrase = hypotheses[entry['hypothesis']]['phrase']
        checked = validate_judgments({phrase: {'verdict': entry['verdict'], 'evidence': entry['evidence']}}, [phrase])[phrase]
        rows.append((*key, checked['verdict'], checked['evidence']))
        pending.remove(key)
    with store.connect(write=True) as connection:
        for row in rows:
            store.require_fingerprint(connection, row[1], row[2])
        connection.executemany('INSERT OR REPLACE INTO hypothesis_judgments VALUES (?, ?, ?, ?, ?)', rows)


def decide(store, identifier, decision):
    if decision not in ('confirm', 'reject'):
        raise ValueError('Choose confirm or reject.')
    with store.connect(write=True) as connection:
        hypothesis = connection.execute('SELECT * FROM hypotheses WHERE id = ?', (identifier,)).fetchone()
        target = 'confirmed' if decision == 'confirm' else 'retired'
        if hypothesis is not None and hypothesis['state'] == target:
            return
        if hypothesis is None or hypothesis['state'] != 'active':
            raise ValueError('This hypothesis is no longer active.')
        if decision == 'confirm':
            if not hypothesis['weight']:
                raise ValueError('This hypothesis has no learned direction yet.')
            row = connection.execute("SELECT value FROM settings WHERE name = 'preferences'").fetchone()
            preferences = json.loads(row['value']) if row else {'likes': [], 'dislikes': []}
            direction = 'likes' if hypothesis['weight'] >= 0 else 'dislikes'
            for kind in ('likes', 'dislikes'):
                preferences[kind] = [phrase for phrase in preferences[kind] if phrase != hypothesis['phrase']]
            preferences[direction].append(hypothesis['phrase'])
            connection.execute("INSERT INTO settings VALUES ('preferences', ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value", (json.dumps(preferences),))
            for judgment in connection.execute('''SELECT j.*, f.preferences FROM hypothesis_judgments j JOIN feature_records f
                    ON f.listing_id = j.listing_id AND f.fingerprint = j.fingerprint WHERE j.hypothesis = ?''', (identifier,)).fetchall():
                values = json.loads(judgment['preferences'])
                values[hypothesis['phrase']] = {'verdict': judgment['verdict'], 'evidence': judgment['evidence']}
                connection.execute('UPDATE feature_records SET preferences = ? WHERE listing_id = ?', (json.dumps(values), judgment['listing_id']))
        connection.execute('UPDATE hypotheses SET state = ? WHERE id = ?', (target, identifier))


def assess(store):
    revision = store.revision()
    inputs = store.ranking_inputs()
    model = fit_model(inputs)
    weights = dict(zip((column.name for column in model.design.columns), model.fit.weights[1:]))
    results = []
    with store.connect() as connection:
        events = [dict(row) for row in connection.execute(f'SELECT * FROM learning_events WHERE {LABEL_EVENT_FILTER} ORDER BY id')]
    for hypothesis in active(store):
        later = [event for event in events if event['id'] > hypothesis['start_event']]
        weight = weights.get(f'hypothesis:{hypothesis["phrase"]}', 0)
        column = model.design.index(f'hypothesis:{hypothesis["phrase"]}')
        interval = None
        if column is not None:
            direction = [float(index == column + 1) for index in range(len(model.fit.weights))]
            transformed = model.fit.factor.forward(direction)
            deviation = math.sqrt(sum(value * value for value in transformed))
            interval = [weight - 1.96 * deviation, weight + 1.96 * deviation]
        comparisons = {'rank': [], 'pair': []}
        if len(later) >= GRACE_LABELS and abs(weight) >= MIN_WEIGHT:
            for event in later:
                if event['kind'] not in ('favorite', 'contact', 'pair', 'note'):
                    continue
                snapshot = decode(event['inputs'])
                with_column = prediction(snapshot, event, required_column=f'hypothesis:{hypothesis["phrase"]}')
                if with_column is None or with_column.get('already_positive'):
                    continue
                without = copy.deepcopy(snapshot)
                for listing in without['listings']:
                    listing.get('hypotheses', {}).pop(hypothesis['phrase'], None)
                without_column = prediction(without, event)
                if without_column:
                    comparisons[with_column['kind']].append([with_column['value'], without_column['value']])
        ranks, pairs = comparisons['rank'], comparisons['pair']
        survives = bool(ranks or pairs) and abs(weight) >= MIN_WEIGHT
        survives = survives and (not ranks or median(a for a, _ in ranks) <= median(b for _, b in ranks))
        survives = survives and (not pairs or fmean(a for a, _ in pairs) >= fmean(b for _, b in pairs))
        state = 'retired' if len(later) >= GRACE_LABELS and not survives else 'active'
        result = {'id': hypothesis['id'], 'phrase': hypothesis['phrase'], 'state': state, 'labels': len(later),
                  'weight': weight, 'weight_interval': interval, 'weight_interval_method': '95% Laplace normal interval when a column exists',
                  'survives': bool(survives), 'comparisons': comparisons}
        result['pair_scoring'] = 'Chance credit for tied scores; paired prequential accuracy, not a binomial Wilson estimate.'
        results.append(result)
    with store.connect(write=True) as connection:
        unchanged = connection.execute('SELECT revision FROM changes WHERE id = 1').fetchone()[0] == revision
        for result in results:
            result['applied'] = True
            updated = unchanged and connection.execute(
                "UPDATE hypotheses SET state = ?, weight = ?, assessment = ? WHERE id = ? AND state = 'active'",
                (result['state'], result['weight'], json.dumps(result), result['id'])).rowcount
            if not updated:
                state = connection.execute('SELECT state FROM hypotheses WHERE id = ?', (result['id'],)).fetchone()[0]
                result.update(state=state, survives=False, applied=False,
                              not_applied_reason='The store changed before this assessment was saved.')
    return results
