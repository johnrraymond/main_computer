"""G2-0091: immutable comparison, paired attribution, sealed scoring governance."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_g2_frozen_generalization_0091 as exp
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES


SPEC={'method':'joint','threshold':0.0,
      'out_caps':{'actor|action|owns':2},
      'in_caps':{'action|actor|owns':2}}
OBJECTS=[[0,2,'actor'],[3,5,'action']]
GOLD={'nodes':[{'id':'n0','kind':'actor','span':[0,2]},
               {'id':'n1','kind':'action','span':[3,5]}],
      'edges':[{'src':'n0','dst':'n1','type':'owns'}]}


def surface(*,has_edge=True):
    label=BIND_CLASSES.index('owns')-1
    m1=[-4.]*(len(BIND_CLASSES)-1)
    m2=[-4.]*(len(BIND_CLASSES)-1)
    if has_edge:m1[label]=6.
    edges=[[["actor",[0,2]],["action",[3,5]],"owns"]] if has_edge else []
    return {'g1_objects':deepcopy(OBJECTS),
            'pair_margins':[[0,1,m1],[1,0,m2]],'binding_edges':edges}


def record(cid,split='dev',family='allocation',*,fuzz_side=None,gold=None):
    prompt=f'G2-0091 synthetic prompt {cid}'
    r={'case_id':cid,'split':split,'family':family,
       'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
       'g1_objects':deepcopy(OBJECTS),
       'raw_heads':{'parent':surface(has_edge=False),
                    'memorized':surface(),
                    'zero_hidden_memorized':surface(has_edge=False)}}
    if fuzz_side is not None:
        r.update({'fuzz_pair':'pair-1','fuzz_side':fuzz_side,
                  'fuzz_mode':'renaming'})
    answer={'case_id':cid,'split':split,'family':family,
            'prompt_sha256':r['prompt_sha256'],
            'graph':deepcopy(gold or GOLD)}
    if fuzz_side is not None:
        answer.update({'fuzz_pair':'pair-1','fuzz_side':fuzz_side,
                       'fuzz_mode':'renaming'})
    query={k:v for k,v in answer.items() if k not in ('graph',)}
    query['prompt']=prompt
    return r,answer,query


def test_four_arms_use_same_g1_objects_and_frozen_decoder():
    r,_,_=record('one')
    got=exp._derived_arms(r,SPEC)
    assert len(got['g2_0087_native'])==0
    assert len(got['g2_0088_joint'])==0
    assert len(got['g2_0090_native'])==1
    assert len(got['g2_0090_joint'])==1
    assert got['g2_0090_no_binding']==set()
    altered=deepcopy(r)
    altered['raw_heads']['memorized']['g1_objects'][0][1]=999
    with pytest.raises(ValueError,match='G2_0091_(PREDICTED_G1_NOT_SHARED|G1_DIFFERENT_BETWEEN_ARMS)|G2_0088_'):
        exp._derived_arms(altered,SPEC)


def test_per_case_and_family_exact_recomputed_from_gold():
    r,answer,_=record('one')
    edges=exp._derived_arms(r,SPEC)
    m,f,cases=exp._metrics([(r,edges)],{'one':answer},'g2_0090_joint')
    assert m['exact']==m['reachable_exact']==m['reachable_cases']==1
    assert m['edge_f1']==1
    assert f['allocation']['exact']==f['allocation']['reachable_exact']==1
    assert cases['one']['fp']==cases['one']['fn']==0
    control,_,_=exp._metrics([(r,edges)],{'one':answer},'g2_0088_joint')
    assert control['exact']==0 and control['fn']==1


def test_matched_gain_and_loss_are_both_counted():
    base={'a':{'exact':1,'fp':0,'fn':0},
          'b':{'exact':0,'fp':2,'fn':1},
          'c':{'exact':1,'fp':0,'fn':0}}
    changed={'a':{'exact':0,'fp':1,'fn':0},
             'b':{'exact':1,'fp':0,'fn':0},
             'c':{'exact':1,'fp':0,'fn':0}}
    d=exp.paired_delta(changed,base)
    assert d['exact_gain']==d['exact_loss']==d['exact_both']==1
    assert d['edge_errors_decreased']==d['edge_errors_increased']==1
    with pytest.raises(ValueError,match='PAIRED_CASE_MISMATCH'):
        exp.paired_delta({'a':changed['a']},base)


def test_counterfactual_needs_correct_gold_delta_not_bare_invariance(monkeypatch):
    monkeypatch.setattr(exp,'FUZZ_PAIRS',1)
    base,gold1,_=record('b','fuzz',fuzz_side='base')
    changed,gold2,_=record('f','fuzz',fuzz_side='fuzz')
    records=[(base,exp._derived_arms(base,SPEC)),
             (changed,exp._derived_arms(changed,SPEC))]
    out=exp.counterfactual_metrics(records,{'b':gold1,'f':gold2})
    assert out['g2_0090_joint']['summary']['correct_both']==1
    assert out['g2_0090_joint']['summary']['delta_exact_g1_exact']==1
    # A transformation that changes the gold relation must no longer be
    # silently treated as a required invariant.
    changed['fuzz_mode']='role_swap';base['fuzz_mode']='role_swap'
    changed_gold=deepcopy(gold2)
    changed_gold['graph']['edges']=[]
    out=exp.counterfactual_metrics(records,{'b':gold1,'f':changed_gold})
    assert out['g2_0090_joint']['summary']['gold_invariant_pairs']==0
    assert out['g2_0090_joint']['summary']['correct_both']==0
    assert out['g2_0090_joint']['summary']['delta_exact']==0


def _file_write(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    exp.write_jsonl(path,rows)


def make_synthetic(tmp_path,monkeypatch):
    monkeypatch.setattr(exp,'DEV',1)
    monkeypatch.setattr(exp,'CHALLENGE',1)
    monkeypatch.setattr(exp,'HISTORICAL_DEV',1)
    monkeypatch.setattr(exp,'HISTORICAL_CHALLENGE',1)
    monkeypatch.setattr(exp,'HISTORICAL',2)
    monkeypatch.setattr(exp,'FUZZ_PAIRS',1)
    dest=tmp_path/'oracle-fabric-g2-frozen-generalization-0091'
    sets=[record('dev','dev'),record('challenge','challenge'),
          record('old-dev','historical_dev'),record('old-challenge','historical_challenge'),
          record('fuzz-base','fuzz',fuzz_side='base'),
          record('fuzz-changed','fuzz',fuzz_side='fuzz')]
    predicted=[r for r,a,q in sets]
    answers=[a for r,a,q in sets]
    queries=[q for r,a,q in sets]
    _file_write(dest/'seal'/'prompts.jsonl',queries)
    _file_write(dest/'seal'/'answers.SECRET.jsonl',answers)
    _file_write(dest/'blind'/'predictions.jsonl',predicted)
    source={'decoder':SPEC,'parent_sha256':'hash-parent',
            'memorized_sha256':'hash-memorized','stage_lock_sha256':'hash-lock'}
    exp.write_json(dest/'comparison.json',{'arms':list(exp.ARMS)})
    committed={'source':source,
               'prompts_sha256':exp.sha(dest/'seal'/'prompts.jsonl'),
               'answers_sha256':exp.sha(dest/'seal'/'answers.SECRET.jsonl'),
               'comparison_sha256':exp.sha(dest/'comparison.json')}
    exp.write_json(dest/'blind'/'lock.json',{'schema':exp.SCHEMA,
        'state':'BLIND_PREDICTIONS_LOCKED','g3_unlocked':False,
        'gold_opened_during_predict':False,
        'prompts_sha256':committed['prompts_sha256'],
        'comparison_sha256':committed['comparison_sha256'],
        'head_0087_sha256':source['parent_sha256'],
        'head_0090_sha256':source['memorized_sha256'],
        'predictions_sha256':exp.sha(dest/'blind'/'predictions.jsonl')})
    monkeypatch.setattr(exp,'check_seal',lambda paths,dest,gold:committed)
    return dest


def test_synthetic_scoring_recomputes_paired_gains(tmp_path,monkeypatch):
    dest=make_synthetic(tmp_path,monkeypatch)
    result=exp.score(root=tmp_path,output=dest)
    assert result['g3_unlocked'] is False and result['g2_certified'] is False
    assert result['splits']['dev']['arms']['g2_0090_joint']['exact']==1
    assert result['splits']['dev']['arms']['g2_0088_joint']['exact']==0
    assert result['splits']['challenge']['paired_vs_0088']['exact_gain']==1
    assert result['counterfactual']['g2_0090_joint']['summary']['correct_both']==1
    assert (dest/'scored'/'cases.jsonl').exists()
    with pytest.raises(ValueError,match='G2_0091_ALREADY_SCORED'):
        exp.score(root=tmp_path,output=dest)


def test_reject_changed_gold_and_predictions(tmp_path,monkeypatch):
    dest=make_synthetic(tmp_path,monkeypatch)
    f=dest/'blind'/'predictions.jsonl'
    f.write_text(f.read_text()+'\n',encoding='utf-8')
    with pytest.raises(ValueError,match='G2_0091_BLIND_LOCK_CHANGED'):
        exp.score(root=tmp_path,output=dest)
    # The actual seal validation (not the synthetic monkeypatch) always binds
    # the answers file before score opens it.


def test_head_manifest_non_deployable_in_archive():
    # Guard against accidental promotion of memorization weights.
    assert exp.ARMS==('g2_0087_native','g2_0088_joint',
                      'g2_0090_native','g2_0090_joint')
    assert exp.SCHEMA.endswith('.v1')


def test_gold_seal_is_verified_only_after_blind_prediction(tmp_path,monkeypatch):
    source={'decoder':SPEC,'parent_sha256':'sha1','memorized_sha256':'sha2'}
    monkeypatch.setattr(exp,'verify_parent',lambda paths:source)
    dest=tmp_path/'g2-0091';(dest/'seal').mkdir(parents=True)
    exp.write_json(dest/'comparison.json',{'arms':list(exp.ARMS)})
    exp.write_jsonl(dest/'seal'/'prompts.jsonl',[{'case_id':'q','prompt':'foo'}])
    exp.write_jsonl(dest/'seal'/'answers.SECRET.jsonl',[{'case_id':'q','graph':GOLD}])
    commit={'schema':exp.SCHEMA,'state':'GOLD_COMMITTED_NO_PREDICTIONS',
        'g3_unlocked':False,'no_training':True,'runner_sha256':exp.sha(exp.__file__),
        'comparison_sha256':exp.sha(dest/'comparison.json'),
        'prompts_sha256':exp.sha(dest/'seal'/'prompts.jsonl'),
        'answers_sha256':exp.sha(dest/'seal'/'answers.SECRET.jsonl'),
        'dev':exp.DEV,'challenge':exp.CHALLENGE,'historical':exp.HISTORICAL,
        'fuzz':exp.FUZZ_PAIRS*2,'source':source}
    exp.write_json(dest/'seal'/'commit.json',commit)
    exp.check_seal({},dest,gold=False)
    exp.check_seal({},dest,gold=True)
    (dest/'seal'/'answers.SECRET.jsonl').write_text('tampered\n',encoding='utf-8')
    # Critical: the prediction phase doesn't open prospective answers.
    exp.check_seal({},dest,gold=False)
    with pytest.raises(ValueError,match='G2_0091_ANSWERS_CHANGED'):
        exp.check_seal({},dest,gold=True)
    (dest/'comparison.json').write_text('{}\n',encoding='utf-8')
    with pytest.raises(ValueError,match='G2_0091_SEAL_OR_PARENT_CHANGED'):
        exp.check_seal({},dest,gold=False)


def test_reject_score_if_metadata_diverges_from_sealed_answers(tmp_path,monkeypatch):
    dest=make_synthetic(tmp_path,monkeypatch)
    rows=exp.read_jsonl(dest/'blind'/'predictions.jsonl')
    rows[0]['split']='challenge'
    (dest/'blind'/'predictions.jsonl').write_text(
        ''.join(json.dumps(row,sort_keys=True)+'\n' for row in rows),encoding='utf-8')
    lock=exp.js(dest/'blind'/'lock.json')
    lock['predictions_sha256']=exp.sha(dest/'blind'/'predictions.jsonl')
    (dest/'blind'/'lock.json').write_text(
        json.dumps(lock,sort_keys=True)+'\n',encoding='utf-8')
    with pytest.raises(ValueError,match='G2_0091_BLIND_GOLD_PROMPT_ALIGNMENT'):
        exp.score(root=tmp_path,output=dest)
