"""Contract/regression tests for fast outward then reverse consolidation."""
import copy
import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from tools.nanojev_zero_line_ladder import AdditionNet
from tools import nanojev_zero_line_outward_return as c


def test_plan_covers_full_sum_and_returns_to_zero():
    assert c.outward_plan(4_000_000)[0]==64
    assert c.outward_plan(4_000_000)[-1]==4_000_000
    assert len(c.outward_plan(4_000_000))==17
    assert c.return_plan(4_000_000)[0]==4_000_000
    assert c.return_plan(4_000_000)[-3:]==[32,16,0]


@pytest.mark.parametrize('radius',[64,512,4096,4_000_000])
@pytest.mark.parametrize('phase,focus', [('outward',64),('return',32),('return',0)])
def test_sampling_remains_bounded_and_training_only(radius,phase,focus):
    rng=random.Random(31)
    rows,step=c.generate_batch(rng,radius,c.old.DEFAULT_SEED,128,[1]*len(c.BANDS),0,
        phase,focus,4_000_000)
    assert step==128 and len(rows)==128
    assert any(d==0 for _,_,d,_,_ in rows)
    for a,b,d,band,m in rows:
        assert a+b==d
        assert max(abs(a),abs(b))==m
        assert max(abs(a),abs(b))<=2_000_000
        assert c.old.stable_fold(m,c.old.DEFAULT_SEED)=='train'
        assert abs(d)<=(radius if phase=='outward' else 4_000_000)
    if phase=='outward':
        assert any(abs(d)>=radius//2 and abs(d)>32 for a,b,d,band,m in rows)


def reports(outer,inner=0.0,zero_errors=10,sign=.9):
    n=100
    return {'cards':n,'classes':{'zero':{'errors':zero_errors,'cards':n,'accuracy':1-zero_errors/n},
        'inner':{'errors':round((1-inner)*n),'cards':n,'accuracy':inner},
        'outer':{'errors':round((1-outer)*n),'cards':n,'accuracy':outer},
        'sign':{'errors':round((1-sign)*n),'cards':n,'accuracy':sign}}}


def thresholds():
    return SimpleNamespace(outward_min_outer=.5,outward_min_frontier=.3,
        outward_min_gain=.03,outward_strong_outer=.8,min_frontier_cards=8)


def test_outward_acceptance_requires_frontier_but_not_zero_or_legacy():
    a=thresholds()
    base=[reports(.15),reports(.16)]
    strong=[reports(.6,inner=.1,zero_errors=80),reports(.65,inner=.1,zero_errors=80)]
    assert c.stage_progress(strong,[{'cards':20,'accuracy':.36}]*2,base,a)
    assert not c.stage_progress(strong,[{'cards':20,'accuracy':.2}]*2,base,a)
    assert not c.stage_progress([reports(.1)]*2,[{'cards':20,'accuracy':.5}]*2,base,a)
    assert not c.stage_progress(strong,[{'cards':0,'accuracy':None}]*2,base,a)


@pytest.mark.parametrize('radius',[64,1024,2_097_152,4_000_000])
def test_frontier_has_real_high_residual_examples(radius):
    for split in ('train','dev','test'):
        rows=c.frontier_cards(radius,split,c.old.DEFAULT_SEED,0,16)
        assert len(rows)==16
        assert any(d>0 for a,b,d,_,m in rows)
        assert any(d<0 for a,b,d,_,m in rows)
        for a,b,d,band,m in rows:
            assert a+b==d and abs(d)>=radius//2 and abs(d)<=radius
            assert c.old.stable_fold(m,c.old.DEFAULT_SEED)==split


def test_checkpoint_roundtrip_and_source_is_frozen(tmp_path):
    model=AdditionNet('digit_tokens',8,2,1,1,77)
    opt=torch.optim.AdamW(model.parameters(),lr=1e-4)
    rng=random.Random(123)
    checkpoint=c.snapshot(model,opt,rng,14,[2.]*len(c.BANDS),99,1,'outward',64)
    file=tmp_path/'state.pt';torch.save(checkpoint,file)
    other=AdditionNet('digit_tokens',8,2,1,1,8)
    opt2=torch.optim.AdamW(other.parameters(),lr=1e-4)
    rr=random.Random(33)
    assert c.restore(torch.load(file,weights_only=True),other,opt2,rr,'outward',64)[0]==14
    assert rr.getstate()==rng.getstate()
    assert all(torch.equal(other.state_dict()[k],model.state_dict()[k]) for k in model.state_dict())
    with pytest.raises(RuntimeError,match='BAD_RESUME'):
        c.restore(checkpoint,other,opt2,rr,'return',64)


def fake_setup(tmp_path,monkeypatch):
    torch.manual_seed(77)
    parent=AdditionNet('digit_tokens',8,2,1,1,77)
    initial=copy.deepcopy(parent.state_dict())
    source={'parent_sha256':'seed-checkpoint','parent_status':'experimental_r32'}
    monkeypatch.setattr(c,'initial_parent',lambda a:(parent,copy.deepcopy(source)))
    monkeypatch.setattr(c,'load_certified_model',lambda _,device:(AdditionNet('digit_tokens',8,2,1,1,77).to(device),{}))
    monkeypatch.setattr(c,'zero_probe',lambda *_:[(100,-100,True,0,100)])
    def tiny(radius,split,seed,stage,per_band=2,views=1,outer_per_anchor=24):
        return [[(100,-100,0,1,100),(100,-99,1,1,100),(100,-66,34,1,100)] for _ in range(views)]
    monkeypatch.setattr(c.old,'evaluation_cards',tiny)
    monkeypatch.setattr(c,'legacy_evaluate',lambda *a,**k:{'groups':{'all':{'passed':65025}},'false_zero':0})
    root=tmp_path/'work'
    zo=tmp_path/'zero';zo.mkdir()
    (zo/'config.json').write_text(json.dumps({'settings':{'seed':20261025}}))
    args=c.parser().parse_args(['--device','cpu','--phase','outward','--target','64',
        '--output',str(root),'--ladder-output','unused','--zero-only-output',str(zo),
        '--stage-steps','1','--return-steps','1','--probe-every','1',
        '--batch-size','16','--legacy-batch-size','8','--historical-probe-cards','8',
        '--eval-batch-size','16','--dev-anchors','1','--dev-views','2',
        '--test-anchors','1','--outer-per-anchor','2','--zero-probe-anchors','1',
        '--cpu-threads','1'])
    return parent,initial,root,args


def test_actual_cpu_training_saves_unpromoted_with_optimizer_and_resume(tmp_path,monkeypatch):
    parent,initial,root,a=fake_setup(tmp_path,monkeypatch)
    # Real CPU optimizer/backprop; deliberately impossible 100%-plus threshold
    # to exercise fail-closed budget and resume without a sealed TEST.
    a.outward_policy='demonstrated'
    monkeypatch.setattr(c,'stage_progress',lambda *x:False)
    result=c.run(a)
    assert result['status']=='halted_budget_exhausted'
    state=torch.load(root/'stages/outward_64/latest.pt',weights_only=True)
    assert state['step']==1 and 'optimizer' in state and 'rng' in state
    assert any(not torch.equal(state['model'][k],initial[k]) for k in initial)
    assert all(torch.equal(parent.state_dict()[k],initial[k]) for k in initial)
    a.resume=True;a.stage_steps=2
    assert c.run(a)['status']=='halted_budget_exhausted'
    assert torch.load(root/'stages/outward_64/latest.pt',weights_only=True)['step']==2
    assert (root/'stages/outward_64/baseline.json').is_file()
    assert not (root/'stages/outward_64/test_opened.json').exists()


def test_research_outward_acceptance_does_not_require_legacy_retention(tmp_path,monkeypatch):
    parent,initial,root,a=fake_setup(tmp_path,monkeypatch)
    a.outward_policy='demonstrated'
    monkeypatch.setattr(c,'stage_progress',lambda *x:True)
    result=c.run(a)
    assert result['status']=='outward_demonstrated_to_target'
    cp=root/'stages/outward_64/accepted.pt'
    assert cp.is_file()
    record=json.loads((root/'stages/outward_64/result.json').read_text())
    assert record['status']=='acquisition_milestone'
    assert record['historical_probe']
    assert not (root/'stages/outward_64/test_opened.json').exists()
    # Reading it back validates the hash chain.
    a.phase='return';a.resume=True
    m,src=c.initial_parent(a)
    restored,prev,done,nxt=c.load_chain(a,root,m,'return')
    assert done==[('outward',64)] and nxt==('return',64)
    # Tampering any model invalidates the chain.
    cp.write_bytes(cp.read_bytes()+b'junk')
    with pytest.raises(RuntimeError,match='CHAIN_TAMPERED'):
        c.load_chain(a,root,m,'return')


def test_default_exposure_advances_even_when_accuracy_poor(tmp_path,monkeypatch):
    parent,initial,root,a=fake_setup(tmp_path,monkeypatch)
    monkeypatch.setattr(c,'stage_progress',lambda *x:False)
    result=c.run(a)
    assert result['status']=='outward_exposure_completed'
    accepted=json.loads((root/'stages/outward_64/result.json').read_text())
    assert accepted['status']=='exposure_milestone'
    assert not accepted['qualified']
    assert accepted['milestone_rule'].startswith('bounded')
    assert accepted['dev']


def test_return_starts_after_exposure_and_trains_same_backbone(tmp_path,monkeypatch):
    parent,initial,root,a=fake_setup(tmp_path,monkeypatch)
    a.outward_policy='exposure'
    assert c.run(a)['status']=='outward_exposure_completed'
    a.phase='return';a.resume=True;a.max_stages=1
    result=c.run(a)
    assert result['status']=='stage_limit_reached'
    state=torch.load(root/'stages/return_64/accepted.pt',weights_only=True)
    assert state['status']=='reverse_milestone'
    assert (root/'stages/return_64/latest.pt').is_file()
    assert not (root/'stages/return_64/test_opened.json').exists()


def test_return_phase_requires_outward_target(tmp_path,monkeypatch):
    parent,initial,root,a=fake_setup(tmp_path,monkeypatch)
    a.phase='return'
    with pytest.raises(RuntimeError,match='OUTWARD_TARGET_NOT_REACHED'):
        c.run(a)


def test_final_holdout_opened_only_after_dev_legacy_and_zero(tmp_path,monkeypatch):
    parent,initial,root,a=fake_setup(tmp_path,monkeypatch)
    a.phase='return'
    a.return_final_min_outer=.9;a.return_final_min_inner=.99;a.return_final_min_sign=.99
    path=root/'final';path.mkdir(parents=True)
    bad=[reports(.5,inner=1,zero_errors=0,sign=1.)]*2
    ret={'status':'training','step':55}
    assert not c.final_conformity(parent,bad,{},parent,{},a,path,ret)
    assert not (path/'test_opened.json').exists()


def test_final_holdout_is_sealed_on_refusal(tmp_path,monkeypatch):
    parent,initial,root,a=fake_setup(tmp_path,monkeypatch)
    path=root/'final';path.mkdir(parents=True)
    a.return_final_min_outer=.9;a.return_final_min_inner=.99;a.return_final_min_sign=.99
    strong=[reports(.95,inner=1,zero_errors=0,sign=1.)]*2
    def patched_metrics(model,rows,device,batch):return reports(.8,inner=1,zero_errors=0,sign=1.)
    monkeypatch.setattr(c,'metrics',patched_metrics)
    monkeypatch.setattr(c,'zero_classify',lambda *_:{'passes':True})
    report={'status':'training','step':77}
    assert not c.final_conformity(parent,strong,{'dev':[], 'test':[]},parent,{},a,path,report)
    assert report['status']=='final_test_refused'
    assert (path/'test_opened.json').exists()
    assert not (path/'accepted.pt').exists()


def test_rejects_bad_arguments(tmp_path):
    a=c.parser().parse_args(['--phase','plan','--target','4000001'])
    with pytest.raises(ValueError,match='INVALID_ARGUMENTS'):c.run(a)


def test_warmstart_from_running_ladder_requires_provenance_and_unsealed_source(tmp_path,monkeypatch):
    parent=AdditionNet('digit_tokens',8,2,1,1,77)
    source={'parent_sha256':'original-r32-hash'}
    monkeypatch.setattr(c.old,'verified_research_parent',lambda a:(parent,source.copy()))
    oldroot=tmp_path/'old_campaign';stage=oldroot/'stages/radius_64';stage.mkdir(parents=True)
    previous={'schema':c.old.SCHEMA,'source':{'parent_sha256':'original-r32-hash'}}
    (oldroot/'config.json').write_text(json.dumps(previous))
    cfg={'schema':c.old.SCHEMA+'.stage','radius':64,
         'parent_sha256':'original-r32-hash','settings_sha256':c.sha256(oldroot/'config.json')}
    (stage/'config.json').write_text(json.dumps(cfg))
    altered=copy.deepcopy(parent.state_dict());name=next(iter(altered));altered[name]=altered[name]+.25
    torch.save({'schema':c.old.SCHEMA+'.checkpoint','step':5250,'model':altered},stage/'latest.pt')
    a=c.parser().parse_args(['--warmstart-stage',str(stage)])
    chosen,src=c.initial_parent(a)
    assert src['warmstart']['step']==5250
    assert torch.equal(chosen.state_dict()[name],altered[name])
    (stage/'test_opened.json').write_text('{}')
    with pytest.raises(RuntimeError,match='SEALED_STAGE'):c.initial_parent(a)
    (stage/'test_opened.json').unlink()
    cfg['parent_sha256']='wrong-parent';(stage/'config.json').write_text(json.dumps(cfg))
    with pytest.raises(RuntimeError,match='PARENT_MISMATCH'):c.initial_parent(a)
