import json
from pathlib import Path
from unittest.mock import patch
import types
import sys

import numpy as np
import pytest
import torch

import nanojev_register_online_hierarchy as h


def test_exact_stage_five_structure():
    assert h.CUTS == (20, 200, 2000, 20000, 200000)
    assert h.REGIMES == 6
    assert h.HEADS_PER_REGISTER == 19
    assert sum(p.numel() for p in h.model_factory(torch).parameters()) == 311334


def test_band_boundaries():
    values = np.array([0, -1, 20, -21, 200, 201, 2000, -2001, 20000, 20001,
                       200000, 200001, 2000000])
    np.testing.assert_array_equal(h.band_of(values),
       [0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5])


def test_expert_roundtrip_all_regimes():
    for b in range(h.REGIMES):
        lo, hi = h.band_limits(b)
        values = np.array([lo, (lo+hi)//2, hi], dtype=np.int64)
        enc = h.expert_encode(values, np.full(len(values), b))
        rec = h.expert_decode(enc, b)
        np.testing.assert_allclose(rec, values, rtol=1e-6, atol=0.4)


def test_two_registers_oracle_recovery_exact():
    y = np.array([[0,-1],[20,201],[-2000,20000],[200001,-2000000]])
    _, bands, normalized = h.supervision(torch, y, 'cpu')
    raw = np.full((len(y),2,h.HEADS_PER_REGISTER), -10., np.float32)
    for i in range(len(y)):
        for p in range(2):
            v=y[i,p]
            raw[i,p,h.expert_index(int(bands[i,p]), int(np.sign(v)))] = float(normalized[i,p])
    np.testing.assert_array_equal(h.decode(raw, oracle_numbers=y), y)


def test_predicted_router_not_ground_truth_router():
    y=np.array([[2100, -25000]])
    _, bands, local = h.supervision(torch,y,'cpu')
    raw = np.zeros((1,2,h.HEADS_PER_REGISTER),np.float32)
    for p in range(2):
        v=int(y[0,p]); r=int(bands[0,p]); sign=int(np.sign(v))
        raw[0,p,0]=-10.0
        raw[0,p,1]=10.0 if v>0 else -10.0
        for i,cut in enumerate(h.CUTS):
            raw[0,p,2+i]=10.0 if abs(v)>cut else -10.0
        raw[0,p,h.expert_index(r,sign)]=float(local[0,p])
    np.testing.assert_array_equal(h.decode(raw),y)
    raw[0,0,2]=-10.0
    assert h.decode(raw)[0,0]!=y[0,0]
    np.testing.assert_array_equal(h.decode(raw,oracle_numbers=y),y)


def test_loss_uses_expert_masks_and_individual_gradients():
    sample = [[0,-1],[25,300],[2100,-25000],[-300000, 2000000],
              [2,-200],[2001,0],[-40000,220000]]
    n, bands, loc = h.supervision(torch,sample,'cpu')
    out = torch.zeros((len(sample),38),requires_grad=True)
    loss, active = h.hierarchy_loss(torch,out,n,bands,loc)
    assert 10 < active <= 38
    loss.backward()
    assert torch.isfinite(out.grad).all()
    used = out.grad.abs().sum(dim=0)
    assert torch.count_nonzero(used)>10
    # Expert for signed large band has zero gradient on unrelated rows.
    col=h.expert_index(5,-1)
    assert torch.count_nonzero(out.grad[:3,col]) == 0  # only row 3 is negative in top band
    assert out.grad[3,col] != 0


def test_rare_expert_gradients_only_selected_examples():
    n,bands,loc = h.supervision(torch, [[1,1],[-2000000,2]], 'cpu')
    out=torch.randn(2,38,requires_grad=True)
    L,_=h.hierarchy_loss(torch,out,n,bands,loc)
    L.backward()
    col=h.expert_index(5,-1)
    assert out.grad[0,col].item() == 0.0
    assert out.grad[1,col].item() != 0.0


def test_balanced_generator_benchmark_separation_and_holdouts():
    _,tasks,_,_=h.v3.build_plan()
    stream=h.make_stream(100, tasks,'balanced')
    sample=stream.batch(260)
    assert len(sample)==260
    assert len({(r['a'],r['b']) for r in sample})==260
    assert not set((r['a'],r['b']) for r in sample) & h.previous.reserved_pairs(tasks)
    assert not set(r['operation'] for r in sample) & set(h.v3.HELD_OUT_OPERATIONS)
    values=np.array([v for r in sample for v in (r['a'],r['b'])])
    for b in range(6):
        assert np.count_nonzero((h.band_of(values)==b)&(values>0)) >= 20
        assert np.count_nonzero((h.band_of(values)==b)&(values<0)) >= 20
    assert np.count_nonzero(values==0)>=20


def test_balanced_generator_resume_exact():
    stream=h.BalancedFreshPairs(13,{(9,9)})
    stream.batch(25)
    state=stream.state_dict()
    next_batch=stream.batch(20)
    restored=h.BalancedFreshPairs(13,{(9,9)})
    restored.load_state_dict(state)
    assert restored.batch(20)==next_batch


def test_checkpoint_roundtrip_exact_cpu(tmp_path):
    torch.manual_seed(23)
    n=h.model_factory(torch)
    opt=torch.optim.AdamW(n.parameters(),lr=.001)
    gen=h.BalancedFreshPairs(55,{(3,3)})
    gen.batch(21)
    cfg={'architecture':'hier38'};identity={'model':'qwen'}
    p=tmp_path/'model.pt'
    h.save_checkpoint(torch,p,n,opt,15,cfg,gen,{'joint':.1,'loss':.22},identity)
    fresh=h.model_factory(torch)
    fresh_opt=torch.optim.AdamW(fresh.parameters(),lr=.001)
    fresh_gen=h.BalancedFreshPairs(55,{(3,3)})
    step,best=h.load_checkpoint(torch,p,fresh,fresh_opt,fresh_gen,cfg,identity)
    assert step==15 and best['joint']==.1
    assert all(torch.equal(a,b) for a,b in zip(n.parameters(),fresh.parameters()))
    assert gen.batch(4)==fresh_gen.batch(4)
    with pytest.raises(RuntimeError,match='CONFIG_OR_BACKBONE'):
        h.load_checkpoint(torch,p,fresh,fresh_opt,fresh_gen,{'architecture':'wrong'},identity)


def test_score_diagnostics_and_checkpoint_tiebreaker():
    n=h.model_factory(torch)
    x=np.random.default_rng(1).normal(size=(12,h.INPUT_WIDTH)).astype('float32')
    y=np.array([[10, -10],[0,1],[2000000,-2000000],[100,100]]*3)
    d=h.score(torch,n,(x,y,['sym_add']*12,['symbolic']*12),'cpu')
    assert d['rows']==12
    assert 0<=d['joint_pass_rate']<=1
    assert 'oracle_routing_joint_pass_rate' in d
    assert 'x_sign_accuracy' in d and 'x_regime_accuracy' in d
    assert 'true_bucket_expert_normalized_mae' in d
    assert np.isfinite(d['head_validation_loss'])
    assert h.better_checkpoint({'joint_pass_rate':0.,'head_validation_loss':.2}, {'joint':0.,'loss':.3})
    assert not h.better_checkpoint({'joint_pass_rate':0.,'head_validation_loss':.4}, {'joint':0.,'loss':.3})


def test_end_to_end_fake_qwen_train_resume_and_eval(tmp_path,monkeypatch):
    root=tmp_path/'benchmark';root.mkdir()
    (root/'model_identity.json').write_text(json.dumps({'model':'qwen','model_info':{'resolved_commit':'abc',
                                                       'source':'synthetic','dtype':'float32'}}))
    # Real code passes through frozen capture helper; replace ONLY IO and frozen model
    # for this offline simulation. Verify train -> checkpoint -> resume -> evaluation.
    fake_tasks=[{'a':a,'b':b,'split':'val','operation':'sym_add','family':'symbolic'}
                for a,b in [(1,22),(-200,0),(300000,-50),(7,8)]]
    fake_tasks += [{'a':99,'b':98,'split':'test','operation':'sym_add','family':'symbolic'}]
    fake_tasks += [{'a':13,'b':-2,'split':'control','operation':'written_average','family':'written'}]
    monkeypatch.setattr(h.previous,'read_benchmark', lambda root: ({'tasks_sha256':'fake'},fake_tasks))
    def eval_data(root,manifest,tasks,split,count=None,seed=19):
        choices=[t for t in tasks if t['split']==split]
        features=np.zeros((len(choices), h.INPUT_WIDTH),dtype='float32')
        features[:,0]=[x['a'] / h.MAX_VALUE for x in choices]
        features[:,1]=[x['b'] / h.MAX_VALUE for x in choices]
        return features,np.array([[x['a'],x['b']] for x in choices]),[x['operation'] for x in choices],[x['family'] for x in choices]
    monkeypatch.setattr(h.previous,'load_eval',eval_data)
    monkeypatch.setattr(h.previous,'verify_capture_alignment',lambda *a,**k:None)
    def capture(torch_,model,tokenizer,prompts,dev,limit):
        feat=torch.zeros(len(prompts),h.INPUT_WIDTH,dtype=torch.float32,device=dev)
        return feat
    monkeypatch.setattr(h.previous,'extract_batched',capture)
    fake = types.ModuleType('nanojev_numerate_spine_cassette_smoke')
    fake.load_source=lambda *a:(None,types.SimpleNamespace(eval=lambda:None),None,
              torch.device('cpu'),{'resolved_commit':'abc','source':'synthetic','dtype':'float32'})
    monkeypatch.setitem(sys.modules,'nanojev_numerate_spine_cassette_smoke',fake)
    result=tmp_path/'result'
    cmd=['--v3-root',str(root),'--results-root',str(result),'--device','cpu',
         '--dtype','float32','--batch-size','4','--steps','2','--eval-every','1',
         '--checkpoint-every','1','--report-every','1']
    assert h.main(cmd)==0
    assert (result/'latest.pt').is_file() and (result/'best.pt').is_file()
    with pytest.raises(RuntimeError, match='USE_--resume'):
        h.main(cmd)
    assert h.main(cmd+['--resume','--steps','3'])==0
    assert h.main(cmd+['--eval-only'])==0


def test_score_reports_all_38_head_metrics():
    model=h.model_factory(torch)
    x=np.zeros((10,h.INPUT_WIDTH),dtype='float32')
    y=np.array([[0,1],[21,-220],[20000,-200001],[3,-5],[-199,100000]]*2)
    metrics=h.score(torch,model,(x,y,['sym_add']*10,['symbolic']*10),'cpu')
    assert len(metrics['heads']) == 38
    assert metrics['heads']['x_zero']['rows'] == 10
    assert metrics['heads']['y_route_above_200']['rows'] >= 1
    assert metrics['heads']['x_expert_0_pos']['rows'] >= 1
    assert metrics['heads']['y_expert_5_neg']['rows'] >= 1


def test_neural_heads_learn_on_deterministic_teacher_features():
    torch.manual_seed(8)
    model=h.model_factory(torch)
    optimizer=torch.optim.AdamW(model.parameters(),lr=0.001)
    rng=np.random.default_rng(8)
    y=[]
    for i in range(64):
        def value():
            regime=int(rng.integers(6))
            low,high=h.band_limits(regime)
            sign=int(rng.choice([-1,1]))
            return int(rng.integers(low,high+1))*sign
        y.append([value(),value()])
    labels, bands, local=h.supervision(torch,y,'cpu')
    x=torch.zeros((64,h.INPUT_WIDTH))
    # Known synthetic signal with six dedicated positional channels per register.
    for i in range(64):
        for p in range(2):
            val=y[i][p]
            x[i,16*p]=torch.sign(torch.tensor(float(val)))
            x[i,16*p+1]=bands[i,p].float()/5
            x[i,16*p+2]=local[i,p]
    with torch.no_grad():
        original=float(h.hierarchy_loss(torch,model(x),labels,bands,local)[0])
    for _ in range(60):
        out=model(x)
        loss,_=h.hierarchy_loss(torch,out,labels,bands,local)
        optimizer.zero_grad(set_to_none=True)
        loss.backward(); optimizer.step()
    with torch.no_grad():
        learned=float(h.hierarchy_loss(torch,model(x),labels,bands,local)[0])
    assert learned < original*0.8, (learned,original)
