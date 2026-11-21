"""G2-0093 source-grounded frozen atlas and separation/governance tests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_g2_error_atlas_0093 as m
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as old
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES


def test_known_prompt_exclusions_accept_cli_string_root_and_preserve_lineage(tmp_path,monkeypatch):
    # The CLI passes --root as str; frozen G2-0091 exclusion reads Path / sub.
    root=tmp_path/'experiments'
    root.mkdir()
    parent_0090=root/'oracle-fabric-g2-progressive-trainability-0090'
    parent_0092=root/'oracle-fabric-g2-mixed-family-fuzz-0092'
    parent_0090.mkdir()
    parent_0092.mkdir()
    m.write(parent_0090/'provenance.json',{'selected_cases':[
        {'prompt_sha256':'0090-selected'}]})
    m.write(parent_0092/'provenance.json',{'diagnostic_fuzz':[
        {'base_sha256':'0092-base','fuzz_sha256':'0092-fuzz'}]})
    for experiment,sha256 in (
        ('oracle-fabric-g2-hard-negative-learning-0087','0087-sealed'),
        ('oracle-fabric-g2-posttrain-graph-bakeoff-0088','0088-sealed'),
        ('oracle-fabric-g2-structural-learning-0089','0089-sealed')):
        seal=root/experiment/'seal'
        seal.mkdir(parents=True)
        m.jsonl_write(seal/'prompts.jsonl',[{'prompt_sha256':sha256}])
    for experiment,prompt in (
        ('oracle-fabric-g2-frozen-generalization-0091','0091-prompt'),
        ('oracle-fabric-g2-mixed-family-fuzz-0092','0092-prompt')):
        seal=root/experiment/'seal'
        seal.mkdir(parents=True,exist_ok=True)
        m.jsonl_write(seal/'prompts.jsonl',[{'prompt':prompt}])

    monkeypatch.setattr(m.frozen.hard,'excluded_prompts',lambda paths:{'dataset-historical'})
    monkeypatch.setattr(m.cur,'load_rows',lambda path:[{'prompt':'training-prompt'}])
    paths={'dataset':root/'dataset.jsonl',
           'parent_0090':parent_0090,'parent_0092':parent_0092}
    expected={'dataset-historical','0090-selected','0087-sealed',
              '0088-sealed','0089-sealed','0092-base','0092-fuzz',
              m.digest('training-prompt'),m.digest('0091-prompt'),
              m.digest('0092-prompt')}
    assert m._known_prompts(paths,str(root))==expected
    assert m._known_prompts(paths,root)==expected


@pytest.mark.parametrize('family',m.FAMILIES)
@pytest.mark.parametrize('seed',[7,19,87,163,9001])
def test_four_counterfactuals_share_a_common_base_and_valid_author_gold(family,seed):
    suite=m._controlled_suite(family,seed,930001)
    assert set(suite)==set(m.MODES)
    assert len({m.digest(x['prompt']) for x in suite.values()})==5
    base=suite['base']
    for mode in ('renaming','role_swap','added_distractor'):
        assert [(e['src'],e['dst'],e['type']) for e in base['graph']['edges']]==[
            (e['src'],e['dst'],e['type']) for e in suite[mode]['graph']['edges']]
    assert len(suite['action_removal']['graph']['edges'])<len(base['graph']['edges'])
    for row in suite.values():
        assert row['annotation_provenance']=='AUTHOR_MARKED_IN_SOURCE_GENERATOR'
        for node in row['graph']['nodes']:
            if 'span' in node:
                a,b=node['span'];assert 0<=a<b<=len(row['prompt'])


def _object_and_surface(graph):
    objs=sorted([[*n['span'],n['kind']] for n in graph['nodes'] if 'span' in n],
                key=lambda n:(n[0],n[1]))
    # A small but legitimate G1 prediction; both heads observe identical nodes.
    objs=objs[:4];n=len(objs)
    margins=[[i,j,[-3.0]*(len(BIND_CLASSES)-1)]
             for i in range(n) for j in range(n) if i!=j]
    return objs,{'g1_objects':objs,'binding_edges':[],'pair_margins':margins}


def test_g1_unreachable_is_not_counted_as_a_g2_missing_reachable_edge():
    suite=m._controlled_suite('allocation',19,9301)
    graph=suite['base']['graph']
    gold=old.truth(graph)
    objs,surface=_object_and_surface(graph)
    events,reachable=m._edge_rows(set(),gold,objs)
    assert len(reachable)<len(gold)
    assert len(events)==len(gold)
    assert any(x['kind']=='unreachable_g1' for x in events)


def test_complete_renaming_and_action_removal_semantic_comparison():
    for family in m.FAMILIES:
        suite=m._controlled_suite(family,19,9301)
        records=[]
        for mode,row in suite.items():
            gold=old.truth(row['graph'])
            g1=[[n['span'][0],n['span'][1],n['kind']]
                for n in row['graph']['nodes'] if 'span' in n]
            records.append({'mode':mode,'gold_graph':dict(row['graph'],
                _g2_0093_prompt=row['prompt']),'g1_exact':True,
                'arms':{arm:{'edges':[m._json_edge(e) for e in sorted(gold)]}
                        for arm in m.ARMS}})
        summary=m._score_suite(records)
        assert all(v['correct_both'] and v['delta_correct']
                   for v in summary.values() for v in v.values())
        assert summary['action_removal']['g2_0092_B']['gold_delta']>0
        assert summary['action_removal']['g2_0092_B']['alignment']=='surface-occurrence'


def test_counterfactual_change_that_does_not_remove_an_action_is_refused():
    suite=m._controlled_suite('allocation',9,93001)
    records=[]
    for mode,row in suite.items():
        if mode=='action_removal':row=suite['base']
        records.append({'mode':mode,'gold_graph':dict(row['graph'],
              _g2_0093_prompt=row['prompt']),'g1_exact':True,
              'arms':{arm:{'edges':[]} for arm in m.ARMS}})
    with pytest.raises(ValueError,match='COUNTERFACTUAL_GOLD_EXPECTATION'):
        m._score_suite(records)


def test_frozen_hypothesis_target_never_reads_gold():
    objects=[[0,4,'actor'],[6,10,'action'],[12,16,'action']]
    surf={'g1_objects':objects,'binding_edges':[],
          'pair_margins':[[i,j,([2.0]+[-1.0]*(len(BIND_CLASSES)-2))]
              for i in range(3) for j in range(3) if i!=j]}
    rule={'category':'extra','src_kind':'actor','dst_kind':'action','binding':BIND_CLASSES[1]}
    chosen=m._blind_target(surf,rule)
    assert chosen==(0,1)
    changed=deepcopy(rule);changed['src_kind']='quantity'
    assert m._blind_target(surf,changed) is None
    with pytest.raises(Exception):
        m._blind_target(dict(surf,pair_margins=[]),rule)


@pytest.fixture
def fake_experiment(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'parents',lambda paths:{'decoder':{'method':'threshold','threshold':0},
                     'head_0087_sha256':'a'*64,'head_0092_B_sha256':'b'*64})
    monkeypatch.setattr(m,'_known_prompts',lambda paths,root:set())
    # A 10-base synthetic integrity test only; user run defaults to 1,000 bases.
    output=tmp_path/'atlas'
    m.prepare(root=tmp_path,output=output,bases=10)
    prompts=list(m.jsonl(output/'seal'/'prompts.jsonl'))
    answers={r['case_id']:r for split in ('discovery','confirmation') for r in
             m.jsonl(output/'seal'/f'answers.{split}.SECRET.jsonl')}
    shard_dir=output/'blind'/'shards';shard_dir.mkdir(parents=True)
    entries=[]
    for p in prompts:
        objects,surface=_object_and_surface(answers[p['case_id']]['graph'])
        case={k:p[k] for k in ('case_id','prompt_sha256','family','split','mode','base_id')}
        case.update({'g1_objects':objects,'raw_heads':{arm:deepcopy(surface) for arm in m.ARMS}})
        path=shard_dir/(p['case_id']+'.json.gz');m._atomic_gzip(path,case)
        entries.append((p['case_id'],m.sha(path)))
    blind=output/'blind'
    m.write(blind/'index.json',{'schema':m.SCHEMA,'cases':len(prompts),'entries':entries,
            'prompts_sha256':m.sha(output/'seal'/'prompts.jsonl'),
            'gold_used':False,'g3_unlocked':False})
    m.write(blind/'lock.json',{'schema':m.SCHEMA,'state':'BLIND_PREDICTIONS_HASH_LOCKED',
             'cases':len(prompts),'index_sha256':m.sha(blind/'index.json'),
             'gold_used':False,'g3_unlocked':False})
    return tmp_path,output


def test_full_discovery_confirmation_report_without_neural_or_gold_leak(fake_experiment,monkeypatch):
    root,dest=fake_experiment
    # This proves the discovery phase never even opens confirmation gold.
    confirmation=dest/'seal'/'answers.confirmation.SECRET.jsonl'
    renamed=confirmation.with_suffix('.hidden')
    confirmation.rename(renamed)
    monkeypatch.setattr(m,'MIN_CLASS_EVENTS',10**9)
    monkeypatch.setattr(m,'MIN_CLASS_CASES',10**9)
    m.discover(root=root,output=dest)
    assert m.read(dest/'discovery'/'hypotheses.json')['rules']==[]
    renamed.rename(confirmation)
    m.confirm(root=root,output=dest)
    result=m.probe(root=root,output=dest,device='cpu')
    assert result['evaluated_inputs']==50 and result['base_graphs']==10
    assert result['g3_unlocked'] is False and result['g2_certified'] is False
    assert result['causal']['probe_cases']==0
    assert result['discovery']['cases']==30 and result['confirmation']['cases']==20
    for split in ('discovery','confirmation'):
        s=m.read(dest/split/'summary.json')
        assert sum(s['arms']['g2_0092_B'][f]['cases'] for f in m.FAMILIES)==s['cases']


def test_tampered_blind_predictions_or_hypotheses_refused(fake_experiment):
    root,dest=fake_experiment
    commit=m._check(m._roots(root),dest)
    m._verify_blind(dest,commit)
    p=next((dest/'blind'/'shards').glob('*.json.gz'))
    with p.open('ab') as f:f.write(b'corrupt')
    with pytest.raises(ValueError,match='G2_0093_BLIND_SHARD_DRIFT'):
        m._verify_blind(dest,commit)


def test_confirmation_cannot_refit_discovery_hypotheses(fake_experiment,monkeypatch):
    root,dest=fake_experiment
    monkeypatch.setattr(m,'MIN_CLASS_EVENTS',10**9)
    m.discover(root=root,output=dest)
    p=dest/'discovery'/'hypotheses.json'
    contents=m.read(p);contents['rules'].append({'signature':'forged'})
    m.write(p,contents)
    with pytest.raises(ValueError,match='G2_0093_DISCOVERY_LOCK_DRIFT'):
        m.confirm(root=root,output=dest)


def test_resume_shard_writes_do_not_overwrite_completed_records(tmp_path):
    dest=tmp_path/'case.json.gz'
    first={'case_id':'a','raw_heads':{}}
    m._atomic_gzip(dest,first)
    assert m._shard_read(dest)==first
    assert not dest.with_suffix('.tmp').exists()


def test_gate_is_not_changed_and_runner_never_promotes():
    import inspect
    src=inspect.getsource(m)
    assert 'optimizer.step(' not in src
    assert 'g3_unlocked' in src and 'g2_certified' in src
    assert m.DEFAULT_BASES==1000
    assert m.MODES==('base','renaming','role_swap','added_distractor','action_removal')


def test_real_causal_hooks_sham_controls_and_no_checkpoint_mutation(monkeypatch):
    import torch
    from tools.nanojev_gamer_v1_graph_head import GamerGraphHead
    head=GamerGraphHead(hidden_size=8,width=16,layers=1,latent_slots=2,
                        memory_slots=2,max_span_tokens=4,oracle_coupling=True)
    head.eval().requires_grad_(False)
    hidden=torch.linspace(-2,2,48).reshape(6,8)
    spans=[{'kind':'actor','token_first':0,'token_last':0},
           {'kind':'action','token_first':2,'token_last':2},
           {'kind':'quantity','token_first':4,'token_last':4}]
    prepared={'prompt':'ABCDEF','prompt_start':0,
              'offsets':[(i,i+1) for i in range(6)],
              'eligible':torch.ones(6,dtype=torch.bool)}
    monkeypatch.setattr(m.bakeoff.previous,'_capture_inference',
                        lambda *args:(prepared,hidden))
    class FakeComposite:
        def predict(self,*args,**kwargs):return {'token_spans':spans}
    graph={'nodes':[{'id':'n0','span':[0,1],'kind':'actor'},
                    {'id':'n1','span':[2,3],'kind':'action'},
                    {'id':'n2','span':[4,5],'kind':'quantity'}],
           'edges':[{'src':'n0','dst':'n1','type':'owns'}]}
    before={key:val.clone() for key,val in head.state_dict().items()}
    out=m._probe_case(head,None,None,FakeComposite(),
        {'prompt':'ABCDEF','family':'allocation'},{'graph':graph},
        {'indices':(0,1)},'cpu')
    assert out['g1_object_count']==3
    assert out['gold_edge_present']
    assert out['outcomes'][0]['intervention']=='sham_unmodified'
    assert out['outcomes'][0]['decision_changed'] is False
    assert {x['intervention'] for x in out['outcomes']} >= {
        'pair_projection_zero','encoder_source_zero','coupler_source_zero',
        'unrelated_source_zero_control'}
    assert all(torch.equal(before[key],head.state_dict()[key]) for key in before)


def test_statistical_intervals_use_base_graphs_not_fuzz_cases():
    assert m.wilson_interval(0,300)[0]==0
    assert m.wilson_interval(300,300)[1]==pytest.approx(1)
    assert m.wilson_interval(0,0)==[None,None]
    assert .4<m.wilson_interval(150,300)[0]<.5
    assert .5<m.wilson_interval(150,300)[1]<.6


def test_1000_base_cohort_is_balanced_unique_and_precommitted(monkeypatch):
    monkeypatch.setattr(m,'_known_prompts',lambda paths,root:set())
    public,secret=m.build_cohort({},'.',bases=1000)
    assert len(public)==len(secret)==5000
    assert len({r['prompt_sha256'] for r in public})==5000
    from collections import Counter
    assert Counter((r['split'],r['family']) for r in public)=={
        ('discovery','allocation'):1750,('discovery','encounter'):1750,
        ('confirmation','allocation'):750,('confirmation','encounter'):750}
