"""Gathers the encoder measurements into the published report. A developer tool, not shipped.

  encoder_report.py <reference-report.json> <records-report.json> <compare.jsonl> <resources.jsonl> <labels.json> <output.json>

The inputs are the outputs of encoder_sweep.py evaluate (against the reference labels and against the owner records'
fields), encoder_sweep.py compare and encoder_resources.py. Only aggregate numbers are kept: no listing, photo or
label of the owner's store.
"""

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'profile/skills/gallery/scripts'))
from encoder_sweep import AucReport, Choice, EvaluateReport, Labels, RoomReport, Rule, RuleResult, Timing  # noqa: E402
from photo_catalog import GATE_PROBES as GATE, Probe  # noqa: E402


class Candidate(TypedDict):
    """A variant in the published report: its measurements and how many gate measures it met."""
    variant: str
    choice: Choice
    room: RoomReport
    probes: dict[Probe, AucReport]
    probe_rule: Rule
    probe_rules: dict[Rule, RuleResult]
    record_fields: dict[str, AucReport] | None
    gate_measures_met: int
    gate_measures_mean: float
    sweep_timing: Timing | None


class ReferenceLabels(TypedDict):
    listings: int
    by_source: dict[str, dict[Probe, dict[str, int]]]
    method: str


class Report(TypedDict):
    """The published encoder report: the gate, every candidate ranked, and the precision and resource measurements."""
    generated_at: str
    ticket: str
    photos: dict[str, int | str]
    reference_labels: ReferenceLabels
    intervals: str
    selection: str
    gate: dict[str, float]
    candidates: list[Candidate]
    chosen: str
    precision: list[object]
    resources: list[object]
    commands: list[str]


def lines(path: Path) -> list[object]:
    """The records of a JSON lines file, passed through to the report as they are."""
    return [cast(object, json.loads(line)) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def measured(value: float | None, what: str) -> float:
    """A gate measure the sweep computed; it is None only when the sweep had nothing to measure, which no gate can pass."""
    if value is None:
        raise ValueError(f'The sweep report has no {what}.')
    return value


def main(argv: list[str]) -> None:
    reference, records, compare, resources, labels, output = map(Path, argv)
    reports = cast(EvaluateReport, json.loads(reference.read_text()))['reports']
    record_reports = {item['variant']: item['probes']['rules']['raw']['owner']
                      for item in cast(EvaluateReport, json.loads(records.read_text()))['reports']}
    label_values = cast(Labels, json.loads(labels.read_text(encoding='utf-8')))
    counts: dict[str, dict[Probe, dict[str, int]]] = {source: {name: dict(Counter(str(value[name]) for listing, value in label_values.items() if listing.startswith(prefix)))
                       for name in GATE}
              for source, prefix in (('owner', 'zillow:'), ('houses', 'house:'))}
    candidates: list[Candidate] = []
    for item in reports:
        room = item['room']['combined_test']
        if room is None:
            raise ValueError(f'The sweep report has no room type agreement for {item["variant"]}.')
        measures = [room['agreement'], *(measured(item['probes'][name]['auc'], f'{name} AUC for {item["variant"]}') for name in GATE)]
        met = (room['photos'] >= 1000 and measures[0] >= 0.85) + sum(value >= 0.85 for value in measures[1:])
        candidates.append({'variant': item['variant'], 'choice': item['choice'], 'room': item['room'],
                           'probes': {name: item['probes'][name] for name in GATE}, 'probe_rule': item['probes']['rule'],
                           'probe_rules': item['probes']['rules'], 'record_fields': record_reports.get(item['variant']),
                           'gate_measures_met': met, 'gate_measures_mean': sum(measures) / len(measures),
                           'sweep_timing': item.get('timing')})
    ranked = sorted(candidates, key=lambda item: (-item['gate_measures_met'], -item['gate_measures_mean']))
    report: Report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'ticket': '0055 Phase 4',
        'photos': {'owner': 973, 'owner_extraction_typed': 462, 'houses': 2140, 'houses_test': 1784,
                   'houses_source': 'Ahmed and Moustafa (2016), https://github.com/emanhamed/Houses-dataset at 5e14821'},
        'reference_labels': {'listings': len(label_values), 'by_source': counts,
                             'method': 'Sonnet read of contact sheets under one written definition per probe (tests/encoder_label_sheets.py)'},
        'intervals': '95% bootstrap, 2,000 resamples: clustered by listing for room types, stratified by class for AUCs',
        'selection': 'most of the six gate measures met (room types, five probe AUCs), then the highest mean of the six',
        'gate': {'room_agreement': 0.85, 'room_photos': 1000, 'probe_auc': 0.85},
        'candidates': ranked, 'chosen': ranked[0]['variant'],
        'precision': lines(compare), 'resources': lines(resources),
        'commands': ['tests/encoder_models.py', 'tests/encoder_sweep.py manifest|text|images|evaluate|compare',
                     'tests/encoder_label_sheets.py', 'tests/encoder_convert.py', 'tests/encoder_resources.py',
                     'tests/encoder_build.py', 'tests/encoder_report.py'],
    }
    _ = output.write_text(json.dumps(report, indent=1, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'chosen': report['chosen'], 'order': [(item['variant'], item['gate_measures_met'], round(item['gate_measures_mean'], 4)) for item in ranked]}))


if __name__ == '__main__':
    main(sys.argv[1:])
