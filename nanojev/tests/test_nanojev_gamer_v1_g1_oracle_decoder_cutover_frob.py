"""No trained model is needed for the oracle-decoder research acceptance tests."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from tools import nanojev_gamer_v1_g1_oracle_decoder_cutover_frob as m
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import decode_neural_spans


def synthetic_scores():
    n=5; w=5
    logits=torch.full((n,w,len(SPAN_KINDS)+1),-10.,dtype=torch.float32)
    logits[...,0]=0.
    valid=torch.zeros((n,w),dtype=torch.bool)
    actor=SPAN_KINDS.index('actor')+1
    action=SPAN_KINDS.index('action')+1
    def put(a,b,k,margin):
        valid[a,b-a]=True
        logits[a,b-a,k]=margin
    # Greedy/global picks the two short actor fragments; length-aware prefers whole.
    put(0,2,actor,7.)
    put(0,0,actor,6.)
    put(1,2,actor,5.)
    put(3,4,action,7.)
    return logits,valid


def mock_prepared():
    return {'prompt':'abcde','offsets':[(i,i+1) for i in range(5)],'prompt_start':0}


def aligned_gold():
    return {'spans':[{'token_first':0,'token_last':2,'kind':'actor'},
                     {'token_first':3,'token_last':4,'kind':'action'}]}


def test_baseline_is_byte_equivalent_to_production_decoder():
    logits,valid=synthetic_scores()
    got=m._all_decoders(logits,valid)
    assert got['global']==decode_neural_spans(logits,valid)
    assert [x['token_last'] for x in got['global']]==[0,2,4]
    assert got['whole_linear'][0]['token_last']==2
    assert list(got)==list(m.ARMS)


def test_gold_is_not_an_argument_to_real_decoder():
    logits,valid=synthetic_scores()
    before=logits.clone()
    first=m._all_decoders(logits,valid)
    second=m._all_decoders(logits,valid)
    assert first==second
    assert torch.equal(logits,before)
    for arm,spans in first.items():
        assert len(spans)<=128
        for a,b in zip(spans,spans[1:]):
            assert a['token_last']<b['token_first']


def test_oracle_ceilings_are_gold_assisted_and_nested():
    logits,valid=synthetic_scores()
    prepared=mock_prepared()
    eligible=m._candidate_scores(logits,valid)
    ceilings=m._oracle_ceilings(logits,valid,prepared,aligned_gold(),eligible)
    expected={(0,3,'actor'),(3,5,'action')}
    assert ceilings['oracle_eligible']==expected
    assert ceilings['oracle_nonnull_type']==expected
    assert ceilings['oracle_forced_type']==expected
    # Null wins for the actor: only the oracle-after-inference ceiling can
    # recover it; the ordinary decoder may not silently do so.
    logits[0,2,0]=9.0
    reduced=m._oracle_ceilings(logits,valid,prepared,aligned_gold(),
                               m._candidate_scores(logits,valid))
    assert (0,3,'actor') not in reduced['oracle_eligible']
    assert (0,3,'actor') in reduced['oracle_nonnull_type']
    # Now prefer the wrong non-null class: forced-type is a different upper bound.
    wrong=SPAN_KINDS.index('quantity')+1
    logits[0,2,wrong]=11.0
    mismatched=m._oracle_ceilings(logits,valid,prepared,aligned_gold(),
                                  m._candidate_scores(logits,valid))
    assert (0,3,'actor') not in mismatched['oracle_nonnull_type']
    assert (0,3,'actor') in mismatched['oracle_forced_type']


def test_upper_bound_excludes_invalid_gold_span():
    logits,valid=synthetic_scores()
    valid[0,2]=False
    ceilings=m._oracle_ceilings(logits,valid,mock_prepared(),aligned_gold(),
                                m._candidate_scores(logits,valid))
    assert (0,3,'actor') not in ceilings['oracle_forced_type']


def test_development_selector_blocks_object_and_kind_regressions():
    gold={(0,1,'actor'),(2,3,'action')}
    base=m._summarize([(gold|{(4,5,'quantity')},gold),
                       ({(0,1,'actor')},gold)])
    # Higher F1 through removal of false positives, but lower actor recall.
    actor_lost=m._summarize([(gold,gold), ({(2,3,'action')},gold)])
    variants={name:copy.deepcopy(base) for name in m.ARMS}
    assert m._safe_select(variants)=='global'
    variants['whole_linear']=actor_lost
    assert m._safe_select(variants)=='global'
    # A strictly higher exact score + all kinds nonregressing is eligible.
    recovered=m._summarize([(gold,gold),(gold,gold)])
    variants['type_actor_guard']=recovered
    assert m._safe_select(variants)=='type_actor_guard'


def test_typed_candidate_rejects_invalid_score():
    with pytest.raises(ValueError,match='BAD_TYPED_PARAMETERS'):
        m._decode_type_scores([[]],actor_weight=float('nan'),
                              action_power=1,action_weight=1)
    with pytest.raises(ValueError,match='BAD_CANDIDATE'):
        m._decode_type_scores([[],[(0,8,1,2.0)]],actor_weight=1,
                              action_power=1,action_weight=1)


def test_checkpoint_preflight_rejects_same_path(tmp_path,monkeypatch):
    ds=tmp_path/'rows.jsonl';ds.write_text('anything',encoding='utf-8')
    a=tmp_path/'g1';a.mkdir()
    weights=a/'gamer_graph_head.safetensors';weights.write_bytes(b'weights')
    doc={'schema':'nanojev.gamer.graph.curriculum.v2.span',
         'status':'TRAINED_NOT_CERTIFIED','stage':'G1',
         'dataset_sha256':m.sha256(ds),'head_sha256':m.sha256(weights),
         'source_adapter_sha256':'fake','source_adapter':'adapter',
         'model_id':'model','production_promotion':False}
    (a/'manifest.json').write_text(json.dumps(doc),encoding='utf-8')
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *_:'fake')
    monkeypatch.setattr(cur,'load_rows',lambda *_:[{'split':'dev'},{'split':'challenge'}])
    with pytest.raises(ValueError,match='LEGACY_CUTOVER_CONFUSION'):
        m._checked_preflight(ds,a,a,tmp_path/'new')
    b=tmp_path/'cutover';b.mkdir()
    (b/'gamer_graph_head.safetensors').write_bytes(b'weights-new')
    doc['head_sha256']=m.sha256(b/'gamer_graph_head.safetensors')
    doc['deterministic_fabric']={'schema':'sample'}
    (b/'manifest.json').write_text(json.dumps(doc),encoding='utf-8')
    found,sha,cohorts=m._checked_preflight(ds,a,b,tmp_path/'new')
    assert found['legacy']['manifest'].get('deterministic_fabric') is None
    assert sha==m.sha256(ds)
    assert len(cohorts['challenge'])==1
    with pytest.raises(FileExistsError,match='IMMUTABLE'):
        m._checked_preflight(ds,a,b,tmp_path)


def test_typed_interval_scores_are_deterministic_and_nonoverlapping():
    logits,valid=synthetic_scores()
    entries=m._candidate_scores(logits,valid)
    a=m._decode_type_scores(entries,actor_weight=1.15,action_power=1.2,
                            action_weight=1.05,object_penalty=.5)
    b=m._decode_type_scores(entries,actor_weight=1.15,action_power=1.2,
                            action_weight=1.05,object_penalty=.5)
    assert a==b
    assert [(s['token_first'],s['token_last']) for s in a]==[(0,2),(3,4)]


def test_file_sha_uses_streaming_chunks(tmp_path):
    p=tmp_path/'large';p.write_bytes(b'a'*1_100_000)
    assert m.sha256(p)==hashlib.sha256(p.read_bytes()).hexdigest()


def test_campaign_runner_freezes_dev_choice_before_challenge(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    dataset=tmp_path/'dataset.jsonl';dataset.write_bytes(b'protected')
    paths={}
    for name in ('legacy','cutover'):
        folder=tmp_path/name;folder.mkdir()
        (folder/cur.HEAD_FILE).write_bytes(name.encode('ascii'))
        paths[name]=folder
    # Minimal run stubs: validate orchestration without Qwen or CUDA.
    entries={name:{'path':str(paths[name]),'manifest':{
        'head_sha256':m.sha256(paths[name]/cur.HEAD_FILE),
        'model_id':'model', 'source_adapter':'adapter',
        'source_adapter_sha256':'adapter_hash'}} for name in paths}
    cohorts={'dev':[{'split':'dev'}], 'challenge':[{'split':'challenge'}]}
    monkeypatch.setattr(m,'_checked_preflight',lambda *_:(entries,m.sha256(dataset),cohorts))
    class FakeHead:
        def eval(self): return self
    monkeypatch.setattr(cur,'_load_checkpoint',lambda *_,**__: (FakeHead(),{}))
    monkeypatch.setattr(backbone,'model_components',lambda *_,**__:('model','tok'))
    monkeypatch.setattr(backbone,'adapter_fingerprint',lambda *_:'adapter_hash')
    order=[]
    good={(0,1,'actor')}
    neutral=m._summarize([(set(),good)])
    better=m._summarize([(good,good)])
    def evaluate(*,split,**kwargs):
        order.append(split)
        if split=='challenge':
            assert decisions and len(decisions)==2
        result={name:{arm:copy.deepcopy(neutral) for arm in m.ARMS}
                for name in ('legacy','cutover')}
        if split=='dev':
            result['cutover']['whole_linear']=better
        oracle={name:{key:better for key in ('oracle_eligible',
                'oracle_nonnull_type','oracle_forced_type')}
                for name in ('legacy','cutover')}
        return result,oracle,[]
    monkeypatch.setattr(m,'_evaluate_split',evaluate)
    decisions=[]
    old_selector=m._safe_select
    def select(metrics):
        result=old_selector(metrics)
        decisions.append(result)
        return result
    monkeypatch.setattr(m,'_safe_select',select)
    output=tmp_path/'report'
    record=m.run(dataset=dataset,legacy=paths['legacy'],cutover=paths['cutover'],
                 output=output,device='cpu')
    assert order==['dev','challenge']
    assert record['arms']['legacy']['selected_decoder']=='global'
    assert record['arms']['cutover']['selected_decoder']=='whole_linear'
    assert record['production_promotion'] is False
    assert record['g2_unlocked'] is False
    assert record['sealed_test_used'] is False
    assert (output/'report.json').is_file()
    assert (output/'cases.jsonl').read_text()==''
    for folder in paths.values():
        assert (folder/cur.HEAD_FILE).read_bytes()==folder.name.encode('ascii')


def test_output_under_checkpoint_is_prohibited(tmp_path):
    with pytest.raises(PermissionError,match='INSIDE_PROTECTED'):
        m._checked_preflight(tmp_path/'dataset',tmp_path/'legacy',
                             tmp_path/'cutover',tmp_path/'legacy'/'diagnostic')
