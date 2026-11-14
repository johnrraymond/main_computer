"""V18: exact-joint QP, signed derivative and immutable checkpoint regression."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_joint_constraint_v18 as v18
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_nonlinear_feasibility_v16 as v16
from tools import nanojev_gamer_v1_gradient_sign_audit_v17 as v17
from tests.test_nanojev_gamer_v1_gradient_sign_audit_v17 import _run_v16


@pytest.fixture(autouse=True)
def _two_threads():
    previous=torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


def test_two_halfspace_qp_improves_both_nearly_opposed_gradients():
    a=torch.tensor([1.,0.])
    b=torch.tensor([-.99,.15])
    direction,info=v18._joint_min_norm(torch,a,b,[.001,.001],.1)
    assert info['status']=='LOCAL_QP_WITHIN_TRUST'
    assert info['active_set']=='TWO_ACTIVE'
    assert float(torch.dot(a,direction))>=.0009999
    assert float(torch.dot(b,direction))>=.0009999
    assert info['condition_number']>10
    limited,other=v18._joint_min_norm(torch,a,b,[.001,.001],.001)
    assert other['status']=='LOCAL_QP_TRUST_SCALED'
    assert float(torch.linalg.vector_norm(limited))<=.00100001
    assert float(torch.dot(a,limited))>0 and float(torch.dot(b,limited))>0


def test_inconsistent_exactly_opposed_gradients_refuse_joint_claim():
    d,info=v18._joint_min_norm(torch,torch.tensor([1.,0.]),
                                torch.tensor([-1.,0.]),[.01,.01],.5)
    assert d is None and info['status']=='DEGENERATE_OR_INCONSISTENT_LOCAL_QP'


class ToyModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.weight=torch.nn.Parameter(torch.zeros(2))
    def forward(self,x):
        return x@self.weight


def _toy(monkeypatch, gate=lambda a,b:True, objective=None):
    net=ToyModel()
    geom={'encoded':torch.eye(2),'old':torch.zeros(2),
          'truth':np.array([-1,1]),'required':np.array([.08,.08]),
          'mask':np.array([True,True]),'deficient':[0,1],
          'rows':[{'id':'A'},{'id':'B'}],
          'anchor_weights':v9._copy_state(net), 'initial_deficit':.16}
    monkeypatch.setattr(v18.v13,'_class_margins',
       lambda torch_mod,model,encoded,old,truth:model(encoded))
    def exact(torch_mod,model,geometry,spec,mu,sd,device,*,scope):
        with torch.no_grad():
            vals=model(geometry['encoded']).detach().cpu().numpy()
        deficits=np.maximum(np.array([.08,.08])-vals,0.)
        merit=float(deficits.sum()) if objective is None else float(objective(*vals))
        allowed=gate(*vals)
        return ({'retention_pass':bool(allowed),'protected_lost':int(not allowed),
                 'total_deficit':merit,'focus_min_safety':float(vals.min()),
                 'deficient_remaining':int((deficits>1e-8).sum()),
                 'all_protected_reserves_met':bool((deficits<=1e-8).all()),
                 'correct_train':2 if allowed else 1,'scope':scope},
                 vals,deficits,np.array([bool(allowed),True]))
    monkeypatch.setattr(v18.v16,'_actual_evaluation',exact)
    return net,geom


def _search(net,geom,**kwargs):
    defaults=dict(max_iterations=3,target_step=.001,trust_radius=.02,
                  max_halvings=3,fd_epsilon=.002,min_joint_gain=1e-6,
                  path_step=.002,enable_path=False,trial_rows=[])
    defaults.update(kwargs)
    return v18._sequential_search(torch,net,geom,None,None,None,'cpu',**defaults)


def test_joint_solver_commits_only_both_improvements_and_relinearizes(monkeypatch):
    net,geom=_toy(monkeypatch)
    trace=[]
    result,best=_search(net,geom,max_iterations=3,trial_rows=trace)
    assert result['accepted_iterations']>=2
    assert result['joint_improved'] is True
    assert all(v>0 for v in result['final_safety'])
    assert result['best_deficit']<.16
    assert all(r['retention_pass'] for r in trace if r.get('joint_improved'))
    assert all(r['after_a']>r['before_a'] and r['after_b']>r['before_b']
               for r in trace if r.get('stage')=='direct_qp' and r.get('joint_improved'))
    assert torch.equal(net.weight.detach(),geom['anchor_weights']['weight'])
    assert all(v>0 for v in best['weight'])
    assert result['status']=='JOINT_PARTIAL_IMPROVEMENT'


def test_hard_train_gate_blocks_even_jointly_improving_steps(monkeypatch):
    net,geom=_toy(monkeypatch,gate=lambda a,b: a<=.0001)
    trace=[]
    result,best=_search(net,geom,max_iterations=2,trial_rows=trace,min_joint_gain=.0002)
    assert result['accepted_iterations']==0
    assert any(row['protected_lost']>0 for row in trace if row.get('stage')=='direct_qp')
    assert not result['joint_improved']
    assert torch.equal(best['weight'],geom['anchor_weights']['weight'])
    assert torch.equal(net.weight.detach(),geom['anchor_weights']['weight'])


def test_non_improving_endpoint_is_rejected_even_with_retention(monkeypatch):
    net,geom=_toy(monkeypatch,objective=lambda a,b:.16+abs(a)+abs(b))
    result,best=_search(net,geom,max_iterations=2)
    assert result['accepted_iterations']==0
    assert result['best_deficit']==pytest.approx(.16)
    assert not result['joint_improved']
    assert torch.equal(net.weight.detach(),geom['anchor_weights']['weight'])


def test_broken_analytic_derivative_refuses_direction(monkeypatch):
    net,geom=_toy(monkeypatch)
    old=v18._two_gradients
    monkeypatch.setattr(v18,'_two_gradients',lambda *a:[-x for x in old(*a)])
    result,best=_search(net,geom,max_iterations=1)
    assert result['accepted_iterations']==0
    assert result['line_checks'][0]['trusted'] is False
    assert torch.equal(net.weight.detach(),geom['anchor_weights']['weight'])


def test_unsafe_intermediate_cannot_be_committed(monkeypatch):
    net,geom=_toy(monkeypatch,gate=lambda a,b: abs(a)<=.00005)
    trace=[]
    result,best=_search(net,geom,max_iterations=1,enable_path=True,trial_rows=trace,
                        min_joint_gain=.0002)
    assert any(row.get('stage')=='path_intermediate' and not row['retention_pass']
               for row in trace)
    assert result['accepted_iterations']==0
    assert torch.equal(best['weight'],geom['anchor_weights']['weight'])


def test_v17_provenance_refuses_tampering(tmp_path):
    r={'schema':v17.SCHEMA,'status':'DIAGNOSTIC_COMPLETE_NO_PROMOTION',
       'mode':'audit','read_only':True,'checkpoint_saved':False,
       'promotion_authorized':False,'production_weights_modified':False,
       'general_acquisition_training':False,'TRAIN_only_gradients_and_probes':True,
       'hard_train_retention_unchanged':True,
       'synthetic_dev_used_for_training_or_selection':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'v16_report_sha256':'v16','training_capture_sha256':'capture',
       'v14_verified':{'x':'y'},'frozen_lineage':{'a':'b'},
       'v6_prior_sha256':'prior','focus_group':'group','baseline_protected_train':2,
       'decision':{'sign_error_proven':False},'config':{'reserve':.08}}
    root=tmp_path/'v17';root.mkdir()
    (root/'report.json').write_text(json.dumps(r))
    data={'probe_info':{'lineage':{'a':'b'}}}
    output,h=v18._checked_v17(root,'v16','capture',{'x':'y'},data,'prior','group',.08)
    assert output==r and len(h)==64
    r['synthetic_dev_used_for_training_or_selection']=True
    (root/'report.json').write_text(json.dumps(r))
    with pytest.raises(ValueError,match='V18_V17_PROVENANCE_MISMATCH'):
        v18._checked_v17(root,'v16','capture',{'x':'y'},data,'prior','group',.08)


def test_e2e_v18_verified_chain_nonpromoting(monkeypatch,tmp_path):
    args16=_run_v16(monkeypatch,tmp_path)
    args17=SimpleNamespace(**dict(vars(args16),mode='audit',v16_run=tmp_path/'v16',
          output=tmp_path/'v17',steps=[.0005,.002]))
    v17.run(args17)
    args18=SimpleNamespace(**dict(vars(args17),mode='solve',v17_run=tmp_path/'v17',
          output=tmp_path/'v18',max_iterations=1,target_step=.0005,
          trust_radius=.01,max_halvings=1,fd_epsilon=.002,min_joint_gain=1e-6,
          path_step=.002,no_path=True))
    report=v18.run(args18)
    assert report['promotion_authorized'] is False
    assert report['production_weights_modified'] is False
    assert report['frozen_v11_specialist'] is True
    assert report['general_acquisition_training'] is False
    assert report['baseline_protected_train']>=1
    assert (tmp_path/'v18'/'joint_constraint_trials.csv').exists()
    assert (tmp_path/'v18'/'finite_difference_checks.csv').exists()
    assert (tmp_path/'v18'/'joint_iteration_summary.csv').exists()
    if report['checkpoint_saved']:
        cp=torch.load(tmp_path/'v18'/'joint_margin_disposable_candidate.pt',weights_only=True)
        assert cp['promotion_authorized'] is False
        assert cp['hard_full_train_retention'] is True
    with pytest.raises(FileExistsError):v18.run(args18)


def test_singular_anchor_qp_can_relinearize_after_safe_intermediate(monkeypatch):
    """A curved two-case region becomes jointly feasible after a safe seed move."""
    net=ToyModel()
    geom={'encoded':torch.eye(2),'old':torch.zeros(2),
          'truth':np.array([-1,1]),'required':np.array([.08,.08]),
          'mask':np.array([True,True]),'deficient':[0,1],
          'anchor_weights':v9._copy_state(net),'initial_deficit':.16}
    def nonlinear(torch_mod,model,encoded,old,truth):
        x,y=model.weight[0],model.weight[1]
        # At (0,0) the gradients are strictly opposed. A retention-safe
        # displacement in x unlocks an additional y gradient for case B.
        a=x;b=-x+1500*x*y
        return torch.where(encoded[:,0]>.5,a,b)
    monkeypatch.setattr(v18.v13,'_class_margins',nonlinear)
    def exact(torch_mod,model,geom,spec,mu,sd,device,*,scope):
        vals=nonlinear(torch_mod,model,geom['encoded'],None,None).detach().numpy()
        deficits=np.maximum(.08-vals,0.)
        return ({'retention_pass':True,'protected_lost':0,
                'total_deficit':float(deficits.sum()),'focus_min_safety':float(min(vals)),
                'deficient_remaining':int(sum(deficits>1e-8)),
                'all_protected_reserves_met':bool((deficits<=1e-8).all()),
                'correct_train':2,'scope':scope},
                vals,deficits,np.array([True,True]))
    monkeypatch.setattr(v18.v16,'_actual_evaluation',exact)
    records=[]
    result,best=_search(net,geom,max_iterations=1,target_step=.005,
                        trust_radius=.02,enable_path=True,trial_rows=records)
    assert records and any(r.get('stage')=='path_intermediate' for r in records)
    assert any(r.get('stage')=='path_final' for r in records)
    # Even when a two-step route is explored, only a jointly improving
    # retention-safe final state may be committed.
    if result['accepted_iterations']:
        assert result['joint_improved']
        assert all(x>0 for x in result['final_safety'])
    assert torch.equal(net.weight.detach(),geom['anchor_weights']['weight'])
