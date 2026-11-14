"""Hard EMP joining preserves the four-head ladder and uses only qualified suppliers."""
from argparse import Namespace
import json
import copy

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_emp_hard_join as hard
from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_dual_bank_ladder as ladder


def small_model():
    return AdditionNet('digit_tokens', width=16, heads=4, layers=1, rounds=1)


def test_strict_competence_gate_preserves_head_provenance():
    cards = [(200, -199, 1, 0, 200)] * 10
    predictions = {
        'left.left': [1] * 7 + [0] * 3,
        'left.right': [1] * 6 + [0] * 4,
        'right.left': [0] * 10,
        'right.right': [1] * 9 + [0],
    }
    accuracies, qualified = hard.qualifying_suppliers(predictions, cards)
    assert qualified == ('left.left', 'right.right')
    assert accuracies['left.right'] == 0.6
    assert hard.pick_sources(qualified, accuracies) == ('right.right',)
    assert hard.pick_sources(qualified, accuracies, standout_margin=.25) == ('right.right', 'left.left')
    assert hard.pick_sources(qualified, {**accuracies, 'right.right': 0.99}) == ('right.right',)
    assert hard.is_correct(0, 0) and not hard.is_correct(0, 1)
    assert hard.is_correct(100, 105) and not hard.is_correct(-10, -9)


def test_captured_latent_is_identical_to_head_input():
    model = small_model()
    x = torch.tensor([[10, -9], [111, 111], [-500, 500]])
    expected = []
    handle = model.sign_head.register_forward_pre_hook(lambda m, inputs: expected.append(inputs[0].detach()))
    try:
        predictions, state = hard.capture_bank(model, x)
    finally:
        handle.remove()
    assert len(predictions) == 3
    assert torch.equal(state, expected[-1])
    assert state.shape == (3, 16)
    assert not state.requires_grad


def test_pairing_only_qualified_and_matched_supplier():
    cards = []
    for m in range(1000, 1100):
        cards.append((m, -m+1, 1, 0, m))
    preds = {name: [1]*len(cards) for name in dual.BANKS}
    preds['left.left'] = [0 if i % 2 else 1 for i in range(len(cards))]
    # All other heads are excluded even when they fail the whole set.
    preds['right.right'] = [0]*len(cards)
    latents = {name: torch.randn(len(cards), 16) for name in dual.BANKS}
    paired = hard.paired_cases(cards, preds, latents, ('left.left',), 1, 16384, 250)
    assert len(paired['training']) + len(paired['validation']) > 0
    for fold, pairs in paired.items():
        for p in pairs:
            assert p['hard']['supplier'] == p['easy']['supplier'] == 'left.left'
            assert p['hard']['source_prediction'] == 0
            assert p['easy']['source_prediction'] == 1
            assert hard.assign_anchor(p['hard']['card'][4],1) == fold
            assert hard.assign_anchor(p['easy']['card'][4],1) == fold
            assert p['hard']['step'] == 250


def test_detector_and_clone_training_changes_parameters_and_preserves_sources():
    a = small_model()
    b = copy.deepcopy(a)
    banks = {key: copy.deepcopy(a) for key in dual.BANKS}
    baseline = copy.deepcopy(banks['left.left'].state_dict())
    units, detector, opts = hard.make_specialist(banks, ('left.left','right.right'), 'cpu', .003)
    items=[]
    for i in range(16):
        cards = [(100+i,-99-i,1,0,100+i), (100+i,-100-i,0,0,100+i)]
        items.append({'hard':{'card':cards[0], 'latent':torch.randn(16)},
                      'easy':{'card':cards[1], 'latent':torch.randn(16)}})
    before={key: value.detach().clone() for key,value in detector.state_dict().items()}
    result=hard.train_on_previous({name:units[name.replace('.','__')] for name in hard.SLOTS},
                                  detector,opts,items,'cpu',4,2,10)
    assert result['updates']==2
    assert any(not torch.equal(v,detector.state_dict()[k]) for k,v in before.items())
    assert all(torch.equal(v,banks['left.left'].state_dict()[key]) for key,v in baseline.items())
    metrics=hard.evaluate_specialist(detector,{name:units[name.replace('.','__')] for name in hard.SLOTS},items[:2],'cpu')
    assert metrics['hard'] == metrics['easy'] == 2
    assert 0 <= metrics['joint_hard_recovery'] <= 1
    assert 0 <= metrics['combined_accuracy'] <= 1
    assert metrics['either_hard_accuracy_upper_bound'] >= metrics['hard_accuracy']


def test_probe_no_qualified_then_qualified_and_resume(tmp_path, monkeypatch):
    root=tmp_path/'campaign'
    model=small_model()
    banks={name:copy.deepcopy(model) for name in dual.BANKS}
    # Force exact predictable scoring without expensive model dependent outcome.
    calls={'n':0}
    def fake_collect(banks,rows,device,batch):
        calls['n']+=1
        n=len(rows)
        feats={k:torch.randn(n,16) for k in dual.BANKS}
        correct=[r[2] for r in rows]
        if calls['n']<=2:
            return {k:[int(v+100000) for v in correct] for k in dual.BANKS},feats
        return {name:[(correct[i] if i%5 else int(correct[i]+100000)) if name=='left.left' else
                      (correct[i] if i%4 else int(correct[i]+100000)) if name=='right.right' else
                      int(correct[i]+100000) for i in range(n)] for name in dual.BANKS},feats
    monkeypatch.setattr(hard,'collect_accuracy',fake_collect)
    monkeypatch.setattr(hard,'accuracy_cards',lambda *x:[(100+i,-99-i,1,0,100+i) for i in range(80)])
    args=Namespace(seed=123,hard_lr=.001,hard_cards=80,hard_batch=4,hard_updates=1,
                   hard_max_queue=128,eval_batch_size=16,device='cpu')
    first=hard.run_probe(root,banks,args,512,0)
    assert first['status']=='no_qualified_heads'
    assert first['qualified']==[]
    second_ineligible=hard.run_probe(root,banks,args,512,125)
    assert second_ineligible['status']=='no_qualified_heads'
    assert second_ineligible['qualified']==[]
    second=hard.run_probe(root,banks,args,512,250)
    assert second['status']=='evaluated'
    assert set(second['qualified'])=={'left.left','right.right'}
    assert second['train_on_previous']['updates']==0
    assert second['source_clones']==['left.left','right.right']
    assert second['training_pairs']>0
    third=hard.run_probe(root,banks,args,512,500)
    assert third['train_on_previous']['updates']==1
    assert third['training_pairs']>0
    assert hard.run_probe(root,banks,args,512,500)==third
    assert calls['n']==4  # Replay is idempotent; no rescoring or retraining.
    audit=torch.load(root/'hard_emp'/'evidence'/'512-250.pt',weights_only=True)
    assert len(audit['cards'])==80
    assert set(audit['predictions'])==set(dual.BANKS)
    assert len(audit['training_pairs'])>0
    cp=torch.load(root/'hard_emp'/'latest.pt',weights_only=True)
    assert len(cp['processed'])==4
    assert cp['selected']==['left.left','right.right']


def test_join_attaches_only_after_original_save_and_restores_hook(tmp_path,monkeypatch):
    calls=[]
    def save(path,*args):
        calls.append('commit')
    def specialist(*args):
        calls.append('specialist')
    def parent_run(args):
        ladder.save_stage(tmp_path,{}, {}, {}, [], None, 250, 0, [], 0, 512)
        return {'ok':True}
    monkeypatch.setattr(ladder,'save_stage',save)
    monkeypatch.setattr(ladder,'run',parent_run)
    monkeypatch.setattr(hard,'run_probe',specialist)
    args=Namespace(output=str(tmp_path),hard_cards=64,hard_batch=4,hard_updates=2,hard_max_queue=64,hard_lr=.0001)
    assert hard.run(args)=={'ok':True}
    assert calls==['commit','specialist']
    assert ladder.save_stage is save


def test_real_ladder_join_has_separate_sidecar_no_parent_schema_mutation(tmp_path, monkeypatch):
    from test_nanojev_emp_dual_bank_ladder import make_run
    args,_=make_run(tmp_path,monkeypatch,stage_steps=2,max_stages=1)
    args.hard_cards=64
    args.hard_batch=4
    args.hard_updates=1
    args.hard_max_queue=64
    args.hard_lr=.0001
    # The parent fixture intentionally mocks training to four repeating cards.
    # The specialist accuracy probe still needs a distinct independent deck.
    monkeypatch.setattr(hard, 'accuracy_cards', lambda radius, seed, serial, n:
                        [(1000+i, -999-i, 1, 0, 1000+i) for i in range(n)])
    result=hard.run(args)
    assert result['status']=='stage_limit_reached'
    out=tmp_path/'out'
    main = torch.load(out/'stages'/'outward_512'/'accepted.pt',weights_only=True)
    sidecar = torch.load(out/'hard_emp'/'latest.pt',weights_only=True)
    assert main['schema'] == ladder.SCHEMA + '.checkpoint'
    assert len(main['optimizers'])==4
    assert sidecar['schema']==hard.SCHEMA+'.checkpoint'
    assert {'512-1','512-2'} <= set(sidecar['processed'])
    assert (out/'hard_emp'/'evidence'/'512-1.pt').is_file()
    assert (out/'hard_emp'/'evidence'/'512-2.pt').is_file()
    assert (out/'stages'/'outward_512'/'acceptance.json').is_file()


def test_normal_training_rng_preserved_by_join(tmp_path,monkeypatch):
    calls=[]
    def fake_save(*a):
        calls.append('checkpoint')
    def fake_specialist(*a):
        torch.rand(40)
        calls.append('hard')
    def fake_run(args):
        original = torch.get_rng_state().clone()
        ladder.save_stage(tmp_path,{}, {}, {}, [], None, 1, 0, [], 0, 512)
        assert torch.equal(original,torch.get_rng_state())
        return 1
    monkeypatch.setattr(ladder,'save_stage',fake_save)
    monkeypatch.setattr(ladder,'run',fake_run)
    monkeypatch.setattr(hard,'run_probe',fake_specialist)
    args=Namespace(output=str(tmp_path),hard_cards=64,hard_batch=4,hard_updates=1,
                   hard_max_queue=64,hard_lr=.0001,resume=False)
    assert hard.run(args)==1
    assert calls==['checkpoint','hard']


def test_attachment_recovers_last_probe_of_already_accepted_stage(tmp_path, monkeypatch):
    from test_nanojev_emp_dual_bank_ladder import make_run
    args, _ = make_run(tmp_path, monkeypatch, stage_steps=2, max_stages=1)
    assert ladder.run(args)['accepted_radii'] == [512]
    args.resume = True
    args.hard_cards = 64
    args.hard_batch = 4
    args.hard_updates = 1
    args.hard_max_queue = 64
    args.hard_lr = .0001
    monkeypatch.setattr(hard, 'accuracy_cards', lambda radius, seed, serial, n:
                        [(1000+i, -999-i, 1, 0, 1000+i) for i in range(n)])
    hard.recover_pending(args)
    sidecar = tmp_path/'out'/'hard_emp'/'latest.pt'
    cp = torch.load(sidecar, weights_only=True)
    assert '512-2' in cp['processed']
    before = sidecar.read_bytes()
    hard.recover_pending(args)
    assert sidecar.read_bytes() == before


def test_frontier_recovery_does_not_replay_previous_radii(tmp_path, monkeypatch):
    root = tmp_path / 'out'
    for radius, step in ((512, 500), (1024, 96000), (2048, 42500),
                         (4096, 11250), (8192, 311000), (16384, 9500)):
        stage = root / 'stages' / f'outward_{radius}'
        stage.mkdir(parents=True)
        torch.save({'radius': radius, 'step': step, 'banks': {}}, stage / 'latest.pt')
    calls = []
    monkeypatch.setattr(ladder, 'source_config', lambda _: (None, None, None, 'left', 'right'))
    monkeypatch.setattr(ladder, 'load_certified_model', lambda *a: (object(), None))
    monkeypatch.setattr(ladder, 'from_source', lambda *a: 'banks')
    monkeypatch.setattr(hard, 'run_probe', lambda root, banks, args, radius, step:
                        calls.append((radius, step, banks)))
    args = Namespace(output=str(root), ladder_output=str(tmp_path / 'ladder'),
                     device='cpu', hard_state_dir='hard_emp_fresh')
    hard.recover_pending(args)
    assert calls == [(16384, 9500, 'banks')]


def test_fresh_named_state_preserves_prior_hard_emp(tmp_path, monkeypatch):
    root = tmp_path / 'out'
    sentinel = root / 'hard_emp' / 'latest.pt'
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b'old sidecar retained')
    banks = {name: small_model() for name in dual.BANKS}
    monkeypatch.setattr(hard, 'accuracy_cards', lambda radius, seed, serial, n:
                        [(1000+i, -999-i, 1, 0, 1000+i) for i in range(n)])
    def collected(banks, rows, device, batch):
        predictions = {name: [r[2] if i % 5 else r[2] + 100000
                              for i, r in enumerate(rows)] for name in dual.BANKS}
        latents = {name: torch.zeros(len(rows), 16) for name in dual.BANKS}
        return predictions, latents
    monkeypatch.setattr(hard, 'collect_accuracy', collected)
    args=Namespace(seed=1,hard_lr=.001,hard_cards=80,hard_batch=4,hard_updates=1,
                   hard_max_queue=128,eval_batch_size=16,device='cpu',hard_state_dir='hard_emp_fresh')
    report=hard.run_probe(root, banks, args, 16384, 9500)
    assert report['status'] == 'evaluated'
    assert sentinel.read_bytes() == b'old sidecar retained'
    assert (root/'hard_emp_fresh'/'latest.pt').is_file()
    assert (root/'hard_emp_fresh'/'evidence'/'16384-9500.pt').is_file()


def test_state_dir_must_be_single_local_folder(tmp_path, monkeypatch):
    for state_dir in ('../escape', '/absolute', '.', 'nested/other'):
        args=Namespace(output=str(tmp_path),hard_cards=64,hard_batch=4,hard_updates=0,
                       hard_max_queue=64,hard_lr=.001,hard_state_dir=state_dir)
        with pytest.raises(ValueError, match='HARD_JOIN_INVALID_STATE_DIR'):
            hard.run(args)


def test_resumed_gate_revision_preserves_training_and_resets_streak(tmp_path, monkeypatch):
    """Simulate the prepatch saved files, then move all four gates forward."""
    from test_nanojev_emp_dual_bank_ladder import make_run
    args, _ = make_run(tmp_path, monkeypatch, stage_steps=2, max_stages=1, gate_streak=3)
    args.hard_cards = 64
    args.hard_batch = 4
    args.hard_updates = 1
    args.hard_max_queue = 64
    args.hard_lr = .0001
    monkeypatch.setattr(hard, 'accuracy_cards', lambda radius, seed, serial, n:
                        [(1000+i, -999-i, 1, 0, 1000+i) for i in range(n)])
    result = hard.run(args)
    assert result['status'] == 'halted_budget_exhausted'
    root = tmp_path / 'out'
    stage = root / 'stages' / 'outward_512'
    old_cp = torch.load(stage / 'latest.pt', weights_only=True)
    assert old_cp['streak'] == 2
    hard_dir = root / 'hard_emp'
    old_state = torch.load(hard_dir / 'latest.pt', weights_only=True)
    old_processed = copy.deepcopy(old_state['processed'])
    assert '512-2' in old_processed
    # Recreate the immutable config fingerprints from the actual older runner.
    cfg = json.loads((root / 'config.json').read_text())
    cfg['code_sha256'] = ladder.PRE_GATE_LADDER_SHA256
    (root / 'config.json').write_text(json.dumps(cfg))
    stage_cfg = json.loads((stage / 'config.json').read_text())
    stage_cfg['campaign_config_sha256'] = hard.sha256(root / 'config.json')
    (stage / 'config.json').write_text(json.dumps(stage_cfg))
    old_hard_cfg = json.loads((hard_dir / 'config.json').read_text())
    old_hard_cfg.pop('detector_gate')
    old_hard_cfg['implementation_sha256'] = hard.PRE_GATE_HARD_SHA256
    (hard_dir / 'config.json').write_text(json.dumps(old_hard_cfg))
    original_configs = [(root / 'config.json').read_bytes(), (stage / 'config.json').read_bytes(),
                        (hard_dir / 'config.json').read_bytes()]
    args.resume = True
    args.stage_steps = 3
    args.supplier_gate = .65
    args.detector_gate = .55
    args.left_gate = .85
    args.right_gate = .55
    with pytest.raises(RuntimeError, match='HARD_JOIN_GATE_CHANGE_REQUIRES_AUTHORIZATION'):
        hard.run(args)
    assert not (root / ladder.GATE_TUNING_FILE).exists()
    args.allow_gate_change = True
    again = hard.run(args)
    assert again['status'] == 'halted_budget_exhausted'
    new_cp = torch.load(stage / 'latest.pt', weights_only=True)
    # New gate must NOT inherit the two old consecutive passes.
    assert new_cp['streak'] == 1 and new_cp['gate_revision'] == 1
    new_hard_state = torch.load(hard_dir / 'latest.pt', weights_only=True)
    assert all(new_hard_state['processed'][k] == v for k,v in old_processed.items())
    assert new_hard_state['selected'] == old_state['selected']
    journal = json.loads((root / ladder.GATE_TUNING_FILE).read_text())
    assert journal['revisions'][-1] == {'revision': 1, 'supplier_gate': .65,
           'detector_gate': .55, 'left_gate': .85, 'right_gate': .55}
    assert original_configs == [(root / 'config.json').read_bytes(),
                                (stage / 'config.json').read_bytes(),
                                (hard_dir / 'config.json').read_bytes()]
    assert new_hard_state['processed']['512-3']['gate_revision'] == 1
    assert new_hard_state['processed']['512-3']['supplier_gate'] == .65
    assert new_hard_state['processed']['512-3']['detector_gate'] == .55
    # A second resume without explicit authorization is allowed if all four
    # CLI thresholds still exactly match the last recorded policy.
    args.allow_gate_change = False
    assert hard.run(args)['status'] == 'halted_budget_exhausted'
    args.detector_gate = .50
    with pytest.raises(RuntimeError, match='HARD_JOIN_GATE_CHANGE_REQUIRES_AUTHORIZATION'):
        hard.run(args)
    # Multiple deliberate gate updates retain a coherent revision sequence.
    args.detector_gate = .60
    args.supplier_gate = .70
    args.allow_gate_change = True
    args.stage_steps = 4
    assert hard.run(args)['status'] == 'halted_budget_exhausted'
    revision2 = json.loads((root / ladder.GATE_TUNING_FILE).read_text())
    assert [entry['revision'] for entry in revision2['revisions']] == [0, 1, 2]
    last = torch.load(stage / 'latest.pt', weights_only=True)
    assert last['gate_revision'] == 2 and last['streak'] == 1
    assert original_configs == [(root / 'config.json').read_bytes(),
                                (stage / 'config.json').read_bytes(),
                                (hard_dir / 'config.json').read_bytes()]


def test_detector_threshold_tunable_at_inference():
    class ConstantDetector(torch.nn.Module):
        def forward(self, x):
            return torch.full((len(x),), 0.20067, device=x.device)  # sigmoid about .55
    class ConstantSpecialist(torch.nn.Module):
        def forward(self, x):
            return None
    detector = ConstantDetector()
    pairs = [{'hard': {'latent':torch.zeros(16), 'card':(100,-99,1,0,100)},
              'easy': {'latent':torch.zeros(16), 'card':(100,-100,0,0,100)}}]
    original_input = hard.input_tensor
    original_decode = dual.decoded
    try:
        hard.input_tensor = lambda cards, device: cards
        dual.decoded = lambda model_output: [1,1]
        a = hard.evaluate_specialist(detector, {'hard.left': ConstantSpecialist()}, pairs, 'cpu', detector_gate=.5)
        b = hard.evaluate_specialist(detector, {'hard.left': ConstantSpecialist()}, pairs, 'cpu', detector_gate=.56)
        assert a['tp'] == 1 and a['fp'] == 1
        assert b['fn'] == 1 and b['tn'] == 1
    finally:
        hard.input_tensor = original_input
        dual.decoded = original_decode


def test_changed_supplier_gate_is_strictly_greater():
    cards = [(100+i,-99-i,1,0,100+i) for i in range(20)]
    predictions = {name: [1]*13 + [100000]*7 for name in dual.BANKS}
    assert not hard.qualifying_suppliers(predictions, cards, threshold=.65)[1]
    assert len(hard.qualifying_suppliers(predictions, cards, threshold=.60)[1])==4


def test_gate_change_retains_legacy_best_pair_migration_certificate(tmp_path, monkeypatch):
    """Old campaigns may pin the original fixed-bank code and a gate migration."""
    from test_nanojev_emp_dual_bank_ladder import make_run
    args, _ = make_run(tmp_path, monkeypatch, stage_steps=1, max_stages=1)
    args.hard_cards=64
    args.hard_batch=4
    args.hard_updates=0
    args.hard_max_queue=64
    args.hard_lr=.0001
    monkeypatch.setattr(hard, 'accuracy_cards', lambda radius, seed, serial, n:
                        [(1000+i,-999-i,1,0,1000+i) for i in range(n)])
    hard.run(args)
    root=tmp_path/'out'
    cfg=json.loads((root/'config.json').read_text())
    del cfg['gate_policy']
    cfg['code_sha256']=ladder.LEGACY_CODE_SHA256
    (root/'config.json').write_text(json.dumps(cfg))
    stage=root/'stages'/'outward_512'
    stage_config=json.loads((stage/'config.json').read_text())
    stage_config['campaign_config_sha256']=hard.sha256(root/'config.json')
    (stage/'config.json').write_text(json.dumps(stage_config))
    certificate={'schema':ladder.SCHEMA+'.gate-migration',
                 'gate_policy':ladder.GATE_POLICY,
                 'campaign_config_sha256':hard.sha256(root/'config.json'),
                 'old_code_sha256':ladder.LEGACY_CODE_SHA256,
                 'new_code_sha256':ladder.PRE_GATE_LADDER_SHA256}
    (root/ladder.MIGRATION_FILE).write_text(json.dumps(certificate))
    old_hard=json.loads((root/'hard_emp'/'config.json').read_text())
    old_hard.pop('detector_gate')
    old_hard['implementation_sha256']=hard.PRE_GATE_HARD_SHA256
    (root/'hard_emp'/'config.json').write_text(json.dumps(old_hard))
    args.resume=True
    args.stage_steps=2
    args.supplier_gate=.65
    args.detector_gate=.55
    args.left_gate=.85
    args.right_gate=.55
    args.allow_gate_change=True
    assert hard.run(args)['status']=='halted_budget_exhausted'
    assert json.loads((root/ladder.MIGRATION_FILE).read_text())==certificate
    assert (root/ladder.GATE_TUNING_FILE).is_file()
