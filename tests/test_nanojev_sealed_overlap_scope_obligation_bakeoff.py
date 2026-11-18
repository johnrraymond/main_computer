"""CPU-only tests: semantic scope, operative status, selective acceptance.

Real Qwen accuracy requires a separate CUDA run; synthetic encoders here are
only for flow/shape/regression checks.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import inspect
import json

import pytest

from tools import nanojev_sealed_overlap_scope_obligation_bakeoff as scope
from tools import nanojev_sealed_overlap_lexicon_blind_bakeoff as blind
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress
from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as interface
from tools import nanojev_sealed_overlap_semantic_interface_repair_bakeoff as repair
from tests.test_nanojev_sealed_overlap_semantic_interface_bakeoff import synthetic_encoder


def context():
    b=binding.load_fixture(binding.FIXTURE)
    s=stress.load_fixture(stress.FIXTURE,b)
    i=interface.load_fixture(interface.FIXTURE,b,s)
    r=repair.load_fixture(repair.FIXTURE,b,i,s)
    x=blind.load_fixture(blind.FIXTURE,b,s,i,r)
    f=scope.load_fixture(scope.FIXTURE,b,s,i,r,x)
    return b,s,i,r,x,f


def holdout(groups=8):
    b,s,i,r,x,f=context()
    data=stress.derive_data(b,s,seed=261117,train_atoms=216,selection_groups=16,final_groups=24)
    return scope.make_holdout(data,b,s,i,r,x,f,seed=261117,groups=groups)


def fake_report(tmp_path):
    b,s,i,r,x,f=context()
    raw={'schema':blind.SCHEMA,'status':blind.STATUS,
         'acceptance':{'no_model_weight_updates':True,'no_final_based_model_selection':True},
         'design':{'view':'final:mean','train_examples':648},
         'results':{'aggregate':{'documents':432,'false_confident_wrong':30,'accepted_indeterminate':24}},
         'sources':{k:sha256(p.read_bytes()).hexdigest() for k,p in (
             ('base_fixture_sha256',binding.FIXTURE),
             ('stress_fixture_sha256',stress.FIXTURE),
             ('interface_fixture_sha256',interface.FIXTURE),
             ('repair_fixture_sha256',repair.FIXTURE),
             ('blind_fixture_sha256',blind.FIXTURE))}}
    p=tmp_path/'prior.json';p.write_text(json.dumps(raw));return p


def test_fixture_fresh_and_golden_semantics():
    b,s,i,r,x,f=context()
    assert len(f['challenges'])==25
    assert sum(q['policy']=='supported' for q in f['challenges'])==9
    assert len({q['template'] for q in f['challenges']})==25
    rows=holdout()
    assert len(rows)==200 and len({q['text'] for q in rows})==200
    for row in rows:
        assert len(binding.split_document(row['text']))==2
        if row['kind'].startswith('condition_not'):
            assert repair.condition_node(row['truth_atoms'][0][3],b)[0]=='not'
        if row['kind'].startswith('double_not'):
            assert row['truth_atoms'][0][-1]==1


def test_fresh_pairs_exclude_inspected_blind_and_previous():
    b,s,i,r,x,f=context()
    data=stress.derive_data(b,s,seed=261117,train_atoms=216,selection_groups=16,final_groups=24)
    old=blind.make_holdout(data,b,s,i,r,x,seed=261117,groups=24)
    now=scope.make_holdout(data,b,s,i,r,x,f,seed=261117,groups=24)
    assert len(now)==600
    assert {a['text'] for a in now}.isdisjoint({a['text'] for a in old})
    older={tuple(sorted(tuple(a) for a in q['truth_atoms'])) for q in old if q['kind']=='known_affirm'}
    newer={tuple(sorted(tuple(a) for a in q['truth_atoms'])) for q in now if q['kind']=='operative_affirm'}
    assert not older&newer


@pytest.mark.parametrize('phrase,polarity',[
  ('a duty exists',1),('no duty exists',-1),('is not required to',-1),
  ('it is not true that no duty exists',1),
  ('it is not the case that no duty exists',1),
  ('it is false that no duty exists',1),
  ('need not transfer',-1),
])
def test_scoped_duty_signs(phrase,polarity):
    result,why=scope.clause_scope_polarity('After the permit is approved, '+phrase)
    assert result==polarity and why==()


@pytest.mark.parametrize('phrase,expected',[
  ('a provision rejects the allegation that a duty exists','DENIED_ASSERTION_NOT_DUTY'),
  ('the board denies the petition claiming a duty exists','DENIED_ASSERTION_NOT_DUTY'),
  ('a draft reports that a duty exists','NONOPERATIVE_PROPOSAL_OR_DRAFT'),
  ('a proposal would impose a duty','NONOPERATIVE_PROPOSAL_OR_DRAFT'),
  ("a discarded quotation 'a duty exists'",'QUOTED_SOURCE_NOT_OPERATIVE'),
  ('there is no evidence that a duty exists','EPISTEMIC_UNCERTAINTY'),
  ('it is uncertain whether a duty exists','EPISTEMIC_UNCERTAINTY'),
  ('suppose a duty exists','HYPOTHETICAL_NOT_OPERATIVE'),
  ('a witness states that it is not true that a duty exists','META_DENIAL_NOT_NEGATIVE_DUTY'),
  ('unless the permit is approved, a duty exists','UNSUPPORTED_SCOPE_OPERATOR'),
])
def test_nonoperative_or_unsupported_never_infers_duty(phrase,expected):
    polarity,reasons=scope.clause_scope_polarity(phrase)
    assert polarity is None and expected in reasons


def test_condition_negation_not_obligation_negation():
    b,_,_,r,_,_=context()
    for k,sign in [('condition_not_duty_yes',1),('condition_not_duty_no',-1)]:
        doc=next(row['text'] for row in holdout() if row['kind']==k)
        ok,why,signs=scope.scope_gate(doc,b,r['reference_markers'])
        assert ok,why
        assert signs[0]==sign
        assert '(not ' in doc


def test_all_fresh_gate_expectations_and_baseline_canaries():
    b,_,_,r,_,_=context()
    rows=holdout(24)
    for row in rows:
        ok,why,_=scope.scope_gate(row['text'],b,r['reference_markers'])
        assert ok==(row['policy']=='supported'),(row['id'],why)
    a={row['kind']:row for row in rows}
    for kind in ('rejected_allegation','rejected_petition','reported_draft','no_evidence_claim','uncertain_duty','hypothetical_clause'):
        assert blind.fail_closed_gate(a[kind]['text'],b,r['reference_markers'])[0]
        assert not scope.scope_gate(a[kind]['text'],b,r['reference_markers'])[0]


def test_gate_supports_old_canaries_without_remodeling():
    b,s,i,r,x,f=context()
    data=stress.derive_data(b,s,seed=261117,train_atoms=216,selection_groups=16,final_groups=24)
    audit=scope.historic_gate_audit(data,b,s,i,r,x,seed=261117)
    assert audit['rows']==432
    for bad in ('denied_assertion','reported_draft_only'):
        assert audit['inspected_canaries'][bad]['baseline_accepted']==24
        assert audit['inspected_canaries'][bad]['repaired_accepted']==0
    for good in ('known_affirm','known_deny','scoped_not'):
        assert audit['inspected_canaries'][good]['repaired_accepted']==24


def test_rejection_only_vs_polarity_overlay_are_separate():
    b,_,_,r,_,_=context()
    rows=holdout()
    selected=[row for row in rows if row['kind']=='double_not_true_no_duty']
    wrong=[]
    for row in selected:
        gold=row['truth_atoms']
        wrong.append([gold[0][:-1]+(-1,),gold[1]])
    baseline=scope.score(selected,wrong,b,r['reference_markers'],arm='scope_rejection_only')
    fixed=scope.overlay_supported_signs(wrong,[r['text'] for r in selected],b,r['reference_markers'])
    repaired=scope.score(selected,fixed,b,r['reference_markers'],arm='scope_with_polarity_grounding')
    assert baseline['aggregate']['raw_graph_exact']==0
    assert repaired['aggregate']['raw_graph_exact']==1
    assert baseline['aggregate']['coverage']==repaired['aggregate']['coverage']==1
    assert all(f[0][-1]==1 for f in fixed)


def test_repaired_arm_never_rewrites_abstained_predictions():
    b,_,_,r,_,_=context()
    row=next(q for q in holdout() if q['kind']=='reported_draft')
    pred=[[('Mira','Arlen','sensor',b['conditions'][0],b['consequences'][0],1),
           ('Mira','Arlen','sensor',b['conditions'][0],b['consequences'][0],1)]]
    assert scope.overlay_supported_signs(pred,[row['text']],b,r['reference_markers'])==pred


def test_counts_rejects_and_correctness_not_conflated():
    b,_,_,r,_,_=context()
    rows=holdout()
    predictions=[]
    for row in rows:
        pred=row['truth_atoms'] if row['truth_atoms'] is not None else [
            ('Mira','Arlen','sensor',b['conditions'][0],b['consequences'][0],1)]*2
        predictions.append(pred)
    fresh=scope.score(rows,predictions,b,r['reference_markers'],arm='scope_rejection_only')['aggregate']
    old=scope.score(rows,predictions,b,r['reference_markers'],arm='untouched_gate')['aggregate']
    assert fresh['unsafe_accepted']==0
    assert fresh['supported_accepted']==8*9
    assert fresh['accepted_indeterminate']==0
    assert fresh['false_confident_wrong']==0
    assert old['unsafe_accepted']>0
    assert old['accepted_indeterminate']>0


def test_source_no_gold_in_scope_gate_or_overlay():
    for f in (scope.scope_gate,scope.clause_scope_polarity,scope.overlay_supported_signs):
        src=inspect.getsource(f)
        for forbidden in ("['truth_atoms']", "['policy']", "['kind']",'row.gold','challenge_truth'):
            assert forbidden not in src


def test_tampered_fixture_or_prior_rejected(tmp_path):
    b,s,i,r,x,f=context()
    bad=deepcopy(f)
    bad['challenges'][0]['template']=x['challenges'][0]['template']
    p=tmp_path/'bad.json';p.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='TEMPLATE_REUSE'):
        scope.load_fixture(p,b,s,i,r,x)
    report=fake_report(tmp_path)
    a=scope.validate_prior(report,binding.FIXTURE,stress.FIXTURE,interface.FIXTURE,repair.FIXTURE,blind.FIXTURE)
    a['sources']['repair_fixture_sha256']='tampered';report.write_text(json.dumps(a))
    with pytest.raises(ValueError,match='PRIOR_REPORT_INVALID'):
        scope.validate_prior(report,binding.FIXTURE,stress.FIXTURE,interface.FIXTURE,repair.FIXTURE,blind.FIXTURE)


def test_synthetic_end_to_end_and_no_overwrite(tmp_path):
    b,_,_,_,_,_=context()
    prior=fake_report(tmp_path)
    out=tmp_path/'report.json'
    args=scope.parser().parse_args(['--device','cpu','--prior-report',str(prior),
                                   '--groups','8','--output',str(out)])
    result=scope.run(args,encoder=synthetic_encoder(b))
    assert result['status']==scope.STATUS
    assert result['design']['fresh_documents']==200
    assert result['design']['frozen_training_examples']==648
    assert result['fresh']['scope_rejection_only']['aggregate']['unsafe_accepted']==0
    assert result['fresh']['scope_rejection_only']['aggregate']['supported_accepted']==72
    assert result['acceptance']['no_model_weight_updates']
    assert json.loads(out.read_text())['sources']['scope_fixture_sha256']==sha256(scope.FIXTURE.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError,match='REFUSE_OVERWRITE'):
        scope.run(args,encoder=synthetic_encoder(b))

@pytest.mark.parametrize('ambiguous',[ 
  'the parties do not dispute that a duty exists',
  'no one can deny that a duty exists',
  'a duty exists but not necessarily for every supplier',
  'it is never clear whether no duty exists',
  'the actor has not yet agreed that a duty exists',
])
def test_unrecognized_negation_never_becomes_authoritative(ambiguous):
    sign,reasons=scope.clause_scope_polarity(ambiguous)
    assert sign is None and reasons
