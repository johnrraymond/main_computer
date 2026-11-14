"""Offline validation for six-chain Numerate Spine search; no external weights."""
from __future__ import annotations

import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_numerate_spine_six_chain_search as ns


def config(**kw):
    c={k:v[0] for k,v in ns.DIMENSIONS.items()}
    c.update(kw)
    return c


def mkargs(**kw):
    a=ns.parser().parse_args(['--steps','2','--confirm-top','2','--trace-top','1',
                               '--trace-total','3','--report-every','1'])
    for k,v in kw.items():setattr(a,k,v)
    return a


def fake_scorer(prompt,answers,capture=False):
    start=prompt.index('EXPR: ')+len('EXPR: ')
    end=min([j for needle in ('\nRULE:', '\nDIRECTION:', '\nSCHEME:', '\nAnswer:')
             if (j:=prompt.find(needle,start))>=0] or [len(prompt)])
    expr=prompt[start:end]
    gold=('+' in expr and 'Each block [|]' in prompt)
    logp=[-3.0,0.0 if gold else -3.5,-3.0]
    vectors=np.array([[[j+i,k+j] for k in (0,1)] for j in range(4)
                     for i in (0,)],dtype=np.float32) if capture else None
    return {'logp':logp,'prefix_tokens':len(prompt)//4},vectors


def test_space_dimensions_and_godel_like_encodings():
    assert ns.space_size()==42336
    assert ns.answers(15,'base15')==['0E','10','11']
    assert ns.answers(15,'binary')==['01110','01111','10000']
    assert ns.answers(5,'decimal')==['4','5','6']
    assert ns.tally(0,config())=='EMPTY'
    assert ns.tally(3,config(encoding='brackets',width=2))=='[]'*6
    assert ns.tally(3,config(encoding='alternating',width=2))=='01'*6


def test_every_mathematical_probe_provably_correct():
    for c in [config(),config(layout='spaced',order='expression-first',noise='mixed'),
              config(layout='lines',direction='backward',answer_radix='binary')]:
        for p in ns.probes(c,ns.DISCOVERY_TOTALS,4):
            assert p['left']+p['right']==p['total']
            assert p['left']-p['right']==p['minus_result']!=p['total']
            assert len(set(p['answers']))==3
            assert 'EXPR: ' in p['plus']
            assert 'EXPR: ' in p['minus']
            assert 'Answer:' in p['plus']


def test_order_and_direction_are_not_implicitly_conflated():
    original=ns.build_prompt(7,3,'+',config())
    reversed_=ns.build_prompt(7,3,'+',config(direction='backward'))
    exprfirst=ns.build_prompt(7,3,'+',config(order='expression-first'))
    assert len({original,reversed_,exprfirst})==3
    p1=ns.build_prompt(7,3,'+',config(scaffold='binary-first'))
    p2=ns.build_prompt(7,3,'+',config(scaffold='binary-last'))
    assert p1!=p2 and 'LEFT=011; RIGHT=100' in p1
    assert 'no answer is supplied' in p2
    assert ns.probes(config(direction='backward'),(7,),2)[0]['left']==6
    assert ns.probes(config(direction='forward'),(7,),2)[0]['left']==0


def test_local_and_global_proposals_and_temperature():
    rng=random.Random(32)
    c=config()
    changes=[]
    for _ in range(100):
        new,move=ns.propose(c,rng,0)
        assert move.startswith('local:')
        assert sum(new[k]!=c[k] for k in c)==1
        changes.append(move)
    assert len(set(changes))>=5
    for _ in range(20):
        new,move=ns.propose(c,rng,1)
        assert move=='global'
        ns.valid_config(new)
    assert ns.mh_accept(5,4,0.1,rng)
    assert ns.exchange_accept(5,4,0.3,0.6,rng)
    a=mkargs(steps=10)
    assert ns.temperature(a,0,0)==ns.TEMPERATURES[0]
    assert ns.temperature(a,0,9)<ns.temperature(a,0,0)
    assert ns.temperature(a,5,9)>ns.temperature(a,0,9)


def test_energy_rejects_constant_uninformative_predictions():
    problem=ns.probes(config(),(5,),3)
    def response(a,b):
        return [{'total':p['total'],'plus':{'logp':a,'prefix_tokens':80},
                 'minus':{'logp':b,'prefix_tokens':80}} for p in problem]
    baseline=ns.reduce_observations(response([-2,0,-2],[-2,0,-2]),2048)
    contrast=ns.reduce_observations(response([-2,0,-2],[0,-2,-2]),2048)
    wrong=ns.reduce_observations(response([0,-2,-2],[0,-2,-2]),2048)
    assert contrast['energy']<baseline['energy']<wrong['energy']
    assert contrast['plus_minus_probability_gap']>0


def test_six_chains_run_checkpoint_cache_and_resume(tmp_path):
    a=mkargs(steps=3,exchange_every=1)
    rankings,state=ns.search(tmp_path,a,fake_scorer)
    assert state['step']==3 and len(state['chains'])==6
    assert all(c is not None for c in state['chains'])
    assert len([r for r in state['history'] if r['event']=='proposal'])==18
    assert len([r for r in state['history'] if r['event']=='exchange'])==15
    assert (tmp_path/'search_manifest.json').exists()
    assert (tmp_path/'search_report.json').exists()
    assert len(rankings)>=6
    a.resume=True
    repeat,again=ns.search(tmp_path,a,fake_scorer)
    assert again['history']==state['history']
    assert repeat==rankings
    assert json.loads((tmp_path/'search_report.json').read_text())['steps_completed']==3


def test_resume_after_interrupted_inference_is_deterministic(tmp_path):
    a=mkargs(steps=3,exchange_every=1)
    ref,_=ns.search(tmp_path/'ref',a,fake_scorer)
    calls=[0]
    def crash(prompt,ans,capture=False):
        calls[0]+=1
        if calls[0]==60:
            raise RuntimeError('simulated-power-loss')
        return fake_scorer(prompt,ans,capture)
    with pytest.raises(RuntimeError,match='simulated-power-loss'):
        ns.search(tmp_path/'restart',a,crash)
    a.resume=True
    ranking,state=ns.search(tmp_path/'restart',a,fake_scorer)
    state_ref=json.loads((tmp_path/'ref'/'checkpoint.json').read_text())
    assert state['chains']==state_ref['chains']
    assert state['accepted']==state_ref['accepted']
    assert [r['key'] for r in ranking]==[r['key'] for r in ref]


def test_config_and_cache_drift_rejected(tmp_path):
    a=mkargs()
    ranking,state=ns.search(tmp_path,a,fake_scorer)
    a.resume=True;a.global_rate=0.42
    with pytest.raises(RuntimeError,match='CONFIGURATION_DRIFT'):
        ns.search(tmp_path,a,fake_scorer)
    a.global_rate=0.3
    path=tmp_path/'cache'/f"{ranking[0]['key']}.json"
    obj=json.loads(path.read_text());obj['config']['width']=128
    ns.atom_json(path,obj)
    with pytest.raises(RuntimeError,match='CACHE_IDENTITY_DRIFT'):
        ns.search(tmp_path,a,fake_scorer)


def test_confirm_uses_disjoint_totals_and_records_responses(tmp_path):
    a=mkargs()
    ranking,_=ns.search(tmp_path,a,fake_scorer)
    records=ns.confirmation(tmp_path,a,fake_scorer,ranking)
    assert len(records)==2
    saved=json.loads(next((tmp_path/'confirmation').glob('*.json')).read_text())
    assert saved['heldout_totals']==list(ns.CONFIRM_TOTALS)
    assert set(saved['heldout_totals']).isdisjoint(ns.DISCOVERY_TOTALS)
    assert len(saved['observations'])==len(ns.CONFIRM_TOTALS)*a.positions_per_total
    robust=ns.robustness_confirmation(tmp_path,a,fake_scorer,ranking)
    assert len(robust)==a.robust_top*len(ns.NOISE)
    assert (tmp_path/'noise_robustness_summary.csv').exists()


def test_trajectory_capture_resumes_without_mutation(tmp_path):
    a=mkargs()
    ranking,_=ns.search(tmp_path,a,fake_scorer)
    rows,layers=ns.record_trace(tmp_path,a,fake_scorer,ranking[0])
    assert len(rows)==4 and len(layers)==4
    out=tmp_path/'traces'/ranking[0]['key']
    original=(out/'000.npz').read_bytes()
    again,_=ns.record_trace(tmp_path,a,fake_scorer,ranking[0])
    assert rows==again and (out/'000.npz').read_bytes()==original
    assert (out/'trajectory.csv').exists()
    with np.load(out/'000.npz',allow_pickle=False) as f:
        assert f['vectors'].shape==(4,2,2)


def test_fast_tokenizer_operator_mapping_multiline():
    class CharTokenizer:
        def __call__(self,s,add_special_tokens=True,return_offsets_mapping=True):
            return {'input_ids':[1]+[ord(ch)%127+2 for ch in s],
                    'offset_mapping':[(0,0)]+[(i,i+1) for i in range(len(s))]}
    tok=CharTokenizer()
    for layout in ns.DIMENSIONS['layout']:
        for order in ns.DIMENSIONS['order']:
            prompt=ns.build_prompt(5,3,'+',config(layout=layout,order=order))
            idx=ns.locate_operator_any(tok,prompt)
            assert prompt[idx-1]=='+'


def test_cpu_toy_model_end_to_end_without_hf(tmp_path):
    import torch
    class Tok:
        pad_token_id=0
        eos_token_id=0
        def encode(self,s,add_special_tokens=True):
            ids=[min(ord(ch),127)+2 for ch in s]
            return ([1] if add_special_tokens else [])+ids
        def __call__(self,s,add_special_tokens=True,return_offsets_mapping=True):
            return {'input_ids':self.encode(s,add_special_tokens),
                    'offset_mapping': ([(0,0)] if add_special_tokens else [])+[(i,i+1) for i in range(len(s))]}
    class Model:
        def __call__(self, input_ids,attention_mask,use_cache,output_hidden_states):
            assert not use_cache
            batch,length=input_ids.shape
            logits=torch.zeros((batch,length,132),dtype=torch.float32)
            logits[:,:,ord('5')+2]=0.4
            embeddings=torch.ones((batch,length,8),dtype=torch.float32)
            hidden=(embeddings,embeddings*2,embeddings*3) if output_hidden_states else None
            return SimpleNamespace(logits=logits,hidden_states=hidden)
    scorer=ns.model_scorer(torch,Model(),Tok(),'cpu',2048)
    prompt=ns.build_prompt(5,3,'+',config(layout='lines',order='expression-first'))
    score,vec=scorer(prompt,['4','5','6'],capture=True)
    assert len(score['logp'])==3 and vec.shape==(3,2,8)
    assert score['logp'][1]>score['logp'][0]
    a=mkargs(steps=1,exchange_every=1)
    ranking,state=ns.search(tmp_path,a,scorer)
    assert len(ranking)>=6
    confirms=ns.confirmation(tmp_path,a,scorer,ranking)
    assert len(confirms)==2
    assert len(ns.robustness_confirmation(tmp_path,a,scorer,ranking))==8
    rows,layers=ns.record_trace(tmp_path,a,scorer,ranking[0])
    assert len(rows)==4
    ns.plot_search(tmp_path,ranking,confirms,[(ranking[0]['key'],rows,layers)])
    assert (tmp_path/'ranked_energy.png').exists()
    assert (tmp_path/'heldout_confirmation.png').exists()
    assert (tmp_path/'traces'/ranking[0]['key']/'answer_margin.png').exists()


def test_short_pilot_cli_accepts_default_report_interval():
    a=ns.parser().parse_args(['--device','cuda','--steps','2',
                              '--exchange-every','1','--confirm-top','2',
                              '--trace-top','1'])
    assert a.report_every==5
    ns.validate_args(a)
    ns.validate_args(ns.parser().parse_args(['--steps','1']))


def test_short_pilot_still_rejects_zero_scheduling_intervals():
    for key in ('report_every','exchange_every'):
        a=ns.parser().parse_args(['--steps','2'])
        setattr(a,key,0)
        with pytest.raises(ValueError,match='SEARCH_INVALID_PARAMETER'):
            ns.validate_args(a)


def test_short_pilot_default_reporting_is_emitted_at_completion(tmp_path,capsys):
    a=ns.parser().parse_args(['--steps','2','--confirm-top','2','--trace-top','1'])
    ns.validate_args(a)
    ranking,state=ns.search(tmp_path,a,fake_scorer)
    assert state['step']==2 and len(state['chains'])==6
    messages=[json.loads(line) for line in capsys.readouterr().out.splitlines()]
    progress=[r for r in messages if r.get('event')=='search_progress']
    assert len(progress)==1 and progress[0]['step']==2
    assert len(ranking)>0
