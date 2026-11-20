"""G2-0088 frozen assembly, calibrated selection, seal and adversarial contracts."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import random
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as x
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES


def fixture(*,n=5):
    objects=[[i*4,i*4+2,('quantity' if i==0 else 'action' if i<=3 else 'outcome')]
             for i in range(n)]
    m=[]
    for i in range(n):
        for j in range(n):
            if i==j:continue
            logits=[-8.0]*(len(BIND_CLASSES)-1)
            if i==0 and j==1:logits[BIND_CLASSES.index('applies_to')-1]=5.
            if i==0 and j==2:logits[BIND_CLASSES.index('applies_to')-1]=9.
            if i==0 and j==3:logits[BIND_CLASSES.index('applies_to')-1]=3.
            if i==1 and j==4:logits[BIND_CLASSES.index('measures')-1]=4.
            m.append([i,j,logits])
    native=[]
    for i,j,logits in m:
        if max(logits)>0:
            t=logits.index(max(logits))+1
            native.append([[objects[i][2],objects[i][:2]],
                           [objects[j][2],objects[j][:2]],BIND_CLASSES[t]])
    return {'g1_objects':objects,'pair_margins':m,'binding_edges':native}


def gold(*,correct=(1,)):
    r=fixture();objs=r['g1_objects']
    nodes=[{'id':f'n{i}','kind':obj[2],'span':obj[:2]} for i,obj in enumerate(objs)]
    edges=[{'src':'n0','dst':f'n{j}','type':'applies_to'} for j in correct]
    return {'nodes':nodes,'edges':edges}


def test_baseline_native_and_keyed_surface():
    r=fixture()
    assert x.decode(r,{'method':'native'})==x.previous._keyed_edges(r['binding_edges'])
    assert len(x.decode(r,{'method':'native'}))==4


def test_margin_threshold_and_topk():
    r=fixture()
    assert len(x.decode(r,{'method':'threshold','threshold':4}))==2
    top=x.decode(r,{'method':'source_topk','threshold':0,'k':1})
    assert any(e[3]==(8,10) for e in top)
    assert not any(e[3]==(4,6) for e in top)  # hard false outranks true
    assert len(top)==2  # quantity->action + action->outcome


def test_joint_global_degree_constraints_and_multi_edge_allowed():
    r=fixture()
    caps={'out_caps':{'quantity|action|applies_to':2},
          'in_caps':{'action|quantity|applies_to':1}}
    out=x.decode(r,{'method':'joint','threshold':0,**caps})
    q=[e for e in out if e[0]=='quantity']
    assert len(q)==2
    assert len(out)==3


def test_type_aware_thresholds_are_applied_only_to_matching_type():
    r=fixture()
    spec={'method':'type_aware','threshold':0,
          'kind_thresholds':{'quantity|action|applies_to':6}}
    out=x.decode(r,spec)
    assert len(out)==2  # hard false quantity edge + unrelated action edge
    assert any(e[4]=='measures' for e in out)


def test_fail_closed_invalid_surface():
    cases=[]
    bad=fixture();bad['pair_margins']=bad['pair_margins'][:-1];cases.append(bad)
    bad=fixture();bad['pair_margins'][0][2][0]=float('nan');cases.append(bad)
    bad=fixture();bad['pair_margins'][1]=bad['pair_margins'][0];cases.append(bad)
    bad=fixture();bad['g1_objects'][1]=bad['g1_objects'][0];cases.append(bad)
    for row in cases:
        with pytest.raises(ValueError,match='G2_0088_'):
            x.decode(row,{'method':'threshold','threshold':0})


def test_decoder_never_adds_self_edge_or_multiple_labels():
    for method in x.METHODS:
        spec={'method':method,'threshold':-1,'k':3}
        result=x.decode(fixture(),spec)
        assert all(e[1]!=e[3] for e in result)
        assert len({e[:4] for e in result})==len(result)


def test_metric_recompute_and_oracle_limit():
    r=fixture();g=gold()
    score=x.score_entries([(r,g)],spec={'method':'threshold','threshold':0})
    assert score['cases']==1 and score['exact']==0
    assert score['tp']==1 and score['fp']==3
    diag=x.diagnose([(r,g)])
    assert diag['counts']['unreachable']==0
    assert diag['counts']['false_edges']==3
    assert diag['counts']['gold_edges']==1
    assert diag['top_k_oracle_ceiling']['1']['tp']==0
    assert diag['top_k_oracle_ceiling']['2']['tp']==1


def test_g1_missing_endpoint_limits_oracle():
    r=fixture();r['g1_objects']=r['g1_objects'][:-1]
    r['pair_margins']=[v for v in r['pair_margins'] if v[0]<4 and v[1]<4]
    r['binding_edges']=[v for v in r['binding_edges'] if v[0][0]!='outcome' and v[1][0]!='outcome']
    diag=x.diagnose([(r,gold(correct=(1,)))])
    assert diag['counts']['unreachable']==0
    extra=gold(correct=(1,));extra['edges'].append({'src':'n1','dst':'n4','type':'measures'})
    assert x.diagnose([(r,extra)])['counts']['unreachable']==1


def test_structural_cap_uses_calibration_gold_and_preserves_multiplicity():
    caps,by_kind=x.fit_caps([(fixture(),gold(correct=(1,2)))])
    assert caps['out_caps']['quantity|action|applies_to']>=3
    assert 'quantity|action|applies_to' in by_kind


def test_group_threshold_fit_rejects_small_sample_oracles():
    assert x._kind_thresholds({'quantity|action|applies_to':[(4.,1)]},0)=={}
    many=[(float(i%10),int(i%8==0)) for i in range(32)]
    assert x._kind_thresholds({'quantity|action|applies_to':many},0)=={}


def test_one_pass_bakeoff_uses_separate_selection(monkeypatch):
    monkeypatch.setattr(x.parent,'DEV',4)
    monkeypatch.setattr(x.parent,'CHALLENGE',2)
    train=[(fixture(),gold()) for _ in range(4)]
    selection=[(fixture(),gold(correct=(2,))) for _ in range(2)]
    result=x.bakeoff(train,selection)
    assert set(v['method'] for v in result['arms'])==set(x.METHODS)
    assert result['candidate_count']==1+len(x.GRID)*6
    assert result['calibration_cases']==4 and result['selection_cases']==2
    assert result['winner_method'] in x.METHODS


def test_seal_does_not_read_gold_during_predict(monkeypatch,tmp_path):
    root=tmp_path/'report';root.mkdir()
    seal=root/'seal';seal.mkdir()
    for file,content in [('prompts.jsonl','p'),('answers.SECRET.jsonl','a')]:
        (seal/file).write_text(content)
    (root/'selection.json').write_text('s')
    p=root/'parent';(p/'blind').mkdir(parents=True)
    (p/'scored').mkdir();(p/'training').mkdir()
    (p/'blind'/'predictions.jsonl').write_text('prior blind')
    (p/'scored'/'report.json').write_text('prior report')
    (p/'training'/x.cur.HEAD_FILE).write_text('prior head')
    commits={'schema':x.SCHEMA,'status':'FROZEN_SELECTION_AND_GOLD_COMMITTED_NOT_PREDICTED',
             'fresh_dev':x.DEV,'fresh_challenge':x.CHALLENGE,
             'historical':112,'fuzz':x.FUZZ_PAIRS*2,
             'G3_training_unlocked':False,'promotion':False,
             'runner_sha256':x.sha(x.__file__),
             'prompts_sha256':x.sha(seal/'prompts.jsonl'),
             'answers_sha256':x.sha(seal/'answers.SECRET.jsonl'),
             'selection_sha256':x.sha(root/'selection.json'),
             'parent_blind_sha256':x.sha(p/'blind'/'predictions.jsonl'),
             'parent_scored_sha256':x.sha(p/'scored'/'report.json'),
             'parent_head_sha256':x.sha(p/'training'/x.cur.HEAD_FILE)}
    x.write_json(seal/'commit.json',commits)
    def reject_gold(path):
        if 'SECRET' in str(path):raise AssertionError('gold opened in blind phase')
        return x.parent.sha(path)
    monkeypatch.setattr(x,'sha',reject_gold)
    assert x.check_seal({'parent_0087':p},root,verify_answers=False)==commits
    with pytest.raises(AssertionError,match='gold opened'):
        x.check_seal({'parent_0087':p},root,verify_answers=True)


def test_fuzz_generator_produces_author_annotated_pairs():
    pairs=x.fuzz.make_pairs(seed=x.SEED,pairs=x.FUZZ_PAIRS)
    assert len(pairs)==8
    assert {p['mode'] for p in pairs}==set(x.fuzz.MODES)
    assert all(p['base']['prompt']!=p['fuzz']['prompt'] for p in pairs)


def test_source_freeze_contract_no_fitting_in_prediction():
    import inspect
    text=inspect.getsource(x.predict)
    assert 'fit_caps(' not in text and 'bakeoff(' not in text
    assert 'answers.SECRET' not in text and 'train(' not in text
    assert 'verify_answers=False' not in inspect.getsource(x.score)


def test_gate_values_are_read_directly_from_original_contract():
    assert x.cur.GATE_REQUIREMENTS['G2']['dev']==.8
    assert x.cur.GATE_REQUIREMENTS['G2']['challenge']==.7
    assert x.cur.GATE_REQUIREMENTS['G2']['neural_gain']==.08


def test_empty_object_lattice_is_safe():
    rec={'g1_objects':[],'binding_edges':[],'pair_margins':[]}
    for method in x.METHODS:
        assert x.decode(rec,{'method':method,'threshold':0})==set()


def test_wrong_cap_does_not_leak_gold_information():
    rec=fixture()
    spec={'method':'joint','threshold':0,'out_caps':{'quantity|action|applies_to':1},
          'in_caps':{}}
    assert len([r for r in x.decode(rec,spec) if r[0]=='quantity'])==1


def synthetic_run(tmp_path):
    root=tmp_path/'runtime';root.mkdir()
    prior=root/'oracle-fabric-g2-hard-negative-learning-0087'
    for rel in ('blind','scored','training'):(prior/rel).mkdir(parents=True,exist_ok=True)
    (prior/'blind'/'predictions.jsonl').write_text('prior blind')
    (prior/'scored'/'report.json').write_text('prior report')
    (prior/'training'/x.cur.HEAD_FILE).write_text('prior head')
    dest=root/'oracle-fabric-g2-posttrain-graph-bakeoff-0088';dest.mkdir()
    seal=dest/'seal';seal.mkdir()
    (dest/'blind').mkdir()
    spec={'method':'source_topk','threshold':0.,'k':1}
    x.write_json(dest/'selection.json',{'schema':x.SCHEMA,
        'status':'DECODER_FROZEN_BEFORE_NEW_INFERENCE','winner_method':'source_topk',
        'winner':spec})
    raw=fixture();gold_graph=gold(correct=(2,))
    gold_graph['edges'].append({'src':'n1','dst':'n4','type':'measures'})
    chosen=x.decode(raw,spec)
    selected=x.previous._safe_edges({((e[0],e[1]),(e[2],e[3]),e[4]) for e in chosen})
    prompts=[];answers=[];pred=[]
    splits=[('historical_dev',80),('historical_challenge',32),
            ('dev',x.DEV),('challenge',x.CHALLENGE),('fuzz',x.FUZZ_PAIRS*2)]
    for split,num in splits:
        for i in range(num):
            ident=f'{split}-{i:04d}'
            prompt='test prompt '+ident
            digest=hashlib.sha256(prompt.encode()).hexdigest()
            family=('encounter','allocation')[i%2]
            shared={'case_id':ident,'split':split,'family':family,'prompt_sha256':digest}
            if split=='fuzz':
                shared.update(fuzz_pair=f'synth-{i//2}',
                              fuzz_mode=('renaming','role_swap','added_distractor','action_removal')[(i//2)%4],
                              fuzz_side='base' if i%2==0 else 'fuzz')
            prompts.append(dict(shared,prompt=prompt))
            answers.append(dict(shared,graph=gold_graph,gold_objects=[]))
            mode=lambda edges:{'g1_objects':raw['g1_objects'],'binding_edges':edges}
            pred.append(dict(shared,raw_surface=raw,modes={
                'selected':mode(selected),'native':mode(raw['binding_edges']),
                'control':mode([]),'untrained':mode([]),'no_binding':mode([]),
                'zero_hidden':mode([])}))
    x.write_jsonl(seal/'prompts.jsonl',prompts)
    x.write_jsonl(seal/'answers.SECRET.jsonl',answers)
    x.write_jsonl(dest/'blind'/'predictions.jsonl',pred)
    s={'schema':x.SCHEMA,'status':'FROZEN_SELECTION_AND_GOLD_COMMITTED_NOT_PREDICTED',
       'fresh_dev':x.DEV,'fresh_challenge':x.CHALLENGE,'historical':112,
       'fuzz':x.FUZZ_PAIRS*2,'runner_sha256':x.sha(x.__file__),
       'G3_training_unlocked':False,'promotion':False,
       'prompts_sha256':x.sha(seal/'prompts.jsonl'),
       'answers_sha256':x.sha(seal/'answers.SECRET.jsonl'),
       'selection_sha256':x.sha(dest/'selection.json'),
       'parent_blind_sha256':x.sha(prior/'blind'/'predictions.jsonl'),
       'parent_scored_sha256':x.sha(prior/'scored'/'report.json'),
       'parent_head_sha256':x.sha(prior/'training'/x.cur.HEAD_FILE)}
    x.write_json(seal/'commit.json',s)
    lock={'schema':x.SCHEMA,'status':'BLIND_LOCKED_NOT_SCORED',
          'cases':len(pred),'prompts_sha256':s['prompts_sha256'],
          'selection_sha256':s['selection_sha256'],
          'checkpoint_sha256':s['parent_head_sha256'],
          'predictions_sha256':x.sha(dest/'blind'/'predictions.jsonl'),
          'gold_read_during_prediction':False}
    x.write_json(dest/'blind'/'lock.json',lock)
    return root,dest


def test_synthetic_seal_score_and_second_score_rejected(tmp_path):
    root,dest=synthetic_run(tmp_path)
    report=x.score(root=root,output=dest)
    assert report['status']=='G2_0088_NUMERIC_GATES_PASSED_NOT_CERTIFIED'
    assert report['g2_certified'] is False
    assert report['g3_training_unlocked'] is False
    assert report['historical_and_prospective']['dev']['cases']==x.DEV
    assert report['historical_and_prospective']['dev']['arms']['selected']['exact']==x.DEV
    assert report['historical_and_prospective']['challenge']['arms']['selected']['exact']==x.CHALLENGE
    assert report['historical_and_prospective']['dev']['arms']['native']['exact']==0
    assert report['fuzz']['totals']['pairs']==x.FUZZ_PAIRS
    with pytest.raises(ValueError,match='ALREADY_SCORED'):
        x.score(root=root,output=dest)


def test_tampered_predictions_rejected_before_gold_open(tmp_path):
    root,dest=synthetic_run(tmp_path)
    with (dest/'blind'/'predictions.jsonl').open('a') as f:f.write('corrupted\n')
    with pytest.raises(ValueError,match='BLIND_INTEGRITY_FAILED'):
        x.score(root=root,output=dest)
    assert not (dest/'scored').exists()


def test_tampered_selection_rejected_before_prediction_scoring(tmp_path):
    root,dest=synthetic_run(tmp_path)
    selection=x.js(dest/'selection.json')
    selection['winner']['threshold']=99.
    x.write_json(dest/'selection.json',selection)
    with pytest.raises(ValueError,match='SEALED_FILE_DRIFT'):
        x.score(root=root,output=dest)


def test_forged_selected_edges_rejected_by_independent_replay(tmp_path):
    root,dest=synthetic_run(tmp_path)
    rows=x.read_jsonl(dest/'blind'/'predictions.jsonl')
    rows[0]['modes']['selected']['binding_edges']=[]
    (dest/'blind'/'predictions.jsonl').write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
    lock=x.js(dest/'blind'/'lock.json')
    lock['predictions_sha256']=x.sha(dest/'blind'/'predictions.jsonl')
    x.write_json(dest/'blind'/'lock.json',lock)
    with pytest.raises(ValueError,match='BLIND_DECODE_REPLAY_DRIFT'):
        x.score(root=root,output=dest)
    assert not (dest/'scored').exists()
