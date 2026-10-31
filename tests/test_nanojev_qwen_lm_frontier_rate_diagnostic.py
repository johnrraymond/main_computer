"""Matched GPU time diagnostic: cohorts, exact transitions, and source isolation."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from tools import nanojev_qwen_lm_frontier_rate_diagnostic as rate
from tools import nanojev_qwen_lm_frontier_accuracy_balance as balance
from tools import nanojev_qwen_lm_frontier_rank1_glasses as glass
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
from tools import nanojev_qwen_zero_line_backbone as zero


def test_correctness_transitions_and_rate():
    rows=[{'id':'a','stratum':'archive_eval'},
          {'id':'b','stratum':'fresh_original'},
          {'id':'c','stratum':'fresh_expanded'},
          {'id':'d','stratum':'fresh_expanded'}]
    before={'a':False,'b':True,'c':False,'d':True}
    after={'a':True,'b':False,'c':True,'d':True}
    scores=rate._with_rate(rate._score(before,after,rows),10,5)
    assert scores['all']['gained']==2 and scores['all']['lost']==1
    assert scores['all']['net']==1
    assert scores['fresh']['net']==0
    assert scores['fresh_expanded']['net']==1
    assert scores['all']['net_per_gpu_hour_reserved']==360
    assert scores['all']['net_per_100_steps']==20


def _args(tmp_path,**changes):
    args=rate.parser().parse_args(['--device','cpu',
                '--stage1-checkpoint',str(tmp_path/'stage1.pt'),
                '--text-checkpoint',str(tmp_path/'text.pt'),
                '--balance-output',str(tmp_path/'balance'),
                '--output',str(tmp_path/'rate'),
                '--budget-seconds','0.00001','--max-steps','1',
                '--check-seconds','0.00001',
                '--archive-train','2','--eval-archive','1',
                '--generated-train-per-cell','1','--eval-per-cell','1',
                '--eval-frontier-per-cell','1'])
    for k,v in changes.items():setattr(args,k,v)
    return args


def setup(tmp_path):
    args=_args(tmp_path)
    (tmp_path/'stage1.pt').write_bytes(b'test')
    (tmp_path/'text.pt').write_bytes(b'test')
    (tmp_path/'balance').mkdir()
    param=torch.nn.Parameter(torch.tensor(0.0))
    optimizer=torch.optim.AdamW([param],lr=.1)
    src={'schema':balance.SCHEMA,'lora':{'test_lora':torch.tensor(0.)},
         'optimizer':optimizer.state_dict(),
         'state':{'step':123,'policy':{'levels':2,'source_levels':2,
               'seed':123,'operand_boundary':1024,'start_rung_override':-1},
                'seed_state':{'rung':0,'source_sha256':'source_hash',
                              'frozen_sha256':'frozen_hash'}}}
    torch.save(src,tmp_path/'balance'/'best.pt')
    for j in range(4):
        fr._append(tmp_path/'balance'/'glass_hard_candidates.jsonl',
             {'source':f'PROBLEM: solve {j}*x=4','target':'PROBLEM: 1',
              'case_id':f'archived{j}','task':'algebra','level':0})
    return args


def test_disjoint_generated_evaluation_and_archived_training(tmp_path):
    args=setup(tmp_path)
    ck,train,evaluation,manifest,_=rate._load_data(args)
    assert len(train)>2 and len(evaluation)>2
    assert {r['source'] for r in train}.isdisjoint({r['source'] for r in evaluation})
    assert any(r['stratum']=='archive_eval' for r in evaluation)
    assert any(r['stratum']=='fresh_expanded' for r in evaluation)
    assert (tmp_path/'rate'/'source_snapshot.pt').read_bytes()==(tmp_path/'balance'/'best.pt').read_bytes()
    assert json.loads((tmp_path/'rate'/'manifest.json').read_text())['read_only_source']
    ck2,train2,eval2,manifest2,_=rate._load_data(args)
    assert ck2['state']['step']==123
    assert train2==train and eval2==evaluation
    args.extra_levels=3
    with pytest.raises(RuntimeError,match='RATE_RESUME_POLICY_OR_SOURCE_DRIFT'):
        rate._load_data(args)


def test_preview_read_only_and_never_loads_gpu(tmp_path,monkeypatch):
    args=setup(tmp_path)
    args.stage='preview'
    monkeypatch.setattr(ladder,'_fresh_parent',lambda *a:pytest.fail('GPU loaded on preview'))
    result=rate.run(args)
    assert result['train_cases']>0
    assert not (tmp_path/'rate').exists()


def test_training_trial_budget_and_optimizer_lineage(tmp_path,monkeypatch):
    args=setup(tmp_path)
    class BB(torch.nn.Module):
        def __init__(self):super().__init__();self.test_lora=torch.nn.Parameter(torch.tensor(0.))
        def get_output_embeddings(self):
            class Head:weight=torch.zeros(4,2)
            return Head()
    class Z:
        @staticmethod
        def lora_named_parameters(bb):return [('test_lora',bb.test_lora)]
        @staticmethod
        def lora_state(bb):return {'test_lora':bb.test_lora.detach().clone()}
        @staticmethod
        def restore_lora(bb,weights):bb.test_lora.data.copy_(weights['test_lora'])
    class FakeGlass:
        def __init__(self,*a,**kw):self.net=torch.nn.Linear(1,1)
    monkeypatch.setattr(rate.ab,'ContinuousGlass',FakeGlass)
    monkeypatch.setattr(rate.cached_glass,'train_step',lambda g,o,bb,row,replay,sites,a,step=0:
                     {'loss':1.})
    monkeypatch.setattr(ladder,'_batch_loss',lambda bb,tok,rows,device,max_seq: (bb.test_lora-1.)**2)
    monkeypatch.setattr(rate,'_greedy',lambda bb,tok,rows,a,glass=None,label='':
        {r['id']:(bool(bb.test_lora.detach()>0) if glass is None else False) for r in rows})
    base={'a':False}
    train=[{'id':'a','task':'algebra','level':0,'source':'q','target':'a'}]
    evaluation=[{'id':'a','stratum':'fresh_expanded'}]
    optimizer=torch.optim.AdamW([torch.nn.Parameter(torch.tensor(0.))],lr=.1)
    out=tmp_path/'out';out.mkdir()
    (out/'source_snapshot.pt').write_bytes(b'fake')
    result=rate._trial('raw',BB(),None,base,base,train,lambda s:(train[0],train[0]),
                       evaluation,args,Z,{'test_lora':torch.tensor(0.)},out,optimizer.state_dict())
    assert result['steps']==1
    assert result['metrics']['fresh']['net']==1
    assert (out/'raw_final_lora.pt').exists()
    with pytest.raises(RuntimeError,match='RATE_RAW_CONTINUATION_REQUIRES_OPTIMIZER_STATE'):
        rate._trial('raw',BB(),None,base,base,train,lambda s:(train[0],train[0]),
               evaluation,args,Z,{'test_lora':torch.tensor(0.)},out,None)


def test_mocked_full_rate_comparison_completes_without_touching_parent(tmp_path,monkeypatch):
    args=setup(tmp_path)
    source=tmp_path/'balance'/'best.pt';before=source.read_bytes()
    class BB(torch.nn.Module):
        def __init__(self):super().__init__();self.test_lora=torch.nn.Parameter(torch.tensor(0.))
        def get_output_embeddings(self):
            class Head:weight=torch.zeros(4,2)
            return Head()
    class FakeGlass:
        def __init__(self,*a,**kw):self.net=torch.nn.Linear(1,1)
    monkeypatch.setattr(rate.ladder,'_fresh_parent',lambda a:(BB(),None,'source_hash','frozen_hash',[],zero))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('test_lora',bb.test_lora)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'test_lora':bb.test_lora.detach().clone()})
    def restore(bb,weights):bb.test_lora.data.copy_(weights['test_lora'])
    monkeypatch.setattr(zero,'restore_lora',restore)
    monkeypatch.setattr(rate.ab,'ContinuousGlass',FakeGlass)
    monkeypatch.setattr(rate.cached_glass,'train_step',lambda g,o,bb,row,replay,sites,a,step=0:
                     {'loss':1.})
    class FakeLocal:
        def __init__(self,*a,**k):self.n_modes=0
        def state(self):return {'modes':self.n_modes}
        @classmethod
        def restore(cls,state,device='cpu'):
            obj=cls();obj.n_modes=state['modes'];return obj
    monkeypatch.setattr(rate.local_glass,'FrontierLocalGlass',FakeLocal)
    monkeypatch.setattr(rate.local_glass,'repair_step',lambda *a,**kw:
                     {'correct_after':False,'created':0,'updated':0,'token_optimizer_steps':0})
    monkeypatch.setattr(rate.ladder,'_batch_loss',lambda bb,tok,rows,device,max_seq: (bb.test_lora-1.)**2)
    monkeypatch.setattr(rate,'_greedy',lambda bb,tok,rows,a,glass=None,label='',cache=None:
            {r['id']: bool(bb.test_lora.detach()>0) if glass is None else False for r in rows})
    result=rate.run(args)
    assert result['status']=='COMPLETE'
    assert set(result['results'])==set(rate.BRANCHES)
    assert result['results']['raw']['metrics']['all']['net']>0
    assert result['results']['glass_A']['metrics']['all']['net']==0
    assert (tmp_path/'rate'/'report.json').exists()
    assert source.read_bytes()==before
    result2=rate.run(args)
    assert result2['status']=='COMPLETE'
    assert source.read_bytes()==before
