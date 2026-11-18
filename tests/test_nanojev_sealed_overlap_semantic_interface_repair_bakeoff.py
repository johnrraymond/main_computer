"""Offline tests of grammar/role repair without Qwen download or CUDA."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from tools import nanojev_sealed_overlap_semantic_interface_repair_bakeoff as repair
from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as previous
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress
from tests.test_nanojev_sealed_overlap_semantic_interface_bakeoff import synthetic_encoder


def inputs():
    base=binding.load_fixture(binding.FIXTURE)
    st=stress.load_fixture(stress.FIXTURE,base)
    old=previous.load_fixture(previous.FIXTURE,base,st)
    fixture=repair.load_fixture(repair.FIXTURE,base,old,st)
    return base,st,old,fixture


def dataset(n=96,groups=8):
    base,st,old,fixture=inputs()
    d=stress.derive_data(base,st,seed=261117,train_atoms=n,selection_groups=8,final_groups=8)
    fresh=repair.make_fresh(d,base,old,st,fixture,seed=261117,groups=groups)
    return d,fresh


def source(tmp_path,n=96):
    base,st,old,fix=inputs()
    doc={'schema':previous.SCHEMA,'status':previous.STATUS,
         'acceptance':{'no_production_mutation':True,'no_final_based_model_selection':True},
         'design':{'view':'final:mean','original_training_examples':n,'augmentation_examples':2*n,'no_new_distinct_gold_training_tuples':True},
         'sources':{
             'base_fixture_sha256':sha256(binding.FIXTURE.read_bytes()).hexdigest(),
             'stress_fixture_sha256':sha256(stress.FIXTURE.read_bytes()).hexdigest(),
             'interface_fixture_sha256':sha256(previous.FIXTURE.read_bytes()).hexdigest()},
         'fresh':{k:({'in_schema':{'by_field':{'asset':1.0,'polarity':1.0}},'by_grammar_template':{i:{} for i in ('0','1','2')}} if k=='grammar_augmented' else {}) for k in previous.ARMS}}
    out=tmp_path/'prior.json';out.write_text(json.dumps(doc))
    return out


def test_fixture_sealed_disjoint_and_labels_preserved():
    base,st,old,fixture=inputs()
    train=stress.derive_data(base,st,seed=261117,train_atoms=96,selection_groups=8,final_groups=8)['train']
    new=repair.targeted_training(train,fixture)
    assert len(new)==len(train)
    assert [x['atom'] for x in new]==[x['atom'] for x in train]
    assert len(set(x['text'] for x in new))==len(new)
    assert set(fixture['targeted_training_templates']).isdisjoint(fixture['new_holdout_templates'])


def test_fixture_bad_slots_and_leaked_template_rejected(tmp_path):
    base,st,old,fixture=inputs()
    wrong=deepcopy(fixture);wrong['new_holdout_templates'][0]=wrong['targeted_training_templates'][0]
    p=tmp_path/'bad.json';p.write_text(json.dumps(wrong))
    with pytest.raises(ValueError,match='TEMPLATE_INVALID'):
        repair.load_fixture(p,base,old,st)
    wrong=deepcopy(fixture);wrong['targeted_training_templates'][0]='{actor} {asset}'
    p.write_text(json.dumps(wrong))
    with pytest.raises(ValueError,match='TEMPLATE_INVALID'):
        repair.load_fixture(p,base,old,st)


def test_fresh_set_independent_of_both_inspected_holdouts():
    d,rows=dataset()
    base,st,old,fix=inputs()
    historical=previous.fresh_data(d,base,st,old,seed=261117,groups=8)
    old_pairs={tuple(sorted(x['atoms'])) for x in historical if x['kind']=='base'}
    old_pairs|={tuple(sorted(x['atoms'])) for phase in ('selection','final') for x in d[phase] if x['kind']=='base'}
    new_pairs={tuple(sorted(x['atoms'])) for x in rows if x['kind']=='base'}
    assert new_pairs.isdisjoint(old_pairs)
    assert len(rows)==8*len(repair.CHALLENGES)==144
    assert len(set(x['text'] for x in rows))==len(rows)
    assert all(len(x['template_ids'])==len(x['atoms']) for x in rows)
    assert all(repair.graph(x['atoms'],base) for x in rows)
    assert set(x['kind'] for x in rows)==set(repair.CHALLENGES)
    assert all(x['scope']=='extension' for x in rows if x['kind'] in repair.OOS)


def test_observed_polarity_denial_not_collapsed_into_affirmation():
    negatives=('is not required to','no duty exists','has no obligation to',
               'does not impose a duty of delivery','does not obligate','does not impose')
    positives=('is required to','a duty exists','is obligated to','imposes a duty of delivery','obligates')
    assert all(repair.extract_polarity('The contract '+x+' transfer')==-1 for x in negatives)
    assert all(repair.extract_polarity('The contract '+x+' transfer')==1 for x in positives)
    assert repair.extract_polarity('Provided not the audit is complete, a duty exists')==1
    assert repair.extract_polarity('The clause states nothing about a duty') is None
    assert repair.extract_polarity('a duty exists; no duty exists') is None


def test_assets_have_boundaries_and_ambiguity_abstains():
    base,_,_,_=inputs()
    assert repair.extract_asset('The transferred item is the ledger',base)=='ledger'
    assert repair.extract_asset('The transferred item is the ledger and sensor',base) is None
    assert repair.extract_asset('The transferred item is the ledgerbook',base) is None


def test_nested_logic_and_scope_preservation():
    base,_,_,_=inputs()
    a,b,c=base['conditions']
    first=f'({a} and ({b} or not {c}))'
    second=f'(({b} or not {c}) and {a})'
    assert repair.condition_node(first,base)==repair.condition_node(second,base)
    assert repair.condition_node(first,base)[0]=='and'
    assert repair.condition_node(f'({a} or {b})',base)!=repair.condition_node(f'({a} and {b})',base)
    assert repair.condition_node(f'({a} or not {b})',base)!=repair.condition_node(f'({a} or {b})',base)
    with pytest.raises(ValueError,match='BOOLEAN_UNCLOSED'):
        repair.condition_node(f'({a} and ({b} or {c})',base)
    with pytest.raises(ValueError,match='BOOLEAN_DUPLICATE'):
        repair.condition_node(f'({a} and {a})',base)
    with pytest.raises(ValueError,match='BOOLEAN_UNRECOGNIZED'):
        repair.condition_node('(an unknown condition)',base)
    x=('Mira','Arlen','sensor',first,'access is suspended',1)
    y=x[:3]+(a,)+x[4:-1]+(-1,)
    assert not repair.contradiction([x,y],base)
    assert repair.contradiction([x,x[:-1]+(-1,)],base)


def test_observed_role_binding_and_dual_oov():
    base,st,old,f=inputs()
    examples={
      'The actor is Nessa and the recipient is Vero':('Nessa','Vero'),
      'For Vero, delivery from Nessa is contemplated':('Nessa','Vero'),
      'Nessa is identified as supplier while Vero is beneficiary':('Nessa','Vero'),
      'the handover to Vero by Nessa is required':('Nessa','Vero'),
    }
    for sentence,expected in examples.items():
        roles,issue=repair.observed_roles(sentence,f['reference_markers'],None,base)
        assert issue is None and (roles['actor'],roles['recipient'])==expected
    roles,issue=repair.observed_roles('Nessa and Vero must transfer',f['reference_markers'],None,base)
    assert issue=='NOVEL_NAME_WITHOUT_ROLE_ANCHOR'
    roles,issue=repair.observed_roles('The actor is Nessa and from Quilla the sensor moves to Vero',f['reference_markers'],None,base)
    assert 'MULTIPLE_CANDIDATES' in issue


def test_reference_must_resolve_from_predicted_context():
    base,st,old,f=inputs()
    refs=f['reference_markers']
    s='From the immediately previous supplier, transfer to Arlen of a sensor'
    roles,issue=repair.observed_roles(s,refs,'Quilla',base)
    assert issue is None and roles['actor']=='Quilla'
    normalized=repair.normalize_for_model(s,base,roles,None,refs)
    assert 'the immediately previous supplier' not in normalized and 'Mira' in normalized
    roles,issue=repair.observed_roles(s,refs,None,base)
    assert 'UNRESOLVED' in issue
    s='The actor is Nessa and the same supplier delivers'
    roles,issue=repair.observed_roles(s,refs,'Mira',base)
    assert 'REFERENCE_ACTOR_COLLISION' in issue


def test_condition_surface_explicit_only():
    base,_,_,_=inputs()
    a,b,c=base['conditions']
    text=f'If ({a} and ({b} or not {c})), Mira must deliver'
    surface,error=repair.observed_condition(text,base)
    assert error is None and repair.condition_node(surface,base)[0]=='and'
    surface,error=repair.observed_condition('If (not an unlisted duty), Mira delivers',base)
    assert surface is None and error=='INVALID_BOOLEAN_SCOPE'
    surface,error=repair.observed_condition('If (the permit is approved, Mira delivers',base)
    assert surface is None and error=='UNCLOSED_BOOLEAN_SCOPE'


def test_report_provenance_rejects_tampering(tmp_path):
    path=source(tmp_path)
    assert repair.validate_prior(path,binding.FIXTURE,stress.FIXTURE,previous.FIXTURE)['schema']==previous.SCHEMA
    raw=json.loads(path.read_text());raw['sources']['interface_fixture_sha256']='tampered'
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='PRIOR_REPORT_INVALID'):
        repair.validate_prior(path,binding.FIXTURE,stress.FIXTURE,previous.FIXTURE)


def test_synthetic_full_path_and_nonoverwrite(tmp_path):
    prior=source(tmp_path)
    base,_,_,_=inputs()
    out=tmp_path/'new.json'
    args=repair.parser().parse_args(['--device','cpu','--prior-report',str(prior),'--output',str(out),
        '--train-atoms','96','--selection-groups','8','--final-groups','8','--groups','8'])
    report=repair.run(args,encoder=synthetic_encoder(base))
    assert report['status']==repair.STATUS
    assert report['design']['new_holdout_documents']==144
    assert report['design']['same_gold_field_supervision']
    assert report['acceptance']['no_production_mutation']
    assert set(report['new_holdout'])==set(repair.ARMS)
    assert report['typed_oracle']=={k:1 for k in repair.CHALLENGES}
    assert report['new_holdout']['targeted_typed']['by_challenge']['dual_oov']['graph_exact']>=0
    assert report['observed_text_intervention_counts']['asset_overlay']>0
    assert report['observed_text_intervention_counts']['actor_overlay']>0
    assert json.loads(out.read_text())['schema']==repair.SCHEMA
    with pytest.raises(FileExistsError,match='OUTPUT_EXISTS'):
        repair.run(args,encoder=synthetic_encoder(base))


def test_gold_labels_not_used_in_prediction(tmp_path):
    base,st,old,fix=inputs()
    data,rows=dataset()
    train=data['train'];generic=previous.augmented_training(train,old)[len(train):]
    synth=synthetic_encoder(base)
    texts=[r['text'] for r in train+generic]
    features=synth(texts)['final:mean']
    m=previous.fit_model(features,[r['atom'] for r in train]*2,base,.1)
    models={a:m for a in repair.ARMS[:3]}
    original,_=repair.evaluate(rows[:4],models,synth,base,fix)
    corrupted=deepcopy(rows[:4]);corrupted[0]['atoms']=[a[:-1]+(-a[-1],) for a in corrupted[0]['atoms']]
    changed,_=repair.evaluate(corrupted,models,synth,base,fix)
    # If inference secretly consumed evaluation targets, the flipped case would
    # remain correct. Real text-only inference stays unchanged and loses credit.
    assert original['targeted_typed']['by_challenge']['base']['graph_exact'] != changed['targeted_typed']['by_challenge']['base']['graph_exact'] or original['targeted_typed']['by_challenge']['base']['atomic_six_field_exact']!=changed['targeted_typed']['by_challenge']['base']['atomic_six_field_exact']
