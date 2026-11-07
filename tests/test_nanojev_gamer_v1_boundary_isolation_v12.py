"""V12 focuses on a certified TRAIN conflict; DEV cannot fit calibration."""
from __future__ import annotations
import copy
import csv
import json
from types import SimpleNamespace
import numpy as np
import pytest
import torch

from tests.test_nanojev_gamer_v1_conflict_projection_v10 import _setup
from tests.test_nanojev_gamer_v1_protected_math_repair_v7 import _checkpoint
from tools import nanojev_gamer_v1_boundary_isolation_v12 as v12
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
from tools import nanojev_gamer_v1_joint_head_repair as joint
from tools import nanojev_gamer_v1_protected_alignment_lab as v6

@pytest.fixture(autouse=True)
def _threads():
    previous=torch.get_num_threads();torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


def test_calibration_zero_start_exact_bypass_and_frozen_base():
    _,_,spec,mu,sd,base=_setup()
    expanded=v11._expanded_model(torch,16)
    expanded.base.load_state_dict(base.state_dict(),strict=True)
    model=v12._calibrator(torch,16)
    model.base.load_state_dict(v9._copy_state(expanded),strict=True)
    x,old,_,rows,routes=spec['train_synthetic']
    t=torch.from_numpy(v6._encode(x,old,routes,mu,sd))
    assert torch.equal(model(t),expanded(t))
    assert model.protected
    assert len([p for p in model.parameters() if p.requires_grad])==1
    assert model.route_bias.numel()==6
    with torch.no_grad():model.route_bias[1,0]=.2
    assert not torch.equal(model(t)[3],expanded(t)[3])
    denied=t[:1].clone();denied[:,-2]=-1
    assert torch.equal(model(denied),torch.zeros(1,4))
    sx,so,_,sr=spec['train_source']
    ss=torch.from_numpy(v6._encode(sx,so,sr,mu,sd))
    assert torch.equal(model(ss),torch.zeros(len(ss),4))
    assert all(not p.requires_grad for p in model.base.parameters())


def test_frozen_feature_focus_atlas_records_full_group_and_cosine(tmp_path):
    _,_,spec,mu,sd,base=_setup()
    model=v11._expanded_model(torch,16)
    model.base.load_state_dict(base.state_dict(),strict=True)
    rows=spec['train_synthetic'][3]
    focus=rows[0]['group']
    prompts={r['id']:r['id'] for r in rows}
    conf=tmp_path/'conflicts.csv'
    with conf.open('w',newline='') as f:
        w=csv.DictWriter(f,['stage','case_id','group'])
        w.writeheader();w.writerow({'stage':'raw_smallest','case_id':rows[0]['id'],'group':focus})
    result,counts=v12._focus_atlas(torch,{'selected':model,'terminal':model},
       spec,mu,sd,'cpu',focus,prompts,conf)
    assert len(result)==12
    assert counts[rows[0]['id']]==1
    assert {r['surface'] for r in result}=={'source_native','spine_difference'}
    assert all(np.isfinite(r['paired_feature_cosine']) for r in result)
    assert all(r['target_sign']==r['predicted_sign'] for r in result if r['correct'])
    with pytest.raises(ValueError,match='FOCUS_GROUP_NOT_COMPLETE'):
        v12._focus_atlas(torch,{'selected':model},spec,mu,sd,'cpu','missing',prompts,conf)


def test_repair_preserves_ledger_and_stores_real_terminal():
    _,_,spec,mu,sd,base=_setup()
    expanded=v11._expanded_model(torch,16)
    expanded.base.load_state_dict(base.state_dict(),strict=True)
    focus=spec['train_synthetic'][3][0]['group']
    model,best,terminal,events,victims,history=v12._repair(torch,expanded,
        spec,mu,sd,'cpu',focus,steps=3,eval_every=1,lr=.001,seed=49,
        replay_weight=2.,max_halvings=2,projection_passes=2,max_constraints=6)
    for snap in (best,terminal):
        model.load_state_dict(snap['weights'],strict=True)
        assert np.array_equal(v9._correct_train(torch,model,spec,mu,sd,'cpu'),snap['ledger_mask'])
    assert terminal['step']==3
    assert best['step']<=3
    assert len(events)==3
    assert len(history)==3
    assert all(r['accepted'] in (0,1) for r in events)


def test_verification_and_train_only_end_to_end(monkeypatch,tmp_path):
    data,prepared,spec,mu,sd,_=_setup()
    prior=tmp_path/'v6'/'disposable_candidate.pt';prior.parent.mkdir()
    _checkpoint(prior,data,mu,sd)
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,prepared))
    monkeypatch.setattr(joint,'_verify_after',lambda data:None)
    common=dict(device='cpu',run=tmp_path/'r',expanded_run=tmp_path/'e',
         input=tmp_path/'i',source_features=tmp_path/'s',prior=prior,
         steps=2,eval_every=1,lr=.002,seed=49,replay_weight=2.,
         max_halvings=1,projection_passes=1,max_constraints=4)
    v9_cfg=dict(common);v9_cfg.pop('projection_passes');v9_cfg.pop('max_constraints')
    v9.run(SimpleNamespace(**v9_cfg,output=tmp_path/'v9'))
    v10.run(SimpleNamespace(**common,v9_run=tmp_path/'v9',output=tmp_path/'v10'))
    v11_report=v11.run(SimpleNamespace(**common,v10_run=tmp_path/'v10',
                         output=tmp_path/'v11',capacity_width=16))
    focus=spec['train_synthetic'][3][0]['group']
    path=tmp_path/'v11'/'report.json'
    doc=json.loads(path.read_text())
    doc['historical_top_conflicts']=[[focus,1]]
    path.write_text(json.dumps(doc))
    # The V11 control conflict CSV can be absent in a two-step smoke; empty
    # capture is still well-defined but never represented as evidence of a
    # real conflict in the final report.
    conflict=tmp_path/'v11'/'projected_control_conflict_cases.csv'
    if not conflict.exists():
        with conflict.open('w',newline='') as f:
            w=csv.DictWriter(f,['stage','case_id','group'])
            w.writeheader()
    result=v12.run(SimpleNamespace(**{k:v for k,v in common.items() if k not in ('steps','eval_every','lr')},
         v11_run=tmp_path/'v11',output=tmp_path/'v12',focus_group=focus,
         mode='repair',steps=2,eval_every=1,lr=.001))
    assert result['schema']==v12.SCHEMA
    assert result['promotion_authorized'] is False
    assert result['source_dev_used_for_fitting'] is False
    assert result['previously_seen_test_used_for_fitting_selection_or_metric'] is False
    assert result['calibration_parameter_count']==6
    assert result['comparison'][0]['state']=='v11_selected'
    assert (tmp_path/'v12'/'focus_group_cases.csv').exists()
    assert (tmp_path/'v12'/'dev_case_transitions.csv').exists()
    assert (tmp_path/'v12'/'calibration_terminal_candidate.pt').exists()
    original=torch.load(tmp_path/'v12'/'calibration_terminal_candidate.pt',weights_only=True)
    assert original['promotion_authorized'] is False
    with pytest.raises(FileExistsError):
        v12.run(SimpleNamespace(**{k:v for k,v in common.items() if k not in ('steps','eval_every','lr')},
             v11_run=tmp_path/'v11',output=tmp_path/'v12',focus_group=focus,
             mode='diagnose',steps=2,eval_every=1,lr=.001))
    saved=torch.load(tmp_path/'v11'/'expanded_capacity_projected_selected_candidate.pt',weights_only=True)
    saved['mathematical_train_retention_ledger']['rows'][0]['prompt_sha256']='0'*64
    torch.save(saved,tmp_path/'v11'/'expanded_capacity_projected_selected_candidate.pt')
    with pytest.raises(ValueError,match='LEDGER_CAPTURE_CHANGED'):
        v12.run(SimpleNamespace(**{k:v for k,v in common.items() if k not in ('steps','eval_every','lr')},
             v11_run=tmp_path/'v11',output=tmp_path/'other',focus_group=focus,
             mode='diagnose',steps=2,eval_every=1,lr=.001))
