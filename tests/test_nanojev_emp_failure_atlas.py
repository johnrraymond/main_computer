"""Adaptive Atlas: sampling, checkpoints, causal controls, and heldout validity."""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_emp_failure_atlas as a
from tools import nanojev_emp_first_failure as ff


def args(**kw):
    values=dict(seed=77,device='cpu',min_magnitude=1,max_magnitude=4096,max_delta=16,
        census_cases=500,chunk_size=100,batch_size=32,discovery_centers=4,
        validation_centers=2,m_window=2,delta_window=2,controls=3,
        ablate_top=0,activation_top=8,min_recurring=2,other_failures=1)
    values.update(kw)
    return argparse.Namespace(**values)


def test_stratified_generator_has_unique_valid_cards_and_fixed_bands():
    cases=list(a.varied_cases(11,1500,1,100000,32))
    assert len(cases)==1500
    assert len({tuple(x['card'][:2]) for x in cases})==1500
    assert {'discovery','validation'} <= set(x['split'] for x in cases)
    assert {'same_sign','opposite_or_zero'}<=set(x['branch'] for x in cases)
    assert all(x['card'][0]+x['card'][1]==x['card'][2] for x in cases)
    assert all(max(abs(x['card'][0]),abs(x['card'][1])) <=a.MAX_ABS for x in cases)
    assert all(a.independent_split(x['m'],11)==x['split'] for x in cases)
    assert not ({x['band'] for x in cases if x['split']=='discovery'} &
                {x['band'] for x in cases if x['split']=='validation'})
    assert cases[:10]==list(a.varied_cases(11,1500,1,100000,32))[:10]


def test_too_large_operands_refused():
    with pytest.raises(ValueError,match='ATLAS_INVALID_ARGUMENTS'):
        a.check_args(args(max_magnitude=a.MAX_ABS,max_delta=64))


def test_streaming_resume_and_corrupt_shard_refused(tmp_path,monkeypatch):
    seen=[]
    def fake_score(_,rows,device,batch_size):
        seen.append(len(rows))
        return [{**r,'prediction':r['card'][2]+(1 if r['census_id']%7==0 else 0),
                 'exact':r['census_id']%7!=0,'within_5pct':r['census_id']%7!=0}
                for r in rows]
    monkeypatch.setattr(a,'score_chunk',fake_score)
    opt=args()
    totals,_=a.collect_census(None,tmp_path,opt)
    assert totals['cases']==500 and totals['errors']==72
    assert seen==[100]*5
    totals2,_=a.collect_census(None,tmp_path,opt)
    assert totals2==totals and len(seen)==5
    assert len(list(a.stream_census(tmp_path)))==500
    bad=tmp_path/'chunk_00003.jsonl.gz'
    bad.write_bytes(bad.read_bytes()+b'CORRUPTED')
    with pytest.raises(RuntimeError,match='SHA_MISMATCH|RESUME_MISMATCH'):
        a.collect_census(None,tmp_path,opt)


def test_local_grid_preserves_branch_and_split_at_power_boundary():
    original=a.make_variant(64,4,1,False)
    original['split']=a.independent_split(64,77)
    rows=a.local_grid(original,args(m_window=8,delta_window=4))
    assert sum(x['original_center'] for x in rows)==1
    assert all(x['card'][0]+x['card'][1]==x['card'][2] for x in rows)
    assert all(x['band']==original['band'] and x['split']==original['split'] for x in rows)
    assert all(a.nb.branch_for(x)==(1,False) for x in rows)


def test_centers_diversify_without_activations(tmp_path):
    opt=args(census_cases=200)
    candidates=list(a.varied_cases(77,200,1,4096,16))
    for row in candidates:
        row.update({'prediction':row['card'][2]+1,'exact':False,'within_5pct':False})
    a.atomic_gzip(tmp_path/'chunk_00000.jsonl.gz',candidates)
    a.atomic_json(tmp_path/'chunk_00000.meta.json',{'sha256':a.sha256(tmp_path/'chunk_00000.jsonl.gz'),
              'start':0,'count':200})
    out=a.select_centers(tmp_path,opt)
    assert len(out['discovery'])==4 and len(out['validation'])==2
    assert len({tuple(s['card'][:2]) for group in out.values() for s in group})==6
    assert all(r['split']==split for split,rows in out.items() for r in rows)


def test_patterns_heldout_discovery_separation():
    def ev(split,k,error,corrective):
        return {'split':split,'attribution_valid':True,
          'center':{'prediction':error+2,'card':[1,error-1,error]},
          'top_channels':[{'layer':'norm@0','channel':k}],
          'interventions':[{'layer':'norm@0','channel':k,'rescued':corrective}]}
    events=[ev('discovery',47,24,True),ev('discovery',47,14,True),
            ev('discovery',12,10,False),ev('validation',47,34,True),
            ev('validation',7,8,False)]
    report=a.patterns(events,2)
    assert report['recurring_features']==[{'layer':'norm@0','channel':47,'discovery_top8':2,
      'validation_top8':1,'discovery_repairs':2,'validation_repairs':1}]
    assert report['discovery_events_with_controls']==3
    assert report['validation_events_with_controls']==2


def test_predictive_check_keeps_validation_out_of_training(tmp_path,monkeypatch):
    monkeypatch.setattr(a,'SCHEMA','test-atlas')
    events=[]
    for split in ('discovery','validation'):
        for i in range(3):
            dest=tmp_path/f'{split}_{i}'
            dest.mkdir()
            rows=[]
            for label,v in ((1,2.),(0,-2.),(0,-1.)):
                rows.append({'role':'failure' if label else 'easy','card':[10,1,11],
                    'activations':{'norm@0':torch.tensor([v+i*.01])}})
            a.atomic_checkpoint(dest/'selected_activations.pt',{'selected':rows})
            events.append({'split':split,'attribution_valid':True,'event_index':i,
                'neighborhood':str(dest),'top_channels':[{'layer':'norm@0','channel':0}]})
    report=a.predictive_check(events,n_features=1,epochs=80)
    assert report['status']=='scored'
    assert report['train_cases']==9 and report['validation_cases']==9
    assert report['tp']==3 and report['fp']==0


def test_historical_snapshots_are_mandatory(tmp_path,monkeypatch):
    root=tmp_path/'run'
    root.mkdir()
    (root/'config.json').write_text(json.dumps({'schema':ff.SCHEMA}))
    q=args(run=str(root),checkpoint_source='event',ring=2)
    with pytest.raises(RuntimeError,match='SNAPSHOT_MISSING'):
        a.load_checkpoint_and_model(q)


def test_end_to_end_short_fake_model_and_resume(tmp_path,monkeypatch):
    # Run all stages without loading any external CUDA checkpoint.
    checkpoint=tmp_path/'fake.pt'
    checkpoint.write_bytes(b'fixed-state')
    monkeypatch.setattr(a,'load_checkpoint_and_model',lambda q:(object(),checkpoint,'final_checkpoint'))
    def fake_score(_,rows,device,batch_size):
        return [{**r,'prediction':r['card'][2]+(2 if r['census_id']%3==0 else 0),
                 'exact':r['census_id']%3!=0,'within_5pct':r['census_id']%3!=0} for r in rows]
    monkeypatch.setattr(a,'score_chunk',fake_score)
    def fake_event(_,center,dest,opts,idx):
        folder=dest/f'{center["split"]}_{idx:04d}'
        folder.mkdir(parents=True,exist_ok=True)
        a.atomic_checkpoint(folder/'selected_activations.pt',{'selected':[]})
        event={'schema':a.SCHEMA+'.event','center':center,'split':center['split'],
            'attribution_valid':False,'correct_controls':0,'top_channels':[],
            'interventions':[],'neighborhood':str(folder)}
        a.atomic_json(folder/'event.json',event)
        return event
    monkeypatch.setattr(a,'inspect_center',fake_event)
    q=args(run=str(tmp_path),campaign=str(tmp_path),hard_state_dir='h',
        output=str(tmp_path/'out'),census_cases=600)
    one=a.run(q)
    assert one['status']=='complete'
    assert one['census']['cases']==600
    assert (tmp_path/'out'/'census_heatmap.svg').exists()
    assert a.run(q)==one
    q.census_cases=650
    with pytest.raises(RuntimeError,match='CONFIG_OR_CHECKPOINT_DRIFT'):
        a.run(q)


def test_neighborhood_heatmaps_and_selected_activations(tmp_path,monkeypatch):
    # A deterministic pseudo-head fails one center surrounded by successes;
    # test the complete evidence path without requiring a user's CUDA state.
    center=a.make_variant(16,2,1,False)
    center.update({'split':a.independent_split(16,77),
                   'prediction':4,'exact':False,'within_5pct':False})
    def fixed_scores(_model,rows,device):
        scored=[];acts=[]
        for r in rows:
            failed=bool(r['original_center'])
            scored.append({**r,'prediction':4 if failed else r['card'][2],
                           'exact':not failed, 'within_5pct':not failed})
            acts.append({'norm@0':torch.tensor([1. if failed else 0.,
                                               float(r['delta']),float(r['m'])])})
        return scored,acts
    monkeypatch.setattr(a.nb,'score_with_activations',fixed_scores)
    monkeypatch.setattr(a.ff,'interventions',lambda *par:[
        {'layer':'norm@0','channel':0,'rescued':True,
         'correct_controls_lost':0,'correct_controls_total':3}])
    opt=args(m_window=2,delta_window=2,ablate_top=1)
    report=a.inspect_center(None,center,tmp_path,opt,0)
    dest=tmp_path/f'{center["split"]}_0000'
    assert report['neighborhood_wrong']==1
    assert report['correct_controls']==3
    assert report['interventions'][0]['rescued']
    assert report['top_channels'][0]['channel']==0
    assert all((dest/name).exists() for name in ('event.json','arithmetic_heatmap.svg',
        'activation_heatmap.svg','selected_activations.pt','local_predictions.jsonl.gz'))
    selected=torch.load(dest/'selected_activations.pt',weights_only=True)['selected']
    assert [r['role'] for r in selected].count('failure')==1
    assert [r['role'] for r in selected].count('easy')==3
