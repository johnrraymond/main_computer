"""CPU tests for zero-only scale-stability backbone training; no CUDA needed."""
from __future__ import annotations

import json
import random
from argparse import Namespace
from pathlib import Path

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_zero_line_zero_only as z


def arguments(tmp_path):
    return Namespace(phase='train', ladder_output=str(tmp_path / 'ladder'),
                     output=str(tmp_path / 'run'), device='cpu', max_steps=1,
                     probe_every=1, batch_size=4, legacy_batch_size=4,
                     legacy_weight=2., lr=0.00005, eval_batch_size=512,
                     legacy_probe_cards=16, anchors_per_band=4,
                     train_probe_anchors=2, stability_probes=2,
                     cpu_threads=2, seed=20261025, resume=False)


def test_fold_deterministic_and_max_sealed():
    assert z.fold(2_000_000) == 'test'
    assert [z.fold(m) for m in range(128, 250)] == [z.fold(m) for m in range(128, 250)]
    assert set(z.fold(m) for m in range(128, 256)) == {'train', 'dev', 'test'}


def test_probe_train_dev_test_magnitude_disjoint():
    sets = {s: set(c[4] for c in z.build_probe(s, 20261025, 6)
                   if c[4] > 127) for s in z.SPLITS}
    for i, s in enumerate(z.SPLITS):
        for other in z.SPLITS[i+1:]:
            assert not (sets[s] & sets[other]), (s, other)
    assert 2_000_000 in sets['test']


def test_probe_zero_and_nonzero_and_symmetry():
    cards = z.build_probe('test', 20261025, 8)
    assert any(a == -b and flag for a,b,flag,*_ in cards)
    assert any(a+b != 0 and not flag for a,b,flag,*_ in cards)
    assert any(a==2_000_000 and b==-2_000_000 for a,b,*_ in cards)
    assert any(a==-2_000_000 and b==2_000_000 for a,b,*_ in cards)
    for a,b,zero,_,m in cards:
        assert (a+b == 0) == zero
        assert max(abs(a), abs(b)) == m <= z.MAX_ABS


def test_train_sampling_balanced_and_strictly_not_test():
    cards = z.train_cards(random.Random(91), 600, 20261025)
    assert sum(c[2] for c in cards) == 300
    for a, b, flag, _, m in cards:
        assert (a+b==0) == flag
        if m>127:
            assert z.fold(m) == 'train'


def test_classify_rejects_always_zero_and_wrong_encoding():
    class ConstantZero(torch.nn.Module):
        def forward(self,x):
            signs=torch.tensor([[0.,9.,0.]],device=x.device).expand(len(x),-1)
            digits=torch.zeros((len(x),7,10),device=x.device)
            digits[:,:,0]=9
            return {'sign':signs,'digits':digits}
    cards=[(300,-300,True,3,300),(300,-299,False,3,300)]
    stats=z.classify(ConstantZero(),cards,'cpu',16)
    assert stats['by_band']['256-511']['zero_rate']==1.
    assert stats['by_band']['256-511']['nonzero_rate']==0.
    assert not stats['passes']


def test_loss_does_not_require_nonzero_numeric_labels():
    torch.manual_seed(4)
    signs=torch.randn(4,3,requires_grad=True)
    digits=torch.randn(4,7,10,requires_grad=True)
    loss=z.zero_logits_loss({'sign': signs, 'digits':digits},torch.tensor([True,False,True,False]))
    loss.backward()
    assert bool(torch.isfinite(loss))
    assert signs.grad is not None and digits.grad is not None
    assert signs.grad.abs().sum()>0 and digits.grad.abs().sum()>0


def test_gradients_reach_real_backbone_not_frozen_output_heads():
    model=AdditionNet('digit_tokens',8,2,1,1,42)
    teacher=AdditionNet('digit_tokens',8,2,1,1,42).eval()
    teacher.requires_grad_(False)
    for name,p in model.named_parameters():
        p.requires_grad_(name.startswith('backbone.') or name.startswith('norm.') or name in ('query','pos'))
    opt=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=1.e-4)
    args=Namespace(batch_size=4,seed=20261025,device='cpu',legacy_batch_size=4,legacy_weight=2.)
    name='backbone.layers.0.self_attn.in_proj_weight'
    before=dict(model.named_parameters())[name].detach().clone()
    head_before=model.sign_head.weight.detach().clone()
    values=z.train_step(model,teacher,opt,random.Random(22),args,[1.]*len(z.BANDS))
    assert all(math_isfinite(v) for v in values)
    assert not torch.equal(before,dict(model.named_parameters())[name])
    assert torch.equal(head_before,model.sign_head.weight)


def math_isfinite(v):
    import math
    return math.isfinite(v)


def test_plan_no_sources_required(tmp_path):
    a=arguments(tmp_path); a.phase='plan'
    result=z.run(a)
    assert result['historical_full_deck']==65025
    assert result['max_magnitude']==2_000_000


def _fake_loader(monkeypatch,tmp_path):
    source=tmp_path/'frozen_source.pt'
    source.write_bytes(b'unchanged checkpoint source fixture')
    checksum=z.sha256(source)
    def load(*args):
        return AdditionNet('digit_tokens',8,2,1,1,42).to(args[1]), {
            'checkpoint':str(source),'checkpoint_sha256':checksum,
            'config_sha256':checksum,'step':100,'source_radius':127,'source_streak':3}
    monkeypatch.setattr(z,'load_certified_model',load)
    return source,checksum


def test_real_cpu_run_resume_and_source_immutable(tmp_path,monkeypatch):
    src, digest=_fake_loader(monkeypatch,tmp_path)
    args=arguments(tmp_path)
    out=z.run(args)
    assert out['status']=='budget_exhausted_no_promotion'
    assert (Path(args.output)/'latest.pt').exists()
    assert (Path(args.output)/'result.json').exists()
    assert not (Path(args.output)/'promoted.pt').exists()
    assert z.sha256(src)==digest
    args.resume=True; args.max_steps=2
    next_out=z.run(args)
    assert next_out['last_step']==2
    assert z.sha256(src)==digest


def test_config_drift_refused(tmp_path,monkeypatch):
    _fake_loader(monkeypatch,tmp_path)
    a=arguments(tmp_path);z.run(a)
    a.resume=True;a.lr=.0002
    with pytest.raises(RuntimeError,match='DRIFT'):
        z.run(a)


def test_test_seal_prevents_resume(tmp_path, monkeypatch):
    _fake_loader(monkeypatch,tmp_path)
    a=arguments(tmp_path);z.run(a)
    output=Path(a.output)/'result.json'
    doc=json.loads(output.read_text())
    doc['holdout_once']={'passes':False}
    output.write_text(json.dumps(doc))
    a.resume=True
    with pytest.raises(RuntimeError,match='SEALED_TEST_ALREADY_OPENED'):
        z.run(a)


def test_perfect_dev_but_historical_regression_cannot_promote(tmp_path,monkeypatch):
    _fake_loader(monkeypatch,tmp_path)
    a=arguments(tmp_path); a.max_steps=2
    calls=[]
    def good_classify(_model,cards,_device,_batch):
        calls.append(len(cards))
        return {'cards':len(cards), 'passes':True, 'min_zero':1.,
                'min_nonzero_rejection':1.,'by_band': {
                    f'{lo}-{hi}':{'zero_rate':1., 'nonzero_rate':1.}
                    for lo,hi in z.BANDS}}
    def bad_history(_model,rows,*_):
        n=len(rows['x'])
        return {'groups':{'all':{'passed':n-1 if n==65025 else n,'accuracy': (n-1)/n if n==65025 else 1.}},
                'false_zero': 0}
    monkeypatch.setattr(z,'classify',good_classify)
    monkeypatch.setattr(z,'legacy_evaluate',bad_history)
    out=z.run(a)
    assert out['status']=='budget_exhausted_no_promotion'
    assert out['historical_full']['groups']['all']['passed']==65024
    assert 'holdout_once' not in out
    assert not (Path(a.output)/'promoted.pt').exists()


def test_one_time_holdout_refusal_fails_closed(tmp_path,monkeypatch):
    _fake_loader(monkeypatch,tmp_path)
    a=arguments(tmp_path);a.max_steps=2
    calls={'classify':0,'full':0}
    def classifier(_model,cards,_device,_batch):
        calls['classify']+=1
        # baseline, then train/dev for steps 1 and 2, then sealed test.
        is_test = calls['classify']==6
        return {'cards':len(cards),'passes':not is_test,
                'min_zero':0.90 if is_test else 1.,
                'min_nonzero_rejection':1., 'by_band':{
                    f'{lo}-{hi}':{'zero_rate':1.,'nonzero_rate':1.}
                    for lo,hi in z.BANDS}}
    def good_history(_model,rows,*_):
        n=len(rows['x']);calls['full']+=int(n==65025)
        return {'groups':{'all':{'passed':n,'accuracy':1.}},'false_zero':0}
    monkeypatch.setattr(z,'classify',classifier)
    monkeypatch.setattr(z,'legacy_evaluate',good_history)
    out=z.run(a)
    assert out['status']=='heldout_refused'
    assert calls['full']==1
    assert not (Path(a.output)/'promoted.pt').exists()
    a.resume=True;a.max_steps=3
    with pytest.raises(RuntimeError,match='SEALED_TEST_ALREADY_OPENED'):
        z.run(a)


def test_promote_requires_every_gate(tmp_path,monkeypatch):
    _fake_loader(monkeypatch,tmp_path)
    a=arguments(tmp_path);a.max_steps=2
    def good(_model,cards,_device,_batch):
        return {'cards':len(cards),'passes':True,'min_zero':1.,
                'min_nonzero_rejection':1.,'by_band':{
                    f'{lo}-{hi}':{'zero_rate':1.,'nonzero_rate':1.}
                    for lo,hi in z.BANDS}}
    def good_history(_model,rows,*_):
        n=len(rows['x'])
        return {'groups':{'all':{'passed':n,'accuracy':1.}}, 'false_zero':0}
    monkeypatch.setattr(z,'classify',good)
    monkeypatch.setattr(z,'legacy_evaluate',good_history)
    out=z.run(a)
    assert out['status']=='promoted'
    promoted=Path(a.output)/'promoted.pt'
    assert promoted.exists()
    st=torch.load(promoted,map_location='cpu',weights_only=True)
    assert st['historical_exact']==65025
    assert st['heldout']['passes']


def test_hard_zero_decoy_provides_digit_gradient():
    signs=torch.tensor([[0.,20.,0.]],requires_grad=True)
    digits=torch.zeros((1,7,10));digits[:,:,0]=30.
    digits.requires_grad_(True)
    loss=z.zero_logits_loss({'sign':signs,'digits':digits},torch.tensor([False]))
    loss.backward()
    assert torch.isfinite(loss)
    assert digits.grad is not None and digits.grad.abs().sum()>0
