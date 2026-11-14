"""G2-0010 frozen weights, prospective crowded recall selection and replay."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import random
import subprocess
import sys

import pytest

from tools import nanojev_gamer_v1_g2_crowded_recall_calibration as m
from tools import nanojev_gamer_v1_g2_recall_preserving_graph as recall9
from tools import nanojev_gamer_v1_g2_learned_graph_context as learned8
from tools import nanojev_gamer_v1_g2_contextual_graph as context7
from tools import nanojev_gamer_v1_g2_graph_consistency as graph6
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
from test_nanojev_gamer_v1_g2_recall_preserving_graph import _fixture as parent_fixture


def test_cli_registered():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
                           'frob-g2-crowded-recall-calibration','--help'],
                          cwd=Path(__file__).resolve().parents[1],capture_output=True,
                          text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert '--recall-run' in result.stdout and '--validation-limit' in result.stdout
    assert '--steps' not in result.stdout # Entire scorer is frozen.


def test_new_cohort_excludes_every_predecessor():
    train=[encounter(random.Random(10),'train',1),allocation(random.Random(11),'train',2)]
    first=base.novel_dev_rows(train,limit=4,seed=base.SEED)
    second=sel.new_validation_rows(train,first,limit=4)
    third=disc.new_validation_rows(train,first,second,limit=4)
    fourth=rel.new_validation_rows(train,first,second,third,limit=4)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=4)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=4)
    seventh=context7.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=4)
    eighth=learned8.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,limit=4)
    ninth=recall9.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,limit=4)
    fresh=m.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,limit=8)
    assert Counter(row['family'] for row in fresh)=={'encounter':4,'allocation':4}
    old={base._sha(row['prompt']) for cohort in
         (train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth) for row in cohort}
    assert not set(base._sha(row['prompt']) for row in fresh)&old
    with pytest.raises(ValueError,match='BAD_COHORT'):
        m.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,ninth,limit=3)


def _calibration():
    parent={'f1':.42,'recall':.63,'false_positive':1400}
    metrics={m.PARENT:parent}
    for name in recall9._rule_names():
        metrics[name]={'f1':.40,'recall':.63,'false_positive':1400}
        metrics[name.replace('retain_','no_context_',1)]={'f1':.415,'recall':.63,'false_positive':1399}
    metrics['retain_w04_p30']={'f1':.47,'recall':.624,'false_positive':1200}
    metrics['retain_w08_p40']={'f1':.45,'recall':.626,'false_positive':1250}
    crowded={m.PARENT:{'f1':.32,'recall':.50,'cases':40}}
    for name in recall9._rule_names():
        crowded[name]={'f1':.30,'recall':.50,'cases':40}
    crowded['retain_w04_p30']={'f1':.36,'recall':.477,'cases':40}
    crowded['retain_w08_p40']={'f1':.34,'recall':.492,'cases':40}
    return metrics,{m.CROWDED:crowded}


def test_crowded_recall_constraint_changes_selection_without_independent_peek():
    metrics,buckets=_calibration()
    chosen,selection,gates=m.choose_rule(metrics,buckets)
    assert chosen=='retain_w08_p40',selection
    assert selection['eligible_count']==1
    assert not gates['retain_w04_p30']['crowded_recall_at_least_parent_minus_002']
    # G2-0009's previous rule wins F1 but cannot win once the crowded gate is imposed.
    assert metrics['retain_w04_p30']['f1']>metrics[chosen]['f1']
    # Failing global recall and the separately trained no-context ablation are also binding.
    metrics['retain_w08_p40']['recall']=.619
    assert m.choose_rule(metrics,buckets)[0] is None
    metrics['retain_w08_p40']['recall']=.626
    metrics['no_context_w08_p40']['f1']=.46
    assert m.choose_rule(metrics,buckets)[0] is None
    buckets[m.CROWDED]={m.PARENT:{'cases':0}}
    assert m.choose_rule(metrics,buckets)[0] is None


def _fixture(monkeypatch,tmp_path):
    args,seen=parent_fixture(monkeypatch,tmp_path)
    monkeypatch.setattr(recall9,'VALIDATION_LIMIT',4)
    recall_dir=tmp_path/'recall-run'
    recall9.run(**args)
    out_args={k:args[k] for k in ('root','g1_validation_report','g2_run','selection_run',
        'discrimination_run','relational_run','density_run','consistency_run','context_run','learned_run')}
    out_args.update(recall_run=recall_dir,output=tmp_path/'crowded-run',
                    device='cpu',validation_limit=4)
    return out_args,seen


def test_complete_frozen_runner_with_mocked_parents(monkeypatch,tmp_path):
    args,seen=_fixture(monkeypatch,tmp_path)
    report=m.run(**args)
    assert report['new_trainable_parameters']==0
    assert report['g2_0009_prediction_replay_pass']
    assert report['g2_0009_retention_weights_frozen']
    assert report['calibration_source']=='PREVIOUSLY_INSPECTED_G2_0009_INDEPENDENT_DEV'
    assert report['independent_cases']==4 and report['calibration_cases']==4
    assert report['selected_rule'] is None # Toy fixture does not have crowded nodes.
    assert report['calibration_selection']['eligible_count']==0
    assert not report['g2_production_unlocked'] and not report['production_promotion']
    assert not report['selected_independent_screen_pass']
    assert (args['output']/m.REPORT).is_file()
    assert len((args['output']/m.CASES).read_text().splitlines())==8
    assert set(report['calibration_metrics'])==set(report['independent_metrics'])
    assert seen[-2:]==['g2_0009_replay','new_independent']
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**args)


def test_tampered_parent_rejected_before_any_new_output(monkeypatch,tmp_path):
    args,_=_fixture(monkeypatch,tmp_path)
    report_path=args['recall_run']/recall9.REPORT
    report=json.loads(report_path.read_text())
    report['independent_metrics'][m.PARENT]['correct']+=1
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='G2_0009_METRICS'):
        m.run(**args)
    assert not args['output'].exists()


def test_tampered_checkpoint_rejected_before_output(monkeypatch,tmp_path):
    args,_=_fixture(monkeypatch,tmp_path)
    with (args['recall_run']/recall9.WEIGHTS).open('ab') as fh:fh.write(b'bad')
    with pytest.raises(ValueError,match='G2_0009_WEIGHT_HASH'):
        m.run(**args)
    assert not args['output'].exists()
