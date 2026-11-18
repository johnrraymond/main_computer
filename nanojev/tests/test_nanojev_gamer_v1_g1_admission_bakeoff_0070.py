"""Frozen G1 admission research runner; no external model required."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g1_admission_bakeoff_0070 as m
from tools import nanojev_gamer_v1_g1_oracle_decoder_cutover_frob as prev
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import decode_neural_spans


def spans(*items):
    values=torch.full((6,6,len(SPAN_KINDS)+1),-12.,dtype=torch.float32)
    values[...,0]=0
    valid=torch.zeros((6,6),dtype=torch.bool)
    for first,last,kind,score in items:
        valid[first,last-first]=True
        values[first,last-first,SPAN_KINDS.index(kind)+1]=score
    return values,valid


def test_catalogue_and_source_decoder_are_immutable():
    m._check_specs()
    assert m.ARM_NAMES[0]=='whole_linear'
    assert len(m.ARM_NAMES)==len(set(m.ARM_NAMES))>=9
    assert all('oracle' not in v for v in m.ARM_NAMES)


def test_whole_linear_exactly_matches_previous_frozen_decoder():
    from tools.nanojev_gamer_v1_g1_decoder_frob import _candidate_scores,decode_whole_span_scores
    logits,valid=spans((0,2,'actor',3),(0,0,'actor',2),(1,2,'actor',2),
                       (3,5,'action',4))
    before=logits.clone()
    pred,_=m._decode_all(logits,valid)
    old=decode_whole_span_scores(_candidate_scores(logits,valid),
                                 length_power=1,per_object_penalty=0)
    assert pred['whole_linear']==old
    assert pred['whole_linear'][0]['token_last']==2
    assert decode_neural_spans(logits,valid)[0]['token_last']==0
    assert torch.equal(before,logits)
    assert set(pred)==set(m.ARM_NAMES)


def test_null_rescue_is_actor_specific():
    logits,valid=spans((0,1,'actor',-.25),(3,4,'action',-.25))
    baseline,_=m._decode_all(logits,valid)
    assert baseline['whole_linear']==[]
    actor=baseline['rescue_actor_0p5']
    action=baseline['rescue_action_0p5']
    assert [p['kind'] for p in actor]==['actor']
    assert [p['kind'] for p in action]==['action']
    assert [p['kind'] for p in baseline['rescue_actor_action_0p5']]==['actor','action']
    assert [p['kind'] for p in baseline['rank_actor_action_0p5']]==[]


def test_null_deficit_half_does_not_rescue_large_deficit():
    logits,valid=spans((0,1,'actor',-.75))
    pred,_=m._decode_all(logits,valid)
    assert pred['rescue_actor_0p5']==[]
    assert len(pred['rescue_actor_action_1p0'])==1


def test_ambiguous_nonnull_types_do_not_admit_without_null_support():
    logits,valid=spans((0,0,'actor',-3))
    pred,_=m._decode_all(logits,valid)
    assert all(not p for p in pred.values())


def test_runnerup_actor_prior_can_retype_near_tie():
    logits,valid=spans((0,1,'action',4))
    actor=SPAN_KINDS.index('actor')+1
    logits[0,1,actor]=3.60
    options,_=m._decode_all(logits,valid)
    assert options['whole_linear'][0]['kind']=='action'
    assert options['runnerup_actor_prior_0p75'][0]['kind']=='actor'
    assert options['rescue_actor_action_0p5'][0]['kind']=='action'


def test_runnerup_actor_prior_rejects_large_type_gap():
    logits,valid=spans((0,1,'action',6))
    logits[0,1,SPAN_KINDS.index('actor')+1]=4
    options,_=m._decode_all(logits,valid)
    assert options['runnerup_actor_prior_0p75'][0]['kind']=='action'


def test_overlap_remains_disallowed_and_typed_scores_finite():
    logits,valid=spans((0,2,'actor',1),(1,4,'action',2),(4,5,'actor',1))
    outputs,_=m._decode_all(logits,valid)
    for options in outputs.values():
        assert options is not None
        for left,right in zip(options,options[1:]):
            assert left['token_last']<right['token_first']


def test_nonfinite_valid_scores_are_rejected():
    logits,valid=spans((0,0,'actor',2))
    logits[0,0,0]=float('nan')
    with pytest.raises(ValueError,match='NONFINITE'):
        m._decode_all(logits,valid)


def test_invalid_shapes_are_rejected():
    logits,valid=spans((0,0,'actor',2))
    with pytest.raises(ValueError,match='SHAPE'):
        m._decode_all(logits[:,:2],valid)


def test_paired_exact_mcnemar_and_mismatch_rejection():
    truth={(0,1,'actor')}
    a=[(set(),truth),(truth,truth),(set(),truth)]
    b=[(truth,truth),(set(),truth),(truth,truth)]
    result=m._paired_exact_test(a,b)
    assert result['gained_exact_cases']==2
    assert result['lost_exact_cases']==1
    assert result['two_sided_exact_p']==1
    with pytest.raises(ValueError,match='UNPAIRED'):
        m._paired_exact_test(a,b[:-1])
    with pytest.raises(ValueError,match='UNPAIRED'):
        m._paired_exact_test(a,[(set(),set()),*b[1:]])


def test_change_accounting_penalizes_false_admissions():
    gold={(0,2,'actor')}
    before=[(set(),gold)]
    after=[({(0,2,'actor'),(4,5,'action')},gold)]
    change=m._changes(before,after)
    assert change['recovered_correct']==1
    assert change['introduced_false_objects']==1
    assert change['net_false']==1
    assert change['exact_pairing']['gained_exact_cases']==0


def _fake_metrics(base,modified):
    samples=[({(0,1,'actor')}, {(0,1,'actor'),(2,3,'action')})]
    old=m._summarize(samples)
    new=m._summarize([({(0,1,'actor'),(2,3,'action')}, samples[0][1])])
    metrics={name:copy.deepcopy(old) for name in m.ARM_NAMES}
    metrics[modified]=copy.deepcopy(new)
    return metrics


def test_dev_selection_only_with_nonregression():
    data=_fake_metrics(None,'rescue_actor_action_0p5')
    assert m._dev_select(data)=='rescue_actor_action_0p5'
    assert m._dev_select(data,{'rescue_actor_action_0p5':1})=='whole_linear'
    changed=copy.deepcopy(data)
    changed['rescue_actor_action_0p5']['by_kind']['actor']['recall']=0
    assert m._dev_select(changed)=='whole_linear'
    changed=copy.deepcopy(data)
    changed['rescue_actor_action_0p5']['precision']=0
    assert m._dev_select(changed)=='whole_linear'


def _reference(tmp_path,entries,dataset_sha):
    prior={
        'schema':prev.SCHEMA,
        'lineage':{'dataset_sha256':dataset_sha,
                    'checkpoints':{n:e['manifest']['head_sha256'] for n,e in entries.items()}},
        'design':{'dev_cases':1,'historical_challenge_cases':1},
        'arms':{name:{'selected_decoder':'whole_linear'} for name in entries},
        'training_performed':False,'production_promotion':False,
        'g2_unlocked':False,'sealed_test_used':False}
    folder=tmp_path/'previous';folder.mkdir()
    (folder/'report.json').write_text(json.dumps(prior),encoding='utf8')
    return folder


def test_prior_reference_requires_matching_lineage(tmp_path):
    entries={x:{'manifest':{'head_sha256':x+'sha'}} for x in ('legacy','cutover')}
    reference=_reference(tmp_path,entries,'ds_hash')
    doc,digest=m._verify_decoder_reference(reference,entries,'ds_hash',{'dev':1,'challenge':1})
    assert doc['arms']['cutover']['selected_decoder']=='whole_linear'
    assert digest==m.sha256(reference/'report.json')
    doc['arms']['legacy']['selected_decoder']='global'
    (reference/'report.json').write_text(json.dumps(doc),encoding='utf8')
    with pytest.raises(ValueError,match='REFERENCE_MISMATCH'):
        m._verify_decoder_reference(reference,entries,'ds_hash',{'dev':1,'challenge':1})


def test_previous_metrics_are_a_required_exact_control():
    metrics={name:{'whole_linear':{'correct':3,'predicted':4,'expected':5,
                                   'exact_prompts':1}} for name in ('legacy','cutover')}
    report={'arms':{name:{'dev':{'models':{'whole_linear':copy.deepcopy(metrics[name]['whole_linear'])}}}
                    for name in metrics}}
    m._prior_parity(report,metrics,'dev')
    metrics['legacy']['whole_linear']['correct']=2
    with pytest.raises(ValueError,match='REFERENCE_DRIFT'):
        m._prior_parity(report,metrics,'dev')


def test_runner_preselects_before_challenge_and_preserves_weights(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    dataset=tmp_path/'dataset.jsonl';dataset.write_bytes(b'locked')
    entries={}
    for name in ('legacy','cutover'):
        folder=tmp_path/name;folder.mkdir()
        (folder/cur.HEAD_FILE).write_bytes(name.encode('ascii'))
        entries[name]={'path':str(folder),'manifest':{
            'head_sha256':m.sha256(folder/cur.HEAD_FILE),
            'model_id':'source','source_adapter':'adapter',
            'source_adapter_sha256':'adapter_hash'}}
    reference=_reference(tmp_path,entries,m.sha256(dataset))
    cohorts={'dev':[{'split':'dev'}],'challenge':[{'split':'challenge'}]}
    monkeypatch.setattr(m,'_checked_preflight',lambda *_:(entries,m.sha256(dataset),cohorts))
    class Head:
        def eval(self):return self
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *_,**__: (Head(),{}))
    monkeypatch.setattr(backbone,'model_components',lambda *_,**__:('model','tokenizer'))
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *_:'adapter_hash')
    paired=[({(0,1,'actor')},{(0,1,'actor')})]
    stats=m._summarize(paired)
    ref=json.loads((reference/'report.json').read_text())
    for checkpoint in ref['arms']:
        ref['arms'][checkpoint]['dev']={'models':{m.CONTROL:stats}}
        ref['arms'][checkpoint]['historical_challenge']={'models':{m.CONTROL:stats}}
    (reference/'report.json').write_text(json.dumps(ref),encoding='utf8')
    order=[];selection=[]
    def fake_eval(*,split,**kwargs):
        order.append(split)
        if split=='challenge':assert len(selection)==2
        records={name:{arm:list(paired) for arm in m.ARM_NAMES} for name in entries}
        summary={name:{arm:copy.deepcopy(stats) for arm in m.ARM_NAMES} for name in entries}
        oracle={name:{k:copy.deepcopy(stats) for k in ('oracle_eligible','oracle_nonnull_type','oracle_forced_type')}
                for name in entries}
        return records,summary,oracle,[],{name:{} for name in entries}
    monkeypatch.setattr(m,'_evaluate',fake_eval)
    original=m._dev_select
    def tracking_select(*args):
        v=original(*args);selection.append(v);return v
    monkeypatch.setattr(m,'_dev_select',tracking_select)
    report=m.run(dataset=dataset,legacy='a',cutover='b',decoder_reference=reference,
                 output=tmp_path/'new_report',device='cpu')
    assert order==['dev','challenge']
    assert selection==['whole_linear','whole_linear']
    assert not report['certification_authorized']
    assert not report['g2_unlocked']
    assert not report['weights_modified']
    assert not report['training_performed']
    assert (tmp_path/'new_report'/'report.json').exists()
    assert (tmp_path/'new_report'/'cases.jsonl').read_text()==''
    for name in ('legacy','cutover'):
        assert (tmp_path/name/cur.HEAD_FILE).read_bytes()==name.encode('ascii')


def test_real_decoder_accepts_no_gold_or_oracle_parameter():
    import inspect
    assert tuple(inspect.signature(m._decode_all).parameters)==('logits','valid')
    assert tuple(inspect.signature(m._score_candidates).parameters)==('size','pool','spec')
    assert all('gold' not in s for s in (str(inspect.signature(m._decode_all)),
                                        str(inspect.signature(m._score_candidates))))
