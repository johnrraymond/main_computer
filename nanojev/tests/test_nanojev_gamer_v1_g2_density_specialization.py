"""G2-0014: frozen source lineage, TRAIN-only specialist and routing gates."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import random
import subprocess
import sys

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_density_specialization as m
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as prior
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES


def test_cli_exposes_density_specialization():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
         'frob-g2-density-specialization','--help'],capture_output=True,text=True,
         cwd=Path(__file__).resolve().parents[1],timeout=30)
    assert result.returncode==0,result.stderr
    assert '--source-run' in result.stdout and '--steps' in result.stdout


def test_disjoint_balanced_fresh_dev():
    previous=[[encounter(random.Random(i),'dev',i),allocation(random.Random(100+i),'dev',i)]
              for i in range(3)]
    fresh=m.fresh_rows(previous,limit=12)
    assert Counter(row['family'] for row in fresh)=={'encounter':6,'allocation':6}
    assert not {base._sha(x['prompt']) for cohort in previous for x in cohort}&\
        {base._sha(x['prompt']) for x in fresh}
    with pytest.raises(ValueError,match='BAD_HOLDOUT'):
        m.fresh_rows(previous,limit=3)


def dense_train_fixture(index=1):
    anchors=[(i*8,i*8+2,'actor' if i==0 else 'action') for i in range(50)]
    rank=torch.zeros(50,50,len(BIND_CLASSES))
    rel=BIND_CLASSES.index('owns')
    rank[0,1,rel]=5.;rank[0,2,rel]=4.;rank[0,3,rel]=6.
    rank[0,10,rel]=8.;rank[0,25,rel]=7.
    gold={(anchors[0],anchors[1],'owns'),(anchors[0],anchors[2],'owns')}
    return {'index':index,'family':'encounter','anchors':anchors,'gold':gold,
            'vectors':torch.randn(50,16),'rank':rank,'allowed':{('owns','actor','action')},
            'prompt_sha256':str(index)*64}


def test_crowded_hard_negatives_include_competing_distant_and_all_positives():
    case=dense_train_fixture()
    inv=prior.make_inventory(case,case['rank'],case['allowed'],train_gold=True)
    sampled=m.hard_crowded_groups(inv,case['gold'],seed=47)
    assert sampled
    indices={i for _,ids in sampled for i in ids}
    edges={inv['edges'][i] for i in indices}
    assert case['gold'] <= edges
    assert any(edge not in case['gold'] and abs(edge[0][0]-edge[1][0])>128 for edge in edges)
    assert (case['anchors'][0],case['anchors'][10],'owns') in edges


def test_crowded_training_fine_tunes_only_new_models():
    cases=[dense_train_fixture(i) for i in range(1,5)]
    initial=prior.SourceConditionedScorer(16)
    a=prior.SourceConditionedScorer(16);a.load_state_dict(initial.state_dict())
    b=prior.SourceConditionedScorer(16);b.load_state_dict(initial.state_dict())
    result=m.train_specialists(a,b,cases,steps=2,device='cpu')
    assert result['crowded_train_cases']==4
    assert result['samples']['distant_negative']>0
    assert result['samples']['positive']>0
    assert not any(p.requires_grad for p in a.parameters())
    assert not any(p.requires_grad for p in b.parameters())
    assert any(not torch.equal(a.state_dict()[k],initial.state_dict()[k]) for k in a.state_dict())
    with pytest.raises(ValueError,match='INSUFFICIENT_TRAIN_CROWDED'):
        m.train_specialists(a,b,cases[:3],steps=1,device='cpu')


def test_specialist_does_not_enforce_one_destination_and_medium_is_exact_fallback():
    case=dense_train_fixture()
    inv=prior.make_inventory(case,case['rank'],case['allowed'])
    a,b=case['anchors'][:2]
    old={(a,b,'owns')}
    scores=torch.full((len(inv['edges']),),8.)
    added=m.specialist_edges(old,inv,scores,'rescue_90')
    assert old<=added and len(added)>2
    assert set(added) <= set(inv['edges'])|old
    assert m.specialist_edges(old,inv,torch.full_like(scores,-8.),'prune_60')==set()
    assert m.specialist_edges(old,inv,scores,'fallback')==old
    assert m.route_edges(m.BUCKET_MEDIUM,old,inv,scores,scores,scores,scores,'joint_50','rescue_90')==old
    assert len(m.route_edges(m.BUCKET_SMALL,old,inv,scores,scores,scores,scores,
                            'joint_50','prune_60'))>1


def test_routing_selection_requires_crowded_improvement_and_recall():
    def score(f1,recall,fp,cases=320):
        return {'f1':f1,'recall':recall,'false_positive':fp,'cases':cases}
    p=m.PARENT
    a=m.route_name('joint_50','prune_60');ctrl='control__'+a
    metrics={p:score(.42,.63,200),a:score(.445,.625,160),ctrl:score(.42,.625,180)}
    buckets={m.BUCKET_SMALL:{p:score(.56,.82,60,100),a:score(.62,.84,40,100)},
             m.BUCKET_MEDIUM:{p:score(.41,.61,100,180),a:score(.41,.61,100,180)},
             m.BUCKET_CROWDED:{p:score(.31,.50,90,40),a:score(.33,.49,70,40)}}
    assert all(m.route_gates(metrics,buckets,a,ctrl).values())
    buckets[m.BUCKET_CROWDED][a]['recall']=.479
    assert not m.route_gates(metrics,buckets,a,ctrl)['crowded_recall_loss_at_most_002']
    buckets[m.BUCKET_CROWDED][a]['recall']=.49
    assert m.route_gates(metrics,buckets,a,ctrl)['medium_route_exact_fallback']


def test_calibration_selection_uses_only_calibration_metrics():
    def score(f,r,fp,cases=320):return {'f1':f,'recall':r,'false_positive':fp,'cases':cases}
    p=m.PARENT
    metrics={p:score(.42,.63,200)}
    buckets={m.BUCKET_SMALL:{p:score(.56,.82,100)},
             m.BUCKET_MEDIUM:{p:score(.41,.62,100)},
             m.BUCKET_CROWDED:{p:score(.31,.5,90,40)}}
    for si,small in enumerate(m.SMALL_POLICIES):
        for ci,crowd in enumerate(m.CROWD_POLICIES):
            if small=='fallback' and crowd=='fallback':continue
            name=m.route_name(small,crowd)
            metrics[name]=score(.45+si*.001+ci*.0001,.627,170)
            metrics['control__'+name]=score(.43,.625,180)
            buckets[m.BUCKET_SMALL][name]=score(.6,.8,80)
            buckets[m.BUCKET_CROWDED][name]=score(.33,.49,70,40)
    chosen,info,gates=m.select_route(metrics,buckets)
    assert chosen==m.route_name('joint_70','rescue_95')
    assert info['eligible_count']==len(m.SMALL_POLICIES)*len(m.CROWD_POLICIES)-1
    metrics[chosen]['recall']=.01
    assert m.select_route(metrics,buckets)[0]!=chosen


def test_small_only_routing_preserves_crowded_fallback():
    def score(f1,r,fp,cases=320):return {'f1':f1,'recall':r,'false_positive':fp,'cases':cases}
    name=m.route_name('joint_50','fallback')
    met={m.PARENT:score(.42,.63,200),name:score(.44,.635,180),
         'control__'+name:score(.425,.63,190)}
    buckets={m.BUCKET_SMALL:{m.PARENT:score(.55,.82,80),name:score(.62,.84,60)},
             m.BUCKET_MEDIUM:{m.PARENT:score(.41,.61,90),name:score(.41,.61,90)},
             m.BUCKET_CROWDED:{m.PARENT:score(.31,.5,60,40),name:score(.31,.5,60,40)}}
    assert all(m.route_gates(met,buckets,name,'control__'+name).values())
    buckets[m.BUCKET_CROWDED][name]['f1']=.30
    assert not m.route_gates(met,buckets,name,'control__'+name)['crowded_f1_preserved_or_improved']


def test_frozen_parent_weight_loader_validates_hash_and_shape(tmp_path):
    source=prior.SourceConditionedScorer(16)
    data={**{'joint.'+k:v.detach().clone().contiguous() for k,v in source.state_dict().items()},
          **{'control.'+k:v.detach().clone().contiguous() for k,v in source.state_dict().items()}}
    path=tmp_path/'weights.safetensors';save_file(data,str(path))
    models=m.load_source13_models(path,16,m.backbone.sha256(path))
    assert set(models)=={'joint','control'}
    assert all(not p.requires_grad for net in models.values() for p in net.parameters())
    with pytest.raises(ValueError,match='PARENT_WEIGHT_HASH'):
        m.load_source13_models(path,16,'0'*64)
    with pytest.raises(ValueError,match='PARENT_WEIGHT_SHAPE'):
        m.load_source13_models(path,20,m.backbone.sha256(path))


def test_parent_contract_rejects_tampering(tmp_path):
    (tmp_path/prior.REPORT).write_text(json.dumps({'schema':'wrong'}))
    (tmp_path/prior.CASES).write_text('')
    (tmp_path/prior.WEIGHTS).write_bytes(b'x')
    with pytest.raises(ValueError,match='PARENT_CONTRACT'):
        m.verify_source13_parent(tmp_path,[{'prompt':'example','family':'encounter'}],{})


def test_full_cpu_runner_uses_preselected_route_only(monkeypatch,tmp_path):
    from test_nanojev_gamer_v1_g2_crowded_recall_calibration import _fixture as base_fixture
    from tools import nanojev_gamer_v1_g2_crowded_recall_calibration as crowd10
    from tools import nanojev_gamer_v1_g2_robustness_validation as robust11
    args,_=base_fixture(monkeypatch,tmp_path)
    monkeypatch.setattr(crowd10,'_gates',lambda *_: {'mocked':True})
    monkeypatch.setattr(crowd10,'choose_rule',lambda *_:
        (robust11.SELECTED,{'reason':'PRESELECTED'},
         {name:{'mocked':True} for name in m.recall9._rule_names()}))
    monkeypatch.setattr(crowd10,'VALIDATION_LIMIT',4)
    crowd10.run(**args)
    parent11=tmp_path/'robustness';parent11.mkdir()
    (parent11/robust11.REPORT).write_text(json.dumps({'cohort_limit':4}))
    monkeypatch.setattr(robust11,'COHORT_LIMIT',4)
    monkeypatch.setattr(m,'CALIBRATION_LIMIT',4)
    monkeypatch.setattr(m.diagnosis12,'verify_robust11_parent',
                        lambda *a,**kw: ({'pass':True},{'parent11':'toy'}))
    monkeypatch.setattr(m.source13,'verify_diagnosis_parent',
                        lambda *a,**kw: ({'pass':True},[],{'parent12':'toy'}))
    monkeypatch.setattr(m,'verify_source13_parent',
                        lambda *a,**kw: ({'new_weights_sha256':'toy'},[],{'parent13':'toy'}))
    monkeypatch.setattr(m,'load_source13_models',lambda path,width,sha:
                        {'joint':prior.SourceConditionedScorer(width),
                         'control':prior.SourceConditionedScorer(width)})
    monkeypatch.setattr(m,'train_specialists',lambda *a,**kw:{'steps':1,'test_only':True})
    inputs={k:args[k] for k in ('root','g1_validation_report','g2_run','selection_run',
        'discrimination_run','relational_run','density_run','consistency_run',
        'context_run','learned_run','recall_run')}
    inputs.update(crowded_run=args['output'],robustness_run=parent11,
                  diagnosis_run=tmp_path/'diagnosis',source_run=tmp_path/'source13',
                  output=tmp_path/'g2-0014',device='cpu',validation_limit=4,steps=1)
    result=m.run(**inputs)
    assert result['calibration_cases']==4 and result['independent_cases']==4
    assert result['selected_route'] is None and not result['independent_screen_pass']
    assert not result['g2_certified'] and not result['graph_execution_authorized']
    assert result['g2_0013_parent_replay_verified']
    assert len((inputs['output']/m.CASES).read_text().splitlines())==8
    assert (inputs['output']/m.WEIGHTS).is_file()
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**inputs)
