"""CPU contract and end-to-end tests; no real user checkpoints are required."""
import copy
import json
import random
from pathlib import Path

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools import nanojev_zero_line_signed_radius16 as r


def args(tmp_path):
    v = r.parser().parse_args(['--device','cpu', '--ladder-output',str(tmp_path/'ladder'),
              '--zero-only-output',str(tmp_path/'zero'), '--output',str(tmp_path/'new'),
              '--batch-size','33','--legacy-batch-size','4','--legacy-probe-cards','12',
              '--anchors-per-band','3','--train-probe-anchors','1',
              '--parent-anchor-count','2','--max-steps','1','--probe-every','1',
              '--eval-batch-size','256','--cpu-threads','2','--stability-probes','2'])
    return v


def fake_source(monkeypatch, tmp_path):
    src=tmp_path/'source.pt'
    src.write_bytes(b'original-certification')
    cfg=tmp_path/'zero'/'config.json'
    cfg.parent.mkdir(parents=True)
    model=AdditionNet('digit_tokens',8,2,1,1,42)
    srcinfo={'checkpoint':str(src),'checkpoint_sha256':r.sha256(src),
             'config_sha256':r.sha256(src),'step':600,'source_radius':127,'source_streak':3}
    cfg.write_text(json.dumps({'schema':r.ZERO_SCHEMA,'settings':{'seed':20261025},
                               'certified_source':srcinfo}))
    cp=tmp_path/'zero'/'promoted.pt'
    torch.save({'schema':r.ZERO_SCHEMA+'.promoted','model':model.state_dict(),
                'step':1000,'historical_exact':65025,'source_checkpoint_sha256':srcinfo['checkpoint_sha256'],
                'config_sha256':r.sha256(cfg),'dev':{'passes':True},'heldout':{'passes':True}},cp)
    def loader(_ladder,device):
        m=AdditionNet('digit_tokens',8,2,1,1,42).to(device)
        m.load_state_dict(copy.deepcopy(model.state_dict()))
        return m,dict(srcinfo)
    monkeypatch.setattr(r,'load_certified_model',loader)
    return src,cp,cfg


def test_every_integer_target_exactly_once():
    assert r.RESIDUALS==tuple(range(-16,17))
    assert len(r.RESIDUALS)==33
    assert sum(d==0 for d in r.RESIDUALS)==1


def test_family_splits_are_stable_and_test_holds_endpoint():
    assert r.magnitude_fold(2_000_000)=='test'
    assert {r.magnitude_fold(m) for m in range(128,256)}==set(r.SPLITS)
    assert r.magnitude_fold(200,17)==r.magnitude_fold(200,17)


def test_endpoint_all_deltas_valid_and_signed_swap():
    for delta in r.RESIDUALS:
        for swapped in (False,True):
            pair=r.pair_for_max(2_000_000,delta,swapped)
            assert sum(pair)==delta
            assert max(map(abs,pair))==2_000_000
            assert all(abs(a)<=2_000_000 for a in pair)
    assert r.pair_for_max(13,16) is None
    assert r.pair_for_max(500,0,False,-1)==(-500,500)
    assert r.pair_for_max(500,0,False,1)==(500,-500)


def test_probe_matrix_all_429_cells_and_no_family_leak():
    splits={s:r.build_matrix(s,20261116,3) for s in r.SPLITS}
    for split,rows in splits.items():
        assert len(set((band,d) for _,_,d,band,_ in rows))==429
        assert any(d==0 for _,_,d,_,_ in rows)
        for a,b,d,band,m in rows:
            assert a+b==d and m==max(abs(a),abs(b))
            assert r.magnitude_fold(m)==split
            assert r.BANDS[band][0]<=m<=r.BANDS[band][1]
    bysplit={s:set(row[4] for row in rows) for s,rows in splits.items()}
    for s in splits:
        for t in splits:
            if s!=t: assert not bysplit[s]&bysplit[t]
    assert 2_000_000 in bysplit['test']


def test_33_balanced_replay_and_train_only():
    rng=random.Random(11)
    rows,cursor=r.train_cards(rng,132,20261116,[1.]*len(r.BANDS),0)
    assert cursor==132
    counts={d:sum(row[2]==d for row in rows) for d in r.RESIDUALS}
    assert set(counts.values())=={4}
    assert all(r.magnitude_fold(row[4])=='train' for row in rows)
    rows,cursor=r.train_cards(rng,128,20261116,[1.]*len(r.BANDS),0)
    assert set(sum(row[2]==d for row in rows) for d in r.RESIDUALS)=={3,4}
    assert cursor==128


def test_joint_lattice_eventually_covers_all_429_cells():
    rng=random.Random(7)
    cursor=0
    combos=set()
    for _ in range(13):
        batch,cursor=r.train_cards(rng,33,20261116,[1.]*len(r.BANDS),cursor)
        combos.update((row[3],row[2]) for row in batch)
    assert len(combos)==13*33
    assert cursor==429


def test_loss_supervises_nonzero_sign_and_every_digit():
    torch.manual_seed(2)
    output={'sign':torch.randn(33,3,requires_grad=True),
            'digits':torch.randn(33,7,10,requires_grad=True)}
    loss=r.exact_supervision(output,list(r.RESIDUALS),3.)
    loss.backward()
    assert torch.isfinite(loss)
    assert output['sign'].grad.abs().sum()>0
    assert output['digits'].grad.abs().sum()>0
    assert output['sign'].grad[0,0]!=0  # negative sign is supervised
    assert output['sign'].grad[-1,2]!=0  # positive sign is supervised


def test_perfect_zero_model_fails_nonzero_cells():
    class Zero(torch.nn.Module):
        def forward(self,x):
            s=torch.tensor([[0.,12.,0.]]).expand(len(x),-1)
            d=torch.zeros(len(x),7,10);d[:,:,0]=12.
            return {'sign':s,'digits':d}
    rows=r.build_matrix('dev',20261116,3)
    report=r.assess(Zero(),rows,'cpu')
    assert report['every_cell_populated']
    assert report['worst_cell_exact']==0.
    assert not report['passes']
    assert report['matrix']['192-255']['0']['exact']==1.
    assert report['matrix']['192-255']['-16']['exact']==0.


def test_model_exact_labels_are_not_network_inputs():
    class Probe(torch.nn.Module):
        def forward(self,x):
            assert x.ndim==2 and x.shape[1]==2
            return {'sign':torch.randn(len(x),3),'digits':torch.randn(len(x),7,10)}
    r.assess(Probe(),[(500,-500,0,3,500),(500,-499,1,3,500)],'cpu')


def test_real_backbone_update_with_frozen_heads():
    m=AdditionNet('digit_tokens',8,2,1,1,7)
    t=AdditionNet('digit_tokens',8,2,1,1,7).eval().requires_grad_(False)
    for name,p in m.named_parameters():
        p.requires_grad_(name.startswith('backbone.') or name.startswith('norm.') or name in ('query','pos'))
    optimizer=torch.optim.AdamW([p for p in m.parameters() if p.requires_grad],lr=1.e-4)
    from argparse import Namespace
    a=Namespace(batch_size=33,seed=20261116,device='cpu',legacy_batch_size=4,legacy_weight=2.,zero_weight=3.)
    before=m.backbone.layers[0].self_attn.in_proj_weight.detach().clone()
    head=m.sign_head.weight.detach().clone()
    loss,delta,legacy,cursor=r.train_step(m,t,optimizer,random.Random(4),a,[1.]*13,0)
    assert loss>0 and delta>0 and legacy>0
    assert cursor==33
    assert not torch.equal(before,m.backbone.layers[0].self_attn.in_proj_weight)
    assert torch.equal(head,m.sign_head.weight)


def test_source_requires_true_promotion_and_provenance(tmp_path,monkeypatch):
    src,cp,cfg=fake_source(monkeypatch,tmp_path)
    _model, origin=r.source_model('ignored',tmp_path/'zero','cpu')
    assert origin['zero_promoted_sha256']==r.sha256(cp)
    x=torch.load(cp,weights_only=True);x['historical_exact']=65024
    torch.save(x,cp)
    with pytest.raises(RuntimeError,match='PROVENANCE_MISMATCH'):
        r.source_model('ignored',tmp_path/'zero','cpu')


def test_plan_needs_no_checkpoint(tmp_path):
    a=args(tmp_path);a.phase='plan'
    plan=r.run(a)
    assert plan['target_count']==33 and plan['historical_full_deck']==65025


def test_cpu_train_restart_sources_immutable(tmp_path,monkeypatch):
    src,cp,cfg=fake_source(monkeypatch,tmp_path)
    hashes=[r.sha256(f) for f in (src,cp,cfg)]
    a=args(tmp_path)
    out=r.run(a)
    assert out['status']=='budget_exhausted_no_promotion'
    assert (Path(a.output)/'latest.pt').exists()
    assert not (Path(a.output)/'promoted.pt').exists()
    a.resume=True;a.max_steps=2
    out=r.run(a)
    assert out['last_step']==2
    assert [r.sha256(f) for f in (src,cp,cfg)]==hashes


def test_restart_config_drift_refused(tmp_path,monkeypatch):
    fake_source(monkeypatch,tmp_path)
    a=args(tmp_path);r.run(a)
    a.resume=True;a.lr*=2
    with pytest.raises(RuntimeError,match='DRIFT'):
        r.run(a)


def test_sealed_test_hard_stops_restart(tmp_path,monkeypatch):
    fake_source(monkeypatch,tmp_path)
    a=args(tmp_path);r.run(a)
    (Path(a.output)/'test_opened.json').write_text('{"status":"already opened"}')
    a.resume=True
    with pytest.raises(RuntimeError,match='SEALED_TEST_ALREADY_OPENED'):
        r.run(a)


def test_perfect_dev_wrong_legacy_refuses_promotion(tmp_path,monkeypatch):
    fake_source(monkeypatch,tmp_path)
    a=args(tmp_path);a.max_steps=2
    def perfect(model,rows,device,batch_size):
        return {'cards':len(rows),'passes':True,'worst_cell_exact':1.,'matrix':{
            f'{lo}-{hi}':{str(d):{'exact':1.} for d in r.RESIDUALS} for lo,hi in r.BANDS}}
    def zpass(model,rows,device,batch_size): return {'passes':True,'min_zero':1.}
    def history(model,rows,*_):
        n=len(rows['y']);correct=n-1 if n==65025 else n
        return {'groups':{'all':{'passed':correct,'accuracy':correct/n}},'false_zero':0}
    monkeypatch.setattr(r,'assess',perfect)
    monkeypatch.setattr(r,'zero_classify',zpass)
    monkeypatch.setattr(r,'legacy_evaluate',history)
    out=r.run(a)
    assert out['status']=='budget_exhausted_no_promotion'
    assert out['historical_full']['groups']['all']['passed']==65024
    assert not (Path(a.output)/'test_opened.json').exists()
    assert not (Path(a.output)/'promoted.pt').exists()


def test_one_time_heldout_failure_refuses_promotion(tmp_path,monkeypatch):
    fake_source(monkeypatch,tmp_path)
    a=args(tmp_path);a.max_steps=2
    calls=[0]
    def assess(model,rows,device,batch_size):
        calls[0]+=1
        # baseline=1; step1 train2/dev3; step2 train4/dev5; TEST=6
        ok=calls[0]!=6
        return {'cards':len(rows),'passes':ok,'worst_cell_exact':1. if ok else 0.,'matrix':{
            f'{lo}-{hi}':{str(d):{'exact':1.} for d in r.RESIDUALS} for lo,hi in r.BANDS}}
    monkeypatch.setattr(r,'assess',assess)
    monkeypatch.setattr(r,'zero_classify',lambda *_:{'passes':True,'min_zero':1.})
    def good(model,rows,*_):
        n=len(rows['y']);return {'groups':{'all':{'passed':n,'accuracy':1.}},'false_zero':0}
    monkeypatch.setattr(r,'legacy_evaluate',good)
    out=r.run(a)
    assert out['status']=='heldout_refused'
    assert (Path(a.output)/'test_opened.json').is_file()
    assert not (Path(a.output)/'promoted.pt').exists()
    a.resume=True;a.max_steps=3
    with pytest.raises(RuntimeError,match='SEALED_TEST_ALREADY_OPENED'):
        r.run(a)


def test_success_promoted_contains_source_hash_and_429_cell_test(tmp_path,monkeypatch):
    src,cp,cfg=fake_source(monkeypatch,tmp_path)
    a=args(tmp_path);a.max_steps=2
    def perfect(model,rows,device,batch_size):
        return {'cards':len(rows),'passes':True,'worst_cell_exact':1.,'matrix':{
            f'{lo}-{hi}':{str(d):{'exact':1.} for d in r.RESIDUALS} for lo,hi in r.BANDS}}
    monkeypatch.setattr(r,'assess',perfect)
    monkeypatch.setattr(r,'zero_classify',lambda *_:{'passes':True,'min_zero':1.})
    def good(model,rows,*_):
        n=len(rows['y']);return {'groups':{'all':{'passed':n,'accuracy':1.}},'false_zero':0}
    monkeypatch.setattr(r,'legacy_evaluate',good)
    out=r.run(a)
    assert out['status']=='promoted'
    promoted=torch.load(Path(a.output)/'promoted.pt',weights_only=True)
    assert promoted['source_zero_promoted_sha256']==r.sha256(cp)
    assert promoted['historical_exact']==65025
    assert promoted['sealed_test']['passes']
