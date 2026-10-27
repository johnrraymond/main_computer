"""Independent first-failure diagnostic tests (CPU, tiny models)."""
import copy
import json
from argparse import Namespace

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_emp_first_failure as ff


def args(tmp_path, **changes):
    defaults = dict(campaign=str(tmp_path/'campaign'), ladder_output=str(tmp_path/'ladder'),
        hard_state_dir='hard_emp_frontier_v2',output=str(tmp_path/'result'),
        device='cpu',seed=20261025,initial_magnitude=16,initial_delta=2,
        magnitude_growth=2,delta_step=1,max_delta=8,max_magnitude=128,
        rings=2,train_cards=32,probe_anchors=8,loss_target=.01,max_steps=2,
        check_every=1,batch=8,eval_batch=32,lr=.0001,neighbors=4,
        activation_top=6,ablate_top=3,failure_mode='exact')
    defaults.update(changes)
    return Namespace(**defaults)


def tiny():
    return AdditionNet('compact', width=8,heads=2,layers=1,rounds=2)


def test_shells_reproducible_gold_and_disjoint(tmp_path):
    a=args(tmp_path)
    train=ff.training_deck(a,0)
    probe=ff.probe_deck(a,1)
    assert train==ff.training_deck(a,0)
    assert probe==ff.probe_deck(a,1)
    assert len(train)==a.train_cards and len(probe)>0
    assert set(c[:2] for c in train).isdisjoint(set(row['card'][:2] for row in probe))
    assert all(c[0]+c[1]==c[2] and ff.split_card(c,a.seed)=='train' for c in train)
    assert all(r['card'][0]+r['card'][1]==r['card'][2] and ff.split_card(r['card'],a.seed)=='dev' for r in probe)
    assert ff.delta_order(2)==[0,1,-1,2,-2]
    assert (ff.schedule(a,0),ff.schedule(a,1)) == ((16,2),(32,3))


def test_capture_all_rounds_no_residual_hooks(tmp_path):
    net=tiny()
    sample=ff.card(30,-29)
    before=net(input_tensor := torch.tensor([[30,-29]]))
    guess, layers=ff.capture(net,sample,'cpu')
    assert len(layers)==4 and set(layers)=={
        'backbone.layers.0@0','backbone.layers.0@1','norm@0','norm@1'}
    assert len({id(x) for x in layers.values()})==4
    assert all(v.shape==(8,) for v in layers.values())
    after=net(input_tensor)
    assert torch.allclose(before['sign'],after['sign'])
    assert isinstance(guess,int)


def test_local_controls_rank_and_ablation(tmp_path):
    net=tiny()
    a=args(tmp_path)
    rows=ff.probe_deck(a,1)[:16]
    measured=ff.score_cards(net,rows,a)
    f=measured[0]
    correct=[{**row,'correct':True} for row in measured[1:7]]
    close, fail=ff.choose_neighbors(f,[f,*correct],4)
    assert len(close)==4 and not fail
    _, failure=ff.capture(net,f['card'],'cpu')
    controls=[ff.capture(net,r['card'],'cpu')[1] for r in close]
    ranking=ff.rank_channels(failure,controls)
    assert len(ranking)==4*8
    assert ranking==sorted(ranking,key=lambda r:(-r['anomaly'],r['layer'],r['channel']))
    original=ff.capture(net,f['card'],'cpu')[0]
    altered=ff.ablation_prediction(net,f['card'],'cpu',ranking[0]['layer'],ranking[0]['channel'])
    assert isinstance(altered,int)
    assert ff.capture(net,f['card'],'cpu')[0]==original
    results=ff.interventions(net,f,close,ranking,a)
    assert len(results)==3 and all('correct_controls_lost' in r for r in results)


def test_archive_event_produces_maps_and_tensor_evidence(tmp_path):
    net=tiny()
    a=args(tmp_path)
    all_rows=ff.score_cards(net,ff.probe_deck(a,1),a)
    failure=next((row for row in all_rows if not row['correct']),None)
    if failure is None:
        pytest.skip('Untrained tiny model unexpectedly solved whole shell')
    # Ensure controls even if the tiny random model misses everything.
    for row in all_rows[1:8]:
        if row['card'] != failure['card']:
            row['correct']=True
    event=ff.save_event(tmp_path,1,failure,all_rows,net,a)
    assert (tmp_path/'ring_01'/'event.json').is_file()
    assert '<svg' in (tmp_path/'ring_01'/'error_heatmap.svg').read_text()
    evidence=torch.load(tmp_path/'ring_01'/'activations.pt',weights_only=True)
    assert evidence['schema']==ff.SCHEMA+'.activations'
    assert len(evidence['failure'])==4
    assert event['all_channel_ranks']>0
    assert len(event['interventions'])==3


def test_memo_gate_prevents_false_boundary(tmp_path,monkeypatch):
    a=args(tmp_path,max_steps=1,loss_target=1e-10)
    sample=tiny()
    opt=torch.optim.AdamW(sample.parameters(),lr=.0001)
    record=ff.memorize(sample,opt,ff.training_deck(a,0),a,0)
    assert not record['memorized']
    assert record['steps']==1


def test_run_isolation_resume_and_fail_closed_on_config(tmp_path,monkeypatch):
    a=args(tmp_path,rings=1,ablate_top=1,train_cards=16)
    a.output=str(tmp_path/'experiment')
    root=tmp_path/'campaign'/'hard_emp_frontier_v2'
    root.mkdir(parents=True)
    (root/'latest.pt').write_bytes(b'existing-specialist')
    source=tiny()
    opt=torch.optim.AdamW(source.parameters(),lr=.0001)
    # Source's read-only digest is also pinned into the experiment config.
    def load(*_):
        return copy.deepcopy(source),None,copy.deepcopy(opt.state_dict()),'hard-digest','reverse-digest'
    monkeypatch.setattr(ff.lasso,'initialize',load)
    monkeypatch.setattr(ff,'memorize',lambda *a:{'steps':0,'deck':16,'mean_ce':0.,
                                             'exact_accuracy':1.,'memorized':True})
    result=ff.run(a)
    assert result['status']=='complete'
    assert (tmp_path/'experiment'/'latest.pt').is_file()
    assert (root/'latest.pt').read_bytes()==b'existing-specialist'
    assert ff.run(a)['status']=='complete'
    a.delta_step=3
    with pytest.raises(RuntimeError,match='CONFIG_DRIFT'):
        ff.run(a)


def test_neighborhood_controls_by_proximity():
    base={'card':ff.card(10,-9),'m':10,'delta':1,'sign':1,'swap':False,'correct':False}
    match={'card':ff.card(11,-10),'m':11,'delta':1,'sign':1,'swap':False,'correct':True}
    remote={'card':ff.card(100,-97),'m':100,'delta':3,'sign':1,'swap':False,'correct':True}
    near,wrong=ff.choose_neighbors(base,[base,remote,match],2)
    assert near[0] is match and not wrong


def test_repeated_checkpoint_source_unchanged(tmp_path,monkeypatch):
    a=args(tmp_path,rings=1,ablate_top=0,train_cards=16)
    source=tiny()
    opt=torch.optim.AdamW(source.parameters(),lr=.0001)
    initial={key:v.clone() for key,v in source.state_dict().items()}
    monkeypatch.setattr(ff.lasso,'initialize',lambda *x:(copy.deepcopy(source),None,opt.state_dict(),'x','y'))
    monkeypatch.setattr(ff,'memorize',lambda *x:{'steps':0,'deck':16,'mean_ce':0.,'exact_accuracy':1.,'memorized':True})
    ff.run(a)
    assert all(torch.equal(value,initial[key]) for key,value in source.state_dict().items())


def test_no_correct_controls_does_not_invent_neuron_ranking(tmp_path):
    net=tiny()
    a=args(tmp_path)
    rows=ff.score_cards(net,ff.probe_deck(a,1)[:6],a)
    for row in rows:
        row['correct']=False
    event=ff.save_event(tmp_path,1,rows[0],rows,net,a)
    assert event['attribution_valid'] is False
    assert not event['top_activation_differences']
    assert not event['interventions']
    assert 'No nearby correct controls' in (tmp_path/'ring_01'/'neuron_heatmap.svg').read_text()


def test_resume_reconstructs_history_after_checkpoint_only_crash(tmp_path,monkeypatch):
    a=args(tmp_path,rings=1,train_cards=16,ablate_top=0)
    src=tiny()
    opt=torch.optim.AdamW(src.parameters(),lr=.0001)
    monkeypatch.setattr(ff.lasso,'initialize',lambda *x:(copy.deepcopy(src),None,opt.state_dict(),'abc','def'))
    monkeypatch.setattr(ff,'memorize',lambda *x:{'steps':0,'deck':16,'mean_ce':0.,'exact_accuracy':1.,'memorized':True})
    ff.run(a)
    out=tmp_path/'result'
    (out/'history.json').write_text('[]')
    assert ff.run(a)['status']=='complete'
    assert len(json.loads((out/'history.json').read_text()))==1
