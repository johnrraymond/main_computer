"""G2-0016: general scorer, multiple-positive labels, withheld topology, safe gates."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import copy
import random
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_general_relational_discrimination as m
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_density_specialization as specialized14


def test_cli_exposes_general_discrimination_command():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-general-relational-discrimination','--help'],
        cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    assert '--routing-robustness-run' in result.stdout and '--steps' in result.stdout


def predecessor():
    return [corpus.encounter(random.Random(18),'train',18),
            corpus.allocation(random.Random(19),'train',19)]


def test_extended_train_and_ood_are_authored_and_disjoint():
    prior=[predecessor()]
    train=m.curriculum_rows(prior,limit=60)
    ood=m.heldout_topology_rows((*prior,train),limit=16)
    assert Counter(r['structural_topology'] for r in train)=={
        '3x3':12,'3x4':12,'4x3':12,'allocation':12,'2x2':4,'2x3':4,'3x2':4}
    assert Counter(r['structural_topology'] for r in ood)=={'4x4':8,'4x2':4,'2x4':4}
    assert all(r['split']=='train' and r['annotation_provenance']=='AUTHOR_MARKED_IN_SOURCE_GENERATOR'
               for r in train)
    assert all(r['split']=='dev' and r['annotation_provenance']=='AUTHOR_MARKED_IN_SOURCE_GENERATOR'
               for r in ood)
    assert len({base._sha(r['prompt']) for r in [*prior[0],*train,*ood]})==78
    assert len({r['template'] for r in ood})==4
    assert m.curriculum_rows(prior,limit=60)==train
    assert m.heldout_topology_rows((*prior,train),limit=16)==ood
    with pytest.raises(ValueError,match='BAD_TRAIN_CURRICULUM'):
        m.curriculum_rows(prior,limit=61)
    with pytest.raises(ValueError,match='BAD_OOD_CONFIG'):
        m.heldout_topology_rows(prior,limit=15)


def test_training_sampler_accepts_multiple_true_destinations_and_far_negatives():
    src=(0,4,'quantity')
    edges=[(src,(100+j*35,104+j*35,'action'),'applies_to') for j in range(7)]
    inv={'edges':edges,'base':torch.tensor([.3,.2,.9,.8,.7,.6,.5]),'groups':[0]*7}
    sampled=m.sample_hard_groups(inv,{edges[0],edges[1]},seed=6,max_neg=6)
    assert sampled and len(sampled)==1
    indices=sampled[0][1]
    assert 0 in indices and 1 in indices and 2 in indices
    assert any(abs(edges[i][0][0]-edges[i][1][0])>128 for i in indices)
    assert m.sample_hard_groups(inv,set(),seed=1)==[]


def fake_training_case(index,topology,family,*,width=8):
    src=(0,4,'actor')
    dst=[(10+i*18,14+i*18,'action') for i in range(5)]
    edges=[(src,d,'owns') for d in dst]
    groups=[0]*len(edges)
    inputs=torch.randn(len(edges),4*width+source13.EXTRAS)
    return {'index':index,'rank':torch.empty(0),'allowed':set(),
            'family':family,'structural_topology':topology,'split':'train',
            'gold':{edges[0],edges[2]},'fake_inventory':{
                'features':inputs,'base':torch.tensor([.8,.7,.6,.5,.4]),
                'groups':groups,'edges':edges,'null_argmax':[1,0,0,0,0]}}


def test_train_general_and_matched_crowd_models(monkeypatch):
    torch.manual_seed(7)
    cases=[fake_training_case(i,'3x3','encounter') for i in range(12)]
    cases.extend(fake_training_case(i+12,'native','allocation') for i in range(3))
    monkeypatch.setattr(m.source13,'make_inventory',lambda row,*args,**kwargs:row['fake_inventory'])
    general=source13.SourceConditionedScorer(8)
    specialist=source13.SourceConditionedScorer(8)
    specialist.load_state_dict(copy.deepcopy(general.state_dict()))
    original=copy.deepcopy(general.state_dict())
    stats=m.train_general_and_crowd(general,specialist,cases,steps=2,device='cpu')
    assert stats['crowd_only_distinct_cases']==12
    assert stats['general_training_cases']==15
    assert stats['samples']['general_hard_negative']>0
    assert stats['samples']['general_null_positive']>0
    assert stats['samples']['crowd_only_hard_negative']>0
    assert any(not torch.equal(original[k],general.state_dict()[k]) for k in original)
    assert any(not torch.equal(general.state_dict()[k],specialist.state_dict()[k]) for k in original)
    assert all(not p.requires_grad for p in general.parameters())
    cases[0]['split']='dev'
    with pytest.raises(ValueError,match='NONTRAIN_SUPERVISION'):
        m.train_general_and_crowd(general,specialist,cases,steps=1)


def _metrics(tp,fp,fn,cases=32):
    return {'correct':tp,'false_positive':fp,'missing':fn,'cases':cases,
            'precision':tp/(tp+fp),'recall':tp/(tp+fn),'f1':2*tp/(2*tp+fp+fn)}


def test_calibration_uses_predeclared_cross_density_gates():
    parent=_metrics(400,300,200)
    stronger=_metrics(415,260,185)
    table={name:parent.copy() for name in m.NAMES}
    table[m.PARENT]=parent
    table['general_60']=stronger
    by_density={bucket:{name:parent.copy() for name in m.NAMES} for bucket in m.BUCKETS}
    for bucket in m.BUCKETS:
        by_density[bucket]['general_60']=_metrics(105,65,45)
        by_density[bucket][m.PARENT]=_metrics(100,75,50)
    choice,detail,gates=m.screen_calibration(table,by_density)
    assert choice=='general_60' and detail['eligible_count']==1
    assert all(gates[choice].values())
    by_density[specialized14.BUCKET_CROWDED]['general_60']=_metrics(92,60,58)
    choice,detail,gates=m.screen_calibration(table,by_density)
    assert choice is None
    assert not gates['general_60']['crowded_f1_improves']


def test_holdout_gate_cannot_select_or_rescue_failed_calibration():
    old=_metrics(100,80,50)
    new=_metrics(110,60,40)
    met={m.PARENT:old,'general_60':new,'crowd_only_60':old}
    by_density={bucket:{m.PARENT:old,'general_60':new} for bucket in m.BUCKETS}
    ood={'4x4':met,'4x2':met,'2x4':met}
    assert not m.screen_independent(None,met,by_density,met,ood)['independent_and_ood_pass']
    assert m.screen_independent('general_60',met,by_density,met,ood)['independent_and_ood_pass']
    failed=copy.deepcopy(ood)
    failed['4x4']['general_60']=_metrics(80,95,70)
    outcome=m.screen_independent('general_60',met,by_density,met,failed)
    assert not outcome['independent_and_ood_pass']
    assert not outcome['ood_4x4_f1_not_worse']


def test_case_ledger_rejects_forgery_and_wrong_split():
    source=corpus.allocation(random.Random(13),'dev',13)
    gold=len(base.gold_binding_edges(source))
    scores={name:{'correct':0,'false_positive':4,'missing':gold,'exact':False} for name in m.NAMES}
    record={'group':'new_independent_validation','index':1,'family':'allocation',
            'prompt_sha256':base._sha(source['prompt']),
            'gold_binding_edges':gold,'g1_predicted_nodes':20,
            'density_bucket':density._bucket(20),'structural_topology':'native',
            'configurations':scores}
    m.validate_records([record],[source],'new_independent_validation')
    bad=copy.deepcopy(record);bad['configurations'][m.PARENT]['correct']=1
    with pytest.raises(ValueError,match='CASE_METRIC'):
        m.validate_records([bad],[source],'new_independent_validation')
    bad=copy.deepcopy(record);bad['prompt_sha256']='bogus'
    with pytest.raises(ValueError,match='CASE_IDENTITY'):
        m.validate_records([bad],[source],'new_independent_validation')


def test_parent_validation_fail_closed(tmp_path):
    (tmp_path/m.robust15.REPORT).write_text('{"schema":"wrong"}')
    with pytest.raises(ValueError,match='PARENT_CONTRACT'):
        m.verify_robust15_parent(tmp_path,[],tmp_path,tmp_path)


def test_cpu_frozen_evaluation_and_no_gold_in_inventory(monkeypatch):
    width=8
    src=(0,4,'actor');dsts=[(8+i*12,12+i*12,'action') for i in range(3)]
    edges=[(src,d,'owns') for d in dsts]
    gold={edges[0],edges[2]}
    anchors=[src,*dsts]+[(1000+i*10,1004+i*10,'quantity') for i in range(22)]
    captured={'anchors':anchors, 'vectors':torch.randn(len(anchors),width),
              'gold':gold,'family':'allocation','prompt_sha256':'test-prompt'}
    source={'prompt':'fake','family':'allocation','structural_topology':'native'}
    monkeypatch.setattr(m.rel,'full_logits',lambda head,vectors:torch.zeros((len(vectors),len(vectors),5)))
    monkeypatch.setattr(m.density,'candidates',lambda anchors,rank,allowed:[])
    monkeypatch.setattr(m.density,'select_edges',lambda items,rule:{edges[0]})
    monkeypatch.setattr(m.learned8,'graph_features',
        lambda anchors,vectors,old,parent,rule:(torch.ones((1,4)),torch.ones(1),[edges[0]]))
    class ConstCtx(torch.nn.Module):
        def forward(self,x,b):return x
    class ConstRet(torch.nn.Module):
        def forward(self,x,b,ctx):return torch.ones(len(x))
    def inventory(item,*args,**kwargs):
        assert not kwargs.get('train_gold',False)
        return {'features':torch.randn(3,4*width+source13.EXTRAS),
                'base':torch.tensor([.1,.2,.3]),'groups':[0,0,0],
                'edges':edges,'null_argmax':[0,0,0]}
    monkeypatch.setattr(m.source13,'make_inventory',inventory)
    model=source13.SourceConditionedScorer(width)
    rows=m.evaluate([captured],None,model,ConstCtx(),ConstRet(),model,model,
                    set(),{},device='cpu',group='new_independent_validation',
                    expected_rows=[source])
    assert len(rows)==1 and set(rows[0]['configurations'])==set(m.NAMES)
    assert rows[0]['configurations'][m.PARENT]['correct']==1
    assert rows[0]['configurations'][m.PARENT]['missing']==1
    assert rows[0]['structural_topology']=='native'
    assert not any(param.requires_grad for param in model.parameters())


def test_rank4_checkpoint_is_applied_without_changing_g1(tmp_path):
    from safetensors.torch import save_file
    class ToyHead(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.pair_projection=torch.nn.Linear(16,8)
            self.binding=torch.nn.Linear(8,5)
            self.g1=torch.nn.Linear(8,8)
    head=ToyHead()
    before=copy.deepcopy(head.g1.state_dict())
    expected=base._binding_snapshot(head)
    trained={k:torch.ones_like(v).contiguous() for k,v in expected.items()}
    path=tmp_path/'g2_rank4.safetensors'
    save_file(trained,str(path))
    h=m.backbone.sha256(path)
    m.load_frozen_rank4(head,path,h)
    assert all(torch.equal(head.state_dict()[k],v) for k,v in trained.items())
    assert all(torch.equal(head.g1.state_dict()[k],v) for k,v in before.items())
    with pytest.raises(ValueError,match='RANK4_WEIGHT_HASH'):
        m.load_frozen_rank4(head,path,'bad-hash')
    broken={**trained,'binding.weight':torch.ones(2,2)}
    save_file(broken,str(tmp_path/'bad.safetensors'))
    with pytest.raises(ValueError,match='RANK4_WEIGHT_SHAPE'):
        m.load_frozen_rank4(head,tmp_path/'bad.safetensors',m.backbone.sha256(tmp_path/'bad.safetensors'))
