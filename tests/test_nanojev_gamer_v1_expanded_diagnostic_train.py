"""Bounded, actual-head diagnostic-train acceptance tests."""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import hashlib

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_expanded_head as exp
from tools import nanojev_gamer_v1_expanded_diagnostic_train as lab
from tools import nanojev_gamer_v1_handoff as gamer
from tools import nanojev_gamer_v1_staircase as cli


def test_trial_parsing_and_source_discovery(tmp_path):
    name=tmp_path/'transition-005-expanded-head-100-0001';name.mkdir()
    src=tmp_path/'transition-005';src.mkdir()
    assert lab._source_run_from_expanded(name)==src
    with pytest.raises(ValueError,match='SOURCE_RUN_REQUIRED'):
        lab._source_run_from_expanded(tmp_path/'experimental')
    assert lab._parse_trials('mixed,zero-focus,game:a')==('mixed','zero-focus','game:a')
    with pytest.raises(ValueError,match='UNKNOWN_TRIAL'):
        lab._parse_trials('random')
    with pytest.raises(ValueError,match='INVALID_TRIALS'):
        lab._parse_trials('mixed,mixed')


def test_ablation_and_regression_bookkeeping():
    x=np.arange(5*1024,dtype='float32').reshape(1,5*1024)
    last=x[:,-1024:]
    assert np.array_equal(lab._ablate(x,'full'),x)
    repeated=lab._ablate(x,'last-token-repeated').reshape(1,5,1024)
    assert all(np.array_equal(v,last[0]) for v in repeated[0])
    half=lab._ablate(x,'old-half-replaced').reshape(1,5,1024)
    assert np.array_equal(half[0,2],x.reshape(1,5,1024)[0,2])
    with pytest.raises(ValueError,match='UNKNOWN_ABLATION'):
        lab._ablate(x,'impossible')
    rows=[{'answer':0.,'game_id':'a'},{'answer':-1.,'game_id':'a'},
          {'answer':1.,'game_id':'b'},{'answer':5.,'game_id':'b'}]
    baseline=np.array([0.,-1.,1.,5.])
    damage=np.array([.1,+1.,2.,5.])
    m=lab._summarize(rows,damage)
    assert m['by_cohort']['zero']['cases']==1
    assert m['by_cohort']['small_negative']['sign_accuracy']==0
    loss=lab._forgetting(rows,baseline,damage)
    assert loss['sign_lost']==1 and loss['zero_exact_lost']==1


def test_parser_diagnostic_is_isolated(tmp_path):
    a=cli.parser().parse_args(['head-100-diagnostic-train','--run',str(tmp_path)])
    assert a.expanded_diag_steps==12
    assert a.expanded_diag_matched_steps==0
    with pytest.raises(ValueError,match='DIAG_FLAGS_ONLY'):
        cli.main(['status','--run',str(tmp_path),'--expanded-diag-steps','2'])
    with pytest.raises(ValueError,match='USES_EXPANDED_DIAG_STEPS'):
        cli.main(['head-100-diagnostic-train','--run',str(tmp_path),'--steps','4'])


class Tok:
    padding_side='right'
    def encode(self,text,add_special_tokens=True):return [(ord(c)%83)+1 for c in text]
    def __call__(self,text,padding=True,**kw):
        ids=[self.encode(p) for p in text]
        length=max(map(len,ids))
        return {'input_ids':torch.tensor([p+[0]*(length-len(p)) for p in ids]),
                'attention_mask':torch.tensor([[1]*len(p)+[0]*(length-len(p)) for p in ids])}


class Model(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.config=SimpleNamespace(hidden_size=1024)
        self.model=torch.nn.Module();self.model.norm=torch.nn.Identity()
    def forward(self,input_ids,attention_mask,*,output_hidden_states=False,return_dict=True,logits_to_keep=0):
        raw=input_ids.float().unsqueeze(-1)
        dims=torch.arange(1024,dtype=torch.float32).reshape(1,1,-1)
        hidden=self.model.norm(raw/(dims+2)+torch.cumsum(raw,dim=1)/(dims+3))
        return SimpleNamespace(hidden_states=(hidden,) if output_hidden_states else None)


def test_full_isolated_training_probe_and_source_immutability(tmp_path,monkeypatch):
    torch.set_num_threads(1)
    certified=tmp_path/'transition-005';certified.mkdir()
    (certified/'certified_pairs.jsonl').write_bytes(b'fixed-certified-source')
    (certified/'to_numerical').mkdir()
    (certified/'to_numerical'/'latest.pt').write_bytes(b'fixed-old-head')
    adapter=certified/'adapter';adapter.mkdir();(adapter/'weights').write_bytes(b'no-change-lora')
    rows=[]
    for part,n in (('train',12),('dev',8),('test',4)):
        for i in range(n):
            value=[0.,-1.,1.,3.][i%4]
            game='captain_crew_risk' if i%2 else 'bertrand_oligopoly'
            rows.append({'split':part,'game_id':game,
                         'prompt_08':f'Crew has {i+12} fuel; uses {i%4} fuel. What remains?',
                         'answer':value})
    tok=Tok();model=Model()
    def last(part):
        part_rows=[r for r in rows if r['split']==part]
        vals=[]
        for start in range(0,len(part_rows),2):
            mini=part_rows[start:start+2];enc=tok([r['prompt_08'] for r in mini])
            hid=model(**enc,output_hidden_states=True).hidden_states[-1]
            vals.extend([hid[i,int(enc['attention_mask'][i].sum())-1].numpy() for i in range(len(mini))])
        return np.stack(vals)
    source={'manifest':{'dataset_sha256':exp._sha(certified/'certified_pairs.jsonl'),
                         'original_prompt_corpus_sha256':'fixed-prompts','model':'fake-qwen'},
            'head_hp':{'max_length':256},'source_adapter':adapter,
            'lora_sha256':'original-lora','head_sha256':exp._sha(certified/'to_numerical'/'latest.pt'),
            'lora_state':{'step':1800},'head_state':{'step':2000},'cycle':certified,
            'full_split_rows':{k:[r for r in rows if r['split']==k] for k in ('train','dev')},
            'selected':{k:{'features':last(k),'rows':[r for r in rows if r['split']==k]}
                        for k in ('train','dev')}}
    monkeypatch.setattr(exp,'_source_inputs',lambda *a,**kw:source)
    monkeypatch.setattr(lab,'_source_inputs',lambda *a,**kw:source)
    monkeypatch.setattr(gamer,'load_cases',lambda *a,**kw:(rows,{'verified':True}))
    monkeypatch.setattr(gamer,'model_components',lambda *a,**kw:(torch,tok,Model()))
    expanded=tmp_path/'transition-005-expanded-head-100-0001'
    completed=exp.run_expanded(certified,device='cpu',output=expanded,steps=3,hidden=(8,6,4,2),
        max_length=256,backend='cpu',batch_size=3,capture_batch=2,eval_every=1,save_every=1)
    initial={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (expanded/'latest.pt',expanded/'best_model.pt',
                       certified/'to_numerical'/'latest.pt',adapter/'weights')}
    report=lab.run_expanded_diagnostic(expanded,device='cpu',steps=2,eval_every=1,
        trials=('mixed','lr-quarter','zero-focus','game:captain_crew_risk'),
        ablation_cases=4,precision_cases=0,matched_steps=2)
    assert report['status']=='COMPLETE_READ_ONLY_NOT_PROMOTED'
    assert report['baselines']['latest_checkpoint']['dev']['cases']==8
    assert report['baselines']['best_checkpoint']['train']['cases']==12
    assert report['best_to_latest']['best_has_optimizer'] is False
    assert report['fixed_weight_input_ablations']['full']['cases']==4
    assert report['matched_scratch_input_control']['status']=='COMPLETE_BOUNDED_MATCHED_TRAINING'
    assert len(report['matched_scratch_input_control']['trials']['full-100']['trace'])==2
    assert len(report['controlled_training']['mixed']['trace'])==2
    assert len(report['controlled_training']['lr-quarter']['trace'])==2
    assert report['controlled_training']['lr-quarter']['learning_rate_scale']==.25
    out=Path(report['provenance']['source_expanded_run']).parent/(
         expanded.name+'-diagnostic-train-0001')
    assert (out/'events.jsonl').is_file()
    assert (out/'predictions.csv').is_file()
    assert (out/'final_report.json').is_file()
    after={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in (expanded/'latest.pt',expanded/'best_model.pt',
                       certified/'to_numerical'/'latest.pt',adapter/'weights')}
    assert initial==after
    with pytest.raises(FileExistsError):
        lab.run_expanded_diagnostic(expanded,device='cpu',output=out,steps=1,
                                   trials=('mixed',),ablation_cases=0,precision_cases=0)


def test_exact_optimizer_moments_restored_in_place():
    torch.manual_seed(19)
    model=exp.make_expanded_head(torch,2048,(8,6,4,2))
    trainer=exp.HeadTrainer(torch,model,backend='cpu',precision='float16',lr=.0003,
                            weight_decay=.0001,clip=1.)
    inputs=torch.randn(3,2048)
    labels=torch.tensor([0.,.5,-1.],dtype=torch.float32)
    trainer.update(inputs,labels)
    import copy
    # A real checkpoint owns independent serialized tensors, unlike the live state() view.
    state=copy.deepcopy(trainer.state())
    losses=[];preds=[]
    for _ in range(2):
        lab._restore_optimizer_in_place(torch,trainer,state)
        losses.append(trainer.update(inputs,labels))
        preds.append(trainer.predict(inputs))
    assert losses[0]==pytest.approx(losses[1],rel=0,abs=1e-8)
    assert np.array_equal(preds[0],preds[1])

def test_large_parameter_sampling_uses_integer_indices_without_allocating_large_weights():
    # A real 102,400 x 4,096 first-layer tensor contains 419,430,400 weights.
    # float32 linspace would round its final index up to 419,430,400 (invalid).
    length=102400*4096
    ix=lab._sample_flat_indices(torch,length,1024)
    assert ix.dtype == torch.long
    assert len(ix)==1024
    assert ix[0].item()==0
    assert ix[-1].item()==length-1
    assert torch.all(ix[1:]>ix[:-1])
    assert int(ix.min())>=0 and int(ix.max())<length
    for m,n in ((1,1024),(2,1024),(9,1024),(1024,1024),(2048,1024),
                (100_000_000,200),(419_639_296,1024)):
        positions=lab._sample_flat_indices(torch,m,n)
        assert len(positions)==min(m,n)
        assert positions[0].item()==0 and positions[-1].item()==m-1
        assert torch.all(positions[1:]>positions[:-1])
    with pytest.raises(ValueError,match='PARAMETER_SAMPLE_SIZE_INVALID'):
        lab._sample_flat_indices(torch,0)
    with pytest.raises(ValueError,match='PARAMETER_SAMPLE_SIZE_INVALID'):
        lab._sample_flat_indices(torch,2,0)


def test_sampled_parameter_deltas_with_small_real_trainer():
    torch.manual_seed(4)
    head=exp.make_expanded_head(torch,2048,(8,6,4,2))
    trainer=exp.HeadTrainer(torch,head,backend='cpu',precision='float16',lr=1e-3,
                            weight_decay=1e-4,clip=1.0)
    before=lab._parameter_sample(torch,trainer,n=64)
    assert before['enc.0.weight'][2][-1].item()==2048*8-1
    x=torch.randn(3,2048)
    y=torch.tensor([0.,0.5,-1.])
    trainer.update(x,y)
    deltas=lab._parameter_deltas(torch,trainer,before)
    assert deltas['enc.0.weight']['sampled_parameters']==64
    assert 0<=deltas['enc.0.weight']['master_changed_fraction']<=1
    assert deltas['enc.0.weight']['master_changed_fraction']>0
