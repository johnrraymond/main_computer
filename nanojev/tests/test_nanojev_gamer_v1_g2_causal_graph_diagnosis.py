"""G2-0034 frozen-model causal localization and counterfactual integrity."""
from copy import deepcopy
from pathlib import Path
import gzip
import json
import subprocess
import sys

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_causal_graph_diagnosis as d


def fixture():
    a=('a', 'b', 'role');b=('a', 'c', 'role')
    c=('a', 'd', 'role');e=('b', 'a', 'role')
    f=('b', 'c', 'role');g=('b', 'd', 'role')
    edges=[a,b,c,e,f,g]
    raw={d.x31.REFERENCE:np.array([2.2,2.1,1.4,3.0,1.4,.3],dtype=np.float32),
         d.x31.CHAMPION:np.array([2.2,2.1,1.4,3.0,1.4,.3],dtype=np.float32)}
    for seed in d.SEEDS:
        raw[f'occurrence_graph_s{seed}']=np.array([3.9,3.3,2.1,-.5,3.5,2.1],dtype=np.float32)
    item={'edges':edges,'raw':raw,'groups':np.array([0,0,0,1,1,1]),
          'gold':{a,b,e},'gold_total':3,'candidate_gold':3,'candidate_count':len(edges)}
    policy={'source':f'occurrence_graph_s{d.SEEDS[0]}','alpha':1.,
            'threshold':.93,'margin':2.,'reference_gate':None}
    return item,policy


def test_candidate_ceiling_separates_missing_endpoint_and_inventory_exclusion():
    item,_=fixture()
    nodes={v for edge in item['edges'] for v in edge[:2]}
    missing_endpoint=('z','a','role')
    inventory_loss=('a','d','extra')
    item['gold']={item['edges'][0],missing_endpoint,inventory_loss}
    actual=d.candidate_ceiling(item,nodes)
    assert actual['missing_g1_endpoint']==1
    assert actual['endpoints_present_but_not_in_inventory']==1
    assert actual['candidate_complete'] is False
    assert actual['fully_representable'] is False
    assert actual['represented_gold']==1
    assert actual['candidate_oracle']=={item['edges'][0]}
    assert d.p30.measure(actual['candidate_oracle'],item)['missing']==2


def test_ranking_oracles_use_only_declared_gold_counts():
    item,_=fixture()
    scores=np.array([9.,7.,-1.,6.,-2.,-3.])
    oracle=d.score_rank_oracles(item,scores)
    assert oracle['global_gold_candidate_count']==item['gold']
    assert oracle['group_gold_candidate_counts']==item['gold']
    # Perfect graph is available but the scorer puts a false candidate ahead
    # of a true one WITHIN the same group; correct group count cannot fix it.
    bad=np.array([9.,2.,8.,6.,-2.,-3.])
    shifted=d.score_rank_oracles(item,bad)
    assert shifted['group_gold_candidate_counts']!=item['gold']
    assert item['edges'][2] in shifted['group_gold_candidate_counts']
    # The ranking counterfactual is gold-dependent, not a deployable decoder.
    altered=deepcopy(item);altered['gold']={item['edges'][0],item['edges'][1],item['edges'][2],item['edges'][3]}
    assert d.score_rank_oracles(altered,bad)['group_gold_candidate_counts']!=shifted['group_gold_candidate_counts']
    with pytest.raises(ValueError,match='RANK_ORACLE_INPUT'):
        d.score_rank_oracles(item,np.array([float('nan')]*6))


def test_fixed_decoder_ablations_use_identical_scores_and_no_gold():
    item,policy=fixture()
    f=d.x33.set_features(item,policy['source'],policy,4)
    logits=np.array([5.,4.,-3.,-4.,4.5,-3.])
    logcount=np.full((2,d.x33.COUNT_CLASSES),-100.)
    logcount[:,1]=0.
    pred=(logits,logcount,1.)
    variants=d.fixed_ablations(item,f,pred,policy['source'])
    assert set(variants)=={a[0] for a in d.ABLATIONS}
    assert len(variants['edge_only'])==3
    assert len(variants['group_only'])==2
    altered=deepcopy(item);altered['gold']=set(altered['edges'])
    assert d.fixed_ablations(altered,f,pred,policy['source'])==variants
    assert d.ABLATIONS==(('edge_only',0.,0.),('group_only',.7,0.),
                         ('global_only',0.,.30),('joint',.7,.30))


def test_attribution_is_exclusive_and_representability_conditional():
    item,_=fixture()
    cd=d.candidate_ceiling(item,{v for e in item['edges'] for v in e[:2]})
    cd.pop('candidate_oracle')
    case={'candidate_diagnosis':cd,'causal_diagnosis':{
        f'structured_set_s{s}':{'attribution':'decoder_selection_limit',
        'selected_exact':0,'oracle_group_exact':1,'oracle_global_exact':0,
        'parent_rank_group_exact':1,'would_repair_with_gold_group_counts':True}
        for s in d.SEEDS}}
    summary=d.summarize_causality([case])
    assert summary['fully_representable']==1
    for stats in summary['models'].values():
        assert stats['attribution']=={'decoder_selection_limit':1}
        assert stats['correctable_decoder_failures']==1
        assert stats['gold_group_counts_oracle_exact_on_fully_representable']==1


def test_candidate_inventory_limit_does_not_masquerade_as_decoder_failure(monkeypatch):
    item,_=fixture();item['gold'].add(('x','y','role'))
    cd=d.candidate_ceiling(item,set())
    assert cd['missing_g1_endpoint']==1 # candidate gold edges already have endpoints
    assert not cd['fully_representable']
    monkeypatch.setattr(d.source13,'MAX_EDGES',1)
    gold=d.candidate_ceiling(item,{v for e in item['edges'] for v in e[:2]})
    assert gold['output_budget_sufficient'] is False
    assert len(gold['candidate_oracle'])==1


def test_corrupt_parent_schema_refused_early(tmp_path):
    (tmp_path/d.x33.REPORT).write_text(json.dumps({'schema':'not-the-parent'}))
    (tmp_path/d.x33.POLICIES).write_text('{}')
    with pytest.raises(ValueError,match='PARENT_0033_CONTRACT:schema'):
        d.verify_parent33(tmp_path,train33=[],fit33=[],audit33=[],
            old_dev={},new_dev={},ancestors={},training_sha='unused')


def test_real_source_has_no_training_and_cli_parse():
    import inspect
    src=inspect.getsource(d.run)
    assert 'fit_models(' not in src
    assert 'fit_policies(' not in src
    assert 'torch.optim.' not in src
    assert 'verify_parent33(' in src
    assert 'PARENT_0033_LIVE_REPLAY_DRIFT' in src
    assert 'dev_checkpoint_selection' in src
    cli=Path(__file__).resolve().parents[1]/'tools'/'nanojev_gamer_v1_graph_curriculum.py'
    text=cli.read_text(encoding='utf-8')
    assert "sub.add_parser('frob-g2-causal-graph-diagnosis'" in text
    assert "from tools.nanojev_gamer_v1_g2_causal_graph_diagnosis import run" in text
    assert "'structured33_run':'graph-g2-structured-graph-decoder-0033'" in text


def test_end_to_end_cpu_mock_keeps_immutable_previous_metrics(monkeypatch,tmp_path):
    item,template=fixture()
    anchors={v for edge in item['edges'] for v in edge[:2]}
    anchors=sorted(anchors)
    row={'prompt':'unseen prompt','family':'encounter','split':'dev',
         'structural_topology':'native','graph':{'nodes':[],'edges':[]}}
    pol30={'calibrated_reference':{**template,'source':d.x31.REFERENCE,'alpha':0.},
           d.x31.CHAMPION:{**template,'source':d.x31.REFERENCE,'alpha':0.}}
    pol31={'calibrated_reference':{**template,'source':d.x31.REFERENCE,'alpha':0.}}
    for seed in d.SEEDS:
        pol31[f'occurrence_graph_s{seed}']={**template,'source':f'occurrence_graph_s{seed}'}
    pol32={f'{arm}_s{seed}':{'source':f'occurrence_graph_s{seed}',
             'enabled':False,'mode':arm} for arm in d.x32.ARMS for seed in d.SEEDS}
    pol33={f'structured_set_s{seed}':{'source':f'occurrence_graph_s{seed}',
              'enabled':False,'fallback':f'group_recovery_s{seed}',
              'group_weight':0.,'global_weight':0.} for seed in d.SEEDS}
    # Exercise real G2-0033 enabled and disabled paths, matching the actual
    # uploaded policy mixture (460331 fallback; other two active).
    pol33[f'structured_set_s{d.SEEDS[1]}']['enabled']=True
    pol33[f'structured_set_s{d.SEEDS[2]}']['enabled']=True
    pol33[f'structured_set_s{d.SEEDS[2]}']['group_weight']=.7
    nets33={s:d.x33.GraphSetEnergy().eval().requires_grad_(False) for s in d.SEEDS}
    nets32={s:(d.x32.GroupCardinality().eval().requires_grad_(False),
               d.x32.GraphCardinality().eval().requires_grad_(False)) for s in d.SEEDS}
    models31={s:{} for s in d.SEEDS}
    old=d.x33.evaluate_case(deepcopy(item),len(anchors),row,d.x33.NEW[0],1,
        {'calibrated_reference_0030':pol30['calibrated_reference'],
         'calibrated_reference_0031':pol31['calibrated_reference'],
         'predicate_argument_0030':pol30[d.x31.CHAMPION],
         **{f'occurrence_graph_s{s}':pol31[f'occurrence_graph_s{s}'] for s in d.SEEDS}},
        pol32,nets32,nets33,pol33)
    old_by_split={split:[{**old,'group':split}] for split in d.x33.NEW}
    dev33_rows={split:[row] for split in d.x33.NEW}
    state={'ancestors':{},'training':[],'history29':[],'fresh29':{},
           'head':object(),'model':torch.nn.Identity(), 'tokenizer':object(),
           'reference':torch.nn.Identity(),'reference_before':{},
           'head_before':object(),'models29':{}}
    monkeypatch.setattr(d.p30,'_prepare',lambda **kw:state)
    monkeypatch.setattr(d.p30,'calibration_and_dev',lambda *args:([],{}))
    monkeypatch.setattr(d.x31,'verify_parent30',lambda *args,**kwargs:(None,pol30,{},{}))
    monkeypatch.setattr(d.x31,'fresh_cohorts',lambda *args:([],{}))
    monkeypatch.setattr(d.x32,'verify_parent31',lambda *args,**kw:(None,pol31,{},None,{}))
    monkeypatch.setattr(d.x32,'fresh_cohorts',lambda *args:(([],[],[]),{}))
    monkeypatch.setattr(d.x33,'verify_parent32',lambda *args,**kw:(None,pol32,{},None,{}))
    monkeypatch.setattr(d.x33,'fresh_cohorts',lambda *args:(([],[],[]),dev33_rows))
    monkeypatch.setattr(d,'verify_parent33',lambda *args,**kw:(None,pol33,old_by_split,None,{}))
    monkeypatch.setattr(d.x32,'load_parent_models',lambda *args,**kw:models31)
    monkeypatch.setattr(d.x33,'load_parent32_models',lambda *args,**kw:nets32)
    monkeypatch.setattr(d,'load_parent33_models',lambda *args,**kw:nets33)
    monkeypatch.setattr(d,'fresh_cohorts',lambda *args:{split:[row] for split in d.NEW})
    monkeypatch.setattr(d.x31,'capture_occurrences',lambda *args,**kw:[{'anchors':anchors}])
    monkeypatch.setattr(d.x31,'example_from_case',lambda *args,**kw:None)
    monkeypatch.setattr(d.x31,'score_case',lambda *args,**kw:deepcopy(item))
    monkeypatch.setattr(d.x31.rel,'validate_original_state',lambda *args,**kw:None)
    monkeypatch.setattr(d.base,'gold_binding_edges',lambda row:set(item['gold']))
    out=tmp_path/'new_report'
    result=d.run(precision30_run='p30',representation31_run='p31',
                 completeness32_run='p32',structured33_run='p33',output=str(out),
                 device='cpu')
    assert result['replay_passed']
    assert result['new_optimizer_steps']==0
    assert result['production_promotion'] is False
    assert (out/d.REPORT).is_file() and (out/d.LEDGER).is_file()
    with gzip.open(out/d.LEDGER,'rt') as f: records=[json.loads(line) for line in f]
    assert len(records)==4
    assert result['causal_by_cohort']['new_0034_dev']['cases']==2
    assert len(records[0]['metrics'])>len(old['metrics'])
    assert all('causal_diagnosis' in rec for rec in records)
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        d.run(precision30_run='p30',representation31_run='p31',
              completeness32_run='p32',structured33_run='p33',output=str(out),
              device='cpu')
