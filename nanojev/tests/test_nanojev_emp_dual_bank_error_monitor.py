"""Bias and error dispersion instrumentation without modifying the ladder's SHA-pinned code."""
import copy
import json
from argparse import Namespace
from pathlib import Path

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_emp_dual_bank_error_monitor as mon
from tools import nanojev_emp_dual_bank_ladder as ladder
from tools import nanojev_emp_dual_bank as dual
from tools.nanojev_emp_population import Population, SCHEMA as POP_SCHEMA


def test_exact_bias_standard_deviation_sign_meanings():
    # Two positive errors (+10, +10) but signed-magnitude biases cancel.
    cases = [(1,99,100),(1,99,100),(1,-101,-100),(1,-101,-100)]
    guesses = [110,90,-90,-110]
    s=mon.error_statistics(cases, guesses)
    assert s['signed_error']['n']==4
    assert s['signed_error']['mean']==0
    assert s['signed_error']['stddev']==10
    assert s['signed_relative_error']['stddev']==.1
    assert s['signed_scale_error']['mean']==pytest.approx(0)
    assert s['positive_target_scale_error']['n']==2
    assert s['negative_target_scale_error']['n']==2
    assert s['scale_bias_sign_asymmetry']==0
    assert s['absolute_magnitude_error']['mean']==pytest.approx(0)
    assert s['tipping_violations']==0


def test_upward_offset_vs_scale_bias_and_sign_asymmetry():
    rows=[(100,0,100),(-100,0,-100)]
    vals=[120,-80]
    stats=mon.error_statistics(rows,vals)
    assert stats['signed_error']['mean']==20
    assert stats['signed_error']['stddev']==0
    assert stats['signed_relative_error']['mean']==pytest.approx(.2)
    assert stats['signed_relative_error']['stddev']==0
    assert stats['signed_scale_error']['mean']==pytest.approx(0)
    assert stats['signed_scale_error']['stddev']==pytest.approx(.2)
    assert round(stats['positive_target_scale_error']['mean'],10)==.2
    assert round(stats['negative_target_scale_error']['mean'],10)==-.2
    assert round(stats['scale_bias_sign_asymmetry'],10)==.4
    assert stats['magnitude_overshoot_count']==1
    assert stats['magnitude_undershoot_count']==1


def test_tipping_zero_and_singleton_are_well_defined():
    rows=[(3,-3,0),(512,0,512),(-512,0,-512)]
    stats=mon.error_statistics(rows,[9,-1,0])
    assert stats['zero_cards']==1
    assert stats['zero_nonexact']==1
    assert stats['zero_error']['mean']==9
    assert stats['tipping_violations']==2
    assert stats['tipping_violation_rate']==1
    assert stats['signed_scale_error']['stddev']>0
    assert mon.error_statistics([(1,-1,0)],[-1])['signed_scale_error']['stddev'] is None
    assert mon.error_statistics([],[])['signed_error']['mean'] is None
    assert mon.error_statistics([(10,0,10)],[8])['signed_error']['stddev']==0
    with pytest.raises(ValueError,match='MISMATCH'):
        mon.error_statistics(rows,[1])


def test_existing_gate_fields_not_changed():
    original = dual.summary_for
    rows=[(512,0,512,1,512),(-512,0,-512,1,512)]
    scores=mon.augmented_summary(rows,[512,-256])
    assert scores['within_5pct']==.5
    assert scores['tipping_violation_rate']==0
    assert scores['error_bias_stats']['signed_scale_error']['mean']==-.25
    assert dual.summary_for is original


def make_campaign(tmp_path,monkeypatch,stage_steps=2):
    src=tmp_path/'source';src.mkdir()
    parent=AdditionNet('digit_tokens',width=32,heads=4,layers=1,rounds=1)
    pop=Population(copy.deepcopy(parent))
    payload={'schema':POP_SCHEMA+'.checkpoint','step':4000,'model':pop.state_dict()}
    for key in dual.EMPS:torch.save(payload,src/f'source_{key}.pt')
    banks=dual.construct_banks(parent,payload,payload)
    controls=dual.default_controls()
    optimizers=dual.make_bank_optimizers(banks,controls)
    torch.save({'schema':dual.SCHEMA+'.checkpoint','step':3000,
                'banks':banks.state_dict(),
                'optimizers':{n:o.state_dict() for n,o in optimizers.items()},
                'controls':controls,'control_log':[]},src/'latest.pt')
    (src/'config.json').write_text(json.dumps({'schema':dual.SCHEMA,'radius':512}))
    monkeypatch.setattr(ladder,'load_certified_model',lambda *a,**kw:(copy.deepcopy(parent),{}))
    cards=[(100,-70,30,1,100),(100,-100,0,1,100),
           (512,-200,312,1,512),(-512,200,-312,1,512)]*4
    monkeypatch.setattr(ladder.outward,'generate_batch',lambda *args,**kw:(cards,0))
    monkeypatch.setattr(dual,'histogram_svg',lambda path,scores:path.write_text('<svg/>'))
    # Real summary_for is called on every fixed DEV card for each bank.
    monkeypatch.setattr(ladder,'stage_probe',lambda banks,args,radius:
        dual.evaluate(banks,{'frontier':[cards,cards], 'breadth':[cards,cards]},'cpu',16))
    root=tmp_path/'campaign'
    args=Namespace(source_run=str(src),ladder_output='unused',output=str(root),device='cpu',
                   target=1024,stage_steps=stage_steps,max_stages=1,probe_every=1,
                   batch_size=16,eval_batch_size=16,dev_cards=4,breadth_anchors=1,
                   breadth_outer_per_anchor=1,seed=77,left_gate=1.,right_gate=1.,
                   gate_streak=2,cpu_threads=1,resume=False)
    return args,root


def test_end_to_end_sidecar_original_source_and_checkpoint_unchanged(tmp_path,monkeypatch,capsys):
    args,root=make_campaign(tmp_path,monkeypatch)
    original_save=ladder.save_stage
    original_summary=dual.summary_for
    source_hash=ladder.sha256(ladder.__file__)
    dual_hash=ladder.sha256(dual.__file__)
    with mon.attach():
        assert ladder.run(args)['status']=='halted_budget_exhausted'
    assert ladder.save_stage is original_save
    assert dual.summary_for is original_summary
    assert ladder.sha256(ladder.__file__)==source_hash
    assert ladder.sha256(dual.__file__)==dual_hash
    stage=root/'stages/outward_512'
    assert (stage/mon.HISTORY).is_file()
    assert (stage/mon.CSV).is_file()
    hist=json.loads((stage/mon.HISTORY).read_text())
    assert [x['step'] for x in hist['probes']]==[0,1,2]
    assert len(hist['probes'][1]['banks'])==4
    for name in dual.BANKS:
        assert len(hist['probes'][1]['banks'][name]['outer'])==2
    record=json.loads((stage/'history.json').read_text())[-1]
    assert 'error_bias_stats' in record['banks']['left.left']['frontier'][0]
    checkpoint=torch.load(stage/'latest.pt',map_location='cpu',weights_only=True)
    assert checkpoint['step']==2 and 'banks' in checkpoint
    assert 'EMP_LADDER_ERROR_STATS=' in capsys.readouterr().out
    # A second run can resume without touching pinned source files.
    args.resume=True
    args.stage_steps=3
    with mon.attach():
        assert ladder.run(args)['status']=='halted_budget_exhausted'
    newer=json.loads((stage/mon.HISTORY).read_text())
    assert [x['step'] for x in newer['probes']]==[0,1,2,3]


def test_offline_test_predictions_do_not_touch_training(tmp_path):
    inp=tmp_path/'sealed.json';out=tmp_path/'statistics.json'
    inp.write_text(json.dumps({'split':'test','rows':[
        {'bank':'left.left','view':0,'subset':'frontier','target':512,'prediction':500},
        {'bank':'left.left','view':0,'subset':'frontier','target':-512,'prediction':-450},
        {'target':0,'prediction':0}]}))
    stats=mon.analyze_prediction_json(inp,out)
    assert stats['split']=='test' and stats['statistics']['nonzero']==2
    assert json.loads(out.read_text())['statistics']['zero_nonexact']==0
    assert stats['by_bank_view']['left.left']['0']['frontier']['nonzero']==2
    with pytest.raises(ValueError,match='SPLIT_REQUIRED'):
        inp.write_text(json.dumps({'rows':[]}));mon.analyze_prediction_json(inp,out)


def _same_state(a, b):
    if isinstance(a, torch.Tensor):
        return isinstance(b, torch.Tensor) and torch.equal(a, b)
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(_same_state(a[k],b[k]) for k in a)
    if isinstance(a, (tuple, list)):
        return type(a) is type(b) and len(a)==len(b) and all(_same_state(x,y) for x,y in zip(a,b))
    return a==b


def test_training_checkpoint_identical_with_and_without_error_monitor(tmp_path,monkeypatch):
    torch.manual_seed(2026)
    args,root=make_campaign(tmp_path,monkeypatch,stage_steps=2)
    with mon.attach():
        ladder.run(args)
    observed=torch.load(root/'stages/outward_512/latest.pt',map_location='cpu',weights_only=True)
    args.output=str(tmp_path/'plain')
    ladder.run(args)
    control=torch.load(tmp_path/'plain/stages/outward_512/latest.pt',map_location='cpu',weights_only=True)
    assert _same_state(observed['banks'],control['banks'])
    assert _same_state(observed['optimizers'],control['optimizers'])
    assert _same_state(observed['rng'],control['rng'])
    assert _same_state(observed['torch_rng'],control['torch_rng'])


def test_read_only_inspect_existing_checkpoint(tmp_path,monkeypatch):
    args,root=make_campaign(tmp_path,monkeypatch,stage_steps=1)
    # Stage is produced with original trainer: no bias stats exist yet.
    ladder.run(args)
    path=root/'stages/outward_512/latest.pt'
    before=ladder.sha256(path)
    args.resume=True
    with mon.attach():
        record=mon.inspect(args,512)
        assert record['step']==1
        assert 'error_bias_stats' in record['banks']['right.right']['outer'][0]
    assert ladder.sha256(path)==before
    data=json.loads((root/'stages/outward_512'/mon.HISTORY).read_text())
    assert [p['step'] for p in data['probes']]==[1]
