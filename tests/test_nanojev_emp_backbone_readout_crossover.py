"""Tests for exact checkpoint component swaps and evidence parity."""
import argparse
import csv
import json
from pathlib import Path

import pytest
import torch
from torch import nn

from tools import nanojev_emp_backbone_readout_crossover as m
from tools import nanojev_emp_defect_cross_transfer as cross
from tools import nanojev_emp_isolated_defects as iso


class Toy(nn.Module):
    def __init__(self):
        super().__init__()
        self.backbone=nn.Linear(2,4)
        self.norm=nn.LayerNorm(4)
        self.sign_head=nn.Linear(4,3)
        self.digit_head=nn.Linear(4,70)
        self.regression_head=nn.Linear(4,1)

    def forward(self, x):
        h=self.norm(self.backbone(x.float()))
        return {'sign':self.sign_head(h),'digits':self.digit_head(h).reshape(-1,7,10),
                'ratio':self.regression_head(h).squeeze(-1)}


def snapshots():
    model=Toy()
    before={k:v.detach().clone() for k,v in model.state_dict().items()}
    after={k:(v.detach().clone()+ (1 if k.startswith(m.READOUT) else 2)) for k,v in before.items()}
    return before,after


def test_readout_is_exactly_both_sign_and_digit_heads():
    old,new=snapshots()
    groups=m.verify_parts(old,new)
    assert len(groups['sign_head'])==2 and len(groups['digit_head'])==2
    mixed=m.mix_state(old,new,False,True)
    assert all(torch.equal(mixed[k],new[k] if m.is_readout(k) else old[k]) for k in mixed)
    mixed=m.mix_state(old,new,True,False)
    assert all(torch.equal(mixed[k],old[k] if m.is_readout(k) else new[k]) for k in mixed)
    assert torch.equal(m.mix_state(old,new,False,False)['backbone.weight'],old['backbone.weight'])
    assert torch.equal(m.mix_state(old,new,True,True)['sign_head.weight'],new['sign_head.weight'])
    assert torch.equal(mixed['regression_head.weight'],new['regression_head.weight'])


def test_rejects_missing_malformed_and_nonfinite_readout():
    old,new=snapshots()
    del new['digit_head.bias']
    with pytest.raises(RuntimeError,match='INCOMPATIBLE_KEYS'):m.verify_parts(old,new)
    old,new=snapshots();new['sign_head.weight']=torch.zeros(1,4)
    with pytest.raises(RuntimeError,match='INCOMPATIBLE_TENSOR'):m.verify_parts(old,new)
    old,new=snapshots();new['sign_head.weight'][0,0]=float('nan')
    with pytest.raises(RuntimeError,match='NONFINITE_TENSOR'):m.verify_parts(old,new)
    old,new=snapshots();old['unexpected_head.weight']=new['unexpected_head.weight']=torch.zeros(1)
    assert 'unexpected_head.weight' in m.verify_parts(old,new)['backbone']
    del old['digit_head.bias'];del new['digit_head.bias']
    with pytest.raises(RuntimeError,match='UNEXPECTED_READOUT'):m.verify_parts(old,new)


def test_parameter_drift_does_not_mix_heads():
    before,after=snapshots()
    drift=m.param_drift(before,after)
    assert drift['backbone']['tensor_count']>0
    assert drift['sign_head']['tensor_count']==2
    assert drift['digit_head']['tensor_count']==2
    assert all(g['change_l2']>0 for g in drift.values())


def fake_eval(preds,scores):
    return [{'prediction':preds[i], 'correct':preds[i]==(-13 if i==0 else -11),
             'gold_log_score':scores[i], 'sign_pred_class':0, 'sign_gold_class':0,
             'sign_gold_margin':2., 'digit_pred':[0]*7, 'digit_gold':[0]*7,
             'digit_gold_margins':[1.]*7, 'sign_logits':[1,0,0],
             'digit_logits':[[0.]*10 for _ in range(7)]} for i in range(2)]


def test_arm_aggregation_identifies_readout_sufficiency_and_damage():
    rows=[{'a':-455,'b':442,'target':-13,'before':-113,'after':-13,'previously_wrong':True},
          {'a':-455,'b':444,'target':-11,'before':-11,'after':-11,'previously_wrong':False}]
    arms={'B0R0':fake_eval([-113,-11],[-5,-1]),
          'B0R1':fake_eval([-13,-111],[-2,-3]),
          'B1R0':fake_eval([-113,-11],[-4,-1]),
          'B1R1':fake_eval([-13,-11],[-1,-1])}
    result,cases,details,logits=m.aggregate(rows,arms)
    assert result['failure_categories']['new_readout_suffices']==1
    assert result['arms']['B0R1']['repaired_previous_failures']==1
    assert result['arms']['B0R1']['damaged_previous_controls']==1
    assert result['arms']['B1R0']['repaired_previous_failures']==0
    assert result['arms']['B1R1']['total_correct']==2
    assert cases[0]['interaction_gold_log_score']==0.
    assert len(details)==8 and len(logits)==8


def test_baseline_drift_rejected():
    rows=[{'a':1,'b':0,'target':1,'before':2,'after':1,'previously_wrong':True},
          {'a':0,'b':0,'target':0,'before':0,'after':0,'previously_wrong':False}]
    arms={name:fake_eval([1,-11],[-1,-1]) for name in m.ARMS}
    with pytest.raises(RuntimeError,match='BASELINE_DRIFT'):
        m.aggregate(rows,arms)


def test_real_sign_digit_scoring_and_decode():
    # Model's sign/digit answer uses independent sign and seven digits.
    def out(correct):
        sign=torch.tensor([[8.,-8.,-8.]])
        digits=torch.zeros((1,7,10))
        gold=[3,1,0,0,0,0,0]
        for i,d in enumerate(gold):digits[0,i,d]=8
        if not correct:digits[0,2,1]=16  # predict -113 instead of -13
        return {'sign':sign,'digits':digits}
    row=[{'target':-13}]
    right=m.head_details(out(True),row)[0]
    wrong=m.head_details(out(False),row)[0]
    assert right['prediction']==-13 and wrong['prediction']==-113
    assert right['gold_log_score']>wrong['gold_log_score']
    assert right['digit_pred'][:3]==[3,1,0]
    assert wrong['digit_gold_margins'][2]<0


def test_evidence_uniqueness_and_flags(tmp_path):
    def write(data):
        with (tmp_path/'natural_repair.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)
    rows=[{'a':1,'b':-2,'target':-1,'before':-101,'after':-1,
           'previously_wrong':'True','repaired':'True','regressed':'False'},
          {'a':2,'b':-3,'target':-1,'before':-1,'after':-1,
           'previously_wrong':'False','repaired':'False','regressed':'False'}]
    write(rows)
    assert len(m.read_evidence(tmp_path))==2
    write([rows[0],rows[0]])
    with pytest.raises(RuntimeError,match='DUPLICATED_CARD'):
        m.read_evidence(tmp_path)
    write([{**rows[0],'repaired':'False'},rows[1]])
    with pytest.raises(RuntimeError,match='REPAIR_FLAG'):
        m.read_evidence(tmp_path)


def test_read_snapshots_provenance_from_original_cross_report(tmp_path):
    run=tmp_path/'run';stage0=run/'stage_027';stage1=run/'stage_028'
    stage0.mkdir(parents=True);stage1.mkdir(parents=True)
    cfg={'model':'frozen'}
    (run/'config.json').write_text(json.dumps(cfg))
    a,b=snapshots()
    for s,path,state in ((27,stage0/'mastered_snapshot.pt',a),(28,stage1/'mastered_snapshot.pt',b)):
        torch.save({'schema':iso.SCHEMA+'.mastery-snapshot','stage':s,'config':cfg,'model':state},path)
    cross_root=tmp_path/'cross';cross_root.mkdir()
    (cross_root/'config.json').write_text(json.dumps({'schema':cross.SCHEMA,'before_stage':27,
        'after_stage':28,'source_run':str(run),'before_sha256':cross.sha256(stage0/'mastered_snapshot.pt'),
        'after_sha256':cross.sha256(stage1/'mastered_snapshot.pt')}))
    (cross_root/'result.json').write_text(json.dumps({'schema':cross.SCHEMA,
        'before_stage':27,'after_stage':28}))
    assert m.check_evidence_provenance(cross_root,run,27,28,
        stage0/'mastered_snapshot.pt',stage1/'mastered_snapshot.pt')['schema']==cross.SCHEMA
    with pytest.raises(RuntimeError,match='CHECKPOINT_MISMATCH'):
        m.check_evidence_provenance(cross_root,run,26,28,
            stage0/'mastered_snapshot.pt',stage1/'mastered_snapshot.pt')


def test_end_to_end_artifacts_use_identical_cards_and_preserve_snapshots(tmp_path,monkeypatch):
    run=tmp_path/'run';root0=run/'stage_027';root1=run/'stage_028'
    root0.mkdir(parents=True);root1.mkdir(parents=True)
    cfg={'run':'fixed'};(run/'config.json').write_text(json.dumps(cfg))
    before,after=snapshots()
    stage_paths=[]
    for stage,root,state in ((27,root0,before),(28,root1,after)):
        path=root/'mastered_snapshot.pt'
        torch.save({'schema':iso.SCHEMA+'.mastery-snapshot', 'stage':stage,
                    'config':cfg,'model':state},path)
        stage_paths.append(path)
    evidence=tmp_path/'evidence';evidence.mkdir()
    (evidence/'config.json').write_text(json.dumps({'schema':cross.SCHEMA,'before_stage':27,
        'after_stage':28,'source_run':str(run),
        'before_sha256':cross.sha256(stage_paths[0]),
        'after_sha256':cross.sha256(stage_paths[1])}))
    (evidence/'result.json').write_text(json.dumps({'schema':cross.SCHEMA,
        'before_stage':27,'after_stage':28}))
    cards=[{'a':-455,'b':442,'target':-13,'before':-113,'after':-13,
            'previously_wrong':'True','repaired':'True','regressed':'False'},
           {'a':-455,'b':444,'target':-11,'before':-11,'after':-11,
            'previously_wrong':'False','repaired':'False','regressed':'False'}]
    with (evidence/'natural_repair.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(cards[0]));writer.writeheader();writer.writerows(cards)
    monkeypatch.setattr(m.lasso,'initialize',lambda *args:(Toy(),None,None,None,None))
    seen=[]
    def evaluate_mock(model,states,rows,device,batch):
        model.load_state_dict(states,strict=True)
        backbone_new=torch.equal(states['backbone.weight'],after['backbone.weight'])
        readout_new=torch.equal(states['sign_head.weight'],after['sign_head.weight'])
        seen.append((backbone_new,readout_new,tuple((r['a'],r['b']) for r in rows)))
        predictions=([-13,-11] if backbone_new and readout_new else
                     [-13,-11] if readout_new else [-113,-11])
        return [{**m.head_details({'sign':torch.tensor([[8.,0.,0.]]),
                            'digits':torch.zeros((1,7,10))},[r])[0],
                 'prediction':predictions[i],
                 'correct':predictions[i]==r['target']}
                for i,r in enumerate(rows)]
    monkeypatch.setattr(m,'evaluate_arm',evaluate_mock)
    args=argparse.Namespace(run=str(run),cross_transfer=str(evidence),
        campaign=str(tmp_path/'campaign'),ladder_output=str(tmp_path/'ladder'),
        hard_state_dir='hard_emp_frontier_v2',output=str(tmp_path/'output'),
        device='cpu',before_stage=27,after_stage=28,batch=128)
    hashes_before=[cross.sha256(p) for p in stage_paths]
    result=m.run(args)
    assert result['arms']['B0R1']['repaired_previous_failures']==1
    assert result['arms']['B1R0']['repaired_previous_failures']==0
    assert result['arms']['B1R1']['damaged_previous_controls']==0
    assert [x[:2] for x in seen]==[(False,False),(False,True),(True,False),(True,True)]
    assert all(x[2]==seen[0][2] for x in seen)
    assert [cross.sha256(p) for p in stage_paths]==hashes_before
    for name in ('cases.csv','head_diagnostics.csv','head_logits.jsonl.gz',
                 'component_changes.json','config.json','result.json'):
        assert (tmp_path/'output'/name).is_file()
    with pytest.raises(RuntimeError,match='OUTPUT_CONFIG_DRIFT'):
        m.run(argparse.Namespace(**{**vars(args),'batch':16}))
