"""Offline lexicon-blind diagnostic tests; no CUDA or model download required."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from tools import nanojev_sealed_overlap_lexicon_blind_bakeoff as blind
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress
from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as interface
from tools import nanojev_sealed_overlap_semantic_interface_repair_bakeoff as repair
from tests.test_nanojev_sealed_overlap_semantic_interface_bakeoff import synthetic_encoder


def fixtures():
    b=binding.load_fixture(binding.FIXTURE)
    s=stress.load_fixture(stress.FIXTURE,b)
    i=interface.load_fixture(interface.FIXTURE,b,s)
    r=repair.load_fixture(repair.FIXTURE,b,i,s)
    x=blind.load_fixture(blind.FIXTURE,b,s,i,r)
    return b,s,i,r,x


def dataset(groups=8):
    b,s,i,r,x=fixtures()
    d=stress.derive_data(b,s,seed=261117,train_atoms=216,selection_groups=16,final_groups=24)
    return blind.make_holdout(d,b,s,i,r,x,seed=261117,groups=groups)


def test_fixture_and_exactly_sealed_templates():
    b,s,i,r,x=fixtures()
    assert len(x['challenges'])==18
    assert all(len(a['text'].split(' || '))==2 for a in dataset())
    assert len({row['text'] for row in dataset()})==144
    assert sum(q['policy']=='supported' for q in x['challenges'])==5
    assert sum(q['truth']=='indeterminate' for q in x['challenges'])==6


def test_no_gold_or_method_metamarkers_in_test_texts():
    rows=dataset()
    assert all(row['kind'] not in row['text'] for row in rows)
    assert all(row['truth_atoms'] is None for row in rows if row['kind'] in
               ('quoted_only','conflicting_duty','ambiguous_actor','ambiguous_condition','unresolved_pronoun','reported_draft_only'))
    assert all(row['truth_atoms'] is not None for row in rows if row['policy']=='supported')


def test_gold_semantics_of_scope_and_negation():
    rows=dataset()
    b,_,_,_,_=fixtures()
    for row in rows:
        if row['kind']=='scoped_not':
            assert repair.condition_node(row['truth_atoms'][0][3],b)[0]=='not'
            assert row['truth_atoms'][0][-1]==1
        if row['kind']=='double_negation':
            assert row['truth_atoms'][0][-1]==1
        if row['kind'] in ('unseen_exempt','unseen_need_not'):
            assert row['truth_atoms'][0][-1]==-1


def test_gate_accepts_known_and_exposes_two_uncaught_adversarial_cases():
    rows=dataset()
    base,_,_,r,_=fixtures()
    canaries={'denied_assertion','reported_draft_only'}
    for row in rows:
        accepted,issues=blind.fail_closed_gate(row['text'],base,r['reference_markers'])
        expected=(row['policy']=='supported' or row['kind'] in canaries)
        assert accepted == expected, (row['kind'],issues)
        assert bool(issues)==(not accepted)



def test_gate_rejects_quoted_modal_and_competing_names():
    base,_,_,r,_=fixtures()
    controls=dataset()
    true_sample=controls[0]['text']
    assert blind.fail_closed_gate(true_sample,base,r['reference_markers'])[0]
    reject=true_sample.replace('a duty exists',"a duty exists; 'no duty exists'")
    assert 'QUOTED_OR_ATTRIBUTED_DUTY' in blind.fail_closed_gate(reject,base,r['reference_markers'])[1]
    reject=true_sample.replace('the actor is Mira','the actor is Mira; the actor is also Leona')
    # The sample might use another actor. We nevertheless require rejecting
    # the independently authored ambiguous-actor cohort.
    ambiguous=next(row['text'] for row in controls if row['kind']=='ambiguous_actor')
    assert 'CONTRADICTORY_NAMED_ROLE_BINDING' in blind.fail_closed_gate(ambiguous,base,r['reference_markers'])[1]


def test_condition_negation_does_not_flip_obligation():
    base,_,_,r,_=fixtures()
    text=next(row['text'] for row in dataset() if row['kind']=='scoped_not').split(' || ')[0]
    assert repair.extract_polarity(text)==1
    assert blind.fail_closed_gate(text+'.',base,r['reference_markers'])[0]


def test_mutated_fixture_leak_and_invalid_truth_refused(tmp_path):
    b,s,i,r,x=fixtures()
    bad=deepcopy(x)
    bad['challenges'][0]['template']=r['targeted_training_templates'][0]
    p=tmp_path/'bad.json';p.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='TEMPLATE_LEAK'):
        blind.load_fixture(p,b,s,i,r)
    bad=deepcopy(x);bad['challenges'][0]['truth']='make up a label';p.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='EXPECTATIONS_INVALID'):
        blind.load_fixture(p,b,s,i,r)


def report_source(tmp_path):
    doc={'schema':repair.SCHEMA,'status':repair.STATUS,
         'acceptance':{'no_production_mutation':True,'no_final_based_model_selection':True},
         'design':{'extraction':'final:mean','original_examples':216,
                   'generic_augmentation_examples':216,'targeted_augmentation_examples':216},
         'sources':{'base_fixture_sha256':sha256(binding.FIXTURE.read_bytes()).hexdigest(),
                    'stress_fixture_sha256':sha256(stress.FIXTURE.read_bytes()).hexdigest(),
                    'interface_fixture_sha256':sha256(interface.FIXTURE.read_bytes()).hexdigest(),
                    'repair_fixture_sha256':sha256(repair.FIXTURE.read_bytes()).hexdigest()},
         'new_holdout':{k:({'in_schema':{'graph_exact':.984848}} if k=='targeted_typed' else {})
                         for k in ('targeted_grammar','targeted_lexical','targeted_typed')}}
    p=tmp_path/'source.json';p.write_text(json.dumps(doc))
    return p


def test_provenance_accepts_prior_and_rejects_tampering(tmp_path):
    genuine=report_source(tmp_path)
    raw=blind.validate_prior(genuine,binding.FIXTURE,stress.FIXTURE,interface.FIXTURE,repair.FIXTURE)
    assert raw['design']['original_examples']==216
    forged=tmp_path/'forged.json'
    raw['sources']['repair_fixture_sha256']='bad';forged.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='PRIOR_REPORT_INVALID'):
        blind.validate_prior(forged,binding.FIXTURE,stress.FIXTURE,interface.FIXTURE,repair.FIXTURE)


def test_score_coverage_does_not_reward_abstention_as_accuracy():
    b,_,_,r,_=fixtures()
    rows=dataset()
    # Synthetic deliberately wrong predictions: rejection will still happen,
    # but zero correct accepted documents must not become 100% accuracy.
    wrong=[[("Mira","Arlen","sensor",b['conditions'][0],b['consequences'][0],1)]*2 for _ in rows]
    m=blind.score(rows,wrong,b,r['reference_markers'])
    assert m['aggregate']['selective_labeled_count']>0
    assert m['aggregate']['selective_graph_exact']==0
    assert m['aggregate']['unsafe_rejected']==8*11
    assert m['aggregate']['supported_accepted']==8*5
    assert m['aggregate']['false_confident_wrong']==8*6
    assert m['aggregate']['accepted_indeterminate']==8


def test_decoder_cannot_use_row_gold_or_challenge_labels():
    import inspect
    source=inspect.getsource(blind.predict_frozen)
    assert "['truth_atoms']" not in source
    assert "['kind']" not in source
    assert "['policy']" not in source
    assert 'fail_closed_gate' not in source


def test_full_default_synthetic_and_refusal_to_overwrite(tmp_path):
    genuine=report_source(tmp_path)
    b,_,_,_,_=fixtures()
    out=tmp_path/'report.json'
    args=blind.parser().parse_args(['--device','cpu','--prior-report',str(genuine),'--output',str(out),
                                     '--groups','8'])
    report=blind.run(args,encoder=synthetic_encoder(b))
    assert report['status']==blind.STATUS
    assert report['design']['train_examples']==648
    assert report['design']['documents']==144
    ag=report['results']['aggregate']
    assert ag['supported_total']==40
    assert ag['unsafe_total']==104
    assert ag['accepted_indeterminate']==8
    assert report['acceptance']['no_production_mutation']
    assert json.loads(out.read_text())['schema']==blind.SCHEMA
    with pytest.raises(FileExistsError,match='REFUSE_OVERWRITE'):
        blind.run(args,encoder=synthetic_encoder(b))
