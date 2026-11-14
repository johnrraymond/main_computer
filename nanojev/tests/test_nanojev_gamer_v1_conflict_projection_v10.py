"""V10 conflict atlas, first-order update repair, hard admissibility tests."""
from __future__ import annotations

import copy
import csv
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tests.test_nanojev_gamer_v1_protected_alignment_lab import _verified_small
from tests.test_nanojev_gamer_v1_protected_math_repair_v7 import _checkpoint
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint


@pytest.fixture(autouse=True)
def _threads():
    prior=torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(prior)


def _setup():
    data, prepared=_verified_small()
    spec,_=v6._routed_profiles(data,prepared)
    sx=spec['train_source'][0]
    tx=spec['train_synthetic'][0]
    mu,sd=joint._normalization(sx,tx)
    net=v6._build_aligned_model(torch,protected=True)
    return data,prepared,spec,mu,sd,net


def test_projection_drops_violating_component_without_destroying_useful_update():
    d=torch.tensor([-1.,2.,3.])
    p,stats=v10._project_halfspaces(d,[torch.tensor([1.,0.,0.])],3)
    assert p[0].item()==pytest.approx(0.,abs=1e-7)
    assert p[1].item()==pytest.approx(2.)
    assert p[2].item()==pytest.approx(3.)
    assert stats['violations_before']==1
    assert stats['violations_after']==0
    assert torch.linalg.vector_norm(p)<=torch.linalg.vector_norm(d)
    assert stats['direction_cosine']>0
    assert torch.equal(d,torch.tensor([-1.,2.,3.]))


def test_projection_handles_multiple_constraints_and_refuses_bad_gradients():
    d=torch.tensor([-1.,-2.,4.])
    p,stats=v10._project_halfspaces(d,[torch.tensor([1.,0.,0.]),
       torch.tensor([0.,1.,0.])],4)
    assert p.tolist()==pytest.approx([0.,0.,4.])
    assert stats['violations_before']==2
    assert stats['violations_after']==0
    with pytest.raises(ValueError,match='INVALID_CONSTRAINT'):
        v10._project_halfspaces(d,[torch.ones(2)],3)
    with pytest.raises(ValueError,match='INVALID_PROJECTED'):
        v10._project_halfspaces(d,[],0)
    with pytest.raises(ValueError,match='INVALID_CONSTRAINT'):
        v10._project_halfspaces(d,[torch.tensor([0.,float('nan'),1.])],2)


def _toy_update(monkeypatch,arm):
    rows=[]
    for i in range(6):
        target=(-1,0,1)[i%3]
        rows.append({'id':str(i),'group':'train.offset.0000',
                     'surface':'source_native' if i<3 else 'spine_difference',
                     'prompt_sha256':str(i)*64,'target':str(target)})
    groups=[(np.arange(6,dtype=np.int64),None)]
    memory=v9.MathematicalLedger(np.ones(6,dtype=bool),rows,groups)
    model=torch.nn.Linear(1,2,bias=False)
    with torch.no_grad():model.weight.zero_()
    optimizer=torch.optim.AdamW(model.parameters(),lr=.01)
    initial=v9._copy_state(model)
    oldopt=copy.deepcopy(optimizer.state_dict())
    # A proposed task update harms the protected direction but improves an
    # independent direction. The first-order projection must rescue the latter.
    def propose(t,net,opt,*args,**kwargs):
        with torch.no_grad():
            net.weight[0,0]=-1.
            net.weight[1,0]=2.
        return 1.,0.
    def check(t,net,spec,mu,sigma,device):
        is_safe=float(net.weight[0,0].detach())>=-1e-7
        sign=np.array([(-1,0,1)[i%3] if is_safe else 1 for i in range(6)])
        truth=np.array([(-1,0,1)[i%3] for i in range(6)])
        return sign==truth,sign,truth
    monkeypatch.setattr(v10,'_loss_and_proposal',propose)
    monkeypatch.setattr(v10,'_full_train_correct',check)
    monkeypatch.setattr(v10,'_margin_gradients',lambda *a,**kw:[torch.tensor([1.,0.])])
    monkeypatch.setattr(v10,'_case_margins',lambda t,net,spec,mu,sigma,device,indices:
       {int(i):{'true_class_margin':float(net.weight[0,0].detach()),
                 'prediction':(-1,0,1)[int(i)%3],
                 'value':float(net.weight[1,0].detach()),
                 'residual_signed_log':float(net.weight[1,0].detach()),
                 'residual_logit_l2':0.} for i in indices})
    tiny=torch.zeros(6,1)
    spec={'train_synthetic':(None,None,None,rows,None)}
    outcome,correct,traces=v10._attempt_update(torch,model,optimizer,
        (tiny,torch.zeros(6),torch.zeros(6)),groups,memory,spec,None,None,
        device='cpu',step=1,arm=arm,max_halvings=3,
        replay_weight=2.,projection_passes=3,max_constraints=4)
    assert outcome['raw_full_lost']>0
    assert outcome['raw_smallest_lost']>0
    assert any(r['group']=='train.offset.0000' for r in traces)
    assert optimizer.state_dict()['state']==oldopt['state']=={}
    if arm=='strict_hard_gate':
        assert outcome['status']=='rejected'
        assert correct is None
        assert all(torch.equal(model.state_dict()[k],b) for k,b in initial.items())
    else:
        assert outcome['status']=='projected'
        assert outcome['projected_full_lost']==0
        assert bool(correct.all())
        assert float(model.weight[0,0].detach())==pytest.approx(0.,abs=1e-7)
        assert float(model.weight[1,0].detach())==pytest.approx(2.)


def test_projection_can_rescue_update_but_hard_gate_still_controls(monkeypatch):
    _toy_update(monkeypatch,'conflict_projected')


def test_identical_proposal_is_rejected_without_projection(monkeypatch):
    _toy_update(monkeypatch,'strict_hard_gate')


def test_full_hard_gate_vetoes_unsafe_projected_update(monkeypatch):
    _,_,spec,mu,sd,model=_setup()
    groups,_=v9._groups_and_fingerprints(spec)
    rows=spec['train_synthetic'][3]
    ledger=v9.MathematicalLedger(np.ones(len(rows),dtype=bool),rows,groups)
    x=torch.from_numpy(v6._encode(spec['train_synthetic'][0],
        spec['train_synthetic'][1],spec['train_synthetic'][-1],mu,sd))
    old=torch.as_tensor(spec['train_synthetic'][1],dtype=torch.float32)
    y=torch.as_tensor(spec['train_synthetic'][2],dtype=torch.float32)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.003)
    before=v9._copy_state(model)
    prior=copy.deepcopy(optimizer.state_dict())
    monkeypatch.setattr(v10,'_full_train_correct',lambda *a:
        (np.zeros(len(rows),dtype=bool),np.zeros(len(rows),dtype=int),
         np.asarray([v10.v7._signed(v10.Fraction(str(r['target']))) for r in rows])))
    monkeypatch.setattr(v10,'_margin_gradients',lambda *a,**kw:[])
    monkeypatch.setattr(v10,'_case_margins',lambda t,net,spec,mu,sigma,device,indices:
       {int(i):{'true_class_margin':0.,'prediction':0,'value':0.,
                 'residual_signed_log':0.,'residual_logit_l2':0.} for i in indices})
    outcome,correct,_=v10._attempt_update(torch,model,optimizer,(x,old,y),
        groups[:4],ledger,spec,mu,sd,device='cpu',step=1,
        arm='conflict_projected',max_halvings=2,replay_weight=2.,
        projection_passes=2,max_constraints=8)
    assert outcome['status']=='rejected'
    assert correct is None
    assert outcome['projected_smallest_lost']==len(rows)
    assert all(torch.equal(before[k],val) for k,val in model.state_dict().items())
    assert optimizer.state_dict()['state']==prior['state']


@pytest.mark.parametrize('bad',[{'steps':0},{'lr':0.},
    {'max_constraints':0},{'projection_passes':0},{'replay_weight':-1.},
    {'max_halvings':13},{'lr':float('nan')}])
def test_invalid_config_blocks_source_access(monkeypatch,tmp_path,bad):
    monkeypatch.setattr(joint,'_prepare',lambda args:pytest.fail('source accessed'))
    cfg=dict(device='cpu',run=tmp_path/'r',expanded_run=tmp_path/'e',
        input=tmp_path/'i',source_features=tmp_path/'s',prior=tmp_path/'p',
        v9_run=tmp_path/'v9',output=tmp_path/'o',steps=2,eval_every=1,
        lr=.002,seed=49,replay_weight=2.,max_halvings=2,
        projection_passes=2,max_constraints=6)
    cfg.update(bad)
    with pytest.raises(ValueError,match='INVALID_CONFIGURATION'):
        v10.run(SimpleNamespace(**cfg))


def test_end_to_end_v9_provenance_controls_and_v10_monotonic_candidates(monkeypatch,tmp_path):
    data,prepared,spec,mu,sd,model=_setup()
    prior=tmp_path/'prior'/'disposable_candidate.pt'
    prior.parent.mkdir()
    _checkpoint(prior,data,mu,sd)
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,prepared))
    monkeypatch.setattr(joint,'_verify_after',lambda data:None)
    cfg=dict(device='cpu',run=tmp_path/'r',expanded_run=tmp_path/'e',
       input=tmp_path/'i',source_features=tmp_path/'s',prior=prior,
       v9_run=tmp_path/'v9',output=tmp_path/'v10',steps=3,eval_every=1,
       lr=.002,seed=49,replay_weight=2.,max_halvings=2,
       projection_passes=2,max_constraints=6)
    v9cfg=SimpleNamespace(**{k:v for k,v in cfg.items() if k not in
                           ('v9_run','projection_passes','max_constraints')})
    v9cfg.output=cfg['v9_run']
    v9.run(v9cfg)
    result=v10.run(SimpleNamespace(**cfg))
    assert result['status']=='EXPERIMENT_COMPLETE_NO_PROMOTION'
    assert result['selected_candidate'] in v10.ARMS
    assert len(result['candidates'])==2
    assert result['v9_historical_reference']['historical_v9_training']['train_only_memory']
    assert result['v9_historical_reference']['report_sha256']
    assert not result['synthetic_dev_used_for_training']
    assert not result['previously_seen_test_used_for_training_or_selection']
    assert not result['production_weights_modified']
    assert not result['promotion_authorized']
    assert (cfg['output']/'strict_hard_gate_attempts.csv').is_file()
    assert (cfg['output']/'conflict_projected_attempts.csv').is_file()
    assert (cfg['output']/'conflict_projected_conflict_cases.csv').is_file()
    assert (cfg['output']/'comparison.csv').is_file()
    with (cfg['output']/'comparison.csv').open() as fh:
        assert len(list(csv.DictReader(fh)))==2
    for arm in v10.ARMS:
        cp=torch.load(cfg['output']/(arm+'_candidate.pt'),weights_only=True)
        assert cp['promotion_authorized'] is False
        assert cp['mathematical_train_retention_ledger']['source_split']=='SYNTHETIC_TRAIN_ONLY'
        assert cp['protected_original_passthrough'] is True
        assert len(cp['mathematical_train_retention_ledger']['rows'])==24
    with pytest.raises(FileExistsError):
        v10.run(SimpleNamespace(**cfg))
    # Audit must reject even small historical configuration drift.
    with pytest.raises(ValueError,match='HISTORICAL_V9_PROVENANCE_OR_SETTINGS'):
        v10._verify_v9_record(torch,cfg['v9_run'],SimpleNamespace(**{**cfg,'seed':50}),
               data,v10.hashlib.sha256(prior.read_bytes()).hexdigest(),
               result['training_capture_sha256'])


def test_actual_conflict_margins_are_finite_and_train_only():
    _,_,spec,mu,sd,model=_setup()
    metrics=v10._case_margins(torch,model,spec,mu,sd,'cpu',[0,1,2])
    assert set(metrics)=={0,1,2}
    assert all(np.isfinite(v['true_class_margin']) for v in metrics.values())
    assert all(v['prediction'] in (-1,0,1) for v in metrics.values())
    assert all(np.isfinite(v['value']) for v in metrics.values())
