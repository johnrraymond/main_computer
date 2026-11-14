"""V19 tests: batch-aligned numerical authority, adaptive search, lineage."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_precision_continuation_v19 as v19
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_joint_constraint_v18 as v18
from tools import nanojev_gamer_v1_gradient_sign_audit_v17 as v17
from tests.test_nanojev_gamer_v1_joint_constraint_v18 import ToyModel, _toy
from tests.test_nanojev_gamer_v1_gradient_sign_audit_v17 import _run_v16


@pytest.fixture(autouse=True)
def _threads():
    current=torch.get_num_threads();torch.set_num_threads(2)
    yield
    torch.set_num_threads(current)


def _search(net,geom,**settings):
    kw=dict(max_iterations=4,max_stalls=3,target_step=.002,
       min_target_step=1e-6,trust_radius=.03,max_halvings=4,
       fd_epsilon=.002,min_joint_gain=2e-7,min_merit_gain=3e-7,
       trial_rows=[])
    kw.update(settings)
    return v19._search(torch,net,geom,None,None,None,'cpu',**kw)


def test_census_aligned_grads_use_the_full_fixed_batch(monkeypatch):
    model=ToyModel()
    sizes=[]
    def margin(torch_mod,net,encoded,old,truth):
        sizes.append(len(encoded))
        # Intentional batch-size-dependent numerics; V19's measured margins
        # and gradients MUST use the same 128-aligned chunk.
        return net(encoded)+len(encoded)*.00001
    monkeypatch.setattr(v19.v13,'_class_margins',margin)
    x=torch.zeros(257,2)
    x[117,0]=1.
    x[135,1]=1.
    geom={'encoded':x,'old':torch.zeros(257),
          'truth':np.ones(257,dtype=np.int64),
          'required':np.ones(257,dtype=np.float32)*.08}
    margins=v19._census_pair(torch,model,geom,[117,135])
    grads=v19._census_gradients(torch,model,geom,[117,135])
    assert margins==pytest.approx([.00128,.00128],abs=1e-7)
    assert sizes==[128,128,1,128,128]
    assert grads[0].tolist()==pytest.approx([1.,0.])
    assert grads[1].tolist()==pytest.approx([0.,1.])


def test_consistent_signed_finite_difference(monkeypatch):
    model,geom=_toy(monkeypatch)
    state=v9._copy_state(model)
    check=v19._finite_difference(torch,model,geom,state,[0,1],torch.tensor([1.,1.]),.002)
    assert check['trusted'] is True
    assert check['reason']=='DERIVATIVES_AGREE'
    assert check['measurement_policy']=='V13_CENSUS_128_ALIGNED'
    assert all(abs(x-.70710678)<.001 for x in check['finite_difference'])
    assert torch.equal(model.weight.detach(),state['weight'])


def test_broken_analytic_gradient_fails_closed(monkeypatch):
    model,geom=_toy(monkeypatch)
    state=v9._copy_state(model)
    real=v19._census_gradients
    monkeypatch.setattr(v19,'_census_gradients',lambda *a:[-x for x in real(*a)])
    check=v19._finite_difference(torch,model,geom,state,[0,1],torch.tensor([1.,1.]),.002)
    assert not check['trusted'] and check['reason']=='SIGN_OR_SCALE_MISMATCH'


def test_adaptive_continuation_commits_and_restores_anchor(monkeypatch):
    model,geom=_toy(monkeypatch)
    trial=[]
    summary,state=_search(model,geom,trial_rows=trial)
    assert summary['accepted_iterations']>=2
    assert summary['joint_improved']
    assert summary['final_deficit']<summary['initial_deficit']
    assert summary['measurement_policy']=='V13_CENSUS_128_ALIGNED'
    assert all(r['retention_pass'] for r in trial if r.get('admitted'))
    assert all(r['gain_a']>0 and r['gain_b']>0 for r in trial if r.get('admitted'))
    assert torch.equal(model.weight.detach(),geom['anchor_weights']['weight'])
    assert state['weight'].min()>0


def test_hard_retention_rejects_gain_without_forgetting(monkeypatch):
    model,geom=_toy(monkeypatch,gate=lambda a,b:a<=0)
    rows=[]
    summary,state=_search(model,geom,trial_rows=rows,target_step=.001,
                          max_iterations=3,max_stalls=2)
    assert summary['accepted_iterations']==0
    assert any(r.get('protected_lost',0)>0 and r.get('merit_gain',0)>0 for r in rows)
    assert torch.equal(state['weight'],geom['anchor_weights']['weight'])
    assert torch.equal(model.weight.detach(),geom['anchor_weights']['weight'])


def test_stalled_iteration_retries_smaller_target_instead_of_stop(monkeypatch):
    model,geom=_toy(monkeypatch,gate=lambda a,b:a<=0)
    trial=[]
    summary,_=_search(model,geom,trial_rows=trial,max_iterations=5,
                      max_stalls=4,target_step=.002,min_target_step=1e-7,
                      min_joint_gain=1e-8,min_merit_gain=1e-8)
    assert summary['iterations'][0]['status']=='NO_ACCEPTABLE_STEP'
    assert len(summary['iterations'])>=2
    assert summary['iterations'][1]['target_step']<summary['iterations'][0]['target_step']
    assert any(r['iteration']>1 for r in trial)
    assert torch.equal(model.weight.detach(),geom['anchor_weights']['weight'])


def test_no_acceptance_when_merit_worsens(monkeypatch):
    model,geom=_toy(monkeypatch,objective=lambda a,b:.16+abs(a)+abs(b))
    summary,state=_search(model,geom,max_iterations=2)
    assert summary['accepted_iterations']==0
    assert summary['status']=='NO_JOINT_STEP_FOUND'
    assert torch.equal(state['weight'],geom['anchor_weights']['weight'])


def test_v18_evidence_requires_saved_verified_checkpoint(monkeypatch,tmp_path):
    model,geom=_toy(monkeypatch)
    r={'schema':v18.SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
       'checkpoint_saved':False,'promotion_authorized':False,'production_weights_modified':False,
       'general_acquisition_training':False,'frozen_v11_specialist':True,
       'TRAIN_only_gradients_and_candidate_admission':True,
       'synthetic_dev_used_for_training_or_selection':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'hard_train_retention':'UNCHANGED_V14_FULL_TRAIN_EXACT_INFERENCE',
       'v17_report_sha256':'s','v14_verified':{'v14_post_consolidation_sha256':'hash'},
       'training_capture_sha256':'cap','frozen_lineage':{'head':'a'},
       'v6_prior_sha256':'prior','focus_group':'group',
       'config':{'reserve':.08},'summary':{'joint_improved':True,'hard_full_train_retention':True}}
    root=tmp_path/'v18';root.mkdir()
    (root/'report.json').write_text(json.dumps(r))
    torch.save({'weights':v9._copy_state(model)},root/'joint_margin_disposable_candidate.pt')
    with pytest.raises(ValueError,match='V19_V18_PROVENANCE_MISMATCH'):
        v19._verify_v18(torch,root,'s',r['v14_verified'],'cap',
              {'probe_info':{'lineage':{'head':'a'}}},'prior','group',.08,
              model,geom,None,None,None,'cpu')


def test_e2e_refuses_unsaved_v18_anchor(monkeypatch,tmp_path):
    a=_run_v16(monkeypatch,tmp_path)
    a17=SimpleNamespace(**dict(vars(a),mode='audit',v16_run=tmp_path/'v16',
                 output=tmp_path/'v17',steps=[.0005,.002]))
    v17.run(a17)
    a18=SimpleNamespace(**dict(vars(a17),mode='solve',v17_run=tmp_path/'v17',
                 output=tmp_path/'v18',max_iterations=1,target_step=.0005,
                 trust_radius=.01,max_halvings=1,fd_epsilon=.002,min_joint_gain=1e-6,
                 path_step=.002,no_path=True))
    result=v18.run(a18)
    if result['checkpoint_saved']:
        # When the small synthetic fixture happens to improve both margins,
        # V19 must be able to inspect its checkpoint. Otherwise it must refuse
        # to invent that historical evidence.
        args=SimpleNamespace(**dict(vars(a18),mode='solve',v18_run=tmp_path/'v18',
            output=tmp_path/'v19',max_iterations=1,max_stalls=1,
            min_target_step=1e-6,min_joint_gain=2e-7,min_merit_gain=3e-7))
        out=v19.run(args)
        assert out['reference_v18']['v18_retention_verified'] is True
        assert out['source_anchor']=='VERIFIED_V14_POST_CONSOLIDATION_NOT_V18'
    else:
        args=SimpleNamespace(**dict(vars(a18),mode='solve',v18_run=tmp_path/'v18',
            output=tmp_path/'v19',max_iterations=1,max_stalls=1,
            min_target_step=1e-6,min_joint_gain=2e-7,min_merit_gain=3e-7))
        with pytest.raises(ValueError,match='V19_V18_EVIDENCE_MISSING'):
            v19.run(args)


def test_cumulative_acquired_answer_gate_is_stricter_than_original_anchor():
    assessment={'retention_pass':True,'protected_lost':0,'total_deficit':.12}
    pair=[.02,.03];prev=[.01,.02];required=[.08,.08]
    ok,_=v19._admissible(assessment,pair,prev,required,2e-7,3e-7,.15,0)
    assert ok
    refused,why=v19._admissible(assessment,pair,prev,required,
                                2e-7,3e-7,.15,1)
    assert not refused and why=='HARD_CUMULATIVE_TRAIN_RETENTION_LOSS'


def test_parser_has_v18_evidence_and_adaptive_continuation_options():
    parser=v19.parser()
    assert parser.get_default('max_iterations')==18
    assert parser.get_default('max_halvings')==10
    assert parser.get_default('min_joint_gain')<parser.get_default('min_target_step')
    names={a.dest for a in parser._actions}
    assert 'v18_run' in names and 'min_target_step' in names
    assert 'max_stalls' in names and 'min_merit_gain' in names

@pytest.mark.parametrize('lose_protected_answer', [False, True])
def test_v18_verified_checkpoint_uses_its_own_retention_mask(
        monkeypatch, tmp_path, lose_protected_answer):
    """Reproduce the CUDA preflight path that previously used an undefined mask.

    An invalid historical answer must still fail, even if the returned
    assessment mistakenly claims its general retention check passed.
    """
    model,geom=_toy(monkeypatch)
    verified14={'v14_post_consolidation_sha256':'post-hash'}
    data={'probe_info':{'lineage':{'head':'frozen'}}}
    report={'schema':v18.SCHEMA,'status':'EXPERIMENT_COMPLETE_NO_PROMOTION',
       'checkpoint_saved':True,'promotion_authorized':False,
       'production_weights_modified':False,'general_acquisition_training':False,
       'frozen_v11_specialist':True,
       'TRAIN_only_gradients_and_candidate_admission':True,
       'synthetic_dev_used_for_training_or_selection':False,
       'previously_seen_test_used_for_training_or_selection':False,
       'hard_train_retention':'UNCHANGED_V14_FULL_TRAIN_EXACT_INFERENCE',
       'v17_report_sha256':'historical-v17-sha','v14_verified':verified14,
       'training_capture_sha256':'capture-sha','frozen_lineage':data['probe_info']['lineage'],
       'v6_prior_sha256':'prior-sha','focus_group':'train.offset.0041',
       'config':{'reserve':.08},
       'summary':{'joint_improved':True,'hard_full_train_retention':True,
                  'best_deficit':.123,'final_safety':[.021,.023]}}
    root=tmp_path/'v18';root.mkdir()
    (root/'report.json').write_text(json.dumps(report))
    with torch.no_grad():
        model.weight.fill_(.1)
    saved=v9._copy_state(model)
    with torch.no_grad():
        model.weight.zero_()
    original=v9._copy_state(model)
    checkpoint={'schema':v18.SCHEMA,'status':'DISPOSABLE_NON_PRODUCTION',
        'promotion_authorized':False,'hard_full_train_retention':True,
        'training_capture_sha256':'capture-sha',
        'v14_post_sha256':'post-hash',
        'v17_report_sha256':'historical-v17-sha',
        'frozen_lineage':data['probe_info']['lineage'],
        'train_ledger_mask':torch.as_tensor(geom['mask']),
        'weights':saved}
    torch.save(checkpoint,root/'joint_margin_disposable_candidate.pt')

    def eval_checkpoint(torch_mod,net,geometry,spec,mu,sd,device,*,scope):
        assert scope=='v18_evidence'
        assert net.weight.detach().tolist()==pytest.approx([.1,.1])
        correct=np.array([True,not lose_protected_answer],dtype=bool)
        return ({'retention_pass':True,'protected_lost':0,
                 'total_deficit':.123},
                 np.array([.021,.023]),None,correct)
    monkeypatch.setattr(v19.v16,'_actual_evaluation',eval_checkpoint)
    args=(torch,root,'historical-v17-sha',verified14,'capture-sha',data,
          'prior-sha','train.offset.0041',.08,model,geom,None,None,None,'cpu')
    if lose_protected_answer:
        with pytest.raises(ValueError,match='V19_V18_HARD_RETENTION_MISMATCH'):
            v19._verify_v18(*args)
    else:
        evidence=v19._verify_v18(*args)
        assert evidence['v18_retention_verified'] is True
        assert evidence['v18_deficit']==pytest.approx(.123)
        assert evidence['v18_safety']==pytest.approx([.021,.023])
    assert all(torch.equal(model.state_dict()[k],v) for k,v in original.items())
