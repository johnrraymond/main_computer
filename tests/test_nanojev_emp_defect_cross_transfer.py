"""Tests for same-coordinate cross-transfer and matched neural controls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import torch
from torch import nn

from tools import nanojev_emp_defect_cross_transfer as m


def test_dose_preserves_zero_and_scales_signed_difference():
    assert m.dose(2,8,0)==2
    assert m.dose(2,8,.5)==5
    assert m.dose(2,8,1)==8
    assert m.dose(2,8,-1)==-4


def test_same_answer_holdouts_stratified_without_training_leak():
    same=[{'m':x,'delta':-13,'card':[x,-13-x,-13,0,500]} for x in
          range(430,441)]+[{'m':x,'delta':-13,'card':[x,-13-x,-13,0,500]} for x in range(446,459)]
    hold=m.holdout_ids(same,444,6)
    assert len(hold)>=4 and len(set(hold))==len(hold)
    assert any(x[0]<444 for x in hold) and any(x[0]>444 for x in hold)
    training=[r for r in same if tuple(r['card'][:2]) not in hold]
    assert len([r for r in training if r['m']<444])>=2
    assert len([r for r in training if r['m']>444])>=2


def test_checkpoint_stage_and_config_must_match(tmp_path):
    p=tmp_path/'mastered_snapshot.pt'
    cfg={'run':17}
    torch.save({'schema':m.iso.SCHEMA+'.mastery-snapshot','config':cfg,'stage':27,
                'model':{'some_weight':torch.tensor([1.])}},p)
    assert m.snapshot(p,cfg,27)['some_weight'][0]==1
    with pytest.raises(RuntimeError,match='SNAPSHOT_DRIFT'):
        m.snapshot(p,cfg,28)
    with pytest.raises(RuntimeError,match='SNAPSHOT_DRIFT'):
        m.snapshot(p,{'run':18},27)


class Readout(nn.Module):
    """A simple model with one hookable site and soft sign/digit logits."""
    def __init__(self):
        super().__init__()
        self.norm=nn.Identity()
    def forward(self,x):
        act=self.norm(x).flatten(1)
        value=act[:,0]
        # Mocking the decoder in the test below allows an exact answer boundary.
        return {'sign':torch.stack((-value, value, -value),dim=1),
                'digits':torch.stack([value, -value],dim=1).unsqueeze(1)}


def test_site_only_hook_noop_and_intervention_share_path(monkeypatch):
    model=Readout()
    monkeypatch.setattr(m.ff,'activation_modules',lambda model:[('norm',model.norm)])
    monkeypatch.setattr(m,'input_tensor',lambda cards,device:torch.tensor([[float(cards[0][0])]]))
    monkeypatch.setattr(m.dual,'decoded',lambda logits:[int(logits['sign'][0,1]>logits['sign'][0,0])])
    before=m.response(model,[4,0,4,0,4],'cpu',('norm@0',0))
    assert before['prediction']==1 and before['activation']==4
    no_op=m.response(model,[4,0,4,0,4],'cpu',('norm@0',0),4)
    assert no_op['prediction']==before['prediction']
    patched=m.response(model,[4,0,4,0,4],'cpu',('norm@0',0),-5)
    assert patched['prediction']==0
    with pytest.raises(RuntimeError,match='HOOK_NOT_REACHED'):
        m.response(model,[4,0,4,0,4],'cpu',('norm@1',0))


def test_two_local_methods_evaluated_on_identical_correct_holdouts(monkeypatch):
    # Linear function of magnitude; matching fit and validation must succeed.
    target={'m':444,'delta':-13,'card':[-457,444,-13,0,457],'prediction':-113}
    points=[]
    for mm in range(430,459):
        if 442<=mm<=444:continue
        points.append({'m':mm,'delta':-13,'card':[-mm-13,mm,-13,0,mm+13],'prediction':-13})
    for mm in (440,441,445,446,450):
        for dd in (-12,-11,-14):
            points.append({'m':mm,'delta':dd,'card':[-mm+dd,mm,dd,0,mm-dd],'prediction':dd})
    ids=set(m.holdout_ids([p for p in points if p['delta']==-13],444,6))
    fit=[x for x in points if tuple(x['card'][:2]) not in ids]
    test=[x for x in points if tuple(x['card'][:2]) in ids]
    assert len(test)==6
    monkeypatch.setattr(m,'response',lambda model,card,device,site:{
        'prediction':card[2],'activation':1.5+0.003*card[1]+0.007*card[2]})
    report=m.fit_models(fit,test,target,('norm@1',25),'cpu',None,4,.6,.0000001)
    assert report['status']=='ok'
    assert report['line']['qualified'] and report['quadratic']['qualified']
    assert report['line']['nrmse']<.01 and report['quadratic']['nrmse']<.01
    assert set(tuple(x) for x in report['heldout_cards'])==ids
    assert all(tuple(r['card'][:2]) not in ids for r in fit)


def test_fit_rejects_unbracketed_constant_answer_controls(monkeypatch):
    target={'m':444,'delta':-13,'card':[-457,444,-13,0,457],'prediction':-113}
    rows=[{'m':i,'delta':-13,'card':[-13-i,i,-13,0,i+13],'prediction':-13}
          for i in range(430,442)]
    monkeypatch.setattr(m,'response',lambda model,card,device,site:{
        'prediction':-13,'activation':float(card[1])})
    result=m.fit_models(rows[:8],rows[8:],target,('norm@1',25),'cpu',None,2,.6,.01)
    assert result['status']=='insufficient_bracket'


def test_cross_transfer_sites_fixed_independent_of_candidate_ranking():
    assert len(m.DEFAULT_SITES)==8
    assert len(set(m.DEFAULT_SITES))==8
    assert ('norm@1',25) in m.DEFAULT_SITES
    assert ('backbone.layers.1@0',47) in m.DEFAULT_SITES


def test_read_centers_requires_valid_preexisting_evidence(tmp_path):
    root=tmp_path/'stage_027'/'relaxed_min_direct_6'
    for i in (0,1):
        folder=root/f'candidate_{i:03d}';folder.mkdir(parents=True)
        (folder/'report.json').write_text(json.dumps({'status':'local_cluster',
            'reconstruction_valid':True,
            'center':{'m':443+i,'delta':-13,'sign':1,'swap':True}}))
    rows=m.read_centers(tmp_path,27,6)
    assert len(rows)==2 and rows[0]['m']==443
    (root/'candidate_001'/'report.json').write_text(json.dumps({'status':'neighbor_failure',
        'reconstruction_valid':False,'center':{}}))
    with pytest.raises(RuntimeError,match='TWO_RECONSTRUCTABLE'):
        m.read_centers(tmp_path,27,6)
