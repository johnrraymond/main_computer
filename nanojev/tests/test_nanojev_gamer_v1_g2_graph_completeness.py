"""G2-0032: learned completeness, immutable lineage, no-gold inference gates."""
import builtins
import inspect
import json
import symtable
from copy import deepcopy

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_graph_completeness as x


def case():
    edges=[('a','b','owns'),('a','c','owns'),('a','d','owns'),
           ('a','e','owns'),('b','a','owns'),('b','d','owns')]
    raw={x.x31.REFERENCE:np.array([3.3,2.6,1.4,.5,3.1,2.4],dtype='float32')}
    for seed in x.SEEDS:
        raw[f'occurrence_graph_s{seed}']=np.array([3.9,3.3,2.1,-.5,3.5,2.1],dtype='float32')
    item={'raw':raw,'groups':np.array([0,0,0,0,1,1],dtype='int32'),
          'edges':edges,'gold':{edges[0],edges[1],edges[4]},
          'gold_total':3,'candidate_gold':3,'candidate_count':6}
    policy={'source':'occurrence_graph_s460331','alpha':1.,
            'threshold':.93,'margin':2.,'reference_gate':None}
    item['parent_policies']={f'occurrence_graph_s{s}':{
        **policy,'source':f'occurrence_graph_s{s}'} for s in x.SEEDS}
    return item,policy


def test_cardinality_features_are_fixed_shape_and_not_gold_dependent():
    item,policy=case()
    a=x.candidate_features(item,policy['source'],policy,4)
    assert a[0].shape==(2,20)
    assert a[3]==[2,1]
    assert x.graph_features(a[0],a[3],a[2],a[5]).shape==(24,)
    altered=deepcopy(item);altered['gold']=set()
    b=x.candidate_features(altered,policy['source'],policy,4)
    assert np.array_equal(a[0],b[0])
    assert a[4]==b[4]
    assert x.supervised_case(item,policy['source'],policy,4)['targets'].tolist()==[2,1]
    assert x.supervised_case(altered,policy['source'],policy,4)['targets'].tolist()==[0,0]


def test_completeness_heads_learn_on_train_targets_and_no_gold_at_inference():
    item,policy=case()
    examples=[x.supervised_case(item,policy['source'],policy,4) for _ in range(8)]
    nets,log=x.fit_heads(examples,seeds=(460331,),steps=3)
    assert set(nets)=={460331}
    assert all(not p.requires_grad for net in nets[460331] for p in net.parameters())
    before=x.probabilities(item,policy['source'],policy,4,nets[460331])
    item['gold']=set()
    after=x.probabilities(item,policy['source'],policy,4,nets[460331])
    assert np.allclose(before[-2],after[-2])
    assert before[-1]==after[-1]
    assert set(log['460331'])=={'group','graph'}


def test_selection_preserves_parent_and_allows_multiple_destinations():
    item,policy=case()
    initial=x.p30.select_graph(item,**policy)
    nets=(x.GroupCardinality(),x.GraphCardinality())
    class AlwaysTwo(torch.nn.Module):
        def forward(self,feats):
            logits=torch.full((len(feats),x.COUNT_CLASSES),-20.)
            logits[:,3]=20.
            return logits
    class AlwaysTen(torch.nn.Module):
        def forward(self,feats):return torch.ones(len(feats))*np.log1p(10.)
    nets=(AlwaysTwo(),AlwaysTen())
    recovery={'enabled':True,'mode':'joint_recovery','edge_prob':.7,
              'confidence':.55,'group_gap':4.,'max_additions':3,'offset':0.}
    result=x.select_complete(item,policy['source'],policy,4,nets,recovery)
    assert initial.issubset(result)
    assert len(result)>len(initial)
    assert ('a','c','owns') in result and ('a','b','owns') in result
    altered=deepcopy(item);altered['gold']=set(item['edges'])
    assert x.select_complete(altered,policy['source'],policy,4,nets,recovery)==result
    recovery['enabled']=False
    assert x.select_complete(item,policy['source'],policy,4,nets,recovery)==initial


def test_calibration_never_chooses_f1_regression(monkeypatch):
    # Pretrained model votes to add wrong edges; a TRAIN-only F1 safeguard
    # must leave the parent selection unchanged.
    item,policy=case()
    wrong=deepcopy(item)
    wrong['gold']=x.p30.select_graph(wrong,**policy)
    wrong['gold_total']=len(wrong['gold'])
    for seed in x.SEEDS:
        wrong['raw'][f'occurrence_graph_s{seed}']=wrong['raw']['occurrence_graph_s460331'].copy()
    class Group(torch.nn.Module):
        def forward(self,feats):
            logits=torch.full((len(feats),x.COUNT_CLASSES),-20.)
            logits[:,6]=20.
            return logits
    class Graph(torch.nn.Module):
        def forward(self,feats):return torch.ones(len(feats))*np.log1p(100.)
    models={seed:(Group(),Graph()) for seed in x.SEEDS}
    policies,metrics=x.fit_policies([(wrong,4)]*4,models)
    assert set(policies)=={f'{arm}_s{s}' for arm in x.ARMS for s in x.SEEDS}
    assert all(not p['enabled'] for p in policies.values())
    assert all(s['parent_training_exact_cases']==4 for s in metrics.values())


def test_acceptance_requires_exact_f1_ood_and_all_seeds():
    base={'f1':.57,'exact_graph_rate':.058,'ranking_failures':6200}
    parent={'f1':.66,'exact_graph_rate':.016,'ranking_failures':4200}
    values={n:dict(base) for n in ('identity_peer_0026','calibrated_reference_0030',
                                     'calibrated_reference_0031','predicate_argument_0030')}
    values.update({n:dict(parent) for n in x.SOURCE_NAMES})
    for arm in x.ARMS:
        for seed in x.SEEDS:
            values[f'{arm}_s{seed}']={'f1':.658,'exact_graph_rate':.06,
                                      'ranking_failures':4200}
    result=x.gates({'models':values},{'models':values})
    assert result['development_success'] is True
    assert not result['production_promotion']
    bad=deepcopy(values)
    bad['graph_recovery_s460331']['exact_graph_rate']=.03
    assert x.gates({'models':bad},{'models':bad})['arms']['graph_recovery']['all_seeds_pass'] is False
    bad=deepcopy(values)
    bad['group_recovery_s460332']['f1']=.63
    assert x.gates({'models':bad},{'models':bad})['arms']['group_recovery']['all_seeds_pass'] is False


def test_fresh_generation_has_no_previous_prompt_or_topology_leakage():
    state={'training':[],'history29':[],'fresh29':{}}
    train,fresh=x.fresh_cohorts(state,[],{},[],{})
    assert tuple(map(len,train))==(240,120,60)
    assert tuple(map(len,fresh.values()))==(320,128)
    allrows=[*(r for group in train for r in group),
             *(r for group in fresh.values() for r in group)]
    assert len({x.base._sha(r['prompt']) for r in allrows})==len(allrows)
    assert all(r['split']=='train' for group in train for r in group)
    assert {r['structural_topology'] for r in fresh[x.NEW[1]]}=={'2x4','4x2','4x4'}


def test_saved_g2_0031_weights_load_strictly_if_supplied():
    from pathlib import Path
    p=Path('/mnt/data/g2_representation_bakeoff_development.safetensors')
    if not p.exists():pytest.skip('Uploaded checkpoint not available in this runtime')
    tensors=x.load_file(str(p),device='cpu')
    models=x.load_parent_models(tensors)
    assert len(models)==3 and all(len(m)==3 for m in models.values())
    tampered=dict(tensors);del tampered[next(iter(tampered))]
    with pytest.raises(ValueError,match='PARENT_0031_WEIGHT_SHAPE'):
        x.load_parent_models(tampered)


def test_cli_and_global_names():
    import subprocess,sys
    cmd=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-graph-completeness','--help'],check=True,capture_output=True,text=True)
    assert '--representation31-run' in cmd.stdout
    for fn in (x.run,x.verify_parent31,x.fresh_cohorts,x.fit_policies,
               x.evaluate_case,x.candidate_features):
        tree=symtable.symtable(inspect.getsource(fn),'<test>','exec')
        scope=next(t for t in tree.get_children() if t.get_name()==fn.__name__)
        missing=[v.get_name() for v in scope.get_symbols() if v.is_global() and
                 not hasattr(x,v.get_name()) and not hasattr(builtins,v.get_name())]
        assert not missing,missing


def test_impossible_parent_state_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        x.verify_parent31(tmp_path,old_cal=[],old_dev={},cal31=[],dev31={},
                          ancestors={},training_sha='')
    (tmp_path/x.x31.REPORT).write_text(json.dumps({'schema':'bad'}))
    for name in (x.x31.POLICIES,x.x31.LEDGER,x.x31.WEIGHTS):
        (tmp_path/name).write_text('{}')
    with pytest.raises(ValueError,match='PARENT_0031_CONTRACT'):
        x.verify_parent31(tmp_path,old_cal=[],old_dev={},cal31=[],dev31={},
                          ancestors={},training_sha='')


def test_bounded_full_run_report_and_no_dev_policy_search(tmp_path,monkeypatch):
    """Exercise all file writes and frozen parent controls without CUDA."""
    class Frozen(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight=torch.nn.Parameter(torch.tensor([.5]),requires_grad=False)
    def rows(name,n,split='train'):
        return [{'prompt':f'{name} unique {i}', 'split':split,
                 'family':'allocation','structural_topology':'native'} for i in range(n)]
    head=Frozen();reference=Frozen();back=Frozen()
    train=rows('train',8)
    cal31=rows('cal31',2)
    dev31={name:rows(name,1,'dev') for name in x.REPLAY}
    dev32={name:rows(name,1,'dev') for name in x.NEW}
    state={'training':train,'history29':[],'fresh29':{},
           'head':head,'head_before':deepcopy(head.state_dict()),
           'reference':reference,'reference_before':deepcopy(reference.state_dict()),
           'model':back,'tokenizer':None,'models29':{},'allowed':None,
           'ancestors':{}}
    monkeypatch.setattr(x.p30,'_prepare',lambda **kw:state)
    monkeypatch.setattr(x.p30,'calibration_and_dev',lambda *args:([],dev31))
    parent_reference={'source':x.x31.REFERENCE,'alpha':0.,'threshold':.75,
                      'margin':None,'reference_gate':None}
    parent_champion={'source':x.x31.CHAMPION,'alpha':1.,'threshold':.75,
                     'margin':None,'reference_gate':None}
    policy31={f'occurrence_graph_s{s}':{'source':f'occurrence_graph_s{s}',
              'alpha':1.,'threshold':.93,'margin':2.,'reference_gate':None}
              for s in x.SEEDS}
    policy31['calibrated_reference']=parent_reference
    monkeypatch.setattr(x.x31,'verify_parent30',lambda *a,**k:({},
        {'calibrated_reference':parent_reference,x.x31.CHAMPION:parent_champion},
        {},{'hash30':'stable'}))
    monkeypatch.setattr(x.x31,'fresh_cohorts',lambda *a:(cal31,dev31))
    monkeypatch.setattr(x,'verify_parent31',lambda *a,**k:({},policy31,{},
                            {},{'hash31':'stable'}))
    monkeypatch.setattr(x,'load_parent_models',lambda *a,**k:{s:{} for s in x.SEEDS})
    # Preserve the real fresh_cohorts() contract: ((train, fit, audit), fresh).
    # A flattened mock previously concealed the caller's unpacking bug.
    monkeypatch.setattr(x,'fresh_cohorts',lambda *a:((
        rows('learn',8),rows('fit',2),rows('audit',2)),dev32))
    def capture(_head,_model,_tok,rs,**kw):
        return [{'anchors':[(i*6,i*6+2,'actor') for i in range(4)]} for row in rs]
    monkeypatch.setattr(x.x31,'capture_occurrences',capture)
    monkeypatch.setattr(x.x31,'example_from_case',lambda *a,**k:{})
    def scored(*a,**k):
        item,policy=case()
        item['raw'][x.x31.CHAMPION]=item['raw'][x.x31.REFERENCE].copy()
        return item
    monkeypatch.setattr(x.x31,'score_case',scored)
    def fitting(data,*,seeds,steps,device):
        return ({seeds[0]:(x.GroupCardinality(),x.GraphCardinality())},
                {str(seeds[0]):{'group':.5,'graph':.5}})
    monkeypatch.setattr(x,'fit_heads',fitting)
    seen=[]
    def fit_policy(cases,nets):
        seen.append(len(cases))
        policies={f'{arm}_s{s}':{'source':f'occurrence_graph_s{s}',
                  'enabled':False,'mode':'disabled','edge_prob':1.,'confidence':1.,
                  'group_gap':0.,'max_additions':0,'offset':0.}
                  for arm in x.ARMS for s in x.SEEDS}
        return policies,{}
    monkeypatch.setattr(x,'fit_policies',fit_policy)
    monkeypatch.setattr(x.x31.rel,'validate_original_state',lambda *a:None)
    out=tmp_path/'complete'
    result=x.run(precision30_run='unused',representation31_run='unused',
                 output=str(out),device='cpu',steps=x.TRAIN_STEPS,bakeoff29_run='unused')
    assert seen==[2]  # no DEV label or replay case can select a policy
    assert result['evaluation']['new_0032_dev']['cases']==2
    assert result['calibration_fit']['cases']==2
    assert result['calibration_audit']['cases']==2
    assert result['sealed_test_used'] is False
    assert result['production_promotion'] is False
    assert result['ledger_sha256']==x.backbone.sha256(out/x.LEDGER)
    assert result['development_weights_sha256']==x.backbone.sha256(out/x.WEIGHTS)
    import gzip
    with gzip.open(out/x.LEDGER,'rt') as reader:
        assert sum(1 for _ in reader)==8
    with pytest.raises(FileExistsError):
        x.run(precision30_run='unused',representation31_run='unused',
                 output=str(out),device='cpu',steps=x.TRAIN_STEPS,bakeoff29_run='unused')
