"""V15: local gradient feasibility, verified capture, paired-seed replication."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_margin_feasibility_v15 as v15
from tools import nanojev_gamer_v1_corrected_margin_v14 as v14
from tools import nanojev_gamer_v1_margin_consolidation_v13 as v13
from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tests.test_nanojev_gamer_v1_conflict_projection_v10 import _setup
from tests.test_nanojev_gamer_v1_protected_math_repair_v7 import _checkpoint


@pytest.fixture(autouse=True)
def _threads():
    old=torch.get_num_threads();torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


def test_deterministic_seed_validation():
    assert v15._seeds('47,49,53')==[47,49,53]
    for bad in ('49','49,49','1,2,3,4,5,6,7,8,9','-1,2','x,2',''):
        with pytest.raises(Exception):v15._seeds(bad)


def test_minimum_norm_feasibility_and_local_contradiction():
    grads=[torch.tensor([1.,0.]),torch.tensor([0.,1.])]
    info,disp=v15._gradient_span_feasibility(grads,[.1,.2],.4)
    assert info['linearized_feasible'] and info['within_trust']
    assert disp.tolist()==pytest.approx([.1,.2],abs=2e-4)
    assert info['minimal_norm']==pytest.approx(np.hypot(.1,.2),rel=1e-4)
    info,_=v15._gradient_span_feasibility(grads,[.1,.2],.05)
    assert info['status']=='LINEARIZED_TARGET_REQUIRES_LARGER_STEP'
    info,_=v15._gradient_span_feasibility([torch.zeros(2)],[.01],.1)
    assert info['status']=='FIRST_ORDER_ZERO_GRADIENT_FOR_REQUIRED_RESERVE'
    info,_=v15._gradient_span_feasibility(
        [torch.tensor([1.,0.]),torch.tensor([-1.,0.])],[.1,.1],1.)
    assert not info['linearized_feasible']
    assert info['status']=='LINEARIZED_SOLVER_INCONCLUSIVE'


def test_sampled_gradient_census_is_diagnostic_and_reversible():
    data,prepared,spec,mu,sd,base=_setup()
    expanded=v11._expanded_model(torch,16)
    expanded.base.load_state_dict(v9._copy_state(base))
    model=v13._margin_model(torch,16,16)
    model.base.load_state_dict(v9._copy_state(expanded))
    before=v9._copy_state(model)
    observed=v9._correct_train(torch,model,spec,mu,sd,'cpu')
    cp={'train_ledger_mask':torch.tensor(observed)}
    focus=spec['train_synthetic'][3][0]['group']
    rows,geometry,probes=v15._margin_study(torch,model,cp,spec,mu,sd,'cpu',
                                           focus,.08,8,.1)
    assert len(rows)==int(observed.sum())
    assert geometry['protected_cases']==int(observed.sum())
    assert geometry['probe_is_diagnostic_only']
    assert len(geometry['focus_cases'])==6
    assert all(torch.equal(v,b) for (v,b) in
               [(model.state_dict()[k],t) for k,t in before.items()])
    assert np.array_equal(observed,v9._correct_train(torch,model,spec,mu,sd,'cpu'))


def _fixture_reports(monkeypatch,tmp_path):
    data,prepared,spec,mu,sd,_=_setup()
    prior=tmp_path/'v6'/'disposable_candidate.pt';prior.parent.mkdir()
    _checkpoint(prior,data,mu,sd)
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,prepared))
    monkeypatch.setattr(joint,'_verify_after',lambda data:None)
    common=dict(device='cpu',run=tmp_path/'r',expanded_run=tmp_path/'e',
       input=tmp_path/'i',source_features=tmp_path/'s',prior=prior,
       steps=1,eval_every=1,lr=.001,seed=49,replay_weight=2.,
       max_halvings=1,projection_passes=1,max_constraints=4)
    v9.run(SimpleNamespace(**{k:v for k,v in common.items()
        if k not in ('projection_passes','max_constraints')},output=tmp_path/'v9'))
    v10.run(SimpleNamespace(**common,v9_run=tmp_path/'v9',output=tmp_path/'v10'))
    v11.run(SimpleNamespace(**common,v10_run=tmp_path/'v10',
                output=tmp_path/'v11',capacity_width=16))
    _,capture=v9._groups_and_fingerprints(spec)
    proof=v12._verify_v11(torch,tmp_path/'v11',data,capture,v15._hash(prior),spec,'cpu')[3]
    focus=spec['train_synthetic'][3][0]['group']
    v12root=tmp_path/'v12';v12root.mkdir()
    (v12root/'report.json').write_text(json.dumps(dict(
       schema=v12.SCHEMA,status='EXPERIMENT_COMPLETE_NO_PROMOTION',mode='repair',
       promotion_authorized=False,source_dev_used_for_fitting=False,
       synthetic_dev_used_for_fitting=False,
       previously_seen_test_used_for_fitting_selection_or_metric=False,
       training_capture_sha256=capture,v6_sha256=v15._hash(prior),
       v11_verified_checkpoints=proof,focus_group=focus,focus_member_count=6,
       v11_historical_conflict_events=1,
       v11_report_sha256=v15._hash(tmp_path/'v11'/'report.json'))))
    provenance=v13._check_v12(v12root,proof,capture,v15._hash(prior),focus,
                      v15._hash(tmp_path/'v11'/'report.json'))
    v13root=tmp_path/'v13';v13root.mkdir()
    (v13root/'report.json').write_text(json.dumps(dict(
        schema=v13.SCHEMA,status='EXPERIMENT_COMPLETE_NO_PROMOTION',
        training_capture_sha256=capture,v6_prior_sha256=v15._hash(prior),
        v11_verified=proof,v12_verified=provenance,focus_group=focus,
        source_dev_used_for_training=False,synthetic_dev_used_for_training=False,
        previously_seen_test_used_for_training_or_selection=False,
        production_weights_modified=False,promotion_authorized=False,
        comparison=[{}, {}, {}, {}],consolidation={'accepted':0},
        selected='no_consolidation_control'))) 
    args=SimpleNamespace(**common,v11_run=tmp_path/'v11',v12_run=v12root,
        v13_run=v13root,output=tmp_path/'v14',focus_group=focus,
        consolidation_steps=1,consolidation_lr=.001,reserve=.08,margin_width=16)
    v14.run(args)
    return data,spec,mu,sd,prior,focus,proof,common


def test_verified_v14_provenance_refuses_tampering(monkeypatch,tmp_path):
    data,spec,mu,sd,prior,focus,proof,common=_fixture_reports(monkeypatch,tmp_path)
    report,cp,model,verification=v15._check_prior14(tmp_path/'v14',proof,
        v9._groups_and_fingerprints(spec)[1],v15._hash(prior),data,focus,
        torch,spec,mu,sd,'cpu')
    assert verification['train_ledger_verified']
    fp=tmp_path/'v14'/'report.json'
    r=json.loads(fp.read_text());r['synthetic_dev_used_for_training']=True
    fp.write_text(json.dumps(r))
    with pytest.raises(ValueError,match='PROVENANCE_MISMATCH'):
        v15._check_prior14(tmp_path/'v14',proof,v9._groups_and_fingerprints(spec)[1],
             v15._hash(prior),data,focus,torch,spec,mu,sd,'cpu')


def test_v15_study_and_two_seed_sweep_e2e(monkeypatch,tmp_path):
    data,spec,mu,sd,prior,focus,proof,common=_fixture_reports(monkeypatch,tmp_path)
    args=dict(run=common['run'],expanded_run=common['expanded_run'],
        input=common['input'],source_features=common['source_features'],
        prior=prior,v11_run=tmp_path/'v11',v14_run=tmp_path/'v14',
        device='cpu',focus_group=focus,seeds=[47,49],steps=1,
        consolidation_steps=1,eval_every=1,lr=.001,
        consolidation_lr=.001,reserve=.08,margin_width=16,
        max_gradient_cases=8,trust_radius=.1,replay_weight=2.,
        max_halvings=1,projection_passes=1,max_constraints=4)
    study=v15.run(SimpleNamespace(**args,mode='study',output=tmp_path/'v15-study'))
    assert study['promotion_authorized'] is False
    assert study['geometry']['probe_is_diagnostic_only']
    assert not (tmp_path/'v15-study'/'seed_pairs.csv').exists()
    sweep=v15.run(SimpleNamespace(**args,mode='sweep',output=tmp_path/'v15-sweep'))
    assert len(sweep['paired_seed_results'])==2
    assert len(sweep['comparisons'])==8
    assert (tmp_path/'v15-sweep'/'seed_pairs.csv').is_file()
    for seed in [47,49]:
        for arm in v14.ARMS:
            for state in ('selected','terminal'):
                cp=torch.load(tmp_path/'v15-sweep'/f'seed{seed}_{arm}_{state}.pt',
                              weights_only=True)
                assert cp['promotion_authorized'] is False
                assert cp['state']==state and cp['seed']==seed
                assert cp['train_capture_sha256']==sweep['training_capture_sha256']
    with pytest.raises(FileExistsError):
        v15.run(SimpleNamespace(**args,mode='study',output=tmp_path/'v15-sweep'))
