"""Atlas local smooth radius: discrete certification, adaptive boundaries, resume."""
import argparse
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_emp_atlas_smooth_radius as s
from tools import nanojev_emp_failure_atlas as a


def opt(**kwargs):
    settings=dict(device='cpu',max_m_radius=32,max_delta_radius=16,
        inner_radius=2,budget=450,refine_rounds=3,activation_budget=56,
        controls=8,ablate_top=0,activation_top=8,threshold=2.,batch_size=128,
        extra_discovery=0,extra_validation=0,max_centers=20)
    settings.update(kwargs)
    return argparse.Namespace(**settings)


def center(seed=77, m=40, d=0, sign=1):
    r=a.make_variant(m,d,sign,False)
    r.update(split=a.independent_split(m,seed),prediction=d+2,exact=False)
    return r


def fake_score(_model, entries, params):
    output=[]
    for r in entries:
        # A contiguous small rectangle of incorrect calculations with correct
        # lattice neighbors immediately outside. One wider distant error island.
        error=(2 if abs(r['m_offset'])<=2 and abs(r['delta_offset'])<=1
               else -10 if r['m_offset']>=16 and abs(r['delta_offset'])<=1 else 0)
        output.append({**r,'prediction':r['card'][2]+error,'exact':error==0,
                       'correct':error==0,'within_5pct':error==0})
    return output


def fake_capture(_model, card, device):
    m=int(card[0]);d=int(card[2]);
    # Controlled activation jump at this arithmetic failure region.
    incorrect=abs(m-40)<=2 and abs(d)<=1
    error=2 if incorrect else -10 if m-40>=16 and abs(d)<=1 else 0
    return d+error, {'backbone.layers.0@0':torch.tensor([
        float(m)/10, float(d), 5. if incorrect else 0.]),
        'norm@0':torch.tensor([10. if incorrect else 0.,float(d)])}


def test_dense_certificate_and_independent_directions():
    opt_ = opt()
    offsets=s.initial_offsets(opt_)
    assert all((dm,dd) in offsets for dm in range(-2,3) for dd in range(-2,3))
    assert (32,0) in offsets and (0,16) in offsets
    rows={}
    for dm in range(-3,4):
        for dd in range(-3,4):
            error=2 if abs(dm)<=2 and abs(dd)<=1 else 0
            rows[dm,dd]={'m_offset':dm,'delta_offset':dd,'error':error,
                         'exact':error==0}
    c=center()
    cert=s.certified_radii(rows,c,opt_)
    assert cert['failure']['certified_radius']==1
    assert cert['failure']['first_fully_sampled_break_radius']==2
    assert cert['same_error']['certified_radius']==1
    axis=s.directional_radii(rows,c)
    assert axis['m+']['nearest_sampled_success']==3
    assert axis['delta-']['nearest_sampled_success']==2
    assert s.sampled_component(rows)['sampled_connected_wrong_cells']==15
    assert not s.sampled_component(rows)['touches_unsampled_edge']


def test_refinement_finds_new_points_on_mixed_error_transition(monkeypatch):
    monkeypatch.setattr(s.ff,'score_cards',fake_score)
    observed,steps=s.explore(None,center(),opt(),77)
    assert observed[0,0]['prediction']==2
    assert observed[0,4]['exact']
    assert all(r['card'][0]+r['card'][1]==r['card'][2] for r in observed.values())
    assert len(observed)<=450
    assert steps
    assert any(x['added']>0 for x in steps)


def test_capture_gradients_with_genuine_controls(monkeypatch,tmp_path):
    monkeypatch.setattr(s.ff,'score_cards',fake_score)
    monkeypatch.setattr(s.ff,'capture',fake_capture)
    result=s.inspect(None,center(),tmp_path/'center',opt(),77)
    assert result['attribution_valid']
    assert result['wrong']>0 and result['correct']>0
    assert result['certified']['failure']['certified_radius']==1
    assert result['directional_observed']['m+']['nearest_sampled_success']==3
    assert result['activation']['available']
    assert result['activation_layers']['norm@0']['cross_boundary_unit_edges']>0
    assert (tmp_path/'center'/'arithmetic_map.svg').exists()
    assert (tmp_path/'center'/'activation_distance_map.svg').exists()
    assert (tmp_path/'center'/'selected_activations.pt').exists()
    assert len(list(a.stream_census(tmp_path/'census'))) == 0
    assert s.sha256(tmp_path/'center'/'samples.jsonl.gz') == json.loads((tmp_path/'center'/'samples.meta.json').read_text())['sha256']


def test_no_controls_do_not_produce_fake_activation_radii():
    row=center()
    data={(0,0):{'exact':False,'error':2,'m_offset':0,'delta_offset':0}}
    vec={'norm@0':torch.tensor([5.,1.])}
    evidence,dist,stats=s.layer_distances(data,{(0,0):vec},[],row,2.0,2)
    assert not evidence['available']
    assert not dist and not stats


def test_checkpoint_guard_and_config_validation(tmp_path):
    q=opt(budget=2)
    with pytest.raises(ValueError,match='INVALID_ARGUMENTS'):
        s.validate_options(q)
    c={'checkpoint':str(tmp_path/'missing.pt'),'checkpoint_sha256':'bad',
       'source_run':str(tmp_path),'checkpoint_mode':'final_checkpoint'}
    with pytest.raises(RuntimeError,match='SHA_MISMATCH'):
        s.model_for_atlas(tmp_path,c,opt())


def test_center_retrieval_without_rescanning_census(tmp_path):
    atlas_dir=tmp_path/'atlas'
    case=center()
    folder=atlas_dir/'events'/'discovery_0000'
    folder.mkdir(parents=True)
    (folder/'event.json').write_text(json.dumps({'center':case}))
    selected=s.source_centers(atlas_dir,{'seed':77,'discovery_centers':1,'validation_centers':0},opt())
    assert len(selected)==1
    assert selected[0]['center']['card']==case['card']


def test_complete_run_and_verified_resume(tmp_path,monkeypatch):
    source=tmp_path/'atlas';source.mkdir()
    sha='abc'
    conf={'schema':a.SCHEMA,'checkpoint_sha256':sha,
        'checkpoint':str(tmp_path/'checkpoint.pt'),'checkpoint_mode':'final_checkpoint',
        'source_run':str(tmp_path/'source_run'),
        'parameters':{'seed':77,'discovery_centers':1,'validation_centers':0}}
    a.atomic_json(source/'config.json',conf)
    a.atomic_json(source/'result.json',{'status':'complete'})
    folder=source/'events'/'discovery_0000';folder.mkdir(parents=True)
    a.atomic_json(folder/'event.json',{'center':center()})
    monkeypatch.setattr(s,'model_for_atlas',lambda *args:None)
    monkeypatch.setattr(s.ff,'score_cards',fake_score)
    monkeypatch.setattr(s.ff,'capture',fake_capture)
    q=opt(atlas=str(source),output=str(tmp_path/'out'),campaign='x',
          ladder_output='y',hard_state_dir='h')
    one=s.run(q)
    assert one['events']==1 and one['events_with_controls']==1
    assert (tmp_path/'out'/'result.json').exists()
    assert s.run(q)==one
    q.threshold=3.
    with pytest.raises(RuntimeError,match='RESUME_CONFIG_DRIFT'):
        s.run(q)


def test_capture_hook_drift_recovers_norm_without_changing_scan(monkeypatch):
    row={'card':[2,22,24,0,22], 'prediction':26,'exact':False,
         'm_offset':0,'delta_offset':0}
    full={'backbone.layers.0@0':torch.tensor([1.,2.]),
          'norm@0':torch.tensor([2.,3.])}
    norm={'norm@0':torch.tensor([4.,5.])}
    monkeypatch.setattr(s.ff,'capture',lambda *_:(28,full))
    monkeypatch.setattr(s.ff,'score_cards',lambda _model, rows, args:[
        {**rows[0],'prediction':26,'exact':False}])
    monkeypatch.setattr(s,'capture_norm_without_encoder_hooks',lambda *_:(26,norm))
    usable,raw,metadata,comparisons=s.capture_selected(None,[row],'cpu')
    assert metadata['full_hook_disagreements']==1
    assert metadata['norm_only_recoveries']==1
    assert metadata['usable_activation_cases']==1
    assert metadata['center_capture_matches']
    assert metadata['center_capture_mode']=='norm_only'
    assert comparisons[0]['full_hook_sensitive']
    assert not comparisons[0]['batch_sensitive']
    assert usable[0,0]==norm
    assert raw[0,0]==full
    assert row['prediction']==26  # No post hoc relabeling of wrong answer
    assert 'backbone.layers.0@0' not in usable[0,0]


def test_capture_disagreement_not_a_false_boundary(monkeypatch,tmp_path):
    monkeypatch.setattr(s.ff,'score_cards',fake_score)
    def wrong_hook_capture(_model,card,device):
        pred,acts=fake_capture(_model,card,device)
        if card[0]==40 and card[2]==0:
            pred=1  # Both captures disagree with original center's +2 error.
        return pred,acts
    monkeypatch.setattr(s.ff,'capture',wrong_hook_capture)
    def wrong_norm_capture(_model,card,device):
        pred,acts=fake_capture(_model,card,device)
        if card[0]==40 and card[2]==0:
            pred=1
        return pred,{'norm@0':acts['norm@0']}
    monkeypatch.setattr(s,'capture_norm_without_encoder_hooks',wrong_norm_capture)
    report=s.inspect(None,center(),tmp_path/'center',opt(),77)
    assert report['capture_consistency']['center_capture_matches'] is False
    assert report['capture_consistency']['uncertified_cases']>=1
    assert not report['attribution_valid']
    assert not report['activation']['available']
    assert report['top_channels']==[] and report['interventions']==[]
    assert report['wrong']>0 and report['sampled_points']>0
    entries=torch.load(tmp_path/'center'/'selected_activations.pt',weights_only=True)['selected']
    center_entry=next(x for x in entries if x['m_offset']==x['delta_offset']==0)
    assert center_entry['usable_activation'] is None
    assert center_entry['capture']['scanned_prediction']==2
    assert center_entry['capture']['capture_mode']=='uncertified'
    assert (tmp_path/'center'/'arithmetic_map.svg').exists()


def test_norm_only_capture_real_addition_model():
    from tools.nanojev_addition_curriculum import AdditionNet
    from tools.nanojev_emp_dual_bank import decoded
    from tools.nanojev_zero_line_zero_only import input_tensor
    model=AdditionNet('compact').eval()
    card=(2,22,24,0,22)
    with torch.inference_mode():
        expected=decoded(model(input_tensor([card],'cpu')))[0]
    prediction,activations=s.capture_norm_without_encoder_hooks(model,card,'cpu')
    assert prediction==expected
    assert 'norm@0' in activations and 'norm@1' in activations
    assert all(v.shape==(64,) for v in activations.values())
    assert not model.norm._forward_hooks


def test_mixed_full_and_norm_capture_does_not_invent_backbone_gradient():
    c=center()
    rows={}
    for dm in (-1,0,1):
        for dd in (-1,0,1):
            rows[dm,dd]={'m_offset':dm,'delta_offset':dd,
                         'exact':not(dm==0 and dd==0), 'error':2 if dm==dd==0 else 0}
    all_layers={'backbone.layers.0@0':torch.tensor([1.,2.]),
                'norm@0':torch.tensor([3.,4.])}
    only_norm={'norm@0':torch.tensor([5.,6.])}
    acts={k:dict(all_layers) for k in rows}
    acts[1,0]=only_norm
    controls=[rows[-1,-1],rows[-1,0]]
    evidence,dist,layers=s.layer_distances(rows,acts,controls,c,2.,1)
    assert evidence['available']
    assert 'backbone.layers.0@0' in layers
    assert layers['backbone.layers.0@0']['cross_boundary_unit_edges']<4
    # An unsampled backbone output cannot be promoted to a certified radius.
    assert layers['backbone.layers.0@0']['activation_fully_sampled_through']<1
    assert set(dist[1,0])=={'norm@0'}
