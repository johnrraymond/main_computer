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
        gate_streak=2,cpu_threads=1,resume=False,migrate_best_pair=False)
    for k,v in overrides.items():setattr(args,k,v)
    return args,src


def test_gate_selects_any_two_distinct_banks_even_same_emp():
    scores={n:{subset:[{'nonzero':16,'within_5pct':.1}]*2
            for subset in ('frontier','outer')} for n in dual.BANKS}
    scores['left.left']['frontier'][0]['within_5pct']=.89
    for kind in ('frontier','outer'):
        scores['left.left'][kind]=[{'nonzero':16,'within_5pct':.93}]*2
        scores['left.right'][kind]=[{'nonzero':16,'within_5pct':.87}]*2
    selected=mod.select_pair(scores)
    assert selected['qualified']
    assert (selected['left'],selected['right'])==('left.left','left.right')
    assert mod.gate(scores)
    scores['left.right']['outer'][1]['within_5pct']=.49
    assert not mod.gate(scores)
    scores['right.left']['outer']=[{'nonzero':16,'within_5pct':.70}]*2
    scores['right.left']['frontier']=[{'nonzero':16,'within_5pct':.70}]*2
    selected=mod.select_pair(scores)
    assert selected['qualified'] and selected['left']=='left.left' and selected['right']=='right.left'


def test_gate_respects_two_views_and_nonzero():
    scores={n:{subset:[{'nonzero':16,'within_5pct':.9} for v in range(2)]
            for subset in ('frontier','outer')} for n in dual.BANKS}
    assert mod.gate(scores)
    for name in ('left.left','right.right','left.right'):
        scores[name]['frontier'][0]['nonzero']=0
    selection=mod.select_pair(scores)
    assert not selection['qualified']
    assert selection['left'] != selection['right']


def test_role_not_derived_from_emp_name():
    scores={n:{subset:[{'nonzero':16,'within_5pct':.12} for v in range(2)]
            for subset in ('frontier','outer')} for n in dual.BANKS}
    for bank, accuracy in (('right.right',.95),('right.left',.91)):
        scores[bank]={subset:[{'nonzero':16,'within_5pct':accuracy} for v in range(2)]
                      for subset in ('frontier','outer')}
    s=mod.select_pair(scores)
    assert (s['left'],s['right'])==('right.right','right.left')


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
        assert ckpt['gate_policy']==mod.GATE_POLICY and ckpt['selected_pair']['left'] != ckpt['selected_pair']['right']
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


def test_migrate_legacy_in_progress_without_erasing_history(tmp_path,monkeypatch):
    args,_=make_run(tmp_path,monkeypatch,stage_steps=1,max_stages=1)
    assert mod.run(args)['status']=='halted_budget_exhausted'
    root=tmp_path/'out'
    cfg=json.loads((root/'config.json').read_text())
    cfg.pop('gate_policy')
    cfg['code_sha256']=mod.LEGACY_CODE_SHA256
    mod.atomic_json(root/'config.json',cfg)
    path=root/'stages'/'outward_512'
    stage_cfg=json.loads((path/'config.json').read_text())
    stage_cfg['campaign_config_sha256']=mod.sha256(root/'config.json')
    mod.atomic_json(path/'config.json',stage_cfg)
    checkpoint=torch.load(path/'latest.pt',weights_only=True)
    checkpoint.pop('gate_policy')
    checkpoint.pop('selected_pair')
    checkpoint['streak']=1  # Old-policy streak MUST be discarded on migration.
    torch.save(checkpoint,path/'latest.pt')
    original_cfg=(root/'config.json').read_bytes()
    original_history=(path/'history.json').read_bytes()
    args.resume=True
    args.stage_steps=2
    with pytest.raises(RuntimeError,match='GATE_MIGRATION_REQUIRED'):
        mod.run(args)
    args.migrate_best_pair=True
    assert mod.run(args)['status']=='halted_budget_exhausted'
    assert (root/'config.json').read_bytes()==original_cfg
    assert (root/mod.MIGRATION_FILE).is_file()
    assert len(json.loads((path/'history.json').read_text()))==2
    resumed=torch.load(path/'latest.pt',weights_only=True)
    assert resumed['streak']==1  # Old-policy streak was reset before step 2.
    # Campaign remains resumable without repeating the explicit migration flag.
    args.migrate_best_pair=False
    args.stage_steps=3
    assert mod.run(args)['accepted_radii']==[512]
    accepted=torch.load(path/'accepted.pt',weights_only=True)
    assert accepted['streak']==2 and accepted['step']==3


def test_migration_preserves_previously_accepted_stage_unchanged(tmp_path,monkeypatch):
    args,_=make_run(tmp_path,monkeypatch,max_stages=1)
    assert mod.run(args)['accepted_radii']==[512]
    root=tmp_path/'out'
    cfg=json.loads((root/'config.json').read_text())
    cfg.pop('gate_policy')
    cfg['code_sha256']=mod.LEGACY_CODE_SHA256
    mod.atomic_json(root/'config.json',cfg)
    stage=root/'stages'/'outward_512'
    stage_cfg=json.loads((stage/'config.json').read_text())
    stage_cfg['campaign_config_sha256']=mod.sha256(root/'config.json')
    mod.atomic_json(stage/'config.json',stage_cfg)
    acc=json.loads((stage/'acceptance.json').read_text())
    acc.pop('gate_policy')
    acc.pop('selected_pair')
    acc['stage_config_sha256']=mod.sha256(stage/'config.json')
    mod.atomic_json(stage/'acceptance.json',acc)
    originals=[p.read_bytes() for p in (stage/'accepted.pt',stage/'acceptance.json',stage/'config.json')]
    args.resume=True;args.migrate_best_pair=True
    assert mod.run(args)['accepted_radii']==[512,1024]
    assert originals==[p.read_bytes() for p in (stage/'accepted.pt',stage/'acceptance.json',stage/'config.json')]


def test_streak_allows_different_qualifying_pairs(tmp_path,monkeypatch):
    args,_=make_run(tmp_path,monkeypatch,stage_steps=3,max_stages=1)
    sequence=[('left.left','left.right'),('right.right','right.left'),('right.right','right.left')]
    def alternating(*unused):
        names=sequence.pop(0)
        scores={n:{kind:[{'nonzero':96,'within_5pct':.1,'tipping_violation_rate':0.,'histogram':{}}]*2
                   for kind in ('frontier','outer')} for n in dual.BANKS}
        for n,accuracy in zip(names,(.95,.85)):
            for kind in ('frontier','outer'):
                scores[n][kind]=[{'nonzero':96,'within_5pct':accuracy,'tipping_violation_rate':0.,'histogram':{}}]*2
        return scores,{'left':{},'right':{}}
    # Stage baseline is first call, so provide the full sequence including it.
    sequence.insert(0,('left.left','left.right'))
    monkeypatch.setattr(mod,'stage_probe',alternating)
    ret=mod.run(args)
    assert ret['accepted_radii']==[512]
    cp=torch.load(tmp_path/'out'/'stages'/'outward_512'/'accepted.pt',weights_only=True)
    assert cp['step']==2
    hist=json.loads((tmp_path/'out'/'stages'/'outward_512'/'history.json').read_text())
    assert [r['streak'] for r in hist]==[1,2]
