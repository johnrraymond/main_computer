"""V17: actual directional derivatives, signed probes, immutable provenance."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_gradient_sign_audit_v17 as v17
from tools import nanojev_gamer_v1_nonlinear_feasibility_v16 as v16
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tests.test_nanojev_gamer_v1_nonlinear_feasibility_v16 import _make_v15


@pytest.fixture(autouse=True)
def _threads():
    before=torch.get_num_threads();torch.set_num_threads(2)
    yield
    torch.set_num_threads(before)


def test_steps_are_strictly_positive_deterministic_and_bounded():
    assert v17._steps('.0001,.0005,.002')==[.0001,.0005,.002]
    for bad in ('','.01','.01,.01','.1,.01','0,.1','nan,.1','inf,.1','a,b','-1,.2','.2,2'):
        with pytest.raises(Exception):v17._steps(bad)


def _two_dim_exact(torch_mod,model,geom,spec,mu,sd,device,*,scope):
    v=model(geom['encoded']).reshape(-1).detach().cpu().numpy()
    margins=v.astype(np.float64)
    deficits=np.maximum(np.asarray(geom['required'])-margins,0.)
    correct=np.array([True,True])
    safe=True
    return {'retention_pass':safe,'protected_lost':0,
        'total_deficit':float(deficits.sum()),'focus_min_safety':float(margins.min()),
        'deficient_remaining':int((deficits>1e-9).sum()),
        'all_protected_reserves_met':bool((deficits<=1e-9).all()),
        'correct_train':2,'scope':scope},margins,deficits,correct


def _simple_geometry():
    net=torch.nn.Linear(2,1,bias=False)
    with torch.no_grad():net.weight.zero_()
    x=torch.eye(2)
    geom={'rows':[{'id':'case-a'},{'id':'case-b'}],
        'deficient':[0,1],'encoded':x,'old':torch.zeros(2),
        'truth':np.array([-1,1]),'mask':np.array([True,True]),
        'required':np.array([.08,.08]),'anchor_weights':v9._copy_state(net)}
    return net,geom


def test_signed_finite_difference_detects_gradients_and_restores_anchor(monkeypatch):
    net,geom=_simple_geometry()
    monkeypatch.setattr(v17.v13,'_class_margins',lambda torch_mod,model,encoded,old,truth:
                        model(encoded).reshape(-1))
    trials,checks,diagnostic=v17._finite_difference_audit(torch,net,geom,None,None,None,
                            'cpu',[.001,.01],execute_exact=_two_dim_exact)
    assert len(trials)>=2*2*4
    assert diagnostic['gradient_norms']==pytest.approx([1.,1.])
    assert diagnostic['gradient_cosine']==pytest.approx(0.)
    assert all(c['within_tolerance'] for c in checks)
    assert all(c['sign_agrees'] for c in checks)
    assert any(t['deficit_improvement']>0 for t in trials)
    assert all(t['protected_retention_pass'] for t in trials)
    assert torch.equal(net.weight.detach(),geom['anchor_weights']['weight'])
    outcome=v17._interpret(trials,checks,[.001,.01])
    assert outcome['status']=='BOTH_SIGN_LOCAL_PROBES_CONFIRM_GRADIENTS'
    assert outcome['retention_safe_merit_improvements']>0


def test_mismatched_analytic_sign_is_not_silently_accepted(monkeypatch):
    net,geom=_simple_geometry()
    monkeypatch.setattr(v17.v13,'_class_margins',lambda torch_mod,model,encoded,old,truth:
                        model(encoded).reshape(-1))
    true= v17._direction_vectors
    def flipped(torch_mod,model,geom):
        directions,summary=true(torch_mod,model,geom)
        # Exact evaluations are untouched; simulated erroneous gradient
        # construction reports the wrong signs for each case.
        return directions,summary
    monkeypatch.setattr(v17,'_direction_vectors',flipped)
    # Wrong predicted derivatives emulate a bug in analytic gradient
    # collection; the finite-difference consistency verdict must flag it.
    original_flat=v17._flat_gradient
    def wrong_gradient(torch_mod,model,scalar):
        return -original_flat(torch_mod,model,scalar)
    monkeypatch.setattr(v17,'_flat_gradient',wrong_gradient)
    trials,checks,_=v17._finite_difference_audit(torch,net,geom,None,None,None,
                         'cpu',[.001,.01],execute_exact=_two_dim_exact)
    result=v17._interpret(trials,checks,[.001,.01])
    assert result['status']=='FINITE_DIFFERENCE_DIRECTION_MISMATCH_REQUIRES_INVESTIGATION'
    assert result['local_sign_disagreements']>0
    assert torch.equal(net.weight.detach(),geom['anchor_weights']['weight'])


def test_retention_gate_is_reported_separately_from_nonlinear_improvement(monkeypatch):
    net,geom=_simple_geometry()
    monkeypatch.setattr(v17.v13,'_class_margins',lambda torch_mod,model,encoded,old,truth:
                        model(encoded).reshape(-1))
    def constrained(torch_mod,model,geom,spec,mu,sd,device,*,scope):
        data=_two_dim_exact(torch_mod,model,geom,spec,mu,sd,device,scope=scope)
        result,margins,deficits,correct=data
        # Sign classification lost at 0.005 change to the first parameter.
        if float(model.weight.detach()[0,0])>.005:
            result['retention_pass']=False
            result['protected_lost']=1
            correct=np.array([False,True])
        return result,margins,deficits,correct
    trials,checks,_=v17._finite_difference_audit(torch,net,geom,None,None,None,
                         'cpu',[.001,.02],execute_exact=constrained)
    assert any(t['deficit_improvement']>0 and not t['protected_retention_pass'] for t in trials)
    assert any(t['deficit_improvement']>0 and t['protected_retention_pass'] for t in trials)
    assert torch.equal(net.weight.detach(),geom['anchor_weights']['weight'])


def _run_v16(monkeypatch,tmp_path):
    _,_,_,_,prior,focus,proof,common=_make_v15(monkeypatch,tmp_path)
    args=SimpleNamespace(mode='study',run=common['run'],expanded_run=common['expanded_run'],
       input=common['input'],source_features=common['source_features'],
       prior=prior,v11_run=tmp_path/'v11',v14_run=tmp_path/'v14',
       v15_run=tmp_path/'v15',output=tmp_path/'v16',device='cpu',focus_group=focus,
       trust_radii=[.01,.05],max_gradient_cases=8,max_iterations=2,
       max_halvings=2,reserve=.08,min_improvement=1e-8,eval_every=1)
    v16.run(args)
    return args


def test_e2e_read_only_verified_v16_audit(monkeypatch,tmp_path):
    a=_run_v16(monkeypatch,tmp_path)
    # The fixture may have a different number of deficient cases than the
    # measured V16 CUDA production run; simulate the production scenario only
    # when exactly two deficient cases are available.
    args=SimpleNamespace(**dict(vars(a),mode='audit',v16_run=tmp_path/'v16',
                         output=tmp_path/'v17',steps=[.0005,.002]))
    result=v17.run(args)
    assert result['read_only'] is True
    assert result['production_weights_modified'] is False
    assert result['checkpoint_saved'] is False
    assert result['baseline_protected_train']>=1
    assert (tmp_path/'v17'/'finite_difference_trials.csv').is_file()
    assert (tmp_path/'v17'/'gradient_consistency.csv').is_file()
    assert (tmp_path/'v17'/'deficient_case_gradients.csv').is_file()
    with pytest.raises(FileExistsError):v17.run(args)


def test_v16_lineage_or_training_policy_tamper_refused(monkeypatch,tmp_path):
    a=_run_v16(monkeypatch,tmp_path)
    path=tmp_path/'v16'/'report.json'
    report=json.loads(path.read_text())
    report['production_weights_modified']=True
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='V17_V16_PROVENANCE_MISMATCH'):
        v17._checked_v16(tmp_path/'v16',report['v15_report_sha256'],
         report['training_capture_sha256'],report['v14_verified'],
         {'probe_info':{'lineage':report['frozen_lineage']}},
         report['v6_prior_sha256'],report['focus_group'],.08)
