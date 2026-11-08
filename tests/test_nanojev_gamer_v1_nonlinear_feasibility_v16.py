"""V16 regression: exact nonlinear region, honest provenance and no promotion."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_nonlinear_feasibility_v16 as v16
from tools import nanojev_gamer_v1_margin_feasibility_v15 as v15
from tools import nanojev_gamer_v1_corrected_margin_v14 as v14
from tools import nanojev_gamer_v1_margin_consolidation_v13 as v13
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tests.test_nanojev_gamer_v1_margin_feasibility_v15 import _fixture_reports
from tests.test_nanojev_gamer_v1_conflict_projection_v10 import _setup


@pytest.fixture(autouse=True)
def _limit_threads():
    previous=torch.get_num_threads();torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


def test_radii_parser_is_deterministic_and_strict():
    assert v16._radii('.03,.1,.25,.5,1')==[.03,.1,.25,.5,1.]
    for x in ('','0,.1','0.1','1,.1','.1,.1','nan,.2','inf,.2','bad,.2','-.1,.2','1,20'):
        with pytest.raises(Exception):v16._radii(x)


def test_nonlinear_search_never_changes_verified_anchor():
    data,prepared,spec,mu,sd,base=_setup()
    expanded=v11._expanded_model(torch,16)
    expanded.base.load_state_dict(v9._copy_state(base))
    net=v13._margin_model(torch,16,16)
    net.base.load_state_dict(v9._copy_state(expanded))
    frozen=v9._copy_state(net)
    mask=v9._correct_train(torch,net,spec,mu,sd,'cpu')
    cp={'train_ledger_mask':torch.tensor(mask)}
    # Feed an untampered deck directly from the locally generated fixture.
    # A near-zero trust-region search always has a valid anchor to preserve.
    x,old,y,rows,routes=spec['train_synthetic']
    target=np.sign(y).astype(np.int64)
    encoded=torch.from_numpy(v6._encode(x,old,routes,mu,sd))
    required=v13._required_margin(old,target,.08)
    margins,deficits=v13._margin_census(torch,net,encoded,torch.as_tensor(old,dtype=torch.float32),target,required)
    geom=dict(anchor_weights=frozen,mask=mask,encoded=encoded,
        old=torch.as_tensor(old,dtype=torch.float32),truth=target,required=required,
        focus_positions=list(range(6)),deficient=list(np.flatnonzero(mask & (deficits>0))),
        positions=[],gradients=[],linear_direction=None,
        initial_deficit=float(deficits[mask].sum()),deficits=deficits)
    trials=[]
    summary,snap,_=v16._search_radius(torch,net,geom,spec,mu,sd,'cpu',.005,2,2,1e-8,trials)
    assert summary['hard_full_train_retention'] is True
    assert summary['best_displacement_norm']<=.00501
    assert summary['best_deficit']<=summary['initial_deficit']+1e-8
    for trial in trials:
        assert trial['displacement_norm']<=.00501
        if trial['accepted']:
            assert trial['retention_pass'] is True
            assert trial['nonlinear_improvement']>1e-8
    assert all(torch.equal(net.state_dict()[k],val) for k,val in frozen.items())
    net.load_state_dict(snap,strict=True)
    assert not (mask & ~v9._correct_train(torch,net,spec,mu,sd,'cpu')).any()


def _make_v15(monkeypatch,tmp_path):
    data,spec,mu,sd,prior,focus,proof,common=_fixture_reports(monkeypatch,tmp_path)
    args=dict(run=common['run'],expanded_run=common['expanded_run'],
        input=common['input'],source_features=common['source_features'],
        prior=prior,v11_run=tmp_path/'v11',v14_run=tmp_path/'v14',
        device='cpu',focus_group=focus,seeds=[47,49],steps=1,
        consolidation_steps=1,eval_every=1,lr=.001,consolidation_lr=.001,
        reserve=.08,margin_width=16,max_gradient_cases=8,trust_radius=.1,
        replay_weight=2.,max_halvings=1,projection_passes=1,max_constraints=4)
    v15.run(SimpleNamespace(**args,mode='study',output=tmp_path/'v15'))
    return data,spec,mu,sd,prior,focus,proof,common


def test_e2e_verified_v15_anchored_nonlinear_study(monkeypatch,tmp_path):
    data,spec,mu,sd,prior,focus,proof,common=_make_v15(monkeypatch,tmp_path)
    args=SimpleNamespace(mode='study',run=common['run'],expanded_run=common['expanded_run'],
       input=common['input'],source_features=common['source_features'],
       prior=prior,v11_run=tmp_path/'v11',v14_run=tmp_path/'v14',
       v15_run=tmp_path/'v15',output=tmp_path/'v16',device='cpu',focus_group=focus,
       trust_radii=[.01,.05],max_gradient_cases=8,max_iterations=2,
       max_halvings=2,reserve=.08,min_improvement=1e-8,eval_every=1)
    result=v16.run(args)
    assert result['status']=='DIAGNOSTIC_COMPLETE_NO_PROMOTION'
    assert result['no_new_trainable_capacity'] is True
    assert result['production_weights_modified'] is False
    assert result['general_acquisition_training'] is False
    assert len(result['radii'])==2
    assert all(r['hard_full_train_retention'] for r in result['radii'])
    assert (tmp_path/'v16'/'nonlinear_trust_trials.csv').is_file()
    assert (tmp_path/'v16'/'protected_train_margin_constraints.csv').is_file()
    if result['checkpoint_saved']:
        cp=torch.load(tmp_path/'v16'/'best_feasible_disposable_candidate.pt',weights_only=True)
        assert cp['train_capture_sha256']==result['training_capture_sha256']
        assert cp['promotion_authorized'] is False
    with pytest.raises(FileExistsError):v16.run(args)


def test_v16_refuses_v15_lineage_tampering(monkeypatch,tmp_path):
    data,spec,mu,sd,prior,focus,proof,common=_make_v15(monkeypatch,tmp_path)
    reportfile=tmp_path/'v15'/'report.json'
    report=json.loads(reportfile.read_text())
    report['previously_seen_test_used_for_training_or_selection']=True
    reportfile.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='V15_PROVENANCE_MISMATCH'):
        v16._checked_prior15(tmp_path/'v15',
          report['v14_verified'],report['training_capture_sha256'],
          report['v6_prior_sha256'],focus,data,.08)

class _OneDim(torch.nn.Module):
    def __init__(self):
        super().__init__();self.coordinate=torch.nn.Parameter(torch.tensor([0.]))
    def forward(self):return self.coordinate


def test_exact_hard_gate_rejects_better_but_destructive_nonlinear_step(monkeypatch):
    """Linearized direction can improve merit but must never defeat hard gate."""
    from tools import nanojev_gamer_v1_nonlinear_feasibility_v16 as module
    net=_OneDim();original=v9._copy_state(net)
    geom={'anchor_weights':original,'linear_direction':torch.tensor([1.]),
          'deficits':np.asarray([.16]),'positions':[0],
          'gradients':[torch.tensor([1.])], 'initial_deficit':.16,
          'deficient':[],'mask':np.array([True])}
    def fake_exact(torch_mod,model,geom,spec,mu,sd,device,*,scope):
        x=float(model.coordinate.detach().item())
        deficit=(x-.4)**2
        # A hypothetical acquired answer is lost if x exceeds 0.30.
        safe=x<=.3+1e-7
        return {'retention_pass':safe,'protected_lost':int(not safe),
                'total_deficit':deficit,'focus_min_safety':1.-deficit,
                'deficient_remaining':int(deficit>1e-8),
                'all_protected_reserves_met':deficit<=1e-8,
                'correct_train':1 if safe else 0,'scope':scope},None,np.array([deficit]),np.array([safe])
    monkeypatch.setattr(module,'_actual_evaluation',fake_exact)
    monkeypatch.setattr(module,'_gradient_step',lambda *args:torch.tensor([1.]))
    logs=[]
    summary,best,_=module._search_radius(torch,net,geom,None,None,None,'cpu',
                                           .5,3,3,1e-8,logs)
    assert any(not r['retention_pass'] and r['nonlinear_improvement']>0 for r in logs)
    assert all(r['retention_pass'] for r in logs if r['accepted'])
    assert summary['best_deficit']<.16 and summary['best_displacement_norm']<=.5
    assert float(best['coordinate'])<=.300001
    assert torch.equal(net.coordinate.detach(),original['coordinate'])


def test_no_unjustified_infeasibility_claim_when_local_search_stalls(monkeypatch):
    from tools import nanojev_gamer_v1_nonlinear_feasibility_v16 as module
    net=_OneDim();original=v9._copy_state(net)
    geom={'anchor_weights':original,'linear_direction':torch.tensor([-1.]),
          'deficits':np.asarray([1.]),'positions':[0],
          'gradients':[torch.tensor([-1.])], 'initial_deficit':1.,
          'deficient':[],'mask':np.array([True])}
    def fake_exact(torch_mod,model,geom,spec,mu,sd,device,*,scope):
        x=float(model.coordinate.detach().item())
        deficit=1.+abs(x)
        return {'retention_pass':True,'protected_lost':0,'total_deficit':deficit,
                'focus_min_safety':0.,'deficient_remaining':1,
                'all_protected_reserves_met':False,'correct_train':1,'scope':scope},None,np.array([deficit]),np.array([True])
    monkeypatch.setattr(module,'_actual_evaluation',fake_exact)
    monkeypatch.setattr(module,'_gradient_step',lambda *args:torch.tensor([-1.]))
    logs=[]
    summary,best,_=module._search_radius(torch,net,geom,None,None,None,'cpu',
                                           .4,2,3,1e-8,logs)
    assert summary['status']=='NO_RETENTION_SAFE_IMPROVEMENT_FOUND'
    assert summary['improvement']==0 and not summary['target_met']
    assert torch.equal(best['coordinate'],original['coordinate'])
