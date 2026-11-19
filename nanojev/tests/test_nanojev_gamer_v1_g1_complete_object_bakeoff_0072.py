"""No-Qwen CPU tests of the read-only G1-0072 complete-object bake-off."""
import copy
import inspect
import json
from pathlib import Path
import zipfile

import pytest
import torch

from tools import nanojev_gamer_v1_g1_complete_object_bakeoff_0072 as m
from tools import nanojev_gamer_v1_g1_actor_rescue_bakeoff_0071 as actor
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


def scenario(*parts):
    logits=torch.full((9,9,len(SPAN_KINDS)+1),-12.,dtype=torch.float32)
    logits[...,0]=0
    mask=torch.zeros((9,9),dtype=torch.bool)
    for start,last,kinds in parts:
        mask[start,last-start]=True
        for kind,score in kinds.items():
            logits[start,last-start,SPAN_KINDS.index(kind)+1]=score
    return logits,mask


def kinds(spans):return [s['kind'] for s in spans]


def test_canonical_arms_are_fixed_and_not_gold_aware():
    m._check_specs()
    assert m.ARM_NAMES[0]==m.CONTROL
    assert len(m.ARM_NAMES)>=12
    assert 'gold' not in inspect.signature(m._decode).parameters
    assert 'row' not in inspect.signature(m._decode).parameters
    assert all('oracle' not in name for name in m.ARM_NAMES)


@pytest.mark.parametrize('control',(
    'runnerup_joint_gap0p50_bonus0p50','actor_prior_0p25'))
def test_prior_selected_decoder_is_bitwise_identical_and_inputs_not_mutated(control):
    logits,valid=scenario((0,1,{'actor':5.}),
                         (3,3,{'action':4.}),(4,5,{'quantity':3.}))
    old_logits=logits.clone();old_mask=valid.clone()
    previous,_=actor._decode(logits,valid)
    results,options=m._decode(logits,valid,control)
    assert results[m.CONTROL]==previous[control]
    assert torch.equal(old_logits,logits) and torch.equal(old_mask,valid)
    assert tuple(results)==m.ARM_NAMES
    for spans in results.values():
        assert spans is not None
        assert all(a['token_last']<b['token_first'] for a,b in zip(spans,spans[1:]))


def test_whole_action_exponent_replaces_two_shorter_fragments():
    logits,valid=scenario((0,0,{'action':3.}),
                          (1,1,{'action':3.}),
                          (0,1,{'action':2.5}))
    pred,_=m._decode(logits,valid,'actor_prior_0p25')
    assert [(x['token_first'],x['token_last']) for x in pred[m.CONTROL]]==[(0,0),(1,1)]
    assert [(x['token_first'],x['token_last']) for x in pred['action_width_1p40']]==[(0,1)]


def test_action_count_penalty_can_favor_complete_span_over_two_short_spans():
    logits,valid=scenario((0,0,{'action':3.}),
                          (1,1,{'action':3.}),
                          (0,1,{'action':2.8}))
    pred,_=m._decode(logits,valid,'actor_prior_0p25')
    assert len(pred[m.CONTROL])==2
    assert len(pred['action_count_penalty_1p00'])==1
    assert pred['action_count_penalty_1p00'][0]['token_last']==1


def test_action_rescue_admits_null_rejected_candidate_without_oracle():
    logits,valid=scenario((0,1,{'action':-1.20}))
    pred,_=m._decode(logits,valid,'actor_prior_0p25')
    assert pred[m.CONTROL]==[]
    assert kinds(pred['action_rescue_1p50_cond0p55'])==['action']


def test_runnerup_action_uses_near_tie_but_rejects_wide_gap():
    a,valid=scenario((0,1,{'actor':4.,'action':3.9}))
    pred,_=m._decode(a,valid,'actor_prior_0p25')
    assert kinds(pred[m.CONTROL])==['actor']
    assert kinds(pred['action_runnerup_gap0p25_bonus0p25'])==['action']
    b,valid=scenario((0,1,{'actor':6.,'action':3.}))
    pred,_=m._decode(b,valid,'actor_prior_0p25')
    assert kinds(pred['action_runnerup_gap0p50_bonus0p50'])==['actor']


def test_merge_only_combines_neighboring_actions_with_full_neural_option():
    a,valid=scenario((0,0,{'action':3.}),
                     (1,1,{'action':3.}),
                     (0,1,{'action':2.5}))
    pred,_=m._decode(a,valid,'actor_prior_0p25')
    assert len(pred[m.CONTROL])==2
    assert len(pred['merge_adjacent_actions_0p65'])==1
    b,valid=scenario((0,0,{'action':3.}),
                     (2,2,{'action':3.}),
                     (0,2,{'action':1.5}))
    pred,_=m._decode(b,valid,'actor_prior_0p25')
    assert len(pred['merge_adjacent_actions_0p65'])==2


def test_invalid_candidates_fail_closed():
    with pytest.raises(ValueError,match='INVALID_CANDIDATE'):
        m._decode_typed([[],[(1,0,m.ACTION,1.)]],m.ARMS[1])
    with pytest.raises(ValueError,match='UNSUPPORTED_PREVIOUS_CONTROL'):
        m._prior_scored_candidates(2,[], 'gap_insert_actor_0p50')


def test_oracle_attribution_is_hierarchical_and_only_post_prediction():
    gold={(0,5,'action'),(10,12,'actor'),(20,24,'action')}
    pred={(0,2,'action'),(2,5,'action'),(10,12,'quantity')}
    oracle={'oracle_eligible':{(0,5,'action')},
            'oracle_nonnull_type':{(0,5,'action'),(10,12,'actor')},
            'oracle_forced_type':set(gold)}
    out=m._failure_map(gold,pred,oracle)
    assert out['missing_by_kind_reason']['action']['eligible_but_not_selected']==1
    assert out['missing_by_kind_reason']['actor']['correct_nonnull_type_not_eligible']==1
    assert out['missing_by_kind_reason']['action']['wrong_top_nonnull_type']==1
    assert out['action_span_shape']['split_multiple_actions']==1
    assert out['minimum_object_edits']==len(gold-pred)+len(pred-gold)


def _metric_pair():
    gold={(0,1,'actor'),(2,3,'action')}
    base=[({(0,1,'actor')},gold),(gold,gold)]
    improved=[(gold,gold),(gold,gold)]
    return base,improved


def test_selection_requires_exact_gain_and_all_recall_retention():
    a,b=_metric_pair()
    baseline=m._summarize(a)
    better=m._summarize(b)
    metrics={name:copy.deepcopy(baseline) for name in m.ARM_NAMES}
    metrics['action_width_1p10']=better
    assert m._select_dev(metrics,{})=='action_width_1p10'
    assert m._select_dev(metrics,{'action_width_1p10':1})==m.CONTROL
    bad=copy.deepcopy(metrics)
    bad['action_width_1p10']['by_kind']['actor']['recall']=0.0
    assert m._select_dev(bad,{})==m.CONTROL
    bad=copy.deepcopy(metrics)
    bad['action_width_1p10']['exact_prompts']=baseline['exact_prompts']
    assert m._select_dev(bad,{})==m.CONTROL


def test_actual_uploaded_0071_report_shape_and_selected_controls():
    path=Path('/mnt/data/oracle-fabric-g1-actor-rescue-bakeoff-0001.zip')
    if not path.is_file():pytest.skip('G1-0071 upload not mounted')
    with zipfile.ZipFile(path) as z:
        name=next(x for x in z.namelist() if x.endswith('report.json'))
        report=json.loads(z.read(name))
    assert report['schema']==actor.SCHEMA
    assert report['models']['cutover']['dev']['selected']=='runnerup_joint_gap0p50_bonus0p50'
    assert report['models']['legacy']['dev']['selected']=='actor_prior_0p25'
    m._reference_parity(report,'dev',{
        k:{m.CONTROL:v['dev']['selected_metrics']}
        for k,v in report['models'].items()})
    m._reference_parity(report,'challenge',{
        k:{m.CONTROL:v['historical_challenge']['selected_metrics']}
        for k,v in report['models'].items()})


def test_actor_reference_lineage_and_selection_rejection(tmp_path):
    dataset='dataset_hash'
    entries={name:{'manifest':{'head_sha256':name+'hash'}}
             for name in ('cutover','legacy')}
    report={'schema':actor.SCHEMA,
       'status':'EXPLORATORY_FROZEN_ACTOR_RESCUE_NOT_CERTIFICATION',
       'lineage':{'dataset_sha256':dataset,
           'checkpoint_sha256':{k:v['manifest']['head_sha256'] for k,v in entries.items()}},
       'design':{'dev_cases':80,'historical_challenge_cases':32},
       'models':{},'weights_modified':False,'training_performed':False,
       'production_promotion':False,'g2_unlocked':False,'sealed_test_used':False,
       'certification_authorized':False}
    for name,choice in (('cutover','runnerup_joint_gap0p50_bonus0p50'),
                        ('legacy','actor_prior_0p25')):
        report['models'][name]={'dev':{'selected':choice,'arms':{choice:{}}},
          'historical_challenge':{'selected':choice,'arms':{choice:{}}}}
    folder=tmp_path/'report';folder.mkdir()
    (folder/'report.json').write_text(json.dumps(report),encoding='utf8')
    loaded,hash_,chosen=m._require_actor_reference(folder,entries,dataset,
        {'dev':80,'challenge':32})
    assert chosen['cutover']=='runnerup_joint_gap0p50_bonus0p50'
    assert hash_==m.sha256(folder/'report.json')
    loaded['models']['cutover']['dev']['selected']='action_width_1p25'
    (folder/'report.json').write_text(json.dumps(loaded),encoding='utf8')
    with pytest.raises(ValueError,match='PREVIOUS_SELECTION_MISMATCH'):
        m._require_actor_reference(folder,entries,dataset,{'dev':80,'challenge':32})


def test_simulated_run_selects_before_challenge_never_promotes(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    dataset=tmp_path/'dataset';dataset.write_bytes(b'frozen data')
    entries={}
    for name in ('cutover','legacy'):
        path=tmp_path/name;path.mkdir()
        (path/cur.HEAD_FILE).write_text(name,encoding='utf8')
        entries[name]={'path':str(path),'manifest':{
            'head_sha256':m.sha256(path/cur.HEAD_FILE),
            'model_id':'Q','source_adapter':'adapter',
            'source_adapter_sha256':'adapterhash'}}
    prior={'schema':actor.SCHEMA,'status':'EXPLORATORY_FROZEN_ACTOR_RESCUE_NOT_CERTIFICATION',
        'lineage':{'dataset_sha256':m.sha256(dataset),
           'checkpoint_sha256':{k:v['manifest']['head_sha256'] for k,v in entries.items()}},
        'design':{'dev_cases':1,'historical_challenge_cases':1},
        'models':{},'weights_modified':False,'training_performed':False,
        'production_promotion':False,'g2_unlocked':False,'sealed_test_used':False,
        'certification_authorized':False}
    a,b=_metric_pair();baseline=m._summarize(a)
    for name,selected in (('cutover','runnerup_joint_gap0p50_bonus0p50'),
                          ('legacy','actor_prior_0p25')):
        prior['models'][name]={'dev':{'selected':selected,'selected_metrics':baseline,
            'arms':{selected:baseline}},
          'historical_challenge':{'selected':selected,'selected_metrics':baseline,
            'arms':{selected:baseline}}}
    ref=tmp_path/'prior';ref.mkdir()
    (ref/'report.json').write_text(json.dumps(prior),encoding='utf8')
    monkeypatch.setattr(m,'_checked_preflight',lambda *_:(entries,m.sha256(dataset),
        {'dev':[{}],'challenge':[{}]}))
    class Head:
        def eval(self):return self
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *_args,**_kwargs:(Head(),{}))
    monkeypatch.setattr(backbone,'model_components',lambda *_args,**_kwargs:('m','t'))
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *_args:'adapterhash')
    order=[];selected_calls=[]
    def fake_eval(*,split,**kwargs):
        order.append(split)
        if split=='challenge':assert len(selected_calls)==2
        records={k:{arm_name:list(a) for arm_name in m.ARM_NAMES} for k in entries}
        metrics={k:{arm_name:copy.deepcopy(baseline) for arm_name in m.ARM_NAMES} for k in entries}
        ceilings={k:{level:baseline for level in
           ('oracle_eligible','oracle_nonnull_type','oracle_forced_type')} for k in entries}
        return records,metrics,ceilings,[],{k:{} for k in entries},{k:{} for k in entries},{k:{} for k in entries}
    monkeypatch.setattr(m,'_evaluate',fake_eval)
    original=m._select_dev
    def select(*args):
        choice=original(*args);selected_calls.append(choice);return choice
    monkeypatch.setattr(m,'_select_dev',select)
    output=tmp_path/'output'
    result=m.run(dataset=dataset,legacy='unused',cutover='unused',
        actor_reference=ref,output=output,device='cpu')
    assert order==['dev','challenge']
    assert all(x==m.CONTROL for x in selected_calls)
    assert result['training_performed'] is False
    assert result['g2_unlocked'] is False
    assert result['production_promotion'] is False
    assert result['sealed_test_used'] is False
    assert (output/'report.json').exists()
    assert (output/'cases.jsonl').exists()
    with pytest.raises(FileExistsError):
        m.run(dataset=dataset,legacy='unused',cutover='unused',
            actor_reference=ref,output=output,device='cpu')
