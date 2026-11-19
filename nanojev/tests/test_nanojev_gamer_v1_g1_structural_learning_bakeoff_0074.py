"""CPU-only contracts for G1-0074 structural competition."""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as m
from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as prior
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


def sample_case():
    def feat(kind,margin=.5,base=1.):
        return prior._feature(margin=margin,p_cond=.85,width=2,n=6,first=1,
            kind=kind,original=True,base=base,top=True)
    options=[
        prior.Option(0,1,m.ACTOR,2.,feat(m.ACTOR),(0,1,'actor'),True),
        prior.Option(1,5,m.ACTION,1.8,feat(m.ACTION),(1,5,'action'),True),
        prior.Option(1,3,m.ACTION,2.,feat(m.ACTION),(1,3,'action'),True),
        prior.Option(3,5,m.ACTION,2.,feat(m.ACTION),(3,5,'action'),True),
        prior.Option(1,5,m.ACTOR,0.9,feat(m.ACTOR),(1,5,'actor'),False),
        prior.Option(5,6,SPAN_KINDS.index('quantity')+1,3.,
                     feat(SPAN_KINDS.index('quantity')+1),(5,6,'quantity'),True),
    ]
    return prior.Case('cutover','train','encounter','dummy',6,options,
        {(0,1,'actor'),(1,3,'action'),(3,5,'action'),(5,6,'quantity')},
        {(0,1,'actor'),(1,5,'action'),(5,6,'quantity')},[])


def test_control_and_features_are_label_blind():
    case=sample_case(); e=m._encode(case)
    assert len(e.features)==len(case.options)
    assert all(len(f)==m.DIM for f in e.features)
    assert all(all(abs(v)<100 for v in f) for f in e.features)
    assert m._predict(e,m.CONTROL,None)==case.control
    wrong=copy.deepcopy(case);wrong.gold={(17,18,'actor')}
    assert m._encode(wrong).features==e.features
    assert 'gold' not in inspect.signature(m._phi_features).parameters
    assert 'gold' not in inspect.signature(m._predict).parameters
    assert 'gold' not in inspect.signature(m._scores).parameters


def test_whole_span_fragment_and_competing_type_features():
    e=m._encode(sample_case())
    whole=e.features[1]
    assert whole[16]>0  # nontrivial positive fragment pair
    assert whole[17]<0  # full option lower than split alternative
    assert whole[31]==0 # actor-only last feature not action
    assert e.features[4][31] != 0 # actor versus competing action span


def test_viterbi_inference_and_oracle_train_only():
    e=m._encode(sample_case())
    base=[o.base*(o.end-o.start) for o in e.case.options]
    predicted=m._viterbi(e,base)
    assert 1 not in predicted # fragments beat whole under base scores
    oracle=m._viterbi(e,base,oracle_gold=e.case.gold)
    assert 1 in oracle
    assert 2 not in oracle and 3 not in oracle
    with pytest.raises(ValueError,match='ORACLE_CONFLICT'):
        m._viterbi(e,base,oracle_gold=e.case.gold,loss_gold=e.case.gold)


def test_score_reweighting_and_invalid_weights():
    e=m._encode(sample_case())
    zeros=((0.,)*m.DIM,)
    scores=m._scores(e,'whole_span_boundary',zeros)
    assert scores==[o.base*(o.end-o.start) for o in e.case.options]
    actor_only=((1.,)*m.DIM,)
    assert m._scores(e,'whole_span_boundary',actor_only)[0]==scores[0]
    assert m._scores(e,'whole_span_boundary',actor_only)[1]!=scores[1]
    joint=((1.,)*m.DIM,(-1.,)*m.DIM)
    j=m._scores(e,'joint_type_admission',joint)
    assert j[0]!=scores[0] and j[1]!=scores[1]
    assert j[-1]==scores[-1]
    with pytest.raises(ValueError,match='INVALID_WEIGHTS'):
        m._scores(e,'joint_type_admission',zeros)
    with pytest.raises(ValueError,match='INVALID_WEIGHTS'):
        m._scores(e,'structured_set_maxmargin',((float('nan'),)*m.DIM,))


def test_boundary_and_joint_supervision_differ():
    train=[m._encode(sample_case()) for _ in range(3)]
    pos,neg,diff=m._hard_action_rows(train)
    assert len(pos)>0 and len(neg)>0 and len(diff)>0
    rows,prior_logits,targets,counts=m._grouped_type_rows(train)
    assert len(rows)==len(targets)==len(prior_logits)
    assert all(x in (0,m.ACTOR,m.ACTION) for x in targets)
    assert all(v>0 for v in counts.values())


def test_three_models_fit_deterministically_and_predict():
    train=[m._encode(sample_case()) for _ in range(4)]
    boundary,_=m._train_boundary(train,steps=4)
    joint,_=m._train_joint(train,steps=4)
    struct,diag=m._train_structured(train,epochs=3)
    assert len(boundary)==len(struct)==1 and len(joint)==2
    assert diag['epochs']==3
    assert boundary==m._train_boundary(train,steps=4)[0]
    assert joint==m._train_joint(train,steps=4)[0]
    assert struct==m._train_structured(train,epochs=3)[0]
    weights={m.CONTROL:None,'whole_span_boundary':boundary,
             'joint_type_admission':joint,'structured_set_maxmargin':struct}
    for arm in m.ARMS:
        assert isinstance(m._predict(train[0],arm,weights[arm]),set)
    results,records=m._metrics(train,weights)
    assert len(results)==len(records)==len(m.ARMS)
    assert all(result['cases']==4 for result in results.values())
    assert m._details(train,weights,m.CONTROL)[0]['selected_exact'] is False


def test_selection_accepts_only_complete_nonregressive_change():
    def score(exact,f1=0.95,precision=.98,actor=.83):
        return {'exact_prompts':exact,'f1':f1,'precision':precision,
                'by_kind':{k:{'recall':actor if k=='actor' else .99} for k in m.REQUIRED_KINDS}}
    values={arm:score(3) for arm in m.ARMS}
    assert m._choose(values)==m.CONTROL
    values['whole_span_boundary']=score(4)
    assert m._choose(values)=='whole_span_boundary'
    values['whole_span_boundary']=score(6,actor=.81)
    assert m._choose(values)==m.CONTROL
    values['joint_type_admission']=score(5,precision=.97)
    assert m._choose(values)==m.CONTROL


def test_actual_reference_lineage_and_parity(tmp_path):
    source=Path('/mnt/data/oracle-fabric-g1-learned-interface-bakeoff-0001.zip')
    if not source.exists():pytest.skip('Actual G1-0073 report not mounted')
    reference=tmp_path/'ref';reference.mkdir()
    with ZipFile(source) as z:
        (reference/'report.json').write_bytes(z.read('oracle-fabric-g1-learned-interface-bakeoff-0001/report.json'))
    doc=json.loads((reference/'report.json').read_text())
    entries={name:{'manifest':{'head_sha256':digest}}
             for name,digest in doc['lineage']['checkpoint_sha256'].items()}
    cohort={name:[None]*n for name,n in doc['design']['cohorts'].items()}
    observed,fp=m._require_previous(reference,entries,doc['lineage']['dataset_sha256'],cohort,
                                     doc['design']['historical_challenge_cases'])
    assert observed==doc and len(fp)==64
    for phase in ('train','selection','audit','challenge'):
        for model in ('cutover','legacy'):
            control=copy.deepcopy(doc['phases'][phase][model]['arms'][m.CONTROL])
            m._check_phase_parity(doc,phase,model,{m.CONTROL:control})
            control['correct']+=1
            with pytest.raises(ValueError,match='CONTROL_DRIFT'):
                m._check_phase_parity(doc,phase,model,{m.CONTROL:control})
    corrupt=copy.deepcopy(doc)
    corrupt['selected']['cutover']='pointwise_admission'
    (reference/'report.json').write_text(json.dumps(corrupt))
    with pytest.raises(ValueError,match='PREVIOUS_LINEAGE'):
        m._require_previous(reference,entries,doc['lineage']['dataset_sha256'],cohort,32)


def test_no_training_of_g1_or_promotion_hooks():
    source=inspect.getsource(m.run)
    assert "'g1_head_weights_modified':False" in source
    assert "'production_promotion':False" in source
    assert "'g2_unlocked':False" in source
    assert "'certification_authorized':False" in source
    assert 'head.optimizer' not in source
    assert 'optimizer.step(' not in source
    assert m.ARMS[0]==prior.CONTROL
    assert len(set(m.ARMS))==4
