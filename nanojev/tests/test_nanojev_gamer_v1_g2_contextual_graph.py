"""G2-0007: train-only contextual evidence, frozen inference, lineage."""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import json
import math
import random
import subprocess
import sys

import pytest
from tools import nanojev_gamer_v1_g2_contextual_graph as m
from tools import nanojev_gamer_v1_g2_graph_consistency as graph6
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
from test_nanojev_gamer_v1_g2_graph_consistency import _end_to_end as parent_fixture


def _train():
    return [encounter(random.Random(500+i),'train',i+1) for i in range(80)]+[
        allocation(random.Random(800+i),'train',i+81) for i in range(80)]


def _feature(src,dst,rel,odds,count=1):
    conf=1/(1+math.exp(-odds))
    return (src,dst,rel,conf,odds,count,0.)


def test_cli_registered():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
                           'frob-g2-contextual-graph','--help'],
                          cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert '--consistency-run' in result.stdout and '--validation-limit' in result.stdout


def test_train_only_evidence_no_oracle_context():
    train=_train();allowed=sel.compatibility_from_train(train)
    weights,evidence=m.learn_motifs(train,allowed)
    assert set('|'.join(k) for k in weights)==set(evidence)
    assert sum(len(v) for v in weights.values())>0
    assert any(x>0 for group in weights.values() for x in group.values())
    assert any(x<0 for group in weights.values() for x in group.values())
    assert all(-2<=x<=2 for group in weights.values() for x in group.values())
    assert m.learn_motifs(train,allowed)==(weights,evidence)
    with pytest.raises(ValueError,match='TRAIN_ONLY'):
        m.learn_motifs(train+[encounter(random.Random(99),'dev',999)],allowed)
    with pytest.raises(ValueError,match='MIN_OBSERVATIONS'):
        m.learn_motifs(train,allowed,min_observations=0)


def test_endpoint_context_excludes_candidate_self_edge():
    actor=(0,3,'actor');action=(5,10,'action');second=(12,17,'action')
    owns=(actor,action,'owns');other=(actor,second,'owns')
    ctx=m.context_patterns({owns,other},[owns,(action,second,'refers_to')])
    assert ('endpoint','src','>','owns','action') in ctx[owns]
    assert ('endpoint','dst','<','owns','actor') not in ctx[owns]
    assert ('twohop','owns','<','owns','>','actor') in ctx[(action,second,'refers_to')]
    # An edge is not evidence for itself when it is selected as a parent edge.
    assert not m.context_patterns({owns},[owns])[owns]


def test_context_can_add_and_remove_without_a_binding_cap_or_cascade():
    a=(0,3,'actor');b=(4,6,'action');c=(7,11,'action');d=(12,16,'action')
    parent_rule={'minimum_confidence':.5,'density_penalty':0.,'competition_penalty':0.}
    edges=[_feature(a,b,'owns',1.0),_feature(a,c,'owns',.8),
           _feature(a,d,'owns',-.15),_feature(b,c,'refers_to',.12)]
    # b->c has a real two-hop witness b<-a->c. A near-threshold a->d may
    # only be added when it has an existing local context; no second pass.
    weights={('refers_to','action','action'):
             {('twohop','owns','<','owns','>','actor'):1.2},
             ('owns','actor','action'):
             {('endpoint','src','>','owns','action'):.5}}
    accepted,audit=m.select_contextual(edges,parent_rule,weights,
                                       positive_bonus=.5,negative_penalty=0.)
    assert (a,b,'owns') in accepted and (a,c,'owns') in accepted
    assert (a,d,'owns') in accepted  # new edge with frozen parent support
    assert (b,c,'refers_to') in accepted
    assert audit['edges_added']==1
    # Two owns edges sharing one actor remain legal, unlike G2-0006 caps.
    assert sum(edge[0]==a and edge[2]=='owns' for edge in accepted)==3
    with pytest.raises(ValueError,match='BAD_CONTEXT_RULE'):
        m.select_contextual(edges,parent_rule,weights,positive_bonus=-1,negative_penalty=0.)


def test_contextual_negative_evidence_rejects_without_cardinality():
    a=(0,3,'actor');b=(4,6,'action');c=(7,11,'action');d=(12,16,'action')
    rule={'minimum_confidence':.5,'density_penalty':0.,'competition_penalty':0.}
    edges=[_feature(a,b,'owns',.5),_feature(a,c,'owns',.4),
           _feature(b,c,'refers_to',.2),_feature(a,d,'owns',.7)]
    weights={('refers_to','action','action'):
             {('twohop','owns','<','owns','>','actor'):-2.0}}
    result,audit=m.select_contextual(edges,rule,weights,positive_bonus=0.,negative_penalty=.5)
    assert (b,c,'refers_to') not in result
    assert (a,b,'owns') in result and (a,c,'owns') in result and (a,d,'owns') in result
    assert audit['edges_removed']==1


def test_fresh_validation_excludes_all_six_preceding_cohorts():
    train=_train()
    first=base.novel_dev_rows(train,limit=4,seed=base.SEED)
    from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
    from tools import nanojev_gamer_v1_g2_relational_training as relation
    second=sel.new_validation_rows(train,first,limit=4)
    third=disc.new_validation_rows(train,first,second,limit=4)
    fourth=relation.new_validation_rows(train,first,second,third,limit=4)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=4)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=4)
    fresh=m.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=8)
    assert Counter(r['family'] for r in fresh)=={'allocation':4,'encounter':4}
    assert {base._sha(r['prompt']) for r in fresh}.isdisjoint(
        {base._sha(r['prompt']) for r in train+first+second+third+fourth+fifth+sixth})
    with pytest.raises(ValueError,match='BAD_COHORT'):
        m.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=3)


def test_calibration_selection_never_uses_independent_metrics():
    rows={'g2_0005_frozen_selected':{'recall':.63,'f1':.41,'false_positive':1300}}
    for name in m.VARIANTS:
        rows[name]={'recall':.63,'f1':.40,'false_positive':1200}
    rows['motif_bonus_050']={'recall':.62,'f1':.43,'false_positive':1100}
    selected,reason=m.choose_rule(rows)
    assert selected=='motif_bonus_050' and reason['eligible_count']==1
    rows['motif_bonus_050']['recall']=.59
    assert m.choose_rule(rows)[0] is None


def _fixture(monkeypatch,tmp_path):
    args,seen=parent_fixture(monkeypatch,tmp_path)
    monkeypatch.setattr(graph6,'VALIDATION_LIMIT',4)
    consistency_report=graph6.run(**args)
    assert consistency_report['selected_rule'] is None
    return {**{key:args[key] for key in ('root','g1_validation_report','g2_run',
                       'selection_run','discrimination_run','relational_run','density_run')},
            'consistency_run':args['output'],'output':tmp_path/'context-run',
            'device':'cpu','validation_limit':4},seen


def test_mocked_end_to_end_replay_and_immutable_results(monkeypatch,tmp_path):
    args,seen=_fixture(monkeypatch,tmp_path)
    report=m.run(**args)
    assert seen==['calibration_replay','new_independent','g2_0005_replay','new_independent',
                  'g2_0006_replay','new_independent']
    assert report['g1_frozen'] and report['parent_g2_0006_replay_pass']
    assert report['trainable_parameters']==0 and not report['graph_execution_authorized']
    assert report['independent_cases']==4 and report['calibration_cases']==4
    assert set(report['independent_metrics'])=={'g2_0005_frozen_selected',*m.VARIANTS}
    assert len((args['output']/m.CASES).read_text().splitlines())==8
    assert (args['output']/m.MOTIFS).is_file()
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**args)


def test_tampered_g2_0006_parent_rejected_before_output(monkeypatch,tmp_path):
    args,_=_fixture(monkeypatch,tmp_path)
    path=args['consistency_run']/graph6.REPORT
    report=json.loads(path.read_text())
    report['independent_metrics']['functional_only']['correct']+=1
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='G2_0006_METRICS'):
        m.run(**args)
    assert not args['output'].exists()


def test_g2_0006_selection_drift_rejected_before_output(monkeypatch,tmp_path):
    args,_=_fixture(monkeypatch,tmp_path)
    path=args['consistency_run']/graph6.REPORT
    report=json.loads(path.read_text())
    report['selected_rule']='functional_only'
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='G2_0006_CONTRACT:selected_rule'):
        m.run(**args)
    assert not args['output'].exists()
