"""G2-0019: train-only label-blind competitor selection and matched objective tests."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import math
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_inference_realistic_training as m
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES


def test_cli_exposes_0019_fixed_protocol_and_parent_0018():
    proc=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
       'frob-g2-inference-realistic-training','--help'],
       cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert proc.returncode==0,proc.stderr
    assert '--context18-run' in proc.stdout
    assert '--general-run' in proc.stdout
    assert '--steps' in proc.stdout
    assert m.THRESHOLD==.75 and m.TOP_K==24


def fake_inventory(groups=3,items_per=8):
    torch.manual_seed(123)
    width=8
    edges=[];ids=[]
    for g in range(groups):
        src=(g*100, g*100+2, 'action')
        for j in range(items_per):
            edges.append((src,(g*100+j*9+4,g*100+j*9+7,'action'),'owns'))
            ids.append(g)
    return {'features':torch.randn(len(ids),4*width+source13.EXTRAS),
            'base':torch.arange(len(ids),0,-1).float(), 'groups':ids,'edges':edges,
            'null_argmax':[int(i%3==0) for i in range(len(ids))]}


def test_label_blind_sampling_retains_whole_groups_and_is_repeatable():
    inv=fake_inventory(groups=10,items_per=24)
    first=m.label_blind_groups(inv,seed=77,max_groups=5)
    assert first==m.label_blind_groups(inv,seed=77,max_groups=5)
    assert len(first)==5*24
    assert set(first)==set(i for i,g in enumerate(inv['groups'])
                           if g in {inv['groups'][j] for j in first})
    assert len({inv['groups'][i] for i in first})==5
    inv2=deepcopy(inv)
    inv2['gold']={inv2['edges'][-1]}
    inv['gold']={inv['edges'][0]}
    assert m.label_blind_groups(inv2,seed=77,max_groups=5)==first
    with pytest.raises(ValueError,match='BAD_GROUP_INVENTORY'):
        m.label_blind_groups({**inv,'groups':list(reversed(inv['groups']))},seed=1)


def test_train_only_uses_unforced_top24_inventory_and_preserves_frozen_baseline():
    inv=fake_inventory(groups=2,items_per=7)
    row=[]
    for i in range(4):
        row.append({'index':i+1,'split':'train','family':('encounter','allocation')[i%2],
                    'structural_topology':'3x3' if i%2==0 else 'allocation',
                    'gold':{inv['edges'][0],inv['edges'][1],inv['edges'][-1]},
                    'rank':torch.empty(0),'allowed':set()})
    calls=[]
    def inventory_fn(item, rank, allowed, *, top_k, train_gold):
        calls.append((top_k,train_gold))
        assert top_k==24 and train_gold is False
        return inv
    base_net=source13.SourceConditionedScorer(8)
    before={k:v.detach().clone() for k,v in base_net.state_dict().items()}
    trained,stat=m.train(row,base_net,steps=2,seed=1,device='cpu',inventory_fn=inventory_fn)
    assert calls and set(calls)=={(24,False)}
    assert set(trained)=={'matched_context','precision_guard'}
    assert stat['counts']['train_positives']>0
    assert stat['counts']['train_negatives']>0
    assert stat['gold_in_candidate_construction'] is False
    assert all(torch.equal(v,base_net.state_dict()[k]) for k,v in before.items())
    assert any(not torch.equal(v,trained['matched_context'].state_dict()[k]) for k,v in before.items())
    assert any(not torch.equal(trained['matched_context'].state_dict()[k],
                               trained['precision_guard'].state_dict()[k]) for k in before)
    assert all(not p.requires_grad for net in trained.values() for p in net.parameters())
    row[0]['split']='dev'
    with pytest.raises(ValueError,match='NONTRAIN_SUPERVISION'):
        m.train(row,base_net,steps=1,inventory_fn=inventory_fn)
    row[0]['split']='train';row[0]['structural_topology']='4x4'
    with pytest.raises(ValueError,match='NONTRAIN_SUPERVISION'):
        m.train(row,base_net,steps=1,inventory_fn=inventory_fn)


def test_guard_loss_has_independent_positives_and_penalizes_high_fp():
    y=torch.tensor([1.,1.,0.,0.])
    n=torch.zeros(4);g=[0]*4
    logits=torch.tensor([2.,2.,3.,-3.],requires_grad=True)
    old=m.training_objective(logits,y,n,n,g,arm='matched_context')
    guard=m.training_objective(logits,y,n,n,g,arm='precision_guard')
    assert guard>old
    g1=torch.autograd.grad(guard,logits)[0]
    assert g1[2]>0 and g1[0]<0 and g1[1]<0
    with pytest.raises(ValueError,match='BAD_LOSS_INPUT'):
        m.training_objective(logits,torch.tensor([1.,2.,0.,0.]),n,n,g,arm='matched_context')
    with pytest.raises(ValueError,match='BAD_LOSS_INPUT'):
        m.training_objective(logits,y,n,n,g,arm='not-listed')


def test_no_gold_forced_into_train_inference_inventory():
    torch.manual_seed(9)
    anchors=[(i*9,i*9+3,'action') for i in range(30)]
    logits=torch.zeros(30,30,len(BIND_CLASSES))
    for j in range(30):logits[:,j,1]=30-j
    gold={(anchors[0],anchors[-1],'owns')}
    row={'anchors':anchors,'vectors':torch.randn(30,8),'gold':gold}
    allowed={('owns','action','action')}
    inventory=source13.make_inventory(row,logits,allowed,top_k=24,train_gold=False)
    changed=source13.make_inventory({**row,'gold':set()},logits,allowed,top_k=24,train_gold=False)
    assert inventory['edges']==changed['edges']
    assert gold.isdisjoint(inventory['edges'])
    assert torch.equal(inventory['features'],changed['features'])
    assert len(m.label_blind_groups(inventory,seed=4))>0


def test_evaluation_baseline_exact_replay_and_fail_closed(monkeypatch):
    torch.manual_seed(19)
    anchors=[(i*5,i*5+3,'action') for i in range(4)]
    rank=torch.zeros(4,4,len(BIND_CLASSES))
    case={'anchors':anchors,'family':'encounter','vectors':torch.randn(4,8),
          'gold':{(anchors[0],anchors[1],'owns')},
          'prompt_sha256':base._sha('paired G2-0019')}
    row={'prompt':'paired G2-0019','family':'encounter','structural_topology':'4x4'}
    baseline=source13.SourceConditionedScorer(8).eval().requires_grad_(False)
    trained={'matched_context':deepcopy(baseline),'precision_guard':deepcopy(baseline)}
    allowed={('owns','action','action')}
    inv=source13.make_inventory(case,rank,allowed)
    with torch.inference_mode():
        predicted=source13.decode_scores(inv,baseline(inv['features'],inv['base'],
                                                     inv['groups']),m.THRESHOLD)
    expected=sel.score_set(predicted,case['gold'])
    parent={'prompt_sha256':case['prompt_sha256'],'family':'encounter',
            'group':'new_independent_validation','index':1,
            'configurations':{'general_75':expected}}
    monkeypatch.setattr(m.rel,'full_logits',lambda head,vec:rank)
    result=m.evaluate_case(case,row,parent,None,baseline,trained,allowed,
                           group='new_independent_validation',index=1,device='cpu')
    assert set(result['configurations'])==set(m.ARMS)
    assert result['configurations']['baseline_0016']==expected
    assert m.aggregate([result])['metrics']['baseline_0016']['cases']==1
    tampered=deepcopy(parent);tampered['configurations']['general_75']['correct']+=1
    with pytest.raises(ValueError,match='BASELINE_REPLAY_DRIFT'):
        m.evaluate_case(case,row,tampered,None,baseline,trained,allowed,
                        group='new_independent_validation',index=1,device='cpu')


def test_parent_0018_contract_and_output_immutability(tmp_path):
    (tmp_path/'g2_competitor_context_intervention_report.json').write_text('{"schema":"bad"}')
    with pytest.raises(ValueError,match='PARENT_0018_CONTRACT'):
        m.verify_parent_0018(tmp_path,{'new_independent_validation':[],
                                      'withheld_topology_validation':[]},{},{})
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(root=tmp_path,g1_validation_report='unused',g2_run='unused',
              selection_run='unused',discrimination_run='unused',relational_run='unused',
              density_run='unused',consistency_run='unused',context_run='unused',
              learned_run='unused',recall_run='unused',crowded_run='unused',
              robustness_run='unused',diagnosis_run='unused',source_run='unused',
              specialization_run='unused',routing_robustness_run='unused',
              general_run='unused',ceiling_run='unused',context18_run='unused',output=tmp_path)
    with pytest.raises(ValueError,match='FIXED_PROTOCOL'):
        m.run(root=tmp_path,g1_validation_report='unused',g2_run='unused',
              selection_run='unused',discrimination_run='unused',relational_run='unused',
              density_run='unused',consistency_run='unused',context_run='unused',
              learned_run='unused',recall_run='unused',crowded_run='unused',
              robustness_run='unused',diagnosis_run='unused',source_run='unused',
              specialization_run='unused',routing_robustness_run='unused',
              general_run='unused',ceiling_run='unused',context18_run='unused',
              output=tmp_path/'new_run',steps=1)
