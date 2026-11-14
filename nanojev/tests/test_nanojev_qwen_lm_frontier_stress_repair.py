"""Fast deterministic CPU contract tests for aggressive stress / paired repair."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import nanojev_qwen_lm_frontier_stress_repair as f
from tools import nanojev_qwen_lm_radius_ladder as ladder


def args(tmp_path, **kw):
    defaults = dict(output=str(tmp_path/'numeric_ladder'), frontier_output=None,
        stage1_checkpoint=str(tmp_path/'stage1'/'best.pt'), text_checkpoint=str(tmp_path/'text'/'best.pt'),
        terminal_mode='numeric', radii='0,1,2,3,4', operand_limit=1024, frontier_max_radius=16,
        frontier_steps=3, frontier_check_every=1, frontier_eval_cases=30,
        frontier_train_cases=12, frontier_harvest_cases=5, frontier_repair_center_cap=64, frontier_neighbor_probe_cap=64, frontier_repair_steps=2,
        local_radius=4, max_rung=8, seed=99, lr=0.00005, batch_size=4,
        max_seq=192, max_tokens=256, max_new_tokens=64, eval_batch=8,
        device='cpu')
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_radius_expands_beyond_configured_without_changing_operand_limit(tmp_path):
    a = args(tmp_path)
    assert f._radii(a) == [4,8,16]
    for i,radius in enumerate(f._radii(a)):
        lo=0 if i==0 else f._radii(a)[i-1]+1
        rows = f._deck(radius,lo,30,a,'PROBE-'+str(i))
        assert len(rows)==30
        assert all(lo <= abs(x['residual']) <= radius for x in rows)
        assert all(max(map(abs,x['leaves']))<=a.operand_limit for x in rows)
        assert len({_key(x) for x in rows})==30
        assert all(x['target'].startswith('PROBLEM: ') for x in rows)


def _key(row):return f._key(row)


def test_neighborhood_is_arithmetically_local_and_deduplicated(tmp_path):
    a=args(tmp_path)
    for rung in (0,2,8):
        r=f._deck(8,5,18,a,'NEIGHBOR-'+str(rung))[rung]
        neighbors=f._neighbors(r, 4, a.operand_limit)
        assert neighbors
        assert len({_key(x) for x in neighbors})==len(neighbors)
        for n in neighbors:
            assert n['residual']==r['residual']
            assert n['rung']==r['rung']
            assert n['target']==r['target'] if rung==0 else n['target'].startswith('PROBLEM: ')
            assert len(n['leaves'])==len(r['leaves'])
            assert sum(abs(x-y) for x,y in zip(n['leaves'],r['leaves']))<=8


def test_transitions_are_paired_and_not_new_sample_counts(tmp_path):
    rows=f._deck(4,0,3,args(tmp_path),'TRANS')
    b={_key(r):{'correct':x} for r,x in zip(rows,[True,False,True])}
    a={_key(r):{'correct':x} for r,x in zip(rows,[False,True,True])}
    score=f._transitions(b,a,rows)
    assert (score['gained'],score['lost'],score['net'])==(1,1,0)


def test_frontier_and_repair_resume_checkpoint_isolation(monkeypatch,tmp_path):
    torch=pytest.importorskip('torch')
    a=args(tmp_path)
    (tmp_path/'numeric_ladder').mkdir()
    created=[]

    class MiniModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight=torch.nn.Parameter(torch.tensor(0.,dtype=torch.float32))

    class Zero:
        @staticmethod
        def lora_state(bb): return {'weight':bb.weight.detach().clone()}
        @staticmethod
        def restore_lora(bb,value):
            with torch.no_grad():bb.weight.copy_(value['weight'])

    def model(args,ck):
        bb=MiniModel();created.append(bb)
        return bb,None,Zero,[bb.weight]

    def score(bb,tok,rows,args):
        # Stable, paired pseudo-greedy metric with a real trainable state.
        ret={}
        for r in rows:
            k=_key(r)
            nominal=(int(k[-1],16)%3==0)
            improved=(float(bb.weight.detach())>0.12 and int(k[-2],16)%7==0)
            ret[k]={'correct':bool(nominal or improved),'emitted':r['target'] if nominal or improved else 'WRONG','parsed':None}
        return ret

    seen=[]
    def train(bb,tok,opt,batch,args):
        seen.append([x['id'] for x in batch])
        with torch.no_grad():bb.weight.add_(0.1)
        return 0.2

    def chain_score(bb,tok,rows,args):
        return {_key(r): {'correct':float(bb.weight.detach()) > 0.12,
                         'terminal_correct':float(bb.weight.detach()) > 0.12,
                         'first_wrong_rung': None if float(bb.weight.detach()) > 0.12 else 'T08',
                         'failure_reason':None, 'trace':[], 'terminal_value':None}
                for r in rows}
    monkeypatch.setattr(f,'_chain_score',chain_score)
    monkeypatch.setattr(f,'_load_source',lambda args:({'optimizer':None, 'lora':{'weight':torch.tensor(0.)}},'digest'))
    monkeypatch.setattr(f,'_load_model',model)
    monkeypatch.setattr(f,'_score',score)
    monkeypatch.setattr(ladder,'_train_step',train)
    s=f.run_frontier(a)
    assert s['status']=='STRESSED' and s['phase_step']==3
    out=f._out(a)
    assert (out/'stress.pt').is_file() and (out/'observations.jsonl').is_file()
    assert (out/'chain_evaluation_deck.json').is_file()
    assert (out/'chain_observations.jsonl').is_file()
    assert s['chain_last_metrics']['gained'] > 0
    assert s['chain_last_step'] == 3
    assert (out/'repair_centers.json').is_file()
    assert f.status(a)['repair_ready'] is True
    n=len(seen)
    assert f.run_frontier(a)['status']=='STRESSED'
    assert len(seen)==n  # no repeat training after completion
    result=f.run_repair_ab(a)
    assert result['A_direct']['steps']==2 and result['B_neighborhood']['steps']==2
    assert (out/'repair_ab_report.json').is_file()
    assert all('NEAR-' not in x for row in seen[3:5] for x in row)
    assert any('NEAR-' in x for row in seen[5:] for x in row)
    chkA=torch.load(out/'A_direct.pt',weights_only=True)
    chkB=torch.load(out/'B_neighborhood.pt',weights_only=True)
    assert abs(float(chkA['lora']['weight'])-0.5)<1e-4
    assert abs(float(chkB['lora']['weight'])-0.5)<1e-4
    assert f.run_repair_ab(a)['A_direct']['steps']==2
    assert len(seen)==n+4


def test_invalid_settings_and_no_source_mutation(tmp_path):
    a=args(tmp_path,frontier_max_radius=4)
    with pytest.raises(ValueError,match='MAX_RADIUS'):f._policy(a)
    a=args(tmp_path,terminal_mode='legacy')
    with pytest.raises(ValueError,match='NUMERIC'):f._policy(a)
    a=args(tmp_path)
    assert f._out(a)!=Path(a.output)
    assert Path(a.output) not in f._out(a).parents


def test_cli_dispatch_to_frontier_and_repair(monkeypatch,tmp_path):
    from tools import nanojev_qwen_lm_radius_ladder as m
    sentinel=object()
    monkeypatch.setattr(f,'run_frontier',lambda args: ('frontier',args.stage))
    monkeypatch.setattr(f,'run_repair_ab',lambda args: ('repair',args.stage))
    assert m.main(['--stage','run','--frontier'])==('frontier','run')
    assert m.main(['--stage','repair-ab'])==('repair','repair-ab')


def test_rung_specific_paired_accuracy_and_regression(tmp_path):
    a=args(tmp_path)
    rows=[]
    for r in range(9):
        row=ladder._case(r, tuple([2]+[0]*(r+1)), f'T{r:02d}', 'numeric')
        row.update(radius=4, band='test')
        rows.append(row)
    before={_key(r):{'correct': r['rung'] % 2 == 0} for r in rows}
    after={_key(r):{'correct': r['rung'] != 2} for r in rows}
    measured=f._transitions(before,after,rows)
    assert measured['tested']==9 and len(measured['by_rung'])==9
    assert measured['by_rung']['T02']=={
        'tested':1,'correct_before':1,'correct_now':0,'gained':0,'lost':1,'net':-1}
    assert measured['by_rung']['T07']['gained']==1
    assert sum(s['net'] for s in measured['by_rung'].values())==measured['net']


def test_true_full_chain_uses_only_model_emissions_and_reports_final(monkeypatch,tmp_path):
    a=args(tmp_path)
    row=ladder._case(8,(22,-19,6,-4,2,0,-3,5,-2,1),'T08-chain','numeric')
    row.update(radius=16,band='test')
    mapping=dict(zip(row['complete_chain'],row['complete_chain'][1:]))
    prompted=[]
    def greedy(bb,tok,sources,*unused):
        prompted.extend(sources)
        return [mapping[s] for s in sources]
    monkeypatch.setattr(ladder,'_greedy',greedy)
    result=f._chain_score(None,None,[row],a)[_key(row)]
    assert len(prompted)==9 and prompted==row['complete_chain'][:-1]
    assert result['correct'] and result['terminal_correct']
    assert result['terminal_value']==sum(row['leaves'])
    assert [step['rung'] for step in result['trace']]==[
        'T08','T07','T06','T05','T04','T03','T02','T01','T00']
    assert all(step['exact'] for step in result['trace'])


def test_wrong_intermediate_may_be_numerically_right_not_exact(monkeypatch,tmp_path):
    a=args(tmp_path)
    row=ladder._case(2,(3,-2,5,-1),'T02-chain','numeric')
    row.update(radius=8,band='test')
    altered='PROBLEM: (2+4)+(-1)'
    mapping={row['source']:altered,
             altered:'PROBLEM: 6+(-1)',
             'PROBLEM: 6+(-1)':'PROBLEM: 5'}
    seen=[]
    def greedy(bb,tok,sources,*unused):
        seen.extend(sources)
        return [mapping[s] for s in sources]
    monkeypatch.setattr(ladder,'_greedy',greedy)
    result=f._chain_score(None,None,[row],a)[_key(row)]
    assert seen==[row['source'],altered,'PROBLEM: 6+(-1)']
    assert result['terminal_correct'] is True
    assert result['correct'] is False
    assert result['first_wrong_rung']=='T02'
    assert result['completed_hops']==3


def test_malformed_or_nonprogressing_generated_packet_is_not_fed_back(monkeypatch,tmp_path):
    a=args(tmp_path)
    row=ladder._case(8,(22,-19,6,-4,2,0,-3,5,-2,1),'T08-chain','numeric')
    row.update(radius=16,band='test')
    seen=[]
    def greedy(bb,tok,sources,*unused):
        seen.extend(sources)
        return ['PROBLEM: 7+8+9+10+11+12+13+14+15+16']
    monkeypatch.setattr(ladder,'_greedy',greedy)
    result=f._chain_score(None,None,[row],a)[_key(row)]
    assert seen==[row['source']]
    assert result['first_wrong_rung']=='T08'
    assert result['failure_reason']=='invalid_or_nonprogressing_packet'
    assert result['correct'] is False


def test_chain_metrics_report_first_error_locus_and_terminal_vs_exact(tmp_path):
    row=ladder._case(2,(3,-2,5,-1),'T02-chain','numeric')
    row.update(radius=8,band='test')
    before={_key(row):{'correct':False,'terminal_correct':False,'first_wrong_rung':'T02'}}
    after={_key(row):{'correct':False,'terminal_correct':True,'first_wrong_rung':'T01'}}
    m=f._chain_metrics(before,after,[row])
    assert (m['gained'],m['lost'],m['net'])==(0,0,0)
    assert (m['terminal_gained'],m['terminal_lost'],m['terminal_net'])==(1,0,1)
    assert m['first_failure_by_rung']=={'T01':1}


def test_backfill_chain_baseline_resumes_old_frontier_without_changing_weights(monkeypatch,tmp_path):
    torch=pytest.importorskip('torch')
    a=args(tmp_path)
    class BB:
        def __init__(self):self.weight=torch.tensor(0.4)
        def eval(self):return self
    class Zero:
        @staticmethod
        def lora_state(bb):return {'weight':bb.weight.clone()}
        @staticmethod
        def restore_lora(bb,state):bb.weight.copy_(state['weight'])
    bb=BB()
    rows=f._evaluation_deck(a,f._radii(a))
    chain=[r for r in rows if r['rung']==a.max_rung]
    assert chain
    def score(bb,tok,deck,args):
        correct=bool(bb.weight.item()>0.2)
        return {_key(r): {'correct':correct,'terminal_correct':correct,
             'first_wrong_rung':None if correct else 'T08',
             'failure_reason':None,'trace':[],'terminal_value':None} for r in deck}
    monkeypatch.setattr(f,'_chain_score',score)
    state={'phase_step':300}
    baseline=f._initialize_chain_baseline(state,bb,None,Zero,
        {'lora':{'weight':torch.tensor(0.)}},rows,a,tmp_path)
    assert torch.equal(bb.weight,torch.tensor(0.4))
    assert all(not x['correct'] for x in state['chain_baseline'].values())
    f._check_chain(state,bb,None,baseline,a,tmp_path)
    assert state['chain_last_step']==300
    assert state['chain_last_metrics']['gained']==len(baseline)
    # A second load reuses the identical baseline without resetting the model.
    assert f._initialize_chain_baseline(state,bb,None,Zero,
        {'lora':{'weight':torch.tensor(0.)}},rows,a,tmp_path)==baseline
    assert torch.equal(bb.weight,torch.tensor(0.4))
