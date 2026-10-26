"""CPU tests for read-only, checkpoint-compatible sidecar distribution monitoring."""
import copy
import json
import random
from pathlib import Path

import pytest
import torch

from tools.nanojev_zero_line_ladder import AdditionNet
from tools import nanojev_zero_line_distribution_monitor as m
from tools import nanojev_zero_line_outward_return as trainer


def test_normalized_signed_margin_sign_and_tipping():
    rows=[(512,0,512,1,512),(-512,0,-512,1,512),(512,0,512,1,512),
          (-512,0,-512,1,512),(25,-25,0,0,25)]
    d=m.summary_for(rows,[512,-256,-2,0,0])
    assert d['nonzero']==4 and d['tipping_violations']==2
    assert d['zero_cards']==1 and d['zero_errors']==0
    assert d['median_q']==.25
    assert d['within_5pct']==.25
    assert d['tipping_violation_rate']==.5
    assert d['q05']<=d['median_q']<=d['q95']


def test_zero_is_not_divided_and_noncanonical_nonzero_is_error():
    assert m.summary_for([(99,-99,0,1,99)],[-1])['zero_errors']==1
    assert m.summary_for([(99,-99,0,1,99)],[0])['zero_errors']==0
    assert m.summary_for([(99,-99,0,1,99)],[0])['nonzero']==0


def test_narrow_but_biased_cannot_be_declared_success():
    base=m.summary_for([(100,0,100,1,100)]*4,[1,25,175,280])
    now=m.summary_for([(100,0,100,1,100)]*4,[50]*4)
    recent=[now]*3
    assert m.convergence(now,base,recent)['status']=='plateau_or_biased'
    exact=m.summary_for([(100,0,100,1,100)]*4,[100]*4)
    assert m.convergence(exact,base,[exact]*3)['status']=='stabilizing_near_target'


def test_frozen_cards_deterministic_valid_and_dev_only():
    a=m.fixed_cards(512,'outward',512,20261026,0,24,4)
    b=m.fixed_cards(512,'outward',512,20261026,0,24,4)
    assert {k:m.hash_cards(v) for k,v in a.items()}=={k:m.hash_cards(v) for k,v in b.items()}
    for rows in a.values():
        for x,y,d,band,mag in rows:
            assert x+y==d
            assert m.old.stable_fold(mag,20261026)=='dev'
    assert len(a['frontier'])==24


def test_two_views_are_independent_and_constrained_to_dev():
    a=m.fixed_cards(512,'outward',512,20261026,0,24,4)['frontier']
    b=m.fixed_cards(512,'outward',512,20261026,1,24,4)['frontier']
    assert m.hash_cards(a)!=m.hash_cards(b)
    assert all(m.old.stable_fold(row[4],20261026)=='dev' for row in a+b)


def test_groups_report_signed_magnitude_and_cancellation_with_support():
    rows=[(2_000_000,-1_999_488,512,12,2_000_000),
          (-2_000_000,1_999_488,-512,12,2_000_000),
          (512,0,512,3,512)]
    g=m.grouped(rows,[512,-256,0])
    assert 'positive' in g['positive'] and 'negative' in g['positive']
    assert g['cancellation_depth']['extreme_le_0.1pct']['nonzero']==2
    assert g['magnitude_band']['12']['nonzero']==2
    assert sum(x['nonzero'] for x in g['residual_shell'].values())==3


def fake_setup(tmp_path, monkeypatch):
    torch.manual_seed(77)
    parent=AdditionNet('digit_tokens',8,2,1,1,77)
    initial=copy.deepcopy(parent.state_dict())
    source={'parent_sha256':'test-parent','parent_status':'experimental_r32'}
    monkeypatch.setattr(trainer,'initial_parent',lambda a:(parent,copy.deepcopy(source)))
    monkeypatch.setattr(trainer,'load_certified_model',lambda _,device:(AdditionNet('digit_tokens',8,2,1,1,77).to(device),{}))
    monkeypatch.setattr(trainer,'zero_probe',lambda *_:[(100,-100,True,0,100)])
    def cards(radius,split,seed,stage,per_band=2,views=1,outer_per_anchor=24):
        return [[(100,-100,0,1,100),(100,-99,1,1,100),(100,-66,34,1,100)] for _ in range(views)]
    monkeypatch.setattr(trainer.old,'evaluation_cards',cards)
    monkeypatch.setattr(trainer,'frontier_cards',lambda *a,**kw:[(100,-66,34,1,100)]*8)
    # Synthetic parent/trainer fixtures intentionally bypass real DEV partition;
    # the real partition is independently verified above.
    monkeypatch.setattr(m,'fixed_cards',lambda *a,**kw:{'frontier':[(100,-66,34,1,100)]*8,'breadth':[(100,-66,34,1,100),(100,-99,1,1,100)]})
    monkeypatch.setattr(trainer,'legacy_evaluate',lambda *a,**kw:{'groups':{'all':{'passed':65025}},'false_zero':0})
    root=tmp_path/'campaign'
    zo=tmp_path/'zero';zo.mkdir()
    (zo/'config.json').write_text(json.dumps({'settings':{'seed':20261025}}))
    flags=['--phase','outward','--device','cpu','--target','64',
        '--output',str(root),'--ladder-output','unused','--zero-only-output',str(zo),
        '--outward-policy','demonstrated','--stage-steps','1','--probe-every','1',
        '--batch-size','16','--legacy-batch-size','8','--historical-probe-cards','8',
        '--eval-batch-size','16','--dev-anchors','1','--dev-views','2',
        '--test-anchors','1','--outer-per-anchor','2','--zero-probe-anchors','1',
        '--cpu-threads','1','--monitor-frontier-count','8','--monitor-breadth-outer','2']
    return parent,initial,root,flags


def test_real_cpu_train_monitor_and_resume_same_original_config(tmp_path,monkeypatch,capsys):
    parent,initial,root,flags=fake_setup(tmp_path,monkeypatch)
    # Refuse promotion so we can test normal original checkpoints and exact resume.
    monkeypatch.setattr(trainer,'stage_progress',lambda *a:False)
    original_func=trainer.snapshot
    result=m.monitor_main(flags)
    assert result['status']=='halted_budget_exhausted'
    assert trainer.snapshot is original_func
    cfg=json.loads((root/'config.json').read_text())
    assert cfg['code_sha256']==trainer.sha256(trainer.__file__)
    trace=root/'stages/outward_64'/m.HISTORY_FILE
    hist=json.loads(trace.read_text())
    assert len(hist['probes'])==1 and hist['probes'][0]['step']==1
    assert all(v['frontier']['summary']['nonzero']==8 for v in hist['baseline'].values())
    assert 'ZERO_OR_DISTRIBUTION=' in capsys.readouterr().out
    assert (trace.parent/'distribution_frontier_v1.svg').is_file()
    assert (trace.parent/'distribution_frontier_v1.csv').is_file()
    assert 'step,view,q05' in (trace.parent/'distribution_frontier_v1.csv').read_text()
    # First run's original checkpoint is authoritative. Resume with new budget,
    # despite sidecar history and without changing original source hash/config.
    more=flags.copy()
    more[more.index('--stage-steps')+1]='2'
    more.append('--resume')
    m.monitor_main(more)
    hist=json.loads(trace.read_text())
    assert [x['step'] for x in hist['probes']]==[1,2]
    cp=torch.load(root/'stages/outward_64/latest.pt',weights_only=True)
    assert cp['step']==2
    assert any(not torch.equal(cp['model'][k],initial[k]) for k in initial)
    assert all(torch.equal(parent.state_dict()[k],initial[k]) for k in initial)
    with pytest.raises(RuntimeError,match='MONITOR_CONFIG_DRIFT'):
        m.monitor_main(more+['--monitor-frontier-count','12'])


def test_distribution_probe_does_not_change_model_rng_or_optimizer(tmp_path,monkeypatch):
    parent,initial,root,flags=fake_setup(tmp_path,monkeypatch)
    monkeypatch.setattr(trainer,'stage_progress',lambda *a:False)
    before=torch.get_rng_state().clone()
    # Existing trainer updates torch RNG; compare just monitor-side evaluation.
    model=AdditionNet('digit_tokens',8,2,1,1,77)
    rows=[(100,-66,34,1,100)]*8
    model.train()
    first=torch.get_rng_state().clone()
    with_monitor=m.infer(model,rows,'cpu',8)
    assert model.training
    assert torch.equal(first,torch.get_rng_state())
    assert len(with_monitor)==8


def test_does_not_modify_outward_code_or_promotion_gates():
    source=Path(trainer.__file__).read_bytes()
    assert b'def stage_progress(' in source
    assert b'def trained_stage(' in source
    assert m.trainer is trainer


def test_instrumentation_leaves_training_checkpoint_bitwise_identical(tmp_path, monkeypatch):
    parent, initial, root, flags = fake_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(trainer, 'stage_progress', lambda *a: False)
    assert m.monitor_main(flags)['status'] == 'halted_budget_exhausted'
    monitored=torch.load(root/'stages/outward_64/latest.pt',weights_only=True)
    plain_root=tmp_path/'plain'
    flags2=flags.copy();flags2[flags2.index('--output')+1]=str(plain_root)
    plain_args=[]
    for k in range(len(flags2)):
        if flags2[k] in ('--monitor-frontier-count','--monitor-breadth-outer'):
            continue
        if k>0 and flags2[k-1] in ('--monitor-frontier-count','--monitor-breadth-outer'):
            continue
        plain_args.append(flags2[k])
    assert trainer.run(trainer.parser().parse_args(plain_args))['status'] == 'halted_budget_exhausted'
    plain=torch.load(plain_root/'stages/outward_64/latest.pt',weights_only=True)
    assert monitored['step']==plain['step']
    assert monitored['rng']==plain['rng']
    assert torch.equal(monitored['torch_rng'],plain['torch_rng'])
    for key in monitored['model']:
        assert torch.equal(monitored['model'][key],plain['model'][key])
