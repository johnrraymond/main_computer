"""EMP dual-bank outward ladder: gated advancement, isolation, and provenance."""
import copy
import json
from argparse import Namespace

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_emp_dual_bank_ladder as mod
from tools import nanojev_emp_dual_bank as dual
from tools.nanojev_emp_population import Population, SCHEMA as POP_SCHEMA
from tools.nanojev_emp_dual_bank_tune import update


def model():
    return AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1)


def make_run(tmp_path, monkeypatch, **overrides):
    src=tmp_path/'dual';src.mkdir(exist_ok=True)
    parent=model();head=Population(copy.deepcopy(parent))
    pop_ckpt={'schema':POP_SCHEMA+'.checkpoint','step':4000,'model':head.state_dict()}
    for name in ('left','right'):
        torch.save(pop_ckpt,src/f'source_{name}.pt')
    banks=dual.construct_banks(parent,pop_ckpt,pop_ckpt)
    controls=dual.default_controls(); controls['revision']=1
    controls['banks']['right.right']['repeat_boost']=8.
    optimizer=dual.make_bank_optimizers(banks,controls)
    torch.save({'schema':dual.SCHEMA+'.checkpoint','step':3000,
                'banks':banks.state_dict(),'optimizers':{n:o.state_dict() for n,o in optimizer.items()},
                'controls':controls,'control_log':[]},src/'latest.pt')
    (src/'config.json').write_text(json.dumps({'schema':dual.SCHEMA,'radius':512}))
    monkeypatch.setattr(mod,'load_certified_model',lambda *a,**kw:(model(),{}))
    cards=[(100,-72,28,0,100),(111,111,222,0,111),(-500,500,0,0,500),(512,-499,13,0,512)]*4
    monkeypatch.setattr(mod.outward,'generate_batch',lambda rng,radius,seed,size,weights,cursor,phase,focus,target:(cards,cursor+len(cards)))
    monkeypatch.setattr(dual,'histogram_svg',lambda path,scores:path.write_text('<svg/>'))
    def passing(*args):
        scores={n:{key:[{'nonzero':96,'within_5pct':.9,'tipping_violation_rate':.01,
                          'histogram':{'counts':[1]}}]*2 for key in ('frontier','outer')}
                for n in dual.BANKS}
        return scores,{'left':{},'right':{}}
    monkeypatch.setattr(mod,'stage_probe',passing)
    args=Namespace(source_run=str(src),ladder_output='/fake/cert',
        output=str(tmp_path/'out'),device='cpu',target=1024,stage_steps=2,max_stages=2,
        probe_every=1,batch_size=16,eval_batch_size=16,dev_cards=4,breadth_anchors=1,
        breadth_outer_per_anchor=1,seed=77,left_gate=.8,right_gate=.5,
        gate_streak=2,cpu_threads=1,resume=False)
    for k,v in overrides.items():setattr(args,k,v)
    return args,src


def test_gate_is_left_left_and_right_right_not_two_lefts():
    scores={n:{subset:[{'nonzero':16,'within_5pct':.9}]*2 for subset in ('frontier','outer')}
            for n in dual.BANKS}
    assert mod.gate(scores)
    scores['right.right']['frontier'][0]['within_5pct']=.49
    assert not mod.gate(scores)
    scores['right.right']['frontier'][0]['within_5pct']=.51
    scores['right.left']['frontier'][0]['within_5pct']=0
    assert mod.gate(scores)
    scores['left.left']['outer'][1]['within_5pct']=.79
    assert not mod.gate(scores)


def test_end_to_end_two_radii_and_source_snapshots(tmp_path,monkeypatch):
    args,src=make_run(tmp_path,monkeypatch)
    state=mod.run(args)
    assert state['status']=='outward_demonstrated_to_target'
    assert state['accepted_radii']==[512,1024]
    out=tmp_path/'out'
    for radius in (512,1024):
        path=out/'stages'/f'outward_{radius}'
        ckpt=torch.load(path/'accepted.pt',map_location='cpu',weights_only=True)
        assert ckpt['radius']==radius and ckpt['step']==2 and len(ckpt['optimizers'])==4
        assert (path/'histogram.svg').is_file()
        assert (path/'acceptance.json').is_file()
        assert len(json.loads((path/'history.json').read_text()))==2
    assert json.loads((out/'controls.json').read_text())['banks']['right.right']['repeat_boost']==8.
    first=torch.load(out/'stages'/'outward_512'/'accepted.pt',map_location='cpu',weights_only=True)
    second=torch.load(out/'stages'/'outward_1024'/'accepted.pt',map_location='cpu',weights_only=True)
    assert any(not torch.equal(first['banks'][k],second['banks'][k]) for k in first['banks'])
    args.resume=True
    # Old source has changed, but our campaign snapshots are the authority.
    torch.save({'changed':'old experiment'},src/'latest.pt')
    assert mod.run(args)['status']=='outward_demonstrated_to_target'


def test_budget_exhaustion_resume_and_tuning(tmp_path,monkeypatch):
    args,_=make_run(tmp_path,monkeypatch,stage_steps=1,max_stages=1)
    first=mod.run(args)
    assert first['status']=='halted_budget_exhausted' and first['pending_radius']==512
    tune=update(args.output,'right.right',{'backbone_lr':.00002,'repeat_boost':10.})
    assert tune['revision']==2
    args.stage_steps=2;args.resume=True
    follow=mod.run(args)
    assert follow['status']=='stage_limit_reached'
    assert follow['accepted_radii']==[512]
    ckpt=torch.load(tmp_path/'out'/'stages'/'outward_512'/'accepted.pt',map_location='cpu',weights_only=True)
    assert ckpt['controls']['revision']==2
    assert ckpt['control_log'][-1]['effective_step']==2


def test_snapshot_or_configuration_change_refused(tmp_path,monkeypatch):
    args,_=make_run(tmp_path,monkeypatch,max_stages=1)
    mod.run(args)
    args.resume=True
    args.left_gate=.95
    with pytest.raises(RuntimeError,match='CONFIG_DRIFT'):mod.run(args)
    args.left_gate=.80
    with open(tmp_path/'out'/'source_dual.pt','ab') as f:f.write(b'invalid')
    with pytest.raises(RuntimeError,match='SNAPSHOT_MUTATED'):mod.run(args)
