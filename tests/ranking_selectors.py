"""Prior-only cold-start comparisons, evaluated before replacing the installed selector."""

import copy

from scoring import (DEFAULT_HYPERPARAMETERS, Factor, Fit, Model, design, label, observations,
                     prior, questions, select_questions)

COLD_ANSWERS = 8


def cold_questions(inputs, count, answered):
    explicit = sum(label(listing, inputs['sentiments'].get(listing['id'])) is not None for listing in inputs['listings'])
    if explicit or len(inputs['pairs']) >= COLD_ANSWERS:
        return questions(inputs, DEFAULT_HYPERPARAMETERS, count, answered)
    state = copy.deepcopy(inputs)
    used = {identifier for pair in inputs['pairs'] for identifier in pair}
    for listing in state['listings']:
        if listing['id'] in used:
            listing['rankable'] = False
            listing['paired'] = True
    model_design = design(state)
    mean, precision = prior(model_design, 1)
    precision = [value * (4 if center else 1) for center, value in zip(mean, precision)]
    matrix = [[value if row == column else 0 for column in range(len(mean))] for row, value in enumerate(precision)]
    fitted = Fit(mean, Factor.of(matrix), [])
    model = Model(model_design, fitted, 1, 0.05, observations(model_design, state, 0.05), (mean, precision))
    return select_questions(model, min(count, COLD_ANSWERS - len(inputs['pairs'])), answered)
