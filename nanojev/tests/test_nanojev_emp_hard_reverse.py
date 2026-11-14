"""Reverse curriculum: current states move down; outward certifications immutable."""
import copy
import json
from argparse import Namespace

import pytest
import torch

from tools import nanojev_emp_hard_reverse as reverse
from tools import nanojev_emp_hard_join as hard
from tools import nanojev_emp_dual_bank_ladder as ladder
from tools import nanojev_emp_dual_bank as dual
from tools.nanojev_addition_curriculum import AdditionNet


def passing(_banks, _args, _radius):
    return {name:{subset:[{'nonzero':32,'within_5pct':.95} for _ in range(2)]
                        for subset in ('frontier','outer')} for name in dual.BANKS}


def test_reverse_schedule_stops_at_exact_zero():
    assert reverse.reverse_radii(32768)==[16384,8192,4096,2048,1024,512,0]
    assert reverse.reverse_radii(512)==[0]
    assert reverse.reverse_radii(1000)==[512,0]
    with pytest.raises(RuntimeError,match='HARD_REVERSE_UNSUPPORTED_FRONTIER'):
        reverse.reverse_radii(100)


def test_reverse_accuracy_train_split_at_zero(monkeypatch):
    from tools import nanojev_zero_line_outward_return as outward
    monkeypatch.setattr(outward,'generate_batch',lambda rng,r,seed,n,weights,c,phase,focus,target:
                       ([(100+i,-100-i,0,0,100+i) if i%2 else
                         (100+i,-99-i,1,0,100+i) for i in range(n)],c+n))
    cards=reverse.reverse_accuracy_cards(0,32768,123,'reverse-0-250',64)
    assert len(cards)==64
    assert all(a+b==target for a,b,target,_,_ in cards)
    assert any(target==0 for _,_,target,_,_ in cards)


def test_reverse_from_latest_preserves_history_and_can_resume(tmp_path,monkeypatch):
    from test_nanojev_emp_dual_bank_ladder import make_run
    args,_=make_run(tmp_path,monkeypatch,stage_steps=2,max_stages=2)
    # Only tiny fixed training cards in the fixture; origin stage reaches 1024.
    result=ladder.run(args)
    assert result['status']=='outward_demonstrated_to_target'
    root=tmp_path/'out'
    before={p:p.read_bytes() for p in (root/'stages').rglob('*.json')}
    cp=root/'stages'/'outward_1024'/'latest.pt'
    before_sha=reverse.sha256(cp)
    prior=torch.load(cp,map_location='cpu',weights_only=True)
    observed=[]
    monkeypatch.setattr(reverse,'probe',passing)
    monkeypatch.setattr(reverse,'attach_hard_probe',lambda root,args,banks,r,step,cp,frontier:
                        observed.append((r,step)))
    args.resume=True
    args.reverse=True
    args.max_stages=2
    args.gate_streak=1
    args.stage_steps=1
    args.hard_state_dir='hard_emp'
    # Avoid the forward gate-streak config drift: reverse uses existing campaign
    # gate parameters as saved by ladder, but may set a reverse-specific streak.
    monkeypatch.setattr(ladder,'source_config',lambda args: (root,{},None,'left','right'))
    monkeypatch.setattr(reverse,'load_certified_model',lambda *a: (AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1),{}))
    # Feed complete sources from the actual checkpoint dimensions using fixture source.
    sources=[dual.checked_source(root/'source_left.pt'),dual.checked_source(root/'source_right.pt')]
    monkeypatch.setattr(ladder,'source_config',lambda args: (root,{},None,*sources))
    assert reverse.run_reverse(args)['status']=='zero_line_complete'
    assert observed==[(512,1),(0,1)]
    assert reverse.sha256(cp)==before_sha
    assert all(p.read_bytes()==value for p,value in before.items())
    accepted=root/'hard_emp_reverse'/'stages'/'return_512'/'accepted.pt'
    zero=root/'hard_emp_reverse'/'stages'/'zero_line'/'accepted.pt'
    first=torch.load(accepted,map_location='cpu',weights_only=True)
    second=torch.load(zero,map_location='cpu',weights_only=True)
    assert first['radius']==512 and second['radius']==0
    assert set(second['optimizers'])==set(dual.BANKS)
    assert reverse.run_reverse(args)['status']=='zero_line_complete'
    assert observed==[(512,1),(0,1)]
    assert json.loads((root/'hard_emp_reverse'/'plan.json').read_text())['source_sha256']==before_sha
    with pytest.raises(RuntimeError,match='HARD_JOIN_REVERSE_COMMITTED'):
        args.reverse=False
        args.hard_cards=64;args.hard_batch=4;args.hard_updates=0;args.hard_max_queue=64;args.hard_lr=.0001
        hard.run(args)


def test_exact_zero_probe_has_nonzero_controls_and_checks_both(monkeypatch):
    from tools import nanojev_zero_line_sampled_radius_ladder as old
    cards=[[(100,-100,0,0,100),(101,-100,1,0,101)] for _ in range(2)]
    monkeypatch.setattr(old,'evaluation_cards',lambda *a,**k:cards)
    class ConstantZero(torch.nn.Module):
        def eval(self):return self
        def __call__(self, x):return x
    monkeypatch.setattr(dual,'decoded',lambda out: [0]*len(out) if out is not None else [0])
    args=Namespace(seed=1,breadth_anchors=1,eval_batch_size=8,device='cpu')
    scores=reverse.exact_zero_probe({n:ConstantZero() for n in dual.BANKS},args)
    for name in dual.BANKS:
        assert scores[name]['frontier'][0]['within_5pct']==1.
        assert scores[name]['outer'][0]['within_5pct']==0.
    assert not ladder.select_pair(scores,.85,.55)['qualified']


def test_budget_exhaustion_resumes_same_reverse_stage(tmp_path,monkeypatch):
    from test_nanojev_emp_dual_bank_ladder import make_run
    args,_=make_run(tmp_path,monkeypatch,stage_steps=1,max_stages=1,gate_streak=1)
    ladder.run(args)
    root=tmp_path/'out'
    args.resume=True; args.reverse=True; args.max_stages=1
    args.stage_steps=1;args.gate_streak=1;args.hard_state_dir='hard_emp'
    _,_,_,left,right=ladder.source_config(args)
    monkeypatch.setattr(reverse,'load_certified_model',lambda *a: (AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1),{}))
    monkeypatch.setattr(reverse,'attach_hard_probe',lambda *a,**k:None)
    state={'passing':False}
    def score(banks,args,radius):
        table=passing(banks,args,radius)
        if not state['passing']:
            for name in table:
                for subset in table[name]:
                    for view in table[name][subset]:view['within_5pct']=.1
        return table
    monkeypatch.setattr(reverse,'probe',score)
    assert reverse.run_reverse(args)['status']=='reverse_budget_exhausted'
    cp=root/'hard_emp_reverse'/'stages'/'zero_line'/'latest.pt'
    saved=torch.load(cp,map_location='cpu',weights_only=True)
    assert saved['step']==1
    assert saved['streak']==0
    state['passing']=True
    args.stage_steps=2
    assert reverse.run_reverse(args)['status']=='zero_line_complete'
    completed=torch.load(cp,map_location='cpu',weights_only=True)
    assert completed['step']==2
    assert (root/'hard_emp_reverse'/'stages'/'zero_line'/'accepted.pt').is_file()
