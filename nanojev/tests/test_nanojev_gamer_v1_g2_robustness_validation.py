"""G2-0011 frozen prospective multi-cohort development robustness gates."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import random
import subprocess
import sys

import pytest

from tools import nanojev_gamer_v1_g2_robustness_validation as m
from tools import nanojev_gamer_v1_g2_crowded_recall_calibration as crowd10
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation


def test_cli_registered_no_train_or_selection_flags():
    p=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
                      'frob-g2-robustness-validation','--help'],
                     cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
    assert p.returncode==0,p.stderr
    for opt in ('--crowded-run','--cohort-count','--cohort-limit','--bootstrap-draws'):
        assert opt in p.stdout
    assert '--steps' not in p.stdout and '--threshold' not in p.stdout


def test_four_cohorts_are_disjoint_and_balanced():
    predecessors=[]
    for i in range(11):
        predecessors.append([encounter(random.Random(i),'dev',i),
                             allocation(random.Random(i+100),'dev',i)])
    # A random generator can repeat prompts between tiny synthetic predecessors;
    # the new cohorts must still exclude all of them and each other.
    cohorts=m.new_cohorts(*predecessors,cohort_count=4,limit=8)
    old={base._sha(row['prompt']) for group in predecessors for row in group}
    hashes=[base._sha(row['prompt']) for group in cohorts for row in group]
    assert len(hashes)==32 and len(hashes)==len(set(hashes))
    assert not old.intersection(hashes)
    assert all(Counter(row['family'] for row in cohort)=={'encounter':4,'allocation':4}
               for cohort in cohorts)
    with pytest.raises(ValueError,match='BAD_COHORT_CONFIG'):
        m.new_cohorts(*predecessors,cohort_count=1,limit=4)
    with pytest.raises(ValueError,match='BAD_COHORT_CONFIG'):
        m.new_cohorts(*predecessors,cohort_count=4,limit=3)


def make_cases(*,cohort_count=4,per_cohort=8,drop_crowded=False,neutral=False):
    rows=[]
    for cohort in range(1,cohort_count+1):
        for index in range(1,per_cohort+1):
            crowded=index<=per_cohort//2
            parent={'correct':3,'false_positive':7,'missing':2,'exact':False}
            selected={'correct':3,'false_positive':7 if neutral else 5,'missing':2,'exact':False}
            control={'correct':3,'false_positive':7 if neutral else 6,'missing':2,'exact':False}
            rows.append({'group':'new_independent_validation','cohort':cohort,'index':index,
                         'family':'encounter' if index%2 else 'allocation',
                         'density_bucket':('crowded_49_128' if crowded and not drop_crowded else 'small_1_24'),
                         'g1_predicted_nodes':64 if crowded and not drop_crowded else 12,
                         'configurations':{m.PARENT:parent.copy(),m.SELECTED:selected.copy(),m.CONTROL:control.copy()}})
    return rows


def test_robustness_screen_strict_and_paired():
    result=m.screen(make_cases(),min_crowded=2,bootstrap_draws=250)
    assert result['robustness_pass']
    assert result['paired_bootstrap']['lower_95']>0
    assert all(value['pass'] for value in result['by_cohort'].values())
    assert result['aggregate_metrics'][m.SELECTED]['false_positive']<result['aggregate_metrics'][m.PARENT]['false_positive']
    bad=make_cases()
    for row in bad:
        if row['cohort']==3:
            row['configurations'][m.SELECTED]['correct']=2
            row['configurations'][m.SELECTED]['missing']=3
    result=m.screen(bad,min_crowded=2,bootstrap_draws=250)
    assert not result['robustness_pass']
    assert not result['by_cohort'][3]['pass']
    assert not result['robustness_gates']['all_cohorts_pass_individually']
    no_crowd=m.screen(make_cases(drop_crowded=True),min_crowded=2,bootstrap_draws=250)
    assert not no_crowd['robustness_pass']
    assert not no_crowd['by_cohort'][1]['gates']['crowded_cases_present']
    assert not m.screen(make_cases(cohort_count=2),min_crowded=2,bootstrap_draws=250)['robustness_pass']


def test_bootstrap_deterministic_and_null_has_zero_gain():
    cases=make_cases()
    a=m.paired_bootstrap(cases,draws=200)
    assert m.paired_bootstrap(cases,draws=200)==a
    assert a['method'].startswith('PAIRED_PROMPT')
    assert m.paired_bootstrap(make_cases(neutral=True),draws=200)['lower_95']==0
    with pytest.raises(ValueError,match='BAD_BOOTSTRAP'):
        m.paired_bootstrap(cases,draws=2)


def test_complete_frozen_runner_with_mocked_parent(monkeypatch,tmp_path):
    from test_nanojev_gamer_v1_g2_crowded_recall_calibration import _fixture as parent_fixture
    args,seen=parent_fixture(monkeypatch,tmp_path)
    # The tiny toy graph has no crowded rows. Synthesize only a *parent gate*
    # that already selected the arm, to exercise the complete frozen 0011 runner.
    # The G2-0011 screening logic remains real and must fail the tiny audit.
    def assumed_parent_gates(*_args):
        return {'test_gated':True}
    def assumed_parent_selection(*_args):
        return m.SELECTED,{'eligible_count':1,'reason':'TOY_PRESELECTED'},\
            {name:assumed_parent_gates() for name in m.recall9._rule_names()}
    monkeypatch.setattr(crowd10,'_gates',assumed_parent_gates)
    monkeypatch.setattr(crowd10,'choose_rule',assumed_parent_selection)
    monkeypatch.setattr(crowd10,'VALIDATION_LIMIT',4)
    crowd10.run(**args)
    inputs={k:args[k] for k in ('root','g1_validation_report','g2_run','selection_run',
        'discrimination_run','relational_run','density_run','consistency_run',
        'context_run','learned_run','recall_run')}
    inputs.update(crowded_run=args['output'],output=tmp_path/'robustness-run',
                  device='cpu',cohort_count=4,cohort_limit=4,bootstrap_draws=60)
    result=m.run(**inputs)
    assert result['selected_rule']==m.SELECTED
    assert result['g2_0010_parent_records_verified'] and result['g2_0010_selected_rule_frozen']
    assert result['new_trainable_parameters']==0
    assert result['total_cases']==16 and len(result['cohorts'])==4
    assert set(result['by_cohort'])=={1,2,3,4}
    assert not result['robustness_pass']  # 4-prompt cohorts cannot meet crowded minimum
    assert not result['production_promotion'] and not result['graph_execution_authorized']
    assert len((inputs['output']/m.CASES).read_text().splitlines())==16
    assert seen[-4:]==['robustness_01','robustness_02','robustness_03','robustness_04']
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**inputs)
    # Even a forged successful parent selection must fail if altered.
    report_path=args['output']/crowd10.REPORT
    prior=json.loads(report_path.read_text())
    forged=dict(prior,selected_independent_screen_pass=False)
    report_path.write_text(json.dumps(forged))
    attempts=dict(inputs,output=tmp_path/'refused-invalid-parent')
    with pytest.raises(ValueError,match='G2_0010_CONTRACT'):
        m.run(**attempts)
    assert not attempts['output'].exists()
