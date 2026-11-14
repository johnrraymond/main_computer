"""G2-0015 frozen density-routing multi-cohort audit: no new model selection."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import copy
import json
import random
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_density_routing_robustness as m
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools.nanojev_gamer_v1_graph_corpus import allocation,encounter


def test_cli_exposes_frozen_density_robustness():
    r=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
          'frob-g2-density-routing-robustness','--help'],capture_output=True,text=True,
          cwd=Path(__file__).resolve().parents[1],timeout=30)
    assert r.returncode==0,r.stderr
    assert '--specialization-run' in r.stdout
    assert '--cohort-count' in r.stdout and '--bootstrap-draws' in r.stdout


def test_four_cohorts_balanced_and_no_prompt_overlap():
    predecessor=[encounter(random.Random(1),'dev',1),allocation(random.Random(11),'dev',1)]
    cohorts=m.new_cohorts([predecessor],limit=8)
    assert len(cohorts)==4
    hashes=[base._sha(row['prompt']) for group in [predecessor,*cohorts] for row in group]
    assert len(hashes)==len(set(hashes))
    assert all(Counter(row['family'] for row in cohort)=={'encounter':4,'allocation':4}
               for cohort in cohorts)
    with pytest.raises(ValueError,match='BAD_COHORT_CONFIG'):
        m.new_cohorts([predecessor],count=2,limit=8)
    with pytest.raises(ValueError,match='BAD_COHORT_CONFIG'):
        m.new_cohorts([predecessor],limit=7)
    with pytest.raises(ValueError,match='BAD_COHORT_CONFIG'):
        m.new_cohorts([])


def counts(tp,fp,fn):
    return {'correct':tp,'false_positive':fp,'missing':fn,'exact':tp>0 and fp==fn==0}


def _rows():
    records=[]
    for cohort in range(1,5):
        for i in range(32):
            bucket=('small_1_24' if i<8 else 'medium_25_48' if i<24 else 'crowded_49_128')
            parent=counts(14,10,6)
            chosen=counts(15,6,5) if bucket=='small_1_24' else parent.copy()
            control=counts(14,9,6) if bucket=='small_1_24' else parent.copy()
            records.append({'cohort':cohort,'index':i+1,'family':'encounter' if i%2 else 'allocation',
                           'density_bucket':bucket,
                           'configurations':{m.PARENT:parent.copy(),m.SELECTED:chosen,
                                             m.CONTROL:control}})
    return records


def test_screen_requires_every_cohort_and_paired_evidence():
    result=m.screen(_rows(),min_small=1,min_crowded=1,bootstrap_draws=60)
    assert result['robustness_pass']
    assert all(info['pass'] for info in result['by_cohort'].values())
    assert all(ci['lower_95']>0 for ci in result['paired_bootstrap'].values())
    assert result['aggregate_by_node_density']['medium_25_48'][m.PARENT]==\
           result['aggregate_by_node_density']['medium_25_48'][m.SELECTED]
    assert result['aggregate_by_node_density']['crowded_49_128'][m.PARENT]==\
           result['aggregate_by_node_density']['crowded_49_128'][m.SELECTED]
    assert not m.screen(_rows(),bootstrap_draws=50)['robustness_pass']


def test_cohort_failure_is_not_masked_by_aggregate_gain():
    rows=_rows()
    for row in rows:
        if row['cohort']==4 and row['density_bucket']=='small_1_24':
            row['configurations'][m.SELECTED]=counts(9,13,11)
    result=m.screen(rows,min_small=1,min_crowded=1,bootstrap_draws=50)
    assert not result['by_cohort'][4]['pass']
    assert not result['robustness_gates']['four_cohorts_pass_individually']
    assert not result['robustness_pass']


def test_medium_and_crowded_must_match_fallback_casewise():
    rows=_rows();row=next(r for r in rows if r['density_bucket']=='crowded_49_128')
    row['configurations'][m.SELECTED]=counts(11,9,9)
    result=m.screen(rows,min_small=1,min_crowded=1,bootstrap_draws=50)
    assert not result['by_cohort'][1]['gates']['crowded_fallback_casewise']
    assert not result['robustness_pass']
    rows=_rows();row=next(r for r in rows if r['density_bucket']=='medium_25_48')
    row['configurations'][m.SELECTED]=counts(11,9,9)
    result=m.screen(rows,min_small=1,min_crowded=1,bootstrap_draws=50)
    assert not result['by_cohort'][1]['gates']['medium_fallback_casewise']


def test_matched_control_must_be_outperformed():
    rows=_rows()
    for row in rows:
        row['configurations'][m.CONTROL]=row['configurations'][m.SELECTED].copy()
    result=m.screen(rows,min_small=1,min_crowded=1,bootstrap_draws=50)
    assert not result['aggregate_gates']['beats_matched_control']
    assert not result['robustness_gates']['matched_control_paired_ci_lower_above_zero']
    assert not result['robustness_pass']


def test_bootstrap_refuses_missing_cohort_and_invalid_parameters():
    with pytest.raises(ValueError,match='INCOMPLETE_BOOTSTRAP_COHORTS'):
        m.paired_bootstrap([r for r in _rows() if r['cohort']<4],draws=50)
    with pytest.raises(ValueError,match='BAD_BOOTSTRAP'):
        m.paired_bootstrap(_rows(),draws=5)
    a=m.paired_bootstrap(_rows(),draws=60)
    b=m.paired_bootstrap(_rows(),draws=60)
    assert a==b


def test_saved_case_structure_checks_gold_and_fallback():
    original=encounter(random.Random(22),'dev',22)
    gold=len(base.gold_binding_edges(original))
    baseline=counts(1,2,max(0,gold-1))
    row={'group':'new_independent_validation','index':1,
         'family':original['family'],'prompt_sha256':base._sha(original['prompt']),
         'gold_binding_edges':gold,'g1_predicted_nodes':30,
         'density_bucket':'medium_25_48',
         'configurations':{name:baseline.copy() for name in m.NAMES}}
    m._verify_saved_rows([row],[original],'new_independent_validation',m.NAMES)
    changed=copy.deepcopy(row)
    changed['configurations'][m.SELECTED]['false_positive']+=1
    with pytest.raises(ValueError,match='PARENT_FALLBACK_DRIFT'):
        m._verify_saved_rows([changed],[original],'new_independent_validation',m.NAMES)
    changed=copy.deepcopy(row);changed['configurations'][m.SELECTED]['correct']+=1
    with pytest.raises(ValueError,match='PARENT_CASE_COUNTS'):
        m._verify_saved_rows([changed],[original],'new_independent_validation',m.NAMES)


def test_parent_selection_and_checkpoint_contract_fail_closed(tmp_path):
    (tmp_path/m.specialization14.REPORT).write_text(json.dumps({'schema':'wrong'}))
    (tmp_path/m.specialization14.CASES).write_text('')
    (tmp_path/m.specialization14.WEIGHTS).write_bytes(b'bad')
    row=encounter(random.Random(1),'dev',1)
    with pytest.raises(ValueError,match='PARENT_CONTRACT'):
        m.verify_specialization14_parent(tmp_path,[row],[row],{})


def test_no_automatic_promotion_and_immutable_output_contract():
    assert m.SELECTED=='route__joint_60__fallback'
    assert m.PARENT=='retain_w08_p40'
    assert m.COHORTS==4 and m.COHORT_LIMIT==320
    assert all(key not in m.NAMES for key in ('route__joint_70__fallback',
                                              'route__joint_60__prune_70'))


def test_full_cpu_runner_frozen_route_end_to_end(monkeypatch,tmp_path):
    from test_nanojev_gamer_v1_g2_crowded_recall_calibration import _fixture
    args,_=_fixture(monkeypatch,tmp_path)
    monkeypatch.setattr(m.crowd10,'_gates',lambda *_:{'mocked':True})
    monkeypatch.setattr(m.crowd10,'choose_rule',lambda *_:(
        m.robust11.SELECTED,{'reason':'PRESELECTED'},
        {name:{'mocked':True} for name in m.recall9._rule_names()}))
    monkeypatch.setattr(m.crowd10,'VALIDATION_LIMIT',4)
    m.crowd10.run(**args)
    parent11=tmp_path/'robustness';parent11.mkdir()
    (parent11/m.robust11.REPORT).write_text(json.dumps({'cohort_limit':4,'cohort_count':4}))
    monkeypatch.setattr(m.robust11,'COHORT_LIMIT',4)
    monkeypatch.setattr(m.diagnosis12,'LIMIT',4)
    monkeypatch.setattr(m.source13,'VALIDATION_LIMIT',4)
    monkeypatch.setattr(m.specialization14,'VALIDATION_LIMIT',4)
    monkeypatch.setattr(m.diagnosis12,'verify_robust11_parent',lambda *a,**kw:({'pass':True},{'parent11':'toy'}))
    monkeypatch.setattr(m.source13,'verify_diagnosis_parent',lambda *a,**kw:({'pass':True},[],{'parent12':'toy'}))
    monkeypatch.setattr(m.specialization14,'verify_source13_parent',lambda *a,**kw:(
        {'new_weights_sha256':'toy'},[],{'parent13':'toy'}))
    monkeypatch.setattr(m,'verify_specialization14_parent',lambda *a,**kw:(
        {'selected_route':m.SELECTED,'independent_screen_pass':True},{'parent14':'toy'}))
    monkeypatch.setattr(m.specialization14,'load_source13_models',lambda path,width,sha:{
        'joint':m.source13.SourceConditionedScorer(width),
        'control':m.source13.SourceConditionedScorer(width)})
    original_evaluate=m.evaluate_frozen
    def verify_exact_0014_inference(captured,rank_head,joint,control,allowed,rule,
                                    context,retention,*,cohort,device):
        ours=original_evaluate(captured,rank_head,joint,control,allowed,rule,context,
                               retention,cohort=cohort,device=device)
        _,legacy=m.specialization14.eval_cases(
            captured,rank_head,joint,control,joint,control,allowed,rule,
            context,retention,device=device,group='parity_check',chosen_only=m.SELECTED)
        for new,old in zip(ours,legacy):
            assert new['density_bucket']==old['density_bucket']
            assert all(new['configurations'][name]==old['configurations'][name]
                       for name in m.NAMES)
        return ours
    monkeypatch.setattr(m,'evaluate_frozen',verify_exact_0014_inference)
    inputs={k:args[k] for k in ('root','g1_validation_report','g2_run','selection_run',
        'discrimination_run','relational_run','density_run','consistency_run',
        'context_run','learned_run','recall_run')}
    inputs.update(crowded_run=args['output'],robustness_run=parent11,
                  diagnosis_run=tmp_path/'diagnosis',source_run=tmp_path/'source13',
                  specialization_run=tmp_path/'specialization14',
                  output=tmp_path/'g2-0015',device='cpu',cohort_count=4,
                  cohort_limit=4,bootstrap_draws=50)
    result=m.run(**inputs)
    assert result['cohort_count']==4 and result['total_cases']==16
    assert result['selected_route']==m.SELECTED
    assert not result['g2_certified'] and not result['graph_execution_authorized']
    assert result['new_trainable_parameters']==0
    assert len((inputs['output']/m.CASES).read_text().splitlines())==16
    assert result['parent_hashes']['parent14']=='toy'
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**inputs)
