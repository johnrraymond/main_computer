"""Offline controls for scope-preserving typed representation and grammar intervention."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as lab
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress


def fixtures():
    base=binding.load_fixture(binding.FIXTURE)
    previous=stress.load_fixture(stress.FIXTURE,base)
    ours=lab.load_fixture(lab.FIXTURE,base,previous)
    return base,previous,ours


def small_data():
    base,prev,ours=fixtures()
    data=stress.derive_data(base,prev,seed=261117,train_atoms=96,
                             selection_groups=8,final_groups=8)
    return data,lab.fresh_data(data,base,prev,ours,seed=261117,groups=8)


def synthetic_encoder(base):
    """Text only: counts lexical items, does not access gold or challenge metadata."""
    lex=[word for opts in binding.values(base)[:-1] for word in opts]
    def encode(texts):
        x=np.zeros((len(texts),len(lex)+8))
        for i,text in enumerate(texts):
            for j,word in enumerate(lex):
                x[i,j]=float(word in text)
            x[i,-8]=int('is not required' in text or 'no duty exists' in text or 'has no obligation' in text or 'does not impose' in text or 'does not obligate' in text)
            x[i,-7]=int('is required' in text or 'a duty exists' in text or 'is obligated' in text or 'imposes a duty' in text or 'obligates' in text)
            x[i,-6]=int('consequence' in text)
            x[i,-5]=int('outcome' in text)
            x[i,-4]=int('result' in text)
            x[i,-3]=int('under' in text.lower())
            x[i,-2]=int('provided' in text.lower())
            x[i,-1]=1.0
        return {'final:mean':x,'half:last':x}
    return encode


def prior_report(tmp_path,train_atoms):
    base,prev,_=fixtures()
    raw={'schema':stress.SCHEMA,'status':stress.STATUS,
         'acceptance':{'no_production_mutation':True,'no_final_based_model_selection':True},
         'design':{'same_six_field_supervision':True,'training_atomic_clauses':train_atoms},
         'sources':{'base_fixture_sha256':sha256(binding.FIXTURE.read_bytes()).hexdigest(),
                    'stress_fixture_sha256':sha256(stress.FIXTURE.read_bytes()).hexdigest()}}
    p=tmp_path/'source.json';p.write_text(json.dumps(raw),encoding='utf-8');return p


def test_fixture_disjoint_and_no_test_template_in_augmentation():
    base,prev,f=fixtures()
    old=set(sum((base['templates'][k] for k in ('train','selection','final')),[]))
    old.update(prev['selection_templates']+prev['final_templates'])
    assert not old.intersection(f['augmentation_templates']+f['fresh_templates'])
    assert set(f['augmentation_templates']).isdisjoint(f['fresh_templates'])
    data,_=small_data()
    aug=lab.augmented_training(data['train'],f)
    assert len(aug)==2*len(data['train'])
    assert [r['atom'] for r in aug]==[r['atom'] for r in data['train']]*2


def test_fixture_rejects_unauthorized_semantic_schema(tmp_path):
    base,prev,f=fixtures()
    f['fresh_templates'][0]='{actor} {asset}'
    p=tmp_path/'bad.json';p.write_text(json.dumps(f))
    with pytest.raises(ValueError,match='TEMPLATE_INVALID'):
        lab.load_fixture(p,base,prev)


def test_fresh_pairs_are_unseen_and_exactly_15_challenges():
    data,fresh=small_data()
    previous={tuple(sorted(row['atoms'])) for ph in ('selection','final') for row in data[ph] if row['kind']=='base'}
    now={tuple(sorted(row['atoms'])) for row in fresh if row['kind']=='base'}
    assert previous.isdisjoint(now)
    assert len(fresh)==8*len(stress.KINDS)==120
    assert len({r['text'] for r in fresh})==len(fresh)
    assert all(len(binding.split_document(r['text']))==len(r['atoms']) for r in fresh)
    assert all(lab.typed_graph(r['atoms'],fixtures()[0]['conditions']) for r in fresh)


def test_typed_ir_preserves_unknown_names_and_and_scope():
    base,prev,f=fixtures()
    condition=prev['nested_condition_connector'].format(first=base['conditions'][0],second=base['conditions'][1])
    a=('NewEntityXYZ','OtherEntityXYZ','sensor',condition,'access is suspended',1)
    b=('NewEntityXYZ','OtherEntityXYZ','sensor',base['conditions'][0],'access is suspended',-1)
    graph=lab.typed_graph([a,b],base['conditions'])
    assert graph[0]!=graph[1]
    assert not lab.typed_contradiction([a,b],base['conditions'])
    assert lab.typed_contradiction([a,a[:-1]+(-1,)],base['conditions'])
    assert lab.typed_graph([a,b],base['conditions'])==lab.typed_graph([b,a],base['conditions'])
    assert lab.condition_ast(condition,base['conditions'])[0]=='and'
    with pytest.raises(ValueError,match='CONDITION_UNSUPPORTED'):
        lab.condition_ast('a nested expression we cannot parse',base['conditions'])


def test_oov_copy_requires_single_unknown_and_known_counterparty():
    base,prev,f=fixtures()
    text='Nessa is required to deliver the sensor to Arlen after the permit is approved; access is suspended'
    norm,meta=lab.surface_transform(text,base,antecedent=None,reference_marker=f['reference_marker'])
    assert meta['novel_actor']=='Nessa' and meta['novel_recipient'] is None
    assert 'Nessa' not in norm and 'Mira' in norm
    text='Mira is required to deliver the sensor to Vero after the permit is approved; access is suspended'
    norm,meta=lab.surface_transform(text,base,antecedent=None,reference_marker=f['reference_marker'])
    assert meta['novel_recipient']=='Vero' and 'Arlen' in norm
    text='Nessa is required to deliver a sensor to Vero after the permit is approved; access is suspended'
    with pytest.raises(ValueError,match='MULTIPLE_UNKNOWN_ENTITIES'):
        lab.surface_transform(text,base,antecedent=None,reference_marker=f['reference_marker'])
    text='Nessa is required to deliver a sensor after the permit is approved; access is suspended'
    _,meta=lab.surface_transform(text,base,antecedent=None,reference_marker=f['reference_marker'])
    assert not meta['supported']


def test_literal_compound_and_reference_have_no_gold_shortcut():
    base,prev,f=fixtures()
    nested=prev['nested_condition_connector'].format(first=base['conditions'][0],second=base['conditions'][1])
    txt='Mira is required to deliver the sensor to Arlen if '+nested+'; access is suspended'
    normalized,meta=lab.surface_transform(txt,base,antecedent=None,reference_marker=f['reference_marker'])
    assert meta['compound']==nested and nested not in normalized
    txt='the same supplier is required to deliver the sensor to Arlen if the permit is approved; access is suspended'
    _,meta=lab.surface_transform(txt,base,antecedent=None,reference_marker=f['reference_marker'])
    assert not meta['supported'] and meta['reason']=='REFERENCE_NO_PRIOR_PREDICTED_ACTOR'
    normalized,meta=lab.surface_transform(txt,base,antecedent='Tarin',reference_marker=f['reference_marker'])
    assert 'Tarin' in normalized and meta['referenced_actor']=='Tarin'


def test_model_unary_equals_historical_for_unique_examples():
    base,_,_=fixtures()
    data,_=small_data(); mock=synthetic_encoder(base)
    x=mock([r['text'] for r in data['train']])['final:mean']
    labels=[r['atom'] for r in data['train']]
    new=lab.predict_model(lab.fit_model(x,labels,base,.1),x,base)
    old=binding.predict_atomic(binding.fit_shared_supervision(x,labels,base,ridge=.1),x,base)['factorized']
    assert new==old


def test_duplicate_control_training_is_valid():
    base,_,_=fixtures();data,_=small_data();encode=synthetic_encoder(base)
    x=encode([r['text'] for r in data['train']])['final:mean']
    labels=[r['atom'] for r in data['train']]
    m=lab.fit_model(np.vstack([x,x]),labels*2,base,.1)
    assert len(lab.predict_model(m,x,base))==len(labels)


def test_source_provenance_rejects_tampering(tmp_path):
    source=prior_report(tmp_path,96)
    assert lab.validate_prior_report(source,binding.FIXTURE,stress.FIXTURE)['schema']==stress.SCHEMA
    raw=json.loads(source.read_text());raw['sources']['stress_fixture_sha256']='bad'
    source.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='PRIOR_REPORT_INVALID'):
        lab.validate_prior_report(source,binding.FIXTURE,stress.FIXTURE)


def test_full_synthetic_runner_and_refuse_overwrite(tmp_path):
    source=prior_report(tmp_path,96);base,_,_=fixtures()
    output=tmp_path/'report.json'
    args=lab.parser().parse_args(['--device','cpu','--output',str(output),
          '--prior-report',str(source),'--train-atoms','96',
          '--selection-groups','8','--final-groups','8','--fresh-groups','8'])
    result=lab.run(args,encoder=synthetic_encoder(base))
    assert result['status']==lab.STATUS
    assert result['typed_oracle']['fresh']['graph_exact']==1
    assert all(v==1 for v in result['typed_oracle']['fresh']['out_of_schema_graph_exact'].values())
    assert result['design']['historical_final_reused_diagnostic_only']
    assert set(result['fresh'])==set(lab.ARMS)
    assert result['design']['augmentation_examples']==192
    assert json.loads(output.read_text())['schema']==lab.SCHEMA
    with pytest.raises(FileExistsError,match='OUTPUT_EXISTS'):
        lab.run(args,encoder=synthetic_encoder(base))


def test_nested_scope_algebra_and_explicit_reference_are_lossless():
    base,_,_=fixtures()
    a,b,c=base['conditions']
    nested={'op':'and','children':[
        {'op':'atom','value':a},
        {'op':'or','children':[
            {'op':'not','child':{'op':'atom','value':b}},
            {'op':'atom','value':c}
        ]}
    ]}
    permuted={'op':'and','children':[
        {'op':'or','children':[{'op':'atom','value':c},
                               {'op':'not','child':{'op':'atom','value':b}}]},
        {'op':'atom','value':a}
    ]}
    assert lab.condition_ast(nested,base['conditions'])==lab.condition_ast(permuted,base['conditions'])
    assert lab.condition_ast(nested,base['conditions'])[0]=='and'
    first=('Nessa','Vero','sensor',nested,'access is suspended',1)
    second=({'ref':'previous_actor'},'Arlen','sensor',nested,'access is suspended',-1)
    g=lab.typed_graph([first,second],base['conditions'])
    assert len(g)==2 and {t[0] for t in g}=={'Nessa'}
    assert not lab.typed_contradiction([first,second],base['conditions'])
    assert lab.typed_contradiction([first,({'ref':'previous_actor'},)+first[1:-1]+(-1,)],base['conditions'])
    with pytest.raises(ValueError,match='UNRESOLVED_REFERENCE'):
        lab.typed_graph([second],base['conditions'])
    with pytest.raises(ValueError,match='SCOPE_OPERATOR_UNSUPPORTED'):
        lab.condition_ast({'op':'xor','children':[]},base['conditions'])
    with pytest.raises(ValueError,match='SCOPE_DUPLICATE_CHILDREN'):
        lab.condition_ast({'op':'and','children':[{'op':'atom','value':a},{'op':'atom','value':a}]},base['conditions'])
