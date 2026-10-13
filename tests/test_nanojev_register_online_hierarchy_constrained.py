"""CPU tests for the optional constrained stage-5 numerical head."""
import json
import math
import sys
import types

import numpy as np
import pytest
import torch

import nanojev_register_online_hierarchy_constrained as c


def test_38_supervised_heads_and_coupling_parameter_budget():
    assert c.HEADS_PER_REGISTER == 19
    assert c.CUTS == (20, 200, 2000, 20000, 200000)
    baseline = c.model_factory('bounded')
    assert sum(p.numel() for p in baseline.parameters()) == 311334
    for stage in ('shared','continuity'):
        model = c.model_factory(stage)
        out = model(torch.randn(3,c.INPUT_WIDTH))
        assert out.shape == (3,38)
        assert 0 < sum(p.numel() for p in model.parameters()) < 350_000
        assert model.sharing_penalty().item() == pytest.approx(0.)


def test_log_target_and_inverse_all_intervals_exact():
    for band in range(6):
        low,high = c.h.band_limits(band)
        test = np.array([low,(low+high)//2,high],dtype=np.int64)
        transformed = c.log_encode(test,np.full(3,band))
        np.testing.assert_allclose(c.log_decode(transformed,band),test,atol=.25,rtol=1e-6)
    zeros = np.array([[0,5],[-20,0]])
    _,_,loc = c.supervision(zeros,'cpu')
    assert np.isfinite(loc.numpy()).all()
    assert loc[0,0] == 0 and loc[1,1] == 0


def test_hard_bound_guaranteed_even_extreme_logits():
    for band in range(6):
        low,high = c.h.band_limits(band)
        np.testing.assert_array_equal(c.log_decode([-999, 999],band),[low,high])
        assert c.log_decode([float('inf'), -float('inf')],band)[0] == high


def test_oracle_exact_recovery_with_zero_and_signed_extremes():
    truth = np.array([[0,-1],[20,21],[-200,201],[-2000,-2001],[200000,-200001],
                      [2_000_000,-2_000_000],[1,-2]])
    _,bands,local = c.supervision(truth,'cpu')
    logits = np.full((len(truth),2,c.HEADS_PER_REGISTER), -2., dtype='float32')
    for i in range(len(truth)):
        for pos in range(2):
            n = int(truth[i,pos])
            if n == 0:
                continue
            logits[i,pos,c.h.expert_index(int(bands[i,pos]),1 if n>0 else -1)] = float(local[i,pos])
    np.testing.assert_array_equal(c.decode(logits,truth),truth)


def test_actual_routing_does_not_observe_oracle_bucket():
    truth=np.array([[2001,-201]])
    _,bands,loc=c.supervision(truth,'cpu')
    raw=np.full((1,2,c.HEADS_PER_REGISTER),-10.,dtype='float32')
    for pos in range(2):
        val=int(truth[0,pos]);band=int(bands[0,pos])
        raw[0,pos,0]=-10
        raw[0,pos,1]=10 if val>0 else -10
        for j,cut in enumerate(c.CUTS):
            raw[0,pos,j+2]=10 if abs(val)>cut else -10
        raw[0,pos,c.h.expert_index(band,1 if val>0 else -1)]=float(loc[0,pos])
    np.testing.assert_array_equal(c.decode(raw),truth)
    raw[0,0,2]=-10
    assert c.decode(raw)[0,0] != truth[0,0]
    np.testing.assert_array_equal(c.decode(raw,truth),truth)


def test_active_regressor_mask_preserved_without_regularizer():
    numbers=[[1,-300000],[22,21],[0,-10]]
    label,bands,local=c.supervision(numbers,'cpu')
    raw=torch.zeros((3,38),requires_grad=True)
    L,meta=c.constrained_loss(raw,label,bands,local,stage='bounded',
             range_weight=0,sharing_weight=0,continuity_weight=0)
    L.backward()
    idx=c.h.expert_index(5,-1)
    assert raw.grad[0,19+idx] != 0
    assert raw.grad[1,19+idx] == 0 and raw.grad[2,19+idx] == 0
    assert meta['active_heads'] > 10


def test_coupled_heads_are_shared_when_residual_zero():
    net=c.model_factory('shared')
    with torch.no_grad():
        z=net(torch.randn(5,c.INPUT_WIDTH)).reshape(5,2,19)
        for band in range(6):
            torch.testing.assert_close(z[:,:,c.h.expert_index(band,-1)],
                                       z[:,:,c.h.expert_index(band,1)])
            if band > 0:
                torch.testing.assert_close(z[:,:,c.h.expert_index(band,-1)],
                                           z[:,:,c.h.expert_index(0,-1)])
    assert net.sharing_penalty().item()==pytest.approx(0.)


def test_shared_correction_receives_gradients():
    net=c.model_factory('shared')
    numbers=np.array([[20,21],[-200,-201],[2000,2001],[-20000,20001],[-200000,200001],
                      [300000,-300001]]*2)
    lbl,bands,local=c.supervision(numbers,'cpu')
    logits=net(torch.randn(len(numbers),c.INPUT_WIDTH))
    loss,_=c.constrained_loss(logits,lbl,bands,local,net,stage='shared')
    loss.backward()
    assert net.common_magnitude.weight.grad.abs().sum()>0
    assert net.band_coefficients.grad.abs().sum()>0
    assert net.sign_coefficients.grad.abs().sum()>0


def test_near_boundary_penalty_updates_both_neighbors_only():
    numbers=np.array([[20,1000]])
    labels,_,_=c.supervision(numbers,'cpu')
    raw=torch.zeros((1,38),requires_grad=True)
    continuity,count=c.boundary_continuity(raw,labels,window=.12)
    assert count==1
    continuity.backward()
    lower=c.h.expert_index(0,1)
    upper=c.h.expert_index(1,1)
    assert raw.grad[0,lower].abs()>0
    assert raw.grad[0,upper].abs()>0
    far=c.h.expert_index(4,1)
    assert raw.grad[0,far] == 0
    numbers=np.array([[1000,-1000]])
    labels,_,_=c.supervision(numbers,'cpu')
    raw=torch.zeros((1,38),requires_grad=True)
    loss,count=c.boundary_continuity(raw,labels,window=.01)
    assert count==0 and loss==0
    loss.backward()
    assert raw.grad.abs().sum()==0


def test_correct_near_edge_expert_predictions_reduce_continuity():
    nums=np.array([[20,21]])
    labels,_,_=c.supervision(nums,'cpu')
    logits=torch.zeros((1,2,19))
    naive,_=c.boundary_continuity(logits.reshape(1,38),labels)
    logits[0,0,c.h.expert_index(0,1)]=1
    logits[0,0,c.h.expert_index(1,1)]=-1
    logits[0,1,c.h.expert_index(0,1)]=1
    logits[0,1,c.h.expert_index(1,1)]=-1
    fitted,_=c.boundary_continuity(logits.reshape(1,38),labels)
    assert fitted < naive


def test_all_stages_loss_backward_finite():
    values=np.array([[1,2],[-21,200],[2100,-20001],[0,200001],[-2000000,3]])
    label,bands,local=c.supervision(values,'cpu')
    for stage in c.STAGES:
        net=c.model_factory(stage)
        out=net(torch.randn(len(values),c.INPUT_WIDTH))
        L,aux=c.constrained_loss(out,label,bands,local,net,stage=stage)
        L.backward()
        assert math.isfinite(L.detach().item())
        assert aux['range']>=0 and aux['active_heads']>10
        if stage=='bounded':
            assert aux['sharing']==0 and aux['continuity']==0
        if stage=='shared':
            assert aux['continuity']==0


def test_fresh_stream_excludes_benchmark_and_heldout_ops():
    _,tasks,_,_=c.v3.build_plan()
    stream=c.h.make_stream(100,tasks,'balanced')
    rows=stream.batch(260)
    assert not { (r['a'],r['b']) for r in rows } & c.previous.reserved_pairs(tasks)
    assert not { r['operation'] for r in rows } & set(c.v3.HELD_OUT_OPERATIONS)


def test_score_has_per_head_metrics_and_predicted_vs_oracle():
    n=c.model_factory('continuity')
    x=np.zeros((8,c.INPUT_WIDTH),dtype='float32')
    values=np.array([[0,1],[20,21],[200,201],[-2000,2001]]*2)
    report=c.score(n,(x,values,['sym_add']*8,['symbolic']*8),'cpu')
    assert len(report['heads'])==38
    assert 0<=report['joint_pass_rate']<=1
    assert 0<=report['oracle_routing_joint_pass_rate']<=1
    assert report['heads']['x_expert_0_pos']['rows']>0
    assert math.isfinite(report['head_validation_loss'])


def test_checkpoint_roundtrip_resume_and_drift(tmp_path):
    net=c.model_factory('continuity')
    optimizer=torch.optim.AdamW(net.parameters(),lr=.0003)
    stream=c.h.BalancedFreshPairs(8,{(9,9)})
    stream.batch(10)
    identity={'model':'qwen'}
    config={'architecture':'constrained'}
    path=tmp_path/'latest.pt'
    c.atomic_checkpoint(path,net,optimizer,10,config,stream,{'joint':0.,'loss':.8},identity)
    newer=c.model_factory('continuity')
    opt2=torch.optim.AdamW(newer.parameters(),lr=.0003)
    newstream=c.h.BalancedFreshPairs(8,{(9,9)})
    step,best=c.load_checkpoint(path,newer,opt2,newstream,config,identity)
    assert step==10 and best['loss']==.8
    assert all(torch.equal(p,q) for p,q in zip(net.parameters(),newer.parameters()))
    assert newstream.batch(4)==stream.batch(4)
    with pytest.raises(RuntimeError,match='CONFIG_OR_BACKBONE'):
        c.load_checkpoint(path,newer,opt2,newstream,{'architecture':'wrong'},identity)


def test_end_to_end_fake_qwen_train_resume_eval_and_baseline(tmp_path,monkeypatch):
    root=tmp_path/'benchmark';root.mkdir()
    identity={'model':'qwen','model_info':{'resolved_commit':'abc','source':'synthetic','dtype':'float32'}}
    (root/'model_identity.json').write_text(json.dumps(identity))
    tasks=[{'a':a,'b':b,'split':'val','operation':'sym_add','family':'symbolic'}
           for a,b in [(20,21),(-200,0),(300000,-50),(7,8)]]
    tasks += [{'a':99,'b':98,'split':'test','operation':'sym_add','family':'symbolic'}]
    tasks += [{'a':13,'b':-2,'split':'control','operation':'written_average','family':'written'}]
    monkeypatch.setattr(c.previous,'read_benchmark',lambda root:({'tasks_sha256':'fake'},tasks))
    def load_eval(root,manifest,tasks,split,count=None,seed=19):
        items=[t for t in tasks if t['split']==split]
        feats=np.zeros((len(items),c.INPUT_WIDTH),dtype='float32')
        return feats,np.array([[t['a'],t['b']] for t in items]),[t['operation'] for t in items],[t['family'] for t in items]
    monkeypatch.setattr(c.previous,'load_eval',load_eval)
    monkeypatch.setattr(c.previous,'verify_capture_alignment',lambda *a,**kw:None)
    monkeypatch.setattr(c.previous,'extract_batched',lambda torch_,model,tok,prompts,dev,lim:
                        torch.zeros(len(prompts),c.INPUT_WIDTH,dtype=torch.float32,device=dev))
    fake=types.ModuleType('nanojev_numerate_spine_cassette_smoke')
    fake.load_source=lambda *a:(None,types.SimpleNamespace(eval=lambda:None),None,torch.device('cpu'),
                                {'resolved_commit':'abc','source':'synthetic','dtype':'float32'})
    monkeypatch.setitem(sys.modules,'nanojev_numerate_spine_cassette_smoke',fake)
    baseline=tmp_path/'baseline';baseline.mkdir()
    baseline_model=c.h.model_factory(torch)
    baseline_opt=torch.optim.AdamW(baseline_model.parameters(),lr=.0003)
    baseline_stream=c.h.BalancedFreshPairs(5,{(8,8)})
    baseline_config={'benchmark_sha256':'fake','lr':.0003,'weight_decay':.01}
    (baseline/'run_config.json').write_text(json.dumps(baseline_config))
    c.h.save_checkpoint(torch,baseline/'best.pt',baseline_model,baseline_opt,1,baseline_config,
                        baseline_stream,{'joint':0.,'loss':.8},identity)
    dest=tmp_path/'result'
    args=['--v3-root',str(root),'--results-root',str(dest),'--device','cpu','--dtype','float32',
          '--batch-size','4','--steps','2','--eval-every','1',
          '--checkpoint-every','1','--report-every','1',
          '--baseline-results-root',str(baseline)]
    assert c.main(args)==0
    assert (dest/'best.pt').exists() and (dest/'latest.pt').exists()
    with pytest.raises(RuntimeError,match='USE_--resume'):
        c.main(args)
    assert c.main(args+['--resume','--steps','3'])==0
    assert c.main(args+['--eval-only'])==0
