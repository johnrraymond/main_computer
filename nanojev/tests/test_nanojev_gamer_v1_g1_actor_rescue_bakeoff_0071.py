"""Focused, no-Qwen/no-CUDA tests for frozen G1 actor rescue."""
import copy
import inspect
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g1_actor_rescue_bakeoff_0071 as m
from tools import nanojev_gamer_v1_g1_admission_bakeoff_0070 as prior
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


def scenario(*items):
    logits=torch.full((7,7,len(SPAN_KINDS)+1),-12.,dtype=torch.float32)
    logits[...,0]=0
    valid=torch.zeros((7,7),dtype=torch.bool)
    for begin,end,values in items:
        valid[begin,end-begin]=True
        for kind,score in values.items():
            logits[begin,end-begin,SPAN_KINDS.index(kind)+1]=score
    return logits,valid


def kind(result):
    return [s['kind'] for s in result]


def test_arm_catalogue_is_unique_and_has_frozen_selected_control():
    m._check_specs()
    assert m.ARM_NAMES[:2]==(m.CONTROL,m.PRIOR_ABLATION)
    assert len(m.ARMS)>=10
    assert all('oracle' not in a.name for a in m.ARMS)


def test_decoder_exactly_matches_both_previous_admission_arms_and_never_mutates():
    logits,mask=scenario((0,2,{'actor':4}),(3,5,{'action':4}))
    before=logits.clone()
    earlier,_=prior._decode_all(logits,mask)
    actual,_=m._decode(logits,mask)
    assert actual[m.CONTROL]==earlier[m.CONTROL]
    assert actual[m.PRIOR_ABLATION]==earlier[m.PRIOR_ABLATION]
    assert tuple(actual)==m.ARM_NAMES
    assert torch.equal(before,logits)
    assert torch.equal(mask,mask.clone())


def test_local_actor_prior_preserves_original_non_actor_score_and_nonoverlap():
    logits,mask=scenario((0,2,{'actor':3}),(1,4,{'action':2}),
                         (5,5,{'quantity':2}))
    size,pool=prior._pool(logits,mask)
    baseline=prior._score_candidates(size,pool,m.JOINT)
    adjusted=m._with_actor_bonus(baseline,.25)
    for row,changed in zip(baseline,adjusted):
        for old,new in zip(row,changed):
            assert old[:3]==new[:3]
            assert new[3]==pytest.approx(old[3]+(.25 if old[2]==m.ACTOR else 0))
    results,_=m._decode(logits,mask)
    for spans in results.values():
        assert spans is not None
        assert all(a['token_last']<b['token_first'] for a,b in zip(spans,spans[1:]))


def test_joint_runnerup_recovers_near_tied_actor_without_gold():
    logits,mask=scenario((0,1,{'action':5.,'actor':4.8}))
    output,_=m._decode(logits,mask)
    assert kind(output[m.CONTROL])==['action']
    assert kind(output['runnerup_joint_gap0p25_bonus0p25'])==['actor']
    assert kind(output['runnerup_joint_gap0p50_bonus0p50'])==['actor']


def test_large_type_gap_is_not_reclassified():
    logits,mask=scenario((0,1,{'action':6,'actor':3}))
    outputs,_=m._decode(logits,mask)
    assert kind(outputs['runnerup_joint_gap0p75_bonus0p75'])==['action']
    assert kind(outputs['guarded_swap_gap0p75'])==['action']


def test_guarded_swap_retypes_one_action_and_preserves_boundaries():
    logits,mask=scenario((0,1,{'action':5.,'actor':4.82}),
                         (3,4,{'action':5.,'actor':4.85}))
    outputs,_=m._decode(logits,mask)
    assert kind(outputs[m.CONTROL])==['action','action']
    changed=outputs['guarded_swap_gap0p25']
    assert kind(changed).count('actor')==1
    assert [(x['token_first'],x['token_last']) for x in changed]==[
        (x['token_first'],x['token_last']) for x in outputs[m.CONTROL]]


def test_gap_insert_only_adds_actors_without_displacing_existing_spans():
    logits,mask=scenario((0,1,{'action':4.0}),
                         (3,4,{'action':-.10,'actor':-.20}))
    outputs,_=m._decode(logits,mask)
    assert kind(outputs[m.CONTROL])==['action']
    changed=outputs['gap_insert_actor_1p00']
    assert kind(changed)==['action','actor']
    assert outputs[m.CONTROL][0]==changed[0]


def test_actor_conditional_relaxation_is_targeted():
    logits,mask=scenario((0,1,{'actor':-.5,'action':-.8}),
                         (3,4,{'action':-.5,'actor':-.8}))
    outputs,_=m._decode(logits,mask)
    assert outputs[m.CONTROL]==[]
    assert kind(outputs['actor_admit_cond0p50'])==['actor']
    assert kind(outputs['actor_admit_cond0p40'])==['actor']


def _metric_pairs():
    gold={(0,1,'actor'),(2,3,'action')}
    base=[({(2,3,'action')},gold),(gold,gold)]
    new=[(gold,gold),(gold,gold)]
    return base,new


def test_selection_requires_actor_gate_and_retention_of_exact_sets():
    base,new=_metric_pairs()
    existing=m._summarize(base)
    better=m._summarize(new)
    # Generic synthetic cohort has actor recall 0.0 -> 1.0.
    metrics={name:copy.deepcopy(existing) for name in m.ARM_NAMES}
    metrics['actor_prior_0p25']=copy.deepcopy(better)
    assert m._select_dev(metrics,{})=='actor_prior_0p25'
    assert m._select_dev(metrics,{'actor_prior_0p25':1})==m.CONTROL
    broken=copy.deepcopy(metrics)
    broken['actor_prior_0p25']['exact_prompts']=0
    assert m._select_dev(broken,{})==m.CONTROL
    broken=copy.deepcopy(metrics)
    broken['actor_prior_0p25']['by_kind']['action']['recall']=0
    assert m._select_dev(broken,{})==m.CONTROL


def _fake_reference(tmp_path,entries,hash_value):
    prior_data={
        'schema':prior.SCHEMA,
        'status':'FROZEN_ADMISSION_EXPERIMENT_NOT_CERTIFICATION',
        'lineage':{'dataset_sha256':hash_value,
          'checkpoint_sha256':{name:e['manifest']['head_sha256'] for name,e in entries.items()}},
        'design':{'dev_cases':1,'historical_challenge_cases':1,'control':prior.CONTROL},
        'models':{model:{'dev':{'selected':m.CONTROL,'arms':{m.CONTROL:{},m.PRIOR_ABLATION:{}}},
                         'historical_challenge':{'arms':{m.CONTROL:{},m.PRIOR_ABLATION:{}}}}
                  for model in entries},
        'weights_modified':False,'training_performed':False,'production_promotion':False,
        'g2_unlocked':False,'sealed_test_used':False,'certification_authorized':False}
    folder=tmp_path/'previous';folder.mkdir()
    (folder/'report.json').write_text(json.dumps(prior_data),encoding='utf8')
    return folder


def test_rejects_admission_reference_with_changed_lineage_or_reselected_arm(tmp_path):
    entries={name:{'manifest':{'head_sha256':name+'hash'}} for name in ('legacy','cutover')}
    folder=_fake_reference(tmp_path,entries,'dataset_hash')
    previous,digest=m._require_admission_reference(folder,entries,'dataset_hash',{'dev':1,'challenge':1})
    assert digest==m.sha256(folder/'report.json')
    previous['models']['cutover']['dev']['selected']='whole_linear'
    (folder/'report.json').write_text(json.dumps(previous),encoding='utf8')
    with pytest.raises(ValueError,match='ADMISSION_REFERENCE_MISMATCH'):
        m._require_admission_reference(folder,entries,'dataset_hash',{'dev':1,'challenge':1})


def test_report_parity_catches_previously_recorded_metric_drift():
    stats=m._summarize(_metric_pairs()[0])
    actual={name:{arm:copy.deepcopy(stats) for arm in (m.CONTROL,m.PRIOR_ABLATION)}
            for name in ('legacy','cutover')}
    expected={'models':{name:{'dev':{'arms':copy.deepcopy(arms)}}
                         for name,arms in actual.items()}}
    m._reference_parity(expected,'dev',actual)
    actual['cutover'][m.CONTROL]['by_kind']['actor']['correct']=123
    with pytest.raises(ValueError,match='PRIOR_KIND_DRIFT'):
        m._reference_parity(expected,'dev',actual)


def test_run_is_dev_selected_before_challenge_and_never_promotes(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    dataset=tmp_path/'dataset';dataset.write_bytes(b'frozen')
    entries={}
    for name in ('legacy','cutover'):
        folder=tmp_path/name;folder.mkdir()
        (folder/cur.HEAD_FILE).write_bytes(name.encode())
        entries[name]={'path':str(folder),'manifest':{
            'head_sha256':m.sha256(folder/cur.HEAD_FILE),
            'model_id':'Q','source_adapter':'adapter',
            'source_adapter_sha256':'fingerprint'}}
    reference=_fake_reference(tmp_path,entries,m.sha256(dataset))
    baseline=m._summarize(_metric_pairs()[0])
    doc=json.loads((reference/'report.json').read_text())
    for name in entries:
        for split in ('dev','historical_challenge'):
            for arm in (m.CONTROL,m.PRIOR_ABLATION):
                doc['models'][name][split]['arms'][arm]=copy.deepcopy(baseline)
    (reference/'report.json').write_text(json.dumps(doc),encoding='utf8')
    monkeypatch.setattr(m,'_checked_preflight',lambda *_:(entries,m.sha256(dataset),
        {'dev':[{'split':'dev'}],'challenge':[{'split':'challenge'}]}))
    class Head:
        def eval(self):return self
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *_args,**_kwargs:(Head(),{}))
    monkeypatch.setattr(backbone,'model_components',lambda *_args,**_kwargs:('model','tokenizer'))
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *_args:'fingerprint')
    order=[];choices=[]
    def fake_eval(*,split,**kwargs):
        order.append(split)
        if split=='challenge':assert len(choices)==2
        records={name:{arm:list(_metric_pairs()[0]) for arm in m.ARM_NAMES} for name in entries}
        stats={name:{arm:copy.deepcopy(baseline) for arm in m.ARM_NAMES} for name in entries}
        oracle={name:{key:copy.deepcopy(baseline) for key in
                ('oracle_eligible','oracle_nonnull_type','oracle_forced_type')} for name in entries}
        return records,stats,oracle,[],{name:{} for name in entries},{name:{} for name in entries}
    monkeypatch.setattr(m,'_evaluate',fake_eval)
    selector=m._select_dev
    def select(*args):
        result=selector(*args);choices.append(result);return result
    monkeypatch.setattr(m,'_select_dev',select)
    output=tmp_path/'report'
    report=m.run(dataset=dataset,legacy='unused',cutover='unused',
                 admission_reference=reference,output=output,device='cpu')
    assert order==['dev','challenge']
    assert choices==[m.CONTROL,m.CONTROL]
    assert report['weights_modified'] is False
    assert report['training_performed'] is False
    assert report['g2_unlocked'] is False
    assert report['certification_authorized'] is False
    assert (output/'report.json').exists()
    for name in entries:
        assert (tmp_path/name/cur.HEAD_FILE).read_bytes()==name.encode()


def test_gold_cannot_enter_any_decoder():
    sig=inspect.signature(m._decode)
    assert tuple(sig.parameters)==('logits','valid')
    for name in ('_actor_options','_with_actor_bonus','_with_actor_nonnull_relaxation',
                 '_runnerup_joint','_guarded_swap','_gap_insert'):
        signature=str(inspect.signature(getattr(m,name)))
        assert all(term not in signature for term in ('gold','oracle','expected'))
