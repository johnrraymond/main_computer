"""V9 mathematical competence acquisition / strict retention replay tests."""
from __future__ import annotations

import csv
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from tests.test_nanojev_gamer_v1_protected_alignment_lab import _verified_small
from tests.test_nanojev_gamer_v1_protected_math_repair_v7 import _checkpoint
from tools import nanojev_gamer_v1_math_retention_v9 as v9
from tools import nanojev_gamer_v1_protected_alignment_lab as v6
from tools import nanojev_gamer_v1_joint_head_repair as joint


@pytest.fixture(autouse=True)
def _thread_limit():
    before = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(before)


def _setup():
    data, prepared = _verified_small()
    spec, _ = v6._routed_profiles(data, prepared)
    sx, _, _, _ = spec['train_source']
    tx = spec['train_synthetic'][0]
    mu, sd = joint._normalization(sx, tx)
    model = v6._build_aligned_model(torch, protected=True)
    return data, prepared, spec, model, mu, sd


def test_train_group_contract_and_exact_provenance_refusal():
    _, _, spec, _, _, _ = _setup()
    groups, fingerprint = v9._groups_and_fingerprints(spec)
    assert len(groups) == 4 and len(fingerprint) == 64
    x, old, target, rows, routes = spec['train_synthetic']
    broken = rows.copy()
    broken[0] = {**broken[0], 'prompt_sha256': broken[1]['prompt_sha256']}
    with pytest.raises(ValueError, match='DUPLICATE_OR_UNVERIFIED'):
        v9._groups_and_fingerprints({**spec,'train_synthetic':(x,old,target,broken,routes)})
    broken = rows.copy()
    broken[0] = {**broken[0], 'target': '1'}
    with pytest.raises(ValueError, match='SIGN_ORDER|TRAIN_GROUP_TARGET_MISMATCH'):
        v9._groups_and_fingerprints({**spec,'train_synthetic':(x,old,target,broken,routes)})
    invalid_routes = routes.copy()
    invalid_routes[0] = -1
    with pytest.raises(ValueError, match='ROUTING_CHANGED'):
        v9._groups_and_fingerprints({**spec,'train_synthetic':(x,old,target,rows,invalid_routes)})


def test_acquisition_is_monotonic_and_keeps_each_acquired_group():
    _, _, spec, _, _, _ = _setup()
    groups, _ = v9._groups_and_fingerprints(spec)
    rows = spec['train_synthetic'][3]
    init = np.zeros(len(rows), dtype=bool)
    init[[0,1,2,3,4,5,7]] = True
    ledger = v9.MathematicalLedger(init, rows, groups)
    assert ledger.initial['complete_six_case_groups'] == 1
    assert ledger.initial['complete_triples'] == 2
    assert not ledger.accepts(np.zeros(len(rows), dtype=bool))
    candidate = init.copy()
    candidate[6] = True
    result = ledger.acquire(candidate, 1)
    assert result['new_correct'] == 1
    assert result['complete_six_case_groups'] == 1
    assert all(ledger.mask[init])
    with pytest.raises(ValueError, match='HARD_MATHEMATICAL_RETENTION'):
        ledger.acquire(np.zeros(len(rows), dtype=bool), 2)
    with pytest.raises(ValueError, match='NONMONOTONIC_STEP'):
        ledger.acquire(candidate, 1)
    rng = np.random.default_rng(49)
    picks = ledger.sample_replay_groups(rng, 200)
    assert len(picks) == 200
    assert np.count_nonzero(picks == 0) > np.count_nonzero(picks == 1)
    assert ledger.as_csv()[6]['first_correct_step'] == 1
    assert ledger.as_csv()[7]['first_correct_step'] == 0


def test_case_transition_rejects_different_oracle_even_with_same_ids():
    base = [{'id':'a','prompt_sha256':'x','target_fraction':'0','group':'g',
        'surface':'source_native','correction_admitted':1,'correct':1}]
    newer = [dict(base[0],correct=0)]
    assert v9._case_transition(base,newer)['lost_correct'] == 1
    newer[0]['target_fraction'] = '1'
    with pytest.raises(ValueError, match='CHANGED_CASE_PROVENANCE'):
        v9._case_transition(base,newer)


def test_replay_full_training_preserves_all_acquired_answers_and_original_baseline():
    _, _, spec, model, mu, sd = _setup()
    initial = v9._copy_state(model)
    net, best, history, ledger, meta = v9._fit_replay(
        torch,spec,initial,mu,sd,device='cpu',steps=4,eval_every=2,
        seed=49,lr=.002,replay_weight=2.,max_halvings=3)
    assert len(history) == 3
    assert best['step'] in (0,2,4)
    assert meta['train_only_memory']
    assert meta['all_acquired_correct_retained_at_selected']
    assert meta['per_update_full_train_hard_gate']
    assert (meta['updates_accepted'] + meta['updates_rejected']) == 4
    now = v9._correct_train(torch,net,spec,mu,sd,'cpu')
    assert np.all(now[ledger.mask])
    assert np.all(ledger.acquired_step[ledger.mask] <= best['step'])
    orig, old, _, route = spec['train_source']
    _, values = v6._predict(torch,net,orig,old,route,mu,sd,'cpu')
    assert np.array_equal(values,old)


def test_unacceptable_proposal_rolls_back_both_optimizer_and_weights(monkeypatch):
    _, _, spec, model, mu, sd = _setup()
    groups, _ = v9._groups_and_fingerprints(spec)
    rows = spec['train_synthetic'][3]
    initial = np.ones(len(rows),dtype=bool)
    ledger = v9.MathematicalLedger(initial,rows,groups)
    encoded = v6._encode(*[
        spec['train_synthetic'][0],spec['train_synthetic'][1],
        spec['train_synthetic'][-1],mu,sd])
    x = torch.from_numpy(encoded)
    old = torch.as_tensor(spec['train_synthetic'][1],dtype=torch.float32)
    target = torch.as_tensor(spec['train_synthetic'][2],dtype=torch.float32)
    before = v9._copy_state(model)
    optim = torch.optim.AdamW(model.parameters(),lr=.003)
    opt_before = optim.state_dict()
    monkeypatch.setattr(v9,'_correct_train',lambda *a:np.zeros(len(rows),dtype=bool))
    proposal = v9._strict_update(torch,model,optim,x,old,target,
        torch.ones(len(rows),dtype=torch.bool),groups_selected=4,
        net_spec=spec,mu=mu,sigma=sd,device='cpu',ledger=ledger,
        max_halvings=2,replay_weight=1.)
    assert proposal['accepted'] is False
    assert all(torch.equal(v,before[k]) for k,v in model.state_dict().items())
    assert optim.state_dict()['state'] == opt_before['state'] == {}


@pytest.mark.parametrize('bad',[{'steps':0},{'lr':0.},
    {'replay_weight':-1.},{'max_halvings':100},{'lr':float('nan')}])
def test_bad_config_refused_before_data_read(monkeypatch,tmp_path,bad):
    monkeypatch.setattr(joint,'_prepare',lambda args:pytest.fail('source read'))
    cfg=dict(device='cpu',run=tmp_path/'r',expanded_run=tmp_path/'e',
        input=tmp_path/'i',source_features=tmp_path/'s',prior=tmp_path/'p',
        output=tmp_path/'o',steps=4,eval_every=2,lr=.002,seed=49,
        replay_weight=2.,max_halvings=3)
    cfg.update(bad)
    with pytest.raises(ValueError,match='INVALID_CONFIGURATION'):
        v9.run(SimpleNamespace(**cfg))


def test_end_to_end_v9_creates_ledgers_compares_and_never_promotes(monkeypatch,tmp_path):
    data, prepared, spec, _, mu, sd = _setup()
    cp = tmp_path/'prior.pt'
    _checkpoint(cp,data,mu,sd)
    monkeypatch.setattr(joint,'_prepare',lambda args:(data,prepared))
    monkeypatch.setattr(joint,'_verify_after',lambda data:None)
    out = tmp_path/'v9-result'
    cfg=dict(device='cpu',run=tmp_path/'r',expanded_run=tmp_path/'e',
        input=tmp_path/'i',source_features=tmp_path/'s',prior=cp,
        output=out,steps=4,eval_every=2,lr=.002,seed=49,
        replay_weight=2.,max_halvings=3)
    report=v9.run(SimpleNamespace(**cfg))
    assert report['status']=='EXPERIMENT_COMPLETE_NO_PROMOTION'
    assert report['selected_candidate'] in ('v6_reference','monotonic_retention_replay')
    assert len(report['candidates'])==3
    assert report['uniform_control_exact_v7_training_function']
    assert report['mathematical_retention']['all_acquired_correct_retained_at_selected']
    assert report['mathematical_retention']['train_only_memory']
    assert not report['promotion_authorized']
    assert not report['production_weights_modified']
    assert not report['synthetic_test_used_for_fitting_or_selection']
    assert 'monotonic_retention_replay' in report['transitions']['previously_seen_test_diagnostic_only']
    assert (out/'mathematical_train_retention_ledger.csv').is_file()
    assert (out/'mathematical_acquisition_events.csv').is_file()
    assert (out/'uniform_continuation_cases_previously_seen_test.csv').is_file()
    assert (out/'monotonic_retention_replay_groups_dev.csv').is_file()
    with (out/'candidate_comparison.csv').open() as f:
        assert len(list(csv.DictReader(f)))==3
    saved=torch.load(out/'disposable_candidate.pt',weights_only=True)
    assert saved['status']=='DISPOSABLE_NON_PRODUCTION'
    assert saved['protected_original_passthrough']
    assert saved['promotion_authorized'] is False
    replay = torch.load(out/'monotonic_retention_replay_candidate.pt',weights_only=True)
    assert replay['mathematical_train_retention_ledger']['source_split']=='SYNTHETIC_TRAIN_ONLY'
    assert len(replay['mathematical_train_retention_ledger']['rows'])==24
    assert all(x['protected'] in (0,1) for x in replay['mathematical_train_retention_ledger']['rows'])
    with pytest.raises(FileExistsError):
        v9.run(SimpleNamespace(**cfg))
