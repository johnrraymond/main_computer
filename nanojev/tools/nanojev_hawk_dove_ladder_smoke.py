#!/usr/bin/env python3
"""Crossed Hawk–Dove smoke: ten jackets independently applied to each shared scenario.

All COMMAND-LINE STATE INPUTS are categorical: on/off, low/medium/high,
in-range/out-of-range, now/soon/later. There are no numerical state inputs.

Each synthetic jacket rubric converts the categorical state into X; a SHARED opponent
model converts the same opponent evidence into Y for every captain. Both lie on
[0, 2_000_000], then SUB gives Z. The learner's final English problem contains
NO NUMERALS; only the progressively scaffolded early rungs show X and Y.

Offline only. Does not import a model or write to a database/checkpoint.

Every captain receives the identical scenario text, state, opponent evidence, and
question; the jacket is the controlled variable. Use --all-scenarios to repeat
the ten-captain comparison for subsequent independent situations. The c01
jacket/rubric remain; scenario defaults illustrate decision contrasts.

Usage from repository root:
    python -m tools.nanojev_hawk_dove_ladder_smoke --list-captains
    python -m tools.nanojev_hawk_dove_ladder_smoke --captain c07 --level 6
    python -m tools.nanojev_hawk_dove_ladder_smoke --all-captains --level 10
    python -m tools.nanojev_hawk_dove_ladder_smoke --matrix --scenario s01
    python -m tools.nanojev_hawk_dove_ladder_smoke --matrix --all-scenarios
    python -m tools.nanojev_hawk_dove_ladder_smoke --matrix --matrix-pair c01:c03
    python -m tools.nanojev_hawk_dove_ladder_smoke --self-test
    python -m tools.nanojev_hawk_dove_ladder_smoke --show-rubric
    python -m tools.nanojev_hawk_dove_ladder_smoke --crew-danger low \
        --resource-priority high --mission-dependency on \
        --friendly-support in-range --decision-horizon now \
        --opponent-pressure low --opponent-escalating off \
        --opponent-support out-of-range
    python -m tools.nanojev_hawk_dove_ladder_smoke --level 10
    python -m tools.nanojev_hawk_dove_ladder_smoke --format json

The text report starts at L10 (the raw captain story), then works BACKWARD
toward L00 (pure numerical scaffolding). Intermediate prompts are actually
condensed rather than carrying the full story unchanged at every rung.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields, replace
import json
import math
import re
from typing import Any

if __package__:
    from .nanojev_register_stability_v3_generate import logbar
    from .nanojev_hawk_dove_captains_v1 import BY_ID, CAPTAINS, Captain
    from .nanojev_hawk_dove_scenarios_v1 import BY_ID as SCENARIO_BY_ID, SCENARIOS, Scenario
else:
    from nanojev_register_stability_v3_generate import logbar
    from nanojev_hawk_dove_captains_v1 import BY_ID, CAPTAINS, Captain
    from nanojev_hawk_dove_scenarios_v1 import BY_ID as SCENARIO_BY_ID, SCENARIOS, Scenario

SCHEMA = 'nanojev.hawk-dove.crossed-scenarios.v5'
MATRIX_SCHEMA = 'nanojev.hawk-dove.head-to-head-matrix.v1'
M = 2_000_000
SCALE_POINTS = 100
TEMPERATURE = 500_000
OPERATION = 'subtract'
DEFAULT_CAPTAIN = BY_ID['c01']
JACKET_ID = 'crew-first-mission-pragmatist-v1'  # compatibility with the original smoke
DEFAULT_SCENARIO = SCENARIO_BY_ID['s01']
QUESTION_ID = DEFAULT_SCENARIO.key
JACKET = DEFAULT_CAPTAIN.jacket
QUESTION = DEFAULT_SCENARIO.question
# Common opponent teacher: identical evidence MUST give identical Y across jackets.
Y_BASE = 45
Y_EFFECTS = {
    'opponent_pressure': {'low': -18, 'medium': 0, 'high': 18},
    'opponent_escalating': {'off': -6, 'on': 10},
    'opponent_support': {'out-of-range': -8, 'in-range': 10},
}

LOW_MED_HIGH = ('low', 'medium', 'high')
ON_OFF = ('off', 'on')
RANGE = ('out-of-range', 'in-range')
WHEN = ('now', 'soon', 'later')
STATE_CHOICES: dict[str, tuple[str, ...]] = {
    'resource_priority': LOW_MED_HIGH,
    'crew_danger': LOW_MED_HIGH,
    'mission_dependency': ON_OFF,
    'friendly_support': RANGE,
    'decision_horizon': WHEN,
    'opponent_pressure': LOW_MED_HIGH,
    'opponent_escalating': ON_OFF,
    'opponent_support': RANGE,
}


@dataclass(frozen=True)
class State:
    resource_priority: str = 'medium'
    crew_danger: str = 'high'
    mission_dependency: str = 'off'
    friendly_support: str = 'out-of-range'
    decision_horizon: str = 'soon'
    opponent_pressure: str = 'high'
    opponent_escalating: str = 'on'
    opponent_support: str = 'in-range'

    def validate(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if value not in STATE_CHOICES[f.name]:
                raise ValueError(f'{f.name.replace("_", "-")} must be one of: '
                                 + ', '.join(STATE_CHOICES[f.name]))


# Compatibility constants for the original c01 smoke; the other nine cases
# have their own independently defined teachers in nanojev_hawk_dove_captains_v1.
X_BASE = DEFAULT_CAPTAIN.x_base
X_EFFECTS = DEFAULT_CAPTAIN.x_effects
assert not set(X_EFFECTS).intersection(Y_EFFECTS)
assert M % SCALE_POINTS == 0


def resolve_captain(value: str | Captain = 'c01') -> Captain:
    if isinstance(value, Captain):
        return value
    try:
        return BY_ID[value]
    except KeyError as exc:
        raise ValueError(f'unknown captain: {value}') from exc



def resolve_scenario(value: str | Scenario = 's01') -> Scenario:
    if isinstance(value, Scenario):
        return value
    try:
        return SCENARIO_BY_ID[value]
    except KeyError as exc:
        raise ValueError(f'unknown scenario: {value}') from exc


def scenario_state(scenario: str | Scenario = 's01', **overrides: str | None) -> State:
    """Defaults belong to the scenario; explicit CLI values override them."""
    scene = resolve_scenario(scenario)
    data = asdict(State())
    data.update(dict(scene.default_state))
    for name, value in overrides.items():
        if name not in STATE_CHOICES:
            raise ValueError(f'unknown state field: {name}')
        if value is not None:
            data[name] = value
    st = State(**data)
    st.validate()
    return st


def interactions(state: State, definitions: tuple) -> tuple[int, list[dict[str, Any]]]:
    activated = []
    for first, first_value, second, second_value, delta in definitions:
        if getattr(state, first) == first_value and getattr(state, second) == second_value:
            activated.append({'when': [first, first_value, second, second_value], 'points': delta})
    return sum(item['points'] for item in activated), activated

def sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    e = math.exp(value)
    return e / (1 + e)


def bounded_logistic(score: int) -> float:
    """Exact endpoints; a preference index, not a calibrated probability."""
    if not -M <= score <= M:
        raise ValueError('Z outside -M..M')
    if score == -M:
        return 0.0
    if score == M:
        return 1.0
    if score == 0:
        return 0.5
    low, high = sigmoid(-M / TEMPERATURE), sigmoid(M / TEMPERATURE)
    return (sigmoid(score / TEMPERATURE) - low) / (high - low)


def project(state: State, base: int, effects: dict[str, dict[str, int]]) -> tuple[int, dict[str, int]]:
    contributions = {name: choices[getattr(state, name)] for name, choices in effects.items()}
    points = min(SCALE_POINTS, max(0, base + sum(contributions.values())))
    return points, contributions


def oracle(state: State, captain: str | Captain = 'c01',
                 scenario: str | Scenario = 's01') -> dict[str, Any]:
    state.validate()
    profile = resolve_captain(captain)
    xp, x_terms = project(state, profile.x_base, profile.x_effects)
    yp, y_terms = project(state, Y_BASE, Y_EFFECTS)
    # Interactions are added BEFORE clamping, so clamping occurs exactly once.
    x_extra, x_active = interactions(state, profile.x_interactions)
    y_extra, y_active = 0, []  # shared opponent teacher, not jacket-conditioned
    if x_extra:
        xp = min(SCALE_POINTS, max(0, profile.x_base + sum(x_terms.values()) + x_extra))
    if y_extra:
        yp = min(SCALE_POINTS, max(0, Y_BASE + sum(y_terms.values()) + y_extra))
    x = xp * (M // SCALE_POINTS)
    y = yp * (M // SCALE_POINTS)
    z = x - y
    return {
        'x': x, 'y': y, 'op': OPERATION, 'z': z,
        'x_points': xp, 'y_points': yp,
        'x_contributions': x_terms, 'y_contributions': y_terms,
        'x_interactions': x_active, 'y_interactions': y_active,
        'decision_index': bounded_logistic(z),
        'preference': 'contest' if z > 0 else 'yield' if z < 0 else 'indifferent',
    }


# Each state term has a surface form but NO digits, weights, or numerical hints.
# Those remain exclusively in the teacher rubric and early numeric scaffolds.
STATE_ENGLISH: dict[str, dict[str, str]] = {
    'resource_priority': {
        'low': 'The cache offers only limited tactical benefit.',
        'medium': 'The cache would improve our supplies but is not decisive on its own.',
        'high': 'The cache is highly valuable to our operational position.',
    },
    'crew_danger': {
        'low': 'Tactical judges the risk to our crew from contesting the cache to be low.',
        'medium': 'Tactical reports a moderate danger to our crew if we fight for the cache.',
        'high': 'Tactical warns that contesting the cache places the crew in serious danger.',
    },
    'mission_dependency': {
        'off': 'The current mission can succeed without this cache.',
        'on': 'The primary mission depends on securing this cache.',
    },
    'friendly_support': {
        'out-of-range': 'Our supporting ships are outside effective supporting range.',
        'in-range': 'Our supporting ships are within effective supporting range.',
    },
    'decision_horizon': {
        'now': 'The rival is moving to seize the cache now.',
        'soon': 'The rival is preparing to move on the cache soon.',
        'later': 'The rival is not expected to move on the cache until later.',
    },
    'opponent_pressure': {
        'low': 'The opposing commander rarely presses contested claims.',
        'medium': 'The opposing commander has shown mixed willingness to fight over disputed resources.',
        'high': 'The opposing commander usually presses contested claims aggressively.',
    },
    'opponent_escalating': {
        'off': 'There is no evidence of recent escalation by the opposing commander.',
        'on': 'Recent encounters suggest the opposing commander is escalating challenges.',
    },
    'opponent_support': {
        'out-of-range': 'Enemy reinforcements remain outside effective supporting range.',
        'in-range': 'Enemy reinforcements are inside effective supporting range.',
    },
}


def render_state_phrase(scenario: str | Scenario, key: str, value: str) -> str:
    """A common report, independent of which captain reads it."""
    scene = resolve_scenario(scenario)
    if scene.key == 's01':
        return STATE_ENGLISH[key][value]
    asset = scene.asset
    if key == 'resource_priority':
        return {
            'low': f'Securing the {asset} would offer only a minor operational advantage.',
            'medium': f'Securing the {asset} would help, but is not decisive by itself.',
            'high': f'Control of the {asset} would be highly valuable to our operations.',
        }[value]
    if key == 'crew_danger':
        return {
            'low': f'Tactical expects little danger to the crew if we contest the {asset}.',
            'medium': f'Contesting the {asset} would expose our crew to moderate danger.',
            'high': f'Tactical warns that contesting the {asset} places our crew in serious danger.',
        }[value]
    if key == 'mission_dependency':
        return {
            'off': f'Our primary mission can succeed without control of the {asset}.',
            'on': f'Our primary mission depends on securing the {asset}.',
        }[value]
    if key == 'decision_horizon':
        return {
            'now': f'The rival is moving to take control of the {asset} now.',
            'soon': f'The rival may take control of the {asset} soon.',
            'later': f'There is time before the rival can take control of the {asset}.',
        }[value]
    return STATE_ENGLISH[key][value]


def shared_report(state: State, scenario: str | Scenario = 's01') -> str:
    """Identical case text for all jackets in a scenario-state pairing."""
    state.validate()
    scene = resolve_scenario(scenario)
    own = ('resource_priority', 'crew_danger', 'mission_dependency',
           'friendly_support', 'decision_horizon')
    opponent = ('opponent_pressure', 'opponent_escalating', 'opponent_support')
    return (
        'SITUATION\n' + scene.context + '\n'
        + ' '.join(render_state_phrase(scene, key, getattr(state, key)) for key in own)
        + '\n\nOPPONENT INTELLIGENCE\n'
        + ' '.join(render_state_phrase(scene, key, getattr(state, key)) for key in opponent)
        + '\n\nQUESTION\n' + scene.question
    )


def full_prompt(state: State, captain: str | Captain = 'c01',
                scenario: str | Scenario = 's01') -> str:
    """Only the jacket changes between captains; rest of prompt is identical."""
    profile = resolve_captain(captain)
    text = 'CAPTAIN JACKET\n' + profile.jacket + '\n\n' + shared_report(state, scenario)
    assert re.search(r'\d', text) is None, 'raw prompt unexpectedly contains a numeral'
    return text


@dataclass(frozen=True)
class Rung:
    level: int
    key: str
    description: str
    prompt: str
    x_support: str
    y_support: str
    op_support: str


def condensed_story(state: State, captain: str | Captain = 'c01',
                    scenario: str | Scenario = 's01') -> str:
    """Shorter text retaining the shared scenario and all categorical state."""
    state.validate()
    profile = resolve_captain(captain)
    scene = resolve_scenario(scenario)
    return (
        'JACKET: ' + profile.jacket_short + '\n'
        'SITUATION: ' + scene.context + ' '
        f'Resource priority is {state.resource_priority}; crew danger is {state.crew_danger}; '
        f'mission dependency is {state.mission_dependency}; friendly support is '
        f'{state.friendly_support}; decision horizon is {state.decision_horizon}.\n'
        'OPPONENT: Their pressure is '
        f'{state.opponent_pressure}; escalation is {state.opponent_escalating}; '
        f'their support is {state.opponent_support}.\n'
        'QUESTION: ' + scene.question
    )


def structured_story(state: State, captain: str | Captain = 'c01',
                     scenario: str | Scenario = 's01') -> str:
    """Condense prose to typed qualitative variables without numeric targets."""
    state.validate()
    profile = resolve_captain(captain)
    scene = resolve_scenario(scenario)
    pairs = asdict(state)
    lines = [
        'JACKET:',
        '  doctrine = ' + profile.jacket_short,
        'SITUATION: ' + scene.context,
        'OUR STATE:',
    ]
    lines.extend(f'  {k} = {pairs[k]}' for k in X_EFFECTS)
    lines.append('OPPONENT EVIDENCE:')
    lines.extend(f'  {k} = {pairs[k]}' for k in Y_EFFECTS)
    lines.extend(['QUESTION: ' + scene.question])
    return '\n'.join(lines)


def build_ladder(state: State, labels: dict[str, Any], focus: str = 'y',
                 captain: str | Captain = 'c01',
                 scenario: str | Scenario = 's01') -> list[Rung]:
    """Compile one canonical problem BACKWARD from raw prose to numeric aids.

    Numbering remains L00 (easiest) through L10 (hardest) for stable IDs.
    Printed presentation is reversed to make the condensation visible.
    """
    if focus not in ('x', 'y'):
        raise ValueError('focus must be x or y')
    profile = resolve_captain(captain)
    scene = resolve_scenario(scenario)
    x, y = labels['x'], labels['y']
    raw = full_prompt(state, profile, scene)
    concise = condensed_story(state, profile, scene)
    structured = structured_story(state, profile, scene)

    def attach(context: str, *aids: str) -> str:
        return '\n'.join(aids) + '\n\n' + context

    numeric_x, numeric_y = f'X = {x}', f'Y = {y}'
    function_x = 'X = J(captain jacket, situation)'
    function_y = 'Y = K(opponent evidence, situation)'
    role_x = "X = our captain's willingness to contest"
    role_y = "Y = opponent's estimated willingness to contest"
    op = 'OP = SUB (X - Y)'
    mini_context = f'CASE: {scene.asset} contest; choices: contest or yield'
    if focus == 'y':
        first_aid = (numeric_x, function_y)
        second_aid = (numeric_x, role_y)
        last_role = role_x
    else:
        first_aid = (function_x, numeric_y)
        second_aid = (role_x, numeric_y)
        last_role = role_y

    rows = [
        Rung(0, 'numeric_baseline', 'Only X/Y/OP numerical scaffolding remains',
             '\n'.join((f'X = {x}, {logbar(x)}', f'Y = {y}, {logbar(y)}', op, 'Answer:')),
             'logbar+numeric', 'logbar+numeric', 'explicit'),
        Rung(1, 'story_full_logbar', 'A brief context tag plus both numerical LogBars',
             attach(mini_context, f'X = {x}, {logbar(x)}', f'Y = {y}, {logbar(y)}', op),
             'logbar+numeric', 'logbar+numeric', 'explicit'),
        Rung(2, 'story_one_logbar', 'Brief context; one LogBar removed',
             attach(mini_context,
                    f'X = {x}, {logbar(x)}' if focus == 'y' else numeric_x,
                    numeric_y if focus == 'y' else f'Y = {y}, {logbar(y)}', op),
             'numeric+logbar' if focus == 'y' else 'numeric',
             'numeric' if focus == 'y' else 'numeric+logbar', 'explicit'),
        Rung(3, 'story_numeric_registers', 'Brief context; numeric registers only',
             attach(mini_context, numeric_x, numeric_y, op),
             'numeric', 'numeric', 'explicit'),
        Rung(4, 'story_first_function', 'One value explicit; other via J/K and categorical state',
             attach(structured, *first_aid, op),
             'numeric' if focus == 'y' else 'function',
             'function' if focus == 'y' else 'numeric', 'explicit'),
        Rung(5, 'story_first_semantic', 'One value explicit; other given a semantic role',
             attach(structured, *second_aid, op),
             'numeric' if focus == 'y' else 'semantic',
             'semantic' if focus == 'y' else 'numeric', 'explicit'),
        Rung(6, 'story_both_functions', 'Categorical state and jacket as structured evidence',
             attach(structured, function_x, function_y, op),
             'function', 'function', 'explicit'),
        Rung(7, 'story_both_semantic', 'Condensed English with X/Y roles and OP',
             attach(concise, role_x, role_y, op),
             'semantic', 'semantic', 'explicit'),
        Rung(8, 'story_infer_op', 'Condensed English with only X/Y roles',
             attach(concise, role_x, role_y),
             'semantic', 'semantic', 'inferred'),
        Rung(9, 'story_one_semantic', 'Condensed English with one semantic role',
             attach(concise, last_role),
             'semantic' if focus == 'y' else 'none',
             'none' if focus == 'y' else 'semantic', 'inferred'),
        Rung(10, 'story_raw', 'Full captain jacket and situation in natural English',
             raw, 'none', 'none', 'inferred'),
    ]
    assert [row.level for row in rows] == list(range(11))
    assert rows[-1].prompt == raw
    assert all(str(v) not in raw for v in (f'LogBar({x})', f'LogBar({y})'))
    return rows

def rubric_info(captain: str | Captain = 'c01',
                scenario: str | Scenario = 's01') -> dict[str, Any]:
    profile = resolve_captain(captain)
    scene = resolve_scenario(scenario)
    return {
        'authority': 'synthetic deterministic teacher; not empirically validated behavior',
        'scope': scene.key,
        'scenario_id': scene.key,
        'x_teacher': 'captain-specific synthetic rubric',
        'y_teacher': 'shared-opponent-v1 (independent of jacket)',
        'principle': profile.rubric_principle,
        'x_meaning': 'captain willingness to contest, scaled 0..2000000',
        'y_meaning': 'estimated opponent willingness to contest, scaled 0..2000000',
        'x_base_points': profile.x_base,
        'x_effects': profile.x_effects,
        'x_interactions': profile.x_interactions,
        'y_base_points': Y_BASE,
        'y_effects': Y_EFFECTS,
        'y_interactions': (),
        'calculation': 'sum baseline and qualitative effects and active interactions; clamp to [0,100] points; multiply by 20000',
        'op': OPERATION,
        'z_rule': 'X-Y',
        'decision_index': 'endpoint-normalized logistic preference index; not probability',
        'teacher_only': 'no rubric weights or numeric outcomes appear in the raw question',
        'control': 'Only jacket changes within a scenario; X captain-specific; Y identical',
    }


def build_output(state: State, focus: str = 'y',
                 captain: str | Captain = 'c01',
                 scenario: str | Scenario = 's01') -> dict[str, Any]:
    profile = resolve_captain(captain)
    scene = resolve_scenario(scenario)
    targets = oracle(state, profile)
    return {
        'schema': SCHEMA,
        'captain_id': profile.key,
        'captain_title': profile.title,
        'jacket_id': JACKET_ID if profile.key == 'c01' else profile.key,
        'jacket': profile.jacket,
        'scenario_id': scene.key,
        'scenario_title': scene.title,
        'question_id': scene.key + '_hawk_dove',
        'question': scene.question,
        'state': asdict(state),
        'rubric': rubric_info(profile, scene), 'targets': targets,
        'focus': focus,
        'rungs': [asdict(rung) for rung in build_ladder(state, targets, focus, profile, scene)],
    }





def head_to_head_story(state: State, our_captain: str | Captain,
                       opposing_captain: str | Captain,
                       scenario: str | Scenario = 's01') -> str:
    """Pair-specific raw input exposing *both* jackets and common observations.

    Unlike the crossed-control stories (where opponent Y is shared), the
    head-to-head teacher uses the opponent's jacket. It must therefore be
    available to a prospective model input; hiding it would leak labels.
    """
    own = resolve_captain(our_captain)
    opponent = resolve_captain(opposing_captain)
    if own.key == opponent.key:
        raise ValueError('head-to-head story needs two different captains')
    story = ('OUR CAPTAIN JACKET\n' + own.jacket
             + '\n\nOPPOSING CAPTAIN JACKET\n' + opponent.jacket
             + '\n\n' + shared_report(state, scenario))
    assert re.search(r'\d', story) is None, 'raw head-to-head story contains a numeral'
    return story


def matrix_captain_policy(state: State, captain: str | Captain) -> dict[str, Any]:
    """Synthetic *unopposed* contest propensity, not an actual tactical action.

    The existing shared opponent rubric supplies a common resistance baseline Y0.
    A captain's own X is projected against Y0 to obtain a proxy for its likelihood
    of contesting. The proxy is used as an opponent model by other captains.
    No recursive best response or Nash equilibrium is claimed.
    """
    profile = resolve_captain(captain)
    own = oracle(state, profile)
    baseline = own['y']
    predicted_hawk_probability = bounded_logistic(own['x'] - baseline)
    return {
        'captain_id': profile.key,
        'captain_title': profile.title,
        'x': own['x'],
        'shared_opponent_baseline_y': baseline,
        'pilot_contest_index': predicted_hawk_probability,
        'projected_resistance_y': round(M * predicted_hawk_probability),
    }


def matrix_action(x: int, predicted_opponent_y: int) -> str:
    """H=contest, D=yield, I=rubric indifference; no coin flip at ties."""
    if x > predicted_opponent_y:
        return 'H'
    if x < predicted_opponent_y:
        return 'D'
    return 'I'


def build_matrix(state: State, scenario: str | Scenario = 's01') -> dict[str, Any]:
    """One complete 10x10 directional display / 45 distinct unordered matchups.

    A's estimate of B is B's provisional *pilot* contest propensity. B's
    estimate of A is constructed symmetrically. Both choose simultaneously;
    this is a deterministic smoke teacher, not a trained opponent predictor.
    """
    state.validate()
    scene = resolve_scenario(scenario)
    policies = {c.key: matrix_captain_policy(state, c) for c in CAPTAINS}
    keys = [c.key for c in CAPTAINS]
    cells = {key: {} for key in keys}
    summaries = {key: {'contests': 0, 'yields': 0, 'indifferent': 0,
                       'mutual_contests': 0, 'mutual_yields': 0,
                       'unilateral_contests': 0, 'unilateral_yields': 0}
                 for key in keys}
    matches = []
    for i, first_id in enumerate(keys):
        a = policies[first_id]
        for second_id in keys[i+1:]:
            b = policies[second_id]
            a_y = b['projected_resistance_y']
            b_y = a['projected_resistance_y']
            a_action = matrix_action(a['x'], a_y)
            b_action = matrix_action(b['x'], b_y)
            code = a_action + b_action
            if 'I' in code:
                outcome = 'indeterminate'
            else:
                outcome = {'HH': 'confrontation', 'HD': 'first_contests_second_yields',
                           'DH': 'first_yields_second_contests',
                           'DD': 'mutual_yield'}[code]
            match = {
                'first': first_id, 'second': second_id,
                'first_x': a['x'], 'first_y': a_y,
                'first_z': a['x'] - a_y, 'first_action': a_action,
                'second_x': b['x'], 'second_y': b_y,
                'second_z': b['x'] - b_y, 'second_action': b_action,
                'first_opponent_contest_index': b['pilot_contest_index'],
                'second_opponent_contest_index': a['pilot_contest_index'],
                'code': code, 'outcome': outcome,
            }
            matches.append(match)
            cells[first_id][second_id] = code
            cells[second_id][first_id] = code[::-1]
            for captain_id, own_action, other_action in (
                (first_id, a_action, b_action),
                (second_id, b_action, a_action),
            ):
                tally = summaries[captain_id]
                tally[{'H': 'contests', 'D': 'yields', 'I': 'indifferent'}[own_action]] += 1
                if own_action == other_action == 'H':
                    tally['mutual_contests'] += 1
                elif own_action == other_action == 'D':
                    tally['mutual_yields'] += 1
                elif own_action == 'H' and other_action == 'D':
                    tally['unilateral_contests'] += 1
                elif own_action == 'D' and other_action == 'H':
                    tally['unilateral_yields'] += 1
    for captain_id in keys:
        cells[captain_id][captain_id] = '--'  # excludes self-play
    counts = {kind: sum(match['outcome'] == kind for match in matches)
              for kind in ('confrontation', 'first_contests_second_yields',
                           'first_yields_second_contests', 'mutual_yield',
                           'indeterminate')}
    return {
        'schema': MATRIX_SCHEMA,
        'scenario_id': scene.key, 'scenario_title': scene.title,
        'shared_question': scene.question, 'state': asdict(state),
        'captains': [{'id': c.key, 'title': c.title, 'jacket': c.jacket, **policies[c.key]}
                     for c in CAPTAINS],
        'cells': cells, 'matches': matches, 'summary': summaries,
        'matchup_counts': counts,
        'teacher': {
            'authority': 'synthetic deterministic hypothesis; no model executed',
            'our_x': 'existing jacket-specific X rubric for the common state',
            'opponent_base_y': 'existing shared opponent evidence projection Y0',
            'pilot_policy': 'bounded_logistic(X_opponent - Y0)',
            'anticipated_opponent_y': 'round(2_000_000 * pilot_policy_opponent)',
            'decision': 'contest when own X > anticipated opponent Y; yield when lower',
            'interpretation': 'simultaneous proxy choices, not Nash equilibrium or observed behavior',
            'self_play': 'excluded',
            'information': 'both captain jackets must be present in a paired raw prompt',
        },
    }


def print_matrix(result: dict[str, Any], *, pair: str | None = None) -> None:
    """Print matrix cells as [row action, column action] in that exact order."""
    keys = [profile.key for profile in CAPTAINS]
    print('HAWK-DOVE CAPTAIN HEAD-TO-HEAD MATRIX — SYNTHETIC TEACHER')
    print(f'SCENARIO {result["scenario_id"]}: {result["scenario_title"]}')
    print('One common scenario and categorical state for everyone.')
    print('Cell = row captain action / column captain action; H=contest, D=yield, I=indifferent, --=self.')
    print('Opponent prediction: rival jacket X -> provisional contest index -> this captain\'s Y.')
    print(' ' * 6 + ''.join(f'{key:>6}' for key in keys))
    for a_id in keys:
        print(f'{a_id:<6}' + ''.join(f'{result["cells"][a_id][b_id]:>6}' for b_id in keys))
    print('\nPER-CAPTAIN RESULTS (9 distinct opponents each)')
    print(f'{"ID":<5} {"JACKET":<29} {"X":>10} {"H":>3} {"D":>3} {"I":>3} {"HH":>4} {"HD":>4} {"DH":>4} {"DD":>4}')
    by_id = {row['id']: row for row in result['captains']}
    for key in keys:
        q = result['summary'][key]
        cap = by_id[key]
        print(f'{key:<5} {cap["title"][:29]:<29} {cap["x"]:>10,} '
              f'{q["contests"]:>3} {q["yields"]:>3} {q["indifferent"]:>3} '
              f'{q["mutual_contests"]:>4} {q["unilateral_contests"]:>4} '
              f'{q["unilateral_yields"]:>4} {q["mutual_yields"]:>4}')
    counts = result['matchup_counts']
    print('\nUNORDERED MATCHUPS: 45 (self-play excluded)')
    print('Mutual contests: {confrontation} | One-sided contests: {one} | Mutual yields: '
          '{mutual_yield} | Indeterminate: {indeterminate}'.format(
              one=(counts['first_contests_second_yields'] + counts['first_yields_second_contests']),
              **counts))
    print('All choices are a deterministic teacher demonstration, not learned play or optimized strategies.')
    if pair is not None:
        a_id, b_id = parse_matrix_pair(pair)
        match = next((m for m in result['matches']
                      if {m['first'], m['second']} == {a_id, b_id}), None)
        assert match is not None
        if match['first'] != a_id:
            match = {
                'first': a_id, 'second': b_id,
                'first_x': match['second_x'], 'first_y': match['second_y'],
                'first_z': match['second_z'], 'first_action': match['second_action'],
                'second_x': match['first_x'], 'second_y': match['first_y'],
                'second_z': match['first_z'], 'second_action': match['first_action'],
                'first_opponent_contest_index': match['second_opponent_contest_index'],
                'second_opponent_contest_index': match['first_opponent_contest_index'],
                'outcome': match['outcome'],
            }
        print(f'\nPAIR DETAIL {a_id} vs {b_id} — {result["scenario_id"]}')
        for prefix, captain_id in (('first', a_id), ('second', b_id)):
            print(f'  {captain_id}: X={match[prefix+"_x"]:,}; '
                  f'Y={match[prefix+"_y"]:,}; Z={match[prefix+"_z"]:+,}; '
                  f'opponent contest index={match[prefix+"_opponent_contest_index"]:.4f}; '
                  f'action={match[prefix+"_action"]}')
        print('  Shared question:', result['shared_question'])
        print('  Same state:', json.dumps(result['state'], sort_keys=True))
        state = State(**result['state'])
        print('\n  RAW HEAD-TO-HEAD PROBLEM (FIRST CAPTAIN PERSPECTIVE)')
        print(head_to_head_story(state, a_id, b_id, result['scenario_id']))
        print('\n  Both jackets are visible: this teacher assumes knowledge of the rival jacket.')


def parse_matrix_pair(value: str) -> tuple[str, str]:
    parts = value.lower().split(':')
    if len(parts) != 2 or parts[0] not in BY_ID or parts[1] not in BY_ID or parts[0] == parts[1]:
        raise ValueError('--matrix-pair must be two distinct captain IDs, e.g. c01:c03')
    return parts[0], parts[1]


def self_test() -> None:
    s = State()
    r = build_output(s)
    t = r['targets']
    assert (t['x'], t['y'], t['z']) == (380_000, 1_660_000, -1_280_000)
    assert len(r['rungs']) == 11
    raw = r['rungs'][-1]['prompt']
    assert not re.search(r'\d', raw)
    assert r['rungs'][9]['prompt'] != raw
    assert 'resource_priority = ' in r['rungs'][6]['prompt']
    assert logbar(t['x']) in r['rungs'][0]['prompt']
    assert logbar(t['y']) in r['rungs'][0]['prompt']
    assert all('X = J(' in row['prompt'] or 'X = ' in row['prompt'] for row in r['rungs'][4:7])
    assert oracle(replace(s, resource_priority='high'))['x'] > t['x']
    assert oracle(replace(s, crew_danger='low'))['x'] > t['x']
    assert oracle(replace(s, opponent_pressure='low'))['y'] < t['y']
    assert oracle(replace(s, opponent_support='out-of-range'))['y'] < t['y']
    assert bounded_logistic(-M) == 0 and bounded_logistic(0) == 0.5 and bounded_logistic(M) == 1
    assert len(CAPTAINS) == 10
    assert len({profile.jacket for profile in CAPTAINS}) == 10
    assert len({oracle(s, profile)['y'] for profile in CAPTAINS}) == 1
    for scene in SCENARIOS:
        state = scenario_state(scene)
        common_report = shared_report(state, scene)
        assert all(shared_report(state, scene) == common_report for profile in CAPTAINS)
    for profile in CAPTAINS:
        labels = oracle(s, profile)
        raw_case = full_prompt(s, profile)
        assert not re.search(r'\d', raw_case)
        assert profile.jacket in raw_case and QUESTION in raw_case
        assert len(build_ladder(s, labels, 'x', profile)) == 11
        assert len(build_ladder(s, labels, 'y', profile)) == 11
    for bad in (replace(s, crew_danger='severe'), replace(s, friendly_support='nearby')):
        try:
            oracle(bad)
        except ValueError:
            pass
        else:
            raise AssertionError('bad categorical state accepted')


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--captain', choices=tuple(BY_ID), default='c01',
                   help='select one jacket within a shared scenario')
    p.add_argument('--list-captains', action='store_true', help='list the ten captain jackets')
    p.add_argument('--scenario', choices=tuple(SCENARIO_BY_ID), default='s01',
                   help='same situation/question for every selected captain')
    p.add_argument('--list-scenarios', action='store_true', help='list available shared scenarios')
    p.add_argument('--all-scenarios', action='store_true',
                   help='evaluate each scenario against the selected captains, scenario-first')
    p.add_argument('--all-captains', action='store_true',
                   help='print or serialize all ten captains using the same state')
    for name, allowed in STATE_CHOICES.items():
        p.add_argument('--' + name.replace('_', '-'), choices=allowed,
                       default=None,
                       help=f'categorical state: {" / ".join(allowed)}')
    p.add_argument('--focus', choices=('x', 'y'), default='y',
                   help='which operand loses numeric aid first')
    p.add_argument('--level', type=int, choices=range(11), default=None, metavar='0..10')
    p.add_argument('--format', choices=('text', 'json'), default='text')
    p.add_argument('--show-rubric', action='store_true', help='include synthetic teacher weights')
    p.add_argument('--matrix', action='store_true',
                   help='head-to-head 10x10 matrix: every captain vs every other captain')
    p.add_argument('--matrix-pair', default=None,
                   help='with --matrix, explain one ordered pair, e.g. c01:c03')
    p.add_argument('--self-test', action='store_true')
    return p


def print_case(out: dict[str, Any], *, level: int | None, show_rubric: bool) -> None:
    t = out['targets']
    print('HAWK-DOVE CAPTAIN LADDER — RAW STORY TO NUMERICAL CONDENSATION')
    print(f'SCENARIO {out["scenario_id"]} — {out["scenario_title"]}')
    print(f'{out["captain_id"]} {out["captain_title"]} | common question={out["question_id"]} | focus={out["focus"]}')
    raw = out['rungs'][-1]
    print('\n' + '=' * 76)
    print('L10  RAW CAPTAIN STORY — THE TARGET PRESENTATION')
    print('=' * 76)
    print(raw['prompt'])
    for rung in reversed(out['rungs'][:-1]):
        if level is not None and level != rung['level']:
            continue
        print('\n' + '=' * 76)
        print(f'L{rung["level"]:02d}  {rung["key"]} — {rung["description"]}')
        print(f'AIDS: X={rung["x_support"]}; Y={rung["y_support"]}; OP={rung["op_support"]}')
        print('-' * 76)
        print(rung['prompt'])
    print('\n' + '=' * 76)
    print('TEACHER TARGETS — NOT PART OF ANY RAW STORY')
    print(f'X={t["x"]:,}  Y={t["y"]:,}  OP=SUB  Z={t["z"]:,}')
    print(f'DECISION INDEX={t["decision_index"]:.6f}  PREFERENCE={t["preference"]}')
    print('STATE:', json.dumps(out['state'], sort_keys=True))
    if show_rubric:
        print('\nRUBRIC (synthetic, teacher only):')
        print(json.dumps(out['rubric'], indent=2, sort_keys=True, ensure_ascii=False))
    print('No model, training, or database operations are performed.')


def print_comparison(scene: Scenario, outputs: list[dict[str, Any]]) -> None:
    """Make the controlled comparison explicit after the raw-first ladders."""
    print('\n' + '=' * 76)
    print(f'CROSSED COMPARISON — {scene.key}: {scene.title}')
    print('All rows share the exact same scenario, state, opponent evidence and question.')
    print('Only the jacket and its X teacher change. Y uses one common opponent teacher.')
    print(f'{"CAPTAIN":<9} {"JACKET":<29} {"X":>10} {"Y":>10} {"Z":>11}  DECISION')
    for output in outputs:
        t = output['targets']
        print(f'{output["captain_id"]:<9} {output["captain_title"][:29]:<29} '
              f'{t["x"]:>10,} {t["y"]:>10,} {t["z"]:>11,}  {t["preference"]}')
    assert len({output['targets']['y'] for output in outputs}) == 1


def run(args: argparse.Namespace) -> int:
    if args.self_test:
        self_test()
        print('HAWK_DOVE_QUALITATIVE_LADDER_SELF_TEST: PASS')
        return 0
    if args.matrix_pair and not args.matrix:
        parser().error('--matrix-pair requires --matrix')
    if args.matrix_pair:
        try:
            parse_matrix_pair(args.matrix_pair)
        except ValueError as exc:
            parser().error(str(exc))
    if args.matrix:
        scenes = SCENARIOS if args.all_scenarios else (resolve_scenario(args.scenario),)
        matrices = [build_matrix(scenario_state(scene, **{key: getattr(args, key)
                             for key in STATE_CHOICES}), scene) for scene in scenes]
        if args.format == 'json':
            print(json.dumps(matrices if args.all_scenarios else matrices[0],
                             indent=2, sort_keys=True, ensure_ascii=False))
        else:
            for i, matrix in enumerate(matrices):
                if i:
                    print('\n' + '#' * 76 + '\n')
                print_matrix(matrix, pair=args.matrix_pair)
        return 0
    if args.list_captains:
        for profile in CAPTAINS:
            print(f'{profile.key}: {profile.title}')
        return 0
    if args.list_scenarios:
        for scene in SCENARIOS:
            print(f'{scene.key}: {scene.title} | {scene.question}')
        return 0
    selected_captains = CAPTAINS if args.all_captains else (resolve_captain(args.captain),)
    selected_scenarios = SCENARIOS if args.all_scenarios else (resolve_scenario(args.scenario),)
    groups = []
    try:
        for scene in selected_scenarios:
            state = scenario_state(scene, **{key: getattr(args, key) for key in STATE_CHOICES})
            outputs = [build_output(state, args.focus, captain, scene)
                       for captain in selected_captains]
            if args.format == 'json':
                for item in outputs:
                    if args.level is not None:
                        item['rungs'] = [r for r in item['rungs'] if r['level'] == args.level]
                    if not args.show_rubric:
                        item.pop('rubric')
            groups.append((scene, outputs))
    except ValueError as exc:
        parser().error(str(exc))
    if args.format == 'json':
        if args.all_scenarios:
            value = [{'scenario_id': scene.key, 'state': group[0]['state'],
                      'captains': group} for scene, group in groups]
        else:
            group = groups[0][1]
            value = group if args.all_captains else group[0]
        print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    for index, (scene, outputs) in enumerate(groups):
        if index:
            print('\n' + '#' * 76 + '\n')
        print(f'SCENARIO {scene.key} — {scene.title} (shared-question comparison)')
        for i, item in enumerate(outputs):
            if i:
                print('\n' + '-' * 76 + '\n')
            print_case(item, level=args.level, show_rubric=args.show_rubric)
        if args.all_captains:
            print_comparison(scene, outputs)
    return 0


if __name__ == '__main__':
    raise SystemExit(run(parser().parse_args()))
