"""Diagnostic replay must preserve event-time provenance and local geometry."""
import argparse
import copy
import json
from pathlib import Path

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_emp_failure_neighborhood as n
from tools import nanojev_emp_first_failure as ff


def model():
    return AdditionNet('compact', width=8, heads=2, layers=1, rounds=2)


def test_local_coordinates_include_original_for_each_branch():
    for m,d in ((16,6),(1024,-20),(2,22)):
        for sign in (-1,1):
            for swap in (False,True):
                a,b=sign*m,-sign*m+d
                if swap:
                    a,b=b,a
                orig={'m':m,'delta':d,'card':ff.card(a,b)}
                assert n.branch_for(orig)==(sign,swap)
                rows=n.neighborhood(orig,2,2)
                center=[r for r in rows if r['original_center']]
                assert len(center)==1 and center[0]['card'][:2]==(a,b)
                assert all(c['card'][0]+c['card'][1]==c['card'][2] for c in rows)
                assert len({(c['m'],c['delta']) for c in rows})==len(rows)


def test_archive_replay_and_local_neighbors(tmp_path):
    net=model().eval()
    f=ff.card(-16,22)
    predicted,_=ff.capture(net,f,'cpu')
    c=ff.card(-17,23)
    cp,_=ff.capture(net,c,'cpu')
    archived={'first_failure':{'card':list(f),'m':16,'delta':6,'prediction':predicted,'exact':predicted==6},
              'neighboring_successes':[{'card':list(c),'prediction':cp,'exact':cp==6}],
              'neighboring_failures':[]}
    folder=tmp_path/'source'/'ring_02'
    folder.mkdir(parents=True)
    (folder/'event.json').write_text(json.dumps(archived))
    (tmp_path/'source'/'config.json').write_text(json.dumps({'parameters':{'seed':20261025}}))
    args=argparse.Namespace(device='cpu',m_window=2,delta_window=2,
                            controls=5,activation_top=5,ablate_top=0)
    result=n.replay_one(tmp_path/'source',tmp_path/'out',2,args,net,'event_snapshot')
    assert result['historical_reproduction'] is True
    assert result['archived_prediction_matches']==2
    assert result['neighborhood_cases']>5
    dest=tmp_path/'out'/'ring_02'
    assert all((dest/file).is_file() for file in ('arithmetic_neighborhood.svg',
        'activation_neighborhood.svg','report.json','all_local_predictions.jsonl.gz',
        'local_activations.pt'))
    archived['first_failure']['prediction']=999
    (folder/'event.json').write_text(json.dumps(archived))
    with pytest.raises(RuntimeError,match='SNAPSHOT_MISMATCH'):
        n.replay_one(tmp_path/'source',tmp_path/'out',2,args,net,'event_snapshot')
    future=n.replay_one(tmp_path/'source',tmp_path/'future',2,args,net,'final_checkpoint')
    assert future['historical_reproduction'] is False
    assert future['archived_prediction_matches']==1


def test_fresh_spiral_saves_probe_time_snapshot(tmp_path,monkeypatch):
    from tools import nanojev_emp_first_failure as original
    a=argparse.Namespace(campaign=str(tmp_path/'campaign'),ladder_output=str(tmp_path/'ladder'),
        hard_state_dir='hard_emp_frontier_v2',output=str(tmp_path/'experiment'),
        device='cpu',seed=20261025,initial_magnitude=16,initial_delta=2,
        magnitude_growth=2,delta_step=1,max_delta=8,max_magnitude=128,
        rings=1,train_cards=16,probe_anchors=8,loss_target=.01,max_steps=2,
        check_every=1,batch=8,eval_batch=32,lr=.0001,neighbors=4,
        activation_top=6,ablate_top=0,failure_mode='exact')
    src=model()
    opt=torch.optim.AdamW(src.parameters(),lr=.0001)
    monkeypatch.setattr(original.lasso,'initialize',lambda *x:
                        (copy.deepcopy(src),None,copy.deepcopy(opt.state_dict()),'x','y'))
    monkeypatch.setattr(original,'memorize',lambda *x:{'steps':0,'deck':16,'mean_ce':0.,
                                                      'exact_accuracy':1.,'memorized':True})
    result=original.run(a)
    assert result['status']=='complete'
    snapshot=tmp_path/'experiment'/'ring_01'/'probe_snapshot.pt'
    state=torch.load(snapshot,map_location='cpu',weights_only=True)
    assert state['schema']==original.SCHEMA+'.probe-snapshot.v1'
    assert state['ring']==1
    assert state['config']['source_hard_sha256']=='x'
    assert 'optimizer' in state and 'model' in state
    assert original.run(a)['status']=='complete'
    assert torch.load(snapshot,weights_only=True)['ring']==1


def test_missing_original_snapshot_fails_closed(tmp_path,monkeypatch):
    root=tmp_path/'run'
    (root/'ring_02').mkdir(parents=True)
    (root/'ring_02'/'event.json').write_text('{}')
    (root/'config.json').write_text(json.dumps({'schema':ff.SCHEMA,'parameters':{},
        'source_hard_sha256':'x','source_reverse_sha256':'y'}))
    a=argparse.Namespace(run=str(root),output=str(tmp_path/'out'),campaign=str(tmp_path),
        ladder_output=str(tmp_path),hard_state_dir='hard',device='cpu',
        ring='all',checkpoint_source='event',m_window=1,delta_window=1,
        controls=3,activation_top=3,ablate_top=0)
    with pytest.raises(RuntimeError,match='EVENT_SNAPSHOT_MISSING'):
        n.run(a)
