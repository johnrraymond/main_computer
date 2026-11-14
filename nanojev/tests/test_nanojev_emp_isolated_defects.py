"""Unit and functional contract tests for competence-first neural reconstruction."""
import argparse
import json

import pytest
import torch
from torch import nn

from tools import nanojev_emp_isolated_defects as m


def options(**kw):
    value=dict(device='cpu',eval_batch=32,window=2,min_direct_correct=6,degree=2,ridge=.001,
               holdouts=6,signal_floor=.05,max_holdout_nrmse=.6,
               anomaly_threshold=3.,interventions=2,report_top=20)
    value.update(kw)
    return argparse.Namespace(**value)


def fake_row(dm,dd,correct=True):
    card=[100+dm,-100-dm+dd,dd,0,101]
    return {'card':card,'m':100+dm,'delta':dd,'m_offset':dm,
            'delta_offset':dd,'center':dm==dd==0,
            'prediction':dd if correct else dd+2,'exact':correct}


def fake_acts(row,shift=0.):
    x,y=row['m_offset'],row['delta_offset']
    return {'norm@0':torch.tensor([1+.3*x+.2*y+.01*x*y+shift,
                                  2.+.1*x*x+.3*y*y, 5+.1*y])}


def test_split_is_disjoint_across_increasing_radii():
    seed=20261025
    for radius in (16,32,64):
        train=m.training_entries(radius,8,100,seed)
        dev=m.probe_entries(radius,8,100,seed)
        test=m.probe_entries(radius,8,100,seed,'test')
        groups=[{tuple(e['card'][:2]) for e in xs} for xs in (train,dev,test)]
        assert all(groups[i].isdisjoint(groups[j]) for i in range(3) for j in range(i+1,3))
        assert all(e['card'][0]*e['card'][1]<=0 for xs in (train,dev,test) for e in xs)
        assert all(e['card'][2]==e['card'][0]+e['card'][1] for xs in (train,dev,test) for e in xs)
        assert dev==m.probe_entries(radius,8,100,seed)


def test_strict_isolation_rejects_nonisolated_and_incomplete():
    good=[fake_row(dx,dy) for dx in (-1,0,1) for dy in (-1,0,1)]
    for r in good:
        if r['center']:
            r['exact']=False
    assert m.isolated_eligibility(good,100,10,1)[0]=='isolated'
    assert m.isolated_eligibility(good[:-1],100,10,1)[0]=='incomplete_neighbors'
    good[0]['exact']=False
    assert m.isolated_eligibility(good,100,10,1,min_direct_correct=8)[0]=='neighbor_failure'


def test_activation_fit_predicts_smooth_controls_and_detects_defect():
    center=fake_row(0,0,False)
    controls=[]
    for dx in range(-2,3):
        for dy in range(-2,3):
            if dx==dy==0:continue
            r=fake_row(dx,dy)
            controls.append((r,fake_acts(r)))
    failed=(center,fake_acts(center,shift=3.))
    result=m.local_fit(controls,failed,degree=2,ridge=.0001,holdouts=6,
                       min_signal=.05,required_r2=.6,threshold=3.)
    assert result['valid'] and result['fit_controls']==18 and result['heldout_controls']==6
    assert result['candidates'][0]['channel']==0
    assert result['candidates'][0]['standardized_residual']>30
    assert abs(result['candidates'][0]['predicted']-1)<.01
    assert not any(v['channel']==1 for v in result['candidates'])


def test_no_fake_attribution_when_controls_insufficient():
    c=fake_row(0,0,False)
    out=m.local_fit([(fake_row(0,i),fake_acts(fake_row(0,i))) for i in (1,2,3)],
                    (c,fake_acts(c)))
    assert not out['valid'] and out['reason']=='insufficient_correct_controls'


def test_local_grid_preserves_order_and_requires_full_direct_neighbors():
    center={'m':100,'delta':0,'sign':-1,'swap':True}
    rows=m.branch_neighborhood(center,150,8,2)
    assert len(rows)==25 and sum(r['center'] for r in rows)==1
    assert all(e['card'][0]*e['card'][1]<=0 for e in rows)
    near=m.branch_neighborhood({'m':1,'delta':0,'sign':1,'swap':False},8,4,2)
    assert len(near)<25


def test_radius_training_card_disjoint_at_any_new_stage():
    seed=314159
    for radius in (32,64,128):
        train=m.training_entries(radius,8,120,seed)
        probes=m.probe_entries(radius,8,100,seed)
        assert not ({tuple(e['card'][:2]) for e in train} &
                    {tuple(e['card'][:2]) for e in probes})


class FakeCore(nn.Module):
    def __init__(self):
        super().__init__()
        self.norm=nn.Identity()
    def forward(self,x):
        return self.norm(x)


def test_intervention_noop_hook_matches_identity(monkeypatch):
    model=FakeCore()
    monkeypatch.setattr(m.ff,'activation_modules',lambda model:[('norm',model.norm)])
    monkeypatch.setattr(m,'input_tensor',lambda cards,device:torch.tensor([[2.,3.]]))
    monkeypatch.setattr(m.dual,'decoded',lambda x:[int(x[0,0].item())])
    assert m.patch_predict(model,[1,-1,0,0,1],'cpu',{('norm@0',0):None})==2
    assert m.patch_predict(model,[1,-1,0,0,1],'cpu',{('norm@0',0):5.})==5
    with pytest.raises(RuntimeError,match='NOT_REACHED'):
        m.patch_predict(model,[1,-1,0,0,1],'cpu',{('norm@1',0):4.})


def test_manifested_replay_uses_stage_path(tmp_path):
    p=tmp_path/'samples.gz'
    m.write_gzip(p,[fake_row(0,0,False)])
    import gzip
    with gzip.open(p,'rt') as f:
        assert json.loads(f.readline())['prediction']==2
    m.write_svg(tmp_path/'grid.svg',[fake_row(0,0,False)])
    assert (tmp_path/'grid.svg').exists()


def test_small_finite_radius_returns_unique_decks_without_exhaustion():
    train=m.training_entries(16,4,20000,20261025)
    dev=m.probe_entries(16,4,20000,20261025)
    assert len(train)<20000 and len(dev)<20000
    assert len({tuple(r['card'][:2]) for r in train}) == len(train)
    assert not ({tuple(r['card'][:2]) for r in train} &
                {tuple(r['card'][:2]) for r in dev})


def test_unmastered_training_can_resume_optimizer_and_rng(monkeypatch):
    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.w=nn.Parameter(torch.tensor(0.))
        def forward(self,x):
            return self.w.expand(x.shape[0])
    model=Model()
    optimizer=torch.optim.SGD(model.parameters(),lr=.25)
    monkeypatch.setattr(m,'input_tensor',lambda cards,device:torch.zeros(len(cards),1))
    monkeypatch.setattr(m.dual,'loss_per_card',lambda output,target:(output-1.)**2)
    monkeypatch.setattr(m.ff,'deck_metrics',lambda mod,deck,args:{
        'mean_ce':float((mod.w.detach()-1.)**2),
        'exact_accuracy':1. if mod.w.item()>.95 else 0.})
    monkeypatch.setattr(m,'score',lambda mod,rows,device,batch:[
        {'exact':mod.w.item()>.95} for _ in rows])
    args=argparse.Namespace(device='cpu',eval_batch=8,seed=123,stage_steps=4,
        batch=2,check_every=1,loss_target=.001,mastery_gate=.99,
        retention_gate=.99)
    values=[]
    cards=[{'card':(1,-1,0,0,1)} for _ in range(4)]
    first,_=m.train_to_gate(model,optimizer,cards,cards,args,0,
                            checkpoint=lambda steps,state:values.append((steps,state)))
    assert not first['accepted'] and first['steps']==4
    assert len(values)==4
    second,_=m.train_to_gate(model,optimizer,cards,cards,args,0,
                  resume_steps=values[-1][0],rng_state=values[-1][1],
                  checkpoint=lambda steps,state:values.append((steps,state)))
    assert second['accepted'] and second['steps']>4
    assert second['steps_this_invocation'] <= 4


def test_relaxed_isolation_accepts_compact_error_clusters():
    rows=[fake_row(dx,dy) for dx in (-1,0,1) for dy in (-1,0,1)]
    next(r for r in rows if r['center'])['exact']=False
    adjacent=next(r for r in rows if (r['m_offset'],r['delta_offset'])==(-1,0))
    adjacent['exact']=False
    assert m.isolated_eligibility(rows,100,10,1)[0]=='local_cluster'  # 7/8 correct
    next(r for r in rows if (r['m_offset'],r['delta_offset'])==(1,0))['exact']=False
    assert m.isolated_eligibility(rows,100,10,1)[0]=='local_cluster'  # 6/8 correct
    assert m.isolated_eligibility(rows,100,10,1,min_direct_correct=8)[0]=='neighbor_failure'
    next(r for r in rows if (r['m_offset'],r['delta_offset'])==(0,-1))['exact']=False
    assert m.isolated_eligibility(rows,100,10,1)[0]=='neighbor_failure'
    assert m.isolated_eligibility(rows[:-1],100,10,1)[0]=='incomplete_neighbors'


def test_compact_cluster_enters_heldout_activation_reconstruction(tmp_path, monkeypatch):
    selected={'m':100,'delta':0,'sign':1,'swap':False}
    rows=m.branch_neighborhood(selected,128,8,2)
    failed={(0,0),(-1,0),(1,0)}
    scored=[]
    for r in rows:
        r=dict(r)
        r['exact']=(r['m_offset'],r['delta_offset']) not in failed
        r['prediction']=int(r['card'][2]) + (100 if not r['exact'] else 0)
        r['error']=int(r['prediction'])-int(r['card'][2])
        scored.append(r)
    lookup={tuple(r['card'][:2]):r for r in scored}
    monkeypatch.setattr(m,'score',lambda model,cases,device,batch:scored)
    def capture(model,card,device):
        r=lookup[tuple(card[:2])]
        shift=3 if r['center'] else 0.
        return int(r['prediction']),fake_acts(r,shift)
    monkeypatch.setattr(m.ff,'capture',capture)
    report=m.neighborhood_diagnostic(None,selected,128,8,options(interventions=0),tmp_path/'cluster')
    assert report['status']=='local_cluster'
    assert report['direct_correct']==6 and report['direct_total']==8
    assert report['wrong_neighbors']==2
    assert report['reconstruction_valid']
    assert report['heldout_controls']==6 and report['fit_controls']==16
    assert report['anomalous_candidates']>=1
    assert (tmp_path/'cluster'/'activation_reconstruction.pt').exists()


def test_revisit_preserves_original_evidence_and_uses_stage_snapshot(tmp_path, monkeypatch):
    stage=tmp_path/'stage_027'
    original=stage/'candidate_000'/'report.json'
    original.parent.mkdir(parents=True)
    original.write_text(json.dumps({'status':'neighbor_failure',
        'center':{'m':443,'delta':-13,'sign':1,'swap':True}}))
    config={'schema':m.SCHEMA}
    torch.save({'schema':m.SCHEMA+'.mastery-snapshot','config':config,
                'stage':27,'model':{'w':torch.tensor([27.])}},stage/'mastered_snapshot.pt')
    class Model:
        def load_state_dict(self, weights):self.weights=weights
        def eval(self):pass
    model=Model()
    def fake_diagnostic(model,center,radius,max_delta,args,folder):
        assert model.weights['w'][0]==27
        assert (radius,max_delta)==(464,32)
        assert args.min_direct_correct==6
        folder.mkdir(parents=True,exist_ok=True)
        m.atomic_json(folder/'report.json',{'status':'local_cluster'})
        return {'status':'local_cluster','reconstruction_valid':True,'repaired':1}
    monkeypatch.setattr(m,'neighborhood_diagnostic',fake_diagnostic)
    history=[{} for _ in range(28)]
    history[27]={'radius':464,'max_delta':32,
                 'candidate_reports':[{'candidate':[-456,443]}]}
    result=m.revisit_stage_candidates(model,tmp_path,history,config,
                        argparse.Namespace(revisit_stage=27,min_direct_correct=6))
    assert result['local_clusters']==1 and result['repairs']==1
    assert json.loads(original.read_text())['status']=='neighbor_failure'
    assert (stage/'relaxed_min_direct_6'/'result.json').is_file()
