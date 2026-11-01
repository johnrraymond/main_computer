"""No-GPU contracts for opt-in Balanced Hawks and Doves zero-line training."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import nanojev_qwen_hdb_frontier as h
from tools import nanojev_qwen_competency_diagnostic as c
from tools import nanojev_qwen_lm_frontier_stress_repair as f
from tools import nanojev_qwen_lm_radius_ladder as ladder


def args(tmp_path, **more):
    data=dict(output=str(tmp_path/'numeric'),frontier_output=None,hdb_output=None,
        stage1_checkpoint=str(tmp_path/'stage1'/'best.pt'),
        text_checkpoint=str(tmp_path/'text'/'best.pt'),
        hdb_steps=18,hdb_check_every=9,hdb_max_offset=8,
        hdb_t_replay_per_batch=1,hdb_max_seq=768,hdb_max_tokens=1024,
        hdb_max_new_tokens=12,competency_hdb_cases=9,
        competency_legacy_decode=False,competency_progress_every=100,
        terminal_mode='numeric',max_rung=8,batch_size=4,eval_batch=32,
        device='cpu',seed=435,lr=0.00005,radii='0,1,2,3,4',
        operand_limit=1024,frontier_max_radius=16,frontier_steps=18,
        frontier_check_every=9,frontier_eval_cases=18,frontier_train_cases=18,
        max_seq=192,max_tokens=256,max_new_tokens=64,
        frontier_neighbor_probe_cap=30,frontier_repair_center_cap=30,
        frontier_harvest_cases=8,frontier_repair_steps=10,local_radius=4)
    data.update(more)
    return SimpleNamespace(**data)


def test_all_hdb_tiers_have_true_matrix_dependent_zero_lines():
    for tier in range(9):
        for variant in (*h.EVAL_VARIANTS,(1,2,3),(3,3,1),(2,1,1)):
            a,b,d=[h._row(tier,k,variant,label='x') for k in (-1,0,1)]
            assert (a['target'],b['target'],d['target'])==('DOVE','TIE','HAWK')
            assert (a['delta'],b['delta'],d['delta'])==(
                -(variant[1]+variant[2]),0,variant[1]+variant[2])
            assert all(int(r['x'])-int(r['y'])==r['delta'] for r in (a,b,d))
            assert all('Return only HAWK, DOVE, or TIE.' in r['source'] for r in (a,b,d))
            assert all('Known payoff values per encounter:' in r['source'] for r in (a,b,d))


def test_training_sampling_zero_sign_balance_tier_coverage_replay_and_disjointness(tmp_path):
    a=args(tmp_path);p=h._policy(a)
    pool=[{'id':'replay','source':'PROBLEM: 3+4','target':'PROBLEM: 7'}]
    eval_sources={r['source'] for r in h._evaluation(p)}
    old_sources={r['source'] for r in c._hdb_deck(c._settings(SimpleNamespace(
        max_rung=8,terminal_mode='numeric',operand_limit=1024,seed=435,
        radii='0,1,2,3,4',frontier_output=None,output='/tmp/unused',
        frontier_max_radius=16,frontier_steps=18,frontier_check_every=9,
        frontier_eval_cases=18,frontier_train_cases=18,batch_size=4,
        competency_per_rung=8,competency_hdb_cases=9,competency_chain_cases=6,
        competency_max_tokens=512,competency_max_new_tokens=24)))}
    all_tiers=set()
    for step in range(72):
        b=h._batch(a,step,p,pool)
        assert len(b)==4
        assert b[-1]['id']=='replay'
        x=b[:3]
        assert len({r['tier'] for r in x})==1
        assert {r['target'] for r in x}=={'HAWK','DOVE','TIE'}
        assert not {r['source'] for r in x}&eval_sources
        assert not {r['source'] for r in x}&old_sources
        assert b==h._batch(a,step,p,pool)
        all_tiers.add(x[0]['tier'])
    assert all_tiers=={f'HDB{i:02d}' for i in range(9)}
    assert h._offset_limit(0,900,64)==1
    assert h._offset_limit(899,900,64)==64


def test_eval_deck_has_sides_and_reserved_matrices(tmp_path):
    p=h._policy(args(tmp_path))
    d=h._evaluation(p)
    assert len(d)==9*3*9
    assert len({r['source'] for r in d})==len(d)
    for tier in range(9):
        r=[x for x in d if x['tier']==f'HDB{tier:02d}']
        assert {x['zero_side'] for x in r}=={'negative','positive','zero'}
        assert sum(x['target']=='TIE' for x in r)==3
    with pytest.raises(ValueError,match='HDB_INVALID_TRAINING_POLICY'):
        h._policy(args(tmp_path,hdb_t_replay_per_batch=2))


def test_paired_hdb_metrics_and_exposures(tmp_path):
    rows=h._evaluation(h._policy(args(tmp_path)))[:3]
    keys=[c._digest(x['source']) for x in rows]
    b={k:{'correct':v} for k,v in zip(keys,[False,True,False])}
    n={k:{'correct':v} for k,v in zip(keys,[True,False,True])}
    m=h._metrics(rows,b,n)['total']
    assert (m['gained'],m['lost'],m['net'])==(2,1,1)
    state={'exposures':h._exposures(),'t_replay_examples':0}
    a=args(tmp_path);policy=h._policy(a)
    batch=h._batch(a,0,policy,[{'id':'x','source':'PROBLEM: 1+2','target':'PROBLEM: 3'}])
    h._add_exposures(state,batch)
    assert state['t_replay_examples']==1
    assert state['exposures']['HDB00']['examples']==3
    assert state['exposures']['HDB00']['steps']==1
    assert all(state['exposures']['HDB00'][x]==1 for x in ('negative','zero','positive'))


def test_forked_hdb_training_and_resume_with_cpu_mocks(monkeypatch,tmp_path):
    torch=pytest.importorskip('torch')
    a=args(tmp_path,hdb_steps=2,hdb_check_every=1)
    source=h._parent(a);source.parent.mkdir(parents=True)
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__();self.weight=torch.nn.Parameter(torch.tensor(0.))
    class Zero:
        @staticmethod
        def lora_state(bb):return {'weight':bb.weight.detach().clone()}
        @staticmethod
        def restore_lora(bb,state):
            with torch.no_grad():bb.weight.copy_(state['weight'])
    parent={'schema':f.SCHEMA,'state':{'status':'STRESSED',
        'phase_step':a.frontier_steps,'policy':f._policy(a)},
        'lora':{'weight':torch.tensor(0.)},'optimizer':None}
    torch.save(parent,source)
    monkeypatch.setattr(h,'_model',lambda args,parent:(Model(),None,Zero,[Model().weight]))
    # Use the same trainable parameter in the mocked optimizer/model.
    def model(args,parent):
        m=Model();return m,None,Zero,[m.weight]
    monkeypatch.setattr(h,'_model',model)
    monkeypatch.setattr(h,'_score_hdb',lambda m,t,r,a,p,**kw:{
        c._digest(x['source']):{'correct':float(m.weight)>0.1,'emitted':'HAWK'} for x in r})
    monkeypatch.setattr(f,'_score',lambda m,t,r,a,**kw:{
        f._key(x):{'correct':float(m.weight)<0.2,'emitted':x['target']} for x in r})
    monkeypatch.setattr(h,'_replay',lambda a:[{'id':'ar','source':'PROBLEM: 1+2',
                                             'target':'PROBLEM: 3'}])
    seen=[]
    def train(m,t,opt,rows,args):
        seen.append(rows)
        with torch.no_grad():m.weight.add_(0.11)
        return .12
    monkeypatch.setattr(ladder,'_train_step',train)
    s=h.run(a)
    assert s['phase_step']==2 and s['status']=='COMPLETE'
    assert len(seen)==2
    assert all({r['target'] for r in batch[:3]}=={'HAWK','DOVE','TIE'} for batch in seen)
    assert int(torch.load(source,weights_only=True)['lora']['weight'])==0
    folder=h._output(a)
    assert (folder/'working.pt').is_file() and (folder/'hdb_report.json').is_file()
    report=json.loads((folder/'hdb_report.json').read_text())
    assert report['hdb_from_start']['total']['gained']==len(h._evaluation(h._policy(a)))
    assert report['t_retention_from_start']['lost']>0
    assert report['training_exposure_cumulative']['HDB00']['steps']==1
    assert h.run(a)['phase_step']==2
    assert len(seen)==2
    assert h.run(a,audit_only=True)['step']==2
