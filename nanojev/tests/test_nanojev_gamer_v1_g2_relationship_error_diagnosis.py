"""Frozen graph-level G2-0012 loss attribution and provenance tests."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import math
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_relationship_error_diagnosis as m
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES
from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
import random


def test_cli_is_diagnostic_and_has_no_training_flags():
    p=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-relationship-error-diagnosis','--help'],
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
    assert p.returncode==0,p.stderr
    assert '--robustness-run' in p.stdout and '--device' in p.stdout and '--limit' in p.stdout
    assert '--steps' not in p.stdout and '--threshold' not in p.stdout


def test_fresh_rows_deduplicate_and_balance():
    previous=[]
    for i in range(3):
        previous.append([encounter(random.Random(i),'dev',i),
                         allocation(random.Random(100+i),'dev',i)])
    generated=m.fresh_rows(previous,limit=12)
    hashes=[base._sha(row['prompt']) for group in previous for row in group]
    assert len(generated)==12
    assert not set(hashes)&{base._sha(row['prompt']) for row in generated}
    assert Counter(row['family'] for row in generated)=={'encounter':6,'allocation':6}
    with pytest.raises(ValueError,match='BAD_COHORT'):
        m.fresh_rows(previous,limit=3)


def sample():
    a=(0,2,'entity');b=(8,10,'object');c=(30,32,'object')
    # Use native relation names. The kind names here are for offline comparison
    # and do not go through the schema's domain-specific compatibility filter.
    rel=BIND_CLASSES[1]
    gold={(a,b,rel),(a,c,rel)}
    anchors=[a,b,c]
    parent={(a,b,rel),(a,c,rel),(b,c,rel)}
    rank=torch.zeros(3,3,len(BIND_CLASSES))
    rank[0,1,1]=5;rank[0,2,1]=5;rank[1,2,1]=5
    item={'index':1,'prompt_sha256':'a'*64,'family':'encounter',
          'anchors':anchors,'gold':gold,'parent':parent,
          'edges':sorted(parent),'candidate_features':[
            (a,b,rel,.9,2.,2,0.),(a,c,rel,.8,1.5,2,.5),
            (b,c,rel,.75,1.2,1,0.)]}
    row={'object_spans':[{'start':x[0],'end':x[1],'kind':x[2]} for x in anchors]}
    return item,row,rank,{(rel,a[2],b[2]),(rel,a[2],c[2]),(rel,b[2],c[2])}


def test_correct_multiple_relationships_are_not_forced_exclusive():
    item,row,rank,allowed=sample()
    selected={(item['anchors'][0],item['anchors'][1],BIND_CLASSES[1]),
              (item['anchors'][0],item['anchors'][2],BIND_CLASSES[1])}
    result=m.diagnose_case(item,row,allowed,rank,selected)
    assert result['gold_failure_boundary']=={'retained_correct':2}
    assert result['retention_false_removed']==1
    assert result['retention_correct_removed']==0
    assert result['selected_false']==0
    assert result['candidate_true_count']==2
    assert result['candidate_false_count']==1
    assert m.aggregate_diagnosis([result])['all']['gold_in_candidate_inventory_count']==2
    assert m._rank_auc([(1.,True),(2.,True),(0.,False)])==1.
    assert m._rank_auc([(1.,False),(2.,False)]) is None


def test_gold_failure_boundaries_cover_every_edge_once():
    item,row,rank,allowed=sample()
    a,b,c=item['anchors'];relation=BIND_CLASSES[1]
    assert m.classify_gold((a,b,relation),item['anchors'],allowed,
                           rank.softmax(-1),item['parent'],set(),set(item['edges']))=='g2_0010_retention_filter'
    assert m.classify_gold((a,b,relation),item['anchors'],allowed,
                           rank.softmax(-1),set(),set(),set(item['edges']))=='g2_0005_density_competition_filter'
    wrong=rank.clone();wrong[0,1,:]=0;wrong[0,1,0]=10
    assert m.classify_gold((a,b,relation),item['anchors'],allowed,
                           wrong.softmax(-1),item['parent'],set(),set(item['edges']))=='rank_argmax_null'
    assert m.classify_gold((a,b,relation),item['anchors'],set(),
                           rank.softmax(-1),item['parent'],set(),set(item['edges']))=='train_compatibility_exclusion'
    assert m.classify_gold((a,(50,53,'object'),relation),item['anchors'],allowed,
                           rank.softmax(-1),item['parent'],set(),set(item['edges']))=='g1_destination_missing'
    assert m.classify_gold(((60,63,'entity'),(50,53,'object'),relation),item['anchors'],allowed,
                           rank.softmax(-1),item['parent'],set(),set(item['edges']))=='g1_both_endpoints_missing'
    assert m.classify_false((a,c,relation),{(a,b,relation)},set(item['anchors']))=='wrong_destination_same_source_relation'
    assert m.classify_false((b,c,relation),{(a,b,relation)},set(item['anchors']))=='gold_nodes_without_that_relation'
    assert m.classify_false((a,b,BIND_CLASSES[2]),{(a,b,relation)},set(item['anchors']))=='wrong_relation_on_true_pair'
    with pytest.raises(ValueError,match='NEW_EDGE'):
        m.diagnose_case(item,row,allowed,rank,{(c,b,relation)})


def test_missing_gold_and_selected_false_are_separate():
    item,row,rank,allowed=sample()
    a,b,c=item['anchors'];relation=BIND_CLASSES[1]
    result=m.diagnose_case(item,row,allowed,rank,{(b,c,relation),(a,b,relation)})
    assert result['gold_failure_boundary']['retained_correct']==1
    assert result['gold_failure_boundary']['g2_0010_retention_filter']==1
    assert result['selected_false_analyses']['association']['wrong_source_same_destination_relation']==1
    grouped=m.aggregate_diagnosis([result])
    assert grouped['all']['totals']['gold_relationships']==2
    assert grouped['all']['totals']['selected_false']==1
    assert grouped['all']['recall']==.5
    assert grouped['crowded_49_128']['cases']==0
    tampered=dict(result,retention_correct_removed=500)
    with pytest.raises(ValueError,match='AGGREGATION_DRIFT'):
        m.aggregate_diagnosis([tampered])


def test_parent_0011_uploaded_saved_metrics_recompute():
    # This test is self-contained on Windows; optional local uploaded artifact
    # is NOT a test dependency. The synthetic fixture covers recomputation.
    from tools import nanojev_gamer_v1_g2_robustness_validation as prior
    from test_nanojev_gamer_v1_g2_robustness_validation import make_cases
    rows=make_cases()
    screen=prior.screen(rows,min_crowded=2,bootstrap_draws=60)
    assert screen['robustness_pass']
    assert screen['paired_bootstrap']['lower_95']>0


def test_complete_frozen_diagnostic_runner_with_mocked_lineage(monkeypatch,tmp_path):
    """End-to-end CPU path; stub only unavailable certified 0011 toy lineage."""
    from test_nanojev_gamer_v1_g2_crowded_recall_calibration import _fixture as previous_fixture
    from tools import nanojev_gamer_v1_g2_crowded_recall_calibration as crowd10
    from tools import nanojev_gamer_v1_g2_robustness_validation as robust11
    args,seen=previous_fixture(monkeypatch,tmp_path)
    # Preexisting small fixture has no crowded graphs. Admit the mocked parent
    # explicitly; the G2-0012 analysis and source/score accounting stay real.
    def fake_gates(*_args):return {'test_only_parent_gate':True}
    def fake_choose(*_args):
        return robust11.SELECTED,{'reason':'TEST_ONLY_PARENT_PRESELECTION'},\
               {name:fake_gates() for name in m.recall9._rule_names()}
    monkeypatch.setattr(crowd10,'_gates',fake_gates)
    monkeypatch.setattr(crowd10,'choose_rule',fake_choose)
    monkeypatch.setattr(crowd10,'VALIDATION_LIMIT',4)
    crowd10.run(**args)
    robust_dir=tmp_path/'robustness'
    robust_dir.mkdir()
    (robust_dir/robust11.REPORT).write_text(json.dumps({'cohort_limit':4}))
    monkeypatch.setattr(m,'verify_robust11_parent',lambda *a,**k: ({'robustness_pass':True},{'mocked_parent':'toy'}))
    inputs={k:args[k] for k in ('root','g1_validation_report','g2_run','selection_run',
        'discrimination_run','relational_run','density_run','consistency_run','context_run','learned_run','recall_run')}
    inputs.update(crowded_run=args['output'],robustness_run=robust_dir,
                  output=tmp_path/'diagnosis',device='cpu',limit=4)
    result=m.run(**inputs)
    assert result['independent_cases']==4 and result['new_trainable_parameters']==0
    assert result['no_training'] and result['no_decoder_change']
    assert result['analysis_by_density']['all']['cases']==4
    assert result['analysis_by_density']['all']['totals']['gold_relationships']>0
    assert result['analysis_by_density']['all']['gold_reachable_by_g1_count']>=0
    assert not result['graph_execution_authorized']
    records=[json.loads(s) for s in (inputs['output']/m.CASES).read_text().splitlines()]
    assert len(records)==4
    assert seen[-1]=='diagnosis_new_independent'
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**inputs)
    with pytest.raises(ValueError,match='G2_0010'):
        bad=dict(inputs,output=tmp_path/'invalid')
        (args['output']/crowd10.REPORT).write_text(
            json.dumps({**json.loads((args['output']/crowd10.REPORT).read_text()),
                        'selected_independent_screen_pass':False}))
        m.run(**bad)
    assert not bad['output'].exists()
