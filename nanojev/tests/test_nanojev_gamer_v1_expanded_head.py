"""GAMER 100-vector capture, geometry, lineage, and training acceptance tests."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_expanded_head as exp
from tools import nanojev_gamer_v1_staircase as stair
from tools import nanojev_gamer_v1_handoff as gamer


@pytest.mark.parametrize('n',[1,2,9,10,39,99,100,101,1024,32768])
def test_exponential_positions_are_bounded_deterministic_and_complete(n):
    p=exp.positions_for_length(n)
    assert len(p)==100 and p==tuple(sorted(p))
    assert p[0]==0 and p[-1]==n-1
    assert all(0<=v<n for v in p)
    assert p==exp.positions_for_length(n)
    assert len(set(p))==min(n,100)
    if n>500:
        gaps=np.diff(p)
        assert np.mean(gaps[:10])>np.mean(gaps[-10:])*3


def test_equal_prompt_length_and_linear_curvature():
    assert exp.positions_for_length(100)==tuple(range(100))
    assert exp.positions_for_length(101,curvature=0)[0]==0
    assert exp.positions_for_length(101,curvature=0)[-1]==100
    with pytest.raises(ValueError,match='CURVATURE'):
        exp.positions_for_length(99,curvature=-1)
    with pytest.raises(ValueError,match='SEQUENCE'):
        exp.positions_for_length(0)


def test_architecture_is_exact_existing_c0_wide_resized():
    net=exp.make_expanded_head(torch,102400,(8,6,4,2))
    assert tuple(net.norm.normalized_shape)==(102400,)
    assert net.enc[0].in_features==102400
    assert net.enc[0].out_features==8
    assert net.enc[2].out_features==6
    assert net.enc[4].out_features==4
    assert net.mid[0].out_features==2
    assert net.corrected.out_features==net.uncorrected.out_features==1
    expected=102400*4096
    assert expected==419430400  # Full proposed first weight matrix.
    assert exp.configuration()['input_width']==102400
    assert exp.configuration()['hidden']==[4096,2048,512,128]
    with pytest.raises(ValueError,match='HYPERPARAMETERS'):
        exp.configuration(samples=10)


class Tok:
    padding_side='right'
    def encode(self,text,add_special_tokens=True):
        return [(ord(c)%83)+1 for c in text]
    def __call__(self,text,padding=True,**kwargs):
        items=[self.encode(p) for p in text]
        length=max(map(len,items))
        return {'input_ids':torch.tensor([p+[0]*(length-len(p)) for p in items]),
                'attention_mask':torch.tensor([[1]*len(p)+[0]*(length-len(p)) for p in items])}


class Model(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.config=SimpleNamespace(hidden_size=1024)
        self.model=torch.nn.Module()
        self.model.norm=torch.nn.Identity()
        self.captured_modes=[]
    def forward(self,input_ids,attention_mask,*,output_hidden_states=False,
                return_dict=True,logits_to_keep=0):
        self.captured_modes.append((output_hidden_states,logits_to_keep))
        raw=input_ids.float().unsqueeze(-1)
        dims=torch.arange(1024,dtype=torch.float32).view(1,1,-1)
        x=raw/(dims+2)+torch.cumsum(raw,dim=1)/(dims+3)
        hidden=self.model.norm(x)
        return SimpleNamespace(hidden_states=(hidden,) if output_hidden_states else None)


def test_capture_uses_only_final_norm_hook_and_preserves_legacy_parity():
    m=Model();tok=Tok()
    rows=[{'prompt_08':'abcdefghijklmnop'}, {'prompt_08':'xy'}]
    enc=tok([r['prompt_08'] for r in rows])
    reference=m(**enc,output_hidden_states=True).hidden_states[-1]
    expected=np.stack([reference[i,int(enc['attention_mask'][i].sum())-1].numpy()
                       for i in range(2)])
    m.captured_modes.clear()
    features,report=exp._capture(torch,m,tok,rows,device='cpu',max_length=256,
                        batch_size=2,samples=100,curvature=4.,original_features=expected)
    assert features.shape==(2,102400)
    assert np.array_equal(features[:,-1024:],expected)
    assert report['max_legacy_last_token_delta']==0
    assert report['short_prompt_cases']==2
    assert report['min_distinct_samples']==2
    assert m.captured_modes==[(False,1)]
    with pytest.raises(ValueError,match='PARITY'):
        exp._capture(torch,m,tok,rows,device='cpu',max_length=256,
                     batch_size=2,samples=100,curvature=4.,original_features=expected+1)
    with pytest.raises(ValueError,match='TRUNCATION'):
        exp._capture(torch,m,tok,rows,device='cpu',max_length=2,
                     batch_size=2,samples=100,curvature=4.)


def test_gpu_offload_is_explicit_not_silently_replaced_by_cpu():
    if not torch.cuda.is_available():
        with pytest.raises(RuntimeError,match='CUDA_REQUIRED'):
            exp.HeadTrainer(torch,exp.make_expanded_head(torch,102400,(8,6,4,2)),
                    backend='offload-cuda',precision='float16',lr=0.0002,
                    weight_decay=0.0001,clip=1.)


def test_cpu_trainer_updates_large_input_without_concat_branch():
    torch.manual_seed(42)
    model=exp.make_expanded_head(torch,102400,(8,6,4,2))
    trainer=exp.HeadTrainer(torch,model,backend='cpu',precision='float16',
                      lr=0.001,weight_decay=0.,clip=1.)
    x=torch.randn(3,102400)
    y=torch.tensor([0.,1.,-.5])
    old=trainer.master.enc[0].weight.detach().clone()
    loss=trainer.update(x,y)
    assert np.isfinite(loss)
    assert not torch.equal(old,trainer.master.enc[0].weight)
    p=trainer.predict(x)
    assert p.shape==(3,) and np.isfinite(p).all()
    state=trainer.state()
    clone=exp.HeadTrainer(torch,exp.make_expanded_head(torch,102400,(8,6,4,2)),
                backend='cpu',precision='float16',lr=.001,weight_decay=0.,clip=1.)
    clone.restore(state)
    assert np.array_equal(p,clone.predict(x))


def test_cli_head_100_does_not_accept_other_phase_controls(tmp_path):
    parsed=stair.parser().parse_args(['head-100','--run',str(tmp_path),'--steps','10'])
    assert parsed.expanded_backend=='offload-cuda'
    assert parsed.expanded_hidden=='4096,2048,512,128'
    with pytest.raises(ValueError,match='EXPANDED_FLAGS_ONLY'):
        stair.main(['status','--run',str(tmp_path),'--expanded-batch','8'])
    with pytest.raises(ValueError,match='POSITIVE_STEPS'):
        stair.main(['head-100','--run',str(tmp_path)])


def test_end_to_end_cpu_lineage_training_resume_and_source_immutability(tmp_path,monkeypatch):
    torch.set_num_threads(1)
    rows=[]
    for split,n in (('train',12),('dev',4),('test',4)):
        for i in range(n):
            answer=[0.,-1.,1.,2.][i%4]
            rows.append({'split':split,'game_id':'demo',
                  'prompt_08':f'Ship has {i+12} fuel; uses {i%4} fuel. What remains?',
                  'answer':answer})
    run=tmp_path/'source';run.mkdir()
    (run/'certified_pairs.jsonl').write_bytes(b'certified-source')
    (run/'to_numerical').mkdir()
    (run/'to_numerical'/'latest.pt').write_bytes(b'original-head-state')
    checkpoint=run/'original_head.pt';checkpoint.write_bytes(b'original-unchanged')
    adapter=run/'adapter';adapter.mkdir();(adapter/'weights').write_bytes(b'frozen')
    tok=Tok();mod=Model()
    def last(split):
        cases=[r for r in rows if r['split']==split]
        out=[]
        for batch_start in range(0,len(cases),2):
            prompts=[r['prompt_08'] for r in cases[batch_start:batch_start+2]]
            encoded=tok(prompts)
            hidden=mod(**encoded,output_hidden_states=True).hidden_states[-1]
            out.extend([hidden[i,int(encoded['attention_mask'][i].sum())-1].numpy()
                       for i in range(len(prompts))])
        return np.stack(out)
    source={'manifest':{'dataset_sha256':exp._sha(run/'certified_pairs.jsonl'),'original_prompt_corpus_sha256':'fixed-prompts','model':'fake-qwen'},
        'head_hp':{'max_length':256},'source_adapter':adapter,
        'lora_sha256':'original-lora-checkpoint','head_sha256':exp._sha(run/'to_numerical'/'latest.pt'),
        'lora_state':{'step':1800},'head_state':{'step':2000},
        'cycle':run,'full_split_rows':{p:[r for r in rows if r['split']==p] for p in ('train','dev')},
        'selected':{p:{'features':last(p),'rows':[r for r in rows if r['split']==p]}
                    for p in ('train','dev')}}
    monkeypatch.setattr(exp,'_source_inputs',lambda *a,**kw:source)
    monkeypatch.setattr(gamer,'load_cases',lambda *a,**kw:(rows,{'verified':True}))
    monkeypatch.setattr(gamer,'model_components',lambda *a,**kw:(torch,tok,Model()))
    out=tmp_path/'expanded'
    values={'device':'cpu','output':out,'steps':2,'hidden':(8,6,4,2),
        'max_length':256,'backend':'cpu','batch_size':3,'capture_batch':2,
        'eval_every':1,'save_every':1}
    report=exp.run_expanded(run,**values)
    assert report['steps']==2 and report['no_lora_updates']
    assert report['dev']['numeric']['cases']==4
    assert report['test']['numeric']['cases']==4
    assert (out/'best_model.pt').is_file() and (out/'latest.pt').is_file()
    assert (out/'feature_cache'/'train.npz').is_file()
    assert (out/'dev_best.csv').is_file()
    assert (out/'test_best.csv').is_file()
    assert (out/'dev_evaluations'/'step_0000002.csv').is_file()
    assert (adapter/'weights').read_bytes()==b'frozen'
    assert checkpoint.read_bytes()==b'original-unchanged'
    values.update(resume=True,steps=3)
    resumed=exp.run_expanded(run,**values)
    assert resumed['steps']==3
    assert len(resumed['checks'])==3
    assert (adapter/'weights').read_bytes()==b'frozen'
    assert checkpoint.read_bytes()==b'original-unchanged'
    with pytest.raises(ValueError,match='PROVENANCE|CHECKPOINT|LINEAGE'):
        exp.run_expanded(run,**{**values,'lr':.002,'steps':4})
    with pytest.raises(ValueError,match='OUTPUT_OVERLAPS_SOURCE'):
        exp.run_expanded(run,**{**values,'output':run,'resume':True,'steps':4})
