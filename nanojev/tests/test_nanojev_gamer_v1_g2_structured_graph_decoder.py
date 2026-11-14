"""G2-0033: joint graph-set decode, immutable parents, no gold at inference."""
from copy import deepcopy
from pathlib import Path
import builtins
import inspect
import json
import symtable

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_structured_graph_decoder as s


def case():
    edges=[('a','b','owns'),('a','c','owns'),('a','d','owns'),
           ('a','e','owns'),('b','a','owns'),('b','d','owns')]
    raw={s.x31.REFERENCE:np.array([3.3,2.6,1.4,.5,3.1,2.4],dtype='float32')}
    for seed in s.SEEDS:
        raw[f'occurrence_graph_s{seed}']=np.array([3.9,3.3,2.1,-.5,3.5,2.1],dtype='float32')
    raw[s.x31.CHAMPION]=raw[s.x31.REFERENCE].copy()
    item={'raw':raw,'groups':np.array([0,0,0,0,1,1],dtype='int32'),
          'edges':edges,'gold':{edges[0],edges[1],edges[4]},
          'gold_total':3,'candidate_gold':3,'candidate_count':6}
    policy={'source':'occurrence_graph_s460331','alpha':1.,
            'threshold':.93,'margin':2.,'reference_gate':None}
    return item,policy


def test_feature_contract_and_no_gold_in_inference():
    item,policy=case()
    f,labels=s.supervised(item,policy['source'],policy,4)
    assert f['edges'].shape==(6,s.EDGE_DIM)
    assert f['groups'].shape==(2,s.GROUP_DIM)
    assert labels.sum()==3
    net=s.GraphSetEnergy().eval()
    before=s.predictions(f,net)
    altered=deepcopy(item);altered['gold']=set()
    new=s.set_features(altered,policy['source'],policy,4)
    after=s.predictions(new,net)
    for a,b in zip(before,after):assert np.allclose(a,b)
    assert np.array_equal(f['edges'],new['edges'])
    assert set(net.state_dict())


def test_real_set_objective_backpropagates_and_trains():
    item,policy=case();f,labels=s.supervised(item,policy['source'],policy,4)
    net=s.GraphSetEnergy()
    inputs=s._tensor_features(f,'cpu')
    out=net(*inputs)
    loss,parts=s.set_objective(out,{'index':inputs[2],'edges':f['edges']},labels)
    assert torch.isfinite(loss)
    assert set(parts)=={'edge','group','graph','exact_set_margin'}
    loss.backward()
    assert net.edge_readout.weight.grad is not None
    assert net.group_readout[-1].weight.grad is not None
    fitted,logs=s.fit_models([(f,labels)]*8,seeds=(s.SEEDS[0],),steps=3)
    assert set(fitted)=={s.SEEDS[0]}
    assert all(not p.requires_grad for p in fitted[s.SEEDS[0]].parameters())
    assert 'exact_set_margin' in logs[str(s.SEEDS[0])]


def test_joint_decoder_can_delete_false_edge_and_add_omitted_edge():
    item,policy=case()
    item['raw'][policy['source']][1]=.2  # parent omits a true edge
    item['raw'][policy['source']][2]=3.4 # parent includes a false edge
    feats=s.set_features(item,policy['source'],policy,4)
    parent=s.p30.select_graph(item,**policy)
    assert item['edges'][2] in parent  # false-positive from parent
    assert item['edges'][4] in parent  # true-positive
    # This score ordering forces a different complete set without gold labels.
    logits=np.array([5.,4.,-3.,-4.,4.5,-3.],dtype=np.float64)
    group_logits=np.zeros((2,s.COUNT_CLASSES),dtype=np.float64)
    config={'enabled':True,'source':policy['source'],'group_weight':0.,'global_weight':0.}
    result=s.decode(item,feats,(logits,group_logits,3.),config)
    assert result==item['gold']
    assert item['edges'][2] in parent-result
    assert item['edges'][1] in result
    altered=deepcopy(item);altered['gold']=set(item['edges'])
    assert s.decode(altered,feats,(logits,group_logits,3.),config)==result
    with pytest.raises(ValueError,match='DISABLED_DECODER'):
        s.decode(item,feats,(logits,group_logits,3.),{'enabled':False})


def test_joint_group_global_energy_changes_selection():
    item,policy=case();feats=s.set_features(item,policy['source'],policy,4)
    edge=np.array([5.,4.,-3.,-4.,4.5,-3.])
    # With very strong count likelihood, select one edge from each group.
    group=np.full((2,s.COUNT_CLASSES),-100.,dtype=np.float64)
    group[:,1]=0.
    config={'enabled':True,'group_weight':50.,'global_weight':0.,'source':policy['source']}
    assert len(s.decode(item,feats,(edge,group,2.),config))==2
    config['group_weight']=0.
    assert len(s.decode(item,feats,(edge,group,2.),config))==3
    # Global count preference couples groups even when group count is disabled.
    config['global_weight']=1000.
    assert len(s.decode(item,feats,(edge,group,1.),config))==1


def test_train_only_calibration_can_decline_invalid_new_decoder(monkeypatch):
    item,policy=case()
    item['gold']=s.p30.select_graph(item,**policy)
    item['gold_total']=len(item['gold'])
    parent={f'occurrence_graph_s{k}':{**policy,'source':f'occurrence_graph_s{k}'}
            for k in s.SEEDS}
    recoveries={f'{arm}_s{k}':{'enabled':False,'source':f'occurrence_graph_s{k}',
         'mode':'disabled','edge_prob':1.,'confidence':1.,'group_gap':0.,
         'max_additions':0,'offset':0.} for k in s.SEEDS for arm in s.x32.ARMS}
    class Group(torch.nn.Module):
        def forward(self,features):return torch.zeros((len(features),s.x32.COUNT_CLASSES))
    class Graph(torch.nn.Module):
        def forward(self,features):return torch.zeros(len(features))
    before=s.set_features(item,policy['source'],policy,4)
    def always_bad(f,net):
        # Empty set is worse than an already perfect parent.
        return np.full(len(f['edges']),-100.),np.zeros((len(f['groups']),s.COUNT_CLASSES)),0.
    monkeypatch.setattr(s,'predictions',always_bad)
    p,a=s.fit_policies([(item,4)]*4,{k:s.GraphSetEnergy() for k in s.SEEDS},
                       parent,recoveries,{k:(Group(),Graph()) for k in s.SEEDS})
    assert all(not v['enabled'] for v in p.values())
    assert len(a)==3


def test_gates_are_f1_exact_topology_ranking_and_all_seeds():
    data={name:{'f1':.58,'exact_graph_rate':.05,'ranking_failures':6000}
          for name in ('identity_peer_0026','calibrated_reference_0030',
                       'calibrated_reference_0031','predicate_argument_0030')}
    for seed in s.SEEDS:
        data[f'occurrence_graph_s{seed}']={'f1':.66,'exact_graph_rate':.02,
                                          'ranking_failures':4100}
        for arm in s.x32.ARMS:
            data[f'{arm}_s{seed}']={'f1':.67,'exact_graph_rate':.047,
                                     'ranking_failures':4100}
        data[f'structured_set_s{seed}']={'f1':.668,'exact_graph_rate':.056,
                                          'ranking_failures':4000}
    policy={f'structured_set_s{s0}':{'enabled':True} for s0 in s.SEEDS}
    passed=s.gates({'models':data},{'models':data},policy)
    assert passed['development_success'] is True
    assert not passed['production_promotion']
    data[f'structured_set_s{s.SEEDS[1]}']['exact_graph_rate']=.04
    assert not s.gates({'models':data},{'models':data},policy)['development_success']


def test_fresh_cohorts_exclude_all_parent_prompts_and_heldout_topology():
    state={'training':[],'history29':[],'fresh29':{}}
    (training,fit,audit),dev=s.fresh_cohorts(state,[],{},[],{},[],[],[],{})
    assert (len(training),len(fit),len(audit))==(s.TRAIN,s.CAL_FIT,s.CAL_AUDIT)
    assert [len(dev[k]) for k in s.NEW]==[320,128]
    rows=[*training,*fit,*audit,*(r for batch in dev.values() for r in batch)]
    assert len({s.base._sha(r['prompt']) for r in rows})==len(rows)
    assert all(r['split']=='train' for r in (*training,*fit,*audit))
    assert all(r['structural_topology'] not in ('2x4','4x2','4x4')
               for r in (*training,*fit,*audit))


def test_uploaded_parent32_weights_reconstruct_if_present():
    file=Path('/mnt/data/g2_graph_completeness_development.safetensors')
    if not file.is_file():pytest.skip('No uploaded G2-0032 development weights')
    nets=s.load_parent32_models(s.load_file(str(file),device='cpu'))
    assert len(nets)==3
    assert all(len(pair)==2 and all(not p.requires_grad
               for net in pair for p in net.parameters()) for pair in nets.values())


def test_cli_and_code_global_name_audit():
    import subprocess,sys
    command=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
       'frob-g2-structured-graph-decoder','--help'],capture_output=True,text=True,check=True)
    assert '--completeness32-run' in command.stdout
    for fn in (s.run,s.verify_parent32,s.fresh_cohorts,s.fit_policies,
               s.evaluate_case,s.set_features,s.fit_models,s.decode):
        tree=symtable.symtable(inspect.getsource(fn),'<audit>','exec')
        scope=next(x for x in tree.get_children() if x.get_name()==fn.__name__)
        missing=[name.get_name() for name in scope.get_symbols() if name.is_global()
           and not hasattr(s,name.get_name()) and not hasattr(builtins,name.get_name())]
        assert not missing,(fn.__name__,missing)


def test_parent32_schema_mismatch_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        s.verify_parent32(tmp_path,train32=[],fit32=[],audit32=[],dev32={},
                          ancestors={},training_sha='')
    (tmp_path/s.x32.REPORT).write_text(json.dumps({'schema':'WRONG'}))
    with pytest.raises(ValueError,match='PARENT_0032_CONTRACT'):
        s.verify_parent32(tmp_path,train32=[],fit32=[],audit32=[],dev32={},
                          ancestors={},training_sha='')


def test_full_pipeline_report_writes_and_replay_guards(tmp_path,monkeypatch):
    class Frozen(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight=torch.nn.Parameter(torch.tensor([.5]),requires_grad=False)
    def rows(name,n,split='train'):
        return [{'prompt':f'{name} unique {i}', 'split':split,
                 'family':'allocation','structural_topology':'native'} for i in range(n)]
    head=Frozen();ref=Frozen();back=Frozen()
    dev31={k:rows(k,1,'dev') for k in s.x32.REPLAY}
    dev32={k:rows(k,1,'dev') for k in s.x32.NEW}
    fresh33={k:rows(k,1,'dev') for k in s.NEW}
    training=rows('parent',8)
    state={'training':training,'history29':[],'fresh29':{},
           'head':head,'head_before':deepcopy(head.state_dict()),
           'reference':ref,'reference_before':deepcopy(ref.state_dict()),
           'model':back,'tokenizer':None,'models29':{},'allowed':None,'ancestors':{}}
    monkeypatch.setattr(s.p30,'_prepare',lambda **kwargs:state)
    monkeypatch.setattr(s.p30,'calibration_and_dev',lambda *args:([],{}))
    ref_policy={'source':s.x31.REFERENCE,'alpha':0.,'threshold':.75,
                'margin':None,'reference_gate':None}
    champion={'source':s.x31.CHAMPION,'alpha':1.,'threshold':.75,
              'margin':None,'reference_gate':None}
    policies31={f'occurrence_graph_s{k}':{'source':f'occurrence_graph_s{k}',
        'alpha':1.,'threshold':.93,'margin':2.,'reference_gate':None} for k in s.SEEDS}
    policies31['calibrated_reference']=ref_policy
    monkeypatch.setattr(s.x31,'verify_parent30',lambda *a,**k:({},
        {'calibrated_reference':ref_policy,s.x31.CHAMPION:champion},{},{'h30':'x'}))
    monkeypatch.setattr(s.x31,'fresh_cohorts',lambda *a:(rows('cal31',2),dev31))
    monkeypatch.setattr(s.x32,'verify_parent31',lambda *a,**k:({},policies31,{},
                                {},{'h31':'x'}))
    monkeypatch.setattr(s.x32,'fresh_cohorts',lambda *a:((rows('t32',2),
          rows('f32',2),rows('a32',2)),dev32))
    policies32={f'{arm}_s{k}':{'enabled':False,'source':f'occurrence_graph_s{k}',
         'mode':'disabled','edge_prob':1.,'confidence':1.,'group_gap':0.,
         'max_additions':0,'offset':0.} for k in s.SEEDS for arm in s.x32.ARMS}
    monkeypatch.setattr(s,'verify_parent32',lambda *a,**k:({},policies32,{},
             {},{'h32':'x'}))
    monkeypatch.setattr(s.x32,'load_parent_models',lambda *a,**k:{k:{} for k in s.SEEDS})
    monkeypatch.setattr(s,'load_parent32_models',lambda *a,**k:{
        k:(s.x32.GroupCardinality().eval().requires_grad_(False),
           s.x32.GraphCardinality().eval().requires_grad_(False)) for k in s.SEEDS})
    monkeypatch.setattr(s,'fresh_cohorts',lambda *a:((rows('train',8),
                   rows('fit',2),rows('audit',2)),fresh33))
    monkeypatch.setattr(s.x31,'capture_occurrences',lambda _head,_mod,_tok,rs,**kw:
            [{'anchors':[(i*6,i*6+2,'actor') for i in range(4)]} for row in rs])
    monkeypatch.setattr(s.x31,'example_from_case',lambda *a,**k:{})
    monkeypatch.setattr(s.x31,'score_case',lambda *a,**k:case()[0])
    monkeypatch.setattr(s,'fit_models',lambda data,*,seeds,steps,device:(
        {seeds[0]:s.GraphSetEnergy().eval().requires_grad_(False)},
        {str(seeds[0]):{'edge':0.,'group':0.,'graph':0.,'exact_set_margin':0.}}))
    monkeypatch.setattr(s.x31.rel,'validate_original_state',lambda *args:None)
    seen=[]
    old_fit=s.fit_policies
    def fitting(cases,*args):
        seen.append(len(cases))
        return old_fit(cases,*args)
    monkeypatch.setattr(s,'fit_policies',fitting)
    out=tmp_path/'structured'
    report=s.run(precision30_run='parent30',representation31_run='parent31',
                 completeness32_run='parent32',output=str(out),device='cpu',
                 steps=s.STEPS,bakeoff29_run='unused')
    assert seen==[2]
    assert report['evaluation']['new_0033_dev']['cases']==2
    assert report['production_promotion'] is False
    assert report['sealed_test_used'] is False
    assert report['ledger_sha256']==s.backbone.sha256(out/s.LEDGER)
    assert report['policies_sha256']==s.backbone.sha256(out/s.POLICIES)
    assert report['development_weights_sha256']==s.backbone.sha256(out/s.WEIGHTS)
    import gzip
    with gzip.open(out/s.LEDGER,'rt') as stream:
        saved=[json.loads(line) for line in stream]
    assert len(saved)==8
    assert all('edge_diagnostics' not in r for r in saved[:6])
    assert all(set(r['edge_diagnostics'])=={
        f'structured_set_s{k}' for k in s.SEEDS} for r in saved[6:])
    assert all('flagged_candidate_edges' in record for r in saved[6:]
               for record in r['edge_diagnostics'].values())
    with pytest.raises(FileExistsError):
        s.run(precision30_run='parent30',representation31_run='parent31',
             completeness32_run='parent32',output=str(out),device='cpu',
             steps=s.STEPS,bakeoff29_run='unused')


def test_vectorized_decoding_matches_exhaustive_joint_set_search():
    from itertools import product
    item,policy=case()
    f=s.set_features(item,policy['source'],policy,4)
    scores=np.array([1.0,-.8,.4,.2,.5,-1.1])
    counts=np.full((2,s.COUNT_CLASSES),-20.,dtype=np.float64)
    counts[0,:5]=[-.4,-.8,0.,-.3,-.5]
    counts[1,:3]=[-1.,-.1,-.5]
    config={'enabled':True,'group_weight':.7,'global_weight':.3}
    expected=None
    ordered=[]
    for g in range(2):
        ids=np.flatnonzero(f['index']==g).tolist()
        ordered.append(sorted(ids,key=lambda i:(-scores[i],item['edges'][i])))
    for ks in product(range(5),range(3)):
        chosen={item['edges'][i] for g,k in enumerate(ks) for i in ordered[g][:k]}
        count=sum(ks)
        val=(sum(scores[i] for g,k in enumerate(ks) for i in ordered[g][:k])+
             .7*sum(counts[g,k] for g,k in enumerate(ks))-
             .3*(np.log1p(count)-np.log1p(3.))**2)
        quality=(val,-count,tuple(-k for k in ks))
        if expected is None or quality>expected[0]:expected=(quality,chosen)
    result=s.decode(item,f,(scores,counts,3.),config)
    assert result==expected[1]


def test_parent32_live_replay_metric_tamper_is_rejected():
    item,_=case()
    refpol={'source':s.x31.REFERENCE,'alpha':0.,'threshold':.75,
            'margin':None,'reference_gate':None}
    parent_policies={'calibrated_reference_0030':refpol,
                     'calibrated_reference_0031':refpol,
                     'predicate_argument_0030':{'source':s.x31.CHAMPION,
                       'alpha':1.,'threshold':.75,'margin':None,'reference_gate':None}}
    parent_policies.update({f'occurrence_graph_s{k}':{'source':f'occurrence_graph_s{k}',
        'alpha':1.,'threshold':.93,'margin':2.,'reference_gate':None} for k in s.SEEDS})
    p32={f'{arm}_s{k}':{'enabled':False,'source':f'occurrence_graph_s{k}',
          'mode':'disabled','edge_prob':1.,'confidence':1.,'group_gap':0.,
          'max_additions':0,'offset':0.} for k in s.SEEDS for arm in s.x32.ARMS}
    nets32={k:(s.x32.GroupCardinality().eval(),s.x32.GraphCardinality().eval())
            for k in s.SEEDS}
    nets33={k:s.GraphSetEnergy().eval() for k in s.SEEDS}
    p33={f'structured_set_s{k}':{'enabled':False,'fallback':f'group_recovery_s{k}',
         'source':f'occurrence_graph_s{k}'} for k in s.SEEDS}
    row={'prompt':'invented test prompt','family':'allocation','structural_topology':'native'}
    original=s.evaluate_case(item,4,row,'new_independent_dev_0032',1,
                 parent_policies,p32,nets32,nets33,p33)
    modified=deepcopy(original)
    modified['metrics']['identity_peer_0026']['false_positive']+=1
    with pytest.raises(ValueError,match='PARENT_0032_LIVE_REPLAY_DRIFT'):
        s.evaluate_case(item,4,row,'new_independent_dev_0032',1,
              parent_policies,p32,nets32,nets33,p33,prior32=modified)
