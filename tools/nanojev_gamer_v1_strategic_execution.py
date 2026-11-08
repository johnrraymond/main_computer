#!/usr/bin/env python3
"""Bounded, read-only GAMER strategic execution for explicit two-player 2x2 games.

This module DOES NOT learn to parse games. It accepts a narrow, fully anchored
English payoff description or a strict JSON game specification and executes
payoff comparisons using GAMER's shared exact factor-DAG arithmetic. It does not
invoke, train, or promote the active to_numerical serving checkpoint. Unsupported
or ambiguous natural-language inputs fail closed, never guess game rules.
"""
from __future__ import annotations

from fractions import Fraction
from decimal import Decimal
import json
import re

from tools import nanojev_gamer_v1_pairs as pairs

SCHEMA = 'nanojev.gamer.v1.bounded-two-player-exact-strategic-execution.v1'
BACKEND = 'GAMER_SOURCE_FACTOR_DAG_EXACT_FRACTION_NOT_PRODUCTION_HEAD'
NUMBER = r'[+-]?(?:\d+(?:\.\d+)?|\d+/\d+)'

# A deliberately *closed* grammar. Do not use re.search or read arbitrary
# numbers from prose: doing so can silently misinterpret altered rules.
_ENGLISH = re.compile(
    rf'\s*Two (?:competing )?players (?:may|can) cooperate or defect\.\s*'
    rf'Mutual cooperation pays each player (?P<cc>{NUMBER})\.\s*'
    rf'If one defects while the other cooperates, the defector (?:gets|earns|receives) '
    rf'(?P<dc>{NUMBER}) and the cooperator (?:gets|earns|receives) (?P<cd>{NUMBER})\.\s*'
    rf'Mutual defection pays each (?P<dd>{NUMBER})\.\s*'
    rf'(?:(?:Now )?impose a penalty of (?P<penalty>{NUMBER}) '
    rf'on (?:any|each) player who defects\.\s*|'
    rf'A penalty of (?P<penalty_alt>{NUMBER}) is imposed on each player who defects\.\s*)?'
    rf'(?:What is each player\'s dominant strategy(?: before and after the penalty)?\?|'
    rf'Which action dominates for each player(?: before and after the penalty)?\?)\s*',
    re.IGNORECASE,
)


def _fraction(value):
    """Exact human decimal/rational; reject booleans, NaNs, huge inputs."""
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        raise ValueError('GAMER_STRATEGY_BAD_PAYOFF_NUMBER')
    token = str(value)
    if len(token) > 32 or not re.fullmatch(NUMBER, token):
        raise ValueError('GAMER_STRATEGY_BAD_PAYOFF_NUMBER')
    try:
        return Fraction(token)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError('GAMER_STRATEGY_BAD_PAYOFF_NUMBER') from exc


def _normalise_json(raw):
    """Strict public 2x2 normal-form game: no target labels as input."""
    if type(raw) is not dict or set(raw) - {'players', 'actions', 'payoffs', 'penalty'}:
        raise ValueError('GAMER_STRATEGY_JSON_UNEXPECTED_FIELDS')
    players = raw.get('players', ['player_1', 'player_2'])
    actions = raw.get('actions')
    if (not isinstance(players, list) or len(players) != 2 or
            not all(type(x) is str and 1 <= len(x) <= 60 for x in players) or
            len(set(x.casefold() for x in players)) != 2):
        raise ValueError('GAMER_STRATEGY_INVALID_PLAYERS')
    if (not isinstance(actions, list) or len(actions) != 2 or
            any(not isinstance(a, list) or len(a) != 2 or
                any(type(v) is not str or not 1 <= len(v) <= 60 for v in a) or
                a[0].casefold() == a[1].casefold() for a in actions)):
        raise ValueError('GAMER_STRATEGY_INVALID_ACTIONS')
    cells = raw.get('payoffs')
    if (not isinstance(cells, list) or len(cells) != 2 or
            any(not isinstance(row, list) or len(row) != 2 or
                any(not isinstance(cell, list) or len(cell) != 2 for cell in row)
                for row in cells)):
        raise ValueError('GAMER_STRATEGY_INVALID_2X2_PAYOFF_TABLE')
    matrix = [[[_fraction(x) for x in cell] for cell in row] for row in cells]
    penalty = raw.get('penalty')
    if penalty is not None:
        if (type(penalty) is not dict or set(penalty) != {'action', 'amount'}
                or type(penalty.get('action')) is not str):
            raise ValueError('GAMER_STRATEGY_INVALID_PENALTY')
        affected = penalty['action']
        if any(affected not in a for a in actions):
            raise ValueError('GAMER_STRATEGY_PENALTY_ACTION_NOT_SHARED')
        amount = _fraction(penalty['amount'])
        if amount < 0:
            raise ValueError('GAMER_STRATEGY_NEGATIVE_PENALTY_NOT_SUPPORTED')
        penalty = {'action': affected, 'amount': amount}
    return {'players': players, 'actions': actions, 'payoffs': matrix,
            'penalty': penalty, 'input_format': 'strict_json_normal_form'}


def _reject_duplicate_keys(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('GAMER_STRATEGY_JSON_DUPLICATE_FIELD:' + name)
        result[name] = value
    return result


def parse_game(prompt):
    """Return an explicitly extracted game or None for unsupported language."""
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16384:
        return None
    if prompt.lstrip().startswith('{'):
        try:
            raw = json.loads(prompt, parse_float=Decimal, object_pairs_hook=_reject_duplicate_keys)
        except json.JSONDecodeError as exc:
            raise ValueError('GAMER_STRATEGY_INVALID_JSON_GAME') from exc
        return _normalise_json(raw)
    match = _ENGLISH.fullmatch(prompt)
    if not match:
        return None
    payoffs = {k: _fraction(match[k]) for k in ('cc', 'dc', 'cd', 'dd')}
    penalty_text = match['penalty'] or match['penalty_alt']
    if penalty_text is None and re.search(r'before and after the penalty', prompt, re.I):
        # The request mentions a penalty but no value was supplied.
        return None
    penalty = None
    if penalty_text is not None:
        amount = _fraction(penalty_text)
        if amount < 0:
            raise ValueError('GAMER_STRATEGY_NEGATIVE_PENALTY_NOT_SUPPORTED')
        penalty = {'action': 'defect', 'amount': amount}
    return {
        'players': ['player_1', 'player_2'],
        'actions': [['cooperate', 'defect'], ['cooperate', 'defect']],
        'payoffs': [
            [[payoffs['cc'], payoffs['cc']], [payoffs['cd'], payoffs['dc']]],
            [[payoffs['dc'], payoffs['cd']], [payoffs['dd'], payoffs['dd']]],
        ],
        'penalty': penalty,
        'input_format': 'closed_english_symmetric_cooperate_defect',
    }


def _sub(left, right, trace, purpose):
    # Use the GAMER factor-DAG operation implementation. Exact Fraction; no
    # decimal float rounding and no memorized expected answers.
    step = {'step': 1, 'left': str(left), 'operation': 'subtract', 'right': str(right)}
    result = pairs.evaluate_factors([step], 1)
    trace.append({'purpose': purpose, 'operation': 'subtract',
                  'left': str(left), 'right': str(right), 'result': str(result)})
    return result


def _payoff_table(game, *, apply_penalty, trace):
    original = game['payoffs']
    penalty = game['penalty'] if apply_penalty else None
    result = []
    for i in range(2):
        row = []
        for j in range(2):
            cell = []
            for player, action in enumerate((i,j)):
                value = original[i][j][player]
                if penalty and game['actions'][player][action] == penalty['action']:
                    value = _sub(value, penalty['amount'], trace,
                                 f'penalty.player_{player+1}.cell_{i}{j}')
                cell.append(value)
            row.append(cell)
        result.append(row)
    return result


def _dominance(table, game, player, trace):
    values = []
    for opponent_action in range(2):
        if player == 0:
            left = table[0][opponent_action][0]
            right = table[1][opponent_action][0]
        else:
            left = table[opponent_action][0][1]
            right = table[opponent_action][1][1]
        values.append(_sub(left, right, trace,
                           f'dominance.player_{player+1}.against_opponent_{opponent_action}'))
    if all(v > 0 for v in values):
        kind, index = 'strict', 0
    elif all(v < 0 for v in values):
        kind, index = 'strict', 1
    elif all(v == 0 for v in values):
        kind, index = 'indifferent', None
    elif all(v >= 0 for v in values):
        kind, index = 'weak', 0
    elif all(v <= 0 for v in values):
        kind, index = 'weak', 1
    else:
        kind, index = 'none', None
    return {
        'dominance': kind,
        'action': game['actions'][player][index] if index is not None else None,
        'action_index': index,
        'action_0_minus_action_1_by_opponent_action': [str(v) for v in values],
        'opponent_actions': list(game['actions'][1-player]),
    }


def _nash(table, game):
    equilibria = []
    for i in range(2):
        for j in range(2):
            if (table[i][j][0] >= table[1-i][j][0] and
                    table[i][j][1] >= table[i][1-j][1]):
                equilibria.append({'actions': [game['actions'][0][i], game['actions'][1][j]],
                                   'payoffs': [str(x) for x in table[i][j]]})
    return equilibria


def execute(prompt):
    """Exact bounded decision, or a transparent unsupported status."""
    game = parse_game(prompt)
    if game is None:
        return {
            'schema': SCHEMA, 'status': 'UNSUPPORTED_GAME_SPECIFICATION_NO_DECISION',
            'reason': ('Requires either the supported complete symmetric cooperate/defect '
                       'payoff wording or a strict 2x2 JSON normal-form game. '
                       'No rules or payoffs are inferred from game names.'),
            'strategic_decision': None,
            'exact_numerical_backend': BACKEND,
            'production_authority_changed': False,
        }
    phases = {}
    trace = []
    for name, apply_penalty in [('before',False),('after',True)] if game['penalty'] is not None else [('base',False)]:
        table = _payoff_table(game, apply_penalty=apply_penalty, trace=trace)
        dominance = {game['players'][player]: _dominance(table, game, player, trace)
                     for player in range(2)}
        phases[name] = {
            'payoffs_by_player_actions': [
                [[str(x) for x in cell] for cell in row] for row in table],
            'dominant_strategies': dominance,
            'pure_nash_equilibria': _nash(table, game),
        }
    return {
        'schema': SCHEMA, 'status': 'BOUNDED_EXACT_TWO_PLAYER_DECISION_COMPUTED',
        'input_format': game['input_format'],
        'players': game['players'], 'actions': game['actions'],
        'original_payoff_table': [[[str(x) for x in cell] for cell in row] for row in game['payoffs']],
        'penalty': ({'action':game['penalty']['action'],'amount':str(game['penalty']['amount'])}
                    if game['penalty'] else None),
        'phases': phases,
        'strategic_decision': {name: phase['dominant_strategies']
                               for name, phase in phases.items()},
        'arithmetic_trace': trace,
        'exact_numerical_backend': BACKEND,
        'independent_oracle_from_sqlite_used': False,
        'model_checkpoint_used_for_decision': False,
        'claim_of_trained_neural_strategic_reasoning': False,
        'production_authority_changed': False,
    }
