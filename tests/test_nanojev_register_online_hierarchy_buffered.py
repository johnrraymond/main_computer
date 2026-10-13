"""Offline regression for stratified online 38-head constrained trainer."""
import json
import math
import sys
import types

import numpy as np
import pytest
import torch

import nanojev_register_online_hierarchy_buffered as b


def samples(values):
    return [dict(a=int(x),b=int(y),operation='sym_add',family='symbolic',prompt=f'{x} {y}')
            for x,y in values]


def add_values(bank, values):
    input=torch.arange(len(values)*b.h.INPUT_WIDTH,dtype=torch.float32).reshape(len(values),b.h.INPUT_WIDTH)/100
    bank.add(input,samples(values))


def test_bucket_boundary_assignment():
    assert b.classify_bucket(0)==0
    assert b.classify_bucket(-1)==1
    assert b.classify_bucket(1)==2
    assert b.classify_bucket(-20)==1
    assert b.classify_bucket(20)==2
    assert b.classify_bucket(-21)==3
    assert b.classify_bucket(21)==4
    assert b.classify_bucket(-2_000_000)==11
    assert b.classify_bucket(2_000_000)==12


def test_gpu_free_supervision_matches_previous_version():
    nums=np.array([[0,1],[-20,21],[200,201],[-200000,200001],[2000000,-2000000]])
    a=b.supervision_fast(nums,'cpu')
    z=b.c.supervision(nums,'cpu')
    for i in range(3):torch.testing.assert_close(a[i],z[i])


def test_masked_vectorized_loss_and_gradients_match_reference():
    torch.manual_seed(42)
    nums=np.array([[0,1],[-20,21],[200,-201],[-2000,2001],
                   [-20000,20001],[-200000,200001],[-2000000,2000000]])
    lab,bands,loc=b.supervision_fast(nums,'cpu')
    raw=torch.randn(len(nums),38,requires_grad=True)
    ref,_=b.h.hierarchy_loss(torch,raw,lab,bands,loc)
    oldgrad=torch.autograd.grad(ref,raw,retain_graph=True)[0]
    new,stats=b.vectorized_hierarchy_loss(raw,lab,bands,loc)
    grad=torch.autograd.grad(new,raw)[0]
    torch.testing.assert_close(ref,new,rtol=1e-6,atol=1e-6)
    torch.testing.assert_close(oldgrad,grad,rtol=1e-5,atol=1e-6)
    assert stats['active_experts']==12
    assert torch.isfinite(new)


def test_vectorized_loss_masked_expert_receives_no_gradient():
    labels,bands,loc=b.supervision_fast([[0,21],[1,-1000]],'cpu')
    raw=torch.zeros((2,38),requires_grad=True)
    loss,_=b.vectorized_hierarchy_loss(raw,labels,bands,loc)
    loss.backward()
    assert raw.grad[0,0+b.h.expert_index(0,-1)]==0
    assert raw.grad[0,19+b.h.expert_index(1,1)]!=0
    assert raw.grad[1,19+b.h.expert_index(1,1)]==0


def test_vectorized_continuity_matches_older_implementation():
    nums=np.array([[20,21],[-200,201],[-2_000,2_001],[2,-2]])
    labels,_,_=b.supervision_fast(nums,'cpu')
    torch.manual_seed(44)
    raw=torch.randn(len(nums),38,requires_grad=True)
    ref,count=b.c.boundary_continuity(raw,labels,window=.12)
    refgrad=torch.autograd.grad(ref,raw,retain_graph=True)[0]
    new,newcount=b.vectorized_boundary_loss(raw,labels,window=.12)
    newgrad=torch.autograd.grad(new,raw)[0]
    torch.testing.assert_close(ref,new,rtol=1e-5,atol=1e-5)
    torch.testing.assert_close(refgrad,newgrad,rtol=1e-5,atol=1e-5)
    assert count==int(newcount)


def test_replay_sampling_balances_sparse_buckets_without_faking_data():
    bank=b.BucketReplay(capacity=3,seed=8)
    vals=[(0,-1),(20,-21),(200,-201),(2000,-2001)]
    add_values(bank,vals)
    x,y=bank.sample(5)
    assert x.shape[0]==len([j for p in bank.counts() for j in p if j])*5
    assert y.shape==(len(x),2)
    assert {tuple(pair) for pair in y.tolist()}==set(vals)
    assert len(bank.records)==len(vals)
    assert bank.counts()[0][0]>0
    assert bank.counts()[1][1]>0


def test_eviction_refs_and_replay_checkpoint_state():
    bank=b.BucketReplay(capacity=2,seed=4)
    vals=[(20,-21),(20,-22),(20,-23),(20,-24)]
    add_values(bank,vals)
    assert bank.counts()[0][2]==2
    assert bank.counts()[1][3]==2
    assert len(bank.records)==2
    snapshot=bank.state_dict()
    other=b.BucketReplay(capacity=2,seed=999)
    other.load_state_dict(snapshot)
    for _ in range(3):
        x1,y1=bank.sample(3);x2,y2=other.sample(3)
        torch.testing.assert_close(x1,x2)
        np.testing.assert_array_equal(y1,y2)
    with pytest.raises(RuntimeError,match='CAPACITY_DRIFT'):
        b.BucketReplay(3,4).load_state_dict(snapshot)


def test_replay_feature_rows_are_independent_cpu_storage():
    bank=b.BucketReplay(capacity=10,seed=4)
    feat=torch.zeros((2,b.h.INPUT_WIDTH),dtype=torch.float32)
    bank.add(feat,samples([(5,6),(7,8)]))
    feat[0]=999
    assert next(iter(bank.records.values()))[0].max()==0
    a=list(bank.records.values())
    assert a[0][0].untyped_storage().data_ptr()!=a[1][0].untyped_storage().data_ptr()


def test_regularizers_all_stages_are_finite_and_differentiable():
    nums=np.array([[0,-1],[20,21],[-200,201],[2000,2001]])
    labels,bands,local=b.supervision_fast(nums,'cpu')
    for stage in ('bounded','shared','continuity'):
        network=b.c.model_factory(stage)
        logits=network(torch.randn(4,b.h.INPUT_WIDTH))
        loss,stats=b.fast_constrained_loss(logits,labels,bands,local,network,
                                          stage=stage,continuity_weight=.1)
        loss.backward()
        assert math.isfinite(float(loss.detach()))
        assert stats['active_experts']>0
        assert stats['route']>=0


def test_full_checkpoint_resume_preserves_buffer_and_optimizer(tmp_path):
    net=b.c.model_factory('continuity')
    optimizer=torch.optim.AdamW(net.parameters(),lr=.0003)
    stream=b.h.BalancedFreshPairs(1,{(12,34)})
    stream.batch(3)
    replay=b.BucketReplay(4,seed=22)
    add_values(replay,[(0,1),(21,-20),(200000,-300000)])
    path=tmp_path/'checkpoint.pt'
    b.save_checkpoint(path,net,optimizer,3,{'k':1},stream,replay,
                      {'joint':0,'loss':1},{'model':'qwen'})
    new=b.c.model_factory('continuity')
    opt2=torch.optim.AdamW(new.parameters(),lr=.0003)
    stream2=b.h.BalancedFreshPairs(1,{(12,34)})
    replay2=b.BucketReplay(4,seed=88)
    step,best=b.load_checkpoint(path,new,opt2,stream2,replay2,{'k':1},{'model':'qwen'})
    assert step==3 and best['loss']==1
    for p,q in zip(net.parameters(),new.parameters()):torch.testing.assert_close(p,q)
    x1,y1=replay.sample(5)
    x2,y2=replay2.sample(5)
    torch.testing.assert_close(x1,x2)
    np.testing.assert_array_equal(y1,y2)
    assert stream.batch(2)==stream2.batch(2)
    with pytest.raises(RuntimeError,match='CONFIG_OR_BACKBONE_DRIFT'):
        b.load_checkpoint(path,new,opt2,stream2,replay2,{'k':2},{'model':'qwen'})


def test_import_constrained_old_weights_only(tmp_path):
    model=b.c.model_factory('continuity')
    optimizer=torch.optim.AdamW(model.parameters(),lr=.0003)
    stream=b.h.BalancedFreshPairs(4,{(1,2)})
    params=b.parser().parse_args([])
    oldconf={'constraint_stage':params.constraint_stage,
             'rank':params.rank,'residual_scale':params.residual_scale}
    path=tmp_path/'old.pt'
    b.c.atomic_checkpoint(path,model,optimizer,400,oldconf,stream,
                          {'joint':0.,'loss':.3},{'model':'qwen'})
    fresh=b.c.model_factory('continuity')
    old=b.import_older_checkpoint(path,fresh,params,{'model':'qwen'})
    assert old==400
    for p,q in zip(model.parameters(),fresh.parameters()):torch.testing.assert_close(p,q)
    with pytest.raises(RuntimeError,match='INCOMPATIBLE_BASELINE'):
        b.import_older_checkpoint(path,fresh,params,{'model':'other'})


def test_end_to_end_fake_backbone_resume_and_evaluation(tmp_path,monkeypatch):
    benchmark=tmp_path/'benchmark';benchmark.mkdir()
    identity={'model':'qwen','model_info':{'resolved_commit':'abc','source':'mock','dtype':'float32'}}
    (benchmark/'model_identity.json').write_text(json.dumps(identity))
    cases={'val':[(20,21),(-200,0),(300000,-50),(7,8)],
           'test':[(99,98)],'control':[(13,-2)]}
    tasks=[dict(a=a,b=z,split=split,operation='sym_add',family='symbolic')
           for split,arr in cases.items() for a,z in arr]
    monkeypatch.setattr(b.previous,'read_benchmark',lambda root:({'tasks_sha256':'mock'},tasks))
    def load_eval(root,manifest,tasks,split,count=None,seed=19):
        vals=cases[split]
        return (np.zeros((len(vals),b.h.INPUT_WIDTH),dtype=np.float32),
                np.asarray(vals,dtype=np.int64),['sym_add']*len(vals),['symbolic']*len(vals))
    monkeypatch.setattr(b.previous,'load_eval',load_eval)
    monkeypatch.setattr(b.previous,'verify_capture_alignment',lambda *args,**kw:None)
    monkeypatch.setattr(b.previous,'extract_batched',lambda torch_,mod,tok,prompts,dev,lim:
                        torch.randn(len(prompts),b.h.INPUT_WIDTH,device=dev).detach())
    cassette=types.ModuleType('nanojev_numerate_spine_cassette_smoke')
    cassette.load_source=lambda *args:(None,types.SimpleNamespace(eval=lambda:None),None,
                                     torch.device('cpu'),dict(identity['model_info']))
    monkeypatch.setitem(sys.modules,'nanojev_numerate_spine_cassette_smoke',cassette)
    root=tmp_path/'result'
    cmd=['--v3-root',str(benchmark),'--results-root',str(root),'--device','cpu',
         '--dtype','float32','--batch-size','4','--steps','2',
         '--buffer-capacity','3','--per-expert-batch','2',
         '--eval-every','1','--report-every','1','--checkpoint-every','1',
         '--replay-probe','12']
    assert b.main(cmd)==0
    assert (root/'latest.pt').is_file() and (root/'best.pt').is_file()
    with pytest.raises(RuntimeError,match='USE_--resume'):
        b.main(cmd)
    assert b.main(cmd+['--resume','--steps','3'])==0
    assert b.main(cmd+['--eval-only'])==0

