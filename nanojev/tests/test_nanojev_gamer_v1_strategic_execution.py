"""Actual bounded game decisions, exact arithmetic, no target-label lookups."""
from __future__ import annotations

from fractions import Fraction
from itertools import product
import json

import pytest

from tools import nanojev_gamer_v1_strategic_execution as solver


PROMPT = ("Two competing players may cooperate or defect. Mutual cooperation pays each player 4. "
          "If one defects while the other cooperates, the defector gets 6 and the cooperator gets 0. "
          "Mutual defection pays each 2. Now impose a penalty of {penalty} on any player who defects. "
          "What is each player's dominant strategy before and after the penalty?")


def _actions(result, phase):
    info = result['strategic_decision'][phase]
    return [(info[x]['dominance'], info[x]['action']) for x in result['players']]


@pytest.mark.parametrize('penalty,kind,action,eq', [
    ('0','strict','defect',['defect','defect']),
    ('1','strict','defect',['defect','defect']),
    ('3/2','strict','defect',['defect','defect']),
    ('2','indifferent',None,None),
    ('2.0000000000000001','strict','cooperate',['cooperate','cooperate']),
    ('3','strict','cooperate',['cooperate','cooperate']),
    ('30','strict','cooperate',['cooperate','cooperate']),
])
def test_exact_payoff_reversal(penalty,kind,action,eq):
    result=solver.execute(PROMPT.format(penalty=penalty))
    assert result['status']=='BOUNDED_EXACT_TWO_PLAYER_DECISION_COMPUTED'
    assert _actions(result,'before')==[('strict','defect')]*2
    assert _actions(result,'after')==[(kind,action)]*2
    assert result['phases']['before']['pure_nash_equilibria'] == [
        {'actions':['defect','defect'],'payoffs':['2','2']}]
    if eq is None:
        assert len(result['phases']['after']['pure_nash_equilibria'])==4
        assert _actions(result,'after')==[('indifferent',None)]*2
    else:
        assert [e['actions'] for e in result['phases']['after']['pure_nash_equilibria']]==[eq]
    assert result['production_authority_changed'] is False
    assert result['model_checkpoint_used_for_decision'] is False
    assert result['independent_oracle_from_sqlite_used'] is False
    assert all(row['operation']=='subtract' and
               Fraction(row['left'])-Fraction(row['right'])==Fraction(row['result'])
               for row in result['arithmetic_trace'])


def test_original_complete_game_and_payoff_table():
    result=solver.execute(PROMPT.format(penalty=3))
    assert result['original_payoff_table']==[
        [['4','4'],['0','6']], [['6','0'],['2','2']]]
    assert result['phases']['after']['payoffs_by_player_actions']==[
        [['4','4'],['0','3']], [['3','0'],['-1','-1']]]
    assert result['phases']['after']['pure_nash_equilibria']==[
        {'actions':['cooperate','cooperate'],'payoffs':['4','4']}]
    assert 'GAMER_SOURCE_FACTOR_DAG' in result['exact_numerical_backend']


def test_bounded_english_variants_are_not_the_result_of_keyword_inference():
    prompt=PROMPT.format(penalty=3)
    variant=(prompt.replace('Two competing players','Two players')
                   .replace('defector gets','defector receives')
                   .replace('cooperator gets','cooperator earns')
                   .replace('Now impose','Impose'))
    assert solver.execute(variant)['strategic_decision'] == solver.execute(prompt)['strategic_decision']


@pytest.mark.parametrize('modified', [
    'Now impose a reward of 3 on any player who defects.',
    'Now impose a penalty of 3 on any player who cooperates.',
    'Now impose a penalty of 3 on any player who defects. Also double the defection payoff.',
    'Now waive a penalty of 3 on any player who defects.',
    'Now impose a penalty of 3 on any player who defects unless the opponent cooperates.',
    'Now impose a penalty of 3 on any player who defects. The penalty is refunded.',
    'Now impose a penalty on any player who defects.',
])
def test_fails_closed_when_rule_changes(modified):
    base=PROMPT.format(penalty=3)
    prompt=base.replace('Now impose a penalty of 3 on any player who defects.',modified)
    result=solver.execute(prompt)
    assert result['status']=='UNSUPPORTED_GAME_SPECIFICATION_NO_DECISION'
    assert result['strategic_decision'] is None


def _json_game(payoffs,*,penalty=None, actions=None):
    game={'players':['Blue','Red'],
          'actions': actions or [['cooperate','defect'],['cooperate','defect']],
          'payoffs': payoffs}
    if penalty is not None:
        game['penalty']={'action':'defect','amount':penalty}
    return json.dumps(game)


def test_asymmetric_2x2_game_and_weak_domination():
    game=_json_game([
        [[2,1],[2,0]],
        [[2,3],[1,2]],
    ])
    result=solver.execute(game)
    assert result['input_format']=='strict_json_normal_form'
    assert result['strategic_decision']['base']['Blue']['dominance']=='weak'
    assert result['strategic_decision']['base']['Blue']['action']=='cooperate'
    assert result['strategic_decision']['base']['Red']['dominance']=='strict'
    assert result['strategic_decision']['base']['Red']['action']=='cooperate'
    assert result['phases']['base']['pure_nash_equilibria']==[
        {'actions':['cooperate','cooperate'],'payoffs':['2','1']},
        {'actions':['defect','cooperate'],'payoffs':['2','3']}]


def test_matching_pennies_has_no_pure_nash_or_dominant_action():
    result=solver.execute(_json_game([
        [[1,-1],[-1,1]],
        [[-1,1],[1,-1]],
    ]))
    assert result['phases']['base']['pure_nash_equilibria']==[]
    assert _actions(result,'base')==[('none',None)]*2


def test_utility_shift_and_scale_preserve_strategic_selection():
    game=[[[1,2],[4,0]],[[3,3],[0,6]]]
    original=solver.execute(_json_game(game))
    changed=solver.execute(_json_game([[[7*x+11 for x in cell] for cell in row] for row in game]))
    assert _actions(original,'base')==_actions(changed,'base')
    assert [x['actions'] for x in original['phases']['base']['pure_nash_equilibria']] == [
        x['actions'] for x in changed['phases']['base']['pure_nash_equilibria']]


def test_negative_fraction_decimal_and_exact_zero():
    result=solver.execute(_json_game([
        [['-1/3','0.1'], ['-2/3','0.2']],
        [['-1/3','0.1'], ['-2/3','0.2']],
    ]))
    assert _actions(result,'base')==[('indifferent',None),('strict','defect')]
    assert result['strategic_decision']['base']['Blue']['action_0_minus_action_1_by_opponent_action']==['0','0']


@pytest.mark.parametrize('obj,error', [
    ({'actions':[['a','b'],['a','b']], 'payoffs':[[[1,2]]],},'2X2'),
    ({'actions':[['a','b'],['a','b']], 'payoffs':[[[1,2],[1,2]],[[1,2],[1,2]]], 'answer':'a'},'UNEXPECTED_FIELDS'),
    ({'actions':[['a','a'],['a','b']], 'payoffs':[[[1,2],[1,2]],[[1,2],[1,2]]]},'INVALID_ACTIONS'),
    ({'actions':[['a','b'],['a','b']], 'payoffs':[[[True,2],[1,2]],[[1,2],[1,2]]]},'BAD_PAYOFF_NUMBER'),
    ({'actions':[['a','b'],['a','b']], 'payoffs':[[['1/0',2],[1,2]],[[1,2],[1,2]]]},'BAD_PAYOFF_NUMBER'),
    ({'actions':[['a','b'],['a','b']], 'payoffs':[[[1,2],[1,2]],[[1,2],[1,2]]], 'penalty':{'action':'z','amount':1}},'PENALTY_ACTION_NOT_SHARED'),
])
def test_invalid_explicit_games_are_rejected(obj,error):
    with pytest.raises(ValueError,match=error):
        solver.execute(json.dumps(obj))


def test_enumerated_independent_oracle_for_every_small_2x2_payoff_table():
    # Every payoff cell independently assigned -1 or +1; no game-title,
    # learned label, or memorized target. Independently check optimal responses.
    for numbers in product((-1, 1), repeat=8):
        game = [
            [[numbers[0],numbers[1]], [numbers[2],numbers[3]]],
            [[numbers[4],numbers[5]], [numbers[6],numbers[7]]],
        ]
        result=solver.execute(_json_game(game))
        best=[]
        for i in range(2):
            for j in range(2):
                if (game[i][j][0]>=game[1-i][j][0] and
                        game[i][j][1]>=game[i][1-j][1]):
                    best.append(['cooperate' if i==0 else 'defect',
                                 'cooperate' if j==0 else 'defect'])
        assert [x['actions'] for x in result['phases']['base']['pure_nash_equilibria']]==best
        for player, name in enumerate(('Blue','Red')):
            if player==0:
                delta=[game[0][k][0]-game[1][k][0] for k in range(2)]
            else:
                delta=[game[k][0][1]-game[k][1][1] for k in range(2)]
            if all(v>0 for v in delta): expected=('strict','cooperate')
            elif all(v<0 for v in delta): expected=('strict','defect')
            elif all(v==0 for v in delta): expected=('indifferent',None)
            elif all(v>=0 for v in delta): expected=('weak','cooperate')
            elif all(v<=0 for v in delta): expected=('weak','defect')
            else: expected=('none',None)
            got=result['strategic_decision']['base'][name]
            assert (got['dominance'],got['action'])==expected


def test_duplicate_json_keys_cannot_hide_payoff_overrides():
    prompt=('{' 
      '"actions": [["cooperate","defect"],["cooperate","defect"]],'
      '"payoffs": [[[1,1],[1,1]],[[1,1],[1,1]]],'
      '"payoffs": [[[9,9],[9,9]],[[9,9],[9,9]]]'
      '}')
    with pytest.raises(ValueError, match='JSON_DUPLICATE_FIELD'):
        solver.execute(prompt)


def test_no_decision_from_label_only_prompt_or_game_name():
    for prompt in [
            'This is a Prisoners Dilemma, tell me the dominant strategy.',
            'A general chooses to expose a flank because regime security pays 10.',
            'Cooperate or defect. Defect!',
    ]:
        result=solver.execute(prompt)
        assert result['status']=='UNSUPPORTED_GAME_SPECIFICATION_NO_DECISION'
        assert result['strategic_decision'] is None
