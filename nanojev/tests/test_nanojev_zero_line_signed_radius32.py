"""Contracts + CPU end-to-end tests for the radius32 research campaign."""
import copy
import json
import random
from pathlib import Path

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_zero_line_signed_radius32 as r
from tools import nanojev_zero_line_signed_radius16 as previous


def test_full_integer_radius_and_endpoint():
    assert r.RESIDUALS==tuple(range(-32,33)) and len(r.RESIDUALS)==65
    for d in r.RESIDUALS:
        for swap in (False,True):
            pair=r.pair_for_max(2_000_000,d,swap)
            assert sum(pair)==d
            assert max(map(abs,pair))==2_000_000
            assert all(abs(x)<=2_000_000 for x in pair)
    assert r.pair_for_max(31,32) is None
    assert r.pair_for_max(300,0,False,-1)==(-300,300)


def test_magnitude_family_quarantine_and_endpoint():
    assert r.fold(2_000_000)=='endpoint'
    for m in range(32,500):
        if previous.magnitude_fold(m,r.PRIOR_SEED)=='test':
            assert r.fold(m)=='quarantine'
        else:
            assert r.fold(m) in ('train','dev','test')
    assert {r.fold(m) for m in range(32,256)}>=set(['train','dev','test','quarantine'])


def test_all_845_cells_three_splits_and_independent_dev_views():
    sets={sp:r.build_matrix(sp,r.DEFAULT_SEED,n,v) for sp,n,v in
          [('train',3,1),('dev',6,2),('test',12,1)]}
    seen={}
    for split,views in sets.items():
        ss=[]
        for rows in views:
            assert len(set((band,d) for _,_,d,band,_ in rows))==845
            mags=set()
            for a,b,d,band,m in rows:
                assert a+b==d
                assert 0<=m<=r.MAX_ABS
                assert max(abs(a),abs(b))==m
                assert r.fold(m)==split
                mags.add(m)
            assert len(mags)==(3 if split=='train' else 6 if split=='dev' else 12)*13
            ss.append(mags)
        if split=='dev':assert ss[0].isdisjoint(ss[1])
        seen[split]=set.union(*ss)
    for a in seen:
        for b in seen:
            if a!=b:assert not seen[a]&seen[b]
    assert 2_000_000 not in set.union(*seen.values())
    assert len(sets['test'][0])==20280


def test_sampler_covers_all_65_and_13_bands():
    rng=random.Random(13)
    cur=0;cells=set()
    for _ in range(13):
        cards,cur=r.train_cards(rng,65,r.DEFAULT_SEED,[1.]*13,cur)
        assert {d for a,b,d,band,m in cards}==set(r.RESIDUALS)
        assert all(r.fold(m)=='train' for a,b,d,band,m in cards)
        cells.update((d,band) for a,b,d,band,m in cards)
    assert cells==set((d,band) for d in r.RESIDUALS for band in range(13))
    cards,cur=r.train_cards(rng,130,r.DEFAULT_SEED,[1.]*13,0)
    assert {sum(row[2]==d for row in cards) for d in r.RESIDUALS}=={2}


def test_canonical_zero_and_nonzero_exact_decoding_no_oracle():
    class Dummy(torch.nn.Module):
        def forward(self,x):
            assert x.shape[-1]==2
            out={'sign':torch.full((len(x),3),-10.), 'digits':torch.full((len(x),7,10),-10.)}
            out['sign'][:,1]=10.;out['digits'][:,:,0]=10.
            return out
    rows=[(400,-400,0,3,400),(400,-399,1,3,400),(-400,400,0,3,400)]
    s=r.assess(Dummy(),rows,'cpu')
    assert s['zero_errors']==0 and s['nonzero_errors']==1
    assert s['every_cell_populated']==False
    assert s['counterexamples'][0]['prediction']==0
    assert s['counterexamples'][0]['delta']==1


def test_strict_zero_and_bounded_nonzero_policy():
    class A: max_cell_errors=1;max_nonzero_error_rate=.001
    d={'every_cell_populated':True,'zero_errors':0,'max_cell_errors':1,
       'nonzero_error_rate':.0008}
    assert r.passes(d,A)
    assert not r.passes({**d,'zero_errors':1},A)
    assert not r.passes({**d,'max_cell_errors':2},A)
    assert not r.passes({**d,'nonzero_error_rate':.00101},A)
    assert not r.passes({**d,'every_cell_populated':False},A)


def test_plan_without_source_checkpoint():
    args=r.parser().parse_args(['--phase','plan'])
    p=r.run(args)
    assert p['total_cells']==845 and p['residual_count']==65


def fake_parent(tmp_path,monkeypatch):
    model=AdditionNet('digit_tokens',8,2,1,1,42)
    src=tmp_path/'certified.pt';src.write_bytes(b'certified')
    r16=tmp_path/'r16';r16.mkdir()
    latest=r16/'latest.pt'
    config=r16/'config.json'
    result=r16/'result.json'
    seal=r16/'test_opened.json'
    zero_prom=tmp_path/'zero_promoted.pt';zero_prom.write_bytes(b'fake zero promoted')
    zero_meta={'zero_promoted_sha256':r.sha256(zero_prom)}
    config.write_text(json.dumps({'schema':previous.SCHEMA,
        'code_sha256':r.sha256(previous.__file__), 'source':zero_meta}))
    torch.save({'schema':previous.SCHEMA+'.checkpoint','model':copy.deepcopy(model.state_dict()),
        'step':8250,'streak':3},latest)
    matrix={'1572864-2000000':{'-16':{'total':16,'correct':15}}}
    result.write_text(json.dumps({'status':'heldout_refused','last_step':8250,
        'dev':{'passes':True}, 'historical_full':{'groups':{'all':{'passed':65025}}},
        'previous_zero_retention':{'dev':{'passes':True},'test':{'passes':True}},
        'holdout_once':{'cards':6864,'matrix':matrix}}))
    seal.write_text('{}')
    def zero_source(*_):
        m=AdditionNet('digit_tokens',8,2,1,1,42)
        m.load_state_dict(copy.deepcopy(model.state_dict()))
        return m,zero_meta
    def loader(_ladder,device):
        m=AdditionNet('digit_tokens',8,2,1,1,42).to(device)
        m.load_state_dict(copy.deepcopy(model.state_dict()))
        return m,{'checkpoint_sha256':r.sha256(src)}
    monkeypatch.setattr(r,'verified_zero_source',zero_source)
    monkeypatch.setattr(r,'load_certified_model',loader)
    (tmp_path/'zero').mkdir()
    (tmp_path/'zero'/'config.json').write_text(json.dumps({'settings':{'seed':20261025}}))
    return r16,latest,config,result,seal,src,zero_prom


def test_one_error_parent_validated_and_not_falsely_promoted(tmp_path,monkeypatch):
    r16,*files=fake_parent(tmp_path,monkeypatch)
    model,p=r.research_parent('ladder',tmp_path/'zero',r16,'cpu')
    assert p['status'].startswith('experimental_') and p['r16_holdout_errors']==1
    assert not (r16/'promoted.pt').exists()
    assert all(not q.requires_grad for q in model.parameters())
    report=json.loads((r16/'result.json').read_text())
    report['holdout_once']['matrix']['1572864-2000000']['-16']['correct']=14
    (r16/'result.json').write_text(json.dumps(report))
    with pytest.raises(RuntimeError,match='ONE_ERROR'):
        r.research_parent('ladder',tmp_path/'zero',r16,'cpu')


def test_parent_rejects_historical_regression(tmp_path,monkeypatch):
    r16,*_=fake_parent(tmp_path,monkeypatch)
    report=json.loads((r16/'result.json').read_text())
    report['historical_full']['groups']['all']['passed']=65024
    (r16/'result.json').write_text(json.dumps(report))
    with pytest.raises(RuntimeError,match='UNVERIFIED'):
        r.research_parent('ladder',tmp_path/'zero',r16,'cpu')


def setup_run(tmp_path,monkeypatch):
    r16,*files=fake_parent(tmp_path,monkeypatch)
    a=r.parser().parse_args(['--device','cpu','--ladder-output','ladder',
        '--zero-only-output',str(tmp_path/'zero'),'--r16-output',str(r16),
        '--output',str(tmp_path/'out'), '--max-steps','1','--probe-every','1',
        '--batch-size','65','--legacy-batch-size','4','--legacy-probe-cards','8',
        '--dev-anchors','1','--dev-views','2','--test-anchors','1',
        '--train-probe-anchors','1','--parent-anchor-count','1',
        '--eval-batch-size','256','--cpu-threads','2','--stability-probes','2'])
    # Keep generator logic checked separately, use small fixed real cards to
    # exercise checkpoint/training/promote paths in seconds.
    rows=[(100,-100,0,0,100),(100,-99,1,0,100),(-100,99,-1,0,100)]
    def small_matrix(split,seed,anchors,views=1):return [list(rows) for _ in range(views)]
    monkeypatch.setattr(r,'build_matrix',small_matrix)
    monkeypatch.setattr(r,'prior_matrix',lambda *a:list(rows))
    monkeypatch.setattr(r,'previous_probe',lambda *a:[(100,-100,True,0,100)])
    return a,files


def test_real_cpu_training_checkpoint_and_resume(tmp_path,monkeypatch):
    a,files=setup_run(tmp_path,monkeypatch)
    hashes=[r.sha256(f) for f in files]
    report=r.run(a)
    assert report['status']=='budget_exhausted_no_promotion'
    assert (Path(a.output)/'latest.pt').is_file()
    assert not (Path(a.output)/'promoted.pt').exists()
    state=torch.load(Path(a.output)/'latest.pt',weights_only=True)
    assert state['step']==1
    a.resume=True;a.max_steps=2
    out=r.run(a)
    assert out['last_step']==2
    assert [r.sha256(f) for f in files]==hashes


def test_resume_refuses_changed_settings_and_test_seal(tmp_path,monkeypatch):
    a,_=setup_run(tmp_path,monkeypatch)
    r.run(a)
    a.resume=True;a.lr*=2
    with pytest.raises(RuntimeError,match='DRIFT'):r.run(a)
    a.lr/=2
    (Path(a.output)/'test_opened.json').write_text('{}')
    with pytest.raises(RuntimeError,match='SEALED'):r.run(a)


def perfect_assess(*_):
    return {'cards':1000,'errors':0,'zero_errors':0,'zero_cards':10,
        'nonzero_errors':0,'nonzero_cards':990,'nonzero_error_rate':0.,
        'max_cell_errors':0,'min_cell_accuracy':1.,'every_cell_populated':True,
        'matrix':{f'{lo}-{hi}':{str(d):{'errors':0,'total':1,'accuracy':1.}
                           for d in r.RESIDUALS} for lo,hi in r.BANDS}}


def promote_setup(tmp_path,monkeypatch):
    a,files=setup_run(tmp_path,monkeypatch)
    a.max_steps=2
    monkeypatch.setattr(r,'assess',perfect_assess)
    monkeypatch.setattr(r,'prior_assess',lambda *_:{'passes':True,'worst_cell_exact':1.})
    monkeypatch.setattr(r,'zero_classify',lambda *_:{'passes':True,'min_zero':1.})
    def good(_m,rows,*_):
        n=len(rows['y'])
        return {'groups':{'all':{'passed':n,'accuracy':1.}},'false_zero':0}
    monkeypatch.setattr(r,'legacy_evaluate',good)
    return a,files


def test_successful_promotion_reports_status_and_sources(tmp_path,monkeypatch):
    a,files=promote_setup(tmp_path,monkeypatch)
    hashes=[r.sha256(f) for f in files]
    out=r.run(a)
    assert out['status']=='promoted'
    p=torch.load(Path(a.output)/'promoted.pt',weights_only=True)
    assert p['schema']==r.SCHEMA+'.promoted'
    assert p['historical_exact']==65025
    assert p['certification_scope'].startswith('sampled experimental')
    assert [r.sha256(f) for f in files]==hashes
    a.resume=True;a.max_steps=3
    with pytest.raises(RuntimeError,match='SEALED'):r.run(a)


def test_test_zero_failure_refuses_promotion_and_seals(tmp_path,monkeypatch):
    a,_=promote_setup(tmp_path,monkeypatch)
    def test_fails(model,rows,device,batch_size=256,max_errors_saved=48):
        d=perfect_assess()
        if rows and rows[0][4]==2_000_000:
            d['zero_errors']=1
        return d
    # Synthetic small matrices are identical in all splits; sentinel is distinct.
    monkeypatch.setattr(r,'assess',test_fails)
    out=r.run(a)
    assert out['status']=='heldout_refused'
    assert (Path(a.output)/'test_opened.json').exists()
    assert not (Path(a.output)/'promoted.pt').exists()


def test_one_nonzero_holdout_error_permitted_with_zero_exact(tmp_path,monkeypatch):
    a,_=promote_setup(tmp_path,monkeypatch)
    # The staged synthetic holdout is not differentiated by rows; use call
    # count to inject a single <0.1% nonzero error into the sealed test only.
    main=r.assess
    calls=[0]
    def allowed(*args,**kwargs):
        calls[0]+=1
        d=main(*args,**kwargs)
        if len(args)>1 and args[1] and args[1][0][4]==2_000_000:
            return d
        # Holdout after 2 stability probes is the last non-endpoint call.
        # Identify by seal file which is only written before sealed evaluation.
        if (Path(a.output)/'test_opened.json').exists():
            d['errors']=1;d['nonzero_errors']=1;d['nonzero_error_rate']=1/990
            d['max_cell_errors']=1
        return d
    # 1/990 is > 0.001: relax just enough for this synthetic miniature to
    # test the policy, not the real default gate.
    a.max_nonzero_error_rate=.0011
    monkeypatch.setattr(r,'assess',allowed)
    out=r.run(a)
    assert out['status']=='promoted'
    assert out['holdout_once']['nonzero_errors']==1
    assert out['holdout_once']['zero_errors']==0
