"""Research contracts: independent four-bank weights, answer-only feedback, resume."""
from argparse import Namespace
import copy
import json

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools.nanojev_emp_population import Population,SCHEMA as SOURCE_SCHEMA
from tools import nanojev_emp_dual_bank as dual
from tools.nanojev_emp_dual_bank_tune import update


def sample_model():
    return AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1)


def source_state(parent):
    population=Population(copy.deepcopy(parent))
    with torch.no_grad():
        population.heads['left'].sign.weight.add_(.013)
        population.heads['right'].sign.weight.sub_(.017)
    return {'schema':SOURCE_SCHEMA+'.checkpoint','step':4000,'model':population.state_dict()}


def test_four_copies_distinct_emp_prefill_and_no_alias():
    parent=sample_model()
    source=source_state(parent)
    banks=dual.construct_banks(parent,source,source)
    assert set(banks)==set(dual.BANKS)
    for emp in dual.EMPS:
        torch.testing.assert_close(banks[f'{emp}.left'].sign_head.weight,banks[f'{emp}.right'].sign_head.weight)
    assert not torch.equal(banks['left.left'].sign_head.weight,banks['right.left'].sign_head.weight)
    identities=[{id(t) for t in bank.parameters()} for bank in banks.values()]
    assert all(not identities[i]&identities[j] for i in range(4) for j in range(i+1,4))
    original=banks['left.right'].sign_head.weight.detach().clone()
    with torch.no_grad():banks['left.left'].sign_head.weight.add_(1.)
    torch.testing.assert_close(banks['left.right'].sign_head.weight,original)
    for name in dual.BANKS:
        assert all(p.requires_grad for p in banks[name].parameters())


def test_answer_only_right_to_left_weights_and_trained_targets():
    cards=[(100,-97,3,0,100),(110,-100,10,0,110),(100,-100,0,0,100)]
    # right better first; left better second; exact equal third
    weights=dual.right_answer_emphasis(cards,[1,10,0],[3,2,0],5.,'cpu')
    assert weights.tolist()==[5.,1.,1.]
    with pytest.raises(ValueError):dual.right_answer_emphasis(cards,[1],[2],3,'cpu')
    logits={'sign':torch.randn(3,3,requires_grad=True),'digits':torch.randn(3,7,10,requires_grad=True)}
    losses=dual.loss_per_card(logits,torch.tensor([3,10,0]))
    assert losses.shape==(3,)
    (losses*weights).mean().backward()
    assert logits['sign'].grad is not None and logits['digits'].grad is not None


def test_bank_optimizers_update_only_owned_weights():
    parent=sample_model();src=source_state(parent);banks=dual.construct_banks(parent,src,src)
    opt=dual.make_bank_optimizers(banks,dual.default_controls())
    x=torch.tensor([[20,2],[-30,11]])
    loss=dual.loss_per_card(banks['left.left'](x),torch.tensor([22,-19])).mean()
    before={k:{n:p.detach().clone() for n,p in model.named_parameters()} for k,model in banks.items()}
    opt['left.left'].zero_grad();loss.backward();opt['left.left'].step()
    assert any(not torch.equal(p,before['left.left'][n]) for n,p in banks['left.left'].named_parameters())
    for key in ('left.right','right.left','right.right'):
        for n,p in banks[key].named_parameters():torch.testing.assert_close(p,before[key][n],rtol=0,atol=0)
    assert set(opt)==set(dual.BANKS)
    cfg=dual.default_controls();cfg['banks']['left.left']['readout_lr']=.00005
    dual.update_rates(opt,cfg)
    assert opt['left.left'].param_groups[1]['lr']==.00005


def test_gate_and_disagreement_scoring():
    pairs=dual.paired_outcomes([(3,-2,1,0,3),(4,-2,2,0,4),(-2,1,-1,0,2),(5,-5,0,0,5)],
                               [1,2,1,0],[1,3,-1,3])
    assert pairs['both_correct']==1
    assert pairs['left_only']==2
    assert pairs['right_only']==1
    assert pairs['left_tip_only']==1
    scores={f'{e}.{b}':{'frontier':[{'nonzero':10,'within_5pct':.9}],
                          'outer':[{'nonzero':10,'within_5pct':.9}]} for e in dual.EMPS for b in dual.SIDES}
    assert dual.research_gate(scores)
    scores['left.left']['outer'][0]['within_5pct']=.79
    assert not dual.research_gate(scores)


def test_controls_validation_and_tuning(tmp_path):
    root=tmp_path/'out';root.mkdir()
    before=dual.checked_controls(dual.default_controls())
    (root/'controls.json').write_text(json.dumps(before))
    after=update(root,'left.left',{'right_answer_boost':10.,'train_every':2})
    assert after['revision']==1
    assert after['banks']['left.left']['train_every']==2
    with pytest.raises(ValueError):update(root,'left.right',{'right_answer_boost':5.})
    with pytest.raises(ValueError):update(root,'left.left',{'frontier_boost':float('nan')})
    with pytest.raises(ValueError):update(root,'left.left',{'train_every':1.5})
    assert json.loads((root/'controls.json').read_text())==after


def test_end_to_end_cpu_resume_and_live_tune(tmp_path,monkeypatch):
    parent=sample_model()
    st=source_state(parent)
    src=tmp_path/'population';src.mkdir()
    torch.save(st,src/'latest.pt')
    (src/'config.json').write_text(json.dumps({'schema':SOURCE_SCHEMA,'radius':512}))
    # Keep source initialization small while verifying actual checkpoint copying.
    monkeypatch.setattr(dual,'load_certified_model',lambda *args,**kw:(sample_model(),{}))
    cards=[(512,-499,13,0,512),(111,111,222,0,111),(-512,512,0,0,512),(100,-72,28,0,100)]
    monkeypatch.setattr(dual.ladder,'frontier_cards',lambda *args:cards)
    monkeypatch.setattr(dual.old,'evaluation_cards',lambda *args:[cards,cards])
    monkeypatch.setattr(dual.ladder,'generate_batch',lambda rng,radius,seed,size,weights,cursor,phase,focus,target:(cards*2,cursor+8))
    args=Namespace(source_population=str(src),left_checkpoint=None,right_checkpoint=None,
        ladder_output='/not/used',output=str(tmp_path/'new'),radius=512,
        steps=1,probe_every=1,batch_size=8,dev_cards=4,breadth_anchors=1,breadth_outer_per_anchor=1,
        eval_batch_size=8,seed=22,left_gate=.8,right_gate=.5,cpu_threads=1,device='cpu',resume=False,stop_on_gate=False)
    first=dual.run(args)
    assert first['step']==1 and len(first['banks'])==4
    assert (tmp_path/'new'/'source_left.pt').is_file()
    assert (tmp_path/'new'/'histogram.svg').is_file()
    assert len(first['paired']['left']['frontier'])==2
    changed=update(args.output,'left.left',{'right_answer_boost':10.,'frontier_boost':10.})
    args.resume=True;args.steps=2
    nxt=dual.run(args)
    assert nxt['revision']==changed['revision'] and nxt['step']==2
    ckpt=torch.load(tmp_path/'new'/'latest.pt',map_location='cpu',weights_only=True)
    assert ckpt['control_log'][-1]['effective_step']==2
    assert len(ckpt['optimizers'])==4
    assert len(json.loads((tmp_path/'new'/'history.json').read_text()))==2
    # Historical source can move without breaking resumed provenance: copied snapshots are authoritative.
    torch.save({'not':'source anymore'},src/'latest.pt')
    args.steps=3
    assert dual.run(args)['step']==3


def test_exclusive_dev_is_not_used_for_train_assistance():
    # Data-generating training cards are the only input to the answer comparator.
    import inspect
    src=inspect.getsource(dual.run)
    assert 'right_answer_emphasis(rows,' in src
    assert 'right_answer_emphasis(fixtures' not in src
    assert 'legacy_loss(' not in src


def test_distinct_source_checkpoints_can_prefill_each_emp():
    parent=sample_model()
    left=source_state(parent)
    right=source_state(parent)
    right['model']['parent.query']=right['model']['parent.query'].clone()+.1
    banks=dual.construct_banks(parent,left,right)
    torch.testing.assert_close(banks['left.left'].query,banks['left.right'].query)
    torch.testing.assert_close(banks['right.left'].query,banks['right.right'].query)
    assert not torch.equal(banks['left.left'].query,banks['right.left'].query)
