"""Focused offline progressive LASSO tests; no changes to original EMP files."""
import copy
import json
from argparse import Namespace

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_emp_hard_lasso as lasso
from tools import nanojev_emp_hard_join as hard


def model():
    return AdditionNet('compact', width=8, heads=2, layers=1, rounds=1)


def test_readout_mask_same_channels_in_both_heads_and_real_ablation():
    net = model()
    hook = lasso.ReadoutMask(net)
    x = torch.tensor([[200, -199], [1000, -1000]])
    full = net(x)
    with torch.no_grad():
        # Readout output should change for at least one head on active input.
        hook.mask[0] = 0
        hook.mask[1] = 0
        new = net(x)
    assert (new['sign'] - full['sign']).abs().sum() > 0 or (new['digits'] - full['digits']).abs().sum() > 0
    assert torch.isfinite(hook.penalty())
    hook.mask[:] = 1
    p = hook.prune_to(.25)
    assert p['removed'] == 2 and p['active'] == 6
    assert torch.count_nonzero(hook.mask) == 6
    with pytest.raises(RuntimeError, match='CANNOT_REGROW'):
        hook.prune_to(.125)
    hook.close()
    assert torch.allclose(net(x)['sign'], full['sign'])


def test_parse_schedule_guards():
    assert lasso.parse_schedule('0,.02,.05,.10') == [0., .02, .05, .10]
    for x in ('0', '.2,.3', '0,.10,.10', '0,-.2', '0,.5,.2', '0,1.0', '0,nan'):
        with pytest.raises(ValueError):
            lasso.parse_schedule(x)


def pair(idx, supplier='right.right'):
    a = 300 + idx
    hard_card = [a, -a+1, 1, 0, a]
    easy_card = [a+1000, -a-999, 1, 0, a+1000]
    return {'hard': {'card': hard_card, 'supplier': supplier, 'supplier_accuracy': .80,
                     'latent': torch.randn(8)},
            'easy': {'card': easy_card, 'supplier': supplier, 'supplier_accuracy': .80,
                     'latent': torch.randn(8)}}


def test_loader_keeps_holdouts_separate_and_rejects_unqualified(tmp_path):
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    record = {'schema': hard.SCHEMA+'.evidence', 'training_pairs':[pair(0),pair(1)],
              'validation_pairs':[pair(20),pair(21)]}
    torch.save(record, evidence/'512-250.pt')
    train, dev, proofs = lasso.load_pairs(evidence, 10, 10, 1)
    assert len(train) == len(dev) == 2 and len(proofs) == 1
    record['validation_pairs'].append(record['training_pairs'][0])
    torch.save(record, evidence/'512-250.pt')
    with pytest.raises(RuntimeError, match='OVERLAP'):
        lasso.load_pairs(evidence, 10, 10, 1)
    record['validation_pairs'].pop()
    record['training_pairs'][0]['hard']['supplier_accuracy'] = .59
    torch.save(record, evidence/'512-250.pt')
    with pytest.raises(RuntimeError, match='UNQUALIFIED'):
        lasso.load_pairs(evidence, 10, 10, 1)


def test_matched_control_and_sparse_resume_isolated(tmp_path, monkeypatch):
    torch.set_num_threads(1)
    root = tmp_path/'campaign'
    out = tmp_path/'lasso'
    (root/'hard_emp_frontier_v2').mkdir(parents=True)
    (root/'hard_emp_reverse'/'stages'/'zero_line').mkdir(parents=True)
    (root/'hard_emp_frontier_v2'/'evidence').mkdir()
    (root/'hard_emp_frontier_v2'/'latest.pt').write_bytes(b'source')
    (root/'hard_emp_reverse'/'stages'/'zero_line'/'accepted.pt').write_bytes(b'reverse')
    primary = model()
    o = torch.optim.AdamW(primary.parameters(), lr=.001, weight_decay=0.)
    # Use actual optimizer moments in the cloned state.
    a = torch.tensor([[30, -29]])
    target = torch.tensor([1])
    loss = __import__('tools.nanojev_emp_dual_bank', fromlist=['loss_per_card']).loss_per_card(primary(a),target).mean()
    loss.backward()
    o.step()
    saved = copy.deepcopy(primary.state_dict())
    saved_opt = copy.deepcopy(o.state_dict())
    detector = hard.HardDetector(8)
    def fake_init(*_):
        cloned = model()
        cloned.load_state_dict(saved)
        return cloned, copy.deepcopy(detector), copy.deepcopy(saved_opt), 'source', 'reverse'
    def fake_pairs(*_):
        return [pair(i) for i in range(5)], [pair(i) for i in range(20,25)], [['fake.pt','sha']]
    monkeypatch.setattr(lasso,'initialize',fake_init)
    monkeypatch.setattr(lasso,'load_pairs',fake_pairs)
    monkeypatch.setattr(lasso,'ood_cards',lambda *a:[(100+i, -99-i, 1, 0, 100+i) for i in range(8)])
    monkeypatch.setattr(lasso,'sha256',lambda p: 'source' if str(p).endswith('latest.pt') else 'reverse')
    args = Namespace(campaign=str(root),ladder_output=str(root), hard_state_dir='hard_emp_frontier_v2',
        output=str(out), device='cpu', seed=7,sparsity='0,.25', steps_per_stage=1,
        batch=2,eval_batch=5,lr=.001,lambda_max=.01,max_train=10,max_dev=10,
        ood_cards=8,ood_min=100,ood_max=200,ablate_top=1,detector_gate=.55)
    result = lasso.experiment(args)
    assert result['status']=='complete' and result['stages']==2
    history = json.loads((out/'history.json').read_text())
    assert history[0]['pruning']['removed'] == 0
    assert history[1]['pruning']['removed'] == 2
    # At zero penalty and no pruning, both arms get equal optimizer steps.
    assert history[0]['control']['heldout_magnitude'] == history[0]['sparse']['heldout_magnitude']
    assert len(history[1]['ablations']) == 1
    assert lasso.experiment(args)['status'] == 'complete'  # completed resume is idempotent
    args.lambda_max = .2
    with pytest.raises(RuntimeError, match='CONFIG_DRIFT'):
        lasso.experiment(args)
    assert (root/'hard_emp_frontier_v2'/'latest.pt').read_bytes() == b'source'
    assert (root/'hard_emp_reverse'/'stages'/'zero_line'/'accepted.pt').read_bytes() == b'reverse'


def test_train_step_regularization_changes_loss_direction():
    net = model()
    gate = lasso.ReadoutMask(net)
    opt = torch.optim.AdamW(net.parameters(), lr=.001, weight_decay=0.)
    batch = [pair(0),pair(1)]
    before = copy.deepcopy(net.sign_head.weight.detach())
    lasso.train_step(net,opt,gate,batch,'cpu',0.001)
    assert not torch.equal(before, net.sign_head.weight)
    gate.close()


def test_initialize_loads_pinned_snapshots_without_invoking_ladder_resume(tmp_path, monkeypatch):
    """A tuned/reversed campaign is read-only; no full ladder args are needed."""
    campaign = tmp_path/'campaign'
    campaign.mkdir()
    sidecar = campaign/'specialist.pt'
    reverse = campaign/'hard_emp_reverse'/'stages'/'zero_line'/'accepted.pt'
    reverse.parent.mkdir(parents=True)
    torch.save({'radius': 0, 'banks': {'saved': torch.tensor(1)}}, reverse)

    snapshot_fields = {
        'source_dual.pt': 'source_checkpoint_sha256',
        'source_config.json': 'source_config_sha256',
        'source_left.pt': 'left_source_sha256',
        'source_right.pt': 'right_source_sha256',
    }
    cfg = {}
    for fn, key in snapshot_fields.items():
        (campaign/fn).write_bytes(('pinned:'+fn).encode())
        cfg[key] = lasso.sha256(campaign/fn)
    (campaign/'config.json').write_text(json.dumps(cfg), encoding='utf-8')

    banks = {name: model() for name in ('left.left','left.right','right.left','right.right')}
    selected = ('right.right',)
    units, detector, opt = hard.make_specialist(banks, selected, 'cpu', .0001)
    torch.save({'selected': list(selected), 'specialists': units.state_dict(),
                'detector': detector.state_dict(),
                'optimizers': {'hard.left': opt['hard.left'].state_dict()}}, sidecar)

    # A previous implementation called this trainer/migration entry point using
    # a partial SimpleNamespace and crashed on a missing `left_gate` attribute.
    monkeypatch.setattr(lasso.ladder, 'source_config',
                        lambda *a, **kw: pytest.fail('read-only LASSO called ladder resume'))
    monkeypatch.setattr(lasso.dual, 'checked_source', lambda p: p.name)
    monkeypatch.setattr(lasso.ladder, 'load_certified_model', lambda *a: (object(), {}))
    def fake_from_source(_base, left, right, saved, device):
        assert left == 'source_left.pt' and right == 'source_right.pt'
        assert saved['saved'].item() == 1
        return banks
    monkeypatch.setattr(lasso.ladder, 'from_source', fake_from_source)

    original = {fn: (campaign/fn).read_bytes() for fn in snapshot_fields}
    specialist, got_detector, moments, digest, reverse_digest = lasso.initialize(
        campaign, tmp_path/'base', sidecar, 'cpu')
    assert isinstance(specialist, torch.nn.Module)
    assert isinstance(got_detector, hard.HardDetector)
    assert moments['param_groups'] == opt['hard.left'].state_dict()['param_groups']
    assert digest == lasso.sha256(sidecar)
    assert reverse_digest == lasso.sha256(reverse)
    assert original == {fn: (campaign/fn).read_bytes() for fn in snapshot_fields}

    (campaign/'source_right.pt').write_bytes(b'mutated')
    with pytest.raises(RuntimeError, match='HARD_LASSO_SOURCE_SNAPSHOT_DRIFT'):
        lasso.initialize(campaign, tmp_path/'base', sidecar, 'cpu')
