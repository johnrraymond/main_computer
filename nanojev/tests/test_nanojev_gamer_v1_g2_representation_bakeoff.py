"""G2-0031: token-occurrence representation learning, parent and no-leakage gates."""
import builtins
import inspect
import math
import symtable
from copy import deepcopy

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_representation_bakeoff as x


def sample(dim=16,seed=7):
    torch.manual_seed(seed)
    nodes=4
    mask=torch.tensor([[0,1,1,1,1],[1,1,1,1,1],
                       [1,1,1,0,0],[1,1,1,1,1]],dtype=torch.bool)
    edges=[(0,1),(0,2),(0,3),(2,0),(2,1),(2,3)]
    anchors=[(i*12,i*12+5,'actor') for i in range(nodes)]
    return {'windows':torch.randn(nodes,5,dim).half(),
        'window_mask':mask,
        'src':torch.tensor([a for a,b in edges]),
        'dst':torch.tensor([b for a,b in edges]),
        'reference':torch.tensor([1.4,1.2,.5,1.7,1.2,.3]),
        'relation':torch.tensor([1,1,1,2,2,2]),
        'group':torch.tensor([0,0,0,1,1,1]),
        'labels':torch.tensor([1.,1.,0.,1.,0.,0.]),
        'gold':{(anchors[a],anchors[b],'owns') for a,b in edges[:2]},
        'edges':[(anchors[a],anchors[b],'owns') for a,b in edges],
        'features':torch.randn(6,61),
        'prompt':'nothing semantic',
        'all_inventory_edges':len(edges),'all_candidate_gold':3}


def test_token_windows_predicted_spans_and_boundaries():
    hidden=torch.tensor([[1.,0.],[2.,0.],[3.,0.],[4.,0.],[5.,0.],[6.,0.]])
    prepared={'offsets':[(0,2),(2,4),(4,6),(6,8),(8,10),(10,12)],
              'eligible':torch.tensor([True]*6),
              'prompt_start':0,'prompt':'abcdefghijkl'}
    anchors=[(4,6,'actor'),(8,10,'actor')]
    windows,mask=x.occurrence_windows(hidden,prepared,anchors)
    assert windows.shape==(2,5,2)
    assert windows[0,:,0].tolist()==[1,2,3,4,5]
    assert windows[1,:,0].tolist()==[3,4,5,6,0]
    assert mask[0].all() and not mask[1,-1]
    with pytest.raises(ValueError,match='BAD_PREDICTED_ANCHOR'):
        x.occurrence_windows(hidden,prepared,[(4,6,'actor'),(20,21,'actor')])


def test_repeated_surface_occurrences_get_distinct_local_evidence():
    hidden=torch.randn(12,16)
    hidden[3]=hidden[9]  # same surface-token evidence, different contexts
    prepared={'offsets':[(i*2,i*2+2) for i in range(12)],
              'eligible':torch.tensor([True]*12),
              'prompt_start':0,'prompt':'x'*24}
    windows,mask=x.occurrence_windows(hidden,prepared,[(6,8,'actor'),(18,20,'actor')])
    assert torch.equal(windows[0,2],windows[1,2])
    assert not torch.equal(windows[0],windows[1])
    torch.manual_seed(15)
    enc=x.TokenOccurrenceEncoder(16)
    vec,_=enc(windows,mask)
    assert not torch.allclose(vec[0],vec[1])


@pytest.mark.parametrize('arm,cls',[
    ('role_span_biaffine',x.RoleSpanBiaffine),
    ('cross_occurrence_attention',x.CrossOccurrenceAttention),
    ('occurrence_graph',x.OccurrenceGraph)])
def test_trainable_token_architecture_and_no_gold_inputs(arm,cls):
    example=sample()
    model=cls(16)
    features=x.scorer_input(example,'cpu')
    assert not {'gold','labels','prompt','features','nodes'}&set(features)
    score=model(features)
    assert score.shape==(6,) and torch.isfinite(score).all()
    altered=deepcopy(example)
    altered['labels']=1-example['labels']
    altered['gold']=set()
    altered['features']=torch.full_like(example['features'],999.)
    assert torch.allclose(model(x.scorer_input(altered,'cpu')),score)
    objective=x.m29.ranking_objective(score,{'labels':example['labels'],
                                            'group':example['group']})
    objective.backward()
    assert any(p.grad is not None and p.grad.abs().sum()>0
               for name,p in model.named_parameters() if 'encoder' in name)
    assert model.encoder.token.weight.grad is not None


def test_three_arm_fit_shared_batches_and_seeds():
    items=[sample(seed=i) for i in range(8)]
    models,log,params=x.fit_architectures(items,16,device='cpu',steps=2,seeds=(1,))
    assert set(models[1])==set(x.ARMS)
    assert set(log['1'])==set(x.ARMS)
    assert all(v>0 for v in params['1'].values())
    assert all(not p.requires_grad for m in models[1].values() for p in m.parameters())


def test_training_scorers_differ_from_reference_without_using_frozen_node_features():
    example=sample()
    token=x.scorer_input(example,'cpu')
    for arm,model in x.models_for_seed(16,22,'cpu').items():
        initial=model(token)
        assert (initial-example['reference']).abs().max()<0.08
        assert torch.isfinite(initial).all()


def test_promotion_requires_graph_f1_exact_ood_and_all_seeds():
    base={'f1':.57,'exact_graph_rate':.06,'ranking_failures':300}
    models={key:dict(base) for key in ('identity_peer_0026',
                    'calibrated_reference_0030','calibrated_reference',
                    'predicate_argument_0030')}
    for arm in x.ARMS:
        for seed in x.SEEDS:
            models[f'{arm}_s{seed}']={'f1':.61,'exact_graph_rate':.06,
                                     'ranking_failures':290}
    result=x.promotion_gates({'models':models},{'models':models})
    assert result['development_success'] is True
    assert result['production_promotion'] is False
    bad=deepcopy(models)
    bad[f'{x.ARMS[0]}_s{x.SEEDS[0]}']['exact_graph_rate']=.05
    assert not x.promotion_gates({'models':bad},{'models':bad})['arms'][x.ARMS[0]]['all_seeds_pass']
    bad=deepcopy(models)
    bad[f'{x.ARMS[1]}_s{x.SEEDS[1]}']['f1']=.58
    assert not x.promotion_gates({'models':bad},{'models':bad})['arms'][x.ARMS[1]]['all_seeds_pass']


def test_parent_verifier_fails_closed_on_missing_or_invalid_report(tmp_path):
    with pytest.raises(FileNotFoundError):
        x.verify_parent30(tmp_path,{},'x',[],{})
    (tmp_path/x.p30.REPORT).write_text('{"schema":"wrong"}')
    with pytest.raises(ValueError,match='PARENT_0030_CONTRACT'):
        x.verify_parent30(tmp_path,{},'x',[],{})


def test_new_calibration_and_dev_prompt_disjoint():
    state={'training':[],'history29':[],'fresh29':{}}
    calib,new=x.fresh_cohorts(state,[],{})
    assert len(calib)==180 and list(map(len,new.values()))==[320,128]
    all_rows=[*calib,*(r for v in new.values() for r in v)]
    assert len({x.base._sha(r['prompt']) for r in all_rows})==len(all_rows)
    assert {r['split'] for r in calib}=={'train'}
    assert {r['split'] for v in new.values() for r in v}=={'dev'}


def test_command_and_all_global_names_resolve():
    import subprocess,sys
    cmd=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
                        'frob-g2-representation-bakeoff','--help'],
                        capture_output=True,text=True,check=True)
    assert '--precision30-run' in cmd.stdout
    for func in (x.run,x.verify_parent30,x.capture_occurrences,x.fresh_cohorts):
        tree=symtable.symtable(inspect.getsource(func),'<source>','exec')
        scope=next(t for t in tree.get_children() if t.get_name()==func.__name__)
        names=[v.get_name() for v in scope.get_symbols()
               if v.is_global() and not hasattr(x,v.get_name())
               and not hasattr(builtins,v.get_name())]
        assert names==[]


def test_no_learned_model_can_read_author_graph_or_frozen_node_vectors():
    for cls in (x.RoleSpanBiaffine,x.CrossOccurrenceAttention,x.OccurrenceGraph):
        source=inspect.getsource(cls.forward)
        assert "['gold']" not in source and "['labels']" not in source
        assert "['features']" not in source and "['nodes']" not in source
    assert x.STEPS==480 and len(x.SEEDS)==3
    assert x.CHAMPION=='predicate_argument_s460038'


def test_full_report_and_immutable_outputs_mocked_without_cuda(tmp_path,monkeypatch):
    """CPU end-to-end contract: no DEV fitting and output files are internally consistent."""
    from torch import nn
    from pathlib import Path
    import json,gzip

    class Frozen(nn.Module):
        def __init__(self):
            super().__init__()
            self.w=nn.Parameter(torch.tensor([0.]),requires_grad=False)
    head=Frozen();ref=Frozen();back=Frozen()
    rows=lambda prefix,n,split:[{'prompt':f'{prefix} unique {i}',
                                  'family':'allocation','split':split,
                                  'structural_topology':'native'} for i in range(n)]
    training=rows('training',120,'train')
    calibration=rows('cal',180,'train')
    old={k:rows(k,1,'dev') for k in x.REPLAY}
    fresh={k:rows(k,1,'dev') for k in x.NEW}
    state={'training':training,'history29':[],
           'fresh29':{},'head':head,'head_before':deepcopy(head.state_dict()),
           'model':back,'tokenizer':None,'reference':ref,
           'reference_before':deepcopy(ref.state_dict()),
           'models29':{1:{'a':Frozen()}},'allowed':None,
           'ancestors':{}}
    monkeypatch.setattr(x.p30,'_prepare',lambda **kw:state)
    monkeypatch.setattr(x.p30,'calibration_and_dev',lambda *args:([],old))
    monkeypatch.setattr(x,'verify_parent30',lambda *args:({},
        {'calibrated_reference':{'source':x.REFERENCE,'alpha':0.,'threshold':.75,
          'margin':None,'reference_gate':None},
         x.CHAMPION:{'source':x.CHAMPION,'alpha':1.,'threshold':.75,
          'margin':None,'reference_gate':None}}, {},{'hash30':'saved'}))
    monkeypatch.setattr(x,'fresh_cohorts',lambda *args:(calibration,fresh))

    def capture(head,model,tok,rs,**kw):
        return [{'anchors':[(i*12,i*12+5,'actor') for i in range(4)],
                 'windows':torch.randn(4,5,16),
                 'window_mask':torch.ones(4,5,dtype=torch.bool)} for row in rs]
    monkeypatch.setattr(x,'capture_occurrences',capture)
    monkeypatch.setattr(x,'example_from_case',lambda case,row,state,**kw:sample())
    def fitted(*args,**kw):
        return ({seed:x.models_for_seed(16,seed,'cpu') for seed in x.SEEDS},
                {str(seed):{arm:0.4 for arm in x.ARMS} for seed in x.SEEDS},
                {str(seed):{arm:1 for arm in x.ARMS} for seed in x.SEEDS})
    monkeypatch.setattr(x,'fit_architectures',fitted)
    monkeypatch.setattr(x,'calibrate',lambda items:({
        'calibrated_reference':{'source':x.REFERENCE,'alpha':0.,'threshold':.75,
          'margin':None,'reference_gate':None},
        **{f'{arm}_s{seed}':{'source':f'{arm}_s{seed}','alpha':1.,
            'threshold':.75,'margin':None,'reference_gate':None}
            for arm in x.ARMS for seed in x.SEEDS}},{}))

    def scored(ex,state,models):
        raw={x.REFERENCE:ex['reference'].numpy().copy(),
             x.CHAMPION:ex['reference'].numpy().copy()}
        raw.update({f'{arm}_s{seed}':ex['reference'].numpy().copy()
                    for arm in x.ARMS for seed in x.SEEDS})
        return {'raw':raw,'edges':ex['edges'],
                'groups':ex['group'].numpy().astype('int32'),
                'gold':ex['gold'],'gold_total':len(ex['gold']),
                'candidate_gold':ex['all_candidate_gold'],
                'candidate_count':ex['all_inventory_edges']}
    monkeypatch.setattr(x,'score_case',scored)
    out=tmp_path/'output'
    result=x.run(precision30_run='unused',output=str(out),device='cpu',steps=x.STEPS,
                 bakeoff29_run='unused')
    assert result['production_promotion'] is False
    assert result['sealed_test_used'] is False
    assert set(result['by_split'])==set(x.NEW+x.REPLAY)
    assert result['calibration_audit']['cases']==60
    assert result['calibration_fit']['cases']==120
    assert result['evaluation']['new_0031_dev']['cases']==2
    assert result['ledger_sha256']==x.backbone.sha256(out/x.LEDGER)
    assert result['development_weights_sha256']==x.backbone.sha256(out/x.WEIGHTS)
    with gzip.open(out/x.LEDGER,'rt') as f:
        ledgers=[json.loads(line) for line in f]
    assert len(ledgers)==180+2+2
    with pytest.raises(Exception):
        x.run(precision30_run='unused',output=str(out),device='cpu',steps=x.STEPS,
              bakeoff29_run='unused')  # mocked parent bypasses immutable check


def test_graph_training_gets_full_label_blind_topology_while_loss_uses_sample(monkeypatch):
    w=8
    nodes=[(i*8,i*8+4,'actor') for i in range(5)]
    edges=[];groups=[];labels=[]
    for group in range(10):
        for j in (0,1):
            edges.append((nodes[group%5],nodes[(group+j+1)%5],f'bind{group}'))
            groups.append(group)
            labels.append(float(j==0 and group<8))
    features=torch.zeros(len(edges),4*w+x.source13.EXTRAS+1+20)
    features[:,4*w+1]=1.
    full={'features':features,'reference':torch.linspace(-2,2,len(edges)),
          'src':torch.tensor([group%5 for group in range(10) for j in (0,1)]),
          'dst':torch.tensor([(group+j+1)%5 for group in range(10) for j in (0,1)]),
          'group':torch.tensor(groups),'edges':edges,
          'labels':torch.tensor(labels),
          'gold':{edge for edge,lab in zip(edges,labels) if lab},
          'nodes':torch.zeros(5,w),'frozen_width':w,
          'all_inventory_edges':len(edges),'all_candidate_gold':8}
    monkeypatch.setattr(x.m29,'case_inventory',lambda *a,**kw:full)
    case={'windows':torch.randn(5,5,16),'window_mask':torch.ones(5,5,dtype=torch.bool),
          'anchors':nodes,'vectors':torch.zeros(5,w),'index':2}
    train=x.example_from_case(case,{'prompt':'dummy'},
                              {'head':None,'reference':None,'allowed':None},train=True)
    assert len(train['edges'])<len(edges)
    assert len(train['message_src'])==len(edges)
    assert len(train['message_reference'])==len(edges)
    view=x.scorer_input(train,'cpu')
    assert not {'labels','gold','features'}&set(view)
    assert len(view['message_src'])>len(view['src'])
    net=x.OccurrenceGraph(16)
    scores=net(view)
    assert scores.shape==(len(train['edges']),)
    assert torch.isfinite(scores).all()
