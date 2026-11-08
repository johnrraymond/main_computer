"""V21: true-double shadow accumulation with unchanged float32 inference gate."""
from __future__ import annotations

import copy
import math
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_shadow_precision_v21 as v21
from tools import nanojev_gamer_v1_math_retention_v9 as v9


@pytest.fixture(autouse=True)
def _cpu_threads():
    previous=torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


class SmallModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.base=torch.nn.Linear(2,2,bias=False)
        self.base.requires_grad_(False)
        self.weight=torch.nn.Parameter(torch.tensor([.02,.03],dtype=torch.float32))
    def forward(self,x):
        return x@self.weight


def _toy(monkeypatch,gate=None):
    model=SmallModel()
    geom={'encoded':torch.eye(2),'old':torch.zeros(2),
          'truth':np.array([-1,1],dtype=np.int64),
          'required':np.array([.08,.08],dtype=np.float32),
          'mask':np.array([True,True]),'deficient':[0,1],
          'rows':[{'id':'A'},{'id':'B'}],
          'anchor_weights':v9._copy_state(model)}
    monkeypatch.setattr(v21.v13,'_class_margins',
       lambda torch_mod,net,encoded,old,truth:net(encoded))
    def exact(torch_mod,net,geometry,spec,mu,sd,device,*,scope):
        margins,deficits=v21.v13._margin_census(torch_mod,net,geometry['encoded'],
                   geometry['old'],geometry['truth'],geometry['required'])
        allowed=bool(gate(*margins)) if gate is not None else True
        correct=np.asarray([allowed,True],dtype=bool)
        count=float(deficits[geometry['mask']].sum())
        return {'retention_pass':allowed,'protected_lost':int(not allowed),
           'total_deficit':count,'correct_train':int(correct.sum()),
           'all_protected_reserves_met':bool((deficits[geometry['mask']]<=1e-6).all()),
           'scope':scope},margins,deficits,correct
    monkeypatch.setattr(v21.v16,'_actual_evaluation',exact)
    monkeypatch.setattr(v21.v6,'_evaluate',
             lambda *a:({'status':'PASS'},None))
    return model,geom


def _run(model,geom,**settings):
    kw=dict(iterations=18,stalls_max=4,target_step=2e-6,
       min_shadow_gain=1e-13,min_runtime_gain=2e-7,
       min_runtime_merit=3e-7,trust_radius=.05,
       max_halvings=4,fd_epsilon=.002,trial_rows=[])
    kw.update(settings)
    return v21._solve(torch,model,geom,None,None,None,'cpu',**kw)


def test_double_shadow_is_independent_does_not_unfreeze_base(monkeypatch):
    model,geom=_toy(monkeypatch)
    original=v9._copy_state(model)
    double,gd=v21._shadow(torch,model,geom)
    assert all(p.dtype==torch.float64 for p in double.parameters())
    assert all(p.dtype==torch.float32 for p in model.parameters())
    assert all(not p.requires_grad for p in double.base.parameters())
    assert gd['encoded'].dtype==torch.float64
    assert gd['old'].dtype==torch.float64
    with torch.no_grad():double.weight.add_(1e-8)
    assert torch.equal(model.weight,original['weight'])


def test_double_qp_nearly_opposing_gradients_and_derivatives(monkeypatch):
    a=torch.tensor([1.,0.],dtype=torch.float64)
    b=torch.tensor([-.99,.15],dtype=torch.float64)
    direction,info=v21._joint64(torch,[a,b],[.001,.001],.05)
    assert direction.dtype==torch.float64
    assert all(float(torch.dot(g,direction))>0 for g in (a,b))
    assert info['active_set']=='TWO_ACTIVE'
    model,geom=_toy(monkeypatch)
    shadow,gd=v21._shadow(torch,model,geom)
    state=v9._copy_state(shadow)
    directions=v21._gradient64(torch,shadow,gd,[0,1])
    assert all(g.dtype==torch.float64 for g in directions)
    check=v21._finite_difference64(torch,shadow,gd,state,[0,1],
               torch.tensor([0.,0.,0.,0.,1.,1.],dtype=torch.float64),.002)
    assert check['trusted']
    assert torch.equal(shadow.weight,state['weight'])


def test_runtime_gate_refuses_forgetting_and_restores_weights(monkeypatch):
    model,geom=_toy(monkeypatch,gate=lambda a,b:a<=.02)
    state=v9._copy_state(model)
    shadow,_=v21._shadow(torch,model,geom)
    with torch.no_grad():shadow.weight[0].add_(.002)
    assessment,pair,correct,safe=v21._runtime_probe(torch,model,geom,None,
                                None,None,'cpu',v9._copy_state(shadow),geom['mask'],[0,1])
    assert not safe and assessment['protected_lost']==1
    assert pair[0]>.02
    assert torch.equal(model.weight,state['weight'])


def test_shadow_can_accumulate_and_export_only_after_actual_runtime_gain(monkeypatch):
    model,geom=_toy(monkeypatch)
    state=v9._copy_state(model)
    summary,best,mask=_run(model,geom,iterations=20)
    assert summary['shadow_accepted_steps']>=2
    assert summary['checkpoint_saved'] is True
    assert summary['float32_export_updates']>=1
    assert best['weight'].dtype==torch.float64
    assert summary['best_export_float32_deficit']<summary['baseline_float32_deficit']
    assert mask.tolist()==[True,True]
    assert torch.equal(model.weight,state['weight'])
    assert all(torch.equal(model.state_dict()[k],v) for k,v in state.items())


def test_shadow_only_gain_not_published_as_runtime_gain(monkeypatch):
    model,geom=_toy(monkeypatch)
    state=v9._copy_state(model)
    result,best,mask=_run(model,geom,iterations=5,
                          min_runtime_gain=.0005,min_runtime_merit=.0005)
    assert result['shadow_accepted_steps']>0
    assert result['status']=='SHADOW_ONLY_IMPROVEMENT'
    assert result['checkpoint_saved'] is False and best is None
    assert torch.equal(model.weight,state['weight'])


def test_rejection_preserves_original_even_when_double_margin_improves(monkeypatch):
    model,geom=_toy(monkeypatch,gate=lambda a,b:a<=.02)
    before=v9._copy_state(model)
    result,best,mask=_run(model,geom,iterations=4,stalls_max=2)
    assert result['shadow_accepted_steps']==0
    assert best is None
    assert torch.equal(model.weight,before['weight'])


def test_export_criteria_require_real_float32_joint_and_aggregate_gain():
    ok=v21._runtime_exportable
    assert not ok([.021,.02],.13,[.02,.03],.14,min_gain=1e-7,min_merit=1e-7)
    assert not ok([.021,.031],.14,[.02,.03],.14,min_gain=1e-7,min_merit=1e-7)
    assert not ok([.02000001,.031],.12,[.02,.03],.14,min_gain=1e-7,min_merit=1e-7)
    assert ok([.021,.031],.12,[.02,.03],.14,min_gain=1e-7,min_merit=1e-7)


def test_replay_provenance_fails_closed():
    with pytest.raises(ValueError,match='V21_V20_VERIFIED_REPLAY_MISMATCH'):
        v21._validate_v20({'schema':'wrong'}, {}, 'x','y','z')


def test_parser_requires_complete_lineage_and_supports_no_new_train_capacity():
    p=v21.parser();names={a.dest for a in p._actions}
    assert {'v20_run','v19_run','v18_run','v14_run','output'}<=names
    assert p.get_default('max_iterations')==60
    assert p.get_default('min_shadow_gain') < p.get_default('min_runtime_gain')
    assert p.get_default('mode')=='solve'


def test_sub_resolution_double_updates_accumulate_until_float32_can_see_them(monkeypatch):
    model,geom=_toy(monkeypatch)
    shadow,_=v21._shadow(torch,model,geom)
    initial=float(model.weight[0].detach())
    original=v9._copy_state(model)
    with torch.no_grad():
        shadow.weight[0].add_(3e-10)
    model.load_state_dict(shadow.state_dict(),strict=True)
    assert float(model.weight[0].detach())==initial
    # Crucially, the second shadow step is added to the double-precision
    # parameter, NOT to the already-rounded float32 runtime parameter.
    with torch.no_grad():
        for _ in range(19):
            shadow.weight[0].add_(3e-10)
    model.load_state_dict(shadow.state_dict(),strict=True)
    assert float(model.weight[0].detach())>initial
    assert shadow.weight.dtype==torch.float64
    model.load_state_dict(original,strict=True)
