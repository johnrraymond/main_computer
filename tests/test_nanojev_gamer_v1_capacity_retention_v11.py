"""V11: separate mathematical capacity, exact retention, audited snapshots."""
from __future__ import annotations

import csv
import copy
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tests.test_nanojev_gamer_v1_conflict_projection_v10 import _setup
from tests.test_nanojev_gamer_v1_protected_math_repair_v7 import _checkpoint
from tools import nanojev_gamer_v1_capacity_retention_v11 as v11
from tools import nanojev_gamer_v1_conflict_projection_v10 as v10
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint


@pytest.fixture(autouse=True)
def _threads():
    old=torch.get_num_threads();torch.set_num_threads(2)
    yield
    torch.set_num_threads(old)


def test_restored_cpu_checkpoint_is_moved_to_requested_inference_device():
    # CUDA may be unavailable in CI; meta verifies an actual device transfer
    # rather than a no-op CPU-to-CPU conversion.
    model=v6._build_aligned_model(torch, protected=True)
    model.train()
    assert next(model.parameters()).device.type=='cpu'
    moved=v11._ready_for_inference(torch,model,'meta','V10_HISTORICAL')
    assert moved is model
    assert not moved.training
    assert all(p.device.type=='meta' for p in moved.parameters())
    assert v11._ready_for_inference(torch,v6._build_aligned_model(torch),
                                    'cpu','V6_REFERENCE').training is False


def test_expansion_exactly_starts_from_base_and_keeps_bypass():
    data,_,spec,mu,sd,base=_setup()
    state=v9._copy_state(base)
    model=v11._expanded_model(torch,width=32)
    model.base.load_state_dict(state,strict=True)
    x,old,_,rows,routes=spec['train_synthetic']
    encoded=torch.from_numpy(v6._encode(x,old,routes,mu,sd))
    with torch.no_grad():
        assert torch.equal(model(encoded),base(encoded))
        assert torch.all(torch.abs(model(encoded)[:,0]) <= v6.MAX_SIGNED_LOG_CORRECTION +1e-6)
        assert torch.all(torch.abs(model(encoded)[:,1:]) <= v6.MAX_CLASS_LOGIT_CORRECTION+1e-6)
        denied=encoded[:2].clone();denied[:,-2]=-1
        assert torch.equal(model(denied),torch.zeros(2,4))
        src_x,src_old,_,src_routes=spec['train_source']
        src_encoded=torch.from_numpy(v6._encode(src_x,src_old,src_routes,mu,sd))
        assert torch.equal(model(src_encoded),torch.zeros(len(src_x),4))
    assert model.protected
    assert all(not p.requires_grad for p in model.base.parameters())
    assert any(p.requires_grad for p in model.decoder.parameters())
    # A new mathematical-capacity weight change cannot rewrite frozen V6.
    before=v9._copy_state(model.base)
    with torch.no_grad(): model.decoder[-1].bias.add_(.15)
    assert all(torch.equal(v,before[k]) for k,v in model.base.state_dict().items())
    assert torch.equal(model(src_encoded),torch.zeros(len(src_x),4))
    with pytest.raises(ValueError,match='BAD_CAPACITY_WIDTH'):
        v11._expanded_model(torch,width=3)


def test_projection_gradient_supports_frozen_parameters():
    _,_,spec,mu,sd,base=_setup()
    model=v11._expanded_model(torch,16)
    model.base.load_state_dict(base.state_dict(),strict=True)
    x,old,y,_,routes=spec['train_synthetic']
    encoded=torch.from_numpy(v6._encode(x,old,routes,mu,sd))
    before=v9._copy_state(model)
    truth=np.sign(y).astype(int)
    grads=v10._margin_gradients(torch,model,before,encoded,
             torch.as_tensor(old,dtype=torch.float32),truth,[0,1],device='cpu')
    assert len(grads)==2
    assert all(g.shape==(sum(p.numel() for p in model.parameters()),) for g in grads)
    base_size=sum(p.numel() for p in model.base.parameters())
    assert torch.equal(grads[0][:base_size],torch.zeros(base_size))
    assert torch.isfinite(grads[0]).all()


def test_dev_transition_detects_lost_and_gained_without_relabeling():
    _,_,spec,mu,sd,base=_setup()
    rows=v11.v7._case_atlas(torch,base,spec,mu,sd,'cpu','dev')
    unchanged,groups=v11._dev_transitions(rows,rows,'test','selected')
    assert len(unchanged)==len(rows)
    assert all(r['transition'] in ('retained_correct','still_wrong') for r in unchanged)
    assert all(not g['lost_complete'] and not g['new_complete'] for g in groups)
    bad=copy.deepcopy(rows)
    bad[0]['prompt_sha256']='x'*64
    with pytest.raises(ValueError,match='UNMATCHED_CAPTURE'):
        v11._dev_transitions(rows,bad,'test','selected')


def test_checkpoint_ledger_refuses_mismatch_and_accepts_matched_snapshot():
    _,_,spec,mu,sd,base=_setup()
    groups,_=v9._groups_and_fingerprints(spec)
    mask=v9._correct_train(torch,base,spec,mu,sd,'cpu')
    acq=np.full(len(mask),-1,dtype=int);acq[mask]=0
    _, dev=v6._evaluate(torch,base,spec,mu,sd,'cpu')
    snap={'step':0,'weights':v9._copy_state(base),'ledger_mask':mask.copy(),
          'acquired_step':acq,'dev':dev}
    info=v11._evaluate_snapshot(torch,base,snap,spec,mu,sd,'cpu',groups)
    assert info['train']['correct_cases']==int(mask.sum())
    bad={**snap,'ledger_mask':~mask}
    with pytest.raises(ValueError,match='LEDGER_NOT_ACTUAL'):
        v11._evaluate_snapshot(torch,base,bad,spec,mu,sd,'cpu',groups)


def test_e2e_saves_distinct_terminal_and_selected_and_audits_v10(monkeypatch,tmp_path):
    data,prepared,spec,mu,sd,_=_setup()
    prior=tmp_path/'v6'/'disposable_candidate.pt';prior.parent.mkdir()
    _checkpoint(prior,data,mu,sd)
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,prepared))
    monkeypatch.setattr(joint,'_verify_after',lambda data:None)
    common=dict(device='cpu',run=tmp_path/'r',expanded_run=tmp_path/'e',
         input=tmp_path/'i',source_features=tmp_path/'s',prior=prior,
         steps=3,eval_every=1,lr=.002,seed=49,replay_weight=2.,
         max_halvings=2,projection_passes=2,max_constraints=6)
    v9_run=tmp_path/'v9';v10_run=tmp_path/'v10'
    v9_cfg=dict(common);v9_cfg.pop('projection_passes');v9_cfg.pop('max_constraints')
    v9.run(SimpleNamespace(**v9_cfg,output=v9_run))
    v10.run(SimpleNamespace(**common,v9_run=v9_run,output=v10_run))
    dest=tmp_path/'v11'
    config=SimpleNamespace(**common,v10_run=v10_run,output=dest,capacity_width=16)
    placement=[]
    original_place=v11._ready_for_inference
    def audit_place(torch_module, model, requested, label):
        placement.append((label,str(requested)))
        return original_place(torch_module,model,requested,label)
    monkeypatch.setattr(v11,'_ready_for_inference',audit_place)
    rep=v11.run(config)
    assert ('V6_REFERENCE','cpu') in placement
    assert ('V10_HISTORICAL','cpu') in placement
    assert rep['status']=='EXPERIMENT_COMPLETE_NO_PROMOTION'
    assert rep['terminal_states_saved_separately']
    assert rep['selected_states_saved_separately']
    assert not rep['promotion_authorized']
    assert rep['v10_verified_prior']['checkpoint_sha256']
    assert len(rep['comparisons'])==4
    assert len(rep['historical_conflict_groups'])<=3
    assert (dest/'critical_train_group_margins.csv').is_file()
    assert (dest/'dev_case_transitions.csv').is_file()
    assert (dest/'dev_group_transitions.csv').is_file()
    assert (dest/'historical_conflict_cases.csv').is_file()
    assert (dest/'disposable_candidate.pt').is_file()
    for arm in v11.ARMS:
        for state in ('selected','terminal'):
            path=dest/f'{arm}_{state}_candidate.pt'
            cp=torch.load(path,weights_only=True)
            assert cp['arm']==arm and cp['state']==state
            assert cp['promotion_authorized'] is False
            assert cp['mathematical_train_retention_ledger']['source_split']=='SYNTHETIC_TRAIN_ONLY'
            assert len(cp['mathematical_train_retention_ledger']['rows'])==24
            assert (dest/f'{arm}_{state}_train_ledger.csv').is_file()
        t=torch.load(dest/f'{arm}_terminal_candidate.pt',weights_only=True)
        s=torch.load(dest/f'{arm}_selected_candidate.pt',weights_only=True)
        assert t['step']==3
        assert s['step']<=3
        assert t['mathematical_train_retention_ledger']['correct_cases'] >= \
               s['mathematical_train_retention_ledger']['correct_cases']
        assert (dest/f'{arm}_attempts.csv').is_file()
    with pytest.raises(FileExistsError):
        v11.run(config)
    # Historical V10 evidence cannot be transplanted into a different capture.
    with pytest.raises(ValueError,match='V10_PROVENANCE_MISMATCH'):
        v11._verify_v10(torch,v10_run,data,'0'*64,rep['train_capture_sha256'],spec)


@pytest.mark.parametrize('changes',[{'capacity_width':3},{'steps':0},
    {'lr':float('nan')},{'max_constraints':0},{'projection_passes':0}])
def test_invalid_config_blocks_capture_access(monkeypatch,tmp_path,changes):
    monkeypatch.setattr(joint,'_prepare',lambda args:pytest.fail('capture accessed'))
    kwargs=dict(run=tmp_path/'a',expanded_run=tmp_path/'b',input=tmp_path/'c',
        source_features=tmp_path/'d',prior=tmp_path/'e',v10_run=tmp_path/'f',
        output=tmp_path/'g',device='cpu',steps=2,eval_every=1,lr=.001,seed=49,
        replay_weight=2.,max_halvings=2,projection_passes=2,max_constraints=4,
        capacity_width=16)
    kwargs.update(changes)
    with pytest.raises(ValueError,match='BAD_CONFIGURATION'):
        v11.run(SimpleNamespace(**kwargs))


def test_critical_group_census_uses_train_only_and_reports_margins():
    _,_,spec,mu,sd,base=_setup()
    rows=spec['train_synthetic'][3]
    first=rows[0]
    another=rows[6]
    conflicts=[
        {'stage':'raw_smallest','case_id':first['id'],
         'group':first['group'],'surface':first['surface'],
         'prompt_sha256':first['prompt_sha256'],
         'trial_true_class_margin':'-0.07'},
        {'stage':'raw_smallest','case_id':first['id'],
         'group':first['group'],'surface':first['surface'],
         'prompt_sha256':first['prompt_sha256'],
         'trial_true_class_margin':'0.02'},
        {'stage':'raw_smallest','case_id':another['id'],
         'group':another['group'],'surface':another['surface'],
         'prompt_sha256':another['prompt_sha256'],
         'trial_true_class_margin':'0.01'}]
    names,margins,counts=v11._critical_groups(torch,base,base,
             [('terminal',base,v9._copy_state(base))],spec,mu,sd,'cpu',conflicts,top=2)
    assert names==[first['group'],another['group']]
    assert counts[first['group']]==2
    assert len(margins)==6*2*3
    observed=[r for r in margins if r['case_id']==first['id']]
    assert all(r['v10_smallest_rejection_events']==2 for r in observed)
    assert all(r['v10_smallest_trial_margin_min']==pytest.approx(-.07)
               for r in observed)
    assert all(np.isfinite(r['true_class_margin']) for r in margins)
    assert all(r['model_state'] in ('v6_reference','v10_projected_selected','terminal')
               for r in margins)
