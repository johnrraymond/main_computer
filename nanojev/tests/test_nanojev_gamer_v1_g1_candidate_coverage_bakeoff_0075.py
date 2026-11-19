"""Local CPU contracts for G1-0075; no heavyweight model or network load."""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import torch

from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as m
from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as prior
from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as learned
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


def sample():
    def feat(k,width,base):
        return learned._feature(margin=1.2,p_cond=.8,width=width,n=6,first=1,
            kind=k,original=True,base=base,top=True)
    actor=SPAN_KINDS.index('actor')+1
    action=SPAN_KINDS.index('action')+1
    quantity=SPAN_KINDS.index('quantity')+1
    original=[
        learned.Option(0,1,actor,2.,feat(actor,1,2.),(0,1,'actor'),True),
        learned.Option(1,5,action,1.,feat(action,4,1.),(1,5,'action'),True),
        learned.Option(1,3,action,1.2,feat(action,2,1.2),(1,3,'action'),True),
        learned.Option(3,5,action,1.2,feat(action,2,1.2),(3,5,'action'),True),
        learned.Option(5,6,quantity,2.,feat(quantity,1,2.),(5,6,'quantity'),True)]
    c=learned.Case('cutover','train','encounter','sha',6,original,
        {(0,1,'actor'),(1,3,'action'),(3,5,'action'),(5,6,'quantity')},
        set(),[])
    logits=torch.full((6,5,len(SPAN_KINDS)+1),-5.0)
    logits[:,:,0]=0.
    valid=torch.zeros((6,5),dtype=torch.bool)
    valid[0,0]=True; logits[0,0,actor]=4.
    valid[1,3]=True; logits[1,3,action]=4.
    valid[1,1]=True; logits[1,1,action]=3.
    valid[3,1]=True; logits[3,1,action]=3.
    valid[5,0]=True; logits[5,0,quantity]=4.
    # Extra whole-span actor, not in the previous candidate inventory.
    valid[2,0]=True; logits[2,0,actor]=2.5
    valid[3,0]=True; logits[3,0,action]=1.2
    prepared={'offsets':[(i,i+1) for i in range(6)],'prompt_start':0,'prompt':'abcdef'}
    return prior._encode(c),logits,valid,prepared


def expanded():
    old,logits,valid,prepared=sample()
    result=m._extend_lattice(old,logits,valid,prepared,budget=55)
    result=m._prepare_scores(result,((0.,)*m.SET_DIM,))
    return result


def test_raw_logit_expansion_is_real_and_preserves_old_features_and_scores():
    data=expanded()
    assert len(data.added.case.options)>len(data.base.case.options)
    assert any(opt.char_key==(2,3,'actor') for opt in data.added.case.options)
    assert data.discarded_by_budget==0
    assert all(len(f)==m.PROP_DIM for f in data.prop_features)
    originals={(o.start,o.end,o.kind):(f,s) for o,f,s in zip(data.base.case.options,
        data.base.features,prior._scores(data.base,m.CONTROL,((0.,)*m.SET_DIM,)))}
    for o,f,s in zip(data.added.case.options,data.added.features,data.base_scores):
        key=(o.start,o.end,o.kind)
        if key in originals:
            assert (f,s)==originals[key]
    assert m._decode(data,m.CONTROL,reference_weights=((0.,)*m.SET_DIM,))==prior._predict(
        data.base,m.CONTROL,((0.,)*m.SET_DIM,))
    assert (2,3,'actor') in data.raw_span_gold_types


def test_budget_preserves_baseline_and_fails_closed():
    base,logits,mask,prep=sample()
    data=m._extend_lattice(base,logits,mask,prep,budget=len(base.case.options))
    assert len(data.added.case.options)==len(base.case.options)
    assert not any(data.is_new) and data.discarded_by_budget>0
    with pytest.raises(ValueError,match='BUDGET'):
        m._extend_lattice(base,logits,mask,prep,budget=len(base.case.options)-1)
    with pytest.raises(ValueError,match='BUDGET'):
        m._extend_lattice(base,logits,mask,prep,budget=m.MAX_OPTIONS+1)
    logits[2,0,1]=float('nan')
    with pytest.raises(ValueError,match='NONFINITE'):
        m._extend_lattice(base,logits,mask,prep)


def test_inference_is_gold_blind_and_no_oracle_in_scoring_signature():
    d=expanded(); no_gold=m._inference_scores(d,m.FROZEN)
    assert len(no_gold)==len(d.added.case.options)
    assert 'gold' not in inspect.signature(m._inference_scores).parameters
    assert 'gold' not in inspect.signature(m._decode).parameters
    d.added.case.gold={(2,3,'actor'),(1,5,'action')}
    assert m._inference_scores(d,m.FROZEN)==no_gold
    p=(0.,)*m.PROP_DIM;s=(0.,)*m.SET_DIM
    assert m._inference_scores(d,m.PROPOSAL,p)==no_gold
    assert m._inference_scores(d,m.JOINT,p,s)==no_gold
    d.added.case.gold={(100,101,'outcome')}
    assert m._inference_scores(d,m.PROPOSAL,p)==no_gold
    with pytest.raises(ValueError,match='INVALID_LEARNED_WEIGHTS'):
        m._inference_scores(d,m.JOINT,p,(float('nan'),)*m.SET_DIM)


def test_local_and_joint_training_are_distinct_and_deterministic():
    data=expanded()
    data.added.case.gold={(2,3,'actor'),(1,5,'action'),(5,6,'quantity')}
    data.base.case.gold=set(data.added.case.gold)
    cohorts=[copy.deepcopy(data) for _ in range(3)]
    p,diag=m._fit_proposals(cohorts,steps=4)
    (jp,js),jdiag=m._fit_joint(cohorts,epochs=3)
    assert diag['positive']>0 and diag['hard_negative']>0
    assert len(p)==len(jp)==m.PROP_DIM and len(js)==m.SET_DIM
    assert p==m._fit_proposals(cohorts,steps=4)[0]
    assert (jp,js)==m._fit_joint(cohorts,epochs=3)[0]
    assert jdiag['joint_proposal_and_set_updates']
    params={m.CONTROL:(None,None),m.FROZEN:(None,None),m.PROPOSAL:(p,None),m.JOINT:(jp,js)}
    met,records=m._metrics(cohorts,params,((0.,)*m.SET_DIM,))
    assert set(met)==set(m.ARMS) and len(records[m.JOINT])==3
    assert m._details(cohorts,params,((0.,)*m.SET_DIM,),m.CONTROL)[0]['selected']==m.CONTROL
    ceilings=m._oracle_coverage(cohorts)
    assert ceilings['expanded_reachable_nonoverlap']['correct']>=ceilings['previous_reachable_nonoverlap']['correct']
    assert ceilings['all_valid_logit_coordinates_independent_upper_bound']['correct']>=ceilings['expanded_reachable_nonoverlap']['correct']


def test_reference_lineage_matches_actual_0074_report(tmp_path):
    source=Path('/mnt/data/oracle-fabric-g1-structural-learning-bakeoff-0001.zip')
    if not source.exists():pytest.skip('G1-0074 report not mounted')
    ref=tmp_path/'reference';ref.mkdir()
    with ZipFile(source) as z:
        (ref/'report.json').write_bytes(z.read('oracle-fabric-g1-structural-learning-bakeoff-0001/report.json'))
    report=json.loads((ref/'report.json').read_text())
    entries={name:{'manifest':{'head_sha256':sha}}
             for name,sha in report['lineage']['checkpoint_sha256'].items()}
    parts={p:[None]*n for p,n in report['design']['cohorts'].items()}
    actual,sha,weights=m._require_previous(ref,entries,report['lineage']['dataset_sha256'],
        parts,report['design']['historical_challenge_cases'])
    assert actual==report and len(sha)==64
    assert set(weights)=={'legacy','cutover'}
    for name in ('cutover','legacy'):
        for phase in ('train','selection','audit','challenge'):
            metrics=copy.deepcopy(report['phases'][phase][name]['selected_metrics'])
            m._check_phase_parity(report,phase,name,{m.CONTROL:metrics})
            metrics['exact_prompts']+=1
            with pytest.raises(ValueError,match='CONTROL_DRIFT'):
                m._check_phase_parity(report,phase,name,{m.CONTROL:metrics})
    report['selected']['legacy']='whole_span_boundary'
    (ref/'report.json').write_text(json.dumps(report))
    with pytest.raises(ValueError,match='REFERENCE_LINEAGE'):
        m._require_previous(ref,entries,report['lineage']['dataset_sha256'],parts,32)


def test_model_specific_predecessor_lattice_prior_is_exact(tmp_path):
    s74=Path('/mnt/data/oracle-fabric-g1-structural-learning-bakeoff-0001.zip')
    s73=Path('/mnt/data/oracle-fabric-g1-learned-interface-bakeoff-0001.zip')
    if not s74.exists() or not s73.exists():
        pytest.skip('G1-0074 or G1-0073 evidence not mounted')
    with ZipFile(s74) as z:
        report=json.loads(z.read('oracle-fabric-g1-structural-learning-bakeoff-0001/report.json'))
    ref=tmp_path/'learned';ref.mkdir()
    with ZipFile(s73) as z:
        (ref/'report.json').write_bytes(z.read('oracle-fabric-g1-learned-interface-bakeoff-0001/report.json'))
    source,digest=m._require_lattice_config(ref,report)
    assert source=={'cutover':'runnerup_joint_gap0p50_bonus0p50',
                    'legacy':'actor_prior_0p25'}
    assert digest==report['lineage']['learned_reference_sha256']
    bad=copy.deepcopy(report);bad['lineage']['learned_reference_sha256']='0'*64
    with pytest.raises(ValueError,match='REFERENCE_HASH_DRIFT'):
        m._require_lattice_config(ref,bad)


def test_selection_rejects_regressions():
    def metric(exact=5,actor=.83,f1=.97,precision=.98):
        return {'exact_prompts':exact,'f1':f1,'precision':precision,
           'by_kind':{k:{'recall':actor if k=='actor' else .94}
                      for k in m.REQUIRED_KINDS}}
    states={arm:metric() for arm in m.ARMS}
    assert m._choose(states)==m.CONTROL
    states[m.FROZEN]=metric(exact=6,actor=.82)
    assert m._choose(states)==m.CONTROL
    states[m.FROZEN]=metric(exact=6,precision=.97)
    assert m._choose(states)==m.CONTROL
    states[m.JOINT]=metric(exact=6)
    assert m._choose(states)==m.JOINT


def test_no_mutation_or_production_unsafe_methods():
    source=inspect.getsource(m.run)
    assert "'g1_head_weights_modified':False" in source
    assert "'production_promotion':False" in source
    assert "'g2_unlocked':False" in source
    assert "'certification_authorized':False" in source
    assert 'head.optimizer' not in source
    assert "_checked_preflight" in source
    assert m.ARMS[0]=='structured_set_maxmargin'
    assert len(set(m.ARMS))==4
