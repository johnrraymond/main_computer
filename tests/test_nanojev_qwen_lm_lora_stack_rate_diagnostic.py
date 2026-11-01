"""Regression tests for a genuinely additive second q/v LoRA diagnostic."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from tools import nanojev_qwen_zero_line_backbone as zero
from tools import nanojev_qwen_lm_lora_stack_rate_diagnostic as diagnostic
from tools import nanojev_qwen_lm_frontier_accuracy_balance as balance
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_frontier_rate_diagnostic as rate
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr


class TinyAttn(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.q_proj=zero.LoRALinear(torch.nn.Linear(4,3),rank=2,alpha=4)
        self.v_proj=zero.LoRALinear(torch.nn.Linear(4,3),rank=2,alpha=4)

class TinyBB(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.attn=TinyAttn()

    def forward(self,x):
        return self.attn.q_proj(x)+self.attn.v_proj(x)


def test_second_adapter_is_identity_and_only_second_receives_gradients():
    bb=TinyBB();x=torch.randn(4,4)
    first=zero.lora_state(bb)
    before=bb(x).detach().clone()
    bb.requires_grad_(False)
    names=diagnostic._install_second(bb)
    assert len(names)==4
    assert torch.equal(before,bb(x))
    assert diagnostic._same_weights(diagnostic._first_state(bb),first)
    assert all(not p.requires_grad for n,p in bb.named_parameters() if not n.endswith(('.lora2_a','.lora2_b')))
    opt=torch.optim.AdamW([p for _,p in names],lr=.1)
    for _ in range(3):
        opt.zero_grad(); ((bb(x)-torch.ones_like(before))**2).sum().backward();opt.step()
    assert diagnostic._same_weights(diagnostic._first_state(bb),first)
    assert any(not torch.equal(p,torch.zeros_like(p)) for n,p in names if n.endswith('lora2_b'))
    assert not torch.equal(before,bb(x).detach())


def test_second_adapter_round_trip_and_conflict_guard():
    bb=TinyBB();x=torch.randn(2,4)
    bb.requires_grad_(False)
    names=diagnostic._install_second(bb)
    with torch.no_grad():
        for n,p in names:p.add_(.05)
    snapshot={n:p.detach().clone() for n,p in names}
    a=bb(x).detach().clone()
    # The original q/v module names are *not* silently replaced in snapshots;
    # the additive branch must preserve a separate explicit second state.
    original=diagnostic._first_state(bb)
    assert set(original)=={'attn.q_proj.lora_a','attn.q_proj.lora_b',
                           'attn.v_proj.lora_a','attn.v_proj.lora_b'}
    with torch.no_grad():
        for n,p in names:p.zero_()
        for n,p in names:p.copy_(snapshot[n])
    assert torch.equal(a,bb(x))


def _setup(tmp_path):
    s=tmp_path/'stage1.pt';s.write_bytes(b'fake')
    t=tmp_path/'text.pt';t.write_bytes(b'fake')
    balance_dir=tmp_path/'balance';balance_dir.mkdir()
    bb=TinyBB()
    optimizer=torch.optim.AdamW([p for _,p in zero.lora_named_parameters(bb)],lr=3e-5)
    # Create nonempty moments to verify A is a genuine optimizer continuation.
    loss=(bb(torch.ones(2,4))**2).sum();loss.backward();optimizer.step()
    checkpoint={'schema':balance.SCHEMA,'state':{'step':18450,
       'policy':{'source_levels':2,'levels':2,'operand_boundary':1024,
                 'start_rung_override':-1,'seed':123},
       'seed_state':{'rung':0,'source_sha256':'source_hash','frozen_sha256':'frozen_hash'}},
       'lora':zero.lora_state(bb),'optimizer':optimizer.state_dict()}
    torch.save(checkpoint,balance_dir/'best.pt')
    for i in range(12):
        fr._append(balance_dir/'glass_hard_candidates.jsonl',{
          'id':f'archived{i}','task':'algebra','level':i%2,
          'source':f'PROBLEM: solve 2*x={i}','target':f'PROBLEM: {i//2}'})
    args=diagnostic.parser().parse_args(['--stage1-checkpoint',str(s),
         '--text-checkpoint',str(t),'--device','cpu',
         '--balance-output',str(balance_dir),'--output',str(tmp_path/'new_rate'),
         '--archive-train','4','--eval-archive','3','--eval-per-cell','1',
         '--eval-frontier-per-cell','1','--generated-train-per-cell','1',
         '--max-steps','1','--budget-seconds','10','--check-seconds','100'])
    return args,bb,checkpoint


def test_preview_is_read_only(tmp_path,monkeypatch):
    args,bb,ck=_setup(tmp_path)
    args.stage='preview'
    monkeypatch.setattr(ladder,'_fresh_parent',lambda *_:pytest.fail('unexpected GPU load'))
    data=diagnostic.run(args)
    assert data['source_step']==18450
    assert data['branches']==('A_continue','B_stack')
    assert not Path(args.output).exists()


def test_manifest_resumption_and_policy_change(tmp_path):
    args,bb,ck=_setup(tmp_path)
    source,train,evals,manifest,_=diagnostic._prepare(args)
    assert len(train)>5 and len(evals)>5
    assert Path(args.output,'source_snapshot.pt').read_bytes()==Path(args.balance_output,'best.pt').read_bytes()
    assert {r['source'] for r in train}.isdisjoint({r['source'] for r in evals})
    s2,t2,e2,m2,_=diagnostic._prepare(args)
    assert t2==train and e2==evals
    args.extra_levels=3
    with pytest.raises(RuntimeError,match='LORA_STACK_RESUME_POLICY_DRIFT'):
        diagnostic._prepare(args)


def test_matched_two_way_end_to_end_without_parent_mutation(tmp_path,monkeypatch):
    args,bb,ck=_setup(tmp_path)
    p=Path(args.balance_output)/'best.pt';original=p.read_bytes()
    def fresh(_args):
        obj=TinyBB()
        obj.requires_grad_(False)
        return obj,None,'source_hash','frozen_hash',[],zero
    monkeypatch.setattr(ladder,'_fresh_parent',fresh)
    monkeypatch.setattr(diagnostic.tasks,'terminal_preflight',lambda:None)
    monkeypatch.setattr(rate,'_greedy',lambda model,tok,rows,rollout,glass=None,label='',cache=None:
        {row['id']: (int(model(torch.ones(1,4)).sum().detach())%3==int(row['id'][-1].isdigit()))
         for row in rows})
    monkeypatch.setattr(ladder,'_batch_loss',lambda model,tok,rows,dev,max_seq:
        ((model(torch.ones(2,4))-2)**2).mean())
    result=diagnostic.run(args)
    assert result['status']=='COMPLETE'
    assert set(result['results'])==set(diagnostic.BRANCHES)
    assert result['results']['A_continue']['inherited_optimizer']
    assert not result['results']['B_stack']['inherited_optimizer']
    assert not result['results']['A_continue']['fixed_first_lora']
    assert result['results']['B_stack']['fixed_first_lora']
    assert result['results']['A_continue']['trainable_parameters']==result['results']['B_stack']['trainable_parameters']
    assert 'lora1_reset' not in str(result)
    assert set(result['comparison'])=={'B_stack'}
    assert p.read_bytes()==original
    assert Path(args.output,'B_stack_best.pt').exists()
    # Second invocation should reuse completed results, not train more.
    again=diagnostic.run(args)
    assert again['results']==result['results']
    assert p.read_bytes()==original


def test_frozen_first_and_missing_optimizer_guards(tmp_path):
    args,bb,ck=_setup(tmp_path)
    source,train,eval_rows,manifest,_=diagnostic._prepare(args)
    source.pop('optimizer')
    with pytest.raises(RuntimeError,match='LORA_STACK_CONTINUATION_REQUIRES_OPTIMIZER'):
        diagnostic._branch('A_continue',bb,None,source,train,lambda i:(train[0],train[1]),
                          {},eval_rows,args,Path(args.output),SimpleNamespace())


def test_reset_branch_is_rejected(tmp_path):
    args,bb,checkpoint=_setup(tmp_path)
    source,train,eval_rows,manifest,_=diagnostic._prepare(args)
    with pytest.raises(ValueError,match='LORA_STACK_UNKNOWN_BRANCH'):
        diagnostic._branch('lora1_reset',bb,None,source,train,lambda i:(train[0],train[1]),
                           {},eval_rows,args,Path(args.output),SimpleNamespace())
