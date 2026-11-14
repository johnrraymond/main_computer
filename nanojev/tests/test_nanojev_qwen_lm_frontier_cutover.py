"""Fast CPU tests with frozen parent and fake LoRA for the actual trainer loop."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
import torch

import tools
# Tests packaged under tests/ run against production tools when installed;
# when run in the staging checkout prepend staged tool modules explicitly.
root=Path(__file__).resolve().parents[1]/'tools'
if root.is_dir() and str(root) not in tools.__path__:
    tools.__path__.insert(0,str(root))
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_frontier_tasks as tasks
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr


def test_documented_competencies_and_numeric_preflight():
    assert len(tasks.TASKS)==9
    assert len(tasks.task_order(2))==18
    assert tasks.terminal_preflight()['passed']==7
    assert ladder.numeric_terminal('PROBLEM: 0')==0
    assert ladder.numeric_terminal('PROBLEM: 10+(-10)') is None
    for task in tasks.TASKS:
        for level in (0,1):
            for index in range(14):
                a=tasks.make_case(task,level,index,seed=1,operand_boundary=1024,start_rung=3)
                b=tasks.make_case(task,level,index,seed=1,operand_boundary=1024,start_rung=3)
                assert a==b and a['source'].startswith('PROBLEM: ')
                assert a['target'].startswith('PROBLEM: ')
                assert not a['source'].startswith('ANSWER: ')
                assert not a['target'].startswith('ANSWER: ')
                if a['terminal_kind']=='int':
                    assert ladder.numeric_terminal(a['target']) is not None
                else:
                    assert ladder.numeric_terminal(a['target']) is None


def test_frontier_in_and_out_and_rung():
    rows=[tasks.make_case('signed_arithmetic',0,i,start_rung=4,operand_boundary=1024)
          for i in range(64)]
    mag=[r['metadata']['operand_magnitude'] for r in rows]
    assert min(mag)<1024<max(mag)
    assert min(r['metadata']['rung'] for r in rows)>=4
    assert any(r['terminal_kind']=='reduction' for r in rows)
    assert any(r['terminal_kind']=='int' for r in rows)==False # rung >=4


def test_exact_canonical_task_invariants():
    from fractions import Fraction
    for i in range(100):
        a=tasks.make_case('signed_arithmetic',0,i,start_rung=0)
        leaves=ladder.stage1._expr([1,-1]) # source formatting matches reference
        assert a['source'].startswith('PROBLEM: ')
        if a['terminal_kind']=='int':
            import ast
            expression=a['source'][9:]
            result=eval(compile(ast.parse(expression,mode='eval'),'<test>','eval'))
            assert result==int(a['target'][9:])
        b=tasks.make_case('fractions_units',1,i)
        assert b['target'].startswith('PROBLEM: ')


def test_plateau_not_accuracy():
    args=SimpleNamespace(loss_window=2,plateau_delta=0.001,
                         plateau_relative_delta=0.,min_steps_per_task=4,plateau_patience=2)
    state={'recent_loss':[],'best_reference_loss':None,'patience':0,'task_step':1}
    assert not fr._plateau_update(state,1.0,args)['plateau']
    state['task_step']=2
    assert not fr._plateau_update(state,0.7,args)['plateau']
    state['task_step']=3
    assert not fr._plateau_update(state,0.7,args)['plateau']
    state['task_step']=4
    assert not fr._plateau_update(state,0.7,args)['plateau']
    state['task_step']=5
    assert fr._plateau_update(state,0.7,args)['plateau']


def _args(tmp_path,source):
    d={'stage':'run','device':'cpu','stage1_checkpoint':str(tmp_path/'parent'/'stage.pt'),
       'text_checkpoint':str(tmp_path/'text'/'text.pt'),'seed_working':str(source),
       'output':str(tmp_path/'frontier'),'operand_boundary':1024,'levels':1,
       'start_rung_override':-1,'batch_size':3,'replay_per_batch':1,
       'lr':0.001,'max_seq':192,'max_tokens':256,'max_new_tokens':16,
       'min_steps_per_task':2,'max_steps_per_task':0,'check_every':1,
       'plateau_patience':1,'loss_window':1,'plateau_delta':0.001,
       'plateau_relative_delta':0.001,'reference_cases':2,'greedy_cases':1,
       'save_every':1,'save_seconds':60.0,'log_every':100,'max_total_steps':2,'seed':123,
       'baseline':None,'backbone':None,'activation':None,'format':None,
       'allow_model_download':False}
    return SimpleNamespace(**d)


def test_real_runner_snapshot_errors_resume_and_unmodified_parent(tmp_path,monkeypatch):
    cfg={'schema':ladder.SCHEMA,'terminal_mode':'numeric'}
    source=tmp_path/'seed'/'working.pt';source.parent.mkdir(parents=True)
    state={'config':cfg,'config_hash':ladder._hash(cfg),'rung':3,'radius_index':2,
           'source_sha256':'src123','frozen_sha256':'fro123'}
    torch.save({'schema':ladder.SCHEMA,'state':state,'lora':{'test_lora':torch.tensor(0.)}},source)
    orig=fr._sha256(source)
    args=_args(tmp_path,source)
    for p in (args.stage1_checkpoint,args.text_checkpoint):
        Path(p).parent.mkdir(parents=True,exist_ok=True);Path(p).write_bytes(b'parent')
    class FakeBackbone(torch.nn.Module):
        def __init__(self):super().__init__();self.test_lora=torch.nn.Parameter(torch.tensor(0.))
    class FakeZero:
        @staticmethod
        def lora_named_parameters(bb):return [('test_lora',bb.test_lora)]
        @staticmethod
        def lora_state(bb):return {'test_lora':bb.test_lora.detach().clone()}
        @staticmethod
        def restore_lora(bb,lora):bb.test_lora.data.copy_(lora['test_lora'])
    def fake_parent(_):return FakeBackbone(),object(),'src123','fro123',[],FakeZero
    def fake_loss(bb,tok,rows,device,max_seq):
        return (bb.test_lora*0 + 1.0 + 0.001*len(rows))
    monkeypatch.setattr(ladder,'_fresh_parent',fake_parent)
    monkeypatch.setattr(ladder,'_batch_loss',fake_loss)
    monkeypatch.setattr(fr,'_greedy_one',lambda bb,tok,row,args:'PROBLEM: WRONG')
    monkeypatch.setattr(fr,'_event',lambda *x:None)
    one=fr.run(args)
    assert one['status']=='BUDGET_LIMIT' and one['step']==2
    assert source.is_file() and fr._sha256(source)==orig
    assert (Path(args.output)/'seed_snapshot.pt').is_file()
    assert (Path(args.output)/'discovered_errors.jsonl').is_file()
    assert (Path(args.output)/'task_checkpoints'/'signed_arithmetic_L0_best.pt').is_file()
    # Source ladder continues changing, but local seed stays immutable.
    torch.save({'corrupted':'new unrelated source'},source)
    args.max_total_steps=4
    two=fr.run(args)
    assert two['step']==4
    assert two['task_index']>=2
    records=[json.loads(x) for x in (Path(args.output)/'discovered_errors.jsonl').read_text().splitlines()]
    assert len(records)>=3
    assert all('source' in x and 'target' in x and 'greedy' in x for x in records)
    bad=_args(tmp_path,source);bad.lr=0.777
    with pytest.raises(RuntimeError,match='POLICY_DRIFT'):fr._prepare(bad)


def test_separate_output_refuses_overlaps_and_no_glass_dependency(tmp_path):
    import ast
    mod=Path(fr.__file__).read_text()
    assert 'glass' in mod.lower() # explicitly documents exclusion
    assert 'import nanojev_qwen_lm_deformation' not in mod
    args=_args(tmp_path,tmp_path/'seed'/'working.pt')
    args.output=str(tmp_path/'seed')
    with pytest.raises(RuntimeError,match='OUTPUT_OVERLAPS'):fr._validate(args)


def test_task_budget_never_fakes_plateau(tmp_path,monkeypatch):
    cfg={'schema':ladder.SCHEMA,'terminal_mode':'numeric'}
    src=tmp_path/'seed'/'working.pt';src.parent.mkdir()
    state={'config':cfg,'config_hash':ladder._hash(cfg),'rung':2,'radius_index':1,
           'source_sha256':'source','frozen_sha256':'frozen'}
    torch.save({'schema':ladder.SCHEMA,'state':state,'lora':{'test_lora':torch.tensor(1.)}},src)
    args=_args(tmp_path,src)
    args.min_steps_per_task=1;args.max_steps_per_task=2
    args.max_total_steps=0;args.plateau_patience=100
    for path in (args.stage1_checkpoint,args.text_checkpoint):
        p=Path(path);p.parent.mkdir(parents=True);p.touch()
    class FakeBackbone(torch.nn.Module):
        def __init__(self):super().__init__();self.test_lora=torch.nn.Parameter(torch.tensor(0.))
    class FakeZero:
        @staticmethod
        def lora_named_parameters(bb):return [('test_lora',bb.test_lora)]
        @staticmethod
        def lora_state(bb):return {'test_lora':bb.test_lora.detach().clone()}
        @staticmethod
        def restore_lora(bb,lora):bb.test_lora.data.copy_(lora['test_lora'])
    monkeypatch.setattr(ladder,'_fresh_parent',lambda a:(FakeBackbone(),None,'source','frozen',[],FakeZero))
    monkeypatch.setattr(ladder,'_batch_loss',lambda bb,tok,rows,device,max_seq:
                        (bb.test_lora-10)**2)
    monkeypatch.setattr(fr,'_greedy_one',lambda *x:'PROBLEM: WRONG')
    monkeypatch.setattr(fr,'_event',lambda *x:None)
    first=fr.run(args)
    assert first['status']=='TASK_BUDGET_LIMIT'
    assert first['task_index']==0 and first['task_step']==2
    assert not first['task_results']
    args.max_steps_per_task=3
    resumed=fr.run(args)
    assert resumed['status']=='TASK_BUDGET_LIMIT'
    assert resumed['step']==3 and resumed['task_index']==0


def test_low_loss_relative_plateau_and_raw_best():
    args=SimpleNamespace(loss_window=1,plateau_delta=0.002,
                         plateau_relative_delta=0.001,min_steps_per_task=10,
                         plateau_patience=2)
    state={'recent_loss':[],'best_reference_loss':None,'patience':0,'task_step':10}
    fr._plateau_update(state,0.0018105,args)
    first=fr._plateau_update(state,0.0018000,args)
    assert first['improved'] and state['patience']==0  # <0.002 absolute drop
    strong=fr._plateau_update(state,0.0001359,args)
    assert strong['improved'] and state['patience']==0
    assert strong['best']==pytest.approx(0.0001359)
    assert not fr._plateau_update(state,0.00013589,args)['plateau']
    assert fr._plateau_update(state,0.00013589,args)['plateau']


def _tiny_runtime(tmp_path,monkeypatch):
    cfg={'schema':ladder.SCHEMA,'terminal_mode':'numeric'}
    src=tmp_path/'seed'/'working.pt';src.parent.mkdir(parents=True)
    state={'config':cfg,'config_hash':ladder._hash(cfg),'rung':2,'radius_index':1,
           'source_sha256':'source','frozen_sha256':'frozen'}
    torch.save({'schema':ladder.SCHEMA,'state':state,
                'lora':{'test_lora':torch.tensor(1.)}},src)
    args=_args(tmp_path,src)
    for path in (args.stage1_checkpoint,args.text_checkpoint):
        p=Path(path);p.parent.mkdir(parents=True);p.touch()
    class FakeBackbone(torch.nn.Module):
        def __init__(self):
            super().__init__();self.test_lora=torch.nn.Parameter(torch.tensor(0.))
    class FakeZero:
        @staticmethod
        def lora_named_parameters(bb):return [('test_lora',bb.test_lora)]
        @staticmethod
        def lora_state(bb):return {'test_lora':bb.test_lora.detach().clone()}
        @staticmethod
        def restore_lora(bb,lora):bb.test_lora.data.copy_(lora['test_lora'])
    monkeypatch.setattr(ladder,'_fresh_parent',lambda a:
                        (FakeBackbone(),None,'source','frozen',[],FakeZero))
    monkeypatch.setattr(fr,'_greedy_one',lambda *x:'PROBLEM: WRONG')
    monkeypatch.setattr(fr,'_event',lambda *x:None)
    return args


def test_atomic_snapshots_backup_and_recover_latest(tmp_path,monkeypatch):
    args=_tiny_runtime(tmp_path,monkeypatch)
    args.check_every=100
    args.save_every=1
    args.max_total_steps=4
    monkeypatch.setattr(ladder,'_batch_loss',lambda bb,tok,rows,device,max_seq:
                        (bb.test_lora-10)**2)
    first=fr.run(args)
    assert first['step']==4
    out=Path(args.output)
    assert torch.load(out/'working.pt',weights_only=True)['state']['step']==4
    assert torch.load(out/'previous.pt',weights_only=True)['state']['step'] in (3,4)
    # Corrupt the primary: resume must automatically recover from previous.
    (out/'working.pt').write_bytes(b'half written torch zip')
    recovered=fr._prepare(args)[0]
    assert recovered['step']>=3
    assert (out/'working.corrupt.pt').read_bytes()==b'half written torch zip'
    # Operational save frequency and logging may change without invalidating
    # optimizer, data order, or seed snapshot lineage.
    args.save_every=7
    args.log_every=2
    state=fr._prepare(args)[0]
    assert state['step']==recovered['step']
    # A genuine training-policy change is still rejected.
    args.lr=0.2
    with pytest.raises(RuntimeError,match='POLICY_DRIFT'):fr._prepare(args)


def test_graceful_stop_commits_latest_step_and_resumes(tmp_path,monkeypatch):
    from contextlib import contextmanager
    args=_tiny_runtime(tmp_path,monkeypatch)
    args.check_every=100;args.save_every=50;args.max_total_steps=0
    stop={'requested':False,'signal':'SIGINT'}
    @contextmanager
    def fake_signals():
        yield stop
    monkeypatch.setattr(fr,'_stop_requests',fake_signals)
    count={'train':0}
    def train_loss(bb,tok,rows,device,max_seq):
        count['train']+=1
        if count['train']==3:stop['requested']=True
        return (bb.test_lora-10)**2
    monkeypatch.setattr(ladder,'_batch_loss',train_loss)
    first=fr.run(args)
    assert first['status']=='PAUSED' and first['step']==3
    out=Path(args.output)
    ck=torch.load(out/'working.pt',map_location='cpu',weights_only=True)
    assert ck['state']['step']==3
    assert 'optimizer' in ck and ck['optimizer']['state']
    assert ck['python_rng'] is not None and ck['torch_rng'] is not None
    stop['requested']=False
    args.max_total_steps=5
    second=fr.run(args)
    assert second['step']==5 and second['status']=='BUDGET_LIMIT'


def test_pending_evaluation_recovery_deduplicates_errors(tmp_path,monkeypatch):
    args=_tiny_runtime(tmp_path,monkeypatch)
    args.check_every=2;args.greedy_cases=2;args.max_total_steps=2
    monkeypatch.setattr(ladder,'_batch_loss',lambda bb,tok,rows,device,max_seq:
                        (bb.test_lora-10)**2)
    original=fr._greedy_one
    calls={'n':0}
    def crash_after_first(*a):
        calls['n']+=1
        if calls['n']==2:raise RuntimeError('artificial kill mid audit')
        return original(*a)
    monkeypatch.setattr(fr,'_greedy_one',crash_after_first)
    with pytest.raises(RuntimeError,match='artificial kill'):fr.run(args)
    out=Path(args.output)
    ck=torch.load(out/'working.pt',map_location='cpu',weights_only=True)
    assert ck['state']['step']==2 and ck['state']['pending_check']
    before=(out/'discovered_errors.jsonl').read_text().splitlines()
    assert len(before)==1
    monkeypatch.setattr(fr,'_greedy_one',original)
    final=fr.run(args)
    assert final['step']==2 and not final['pending_check']
    after=(out/'discovered_errors.jsonl').read_text().splitlines()
    assert len(after)==2
    assert final['errors_seen']==2
    assert len({(json.loads(x)['step'],json.loads(x)['case_id']) for x in after})==2
