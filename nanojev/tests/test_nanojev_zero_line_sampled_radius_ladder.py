"""Contracts and CPU smoke/regression tests for the sampled backbone ladder."""
import copy
import hashlib
import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_zero_line_sampled_radius_ladder as r


def test_geometric_plan_reaches_entire_two_operand_sum_range():
    assert r.planned_radii() == [64,128,256,512,1024,2048,4096,8192,16384,
         32768,65536,131072,262144,524288,1048576,2097152,4000000]
    assert r.planned_radii(300)==[64,128,256,300]
    assert r.run(r.parser().parse_args(['--phase','plan']))['stages']==17
    with pytest.raises(ValueError):r.planned_radii(4000001)


@pytest.mark.parametrize('m,d',[(2_000_000,0),(2_000_000,1),
    (2_000_000,-32),(2_000_000,32),(2_000_000,2_000_000),
    (2_000_000,-3_999_999),(2_000_000,4_000_000),
    (2_000_000,-4_000_000),(100,200),(32,-64)])
def test_bounded_pairs(m,d):
    for swap in (False,True):
        a,b=r.pair_for(m,d,swap)
        assert a+b==d
        assert max(abs(a),abs(b))==m
        assert max(abs(a),abs(b))<=2_000_000
    assert r.pair_for(20,60) is None
    assert r.pair_for(2_000_000,4_000_001) is None


def test_zero_both_orientations():
    assert r.pair_for(2000,0,orientation=1)==(2000,-2000)
    assert r.pair_for(2000,0,orientation=-1)==(-2000,2000)


def test_parent_sealed_magnitude_families_are_not_trained_on():
    statuses=set()
    for m in range(32,1000):
        f=r.stable_fold(m)
        statuses.add(f)
        if r.r32_fold(m) in ('test','quarantine','endpoint'):
            assert f=='quarantine'
    assert statuses=={'train','dev','test','quarantine'}
    assert r.stable_fold(2_000_000)=='quarantine'


def test_card_generator_no_interpolation_from_answer_at_inference():
    for radius in (64,1024,4_000_000):
        rng=random.Random(4)
        cards,cursor=r.train_cards(rng,radius,r.DEFAULT_SEED,128,[1.]*len(r.BANDS),0)
        assert cursor==128
        assert any(abs(d)>32 for _,_,d,_,_ in cards)
        assert any(d==0 for _,_,d,_,_ in cards)
        assert any(0<abs(d)<=32 for _,_,d,_,_ in cards)
        for a,b,d,band,m in cards:
            assert a+b==d and abs(d)<=radius and m==max(abs(a),abs(b))
            assert r.stable_fold(m)=='train'
    assert r.pair_for(2_000_000,4_000_000)==(2_000_000,2_000_000)


def test_long_run_training_visits_all_inner_signed_values():
    rng=random.Random(42); cursor=0; seen=set(); bands=set()
    for _ in range(32):
        rows,cursor=r.train_cards(rng,64,r.DEFAULT_SEED,128,[1.]*13,cursor)
        seen.update(d for a,b,d,band,m in rows if 0<abs(d)<=32)
        bands.update(band for a,b,d,band,m in rows)
    assert seen==set(range(-32,0))|set(range(1,33))
    assert bands==set(range(13))


def test_matrix_has_separate_disjoint_magnitude_families():
    views=r.evaluation_cards(1024,'dev',r.DEFAULT_SEED,1024,2,2,16)
    seen=[]
    for rows in views:
        mags={m for _,_,_,_,m in rows}
        assert {band for _,_,_,band,_ in rows}==set(range(13))
        assert all(r.stable_fold(m)=='dev' for m in mags)
        assert all(a+b==d and max(abs(a),abs(b))==m for a,b,d,_,m in rows)
        assert all(abs(d)<=1024 for a,b,d,_,m in rows)
        seen.append(mags)
    assert not seen[0]&seen[1]
    test=r.evaluation_cards(1024,'test',r.DEFAULT_SEED,1024,8,1,16)[0]
    assert all(r.stable_fold(m)=='test' for a,b,d,_,m in test)
    assert not (seen[0]|seen[1])&{m for a,b,d,_,m in test}


def test_large_radius_sampling_is_stratified_not_enumerated():
    rows=r.evaluation_cards(4_000_000,'dev',r.DEFAULT_SEED,4_000_000,2,1,32)[0]
    assert max(abs(d) for _,_,d,_,_ in rows)>2_000_000
    assert any(abs(d)>1_000_000 and a*b>0 for a,b,d,_,_ in rows)
    assert any(d==0 for _,_,d,_,_ in rows)


def test_metric_treats_zero_exact_inner_exact_far_five_percent():
    rows=[(300,-300,0,3,300),(300,-299,1,3,300),(300,-299,1,3,300),
          (300,200,500,3,300),(-300,-200,-500,3,300)]
    # repeated cards are fine for this isolated metric test
    x=r.accuracy_report_from_predictions(rows,[0,2,1,525,-474])
    assert x['classes']['zero']['errors']==0
    assert x['classes']['inner']['errors']==1
    assert x['classes']['outer']['correct']==1
    assert x['classes']['sign']['errors']==0
    y=r.accuracy_report_from_predictions(rows,[1,1,1,-525,-500])
    assert y['classes']['zero']['errors']==1
    assert y['classes']['outer']['errors']==1
    assert y['classes']['sign']['errors']==1
    z=r.accuracy_report_from_predictions(rows,[0,1,1,500,-500],[False]*5)
    assert z['classes']['zero']['errors']==1


def test_zero_exact_and_outer_gates_do_not_have_an_oracle():
    opts=SimpleNamespace(max_inner_error_rate=.001,min_outer_5pct=.9,
        min_bucket_5pct=.8,min_bucket_cards=16,min_sign_accuracy=.99)
    rows=[(100,-100,0,0,100),(100,-99,1,0,100)] + [(200,200,400,0,200)]*20
    x=r.accuracy_report_from_predictions(rows,[0,1]+[400]*20)
    assert r.gates(x,opts)
    assert not r.gates(r.accuracy_report_from_predictions(rows,[1,1]+[400]*20),opts)
    assert not r.gates(r.accuracy_report_from_predictions(rows,[0,1]+[200]*20),opts)


def test_model_sees_only_operands():
    class InputChecker(torch.nn.Module):
        def forward(self,x):
            assert x.shape==(2,2)
            assert x.tolist()==[[400,-400],[400,-399]]
            signs=torch.full((2,3),-9.);signs[:,1]=9.
            digits=torch.full((2,7,10),-9.);digits[:,:,0]=9.
            return {'sign':signs,'digits':digits}
    r.assess(InputChecker(),[(400,-400,0,3,400),(400,-399,1,3,400)],'cpu')


def fake_parent_files(tmp_path,monkeypatch):
    torch.manual_seed(77)
    model=AdditionNet('digit_tokens',8,2,1,1,77)
    old=tmp_path/'r32';old.mkdir()
    r16=tmp_path/'r16';r16.mkdir()
    z=tmp_path/'zero';z.mkdir()
    zp=z/'promoted.pt';zp.write_bytes(b'certified zero checkpoint digest')
    r16p=r16/'latest.pt';r16p.write_bytes(b'r16 checkpoint digest')
    cfg={'schema':r.PARENT_SCHEMA,'code_sha256':r.sha256(r.nanojev_zero_line_signed_radius32.__file__),
        'source':{'r16_latest_sha256':r.sha256(r16p),'zero_origin':{'zero_promoted_sha256':r.sha256(zp)}},
        'settings':{'seed':20261232}}
    (old/'config.json').write_text(json.dumps(cfg))
    result={'status':'heldout_refused','last_step':2750,
        'holdout_once':{'cards':20280,'errors':10,'zero_errors':0,'max_cell_errors':2},
        'historical_full':{'groups':{'all':{'passed':65025}}},
        'r16_historical_dev':{'passes':True},
        'previous_zero_retention':{'dev':{'passes':True},'test':{'passes':True}}}
    (old/'result.json').write_text(json.dumps(result))
    torch.save({'schema':r.PARENT_SCHEMA+'.checkpoint','model':copy.deepcopy(model.state_dict()),
                'step':2750,'streak':3},old/'latest.pt')
    (old/'test_opened.json').write_text('{}')
    (z/'config.json').write_text(json.dumps({'settings':{'seed':20261025}}))
    paths=[old/p for p in ('config.json','result.json','latest.pt','test_opened.json')]+[zp,r16p]
    def loader(_name,_device):
        m=AdditionNet('digit_tokens',8,2,1,1,77).to(_device)
        return m,{'checkpoint_sha256':'mock'}
    monkeypatch.setattr(r,'load_certified_model',loader)
    return model,old,r16,z,paths


def make_args(tmp_path,old,r16,z):
    return r.parser().parse_args(['--device','cpu','--target','64',
       '--ladder-output','fake-ladder','--zero-only-output',str(z),
       '--r16-output',str(r16),'--r32-output',str(old),
       '--output',str(tmp_path/'output'), '--max-new-stages','1',
       '--steps-per-stage','1','--probe-every','1','--batch-size','16',
       '--legacy-batch-size','4','--legacy-probe-cards','8',
       '--dev-anchors','1','--dev-views','2','--test-anchors','1',
       '--outer-per-anchor','2','--eval-batch-size','16','--cpu-threads','1',
       '--zero-probe-anchors','1','--stability-probes','2'])


def tiny_eval(radius,split,seed,stage,per_band=2,views=1,outer_per_anchor=24):
    return [[(100,-100,0,0,100),(100,-99,1,0,100),(100,-66,34,0,100)] for _ in range(views)]


def test_verified_parent_accepts_annotated_research_source_and_rejects_tampering(tmp_path,monkeypatch):
    model,old,r16,z,paths=fake_parent_files(tmp_path,monkeypatch)
    args=make_args(tmp_path,old,r16,z)
    m,origin=r.verified_research_parent(args)
    assert origin['parent_status'].startswith('experimental_')
    assert origin['parent_step']==2750
    assert m(torch.tensor([[200,-200]]) )['sign'].shape==(1,3)
    report=json.loads((old/'result.json').read_text())
    report['holdout_once']['zero_errors']=1
    (old/'result.json').write_text(json.dumps(report))
    with pytest.raises(RuntimeError,match='PROVENANCE'):
        r.verified_research_parent(args)


def test_cpu_backbone_train_checkpoint_resume_provenance(tmp_path,monkeypatch):
    model,old,r16,z,paths=fake_parent_files(tmp_path,monkeypatch)
    a=make_args(tmp_path,old,r16,z)
    orig=[r.sha256(p) for p in paths]
    monkeypatch.setattr(r,'evaluation_cards',tiny_eval)
    monkeypatch.setattr(r,'zero_probe',lambda *_:[(100,-100,True,0,100)])
    result=r.run(a)
    assert result['status']=='halted_budget_exhausted'
    latest=Path(a.output)/'stages/radius_64/latest.pt'
    s=torch.load(latest,weights_only=True)
    assert s['step']==1
    assert any(not torch.equal(model.state_dict()[key],s['model'][key]) for key in s['model'])
    assert [r.sha256(p) for p in paths]==orig
    a.resume=True;a.steps_per_stage=2
    res=r.run(a)
    assert res['status']=='halted_budget_exhausted'
    assert torch.load(latest,weights_only=True)['step']==2


def perfect_report():
    return {'cards':100,'classes':{k:{'correct':20,'cards':20,'accuracy':1.,'errors':0}
        for k in ('zero','inner','outer','sign')},
        'outer_buckets':{'band:0-127':{'correct':20,'cards':20,'accuracy':1.,'errors':0}},
        'outer_by_band':{str(i):{'correct':20,'cards':20,'accuracy':1.,'errors':0} for i in range(13)},
        'counterexamples':[]}


def setup_promotion(tmp_path,monkeypatch,test_fail=False):
    model,old,r16,z,paths=fake_parent_files(tmp_path,monkeypatch)
    a=make_args(tmp_path,old,r16,z);a.steps_per_stage=2
    monkeypatch.setattr(r,'evaluation_cards',tiny_eval)
    monkeypatch.setattr(r,'zero_probe',lambda *_:[(100,-100,True,0,100)])
    def score(model,rows,device,batch_size=256):
        out=perfect_report()
        if test_fail and len(rows) and rows[0][0]==88:
            out['classes']['zero']={'cards':20,'correct':19,'errors':1,'accuracy':.95}
        return out
    def tiny_eval_test(radius,split,seed,stage,per_band=2,views=1,outer_per_anchor=24):
        if split=='test':return [[(88,-88,0,0,88)]]
        return tiny_eval(radius,split,seed,stage,per_band,views,outer_per_anchor)
    monkeypatch.setattr(r,'evaluation_cards',tiny_eval_test)
    monkeypatch.setattr(r,'assess',score)
    monkeypatch.setattr(r,'zero_classify',lambda *_:{'passes':True})
    def hist(_model,rows,*_):
        n=len(rows['y'])
        return {'groups':{'all':{'passed':n,'accuracy':1.}},'false_zero':0}
    monkeypatch.setattr(r,'legacy_evaluate',hist)
    return a,paths


def test_cpu_promotion_and_hash_rejection_on_resume(tmp_path,monkeypatch):
    a,paths=setup_promotion(tmp_path,monkeypatch)
    hashes=[r.sha256(p) for p in paths]
    res=r.run(a)
    assert res['status']=='target_reached_sample_evaluated'
    cp=Path(a.output)/'stages/radius_64/promoted.pt'
    assert cp.exists() and (cp.parent/'acceptance.json').exists()
    assert [r.sha256(p) for p in paths]==hashes
    a.resume=True
    assert r.run(a)['status']=='target_reached_sample_evaluated'
    cp.write_bytes(cp.read_bytes()+b'corruption')
    with pytest.raises(RuntimeError,match='SHA_DRIFT'):
        r.run(a)


def test_sealed_failed_test_cannot_be_reused(tmp_path,monkeypatch):
    a,_=setup_promotion(tmp_path,monkeypatch,test_fail=True)
    res=r.run(a)
    assert res['status']=='halted_heldout_refused'
    path=Path(a.output)/'stages/radius_64'
    assert (path/'test_opened.json').exists()
    assert not (path/'promoted.pt').exists()
    a.resume=True
    with pytest.raises(RuntimeError,match='TEST_ALREADY_OPENED'):r.run(a)
