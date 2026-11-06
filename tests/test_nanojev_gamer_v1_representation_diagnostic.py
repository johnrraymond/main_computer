"""Source-certification, frozen-feature and matched-probe acceptance checks."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import math
import re
import sys
import types

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_representation_diagnostic as r
from tools import nanojev_gamer_v1_pairs as pairs
from tools import nanojev_gamer_v1_staircase as stair
from tools.nanojev_gamer_v1_register_oracle import build_oracle


class FakeTokenizer:
    is_fast=True
    pad_token_id=0
    eos_token='EOS'
    padding_side='right'
    @classmethod
    def from_pretrained(cls,*args,**kwargs):return cls()
    def __call__(self, text,**kwargs):
        if isinstance(text,list):
            ids=[torch.tensor([ord(c)%191+1 for c in t],dtype=torch.long) for t in text]
            max_len=max(len(x) for x in ids)
            if not kwargs.get('padding') and len({len(x) for x in ids})!=1:
                raise ValueError('batch without padding')
            input_ids=torch.stack([torch.nn.functional.pad(x,(0,max_len-len(x))) for x in ids])
            attention_mask=torch.stack([torch.cat([torch.ones_like(x),torch.zeros(max_len-len(x),dtype=torch.long)]) for x in ids])
            return {'input_ids':input_ids,'attention_mask':attention_mask}
        # Character-aligned fast tokenizer surrogate. Completely lossless.
        ids=torch.tensor([[ord(c)%191+1 for c in text]],dtype=torch.long)
        return {'input_ids':ids,'attention_mask':torch.ones_like(ids),
                'offset_mapping':torch.tensor([[(i,i+1) for i in range(len(text))]])}


class FakeModel(torch.nn.Module):
    def __init__(self,adapter=0):
        super().__init__();self.adapter=adapter
        self.parameter_for_eval_only=torch.nn.Parameter(torch.tensor([1.]))
    @classmethod
    def from_pretrained(cls,*args,**kwargs):return cls()
    def forward(self,input_ids,attention_mask,**kwargs):
        t=input_ids.float()/180.
        a=torch.stack([t,torch.sin(t),torch.cos(t),t**2,
                       t*2,torch.ones_like(t),1/(t+.3),torch.tanh(t)],dim=-1)
        avg=a.cumsum(dim=1)/torch.arange(1,len(t[0])+1,dtype=torch.float32)[None,:,None]
        h1=(a*.5+avg*.5)
        h2=h1+float(self.adapter)*.05
        return SimpleNamespace(hidden_states=(a,h1,h2,h2+.015))


class FakePeft:
    @staticmethod
    def from_pretrained(base,adapter,**kwargs):
        base.adapter=1
        return base


def test_tokenization_multitoken_fraction_decimals_and_fail_closed():
    tok=FakeTokenizer()
    text='Start: -0.0005, spending 1.2e-3, two identical 7 and 7.'
    enc,meta=r._tokenize(tok,text,1000)
    assert meta['candidate_count']==4
    assert all(n['token_end']>n['token_start'] for n in meta['numbers'])
    assert [n['literal'] for n in meta['numbers']]==['-0.0005','1.2e-3','7','7']
    with pytest.raises(ValueError,match='TRUNCATED'):
        r._tokenize(tok,text,2)
    tok.is_fast=False
    with pytest.raises(ValueError,match='FAST_TOKENIZER'):
        r._tokenize(tok,text,1000)


def test_real_generator_oracles_are_separate_from_model_features():
    games=('captain_fuel_reserve','captain_crew_risk','prisoners_dilemma','centipede')
    tok=FakeTokenizer()
    for game in games:
        for i in range(4):
            row=pairs.generate_case(game,i,47)
            doc=build_oracle(row)
            enc,meta=r._tokenize(tok,row['prompt_08'],10000)
            alignment=r._literal_alignment(row['prompt_08'],doc,meta['numbers'])
            assert len(alignment)==len(doc['registers'])
            assert all(a['status']!='MISSING' for a in alignment)
            assert enc['input_ids'].shape[1]==len(row['prompt_08'])


def test_frozen_capture_same_final_token_as_source(tmp_path):
    tok=FakeTokenizer();model=FakeModel(adapter=1)
    rows=[pairs.generate_case('captain_fuel_reserve',i,47) for i in (0,1)]
    docs=[build_oracle(row) for row in rows]
    reference=[]
    for row in rows:
        enc,_=r._tokenize(tok,row['prompt_08'],2000)
        reference.append(model(**{k:v for k,v in enc.items()},output_hidden_states=True).hidden_states[-1][0,-1].detach().numpy())
    out=r._capture(torch,model,tok,rows,docs,device='cpu',max_length=2000,
                   mode='frozen_lora',output=tmp_path,expected_last=np.asarray(reference))
    assert out['last_token_cache_max_abs_delta']==0
    assert len(out['features'])==len(r._layers(4))*(len(r.FEATURES)-1)+1
    assert out['features'][(3,'number_position_buckets')].shape==(2,32)
    assert out['features'][(3,'number_ordered_32')].shape==(2,256)
    assert (0,'number_ordered_32') not in out['features']
    wrong=np.asarray(reference)+.5
    with pytest.raises(ValueError,match='CACHED_FEATURE_MISMATCH'):
        r._capture(torch,model,tok,rows,docs,device='cpu',max_length=2000,
                   mode='frozen_lora',output=tmp_path,expected_last=wrong)


def test_probes_fit_train_only_and_report_inconclusive_when_needed():
    x=np.linspace(-1,1,24)[:,None]
    train=x[:18];dev=x[18:]
    ytrain=np.sin(3*train[:,0])
    for decoder in r.DECODERS:
        fitted=r._fit_kernel(train,ytrain,decoder=decoder)
        p1=r._predict_probe(fitted,dev)
        p2=r._predict_probe(fitted,dev)
        assert np.array_equal(p1,p2)
        assert np.isfinite(p1).all()
    cases=[{'game_id':'test','register_id':'a','mode':'frozen_lora','layer':1,
            'feature':'final_token','decoder':'ridge_linear','dev_mae':1.,
            'mean_baseline_mae':2.,'dev_support':3},
           {'game_id':'test','register_id':'a','mode':'frozen_lora','layer':2,
            'feature':'final_token','decoder':'ridge_linear','dev_mae':2.,
            'mean_baseline_mae':2.,'dev_support':3}]
    assert r._first_degradations(cases,[1,2])[0]['first_measured_degradation_layer']==2


def test_cli_rejects_unrelated_controls_and_steps(tmp_path):
    with pytest.raises(ValueError,match='GAMER_REPR_FLAGS_ONLY'):
        stair.main(['status','--run',str(tmp_path),'--repr-max-games','4'])
    with pytest.raises(ValueError,match='DOES_NOT_TRAIN'):
        stair.main(['representation-diagnostic','--run',str(tmp_path),'--steps','100'])


def test_end_to_end_mock_source_fingerprints_and_train_dev_isolation(tmp_path,monkeypatch):
    selected={};rows=[]
    for game in ('captain_fuel_reserve','prisoners_dilemma'):
        for i in range(8):
            row=pairs.generate_case(game,i,47)
            rows.append(row)
    for part,ids in [('train',(0,1,2,3,4)),('dev',(5,6,7))]:
        sub=[row for game in ('captain_fuel_reserve','prisoners_dilemma')
             for j,row in enumerate([r0 for r0 in rows if r0['game_id']==game]) if j in ids]
        selected[part]={'rows':sub,'features':np.empty((len(sub),8),dtype=np.float32)}
    tok=FakeTokenizer()
    pretrained=FakeModel(adapter=1)
    for part in selected:
        vals=[]
        for row in selected[part]['rows']:
            enc,_=r._tokenize(tok,row['prompt_08'],4000)
            vals.append(pretrained(**enc).hidden_states[-1][0,-1].detach().numpy())
        selected[part]['features']=np.stack(vals)
    cycle=tmp_path/'cycle'
    (cycle/'to_numerical').mkdir(parents=True)
    (cycle/'to_numerical'/'latest.pt').write_bytes(b'source-checkpoint')
    (cycle/'lora'/'best_adapter').mkdir(parents=True)
    (cycle/'lora'/'best_adapter'/'adapter_model.safetensors').write_bytes(b'untouched-adapter')
    fake_source={'selected':selected,'manifest':{'model':'fake-qwen','dataset_sha256':'certified'},
                 'cycle':cycle,'source_adapter':cycle/'lora'/'best_adapter',
                 'head_state':{'adapter_sha256':'fake-sha'},
                 'lora_sha256':'lora-state','head_sha256':'head-checkpoint',
                 'lora_state':{'step':1800,'hyperparameters':{'max_length':4000}},
                 'head_hp':{'max_length':4000},'games':['captain_fuel_reserve','prisoners_dilemma']}
    monkeypatch.setattr(r,'_source_inputs',lambda *args,**kw:fake_source)
    fake_transformers=types.ModuleType('transformers')
    fake_transformers.AutoTokenizer=FakeTokenizer
    fake_transformers.AutoModelForCausalLM=FakeModel
    fake_peft=types.ModuleType('peft');fake_peft.PeftModel=FakePeft
    monkeypatch.setitem(sys.modules,'transformers',fake_transformers)
    monkeypatch.setitem(sys.modules,'peft',fake_peft)
    out=tmp_path/'report'
    orig_head=(cycle/'to_numerical'/'latest.pt').read_bytes()
    orig_adapter=(cycle/'lora'/'best_adapter'/'adapter_model.safetensors').read_bytes()
    report=r.run_representation_diagnostic(tmp_path/'run',device='cpu',output=out,
                    max_games=2,train_per_game=5,dev_per_game=3,perturbations=1)
    assert report['status']=='COMPLETED_DIAGNOSTIC_NOT_PROMOTED'
    assert report['train_cases']==10 and report['dev_cases']==6
    assert (out/'layer_recoverability.csv').exists()
    assert (out/'first_degradations.csv').exists()
    assert (out/'register_perturbations.csv').exists()
    assert (out/'provenance.json').exists()
    assert (cycle/'to_numerical'/'latest.pt').read_bytes()==orig_head
    assert (cycle/'lora'/'best_adapter'/'adapter_model.safetensors').read_bytes()==orig_adapter
    assert len(json.loads((out/'final_report.json').read_text())['frozen_lora_final_layer_comparison'])==10


def test_146_generator_families_register_token_alignment_without_value_routing():
    from tools import nanojev_game_transformation_library as native
    from tools import nanojev_gamer_v1_catalog_extensions as ext
    from tools import nanojev_gamer_v1_transition_games as transition
    games=sorted(set(native.MATRICES)|set(native.SCALAR_MODELS)|
                 set(ext.EXTRA_IDS)|set(transition.TRANSITION_IDS))
    assert len(games)==146
    tok=FakeTokenizer();totals={'cases':0,'labels':0,'numeric_spans':0}
    for game in games:
        for i in range(18):
            row=pairs.generate_case(game,i,47)
            doc=build_oracle(row)
            _,meta=r._tokenize(tok,row['prompt_08'],10000)
            align=r._literal_alignment(row['prompt_08'],doc,meta['numbers'])
            assert len(align)==len(doc['registers'])
            assert all(item['status'] in ('LITERAL_TOKEN_COVERED',
                       'MULTIPLE_MATCHING_SPANS_ROLE_UNRESOLVED',
                       'SYMBOLIC_SELECTOR_IN_PROMPT') for item in align)
            totals['cases']+=1;totals['labels']+=len(align)
            totals['numeric_spans']+=meta['candidate_count']
    assert totals=={'cases':2628,'labels':10674,'numeric_spans':11124}


def test_dev_label_mutation_does_not_change_any_predictions():
    from copy import deepcopy
    game='captain_fuel_reserve'
    train=[build_oracle(pairs.generate_case(game,i,47)) for i in range(5)]
    dev=[build_oracle(pairs.generate_case(game,i,47)) for i in range(5,8)]
    feat=np.arange(8*8,dtype=np.float32).reshape(8,8)
    features={('frozen_lora',3,'final_token'):feat}
    _, original=r._probe_all(features,train,dev)
    changed=deepcopy(dev)
    for doc in changed:
        for reg in doc['registers']:
            reg['value']['float']=999.0
    _, revised=r._probe_all(features,train,changed)
    assert [(x['register_id'],x['predicted']) for x in original]==[
           (x['register_id'],x['predicted']) for x in revised]
    assert any(a['absolute_error']!=b['absolute_error'] for a,b in zip(original,revised))

class BatchSensitiveModel(FakeModel):
    """Simulates a backend whose precision depends on padding and cache flags."""
    def forward(self,input_ids,attention_mask,**kwargs):
        out=super().forward(input_ids,attention_mask,**kwargs)
        batch_offset=.5 if input_ids.shape[0]==1 else 0.
        cache_offset=.25 if kwargs.get('use_cache') is False else 0.
        return SimpleNamespace(hidden_states=tuple(
            h + batch_offset + cache_offset for h in out.hidden_states))


def test_source_batched_capture_matches_cached_vector_not_single_prompt(tmp_path):
    tok=FakeTokenizer();model=BatchSensitiveModel(adapter=1)
    rows=[pairs.generate_case('captain_fuel_reserve',i,47) for i in range(4)]
    docs=[build_oracle(row) for row in rows]
    docs=[dict(d,split='train' if i<2 else 'dev') for i,d in enumerate(docs)]
    reference=[]
    for split_rows in (rows[:2],rows[2:]):
        encoded=tok([row['prompt_08'] for row in split_rows],padding=True,
                    truncation=True,max_length=4000,return_tensors='pt')
        h=model(**encoded,output_hidden_states=True,return_dict=True).hidden_states[-1]
        last=encoded['attention_mask'].sum(dim=-1)-1
        reference.extend([h[j,last[j]].detach().numpy() for j in range(len(last))])
    expected=np.stack(reference)
    # The original implementation used single forwards with use_cache=False.
    # That is genuinely a different numeric capture even on the same weights.
    original_wrong=[]
    for row in rows:
        enc,_=r._tokenize(tok,row['prompt_08'],4000)
        original_wrong.append(model(**enc,output_hidden_states=True,
                                    return_dict=True,use_cache=False).hidden_states[-1][0,-1].detach().numpy())
    assert np.max(np.abs(expected-np.stack(original_wrong)))>.1
    result=r._capture(torch,model,tok,rows,docs,device='cpu',max_length=4000,
                      capture_batch=2,mode='frozen_lora',output=tmp_path,
                      expected_last=expected)
    assert result['capture_parity']['status']=='MATCHED_SOURCE_FEATURE_CACHE'
    assert result['last_token_cache_max_abs_delta']==0
    assert (tmp_path/'capture_parity.csv').is_file()
    assert len((tmp_path/'capture_parity.csv').read_text().splitlines())==5
    with pytest.raises(ValueError,match='CACHED_FEATURE_MISMATCH'):
        r._capture(torch,model,tok,rows,docs,device='cpu',max_length=4000,
                   capture_batch=1,mode='frozen_lora',output=tmp_path,
                   expected_last=expected)
    report=json.loads((tmp_path/'capture_parity_report.json').read_text())
    assert report['cases_exceeding_original_max_abs_tolerance']==4
    assert report['max_abs_delta']>=.5
    assert report['worst_cases'][0]['case_sha256']


def test_capture_batch_boundaries_restart_at_split():
    docs=[{'split':s} for s in ['train']*3+['dev']*3]
    assert list(r._source_capture_batches(docs,2))==[(0,2),(2,3),(3,5),(5,6)]
    with pytest.raises(ValueError,match='INVALID_CAPTURE_BATCH'):
        list(r._source_capture_batches(docs,0))
    with pytest.raises(ValueError,match='CACHED_FEATURE_SHAPE'):
        r._parity_metrics(np.ones(2),np.ones(3))

def test_partial_game_sample_preserves_original_padded_batch_partners(tmp_path):
    tok=FakeTokenizer();model=BatchSensitiveModel(adapter=1)
    full=[pairs.generate_case('captain_fuel_reserve',i,47) for i in range(6)]
    positions=[0,2,5]  # Every selected row has a missing production-batch partner.
    rows=[full[i] for i in positions]
    docs=[dict(build_oracle(row),split='train') for row in rows]
    vectors=[]
    for k in range(0,len(full),2):
        enc=tok([r0['prompt_08'] for r0 in full[k:k+2]],padding=True,
                truncation=True,max_length=4000,return_tensors='pt')
        out=model(**enc,output_hidden_states=True,return_dict=True)
        last=enc['attention_mask'].sum(dim=-1)-1
        vectors.extend([out.hidden_states[-1][j,last[j]].detach().numpy() for j in range(len(last))])
    expected=np.stack(vectors)[positions]
    context={'full_rows':{'train':full,'dev':[]},
             'selected_indices':{'train':positions,'dev':[]}}
    out=r._capture(torch,model,tok,rows,docs,device='cpu',max_length=4000,
                   capture_batch=2,mode='frozen_lora',output=tmp_path,
                   source_batch_context=context,expected_last=expected)
    assert out['capture_parity']['cases_compared']==3
    assert out['last_token_cache_max_abs_delta']==0
    assert len(out['features'][(3,'final_token')])==3
    assert list(r._capture_plan(rows,docs,2,context))==[
        ([0],[full[0]['prompt_08'],full[1]['prompt_08']],[0]),
        ([1],[full[2]['prompt_08'],full[3]['prompt_08']],[0]),
        ([2],[full[4]['prompt_08'],full[5]['prompt_08']],[1])]
