"""V14: corrected matched acquisition, hard-gated consolidation and evidence."""
from __future__ import annotations
import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_corrected_margin_v14 as v14
from tools import nanojev_gamer_v1_margin_consolidation_v13 as v13
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tests.test_nanojev_gamer_v1_conflict_projection_v10 import _setup
from tests.test_nanojev_gamer_v1_protected_math_repair_v7 import _checkpoint


@pytest.fixture(autouse=True)
def _threads():
    old=torch.get_num_threads();torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


def test_matching_architecture_preserves_v11_gradients_and_v6_freeze():
    _,_,spec,mu,sd,base=_setup()
    torch.manual_seed(49)
    expanded=v11._expanded_model(torch,16)
    expanded.base.load_state_dict(v9._copy_state(base))
    c=v13._margin_model(torch,16,16)
    c.base.load_state_dict(v9._copy_state(expanded))
    m=v14._acquisition_model(torch,16,16)
    assert c.state_dict().keys()==m.state_dict().keys()
    m.load_state_dict(v9._copy_state(c))
    x,old,_,_,routes=spec['train_synthetic']
    encoded=torch.from_numpy(v6._encode(x,old,routes,mu,sd))
    with torch.no_grad():
        torch.testing.assert_close(m(encoded),c(encoded),atol=0,rtol=0)
        denied=encoded[:2].clone();denied[:,-2]=-1
        assert torch.equal(m(denied),torch.zeros(2,4))
        assert bool((m(encoded)[:,1:].abs()<=v6.MAX_CLASS_LOGIT_CORRECTION+1e-6).all())
    # Nonzero gradient to the original V11 expanded branch, not V6.
    m.train()
    m(encoded[:9]).sum().backward()
    assert any(p.grad is not None and bool(p.grad.abs().sum()>0)
               for p in m.base.decoder.parameters())
    assert all(p.grad is None for p in m.base.base.parameters())
    assert any(p.grad is not None for p in m.native.parameters())
    assert all(not p.requires_grad for p in c.base.parameters())


def test_hard_sign_gate_allows_safe_margin_tradeoffs(monkeypatch):
    _,_,spec,mu,sd,base=_setup()
    expanded=v11._expanded_model(torch,16)
    expanded.base.load_state_dict(v9._copy_state(base))
    model=v13._margin_model(torch,16,16)
    model.base.load_state_dict(v9._copy_state(expanded))
    groups,_=v9._groups_and_fingerprints(spec)
    correct=v9._correct_train(torch,model,spec,mu,sd,'cpu')
    ledger=v9.MathematicalLedger(correct,spec['train_synthetic'][3],groups)
    ok,actual=v14._hard_probe(torch,model,spec,ledger,mu,sd,'cpu')
    assert ok and np.array_equal(actual,correct)
    model.native[-1].bias.data[:]=torch.tensor([-200.,200.,-200.])
    ok,actual=v14._hard_probe(torch,model,spec,ledger,mu,sd,'cpu')
    assert not ok
    # Unlike V13, the admission check takes no per-case margin floor argument.
    assert v14._hard_probe.__code__.co_argcount==7


def test_small_consolidation_and_both_matched_continuations():
    _,_,spec,mu,sd,base=_setup()
    expanded=v11._expanded_model(torch,16)
    expanded.base.load_state_dict(v9._copy_state(base))
    net,initial,consolidated,events=v14._consolidate(torch,v9._copy_state(expanded),
        spec,mu,sd,'cpu',margin_width=16,capacity_width=16,steps=2,
        lr=.001,seed=49,reserve=.08,max_halvings=2,projection_passes=2,
        max_constraints=4,eval_every=1)
    assert events and len(events)<=2
    assert int(consolidated['ledger_mask'].sum())>=int(initial['ledger_mask'].sum())
    for checkpoint in (initial,consolidated):
        net.load_state_dict(checkpoint['weights'])
        assert np.array_equal(v9._correct_train(torch,net,spec,mu,sd,'cpu'),
                              checkpoint['ledger_mask'])
        model=v14._acquisition_model(torch,16,16)
        model.load_state_dict(checkpoint['weights'])
        trained,best,ledger,attempts,victims,history,stats=v10._fit_arm(
            torch,spec,checkpoint['weights'],mu,sd,arm='expanded_capacity_projected',
            device='cpu',steps=2,eval_every=1,lr=.001,seed=49,
            replay_weight=2.,max_halvings=2,projection_passes=2,
            max_constraints=4,model_builder=lambda t:v14._acquisition_model(t,16,16),
            capture_terminal=True)
        terminal=stats['terminal_snapshot']
        for snap in (best,terminal):
            model.load_state_dict(snap['weights'])
            assert np.array_equal(v9._correct_train(torch,model,spec,mu,sd,'cpu'),
                                  snap['ledger_mask'])
        assert len(attempts)==2


def test_v13_provenance_refuses_unverified_or_contaminated_evidence(tmp_path):
    root=tmp_path/'prior';root.mkdir()
    item={'schema':v13.SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
        'training_capture_sha256':'train','v6_prior_sha256':'head',
        'v11_verified':{'selected':'sha'},'v12_verified':{'report_sha256':'v12sha'},
        'focus_group':'train.offset.0041','source_dev_used_for_training':False,
        'synthetic_dev_used_for_training':False,
        'previously_seen_test_used_for_training_or_selection':False,
        'production_weights_modified':False,'promotion_authorized':False,
        'comparison':[{}, {}, {}, {}],
        'consolidation':{'accepted':0},'selected':'no_consolidation_control'}
    report=root/'report.json';report.write_text(json.dumps(item))
    evidence=v14._verify_v13(root,'train','head',{'selected':'sha'},
                             {'report_sha256':'v12sha'},'train.offset.0041')
    assert evidence['historical_consolidation_accepted']==0
    item['previously_seen_test_used_for_training_or_selection']=True
    report.write_text(json.dumps(item))
    with pytest.raises(ValueError,match='PROVENANCE_MISMATCH'):
        v14._verify_v13(root,'train','head',{'selected':'sha'},
                        {'report_sha256':'v12sha'},'train.offset.0041')


def test_end_to_end_checked_evidence_and_disposable_artifacts(monkeypatch,tmp_path):
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
    proof=v12._verify_v11(torch,tmp_path/'v11',data,capture,v14._sha(prior),spec,'cpu')[3]
    focus=spec['train_synthetic'][3][0]['group']
    v12root=tmp_path/'v12';v12root.mkdir()
    (v12root/'report.json').write_text(json.dumps(dict(
       schema=v12.SCHEMA,status='EXPERIMENT_COMPLETE_NO_PROMOTION',mode='repair',
       promotion_authorized=False,source_dev_used_for_fitting=False,
       synthetic_dev_used_for_fitting=False,
       previously_seen_test_used_for_fitting_selection_or_metric=False,
       training_capture_sha256=capture,v6_sha256=v14._sha(prior),
       v11_verified_checkpoints=proof,focus_group=focus,focus_member_count=6,
       v11_historical_conflict_events=1,
       v11_report_sha256=v14._sha(tmp_path/'v11'/'report.json'))))
    provenance=v13._check_v12(v12root,proof,capture,v14._sha(prior),focus,
                      v14._sha(tmp_path/'v11'/'report.json'))
    v13root=tmp_path/'v13';v13root.mkdir()
    (v13root/'report.json').write_text(json.dumps(dict(
        schema=v13.SCHEMA,status='EXPERIMENT_COMPLETE_NO_PROMOTION',
        training_capture_sha256=capture,v6_prior_sha256=v14._sha(prior),
        v11_verified=proof,v12_verified=provenance,focus_group=focus,
        source_dev_used_for_training=False,synthetic_dev_used_for_training=False,
        previously_seen_test_used_for_training_or_selection=False,
        production_weights_modified=False,promotion_authorized=False,
        comparison=[{}, {}, {}, {}],consolidation={'accepted':0},
        selected='no_consolidation_control')))
    p=SimpleNamespace(**common,v11_run=tmp_path/'v11',v12_run=v12root,
        v13_run=v13root,output=tmp_path/'v14',focus_group=focus,
        consolidation_steps=2,consolidation_lr=.001,reserve=.08,margin_width=16)
    doc=v14.run(p)
    assert doc['schema']==v14.SCHEMA
    assert doc['promotion_authorized'] is False
    assert doc['stage_b_gate']=='UNCHANGED_V10_EXACT_INFERENCE_MONOTONIC_TRAIN_SIGNS'
    assert len(doc['comparisons'])==4
    assert (p.output/'train_margin_census.csv').is_file()
    assert (p.output/'post_consolidation_candidate.pt').is_file()
    for arm in v14.ARMS:
        for state in ('selected','terminal'):
            cp=torch.load(p.output/f'{arm}_{state}_candidate.pt',weights_only=True)
            assert cp['status']=='DISPOSABLE_NON_PRODUCTION'
            assert cp['promotion_authorized'] is False
            assert cp['mathematical_train_retention_ledger']['source_split']=='SYNTHETIC_TRAIN_ONLY'
            assert cp['state']==state and cp['arm']==arm
    with pytest.raises(FileExistsError):v14.run(p)


@pytest.mark.parametrize('change',[{'steps':0},{'reserve':0},{'reserve':.5},
     {'margin_width':1},{'consolidation_steps':0},{'lr':float('nan')}])
def test_invalid_args_before_capture(monkeypatch,tmp_path,change):
    monkeypatch.setattr(joint,'_prepare',lambda args:pytest.fail('opened capture'))
    args=dict(run=tmp_path/'r',expanded_run=tmp_path/'e',input=tmp_path/'i',
       source_features=tmp_path/'s',prior=tmp_path/'p',v11_run=tmp_path/'v11',
       v12_run=tmp_path/'v12',v13_run=tmp_path/'v13',output=tmp_path/'v14',
       device='cpu',steps=1,consolidation_steps=1,eval_every=1,lr=.001,
       consolidation_lr=.001,reserve=.08,margin_width=16,max_halvings=1,
       projection_passes=1,max_constraints=4,replay_weight=2.,seed=49,
       focus_group='train.offset.0041')
    args.update(change)
    with pytest.raises(ValueError,match='INVALID_CONFIGURATION'):
        v14.run(SimpleNamespace(**args))
