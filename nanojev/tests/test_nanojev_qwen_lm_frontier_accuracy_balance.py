"""Raw accuracy-balance: paired gains/losses and independent optimizer lineage."""
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
import torch

from tools import nanojev_qwen_lm_frontier_accuracy_balance as balance
from tools import nanojev_qwen_lm_frontier_mixed_pass as mixed
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_competency_ledger as ledger


class BB(torch.nn.Module):
    def __init__(self):super().__init__();self.test_lora=torch.nn.Parameter(torch.tensor(0.))


class Zero:
    @staticmethod
    def lora_named_parameters(bb):return [('test_lora',bb.test_lora)]
    @staticmethod
    def lora_state(bb):return {'test_lora':bb.test_lora.detach().clone()}
    @staticmethod
    def restore_lora(bb,state):bb.test_lora.data.copy_(state['test_lora'])
    @staticmethod
    def resolve_amp_dtype(*a):return 'cpu'


def setup(tmp_path,monkeypatch,**options):
    monkeypatch.setattr(ladder,'_fresh_parent',lambda a:(BB(),None,'source_hash','frozen_hash',[],Zero))
    monkeypatch.setattr(ladder,'_batch_loss',lambda bb,tok,rows,device,max_seq:
                        (bb.test_lora-2.)**2+.00001*len(rows))
    monkeypatch.setattr(fr,'_greedy_one',lambda bb,tok,row,args:'WRONG')
    monkeypatch.setattr(fr,'_event',lambda *a:None)
    parents=tmp_path/'upstream'
    one=parents/'stage'/'best.pt';two=parents/'text'/'best.pt'
    for path in (one,two):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fake parent')
    samples=tmp_path/'mixed';samples.mkdir()
    param=torch.nn.Parameter(torch.tensor(0.))
    opt=torch.optim.AdamW([param],lr=.1)
    state={'policy':{'seed':91733,'source_levels':2,'operand_boundary':1024,
                      'start_rung_override':-1},
           'step':17950,'best_step':17950,'seed_state':{'rung':0,
                'source_sha256':'source_hash','frozen_sha256':'frozen_hash'}}
    ck={'schema':mixed.SCHEMA,'state':state,'lora':{'test_lora':torch.tensor(0.)},
        'optimizer':opt.state_dict()}
    torch.save(ck,samples/'glass_ready.pt')
    (samples/'glass_readiness.json').write_text(json.dumps({'status':'MIXED_LOSS_PLATEAU'}))
    fr._append(samples/'glass_hard_candidates.jsonl',{
        'case_key':ledger._key({'source':'solve 2*x=14','target':'7'}),
        'source':'solve 2*x=14','target':'7','task':'algebra','level':0,
        'case_id':'seed-hard','kind':'historical_unrechecked'})
    d={'output':str(tmp_path/'systematic'),'stage1_checkpoint':str(one),
       'text_checkpoint':str(two),'device':'cpu',
       'balance_batch_size':4,'balance_lr':.1,'balance_check_every':1,
       'balance_min_steps':2,'balance_max_steps':0,'balance_patience':2,
       'balance_window':2,'balance_anchors_per_cell':1,
       'balance_fresh_per_check':2,'balance_replay_per_check':2,
       'balance_expansion_every':1,'balance_max_extra_levels':2,
       'max_seq':192,'max_tokens':256,'max_new_tokens':16}
    d.update(options)
    return SimpleNamespace(**d),samples,tmp_path/'balance'


def test_balancing_uses_identical_paired_problems_and_stops(tmp_path,monkeypatch):
    args,samples,out=setup(tmp_path,monkeypatch)
    result=balance.run(args,samples,out)
    assert result['status']=='BALANCED'
    assert result['phase_step']>=3
    assert result['last_check']['net']==0
    assert result['last_check']['paired_tested']>0
    hist=ledger._read(out/'competency_checks.jsonl')
    assert len(hist)==result['check_count']
    assert hist[0]['paired']['paired_tested']==0
    assert len(hist[-1]['observations'])>=len(hist[0]['observations'])-2
    assert (out/'glass_ready.pt').is_file()
    assert (out/'best.pt').read_bytes()==(out/'glass_ready.pt').read_bytes()
    assert json.loads((out/'glass_readiness.json').read_text())['status']=='RAW_ACCURACY_BALANCED'
    assert (samples/'glass_ready.pt').is_file()
    assert balance.run(args,samples,out)['phase_step']==result['phase_step']


def test_resume_from_budget_without_reset(tmp_path,monkeypatch):
    args,samples,out=setup(tmp_path,monkeypatch,balance_max_steps=2,
                            balance_min_steps=200)
    first=balance.run(args,samples,out)
    assert first['status']=='BUDGET_LIMIT' and first['phase_step']==2
    second=balance.run(args,samples,out)
    assert second['status']=='BUDGET_LIMIT' and second['phase_step']==2
    assert len(ledger._read(out/'competency_checks.jsonl'))==3
    assert len(ledger._read(out/'glass_hard_candidates.jsonl'))>=1


def test_loss_parent_unchanged_and_incompatible_policy_rejected(tmp_path,monkeypatch):
    args,samples,out=setup(tmp_path,monkeypatch,balance_max_steps=1,balance_min_steps=200)
    source=(samples/'glass_ready.pt').read_bytes()
    balance.run(args,samples,out)
    assert (samples/'glass_ready.pt').read_bytes()==source
    args.balance_lr=.001
    with pytest.raises(RuntimeError,match='BALANCE_RESUME_POLICY_DRIFT'):
        balance.run(args,samples,out)


def test_stopping_requires_frontier_growth_and_nonpositive_net():
    make=lambda g,l:{'paired':{'paired_tested':20,'gained':g,'lost':l,'net':g-l}}
    flat=[make(0,0)]*7
    assert not balance._balance_status(flat,4,4,0,1000,7,4)['balanced']
    assert balance._balance_status(flat+[make(0,0)]*5,4,4,0,1000,12,4)['balanced']
    positive=[make(4,1)]*12
    assert not balance._balance_status(positive,4,4,0,1000,12,4)['balanced']
    mixed_hist=[make(2,2)]*12
    assert balance._balance_status(mixed_hist,4,4,0,1000,12,4)['balanced']


def test_stage_requires_loss_plateau(tmp_path,monkeypatch):
    args,samples,out=setup(tmp_path,monkeypatch)
    (samples/'glass_readiness.json').write_text(json.dumps({'status':'TRAINING'}))
    with pytest.raises(RuntimeError,match='BALANCE_LOSS_PARENT_NOT_PLATEAU'):
        balance._prepare(args,samples,out)


def test_glass_handoff_uses_balance_checkpoint_and_rejects_old_loss_only(tmp_path,monkeypatch):
    from tools import nanojev_qwen_lm_frontier_rank1_glasses as glass
    args,samples,out=setup(tmp_path,monkeypatch)
    with pytest.raises(RuntimeError,match='FRONTIER_GLASSES_REQUIRE_COMPLETED_ACCURACY_BALANCE'):
        args.stage='run';args.balance_output=str(out);args.samples_output=str(samples)
        args.glasses_output=None;args.glass_scheme='easy'
        fr._dispatch_glasses(args)
    args.stage='run'
    state=balance.run(args,samples,out)
    assert state['status']=='BALANCED'
    snap,ready=glass._readiness(out)
    assert ready['status']=='RAW_ACCURACY_BALANCED'
    assert glass._verify_backbone(snap)['schema']==balance.SCHEMA
