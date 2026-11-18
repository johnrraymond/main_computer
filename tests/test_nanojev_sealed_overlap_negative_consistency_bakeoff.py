"""Offline controls only; actual Qwen outcome requires the CUDA bakeoff."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import inspect
import json

import pytest

from tools import nanojev_sealed_overlap_negative_consistency_bakeoff as lab
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress
from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as interface
from tools import nanojev_sealed_overlap_semantic_interface_repair_bakeoff as repair
from tools import nanojev_sealed_overlap_lexicon_blind_bakeoff as blind
from tools import nanojev_sealed_overlap_scope_obligation_bakeoff as scope
from tests.test_nanojev_sealed_overlap_semantic_interface_bakeoff import synthetic_encoder


def context():
    b = binding.load_fixture(binding.FIXTURE)
    s = stress.load_fixture(stress.FIXTURE, b)
    i = interface.load_fixture(interface.FIXTURE, b, s)
    r = repair.load_fixture(repair.FIXTURE, b, i, s)
    bf = blind.load_fixture(blind.FIXTURE, b, s, i, r)
    sf = scope.load_fixture(scope.FIXTURE, b, s, i, r, bf)
    f = lab.load_fixture(lab.FIXTURE, b, s, i, r, bf, sf)
    return b,s,i,r,bf,sf,f


def old_and_fresh(groups=8):
    b,s,i,r,bf,sf,f = context()
    data = stress.derive_data(b,s,seed=261117,train_atoms=216,selection_groups=16,final_groups=24)
    old = scope.make_holdout(data,b,s,i,r,bf,sf,seed=261117,groups=24)
    fresh = lab.make_fresh(data,b,s,i,r,bf,sf,f,seed=261117,groups=groups)
    return old,fresh


def synthetic_prior_report(tmp_path):
    p={'schema':scope.SCHEMA,'status':scope.STATUS,
       'acceptance':{'no_model_weight_updates':True,'no_final_based_model_selection':True},
       'design':{'view':'final:mean','frozen_training_examples':648},
       'sources':{k:sha256(v.read_bytes()).hexdigest() for k,v in (
           ('base_fixture_sha256',binding.FIXTURE),
           ('stress_fixture_sha256',stress.FIXTURE),
           ('interface_fixture_sha256',interface.FIXTURE),
           ('repair_fixture_sha256',repair.FIXTURE),
           ('blind_fixture_sha256',blind.FIXTURE),
           ('scope_fixture_sha256',scope.FIXTURE))},
       'fresh':{'scope_with_polarity_grounding':{'aggregate':{'documents':600,'supported_accepted':216,
                 'unsafe_accepted':0,'false_confident_wrong':6}}}}
    dest=tmp_path/'prior.json';dest.write_text(json.dumps(p));return dest


def test_new_templates_all_distinct_prior_and_semantically_sealed():
    b,s,i,r,bf,sf,f=context()
    old,fresh=old_and_fresh()
    assert len(f['challenges'])==17 and sum(x['policy']=='supported' for x in f['challenges'])==9
    assert len(fresh)==136
    assert len({r['text'] for r in fresh})==len(fresh)
    assert {r['text'] for r in fresh}.isdisjoint({r['text'] for r in old})
    assert len(binding.split_document(fresh[0]['text']))==2
    for row in fresh:
        assert row['truth_atoms'] is None if row['policy']=='abstain' else row['truth_atoms'] is not None
        assert row['kind'] not in row['text']


def test_fresh_pair_provenance_new_and_no_previous_pair_reuse():
    b,s,i,r,bf,sf,f=context()
    data=stress.derive_data(b,s,seed=261117,train_atoms=216,selection_groups=16,final_groups=24)
    old=scope.make_holdout(data,b,s,i,r,bf,sf,seed=261117,groups=24)
    fresh=lab.make_fresh(data,b,s,i,r,bf,sf,f,seed=261117,groups=24)
    old_pairs={tuple(sorted(v for v in row['truth_atoms'])) for row in old if row['kind']=='operative_affirm'}
    new_pairs={tuple(sorted(v for v in row['truth_atoms'])) for row in fresh if row['kind']=='pos_control'}
    assert len(new_pairs)==24 and not new_pairs & old_pairs
    assert len(fresh)==408


def test_grounded_six_fields_for_all_supported_old_and_fresh():
    b,s,i,r,_,_,_=context()
    old,fresh=old_and_fresh()
    for row in old+fresh:
        if row['policy']!='supported':continue
        gold=row['truth_atoms']
        # Artificial wrong model predictions: the independent text extractor
        # may repair only supported, complete, uniquely observed bindings.
        prediction=[tuple(['Mira','Arlen','sensor',b['conditions'][0],b['consequences'][0],1])]*len(gold)
        accepted, revised, why, provenance=lab.grounded_document(row['text'],prediction,b,r['reference_markers'],repair_mode=True)
        assert accepted,(row['id'],why)
        assert repair.graph(revised,b)==repair.graph(gold,b),row['id']
        assert len(provenance['observed'])==len(gold)


def test_unsupported_and_ambiguous_are_fail_closed_not_guessing():
    b,s,i,r,_,_,_=context()
    _,fresh=old_and_fresh()
    pred=[('Mira','Arlen','sensor',b['conditions'][0],b['consequences'][0],1)]*2
    for row in fresh:
        if row['policy']=='abstain':
            ok,revised,why,info=lab.grounded_document(row['text'],pred,b,r['reference_markers'],repair_mode=True)
            assert not ok,(row['id'],why)
            assert revised==pred
            assert why


def test_consensus_rejects_mismatch_repair_corrects_only_grounded_text():
    b,s,i,r,_,_,_=context()
    _,fresh=old_and_fresh()
    row=next(x for x in fresh if x['kind']=='neg_need_not_paraphrase')
    pred=[tuple(['Mira','Arlen','sensor',b['conditions'][0],b['consequences'][0],1])]*2
    consensus=lab.grounded_document(row['text'],pred,b,r['reference_markers'],repair_mode=False)
    fixed=lab.grounded_document(row['text'],pred,b,r['reference_markers'],repair_mode=True)
    assert consensus[0] is False and 'EVIDENCE_DISAGREES_WITH_MODEL' in consensus[2]
    assert consensus[1]==pred
    assert fixed[0] and repair.graph(fixed[1],b)==repair.graph(row['truth_atoms'],b)


def test_scope_not_distinguished_from_duty_not():
    b,s,i,r,_,_,_=context()
    _,fresh=old_and_fresh()
    for kind,p in [('pos_condition_negated',1),('neg_condition_negated',-1)]:
        row=next(x for x in fresh if x['kind']==kind)
        fields,why=lab.observe_clause_fields(binding.split_document(row['text'])[0],b,r['reference_markers'],None)
        assert fields['condition'].startswith('(not ')
        assert fields['polarity']==p and not why


def test_not_tuning_to_old_six_gold_and_no_model_retraining():
    for fn in (lab.observe_clause_fields,lab.grounded_document,lab.predict_baseline):
        src=inspect.getsource(fn)
        for marker in ("['truth_atoms']","['kind']","['policy']","['gold']"):
            assert marker not in src
    src=inspect.getsource(lab.run)
    assert 'blind.train_frozen' in src
    assert 'scope.overlay_supported_signs' in inspect.getsource(lab.predict_baseline)


def test_bad_fixture_and_prior_fail_fast(tmp_path):
    b,s,i,r,bf,sf,f=context()
    bad=deepcopy(f)
    bad['challenges'][0]['template']=sf['challenges'][0]['template']
    path=tmp_path/'bad.json';path.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='TEMPLATE_REUSE_OR_LEAK'):
        lab.load_fixture(path,b,s,i,r,bf,sf)
    prior=synthetic_prior_report(tmp_path)
    paths={'base_fixture_sha256':binding.FIXTURE,'stress_fixture_sha256':stress.FIXTURE,
           'interface_fixture_sha256':interface.FIXTURE,'repair_fixture_sha256':repair.FIXTURE,
           'blind_fixture_sha256':blind.FIXTURE,'scope_fixture_sha256':scope.FIXTURE}
    assert lab.validate_prior(prior,paths)['schema']==scope.SCHEMA
    corrupted=json.loads(prior.read_text());corrupted['fresh']['scope_with_polarity_grounding']['aggregate']['false_confident_wrong']=0
    prior.write_text(json.dumps(corrupted))
    with pytest.raises(ValueError,match='NEGCONS_PRIOR_REPORT_INVALID'):
        lab.validate_prior(prior,paths)


def test_full_frozen_synthetic_and_no_report_overwrite(tmp_path):
    b,s,i,r,bf,sf,f=context()
    prior=synthetic_prior_report(tmp_path)
    output=tmp_path/'result.json'
    args=lab.parser().parse_args(['--device','cpu','--groups','8','--prior-report',str(prior),'--output',str(output)])
    result=lab.run(args,encoder=synthetic_encoder(b))
    assert result['status']==lab.STATUS
    assert result['design']['fresh_holdout_rows']==136
    for source in (result['historical_diagnostic'],result['new_holdout']):
        counts=source['arms']['evidence_repair']['aggregate']
        assert counts['false_confident_wrong']==0
        assert counts['unsafe_accepted']==0
        assert counts['supported_accepted']==counts['supported_total']
    assert result['acceptance_targets']['fresh_zero_confident_wrong']
    assert result['acceptance_targets']['historical_supported_preserved']
    assert output.is_file()
    with pytest.raises(FileExistsError,match='REFUSE_OVERWRITE'):
        lab.run(args,encoder=synthetic_encoder(b))
