"""CPU-only assertions for G1-0073 learned-interface competition."""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import torch

from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as m
from tools import nanojev_gamer_v1_g1_complete_object_bakeoff_0072 as complete
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS


def example_logits():
    logits=torch.full((6,6,1+len(SPAN_KINDS)),-15.,dtype=torch.float32)
    logits[...,0]=0.
    valid=torch.zeros((6,6),dtype=torch.bool)
    def add(a,b,**classes):
        valid[a,b-a]=True
        for k,v in classes.items():logits[a,b-a,SPAN_KINDS.index(k)+1]=v
    add(0,1,actor=4.)
    add(2,3,action=2.2)
    add(2,2,action=3.)
    add(3,3,action=3.)
    add(4,4,quantity=4.)
    return logits,valid,{'offsets':[(i,i+1) for i in range(6)],
                         'prompt_start':0,'prompt':'abcdef'}


def make_case(prompt_id):
    f1=(1.,.4,.8,.2,.2,.2,1.,0.,1.,.5,1.)
    f2=(1.,-.4,-.5,.2,.2,.2,1.,0.,0.,.1,1.)
    pos=m.Option(0,1,m.ACTOR,1.2,f1,(0,1,'actor'),True)
    neg=m.Option(0,1,m.ACTOR,-.3,f2,(0,1,'action'),False)
    third=m.Option(2,3,m.ACTION,.8,(1.,.5,.6,.2,.2,.2,0.,1.,1.,.5,1.),
                   (2,3,'action'),True)
    return m.Case('cutover','train','encounter',str(prompt_id),4,[pos,neg,third],
                  {(0,1,'actor'),(2,3,'action')},
                  {(0,1,'actor'),(2,3,'action')},[])


def test_fixed_arm_contracts_and_no_gold_arg_on_inference():
    assert m.ARMS==(m.CONTROL,'pointwise_admission','pairwise_competition',
                    'structured_interval','pointwise_pairwise_ensemble')
    assert 'gold' not in inspect.signature(m._lattice).parameters
    assert 'row' not in inspect.signature(m._lattice).parameters
    assert 'gold' not in inspect.signature(m._decode_options).parameters
    assert 'gold' not in inspect.signature(m._prediction).parameters
    assert m.DIM==11


@pytest.mark.parametrize('prior',('actor_prior_0p25','runnerup_joint_gap0p50_bonus0p50'))
def test_lattice_exact_prior_control_parity_and_no_logits_mutation(prior):
    logits,valid,prepared=example_logits()
    before=logits.clone();mask=valid.clone()
    opts,control,n=m._lattice(logits,valid,prepared,prior)
    predictions,_=complete._decode(logits,valid,prior)
    assert control==predictions['action_rescue_2p00_cond0p50']
    assert n==6 and opts
    assert all(x.char_key is None or isinstance(x.char_key,tuple) for x in opts)
    assert torch.equal(logits,before) and torch.equal(valid,mask)
    assert all(len(x.features)==m.DIM for x in opts)
    assert len(opts)==len({(x.start,x.end,x.kind) for x in opts})


def test_decoder_global_nonoverlap_and_override():
    f=(1.,)*m.DIM
    options=[m.Option(0,2,m.ACTION,2.,f,(0,2,'action'),True),
             m.Option(0,1,m.ACTION,1.,f,(0,1,'action'),True),
             m.Option(1,2,m.ACTOR,1.,f,(1,2,'actor'),True)]
    assert m._decode_options(options,2,[0.]*m.DIM)==[0]
    assert m._decode_options(options,2,[0.]*m.DIM,loss_aug_gold={(0,2,'action')},
                             loss_margin=.4)==[0]
    with pytest.raises(ValueError,match='INVALID_INTERFACE_WEIGHTS'):
        m._decode_options(options,2,[float('nan')]*m.DIM)
    with pytest.raises(ValueError,match='INVALID_LATTICE'):
        m._decode_options([m.Option(2,2,m.ACTION,1.,f,None,True)],3)


def test_three_distinct_learners_produce_finite_deterministic_weights():
    train=[make_case(i) for i in range(8)]
    a=m._competitors(train)
    b=m._competitors(train)
    assert a==b
    assert set(a)==set(m.ARMS)
    for name in m.ARMS[1:]:
        assert len(a[name])==m.DIM
        assert all(abs(x)<100 for x in a[name])
    assert a['pointwise_admission']!=a['pairwise_competition']
    assert a['pairwise_competition']!=a['structured_interval']
    assert a['pointwise_pairwise_ensemble']==tuple(
        .5*(x+y) for x,y in zip(a['pointwise_admission'],a['pairwise_competition']))


def test_labels_change_training_but_cannot_change_inference():
    cases=[make_case(i) for i in range(6)]
    learned=m._train_pointwise(cases,steps=25)
    changed=copy.deepcopy(cases)
    for item in changed:item.gold={(0,1,'action')}
    alternate=m._train_pointwise(changed,steps=25)
    assert learned!=alternate
    for a,b in zip(cases,changed):
        assert m._prediction(a,'pointwise_admission',learned)==m._prediction(
            b,'pointwise_admission',learned)


def test_partition_is_stratified_and_nonoverlapping():
    rows=[{'family':family,'prompt':f'{family}-{i}'}
          for family in ('encounter','allocation') for i in range(40)]
    groups=m._partition(rows)
    assert {k:len(v) for k,v in groups.items()}=={'train':48,'selection':16,'audit':16}
    assert groups==m._partition(rows)
    assert len({x['prompt'] for rows in groups.values() for x in rows})==80
    for split in groups:
        assert sum(r['family']=='encounter' for r in groups[split])==len(groups[split])//2
    with pytest.raises(ValueError,match='PARTITION_LEAK'):
        m._partition(rows+[rows[0]])


def test_selection_fails_closed_and_accepts_clean_gain():
    def metrics(exact,correct=8,fp=1,actor=1.):
        return {'exact_prompts':exact,'f1':correct/10.,'precision':1.-fp/10.,
                'by_kind':{k:{'recall':actor if k=='actor' else 1.}
                           for k in m.REQUIRED_KINDS}}
    base=metrics(5)
    v={name:base for name in m.ARMS}
    assert m._select(v)==m.CONTROL
    v['pointwise_admission']=metrics(6,correct=9,fp=1)
    assert m._select(v)=='pointwise_admission'
    v['pointwise_admission']=metrics(8,correct=9,fp=1,actor=.75)
    assert m._select(v)==m.CONTROL


def test_actual_0072_reference_contract_matches_uploaded_report(tmp_path):
    source=Path('/mnt/data/oracle-fabric-g1-complete-object-bakeoff-0001.zip')
    if not source.exists():pytest.skip('Prior report not mounted')
    ref=tmp_path/'reference';ref.mkdir()
    with ZipFile(source) as z:
        (ref/'report.json').write_bytes(z.read('oracle-fabric-g1-complete-object-bakeoff-0001/report.json'))
    report=json.loads((ref/'report.json').read_text())
    entries={key:{'manifest':{'head_sha256':value}}
             for key,value in report['lineage']['checkpoint_sha256'].items()}
    observed,h=m._require_complete_reference(ref,entries,report['lineage']['dataset_sha256'],
                                               {'dev':[None]*80,'challenge':[None]*32})
    assert observed==report and len(h)==64
    for split in ('dev','challenge'):
        control={name:{m.CONTROL:report['models'][name][
            'dev' if split=='dev' else 'historical_challenge']['selected_metrics']}
                 for name in entries}
        m._parity(report,split,control)
        modified=copy.deepcopy(control)
        modified['cutover'][m.CONTROL]['predicted']+=1
        with pytest.raises(ValueError,match='CONTROL_METRIC_DRIFT'):
            m._parity(report,split,modified)


def test_no_mutation_or_certification_hooks():
    source=inspect.getsource(m.run)
    assert 'optimizer.step(' not in source
    assert "'production_promotion':False" in source
    assert "'certification_authorized':False" in source
    assert "'g2_unlocked':False" in source
    assert "'g1_head_weights_modified':False" in source
    assert "'interface_training_performed':True" in source
