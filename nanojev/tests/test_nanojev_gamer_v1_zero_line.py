"""Target-magnitude sampling, deterministic and isolated from held-out decks."""

import numpy as np
import pytest

from tools.nanojev_gamer_v1_zero_line import (
    ZeroLineSampler, resolve_policy, target_zero_line_metrics,
)


def _deck():
    # Same games have different numbers of cases to test game, not case, balance.
    return ([{'game_id': 'dense', 'answer': 0.0}] * 80 +
            [{'game_id': 'sparse', 'answer': 0.0}] +
            [{'game_id': 'dense', 'answer': -0.25}] * 40 +
            [{'game_id': 'sparse', 'answer': -1.0}] +
            [{'game_id': 'dense', 'answer': 0.3}] * 40 +
            [{'game_id': 'sparse', 'answer': 1.0}] +
            [{'game_id': 'outside', 'answer': 12.0}] * 30 +
            [{'game_id': 'outside', 'answer': -20.0}] * 30)


def test_focused_sampling_balances_signs_and_games_without_leaking_outside_targets():
    rows=_deck()
    sampler=ZeroLineSampler(rows,focus=1.0,radius=1.0)
    assert sampler.coverage['train_games']==3
    assert sampler.coverage['eligible_games_any']==2
    assert sampler.coverage['games_without_eligible_examples']==['outside']
    assert sampler.coverage['eligible_cases_by_class']=={'zero':81,'negative':41,'positive':41}
    rng=np.random.default_rng(47)
    indices,strata=sampler.draw(rng,6000)
    assert set(strata)=={'zero','negative','positive'}
    assert all(abs(float(rows[int(i)]['answer']))<=1.0 for i in indices)
    for group in ('zero','negative','positive'):
        focused=[int(i) for i,kind in zip(indices,strata) if kind==group]
        assert 1750<=len(focused)<=2250
        dense=sum(rows[i]['game_id']=='dense' for i in focused)
        # Every game in this class gets equal weight even when one has 80x more samples.
        assert 0.43<=dense/len(focused)<=0.57


def test_mixture_retains_baseline_over_all_games():
    rows=_deck()
    sampler=ZeroLineSampler(rows,focus=.75,radius=1)
    indices,strata=sampler.draw(np.random.default_rng(32),10000)
    assert .22<=strata.count('uniform')/len(strata)<=.28
    assert any(rows[int(i)]['game_id']=='outside' for i in indices)
    assert .22<=strata.count('zero')/len(strata)<=.28
    assert .22<=strata.count('negative')/len(strata)<=.28
    assert .22<=strata.count('positive')/len(strata)<=.28


def test_uniform_sampling_preserves_old_numpy_rng_draws_exactly():
    rows=_deck()
    old=np.random.default_rng(123)
    current=np.random.default_rng(123)
    indices,strata=ZeroLineSampler(rows,focus=0,radius=1).draw(current,32)
    assert np.array_equal(indices,old.integers(0,len(rows),size=32))
    assert all(s=='uniform' for s in strata)
    assert int(current.integers(0,100000))==int(old.integers(0,100000))


def test_policy_inherits_checkpoint_values_and_refuses_nonsensical_flags():
    previous={'focus':.75,'radius':2.0}
    assert resolve_policy(previous=previous)==previous
    assert resolve_policy(previous=previous,focus=0)=={'focus':0.,'radius':2.0}
    assert resolve_policy(radius=.5)=={'focus':0.,'radius':.5}
    for bad in (-.01,1.01,float('nan'),float('inf')):
        with pytest.raises(ValueError,match='FOCUS'):
            resolve_policy(focus=bad)
    for bad in (0,-1,float('nan'),float('inf')):
        with pytest.raises(ValueError,match='RADIUS'):
            resolve_policy(radius=bad)
    with pytest.raises(ValueError,match='CHECKPOINT_POLICY'):
        resolve_policy(previous={'focus':.7})
    with pytest.raises(ValueError,match='MISSING_TARGET_CLASS'):
        ZeroLineSampler([{'game_id':'only-positive','answer':.5}],focus=.75,radius=1)


def test_heldout_zero_line_metrics_explicitly_separate_targets_from_residuals():
    rows=[{'game_id':'a','answer':0,'zero_line_residual':-5},
          {'game_id':'b','answer':-0.5,'zero_line_residual':0},
          {'game_id':'c','answer':0.5,'zero_line_residual':-5},
          {'game_id':'d','answer':40,'zero_line_residual':0}]
    measured=target_zero_line_metrics(rows,[0.02,0.1,0.4,15],radius=1)
    assert measured['by_class']['zero']['cases']==1
    assert measured['by_class']['negative']['correct_sign']==0
    assert measured['by_class']['positive']['correct_sign']==1
    assert measured['by_class']['outside_radius']['cases']==1
    assert measured['by_class']['zero']['predicted_zero_within_0_1']==1
