"""G2-0030 graph-F1-calibration and no-leakage contract."""
import builtins
import inspect
import json
import math
import symtable
from collections import Counter

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_graph_precision_selection as x


def example(*,empty=False):
    anchors=[(i*10,i*10+5,'actor') for i in range(6)]
    edges=[(anchors[0],anchors[1],'owns'),(anchors[0],anchors[2],'owns'),
           (anchors[0],anchors[3],'owns'),(anchors[4],anchors[1],'owns'),
           (anchors[4],anchors[2],'owns'),(anchors[4],anchors[3],'owns')]
    if empty:edges=[]
    return {'raw':{x.REFERENCE:np.array([2.5,1.2,2.0,2.5,2.0,1.2][:len(edges)],dtype='float32'),
             'predicate_argument_s1':np.array([2.7,2.5,0.2,2.1,1.2,0.1][:len(edges)],dtype='float32')},
            'edges':edges,'groups':np.array([0,0,0,1,1,1][:len(edges)],dtype='int32'),
            'gold':set(edges[i] for i in (0,1,3) if i<len(edges)),
            'gold_total':0 if empty else 3,'candidate_gold':0 if empty else 3,
            'candidate_count':len(edges)}


def policy(source,alpha,threshold=.75,margin=None,reference_gate=None):
    return dict(source=source,alpha=alpha,threshold=threshold,
                margin=margin,reference_gate=reference_gate)


def test_reference_decode_exactly_matches_original_decoder():
    item=example()
    ex={'edges':item['edges'],'group':torch.tensor(item['groups'])}
    for threshold in (.55,.65,.75,.82,.88,.93):
        ours=x.select_graph(item,**policy(x.REFERENCE,0,threshold))
        assert ours==x.source13.decode_scores(
            {'edges':ex['edges'],'groups':list(ex['group']),
             'null_argmax':[False]*len(ex['edges'])},
            torch.tensor(item['raw'][x.REFERENCE]),threshold)


def test_agreement_gate_prunes_false_edges_and_allows_multiple_gold_edges():
    item=example()
    blind=x.select_graph(item,**policy('predicate_argument_s1',1,.75))
    assert item['edges'][0] in blind and item['edges'][1] in blind
    assert item['edges'][2] not in blind
    gated=x.select_graph(item,**policy('predicate_argument_s1',1,.75,reference_gate=.60))
    assert item['edges'][0] in gated and item['edges'][1] in gated
    assert gated.issubset(blind)
    squeezed=x.select_graph(item,**policy('predicate_argument_s1',1,.75,margin=.3))
    assert squeezed.issubset(blind)
    assert x.measure(blind,item)['correct']==3


def test_gold_and_label_mutation_cannot_affect_decoding():
    item=example()
    selected=x.select_graph(item,**policy('predicate_argument_s1',.5,.75,margin=2))
    altered={**item,'gold':set(),'labels':np.ones(6),
             'prompt':'arbitrary alternative unrelated words'}
    assert x.select_graph(altered,**policy('predicate_argument_s1',.5,.75,margin=2))==selected
    assert x.select_graph(example(empty=True),**policy(x.REFERENCE,0))==set()
    with pytest.raises(ValueError,match='ALPHA'):
        x.select_graph(item,**policy(x.REFERENCE,1))


def test_train_only_policy_selection_optimizes_graph_f1_and_is_deterministic():
    fit=[example() for _ in range(12)]
    arms=('predicate_argument',)
    p1,f1=x.select_calibration_policies(fit,seeds=(1,),arms=arms)
    p2,f2=x.select_calibration_policies(fit,seeds=(1,),arms=arms)
    assert p1==p2 and f1==f2
    assert set(p1)=={'calibrated_reference','predicate_argument_s1'}
    assert f1['predicate_argument_s1']>=f1['calibrated_reference']
    assert p1['predicate_argument_s1']['alpha'] in x.ALPHAS
    assert p1['calibrated_reference']['source']==x.REFERENCE
    assert math.isfinite(f1['calibrated_reference'])


def test_parent_weight_loader_requires_all_nine_complete_frozen_models():
    width=8;dim=4*width+x.source13.EXTRAS+21
    tensors={}
    classes={'predicate_argument':x.m.PredicateArgument,
             'dynamic_span_graph':x.m.DynamicSpanGraph,
             'joint_graph_decoder':x.m.JointGraphDecoder}
    for seed in x.SEEDS:
        for arm,cls in classes.items():
            for key,value in cls(width,dim).state_dict().items():
                tensors[f'{arm}_s{seed}.{key}']=value.detach().cpu().contiguous()
    models=x.load_frozen_challengers(tensors,width,dim)
    assert len(models)==3 and all(len(v)==3 for v in models.values())
    assert all(not par.requires_grad for group in models.values() for model in group.values()
               for par in model.parameters())
    missing=dict(tensors);missing.pop(next(iter(missing)))
    with pytest.raises(ValueError,match='WEIGHT_SHAPE'):
        x.load_frozen_challengers(missing,width,dim)


def test_aggregates_and_development_gates_preclude_false_success():
    baseline={'correct':4,'false_positive':4,'missing':2,
              'exact_cases':0,'competing_gold':6,'ranking_failures':2}
    challenger={'correct':5,'false_positive':12,'missing':1,
                'exact_cases':0,'competing_gold':6,'ranking_failures':1}
    rec={'gold_total':6,'candidate_gold':5,'metrics':{
        'identity_peer_0026':baseline,'calibrated_reference':baseline,
        'predicate_argument_s1':challenger}}
    summary=x.aggregate([rec])
    assert summary['models']['identity_peer_0026']['f1']>summary['models']['predicate_argument_s1']['f1']
    gates=x.development_gates(summary,summary,seeds=(1,),arms=('predicate_argument',))
    assert gates['arms']['predicate_argument']['all_seeds_pass'] is False
    assert gates['production_promotion'] is False
    assert summary['offline_deterministic_candidate_oracle_ceiling_f1']==10/11


def test_parent_verifier_rejects_wrong_schema_without_loading_any_weights(tmp_path):
    with pytest.raises(FileNotFoundError):
        x.verify_parent29(tmp_path,{}, {},'a')
    (tmp_path/x.m.REPORT).write_text(json.dumps({'schema':'invalid'}))
    with pytest.raises(ValueError,match='CONTRACT'):
        x.verify_parent29(tmp_path,{}, {},'a')


def test_synthetic_calibration_split_and_fresh_dev_are_disjoint():
    calibration,new=x.calibration_and_dev([], {k:[] for k in x.m.NEW})
    assert len(calibration)==180 and [len(new[k]) for k in x.NEW]==[320,128]
    hashes=[x.base._sha(r['prompt']) for r in [*calibration,*(r for v in new.values() for r in v)]]
    assert len(hashes)==len(set(hashes))
    assert all(r['split']=='train' for r in calibration)
    assert all(r['split']=='dev' for rows in new.values() for r in rows)
    # Fit/audit are shuffled, not divided by the generator's topology order.
    assert len({r['structural_topology'] for r in calibration[:x.CAL_FIT]})>=4
    assert len({r['structural_topology'] for r in calibration[x.CAL_FIT:]})>=4


def test_command_and_all_global_references_resolve():
    assert x.REFERENCE==x.m.REFERENCE and x.REPLAY==x.m.NEW
    assert x.CAL_FIT==120 and x.CAL_AUDIT==60
    assert x.NEW==('new_independent_dev_0030','new_topology_dev_0030')
    for func in (x._prepare,x.run):
        table=symtable.symtable(inspect.getsource(func),'<code>','exec')
        child=next(t for t in table.get_children() if t.get_name()==func.__name__)
        unresolved=[symbol.get_name() for symbol in child.get_symbols()
                    if symbol.is_global() and not hasattr(x,symbol.get_name())
                    and not hasattr(builtins,symbol.get_name())]
        assert unresolved==[]


def test_end_to_end_diagnostic_report_mocked_without_cuda_or_gold_in_scorers(monkeypatch,tmp_path):
    """Exercise output, replay verification and immutable artifacts without a GPU."""
    from copy import deepcopy

    base_item=example()
    sample=[{'prompt':'synthetic '+str(i),'family':'allocation','split':'train',
             'structural_topology':'allocation'} for i in range(180)]
    previous={k:[{'prompt':f'previous {k} {i}','family':'allocation',
                   'structural_topology':'allocation','split':'dev'}
                 for i in range(n)] for k,n in zip(x.REPLAY,(320,128))}
    future={k:[{'prompt':f'future {k} {i}','family':'allocation',
                 'structural_topology':'allocation','split':'dev'}
               for i in range(n)] for k,n in zip(x.NEW,(320,128))}

    class Dummy(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.weight=torch.nn.Parameter(torch.zeros(1),requires_grad=False)
        def forward(self,case):
            assert 'gold' not in case and 'labels' not in case
            return case['reference']+.1*self.weight

    ref=Dummy();head=Dummy();model=Dummy()
    old={k:[{'metrics':{x.REFERENCE:{'correct':3,'false_positive':3,'missing':0,
              'competing_gold':3,'ranking_failures':1}}} for _ in rows]
         for k,rows in previous.items()}
    monkeypatch.setattr(x,'_prepare',lambda **kw:{
        'history29':[],'fresh29':previous,'head':head,'model':model,'tokenizer':None,
        'reference':ref,'allowed':None,'models29':{
            seed:{arm:Dummy() for arm in x.ARMS} for seed in x.SEEDS},
        'old29':old,'parent29_report':{'new_prompt_sha256':{k:[] for k in x.REPLAY}},
        'training':sample,'ancestors':{},'head_before':deepcopy(head.state_dict()),
        'reference_before':deepcopy(ref.state_dict())})
    monkeypatch.setattr(x,'calibration_and_dev',lambda history,fresh:(sample,future))
    monkeypatch.setattr(x.rel,'capture',lambda head,model,tok,rows,**kw:[{
        'anchors':[(i,i+1,'actor') for i in range(6)],'gold':base_item['gold']}
        for r in rows])
    def inventory(case,row,head,reference,allowed,**kw):
        assert not kw['train']
        return {'features':torch.zeros((6,3)),'reference':torch.tensor(
                 base_item['raw'][x.REFERENCE]),'nodes':torch.zeros(6,2),
            'src':torch.tensor([0,0,0,4,4,4]),
            'dst':torch.tensor([1,2,3,1,2,3]),
            'group':torch.tensor([0,0,0,1,1,1]),
            'edges':base_item['edges'],'gold':base_item['gold'],
            'all_inventory_edges':6,'all_candidate_gold':3}
    monkeypatch.setattr(x.m,'case_inventory',inventory)
    monkeypatch.setattr(x.rel,'validate_original_state',lambda a,b:None)
    monkeypatch.setattr(x,'select_calibration_policies',lambda cases:(
        {'calibrated_reference':policy(x.REFERENCE,0),
         **{f'{arm}_s{seed}':policy(f'{arm}_s{seed}',.5)
             for arm in x.ARMS for seed in x.SEEDS}},
        {'calibrated_reference':.5}))
    dest=tmp_path/'g2-0030'
    report=x.run(bakeoff29_run=str(tmp_path),output=str(dest),device='cpu')
    assert report['evaluation']['new_0030_dev']['cases']==448
    assert report['evaluation']['replayed_0029_dev']['cases']==448
    assert report['calibration_fit']['cases']==120
    assert report['calibration_audit']['cases']==60
    assert report['production_promotion'] is False
    assert (dest/x.REPORT).is_file() and (dest/x.POLICIES).is_file()
    assert (dest/x.LEDGER).is_file()
    assert report['ledger_sha256']==x.backbone.sha256(dest/x.LEDGER)
