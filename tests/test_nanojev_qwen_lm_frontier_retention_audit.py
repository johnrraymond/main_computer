"""Read-only cross-task audit tests: snapshot, real matching, safe output, failure."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
import torch

import tools
root=Path(__file__).resolve().parents[1]/'tools'
compat=Path(__file__).resolve().parents[2]/'frontier_checkpoint_fix'/'tools'
for path in (compat,root):
    if path.is_dir() and str(path) not in tools.__path__:tools.__path__.insert(0,str(path))
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_radius_ladder_frontier as frontier
from tools import nanojev_qwen_lm_frontier_retention_audit as audit


def _fixture(tmp_path):
    root=tmp_path/'training';root.mkdir()
    p1=tmp_path/'parents'/'stage'/'best.pt'
    p2=tmp_path/'parents'/'text'/'best.pt'
    for path in (p1,p2):path.parent.mkdir(parents=True);path.write_bytes(b'parent')
    seed_state={'config':{'terminal_mode':'numeric'},'config_hash':None,
               'rung':3,'radius_index':0,'source_sha256':'source1',
               'frozen_sha256':'frozen1'}
    seed_state['config_hash']=ladder._hash(seed_state['config'])
    seed={'schema':ladder.SCHEMA,'state':seed_state,'lora':{'x':torch.tensor(0.)}}
    torch.save(seed,root/'seed_snapshot.pt')
    policy={'levels':2,'seed':12345,'operand_boundary':1024,'start_rung_override':-1}
    state={'policy':policy,'step':120,'task_index':3,'status':'TRAINING',
           'seed_sha256':audit._sha(root/'seed_snapshot.pt'),
           'seed_state':seed_state}
    torch.save({'schema':frontier.SCHEMA,'state':dict(state),
                'lora':{'x':torch.tensor(1.)}},root/'working.pt')
    best=root/'task_checkpoints';best.mkdir()
    for task in ('signed_arithmetic','place_value','arithmetic_operators','fractions_units'):
        torch.save({'schema':frontier.SCHEMA,'lora':{'x':torch.tensor(0.5)},
                    'task':task,'level':0,'step':100,'reference_loss':0.05},
                    best/(task+'_L0_best.pt'))
    prior=dict(state);prior['step']=110
    torch.save({'schema':frontier.SCHEMA,'state':prior,
                'lora':{'x':torch.tensor(0.5)}},root/'previous.pt')
    return SimpleNamespace(frontier_output=str(root),stage1_checkpoint=str(p1),
        text_checkpoint=str(p2),report_root=str(tmp_path/'reports'),
        scope='visited',fixed_cases=4,novel_cases=4,novel_seed=7,
        max_tokens=256,max_seq=192,max_new_tokens=16,device='cpu',
        progress_every=100,compare_previous=True,compare_task_best=True,allow_model_download=False)


def test_decks_stable_fixed_and_novel_vary():
    state={'policy':{'levels':2,'seed':202,'operand_boundary':1024,'start_rung_override':-1},
           'seed_state':{'rung':4},'task_index':2,'status':'TRAINING'}
    rows,cells=audit._test_decks(state,'visited',8,8,987)
    assert len(cells)==3 and len(rows)==48
    assert rows==audit._test_decks(state,'visited',8,8,987)[0]
    later=audit._test_decks(state,'visited',8,8,990)[0]
    assert [x['case'] for x in rows if x['kind']=='fixed']==[
           x['case'] for x in later if x['kind']=='fixed']
    assert any(x['case']['source']!=y['case']['source']
               for x,y in zip([r for r in rows if r['kind']=='novel'],
                              [r for r in later if r['kind']=='novel']))
    assert all(x['case']['source'].startswith('PROBLEM: ') for x in rows)
    assert {c['case']['task'] for c in rows}=={'signed_arithmetic','place_value','arithmetic_operators'}


def test_readonly_compare_and_report(tmp_path,monkeypatch):
    args=_fixture(tmp_path)
    training=Path(args.frontier_output)
    original={p.name:audit._sha(p) for p in training.iterdir() if p.is_file()}
    class FakeBb(torch.nn.Module):
        def __init__(self):
            super().__init__();self.x=torch.nn.Parameter(torch.tensor(0.))
    class FakeZero:
        @staticmethod
        def restore_lora(bb,weights):bb.x.data.copy_(weights['x'])
    monkeypatch.setattr(ladder,'_fresh_parent',lambda *a:
        (FakeBb(),object(),'source1','frozen1',[],FakeZero))
    def predict(bb,tok,case,args):
        i=int(case['id'].rsplit(':',1)[1])
        w=float(bb.x)
        if w==0.0:
            good=i%4==0
        elif w==0.5:
            good=i%4 in (0,1)
        else:
            good=i%4 in (1,2,3) # fixes 3, regresses 1 in each quartet
        return case['target'] if good else 'PROBLEM: deliberately wrong'
    monkeypatch.setattr(frontier,'_greedy_one',predict)
    result=audit.audit(args)
    assert result['status']=='COMPLETE'
    assert result['summary']['all']=={
        'tested':32,'seed_correct':8,'latest_correct':24,
        'seed_accuracy':0.25,'latest_accuracy':0.75,'net_gained':16,
        'acquired':24,'regressed':8,'retained':0,'still_wrong':0}
    assert result['previous_to_latest']['regressed']==8
    assert result['task_best_to_latest']['overall']['regressed']==8
    assert not result['missing_task_best']
    path=Path(result['report_path'])
    assert len((path/'cases.jsonl').read_text().splitlines())==32
    assert len((path/'results.jsonl').read_text().splitlines())==32
    assert (path/'snapshots'/'latest.pt').exists()
    assert all(audit._sha(training/name)==value for name,value in original.items())
    # Multiple reports never overwrite; stable monitoring deck reused.
    result2=audit.audit(args)
    assert result2['report_path']!=result['report_path']
    assert (Path(result2['report_path'])/'cases.jsonl').read_text()==(path/'cases.jsonl').read_text()


def test_guardrails_and_lineage(tmp_path):
    args=_fixture(tmp_path)
    args.report_root=args.frontier_output+'/reports'
    with pytest.raises(ValueError,match='OVERLAPS'):audit._validate_paths(args)
    args.report_root=str(tmp_path/'reports')
    path=Path(args.frontier_output)/'working.pt'
    obj=torch.load(path,weights_only=True)
    obj['state']['seed_sha256']='falsified'
    torch.save(obj,path)
    reports=Path(args.report_root); reports.mkdir()
    dest=reports/'bad';dest.mkdir();(dest/'snapshots').mkdir()
    with pytest.raises(RuntimeError,match='SEED_MISMATCH'):
        audit._checkpoint_set(Path(args.frontier_output),dest)
    assert path.exists()  # never modified on lineage failure


def test_visited_and_all_scope_after_completion(tmp_path):
    args=_fixture(tmp_path)
    state=torch.load(Path(args.frontier_output)/'working.pt',weights_only=True)['state']
    assert len(audit._cells(state,'visited'))==4
    assert len(audit._cells(state,'all'))==18
    state['task_index']=18;state['status']='COMPLETE'
    assert len(audit._cells(state,'visited'))==18
    assert audit._classify(True,False)=='regressed'
    assert audit._classify(False,True)=='acquired'
    assert audit._classify(True,True)=='retained'
    assert audit._classify(False,False)=='still_wrong'
