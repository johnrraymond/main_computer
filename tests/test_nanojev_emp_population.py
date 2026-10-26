"""CPU-level safety and correctness contracts for experimental EMP population."""
import copy
import json

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools.nanojev_emp_population import (
    HIST_BOUNDS, HEADS, Population, apply_learning_rates, checked_controls,
    default_controls, example_emphases, is_numeric_repeat, make_optimizer,
    meets_gate, population_gate, ratios_histogram, training_loss,
)


def test_initial_population_heads_are_identical_to_original_outputs():
    torch.manual_seed(12)
    parent=AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1)
    pop=Population(parent)
    x=torch.tensor([[35,-12],[-10,10],[512,0]],dtype=torch.long)
    with torch.no_grad():
        original=parent(x)
        result=pop(x)
    for name in HEADS:
        torch.testing.assert_close(result[name]['sign'],original['sign'],rtol=0,atol=0)
        torch.testing.assert_close(result[name]['digits'],original['digits'],rtol=0,atol=0)


def test_one_step_independent_heads_and_shared_backbone_receive_gradients():
    parent=AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1)
    pop=Population(parent)
    cards=[(100,-101,-1,0,100),(512,-256,256,1,512),(111,111,222,1,111),(150,-150,0,0,150)]
    x=torch.tensor([[c[0],c[1]] for c in cards])
    target=torch.tensor([c[2] for c in cards])
    controls=default_controls()
    results=pop(x)
    loss=sum(training_loss(results[name],target,example_emphases(cards,512,controls['heads'][name],'cpu'))
             *controls['heads'][name]['loss_weight'] for name in HEADS)
    loss.backward()
    assert pop.parent.query.grad is not None and torch.isfinite(pop.parent.query.grad).all()
    assert all(pop.heads[name].sign.weight.grad is not None for name in HEADS)
    assert pop.parent.input.digit.weight.grad is None


def test_repeated_digits_is_numeric_not_lexical():
    assert is_numeric_repeat(111,9)
    assert is_numeric_repeat(-122,9)
    assert not is_numeric_repeat(-123,456)
    c=default_controls()['heads']['repeated_digits']
    rows=[(111,9,120,0,111),(123,9,132,0,123),(200,-200,0,0,200)]
    weights=example_emphases(rows,512,c,'cpu')
    assert weights.tolist()==[6.,1.,6.]


def test_control_validation_live_revision_and_optimizer_groups():
    p=Population(AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1))
    c=checked_controls(default_controls())
    opt=make_optimizer(p,c)
    assert set(g['head_name'] for g in opt.param_groups)=={'backbone',*HEADS}
    new=copy.deepcopy(c)
    new['revision']=1
    new['backbone_lr']=.000002
    new['heads']['left']['lr']=.00003
    checked_controls(new)
    apply_learning_rates(opt,new)
    lr={g['head_name']:g['lr'] for g in opt.param_groups}
    assert lr['left']==.00003 and lr['backbone']==.000002
    for bad in (-1,0,float('nan'),float('inf')):
        corrupt=copy.deepcopy(c)
        corrupt['heads']['right']['loss_weight']=bad
        with pytest.raises(ValueError):checked_controls(corrupt)
    corrupt=copy.deepcopy(c)
    corrupt['heads']['lexical_repeats']=corrupt['heads']['left']
    with pytest.raises(ValueError):checked_controls(corrupt)


def test_histogram_counts_wrong_side_separately():
    rows=[(5,-3,2,0,5),(-4,2,-2,0,4),(1,-1,0,0,1),(1,1,2,0,1)]
    values=[-1,2,0,2]
    bins=ratios_histogram(rows,values)
    assert sum(bins)==3
    assert bins[0]==0
    # negative q values (wrong side) must fall below q=0.
    zero_cut=HIST_BOUNDS.index(0.)
    assert sum(bins[:zero_cut+1])==2


def test_population_gate_measures_both_views_per_actual_head():
    reports={'left':{'frontier':[{'nonzero':10,'within_5pct':.88},{'nonzero':10,'within_5pct':.80}],
                       'outer':[{'nonzero':10,'within_5pct':.90},{'nonzero':10,'within_5pct':.85}]},
             'right':{'frontier':[{'nonzero':10,'within_5pct':.50},{'nonzero':10,'within_5pct':.52}],
                      'outer':[{'nonzero':10,'within_5pct':.57},{'nonzero':10,'within_5pct':.53}]}}
    assert population_gate(reports)
    reports['left']['frontier'][1]['within_5pct']=.79
    assert not population_gate(reports)
    reports['left']['frontier'][1]['within_5pct']=.81
    reports['right']['frontier'][1]['within_5pct']=.49
    assert not population_gate(reports)


def test_tuning_command_atomic_revision(tmp_path):
    from tools.nanojev_emp_tune import update
    root=tmp_path/'experiment';root.mkdir()
    f=root/'controls.json';f.write_text(json.dumps(default_controls()))
    result=update(root,'left',{'loss_weight':7.,'lr':.00008},backbone_lr=.000002)
    assert result['revision']==1
    assert result['heads']['left']['loss_weight']==7.
    assert json.loads(f.read_text())==result
    with pytest.raises(ValueError):update(root,'left',{'frontier_boost':float('inf')})
    assert json.loads(f.read_text())==result


def test_training_and_resume_with_live_control_changes(tmp_path,monkeypatch):
    from argparse import Namespace
    from tools import nanojev_emp_population as emp
    state=AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1).state_dict()
    source_info={'checkpoint':'/fake/outward_512/latest.pt','sha256':'sample-source-sha','step':15000}
    monkeypatch.setattr(emp,'stage_source',lambda args:({'model':state},source_info))
    monkeypatch.setattr(emp,'load_certified_model',lambda *a,**k:(AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1),{}))
    # Fixed DEV examples stay disjoint from training examples.
    fixed=[(512,-400,112,0,512),(-300,100,-200,0,300),(-512,333,-179,0,512),(250,-250,0,0,250)]
    monkeypatch.setattr(emp.ladder,'frontier_cards',lambda radius,split,seed,view,count:fixed)
    monkeypatch.setattr(emp.old,'evaluation_cards',lambda radius,split,seed,stage,anchors,views,outer:[fixed for i in range(views)])
    batch=[(512,-450,62,0,512),(-512,500,-12,0,512),(111,111,222,0,111),(420,-420,0,0,420)]*4
    monkeypatch.setattr(emp.ladder,'generate_batch',lambda rng,radius,seed,size,weights,cursor,phase,focus,target:(batch,cursor+size))
    args=Namespace(source_stage='/fake/outward_512',ladder_output='/fake/l',output=str(tmp_path/'out'),
        radius=512,steps=1,probe_every=1,batch_size=16,dev_cards=4,breadth_anchors=1,breadth_outer_per_anchor=1,eval_batch_size=32,
        seed=17,left_gate=.8,right_gate=.5,cpu_threads=1,device='cpu',resume=False,stop_on_gate=False)
    first=emp.run(args)
    assert first['step']==1 and len(first['heads'])==4
    from tools.nanojev_emp_tune import update
    control=update(args.output,'left',{'loss_weight':4.})
    args.resume=True;args.steps=2
    second=emp.run(args)
    assert second['step']==2 and second['revision']==control['revision']
    assert len(json.loads((tmp_path/'out'/'history.json').read_text()))==2
    assert 'svg' in (tmp_path/'out'/'histogram.svg').read_text()
    ckpt=torch.load(tmp_path/'out'/'latest.pt',map_location='cpu',weights_only=True)
    assert ckpt['control_log'][-1]['effective_step']==2
    assert ckpt['controls']['heads']['left']['loss_weight']==4.
