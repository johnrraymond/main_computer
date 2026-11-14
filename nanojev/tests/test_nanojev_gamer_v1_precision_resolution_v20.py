"""V20: verified V19 replay, precision comparison and immutable evidence."""
from __future__ import annotations

import csv
import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_precision_resolution_v20 as v20
from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tests.test_nanojev_gamer_v1_joint_constraint_v18 import _toy


@pytest.fixture(autouse=True)
def _threads():
    before=torch.get_num_threads();torch.set_num_threads(2)
    yield
    torch.set_num_threads(before)


def test_validation_rejects_mismatched_report_config_and_provenance():
    cfg={k:1 for k in v20.CONFIG_KEYS}
    r={'schema':v19.SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
       'config':cfg}
    with pytest.raises(ValueError,match='V20_V19_REPORT_PROVENANCE_MISMATCH'):
        v20._validate_saved_report(r,cfg,'capture',{'probe_info':{'lineage':'l'}},
              'proof','post','v15','v16','v17','v18','prior','group')


def test_replay_refuses_different_decisions_missing_rows_and_magnitudes():
    row={'iteration':'3','direction':'balanced','halvings':'2',
         'decision':'JOINT_SAFETY_NOT_IMPROVED','after_a':'.016',
         'after_b':'.017','merit_after':'.125','merit_gain':'-.002',
         'correct_train':'239'}
    trial={'iteration':3,'direction':'balanced','halvings':2,
           'decision':'JOINT_SAFETY_NOT_IMPROVED','after_a':.016,
           'after_b':.017,'merit_after':.125,'merit_gain':-.002,
           'correct_train':239}
    assert v20._verify_replay_rows([row],[trial])
    with pytest.raises(ValueError,match='TRIAL_COUNT'):
        v20._verify_replay_rows([row],[])
    with pytest.raises(ValueError,match='DECISION_MISMATCH'):
        v20._verify_replay_rows([row],[dict(trial,decision='AGGREGATE_DEFICIT_NOT_IMPROVED')])
    with pytest.raises(ValueError,match='NUMERIC_MISMATCH'):
        v20._verify_replay_rows([row],[dict(trial,after_a=.020)])


def test_float64_is_computed_from_double_parameters_not_rounded_float32_output(monkeypatch):
    model,geom=_toy(monkeypatch)
    with torch.no_grad():
        model.weight[:]=torch.tensor([.021,.027])
    logits=lambda torch_mod,old,residual:torch.stack([residual,residual+1.,residual+2.],dim=1)
    monkeypatch.setattr(v20.joint,'_head_logits',logits)
    initial=v20._numerical_census(torch,model,geom,[0,1],capture_logits=True)
    assert len(initial['raw_logits'])==2
    assert next(model.parameters()).dtype==torch.float32
    model64=model.double()
    assert next(model64.parameters()).dtype==torch.float64
    observed=v20._numerical_census(torch,model64,geom,[0,1],capture_logits=True)
    assert observed['pair']==pytest.approx([.021,.027],abs=2e-8)
    assert observed['merit']==pytest.approx(.112,abs=2e-8)
    assert model64.weight.dtype==torch.float64
    assert all(v>0 for v in v20._ulp_scale(initial['raw_logits']))


def test_observer_remains_read_only_and_covers_rejected_stalled_iterations(monkeypatch):
    model,geom=_toy(monkeypatch,gate=lambda a,b:a<=0)
    monkeypatch.setattr(v20.joint,'_head_logits',
         lambda t,o,r:torch.stack((r,r+1.,r+2.),dim=1))
    anchor=v9._copy_state(model)
    observer=v20._AuditObserver(torch,model,geom,min_joint_gain=1e-8,
                   min_merit_gain=1e-8,repeats=2,from_iteration=3)
    rows=[]
    summary,_=v19._search(torch,model,geom,None,None,None,'cpu',
         max_iterations=4,max_stalls=4,target_step=.002,min_target_step=1e-8,
         trust_radius=.02,max_halvings=2,fd_epsilon=.002,
         min_joint_gain=1e-8,min_merit_gain=1e-8,
         trial_rows=rows,probe_observer=observer)
    assert summary['accepted_iterations']==0
    assert observer.rows and all(r['iteration']>=3 for r in observer.rows)
    assert len(observer.rows)==sum(int(r.get('iteration',0))>=3 and
                                 'halvings' in r for r in rows)
    assert all(isinstance(r['diagnostic_class'],str) for r in observer.rows)
    assert all(r['protected_retention_pass'] is False for r in observer.rows)
    assert all(r['repeat_max_safety_spread']<1e-9 for r in observer.rows)
    assert v20._aggregate(observer.rows)['retention_safe']==0
    assert torch.equal(model.weight.detach(),anchor['weight'])


def test_observer_detects_original_trial_precision_and_retention_without_mutation(monkeypatch):
    model,geom=_toy(monkeypatch)
    monkeypatch.setattr(v20.joint,'_head_logits',
         lambda t,o,r:torch.stack((r,r+1.,r+2.),dim=1))
    observer=v20._AuditObserver(torch,model,geom,min_joint_gain=1e-8,
                   min_merit_gain=1e-8,repeats=2,from_iteration=1)
    trials=[]
    summary,_=v19._search(torch,model,geom,None,None,None,'cpu',
          max_iterations=2,max_stalls=2,target_step=.002,min_target_step=1e-8,
          trust_radius=.02,max_halvings=2,fd_epsilon=.002,
          min_joint_gain=1e-8,min_merit_gain=1e-8,
          trial_rows=trials,probe_observer=observer)
    assert summary['accepted_iterations']>=1
    assert len(observer.rows)==len([r for r in trials if 'halvings' in r])
    assert all(abs(r['float32_vs_original_delta_a'])<1e-8 for r in observer.rows)
    assert all(abs(r['float32_vs_original_delta_b'])<1e-8 for r in observer.rows)
    assert any(r['diagnostic_class']=='BOTH_MARGIN_GAINS_RESOLVED'
               for r in observer.rows)


def test_proposal_classification_distinguishes_tradeoff_threshold_precision():
    template={'min_joint_gain':2e-7,'min_merit_gain':3e-7,'merit_gain':0.}
    classify=v20._classify_trial
    assert classify(template,fp32=[.01,-.01],fp64=[.01,-.01],
           repeat_jitter=0.,ulp=[1e-7])=='RESOLVED_OPPOSING_MARGIN_TRADEOFF'
    assert classify(template,fp32=[0.,0.],fp64=[0.,0.],
           repeat_jitter=0.,ulp=[1e-7])=='NO_RESOLVED_JOINT_ASCENT'
    assert classify(template,fp32=[0.,0.],fp64=[.00001,.00002],
           repeat_jitter=0.,ulp=[1e-7])=='FP32_VS_FP64_EVALUATION_DISAGREEMENT'
    assert classify(template,fp32=[1e-6,1e-6],fp64=[1e-6,1e-6],
           repeat_jitter=2e-5,ulp=[1e-7])=='EVALUATION_NOT_REPEATABLE'


def test_parser_requires_source_run_and_no_training_mode():
    p=v20.parser();names={x.dest for x in p._actions}
    assert 'v19_run' in names and 'v14_run' in names
    assert p.get_default('repeats')==3
    assert p.get_default('from_iteration')==3
    assert p.get_default('mode')=='audit'
