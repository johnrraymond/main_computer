"""G2-0006: train-evidenced graph consistency and conservative completion."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import random
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_graph_consistency as m
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools.nanojev_gamer_v1_graph_corpus import encounter, allocation
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

# The G2-0005 test builder creates a complete, internally consistent fake
# G1/G2-0001/0002/0003/0004 lineage and a real mocked G2-0005 evaluation.
from test_nanojev_gamer_v1_g2_density_discrimination import _fixture as parent_fixture


def _train():
    return [encounter(random.Random(500+i),'train',i+1) for i in range(80)]+[
        allocation(random.Random(800+i),'train',i+81) for i in range(80)]


def test_cli():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
                           'frob-g2-graph-consistency','--help'],
                          cwd=Path(__file__).resolve().parents[1],capture_output=True,
                          text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert '--density-run' in result.stdout and '--validation-limit' in result.stdout


def test_training_only_directional_functionality():
    train=_train();allowed=sel.compatibility_from_train(train)
    functional,report=m.learn_constraints(train,allowed)
    assert (('owns','actor','action'),'dst') in functional
    assert (('owns','actor','action'),'src') not in functional
    assert (('refers_to','action','action'),'src') in functional
    assert (('refers_to','action','action'),'dst') not in functional
    assert (('applies_to','quantity','action'),'src') not in functional
    assert (('measures','quantity','outcome'),'src') in functional
    assert (('measures','quantity','outcome'),'dst') in functional
    assert report['owns|actor|action']['dst']['observations']>=m.MIN_SUPPORT
    assert report['owns|actor|action']['dst']['maximum']==1
    with pytest.raises(ValueError,match='TRAIN_ONLY'):
        m.learn_constraints(train+[encounter(random.Random(42),'dev',400)],allowed)
    with pytest.raises(ValueError,match='BAD_SUPPORT'):
        m.learn_constraints(train,allowed,min_support=0)


def test_new_validation_disjoint_all_prior_cohorts():
    train=_train()
    first=base.novel_dev_rows(train,limit=4,seed=base.SEED)
    from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
    from tools import nanojev_gamer_v1_g2_relational_training as relational
    second=sel.new_validation_rows(train,first,limit=4)
    third=disc.new_validation_rows(train,first,second,limit=4)
    fourth=relational.new_validation_rows(train,first,second,third,limit=4)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=4)
    fresh=m.new_validation_rows(train,first,second,third,fourth,fifth,limit=8)
    assert Counter(r['family'] for r in fresh)=={'encounter':4,'allocation':4}
    assert {base._sha(r['prompt']) for r in fresh}.isdisjoint(
        {base._sha(r['prompt']) for r in train+first+second+third+fourth+fifth})
    with pytest.raises(ValueError,match='BAD_COHORT'):
        m.new_validation_rows(train,first,second,third,fourth,fifth,limit=3)


def _feature(src,dst,rel,adjusted,*,count=1):
    # Equal odds/adjusted when penalty weights set to 0 below.
    conf=1/(1+__import__('math').exp(-adjusted))
    return (src,dst,rel,conf,adjusted,count,0.)


def test_graph_consistency_finds_conflicts_without_universal_caps():
    actor1=(1,3,'actor');actor2=(5,7,'actor');action1=(8,11,'action');action2=(12,15,'action')
    rule={'minimum_confidence':.5,'density_penalty':0.,'competition_penalty':0.}
    candidate=[_feature(actor1,action1,'owns',1.9),_feature(actor2,action1,'owns',1.1),
               _feature(actor1,action2,'owns',.8)]
    constraints=frozenset({(('owns','actor','action'),'dst')})
    edges,audit=m.graph_decode(candidate,rule,constraints)
    assert (actor1,action1,'owns') in edges
    assert (actor1,action2,'owns') in edges  # actor may own >1 action
    assert (actor2,action1,'owns') not in edges
    assert audit['functional_conflicts_removed']==1
    unconstrained,_=m.graph_decode(candidate,rule,frozenset())
    assert len(unconstrained)==3


def test_completion_requires_independent_support_and_freezes_evidence():
    actor=(1,3,'actor');a=(5,8,'action');b=(10,14,'action')
    qty=(15,19,'quantity');outcome=(22,27,'outcome');extra=(29,34,'action')
    rule={'minimum_confidence':.5,'density_penalty':0.,'competition_penalty':0.}
    # Three subthreshold proposals: two with graph support, the other would
    # become connected only if recovery were allowed to cascade.
    features=[_feature(actor,a,'owns',1.2),_feature(qty,outcome,'measures',.9),
              _feature(a,b,'refers_to',-.10),_feature(a,qty,'refers_to',-.12),
              _feature(b,extra,'refers_to',-.15)]
    # Ignore semantic compatibility in this synthetic decoder-only test;
    # compatibility is already enforced when generating frozen candidates.
    once,diag=m.graph_decode(features,rule,frozenset(),margin=.20,support='one')
    assert (a,b,'refers_to') in once
    assert (a,qty,'refers_to') in once
    assert (b,extra,'refers_to') not in once  # no repair cascade
    assert diag['near_threshold_added']==2
    both,_=m.graph_decode(features,rule,frozenset(),margin=.20,support='both')
    assert (a,qty,'refers_to') in both
    assert (a,b,'refers_to') not in both
    assert (b,extra,'refers_to') not in both
    strict,_=m.graph_decode(features,rule,frozenset())
    assert len(strict)==2
    with pytest.raises(ValueError,match='BAD_REPAIR_RULE'):
        m.graph_decode(features,rule,frozenset(),margin=-.1,support='one')


def test_calibration_selection_no_independent_peeking():
    scores={'g2_0005_frozen_selected':{'recall':.64,'f1':.41,'false_positive':1300}}
    for name in m.VARIANTS:
        scores[name]={'recall':.64,'f1':.40,'false_positive':1200}
    scores['functional_only']={'recall':.625,'f1':.43,'false_positive':1200}
    choice,info=m.choose_rule(scores)
    assert choice=='functional_only'
    assert info['recall_floor']==pytest.approx(.62)
    scores['functional_only']['f1']=.39
    assert m.choose_rule(scores)[0] is None


def _end_to_end(monkeypatch,tmp_path):
    root,selection_dir,disc_dir,relational_dir,g2_dir,g1_report,seen,parent=parent_fixture(monkeypatch,tmp_path)
    monkeypatch.setattr(density,'VALIDATION_LIMIT',4)
    # The mock's tiny, untrained head is not supposed to meet real screening
    # gates. Fix the predeclared parent candidate *in this fixture only*.
    monkeypatch.setattr(density,'choose_rule',lambda *_:(m.PARENT_ARM,{
        'recall_floor':.60,'eligible_count':1,
        'reason':'MAX_CALIBRATION_F1_WITH_RECALL_FLOOR'}))
    monkeypatch.setattr(sel,'validation_gate',lambda *_:{'mocked_independent_gate':True})
    density_dir=tmp_path/'density-run'
    density.run(root=root,g1_validation_report=g1_report,g2_run=g2_dir,
                selection_run=selection_dir,discrimination_run=disc_dir,
                relational_run=relational_dir,output=density_dir,device='cpu',validation_limit=4)
    args={'root':root,'g1_validation_report':g1_report,'g2_run':g2_dir,
          'selection_run':selection_dir,'discrimination_run':disc_dir,
          'relational_run':relational_dir,'density_run':density_dir,
          'output':tmp_path/'graph-run','device':'cpu','validation_limit':4}
    return args,seen


def test_complete_mocked_cpu_replay_outputs_and_immutability(monkeypatch,tmp_path):
    args,seen=_end_to_end(monkeypatch,tmp_path)
    result=m.run(**args)
    assert seen==['calibration_replay','new_independent','g2_0005_replay','new_independent']
    assert result['parent_g2_0005_replay_pass'] and result['trainable_parameters']==0
    assert result['g1_frozen'] and result['g2_0004_rank_weights_frozen']
    assert not result['production_promotion'] and not result['graph_execution_authorized']
    assert result['independent_cases']==4 and result['calibration_cases']==4
    assert set(result['independent_metrics'])=={'g2_0005_frozen_selected',*m.VARIANTS}
    assert len((args['output']/m.CASES).read_text().splitlines())==8
    assert (args['output']/m.CONSTRAINTS).is_file()
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**args)


def test_tampered_saved_parent_rejected_before_output(monkeypatch,tmp_path):
    args,_=_end_to_end(monkeypatch,tmp_path)
    report_file=args['density_run']/density.REPORT
    report=json.loads(report_file.read_text())
    report['independent_metrics'][m.PARENT_ARM]['correct']+=1
    report_file.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='PARENT_METRICS'):
        m.run(**args)
    assert not args['output'].exists()


def test_changed_parent_rule_rejected_before_output(monkeypatch,tmp_path):
    args,_=_end_to_end(monkeypatch,tmp_path)
    report_file=args['density_run']/density.REPORT
    report=json.loads(report_file.read_text())
    report['selected_rule']='density_p45_d15_c00'
    report_file.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='PARENT_CONTRACT:selected_rule'):
        m.run(**args)
    assert not args['output'].exists()
