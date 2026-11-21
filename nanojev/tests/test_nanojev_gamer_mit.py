"""Numerical functionality of the MIT G2 cutover, without GPU/checkpoint artifacts."""
from __future__ import annotations

import torch

from tools.nanojev_gamer_v1_g2_active import ACTIVE_VERSION
from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import decode as greedy
from tools.nanojev_gamer_v1_graph_head import GamerGraphHead
from tools.nanojev_gamer_v1_mit import sparse_train
from tools.nanojev_gamer_v1_mit_decoder import decode, candidate_edges


def _surface():
    objects=[[0,2,'actor'],[3,5,'actor'],[6,8,'action'],[9,11,'action']]
    gains={(0,2):9.,(0,3):8.,(1,2):8.,(1,3):.1}
    return {'g1_objects':objects,'binding_edges':[],
        'pair_margins':[[i,j,[gains.get((i,j),-5.),-5.,-5.,-5.]]
                        for i in range(4) for j in range(4) if i!=j]}


def test_mit_is_active_and_exact_optimizer_beats_greedy():
    assert ACTIVE_VERSION=='MIT'
    record=_surface()
    spec={'method':'joint','threshold':0.,
       'out_caps':{'actor|action|owns':1},'in_caps':{'action|actor|owns':1}}
    old=greedy(record,spec)
    result,meta=decode(record,spec,return_diagnostics=True)
    assert len(old)==len(result)==2
    assert {(edge[1],edge[3]) for edge in result}=={((0,2),(9,11)),((3,5),(6,8))}
    assert meta['status']=='OPTIMAL' and abs(meta['objective_gain']-16.)<1e-9
    assert sum({(0,2):9.,(0,3):8.,(1,2):8.,(1,3):.1}[
         ((edge[1][0]//3), (edge[3][0]-6)//3+2)] for edge in old)<16


def test_mit_enforces_at_most_one_label_per_directed_pair():
    record=_surface()
    for record_item in record['pair_margins']:
        if record_item[:2]==[0,2]:record_item[2][:2]=[6.,5.]
    spec={'method':'joint','threshold':0.,'out_caps':{},'in_caps':{}}
    selected,diagnostic=decode(record,spec,return_diagnostics=True)
    assert sum(e[1]==(0,2) and e[3]==(6,8) for e in selected)==1
    assert diagnostic['status']=='OPTIMAL'
    assert len(candidate_edges(record,spec)[1])>=5


def test_sparse_weight_edit_is_limited_to_g2_selected_coordinates():
    torch.manual_seed(2)
    head=GamerGraphHead(hidden_size=32,width=16,layers=1,
                        latent_slots=2,memory_slots=2,max_span_tokens=4)
    before={name:p.detach().clone() for name,p in head.named_parameters()}
    spans=[{'kind':kind,'token_first':i,'token_last':i} for i,kind in
       enumerate(('actor','action','action','quantity'))]
    targets=torch.zeros((4,4),dtype=torch.long)
    targets[0,1]=1
    targets[0,2]=1
    item={'hidden':torch.randn(8,32), 'eligible':torch.ones(8,dtype=torch.bool),
          'spans':spans,'targets':targets}
    masks,detail=sparse_train(head,[item],device='cpu',steps=3,lr=.001,fraction=.01)
    after=dict(head.named_parameters())
    count=0
    for name,p in after.items():
        if name in masks:
            assert torch.equal(p.detach()[~masks[name]],before[name][~masks[name]])
            assert float((p.detach()-before[name]).abs().max()) <=.030001
            count+=int(torch.count_nonzero(p.detach()!=before[name]))
        else:
            assert torch.equal(p.detach(),before[name])
    assert 0<count<=detail['selected_coefficients']
    assert detail['actually_changed']==count
