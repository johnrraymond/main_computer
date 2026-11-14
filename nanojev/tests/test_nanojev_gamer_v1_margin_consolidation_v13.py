"""Margin consolidation: actual V6 abstention, bound and TRAIN-only audit."""
from __future__ import annotations
import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tests.test_nanojev_gamer_v1_conflict_projection_v10 import _setup
from tests.test_nanojev_gamer_v1_protected_math_repair_v7 import _checkpoint
from tools import nanojev_gamer_v1_margin_consolidation_v13 as v13
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_protected_alignment_lab as v6


@pytest.fixture(autouse=True)
def _threads():
    old=torch.get_num_threads();torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


def test_correct_margin_reserve_respects_baseline_abstention():
    old=np.asarray([-1.0,-1.0,0.0,0.0,1.0])
    expected=np.asarray([-1,1,0,-1,0])
    observed=v13._required_margin(old,expected,0.08)
    np.testing.assert_allclose(observed,[.08,.08,.08,.08,.08],atol=1e-6)
    assert v6.MIN_CLASS_CHANGE_MARGIN==.5



def test_real_abstention_margin_when_frozen_correct_class_is_third():
    # Frozen negative logit = 1.5. The specialist's raw winner is positive
    # with top-two gap 0.3. V6 falls back to the frozen negative, even though
    # the frozen negative logit ranks THIRD, not second. A true-class margin
    # check would falsely treat this as an unsafe -1.4 margin.
    class Fixed(torch.nn.Module):
        protected=True
        def __init__(self,correction):
            super().__init__()
            self.register_buffer('fixed',torch.as_tensor(correction,dtype=torch.float32))
        def forward(self,x):
            return self.fixed[None,:].expand(len(x),-1)
    x=np.zeros((1,1024),dtype=np.float32)
    mu=np.zeros(1024,dtype=np.float32)
    sd=np.ones(1024,dtype=np.float32)
    old=np.array([-1.0],dtype=np.float32)
    encoded=torch.from_numpy(v6._encode(x,old,np.array([0],dtype=np.float32),mu,sd))
    model=Fixed([0,-1.5,1.1,1.4])
    prediction,_=v6._predict(torch,model,x,old,np.array([0]),mu,sd,'cpu')
    assert prediction.tolist()==[-1]
    guard=v13._class_margins(torch,model,encoded,torch.tensor(old),np.array([-1]))
    truth=v13._true_class_margins(torch,model,encoded,torch.tensor(old),np.array([-1]))
    assert guard.item()==pytest.approx(.2,abs=1e-6)
    assert truth.item()==pytest.approx(-1.4,abs=1e-6)
    # For a wrong baseline, a raw true-class lead of exactly .5 is correct
    # but has NO spare safety; consolidation should increase that margin.
    model=Fixed([0,2,0,0])
    old=np.array([1.0],dtype=np.float32)
    encoded=torch.from_numpy(v6._encode(x,old,np.array([0],dtype=np.float32),mu,sd))
    pred,_=v6._predict(torch,model,x,old,np.array([0]),mu,sd,'cpu')
    assert pred.tolist()==[-1]
    guard=v13._class_margins(torch,model,encoded,torch.tensor(old),np.array([-1]))
    assert guard.item()==pytest.approx(0,abs=1e-6)


def test_margin_branch_exact_zero_start_and_bounded_original_passthrough():
    _,_,spec,mu,sd,base=_setup()
    expanded=v11._expanded_model(torch,16)
    expanded.base.load_state_dict(v9._copy_state(base))
    net=v13._margin_model(torch,16,16)
    net.base.load_state_dict(v9._copy_state(expanded))
    x,old,_,_,routes=spec['train_synthetic']
    encoded=torch.from_numpy(v6._encode(x,old,routes,mu,sd))
    with torch.no_grad():
        assert torch.equal(net(encoded),expanded(encoded))
        assert torch.all(net(encoded)[:,1:].abs() <= v6.MAX_CLASS_LOGIT_CORRECTION+1e-6)
        denied=encoded[:2].clone();denied[:,-2]=-1
        assert torch.equal(net(denied),torch.zeros(2,4))
        net.native[-1].bias.add_(100.)
        net.game[-1].bias.sub_(100.)
        assert torch.all(net(encoded)[:,1:].abs() <= v6.MAX_CLASS_LOGIT_CORRECTION+1e-6)
        assert torch.equal(net(denied),torch.zeros(2,4))
    assert all(not p.requires_grad for p in net.base.parameters())
    assert all(p.requires_grad for p in net.native.parameters())
    with pytest.raises(ValueError,match='BAD_MARGIN_WIDTH'):
        v13._margin_model(torch,1,16)


def test_affine_projection_replenishes_deficit_and_spends_surplus():
    raw=torch.tensor([-1.0,1.0])
    protected=[torch.tensor([1.0,0.0]),torch.tensor([0.0,1.0])]
    adjusted,info=v13._affine_projection(raw,protected,[0.25,-0.5],passes=3,max_ratio=10)
    assert adjusted[0].item()>=.25-1e-5
    assert adjusted[1].item()>=-.5-1e-5
    assert info['violations_before']==1
    assert info['violations_after']==0
    with pytest.raises(ValueError,match='INVALID_AFFINE_CONSTRAINTS'):
        v13._affine_projection(raw,protected,[1],passes=2)
    with pytest.raises(ValueError,match='INVALID_AFFINE_GRADIENT'):
        v13._affine_projection(raw,[torch.ones(3)],[1],passes=2)


def test_floor_never_weakens_and_captures_low_reserve():
    m=np.asarray([.000001,.75,.01,-.25])
    required=np.asarray([.08,.08,.08,.08])
    protected=np.asarray([True,True,True,False])
    floor=v13._training_floor(m,required,protected)
    np.testing.assert_allclose(floor[:3],[.000001,.08,.01])
    assert floor[-1]<-1e6


def test_full_train_hard_gate_and_margin_gate_independent():
    _,_,spec,mu,sd,base=_setup()
    groups,_=v9._groups_and_fingerprints(spec)
    net=v13._margin_model(torch,16,16)
    expanded=v11._expanded_model(torch,16)
    expanded.base.load_state_dict(v9._copy_state(base))
    net.base.load_state_dict(v9._copy_state(expanded))
    tx,old,y,rows,routes=spec['train_synthetic']
    x=torch.from_numpy(v6._encode(tx,old,routes,mu,sd))
    oldt=torch.as_tensor(old,dtype=torch.float32)
    truth=np.sign(y).astype(np.int64)
    req=v13._required_margin(old,truth,.08)
    before=v13._margin_census(torch,net,x,oldt,truth,req)[0]
    correct=v9._correct_train(torch,net,spec,mu,sd,'cpu')
    ledger=v9.MathematicalLedger(correct,rows,groups)
    floor=v13._training_floor(before,req,ledger.mask)
    ok,seen,detail=v13._safety(torch,net,spec,x,oldt,truth,req,ledger,floor,mu,sd,'cpu')
    assert ok and np.array_equal(seen,correct)
    altered=floor.copy();altered[ledger.mask]+=10
    ok,_,detail=v13._safety(torch,net,spec,x,oldt,truth,req,ledger,altered,mu,sd,'cpu')
    assert not ok and detail['sign_lost']==0 and detail['margin_lost']>0


def test_tiny_consolidation_and_acquisition_preserve_training_ledger():
    _,_,spec,mu,sd,base=_setup()
    expanded=v11._expanded_model(torch,16)
    expanded.base.load_state_dict(v9._copy_state(base))
    net,initial,consolidated,events=v13._consolidate(torch,v9._copy_state(expanded),
       spec,mu,sd,'cpu',width=16,capacity_width=16,steps=2,lr=.001,seed=49,
       epsilon=.08,max_halvings=2)
    assert len(events)>=1
    assert consolidated['ledger_mask'].sum()>=initial['ledger_mask'].sum()
    for state in [initial,consolidated]:
        net.load_state_dict(state['weights'])
        observed=v9._correct_train(torch,net,spec,mu,sd,'cpu')
        assert np.array_equal(observed,state['ledger_mask'])
    a,b,attempts,history=v13._acquire(torch,net,consolidated,spec,mu,sd,'cpu',
      steps=2,eval_every=1,lr=.001,seed=49,replay_weight=2.,epsilon=.08,
      max_halvings=2,projection_passes=2,max_constraints=4)
    assert len(attempts)==2 and len(history)==2
    assert np.all(consolidated['ledger_mask']<=b['ledger_mask'])
    for state in [a,b]:
        net.load_state_dict(state['weights'])
        assert np.array_equal(v9._correct_train(torch,net,spec,mu,sd,'cpu'),state['ledger_mask'])


def test_v12_provenance_refuses_wrong_capture(tmp_path):
    root=tmp_path/'v12';root.mkdir()
    minimal=dict(schema=v12.SCHEMA,status='EXPERIMENT_COMPLETE_NO_PROMOTION',
      promotion_authorized=False,mode='repair',source_dev_used_for_fitting=False,
      synthetic_dev_used_for_fitting=False,
      previously_seen_test_used_for_fitting_selection_or_metric=False,
      training_capture_sha256='123',v6_sha256='abc',
      v11_verified_checkpoints={'selected':{'checkpoint_sha256':'yes'}},
      focus_group='train.offset.0041',v11_report_sha256='v11ref',
      focus_member_count=6,v11_historical_conflict_events=299)
    (root/'report.json').write_text(json.dumps(minimal))
    assert v13._check_v12(root,minimal['v11_verified_checkpoints'],'123','abc',
                           'train.offset.0041','v11ref')['historical_focus']=='train.offset.0041'
    with pytest.raises(ValueError,match='PROVENANCE_MISMATCH'):
        v13._check_v12(root,minimal['v11_verified_checkpoints'],'wrong','abc',
                        'train.offset.0041','v11ref')


def test_e2e_requires_verified_v11_v12_and_saves_both_arms(monkeypatch,tmp_path):
    data,prepared,spec,mu,sd,_=_setup()
    prior=tmp_path/'v6'/'disposable_candidate.pt';prior.parent.mkdir()
    _checkpoint(prior,data,mu,sd)
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,prepared))
    monkeypatch.setattr(joint,'_verify_after',lambda data:None)
    common=dict(device='cpu',run=tmp_path/'r',expanded_run=tmp_path/'e',
       input=tmp_path/'i',source_features=tmp_path/'s',prior=prior,
       steps=2,eval_every=1,lr=.002,seed=49,replay_weight=2.,
       max_halvings=1,projection_passes=1,max_constraints=4)
    v9.run(SimpleNamespace(**{k:v for k,v in common.items()
          if k not in ('projection_passes','max_constraints')},output=tmp_path/'v9'))
    v10.run(SimpleNamespace(**common,v9_run=tmp_path/'v9',output=tmp_path/'v10'))
    v11.run(SimpleNamespace(**common,v10_run=tmp_path/'v10',
               output=tmp_path/'v11',capacity_width=16))
    _,_,spec,_,_,_=_setup()
    _,capture=v9._groups_and_fingerprints(spec)
    proof=v12._verify_v11(torch,tmp_path/'v11',data,capture,v13._sha(prior),spec,'cpu')[3]
    v12path=tmp_path/'v12';v12path.mkdir()
    focus=spec['train_synthetic'][3][0]['group']
    (v12path/'report.json').write_text(json.dumps(dict(
       schema=v12.SCHEMA,status='EXPERIMENT_COMPLETE_NO_PROMOTION',mode='repair',
       promotion_authorized=False,source_dev_used_for_fitting=False,
       synthetic_dev_used_for_fitting=False,
       previously_seen_test_used_for_fitting_selection_or_metric=False,
       training_capture_sha256=capture,v6_sha256=v13._sha(prior),
       v11_verified_checkpoints=proof,focus_group=focus,focus_member_count=6,
       v11_historical_conflict_events=1,
       v11_report_sha256=v13._sha(tmp_path/'v11'/'report.json'))))
    p=SimpleNamespace(**common,v11_run=tmp_path/'v11',v12_run=v12path,
       output=tmp_path/'v13',focus_group=focus,consolidation_steps=2,
       consolidation_lr=.001,reserve=.08,margin_width=16)
    report=v13.run(p)
    assert report['schema']==v13.SCHEMA
    assert report['selected'] in v13.ARMS
    assert report['promotion_authorized'] is False
    assert len(report['comparison'])==4
    assert (p.output/'focus_train_margins.csv').exists()
    assert (p.output/'consolidated_start_candidate.pt').exists()
    for arm in v13.ARMS:
        for state in ('selected','terminal'):
            cp=torch.load(p.output/f'{arm}_{state}_candidate.pt',weights_only=True)
            assert cp['status']=='DISPOSABLE_NON_PRODUCTION'
            assert cp['promotion_authorized'] is False
            assert cp['mathematical_train_retention_ledger']['source_split']=='SYNTHETIC_TRAIN_ONLY'
    with pytest.raises(FileExistsError):
        v13.run(p)


@pytest.mark.parametrize('change',[{'steps':0},{'reserve':0},{'reserve':.5},
    {'margin_width':1},{'lr':float('nan')},{'consolidation_steps':0}])
def test_invalid_arguments_before_capture(monkeypatch,tmp_path,change):
    monkeypatch.setattr(joint,'_prepare',lambda args:pytest.fail('capture opened'))
    values=dict(run=tmp_path/'r',expanded_run=tmp_path/'e',input=tmp_path/'i',
      source_features=tmp_path/'s',prior=tmp_path/'p',v11_run=tmp_path/'v11',
      v12_run=tmp_path/'v12',output=tmp_path/'v13',device='cpu',steps=3,
      consolidation_steps=3,eval_every=1,lr=.001,consolidation_lr=.001,
      reserve=.08,margin_width=16,max_halvings=2,projection_passes=2,
      max_constraints=4,replay_weight=2.,seed=49,focus_group='train.offset.0041')
    values.update(change)
    with pytest.raises(ValueError,match='BAD_CONFIGURATION'):
        v13.run(SimpleNamespace(**values))
