"""Deterministic, CUDA-free competency diagnostic contract checks."""
from __future__ import annotations
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import nanojev_qwen_competency_diagnostic as c
from tools import nanojev_qwen_lm_frontier_stress_repair as frontier
from tools import nanojev_qwen_lm_radius_ladder as ladder


def args(tmp_path, **changes):
    d=dict(output=str(tmp_path/'parent'), stage1_checkpoint=str(tmp_path/'stage1.pt'),
           text_checkpoint=str(tmp_path/'text.pt'), device='cpu',
           terminal_mode='numeric', radii='0,1,2,3,4', frontier_max_radius=64,
           frontier_steps=1200, frontier_check_every=100,
           frontier_train_cases=128, frontier_eval_cases=192, frontier_harvest_cases=48,
           frontier_neighbor_probe_cap=384,frontier_repair_center_cap=256,
           frontier_repair_steps=150,local_radius=4, batch_size=4, eval_batch=8,
           max_rung=8, seed=42257, operand_limit=1024, max_tokens=256,
           max_new_tokens=64, max_seq=192,lr=0.00005,frontier_output=None,
           competency_per_rung=6, competency_hdb_cases=9, competency_chain_cases=3,
           competency_max_tokens=512, competency_max_new_tokens=24,
           frontier_competency_every=0)
    d.update(changes)
    return SimpleNamespace(**d)


def test_deck_families_and_hdb_zero_balance(tmp_path):
    a=args(tmp_path)
    settings=c._settings(a)
    deck=c._deck(a,settings)
    assert len(deck['hdb'])==9*9
    assert {r['tier'] for r in deck['hdb']}=={f'HDB{i:02d}' for i in range(9)}
    for tier in {r['tier'] for r in deck['hdb']}:
        rows=[r for r in deck['hdb'] if r['tier']==tier]
        assert {r['expected'] for r in rows}=={'HAWK','DOVE','TIE'}
        assert {r['zero_side'] for r in rows}=={'negative','zero','positive'}
        for r in rows:
            assert int(r['x'])-int(r['y'])==r['residual']==2*r['offset']
            assert 'DOVE_DOVE_PAYOFF = 2' in r['source']
            assert r['tier'] not in r['source']
    for name, rows in deck['arithmetic'].items():
        assert len(rows)==9*6
        assert {r['rung'] for r in rows}==set(range(9))
        assert len({frontier._key(r) for r in rows})==len(rows)
        for r in rows:
            assert sum(r['leaves'])==r['residual']
            assert max(abs(v) for v in r['leaves'])<=a.operand_limit
            assert r['target']==r['complete_chain'][1]
            if name=='near_zero': assert abs(r['residual'])<=2
            if name=='outer_result': assert abs(r['residual'])>64
            if name=='operand_edge': assert max(map(abs,r['leaves']))>=1008
    assert len(deck['chains'])==9
    assert {r['rung'] for r in deck['chains']}=={3,5,8}


def test_hdb_format_contract_and_transitions(tmp_path):
    rows=c._hdb_deck(c._settings(args(tmp_path)))[:9]
    key=lambda row:c._digest(row['source'])
    before={key(r):{'correct':v} for r,v in zip(rows,[False,True,None,False,True,False,True,False,True])}
    after={key(r):{'correct':v} for r,v in zip(rows,[True,False,None,False,True,True,True,True,False])}
    d=c._metrics(rows,before,after,'hdb')
    assert d['total']['tested']==8
    assert d['total']['not_tested']==1
    assert d['total']['gained']==3 and d['total']['lost']==2
    assert d['total']['net']==1
    assert c._grade_choice('hawk\n')=='HAWK'
    assert c._grade_choice('HAWK is best') is None
    assert c._grade_choice('PROBLEM: 0') is None


def test_exposure_reconstructs_exact_deterministic_batch_count(tmp_path):
    a=args(tmp_path)
    e=c._exposures(a,300)
    assert sum(x['examples'] for x in e.values())==300*a.batch_size
    assert all(0<=v['steps_with_exposure']<=300 for v in e.values())
    # Tiers are not synchronized to 1/n equal batches; count real samples.
    assert len({v['examples'] for v in e.values()})>1
    assert all(sum(v['by_radius'].values())==v['examples'] for v in e.values())
    x=c._exposures(a,0)
    assert all(v['examples']==0 for v in x.values())
    speed=c._rate({'by_tier':{'HDB00':{'correct':4,'tested':9,'accuracy':4/9,
       'gained':2,'lost':1,'net':1}}},None,None,2.,20,{},e,trained=False)
    assert speed['HDB00']['training_exposures']==0
    assert speed['HDB00']['interpretation']=='transfer_only_no_HDB_training'


def test_in_place_audit_preserves_weights_and_resumes_without_double_count(monkeypatch,tmp_path):
    torch=pytest.importorskip('torch')
    a=args(tmp_path,competency_per_rung=2,competency_hdb_cases=3,
           competency_chain_cases=1)
    class Zero:
        @staticmethod
        def lora_state(model):return {'weight':model.weight.detach().clone()}
        @staticmethod
        def restore_lora(model, weights):
            with torch.no_grad(): model.weight.copy_(weights['weight'])
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight=torch.nn.Parameter(torch.tensor(2.))
    m=Model()
    calls=[]
    def fake_observe(model,tok,args,settings,deck,**kwargs):
        calls.append(float(model.weight.detach()))
        correct=float(model.weight.detach())>1
        scores={'arithmetic':{family:{frontier._key(r):{'correct':correct}
              for r in rows} for family,rows in deck['arithmetic'].items()},
              'hdb':{c._digest(r['source']):{'correct':correct} for r in deck['hdb']},
              'chains':{frontier._key(r):{'correct':correct,'terminal_correct':correct,
                'first_wrong_rung':None if correct else 'T07'} for r in deck['chains']}}
        return scores,0.1,{"near_zero":0.02,"operand_edge":0.02,"outer_result":0.02,"chains":0.02,"hdb":0.02}
    monkeypatch.setattr(c,'_observe',fake_observe)
    out=tmp_path/'report';out.mkdir()
    state={'phase_step':300,'training_seconds':120.,'source_sha256':'parent-abc'}
    report=c._check(state,m,None,a,out,{'lora':{'weight':torch.tensor(0.)}},Zero)
    assert calls==[0.,2.]
    assert float(m.weight.detach())==2.
    assert report['from_start']['hdb']['total']['gained']==27
    assert report['hdb_training_exposures']==0
    assert report['velocity_by_tier']['hdb']['HDB00']['net_per_100_exposures'] is None
    report2=c._check(state,m,None,a,out,{'lora':{'weight':torch.tensor(0.)}},Zero)
    assert len(calls)==2 and report2['step']==300
    state['phase_step']=400;state['training_seconds']=160.
    m.weight.data.fill_(1.5)
    next_report=c._check(state,m,None,a,out,{'lora':{'weight':torch.tensor(0.)}},Zero)
    assert calls==[0.,2.,1.5]
    assert next_report['interval_optimizer_steps']==100
    assert next_report['velocity_by_tier']['hdb']['HDB08']['training_exposures']==0
    assert len((out/'competency_history.jsonl').read_text().splitlines())==2
    with pytest.raises(RuntimeError,match='COMPETENCY_CHECKPOINT_REWOUND'):
        state['phase_step']=399
        c._check(state,m,None,a,out,{'lora':{'weight':torch.tensor(0.)}},Zero)


def test_diagnostic_deck_never_duplicates_current_frontier_training(tmp_path):
    a=args(tmp_path)
    settings=c._settings(a)
    forbidden=c._forbidden_keys(a,settings)
    deck=c._deck(a,settings)
    ids=[frontier._key(row) for rows in deck['arithmetic'].values() for row in rows]
    ids.extend(frontier._key(row) for row in deck['chains'])
    assert len(ids)==len(set(ids))
    assert not set(ids)&forbidden


def test_diagnostic_cached_greedy_matches_legacy_tokens_and_uses_incremental_input():
    torch=pytest.importorskip('torch')
    class Tokenizer:
        pad_token_id=0
        eos_token_id=1
        def __call__(self,s,add_special_tokens=False):
            return {'input_ids':[ord(c) for c in s]}
        def decode(self,ids,skip_special_tokens=True):
            return ''.join(chr(i) for i in ids if not skip_special_tokens or i!=1)
    class Decoder(torch.nn.Module):
        def __init__(self):
            super().__init__();self.lengths=[]
        def forward(self, input_ids,attention_mask,use_cache,return_dict,past_key_values=None):
            self.lengths.append(input_ids.shape[1])
            rows,length=input_ids.shape
            past=(past_key_values or 0)+length if use_cache else None
            return SimpleNamespace(last_hidden_state=torch.ones(rows,length,1),
                                   past_key_values=past)
    class Head(torch.nn.Module):
        def __init__(self):
            super().__init__();self.weight=torch.nn.Parameter(torch.zeros(128,1))
        def forward(self,h):
            logits=torch.zeros(h.shape[0],128)
            logits[:,ord('7')]=1
            return logits
    class Model:
        def __init__(self):self.decoder=Decoder();self.head=Head()
        def get_decoder(self):return self.decoder
        def get_output_embeddings(self):return self.head
    bb=Model();tok=Tokenizer();sources=['PROBLEM: 12','PROBLEM: 123456']
    legacy=ladder._greedy(bb,tok,sources,'cpu',100,4)
    legacy_lengths=bb.decoder.lengths[:]
    bb.decoder.lengths=[]
    cached=c._cached_greedy(bb,tok,sources,'cpu',100,4)
    assert cached==legacy==['7777','7777']
    assert legacy_lengths==[len('PROBLEM: 123456\n')+i for i in range(4)]
    assert bb.decoder.lengths==[len('PROBLEM: 123456\n'),1,1,1]


def test_baseline_cache_skips_repeated_baseline_after_interruption(monkeypatch,tmp_path):
    torch=pytest.importorskip('torch')
    a=args(tmp_path,competency_per_rung=2,competency_hdb_cases=3,competency_chain_cases=1)
    class Zero:
        @staticmethod
        def lora_state(m):return {'weight':m.weight.detach().clone()}
        @staticmethod
        def restore_lora(m,v):
            with torch.no_grad():m.weight.copy_(v['weight'])
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__();self.weight=torch.nn.Parameter(torch.tensor(2.))
    model=Model(); calls=[]
    def observe(m,t,a,settings,deck,**kwargs):
        calls.append(float(m.weight.detach()))
        if len(calls)==2:
            raise KeyboardInterrupt()
        x={'arithmetic':{f:{frontier._key(r):{'correct':False}
                 for r in rows} for f,rows in deck['arithmetic'].items()},
           'hdb':{c._digest(r['source']):{'correct':False} for r in deck['hdb']},
           'chains':{frontier._key(r):{'correct':False,'terminal_correct':False}
                     for r in deck['chains']}}
        return x,0.1,{'near_zero':0.02,'operand_edge':0.02,
                      'outer_result':0.02,'chains':0.02,'hdb':0.02}
    monkeypatch.setattr(c,'_observe',observe)
    out=tmp_path/'out';out.mkdir()
    state={'phase_step':300,'source_sha256':'parent-abc','training_seconds':40.}
    with pytest.raises(KeyboardInterrupt):
        c._check(state,model,None,a,out,{'lora':{'weight':torch.tensor(0.)}},Zero)
    assert calls==[0.,2.]
    assert float(model.weight.detach())==2.
    assert (out/'competency_baseline_cache.json').is_file()
    calls.clear()
    report=c._check(state,model,None,a,out,{'lora':{'weight':torch.tensor(0.)}},Zero)
    assert calls==[2.]
    assert report['step']==300
