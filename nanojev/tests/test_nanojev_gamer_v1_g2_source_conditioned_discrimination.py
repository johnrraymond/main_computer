"""G2-0013: multi-positive source-conditioned ranking, safe replay and screening."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import random
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as m
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES
from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation


def fixture():
    a=(0,2,'actor'); b=(10,13,'action'); c=(25,28,'action'); d=(31,35,'action')
    r='owns'
    gold={(a,b,r),(a,c,r)}
    anchors=[a,b,c,d]
    rank=torch.zeros(4,4,len(BIND_CLASSES))
    k=BIND_CLASSES.index(r)
    rank[0,1,k]=4.
    rank[0,2,0]=2.;rank[0,2,k]=1.2 # genuine relationship originally labeled none
    rank[0,3,k]=5. # stronger distractor than the correct relationships
    case={'index':1,'family':'encounter','anchors':anchors,'gold':gold,
          'vectors':torch.randn(4,16),'rank':rank,
          'prompt_sha256':'a'*64}
    allowed={(r,'actor','action')}
    case['allowed']=allowed
    return case,rank,allowed,gold


def test_cli_exposes_source_conditioned_command():
    out=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-source-conditioned-discrimination','--help'],
        capture_output=True,text=True,cwd=Path(__file__).resolve().parents[1],timeout=30)
    assert out.returncode==0,out.stderr
    assert '--diagnosis-run' in out.stdout and '--steps' in out.stdout


def test_fresh_dev_prompt_disjoint_and_balanced():
    previous=[[encounter(random.Random(i),'dev',i),
               allocation(random.Random(100+i),'dev',i)] for i in range(3)]
    out=m.new_validation_rows(previous,limit=12)
    assert Counter(x['family'] for x in out)=={'encounter':6,'allocation':6}
    assert not {base._sha(x['prompt']) for chunk in previous for x in chunk} & \
        {base._sha(x['prompt']) for x in out}
    with pytest.raises(ValueError,match='BAD_COHORT'):
        m.new_validation_rows(previous,limit=3)


def test_null_argmax_is_candidate_without_gold_leakage():
    case,rank,allowed,gold=fixture()
    a,b,c,d=case['anchors'];r='owns'
    inv=m.make_inventory(case,rank,allowed,top_k=3)
    assert (a,c,r) in inv['edges'] and inv['null_argmax'][inv['edges'].index((a,c,r))]
    assert (a,b,r) in inv['edges'] and (a,d,r) in inv['edges']
    assert len(inv['edges'])==3 and inv['train_positive_groups']==0
    with pytest.raises(ValueError,match='BAD_INVENTORY'):
        m.make_inventory(case,rank,allowed,top_k=1)
    with pytest.raises(ValueError,match='GOLD_IN_INFERENCE'):
        m.make_inventory({**case,'force_training_gold':True},rank,allowed)


def test_train_gold_injection_is_train_only():
    case,rank,allowed,gold=fixture()
    a,b,c,d=case['anchors']
    without=m.make_inventory(case,rank,allowed,top_k=2)
    with_gold=m.make_inventory(case,rank,allowed,top_k=2,train_gold=True)
    assert (a,c,'owns') not in without['edges']
    assert (a,c,'owns') in with_gold['edges']
    assert with_gold['train_positive_groups']==1
    sampled=m.training_groups(with_gold,gold,max_groups=4)
    assert sampled and any((a,c,'owns')==with_gold['edges'][i] for _,ids in sampled for i in ids)


def test_forward_is_multi_positive_and_context_is_nonexclusive():
    case,rank,allowed,gold=fixture()
    inv=m.make_inventory(case,rank,allowed,top_k=3)
    joint=m.SourceConditionedScorer(16)
    scores=joint(inv['features'],inv['base'],inv['groups'])
    assert scores.shape==(len(inv['edges']),) and torch.isfinite(scores).all()
    # No softmax over destination choices. Independent logits can all be high.
    assert m.decode_scores(inv,torch.full_like(scores,10.),.7)==set(inv['edges'])
    assert m.decode_scores(inv,torch.full_like(scores,-10.),.7)==set()
    assert joint(inv['features'],inv['base'],inv['groups'],with_competition=False).shape==scores.shape
    with pytest.raises(ValueError,match='UNSORTED_GROUPS'):
        joint(inv['features'],inv['base'],list(reversed(inv['groups']))) if len(set(inv['groups']))>1 else \
            joint(torch.cat((inv['features'],inv['features'])),torch.cat((inv['base'],inv['base'])),[1]*len(scores)+[0]*len(scores))


def test_two_models_train_with_hard_null_positive_and_distractor():
    case,rank,allowed,gold=fixture()
    second={**case,'index':2,'family':'allocation','prompt_sha256':'b'*64}
    net=m.SourceConditionedScorer(16)
    control=m.SourceConditionedScorer(16)
    control.load_state_dict(net.state_dict())
    result=m.train_pair(net,control,[case,second],steps=2,device='cpu')
    assert result['steps']==2 and result['samples']['positive']>0
    assert result['samples']['hard_null_positive']>0
    assert result['samples']['negative']>0
    assert not any(p.requires_grad for p in net.parameters())
    assert not any(p.requires_grad for p in control.parameters())
    assert set(result['last_loss'])=={'joint','control'}


def test_recall_and_crowded_gates_are_required():
    def score(f1,recall,fp):return {'f1':f1,'recall':recall,'false_positive':fp,'cases':320}
    metrics={m.PARENT:score(.4,.63,200),
             'joint_50':score(.44,.625,180),'control_50':score(.41,.625,180)}
    crowd={'crowded_49_128':{m.PARENT:{**score(.3,.5,90),'cases':38},
                                 'joint_50':{**score(.34,.48,80),'cases':38}}}
    # Rule is just inside the 2-point crowded recall bound.
    gates=m.evaluation_gate(metrics,crowd,'joint_50')
    assert all(gates.values())
    crowd['crowded_49_128']['joint_50']['recall']=.479
    assert not m.evaluation_gate(metrics,crowd,'joint_50')['crowded_recall_at_least_parent_minus_002']


def test_calibration_selection_never_looks_at_independent():
    def score(f1,recall,fp):return {'f1':f1,'recall':recall,'false_positive':fp,'cases':320}
    metrics={m.PARENT:score(.4,.63,200)}
    crowd={'crowded_49_128':{m.PARENT:{**score(.3,.5,90),'cases':40}}}
    for t in m.THRESHOLDS:
        t=int(t*100)
        metrics[f'joint_{t:02d}']=score(.41+t/10000,.625,180)
        metrics[f'control_{t:02d}']=score(.40,.625,190)
        crowd['crowded_49_128'][f'joint_{t:02d}']={**score(.32,.49,80),'cases':40}
    chosen,info,_=m.choose_rule(metrics,crowd)
    assert chosen=='joint_80' and info['eligible_count']==4
    metrics['joint_80']['recall']=.1
    assert m.choose_rule(metrics,crowd)[0]=='joint_70'


def test_no_silent_inference_new_edges_from_gold():
    case,rank,allowed,gold=fixture()
    inferred=m.make_inventory(case,rank,allowed,top_k=2)
    assert len(set(inferred['edges']))==len(inferred['edges'])
    # A gold edge not in the evidence-driven inventory cannot appear in decoder output.
    a,b,c,d=case['anchors']
    selected=m.decode_scores(inferred,torch.full((len(inferred['edges']),),20.),.5)
    assert (a,c,'owns') not in selected


def test_parent_0012_synthetic_tamper_fails_closed(tmp_path):
    case,_,_,_=fixture()
    original={'prompt':'example','family':'encounter','graph':{}}
    d=tmp_path
    (d/m.diagnosis12.REPORT).write_text(json.dumps({'schema':'invalid'}))
    (d/m.diagnosis12.CASES).write_text('')
    with pytest.raises(ValueError,match='PARENT_CONTRACT'):
        m.verify_diagnosis_parent(d,[original],{})


def test_full_cpu_runner_with_mocked_legacy_lineage(monkeypatch,tmp_path):
    """Exercise the real 0013 scoring/output path with a small upstream fixture."""
    from test_nanojev_gamer_v1_g2_crowded_recall_calibration import _fixture as parent_fixture
    from tools import nanojev_gamer_v1_g2_crowded_recall_calibration as crowd10
    from tools import nanojev_gamer_v1_g2_robustness_validation as robust11
    args,seen=parent_fixture(monkeypatch,tmp_path)
    monkeypatch.setattr(crowd10,'_gates',lambda *_args: {'mocked_legacy_gate':True})
    monkeypatch.setattr(crowd10,'choose_rule',lambda *_args:
        (robust11.SELECTED,{'reason':'TEST_PRESELECTED'},
         {name:{'mocked_legacy_gate':True} for name in m.recall9._rule_names()}))
    monkeypatch.setattr(crowd10,'VALIDATION_LIMIT',4)
    crowd10.run(**args)
    robust_dir=tmp_path/'robustness';robust_dir.mkdir()
    (robust_dir/robust11.REPORT).write_text(json.dumps({'cohort_limit':4}))
    monkeypatch.setattr(robust11,'COHORT_LIMIT',4)
    monkeypatch.setattr(m,'CALIBRATION_LIMIT',4)
    monkeypatch.setattr(m.diagnosis12,'verify_robust11_parent',
                        lambda *a,**kw: ({'robustness_pass':True},{'mocked_parent11':'toy'}))
    records=[]
    monkeypatch.setattr(m,'verify_diagnosis_parent',
                        lambda *a,**kw: ({'diagnosis_ok':True},records,{'mocked_parent12':'toy'}))
    monkeypatch.setattr(m,'train_pair',lambda *a,**kw: {'steps':1,'synthetic_training_tested_elsewhere':True})
    real_evaluate=m.evaluate
    def checked_evaluate(*a,**kw):
        answer=real_evaluate(*a,**kw)
        if kw['group']=='previously_inspected_calibration':
            records.extend({'configurations':row['configurations']} for row in answer[1])
        return answer
    monkeypatch.setattr(m,'evaluate',checked_evaluate)
    inputs={k:args[k] for k in ('root','g1_validation_report','g2_run','selection_run',
        'discrimination_run','relational_run','density_run','consistency_run',
        'context_run','learned_run','recall_run')}
    inputs.update(crowded_run=args['output'],robustness_run=robust_dir,
                  diagnosis_run=tmp_path/'diagnosis',output=tmp_path/'0013',
                  device='cpu',validation_limit=4,steps=1)
    result=m.run(**inputs)
    assert result['independent_cases']==4 and result['calibration_cases']==4
    assert not result['g2_certified'] and not result['graph_execution_authorized']
    assert result['source_conditioned_multi_positive'] and result['no_gold_in_inference']
    assert (inputs['output']/m.WEIGHTS).is_file()
    assert len((inputs['output']/m.CASES).read_text().splitlines())==8
    assert result['new_weights_sha256']
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**inputs)
