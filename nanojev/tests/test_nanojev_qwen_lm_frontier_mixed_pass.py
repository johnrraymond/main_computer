"""Offline CPU tests for the real phase-two runner and its safety invariants."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import pytest
import torch

import tools
root=Path(__file__).resolve().parents[1]/'tools'
compat=Path(__file__).resolve().parents[2]/'frontier_checkpoint_fix'/'tools'
for d in (compat,root):
    if d.is_dir() and str(d) not in tools.__path__:
        tools.__path__.insert(0,str(d))
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_frontier_tasks as tasks
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
from tools import nanojev_qwen_lm_frontier_mixed_pass as mixed


def args_for(tmp_path,*,source='completed',**changes):
    root=tmp_path/source
    root.mkdir(exist_ok=True)
    parents=tmp_path/'parents'
    one=parents/'stage'/'best.pt';two=parents/'text'/'best.pt'
    for p in (one,two):p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'parent')
    state={'policy':{'levels':1,'seed':1,'operand_boundary':1024,
                     'start_rung_override':-1},
           'status':'COMPLETE','pending_check':False,
           'step':125,'task_index':len(tasks.TASKS),
           'seed_sha256':'seed_hash','seed_state':{'rung':2,
               'source_sha256':'source_hash','frozen_sha256':'frozen_hash'}}
    parent_param=torch.nn.Parameter(torch.tensor(0.))
    optim=torch.optim.AdamW([parent_param],lr=.1)
    parent={'schema':fr.SCHEMA,'state':state,
            'lora':{'test_lora':torch.tensor(0.)},
            'optimizer':optim.state_dict()}
    torch.save(parent,root/'final.pt')
    d={'stage':'run','device':'cpu','frontier_output':str(root),
       'stage1_checkpoint':str(one),'text_checkpoint':str(two),
       'output':str(tmp_path/'mixed'),'seed':123,'batch_size':4,'lr':0.1,
       'max_seq':192,'max_tokens':256,'max_new_tokens':16,
       'check_every':2,'reference_per_cell':16,'greedy_cases':1,
       'min_steps':5,'plateau_patience':3,'loss_window':1,
       'plateau_delta':1e-5,'plateau_relative_delta':.001,
       'save_every':2,'save_seconds':0,'log_every':100,
       'max_steps':6,'baseline':None,'backbone':None,
       'activation':None,'format':None,'allow_model_download':False}
    d.update(changes)
    return SimpleNamespace(**d)


class BB(torch.nn.Module):
    def __init__(self):
        super().__init__();self.test_lora=torch.nn.Parameter(torch.tensor(0.))


class Zero:
    @staticmethod
    def lora_named_parameters(bb):return [('test_lora',bb.test_lora)]
    @staticmethod
    def lora_state(bb):return {'test_lora':bb.test_lora.detach().clone()}
    @staticmethod
    def restore_lora(bb,state):bb.test_lora.data.copy_(state['test_lora'])
    @staticmethod
    def resolve_amp_dtype(*a):return 'cpu'


def fake_system(monkeypatch):
    monkeypatch.setattr(ladder,'_fresh_parent',lambda a:(BB(),None,'source_hash',
                                                      'frozen_hash',[],Zero))
    monkeypatch.setattr(ladder,'_batch_loss',lambda bb,tok,rows,device,max_seq:
                       (bb.test_lora-2.0)**2 + 0.001*len(rows))
    monkeypatch.setattr(fr,'_greedy_one',lambda bb,tok,row,args:'NOT A PROBLEM')
    monkeypatch.setattr(fr,'_event',lambda *a:None)


def test_balanced_randomized_sampling_reproducible():
    state={'policy':{'seed':123,'source_levels':2,'operand_boundary':1024,
                     'start_rung_override':-1},'seed_state':{'rung':2}}
    cells=tasks.task_order(2)
    first=[mixed._sample_spec(state,n) for n in range(18)]
    assert len({(t,l) for t,l,_ in first})==18
    assert first==[mixed._sample_spec(state,n) for n in range(18)]
    assert [(t,l) for t,l,_ in first]!=cells
    for epoch in range(6):
        sample=[mixed._sample_spec(state,n) for n in range(18*epoch,18*(epoch+1))]
        assert set((t,l) for t,l,_ in sample)==set(cells)
        assert all(i>=30_000_000 for _,_,i in sample)
    logic=[next(idx for task,lev,idx in (mixed._sample_spec(state,n)
               for n in range(18*epoch,18*(epoch+1))) if task=='logic_graphs' and lev==0)
           for epoch in range(60)]
    assert len(set(idx%60 for idx in logic))==60
    values=[mixed._make(state,'logic_graphs',0,i)['source'] for i in logic]
    assert 'PROBLEM: reachable A>C in A>B,C>B' in values
    assert 'PROBLEM: reachable A>C in A>B,B>C' in values


def test_fixed_reference_remains_stable_and_separate():
    state={'policy':{'seed':123,'source_levels':2,'operand_boundary':1024,
                     'start_rung_override':-1},'seed_state':{'rung':2}}
    args=SimpleNamespace(seed=123,reference_per_cell=16)
    a=mixed._reference(state,args)
    assert a==mixed._reference(state,args)
    assert len(a)==18 and all(len(v)==16 for v in a.values())
    reach=[r['source'] for r in a['logic_graphs:L0'] if 'reachable' in r['source']]
    assert 'PROBLEM: reachable A>C in A>B,B>C' in reach
    assert 'PROBLEM: reachable A>C in A>B,C>B' in reach
    place=[r['source'] for r in a['place_value:L0']]
    assert all(any(name in source for source in place)
               for name in ('hundred','thousand','million','billion','trillion'))
    assert not ({r['id'] for group in a.values() for r in group} &
                {mixed._make(state,t,l,i)['id'] for t,l,i in
                 (mixed._sample_spec(state,x) for x in range(100))})


def test_real_checkpoint_isolation_resume_and_policy_drift(tmp_path,monkeypatch):
    fake_system(monkeypatch)
    args=args_for(tmp_path)
    original=fr._sha256(Path(args.frontier_output)/'final.pt')
    first=mixed.run(args)
    assert first['status']=='BUDGET_LIMIT' and first['phase_step']==6
    assert first['sample_cursor']==24 and first['step']==131
    assert fr._sha256(Path(args.frontier_output)/'final.pt')==original
    assert (Path(args.output)/'parent_snapshot.pt').exists()
    assert (Path(args.output)/'working.pt').exists()
    assert (Path(args.output)/'mixed_history.jsonl').exists()
    assert (Path(args.output)/'mixed_discovered_errors.jsonl').exists()
    # Alter the source AFTER the pass copied its immutable parent.
    (Path(args.frontier_output)/'final.pt').write_bytes(b'changed on disk')
    args.max_steps=8
    second=mixed.run(args)
    assert second['phase_step']==8
    assert second['sample_cursor']==32
    assert (Path(args.output)/'best.pt').exists()
    args.lr=.077
    with pytest.raises(RuntimeError,match='POLICY_DRIFT'):
        mixed._prepare(args)


def test_plateau_marks_best_snapshot_for_glasses(tmp_path,monkeypatch):
    fake_system(monkeypatch)
    args=args_for(tmp_path,check_every=1,min_steps=3,
                  plateau_patience=2,loss_window=1,max_steps=10)
    monkeypatch.setattr(mixed,'_full_reference_loss',lambda *a:(0.1,{
        key:0.1 for key in a[4]}))
    result=mixed.run(args)
    assert result['status']=='PLATEAU'
    assert result['phase_step']==9
    assert result['best_phase_step']==1
    root=Path(args.output)
    assert (root/'glass_ready.pt').exists()
    assert (root/'best.pt').read_bytes()==(root/'glass_ready.pt').read_bytes()
    manifest=json.loads((root/'glass_readiness.json').read_text())
    assert manifest['no_glass_training'] is True
    assert manifest['status']=='MIXED_LOSS_PLATEAU'
    assert manifest['best_phase_step']==1
    # Idempotent terminal run; does not perform new training.
    second=mixed.run(args)
    assert second['phase_step']==9


def test_incomplete_parent_fails_and_never_mutates_source(tmp_path,monkeypatch):
    args=args_for(tmp_path)
    ck=torch.load(Path(args.frontier_output)/'final.pt',weights_only=True)
    ck['state']['status']='TRAINING'
    torch.save(ck,Path(args.frontier_output)/'final.pt')
    with pytest.raises(RuntimeError,match='NOT_COMPLETED'):
        mixed._prepare(args)
    assert not (Path(args.output)/'working.pt').exists()


def test_refuses_in_place_mixed_pass(tmp_path):
    args=args_for(tmp_path)
    args.output=args.frontier_output
    with pytest.raises(RuntimeError,match='OUTPUT_OVERLAPS'):
        mixed._prepare(args)


def test_checkpoint_recovers_previous_without_retraining_parent(tmp_path,monkeypatch):
    fake_system(monkeypatch)
    args=args_for(tmp_path)
    mixed.run(args)
    root=Path(args.output)
    previous=mixed._load(root/'previous.pt')
    (root/'working.pt').write_bytes(b'broken')
    restored=mixed._load_working(root)
    assert restored['state']['step']==previous['state']['step']
    assert (root/'working.corrupt.pt').read_bytes()==b'broken'
    assert (root/'working.pt').is_file()
