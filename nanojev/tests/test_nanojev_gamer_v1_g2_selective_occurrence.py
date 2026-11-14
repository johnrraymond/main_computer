"""G2-0036 selective occurrence model, catalog and lineage regression tests."""
from pathlib import Path
import inspect
import json
import subprocess
import sys

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_selective_occurrence as m


def _evidence(proposals=None):
    a=(1,2,'actor');b=(3,4,'action');c=(5,6,'actor')
    spans=[{'token_first':3,'token_last':3,'kind':'action'},
           {'token_first':5,'token_last':5,'kind':'actor'}]
    proposals=proposals if proposals is not None else [b,c]
    return {'baseline_spans':[{'token_first':1,'token_last':1,'kind':'actor'}],
      'baseline_anchors':[a],'catalog_anchors':{a,b,c},
      'spans':spans[:len(proposals)],'anchors':proposals,
      'features':torch.zeros(len(proposals),m.FEATURE_DIM)}


def test_fixed_train_protocol_and_sealed_dev_boundaries():
    assert m.TRAIN>=300 and m.CAL_FIT>=120 and m.CAL_AUDIT>=60
    assert m.MAX_EXTRA<=4 and max(m.BUDGETS)<=m.MAX_EXTRA
    for func in (m.proposal_features,m.choose_spans,m.score_decoders):
        src=inspect.getsource(func)
        assert 'gold_binding_edges' not in src
        assert 'positive_endpoints' not in src
        assert 'row[\'graph\']' not in src
    assert 'verify_parent35(' in inspect.getsource(m.run)
    assert 'PARENT_0035_LIVE_REPLAY_DRIFT' in inspect.getsource(m.analyze_case)
    assert not set(m.FRESH)&set(m.REPLAY)


def test_neural_catalog_features_are_label_blind_and_include_g1_decisions(monkeypatch):
    prepared={'offsets':[(i,i+1) for i in range(16)],
        'prompt_start':0,'prompt':'a'*16}
    baseline=[{'token_first':1,'token_last':1,'kind':'actor'}]
    catalog={(1,1,'actor'):0.95,(3,3,'action'):0.9,(5,5,'actor'):0.8}
    stage={'all_catalog':catalog,'filtered_catalog':{(3,3,'action'):0.9},
        'selected':{'guard':[{'token_first':3,'token_last':3,'kind':'action'}],
                    'filtered':[{'token_first':5,'token_last':5,'kind':'actor'}]}}
    monkeypatch.setattr(m.x35.g1diag,'frozen_stages',lambda *_:stage)
    e=m.proposal_features({},prepared,baseline)
    assert e['features'].shape==(2,m.FEATURE_DIM)
    assert e['catalog_anchors']=={(1,2,'actor'),(3,4,'action'),(5,6,'actor')}
    assert (1,2,'actor') not in e['anchors']
    assert e['spans'][0]['kind']=='action'
    assert e['features'][0,6].item()==1.0  # preserved G1 filter signal
    assert e['features'][0,7].item()==1.0  # guard selection signal
    assert e['features'][1,8].item()==1.0  # filtered selection signal
    with pytest.raises(ValueError,match='PARENT_ANCHOR_DRIFT'):
        m.proposal_features({},prepared,baseline*2)


def test_selector_can_abstain_and_add_without_losing_parent_anchors():
    e=_evidence()
    class Score(torch.nn.Module):
        def forward(self,x):return torch.tensor([3.,-2.])[:len(x)]
    net=Score()
    disabled=m.choose_spans(e,net,{'enabled':False})
    assert disabled==e['baseline_spans']
    added=m.choose_spans(e,net,{'enabled':True,'threshold':0.85,'max_extra':1})
    assert len(added)==2 and added[1]['kind']=='action'
    assert m.choose_spans(e,net,{'enabled':True,'threshold':0.99,'max_extra':1})==disabled
    with torch.inference_mode():
        again=m.choose_spans({**e,'features':e['features'].clone()},net,
            {'enabled':True,'threshold':0.85,'max_extra':1})
    assert added==again


def test_calibration_uses_train_only_labels_and_is_precise():
    a=(1,2,'actor');b=(3,4,'action');c=(5,6,'actor')
    # Separate gold-bearing and non-gold cases: high-score positive gets
    # admitted; low-score negative never crosses the threshold.
    data=[]
    for i in range(4):
        e=_evidence();e['features'][0,0]=2.
        row={'graph':{'nodes':[{'id':'a','span':[1,2],'kind':'actor'},
             {'id':'b','span':[3,4],'kind':'action'}],
             'edges':[{'src':'a','dst':'b','type':next(iter(m.base.BINDINGS))}]}}
        data.append((e,row))
    class Score(torch.nn.Module):
        def forward(self,x):return (x[:,0]-1.)*5.
    stats=m.calibration_metrics(data,Score(),{'enabled':True,'threshold':.85,'max_extra':1})
    assert stats['true_new_endpoints']==4 and stats['false_new_endpoints']==0
    assert stats['newly_reachable_gold_edges']==4
    assert stats['admission_precision']==1.
    disabled=m.calibration_metrics(data,Score(),{'enabled':False})
    assert disabled['added_endpoints']==0


def test_training_produces_freezable_weights_and_does_not_mutate_inputs():
    torch.set_num_threads(1)
    features=torch.randn(40,m.FEATURE_DIM)
    labels=(features[:,0]>0).float()
    before=features.clone()
    nets,log=m.train_acceptors([({'features':features},labels)],steps=8)
    assert set(nets)==set(m.SEEDS)
    assert all(not any(p.requires_grad for p in net.parameters())
               for net in nets.values())
    assert all(log[str(seed)]['optimizer_steps']==8 for seed in m.SEEDS)
    assert torch.equal(features,before)
    assert all(torch.isfinite(net(features)).all() for net in nets.values())
    with pytest.raises(ValueError,match='ONE_CLASS_TRAIN_CATALOG'):
        m.train_acceptors([({'features':features},torch.zeros(len(labels)))],steps=1)


def test_parent35_refuses_bad_contract(tmp_path):
    (tmp_path/m.x35.REPORT).write_text(json.dumps({'schema':'wrong'}))
    with pytest.raises(ValueError,match='PARENT_0035_CONTRACT:schema'):
        m.verify_parent35(tmp_path,ancestors={},train_sha='',old_dev={},fresh35={})


def test_command_help_is_registered_without_loading_backbone():
    output=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-selective-occurrence','--help'],capture_output=True,text=True)
    assert output.returncode==0,output.stderr
    assert '--coverage35-run' in output.stdout
    assert '--device' in output.stdout


def test_coverage_aggregate_and_gate_no_promotion():
    row={'coverage':{'gold_endpoint_count':4,'catalog_gold_endpoint_count':3,
      'baseline_gold_endpoint_count':2,'unrecoverable_gold_endpoints':1,
      'actionable_catalog_gold_endpoint_count':3,
      'lost_gold_endpoints_to_catalog_cap':0,
      'catalog_candidate_count':18,
      'baseline':{'selected_anchors':3,'new_gold_anchors':0,'new_false_anchors':0,
                  'gold_edges_reachable_by_endpoints':1,'represented_gold_edges':1,
                  'fully_representable':False}}}
    for seed in m.SEEDS:
        row['coverage'][str(seed)]={'selected_anchors':4,'new_gold_anchors':1,
          'new_false_anchors':0,'gold_edges_reachable_by_endpoints':2,
          'represented_gold_edges':2,'fully_representable':True}
    summary=m.summarize_coverage([row])
    assert summary['unrecoverable_gold_endpoints']==1
    assert summary['variants']['baseline']['represented_gold_edges']==1
    assert summary['variants'][str(m.SEEDS[0])]['represented_gold_edges']==2


def test_analyze_case_runs_inference_before_gold_and_checks_frozen_replay(monkeypatch):
    rel=next(iter(m.base.BINDINGS))
    a=(1,2,'actor');b=(3,4,'action');edge=(a,b,rel)
    row={'prompt':'example','family':'encounter','structural_topology':'native',
        'graph':{'nodes':[{'id':'a','kind':'actor','span':[1,2]},
                          {'id':'b','kind':'action','span':[3,4]}],
                 'edges':[{'src':'a','dst':'b','type':rel}]}}
    e=_evidence([b]);e['spans']=e['spans'][:1];e['catalog_anchors']={a,b}
    records={'inference_calls':0}
    def frozen(row,spans,*args):
        return {'anchors':[a] if len(spans)==1 else [a,b], 'gold':set(),
                'prompt_sha256':m.base._sha(row['prompt'])}
    def score(case,row,*args,**kwargs):
        assert case['gold']==set(), 'gold leaked into frozen scoring'
        records['inference_calls']+=1
        item={'edges':[edge],'groups':np.array([0]),'gold':set(),
              'gold_total':0,'candidate_gold':0,'candidate_count':1,
              'raw':{f'occurrence_graph_s{s}':np.array([2.]) for s in m.SEEDS}}
        decisions={f'{mode}_s{seed}':{edge} for mode in ('group','structured')
                   for seed in m.SEEDS}
        ranks={k:np.array([2.]) for k in decisions}
        return decisions,ranks,item
    monkeypatch.setattr(m,'frozen_g2_case',frozen)
    monkeypatch.setattr(m,'score_decoders',score)
    class Score(torch.nn.Module):
        def forward(self,x):return torch.ones(len(x))*10
    nets={seed:Score() for seed in m.SEEDS}
    pol={f'acceptor_s{seed}':{'enabled':True,'threshold':0.85,'max_extra':1}
         for seed in m.SEEDS}
    fake_state={'head':object()}
    result=m.analyze_case(row,e,fake_state,{}, {},{}, {},{}, {},nets,pol,
        prepared={},hidden=torch.zeros(4,8),eligible=None,split='replay',index=1)
    assert records['inference_calls']==4
    assert result['coverage']['catalog_gold_endpoint_count']==2
    assert result['coverage']['baseline']['new_gold_anchors']==0
    assert result['coverage'][str(m.SEEDS[0])]['new_gold_anchors']==1
    assert result['metrics']['baseline__group_s460331']['correct']==1
    parent={'group':'replay','index':1,'prompt_sha256':m.base._sha(row['prompt']),
            'family':'encounter','gold_total':1,
            'metrics':{k:v for k,v in result['metrics'].items()
                       if k.startswith('baseline__')}}
    m.analyze_case(row,e,fake_state,{}, {},{}, {},{}, {},nets,pol,
        prepared={},hidden=torch.zeros(4,8),eligible=None,split='replay',index=1,
        parent35=parent)
    parent['metrics']['baseline__group_s460331']['correct']=0
    with pytest.raises(ValueError,match='PARENT_0035_LIVE_REPLAY_DRIFT'):
        m.analyze_case(row,e,fake_state,{}, {},{}, {},{}, {},nets,pol,
            prepared={},hidden=torch.zeros(4,8),eligible=None,split='replay',index=1,
            parent35=parent)


def test_mocked_bounded_run_writes_checkpoint_policies_and_ledger(monkeypatch,tmp_path):
    row={'prompt':'fresh synthetic prompt','family':'encounter',
        'structural_topology':'native','split':'dev',
        'graph':{'nodes':[],'edges':[]}}
    state={'ancestors':{},'training':[], 'history29':[], 'fresh29':{},
           'head':object(),'head_before':object(), 'model':torch.nn.Identity(),
           'reference':torch.nn.Identity(),'reference_before':{},
           'tokenizer':object(),'models29':{}}
    pol={}
    for s in m.SEEDS:
        pol[f'occurrence_graph_s{s}']={}
    monkeypatch.setattr(m.p30,'_prepare',lambda **kwargs:state)
    monkeypatch.setattr(m.p30,'calibration_and_dev',lambda *args:([],{}))
    monkeypatch.setattr(m.x31,'verify_parent30',lambda *a,**k:(None,
        {'calibrated_reference':{},m.x31.CHAMPION:{}},None,{}))
    monkeypatch.setattr(m.x31,'fresh_cohorts',lambda *a:([],{}))
    monkeypatch.setattr(m.x32,'verify_parent31',lambda *a,**k:(None,
        {'calibrated_reference':{},**{name:{} for name in m.x32.SOURCE_NAMES}},None,{},{}))
    monkeypatch.setattr(m.x32,'fresh_cohorts',lambda *a:(([],[],[]),{}))
    monkeypatch.setattr(m.x33,'verify_parent32',lambda *a,**k:(None,{},None,{},{}))
    monkeypatch.setattr(m.x33,'fresh_cohorts',lambda *a:(([],[],[]),{}))
    monkeypatch.setattr(m.x34,'verify_parent33',lambda *a,**k:(None,{},None,{},{}))
    monkeypatch.setattr(m.x34,'fresh_cohorts',lambda *a:{k:[row] for k in m.x35.REPLAY})
    monkeypatch.setattr(m.x35,'verify_parent34',lambda *a,**k:(None,{}))
    monkeypatch.setattr(m.x35,'fresh_cohorts',lambda *a:{k:[row] for k in m.REPLAY})
    monkeypatch.setattr(m,'verify_parent35',lambda *a,**k:({k:[{}] for k in m.REPLAY},{}))
    monkeypatch.setattr(m,'fresh_cohorts',lambda *a:(([row],[row],[row]),
         {k:[row] for k in m.FRESH}))
    monkeypatch.setattr(m.x32,'load_parent_models',lambda *a,**k:{})
    monkeypatch.setattr(m.x33,'load_parent32_models',lambda *a,**k:{})
    monkeypatch.setattr(m.x34,'load_parent33_models',lambda *a,**k:{})
    e=_evidence()
    monkeypatch.setattr(m,'freeze_case',lambda *a,**k:(e,({},torch.zeros(2,8),None)))
    monkeypatch.setattr(m,'label_train_case',lambda e,r:torch.tensor([1.,0.]))
    class TinyNet(torch.nn.Module):
        def __init__(self):
            super().__init__();self.scale=torch.nn.Parameter(torch.tensor([1.]))
        def forward(self,x):return x[:,0]*self.scale
    monkeypatch.setattr(m,'train_acceptors',lambda *a,**k:({s:TinyNet().eval().requires_grad_(False)
        for s in m.SEEDS},{str(s):{'optimizer_steps':m.TRAIN_STEPS} for s in m.SEEDS}))
    monkeypatch.setattr(m,'fit_policies',lambda *a:({f'acceptor_s{s}':
        {'enabled':False,'threshold':0.97,'max_extra':0} for s in m.SEEDS},{}))
    def analyze(*args,**kwargs):
        metrics={f'{arm}__{mode}_s{seed}':{'correct':0,'false_positive':0,'missing':0,
                'exact_cases':1,'competing_gold':0,'ranking_failures':0}
             for arm in ('baseline','selective') for mode in ('group','structured')
             for seed in m.SEEDS}
        cov={'gold_endpoint_count':0,'catalog_gold_endpoint_count':0,
            'baseline_gold_endpoint_count':0,'unrecoverable_gold_endpoints':0,
            'actionable_catalog_gold_endpoint_count':0,
            'lost_gold_endpoints_to_catalog_cap':0,
            'catalog_candidate_count':2}
        for arm in ('baseline',*[str(s) for s in m.SEEDS]):
            cov[arm]={'selected_anchors':1,'new_gold_anchors':0,'new_false_anchors':0,
                'gold_edges_reachable_by_endpoints':0,'represented_gold_edges':0,
                'fully_representable':True}
        return {'group':kwargs['split'],'index':kwargs['index'],
            'prompt_sha256':m.base._sha(row['prompt']),'family':row['family'],
            'gold_total':0,'candidate_gold':0,'metrics':metrics,'coverage':cov}
    monkeypatch.setattr(m,'analyze_case',analyze)
    monkeypatch.setattr(m.x31.rel,'validate_original_state',lambda *a:None)
    path=tmp_path/'run'
    report=m.run(precision30_run='p30',representation31_run='p31',completeness32_run='p32',
        structured33_run='p33',diagnosis34_run='p34',coverage35_run='p35',
        output=str(path),device='cpu')
    assert report['evaluation']['new_0036_dev']['cases']==2
    assert report['train_cases']==1 and report['train_fit_cases']==1
    assert report['new_optimizer_steps']==m.TRAIN_STEPS*len(m.SEEDS)
    assert report['production_promotion'] is False and report['sealed_test_used'] is False
    for filename in (m.REPORT,m.LEDGER,m.WEIGHTS,m.POLICIES):
        assert (path/filename).is_file()
    assert report['weights_sha256']==m.backbone.sha256(path/m.WEIGHTS)
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(precision30_run='p30',representation31_run='p31',completeness32_run='p32',
            structured33_run='p33',diagnosis34_run='p34',coverage35_run='p35',
            output=str(path),device='cpu')


def test_real_frozen_g2_group_and_structured_paths_see_no_gold(monkeypatch):
    """Execute actual G2-0032/0033 frozen decoders, not mocked selections."""
    a=(0,1,'actor');b=(2,3,'actor');c=(4,5,'actor')
    rel=next(iter(m.base.BINDINGS))
    edges=[(a,b,rel),(a,c,rel)]
    source_name=m.x31.REFERENCE
    reference=np.array([5.,-5.],dtype=np.float32)
    raw={source_name:reference.copy(),
         **{f'occurrence_graph_s{s}':reference.copy() for s in m.SEEDS}}
    item={'edges':edges,'groups':np.array([0,0],dtype=np.int32),
          'raw':raw,'gold':set(),'gold_total':0,
          'candidate_gold':0,'candidate_count':2}
    pol={f'occurrence_graph_s{s}':dict(source=f'occurrence_graph_s{s}',
        alpha=1.,threshold=.93,margin=2.,reference_gate=None)
        for s in m.SEEDS}
    recovery={f'group_recovery_s{s}':dict(source=f'occurrence_graph_s{s}',
        enabled=False,mode='disabled',edge_prob=1.,confidence=1.,
        group_gap=0.,max_additions=0,offset=0.) for s in m.SEEDS}
    structured={f'structured_set_s{s}':dict(source=f'occurrence_graph_s{s}',
        enabled=True,group_weight=0.,global_weight=0.,fallback=f'group_recovery_s{s}')
        for s in m.SEEDS}
    nets32={s:(m.x32.GroupCardinality().eval().requires_grad_(False),
               m.x32.GraphCardinality().eval().requires_grad_(False))
            for s in m.SEEDS}
    nets33={s:m.x33.GraphSetEnergy().eval().requires_grad_(False) for s in m.SEEDS}
    def score_stub(*args,**kwargs):
        assert args[0].get('without_gold')
        return item
    monkeypatch.setattr(m.x31,'example_from_case',lambda *args,**kwargs: {'without_gold':True})
    monkeypatch.setattr(m.x31,'score_case',score_stub)
    case={'anchors':[a,b,c],'gold':set()}
    decisions,ranks,scored=m.score_decoders(case,{'prompt':'synthetic'},
        {},{},pol,recovery,nets32,nets33,structured,device='cpu')
    assert scored['gold']==set()
    assert set(decisions)=={f'{mode}_s{s}' for mode in ('group','structured')
                            for s in m.SEEDS}
    assert all(chosen<=set(edges) for chosen in decisions.values())
    assert all(len(v)==len(edges) for v in ranks.values())
