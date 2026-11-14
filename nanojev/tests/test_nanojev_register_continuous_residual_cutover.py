"""Tests for non-destructive zero-residual continuous cutover v2."""
import hashlib
import json
import math
import sys
import types

import numpy as np
import pytest
import torch

import nanojev_register_continuous_residual_cutover as m


def test_zero_initialized_inference_exact_for_random_inputs():
    torch.manual_seed(4)
    net=m.ResidualNumeracy(residual_width=8,op_width=8)
    net.train()
    for batch in [1,4,15]:
        data=torch.randn(batch,m.h.INPUT_WIDTH)
        pred,base,delta,op_logits=net(data)
        torch.testing.assert_close(delta,torch.zeros_like(delta),atol=0,rtol=0)
        assert torch.equal(m.decode_int(pred),base.to(torch.int64))
        assert not net.source.training
        assert not any(p.requires_grad for p in net.source.parameters())


def test_signed_asinh_corrects_across_boundaries_sign_and_zero():
    # The frozen source may output 0, -9 or 20,000. No discrete gate can
    # prevent a continuous correction from reaching any signed target.
    base=torch.tensor([[0.,-9.],[20000.,200000.]],dtype=torch.float64)
    desired=torch.tensor([[-55.,125.],[135000.,201000.]],dtype=torch.float64)
    delta=(torch.asinh(desired)-torch.asinh(base)).requires_grad_()
    output=torch.sinh(torch.asinh(base)+delta)
    torch.testing.assert_close(output,desired,atol=1e-9,rtol=1e-12)
    output.square().sum().backward()
    assert torch.isfinite(delta.grad).all() and (delta.grad!=0).all()
    assert torch.equal(m.decode_int(output),desired.to(torch.int64))


def test_direct_branch_does_not_use_operation_result_gradients():
    x=torch.tensor([[10.,-20.],[20000.,-13.]],dtype=torch.float64)
    labels=torch.tensor([[15,-22],[135000,-10]])
    d1=torch.zeros_like(x,requires_grad=True)
    d2=torch.zeros_like(x,requires_grad=True)
    p1=torch.sinh(torch.asinh(x)+d1)
    p2=torch.sinh(torch.asinh(x)+d2)
    ops=['sym_add','written_product']
    logits=torch.zeros(2,len(m.op.TRAIN_OPS))
    direct,_=m.residual_loss(p1,x,d1,logits,labels,ops,branch='direct',magnitude_weight=1,
                            anchor_weight=0,result_weight=100,candidate_weight=100)
    combined,terms=m.residual_loss(p2,x,d2,logits,labels,ops,branch='result',magnitude_weight=1,
                           anchor_weight=0,result_weight=1,candidate_weight=1)
    direct.backward();combined.backward()
    assert terms['oracle_result']>0
    assert terms['candidate_result']>0
    assert direct < combined
    assert not torch.allclose(d1.grad,d2.grad)


def test_result_only_gradients_reach_residual_and_not_source():
    torch.manual_seed(55)
    net=m.ResidualNumeracy(residual_width=8,op_width=8)
    feats=torch.randn(4,m.h.INPUT_WIDTH)
    pred,base,delta,ops=net(feats)
    target=torch.tensor([[3847,2916],[-3,4],[7,-1],[0,-1]],dtype=torch.long)
    loss,_=m.residual_loss(pred,base,delta,ops,target,
                     ['sym_multiply','sym_sub','conditional_gt','written_product'],
                     branch='result',magnitude_weight=0,anchor_weight=0,
                     result_weight=1,candidate_weight=1)
    loss.backward()
    assert net.residual[-1].bias.grad.abs().sum()>0
    assert all(p.grad is None for p in net.source.parameters())


def test_schema_branch_paths_and_defaults():
    assert m.SCHEMA not in (m.op.SCHEMA,m.v1.SCHEMA)
    opts=m.parser().parse_args([])
    assert opts.source_checkpoint.name=='latest.pt'
    assert opts.residual_width==128
    assert opts.branch=='direct'
    assert opts.warmup_steps>0
    assert opts.source_checkpoint.parent==m.op.DEFAULT_ROOT


def _make_source(tmp_path,identity,seed=123):
    tasks=[{'a':3,'b':4,'split':'val','operation':'sym_add','family':'symbolic'},
           {'a':7,'b':8,'split':'test','operation':'sym_sub','family':'symbolic'},
           {'a':9,'b':10,'split':'control','operation':'written_average','family':'written'}]
    network=m.op.ExecutableNumeracy(op_width=8)
    optimizer=torch.optim.AdamW(network.parameters())
    stream=m.h.make_stream(seed,tasks,'balanced')
    batch=stream.batch(5)
    replay=m.op.OperationReplay(4,seed+17)
    replay.add(torch.randn(5,m.h.INPUT_WIDTH),batch)
    source=tmp_path/'old-v12'/'latest.pt'
    source.parent.mkdir(parents=True)
    config={'constraint_stage':'continuity','rank':8,'residual_scale':.25,
            'buffer_capacity':4,'op_width':8,'operation_catalog':list(m.op.TRAIN_OPS),
            'operation_programs':m.op.OP_TO_PROGRAM,'sampler':'balanced','seed':seed}
    m.op.save(source,network,optimizer,3000,config,stream,replay,
             {'result':.28,'op_accuracy':1.},identity,{'source_step':43900})
    return source,network,stream,tasks


def test_cutover_preserves_source_checkpoint_and_replay(tmp_path):
    identity={'model':'qwen','model_info':{'resolved_commit':'fake','source':'fake','dtype':'float32'}}
    source,old,oldstream,tasks=_make_source(tmp_path,identity)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    net=m.ResidualNumeracy(op_width=8,residual_width=8)
    stream=m.h.make_stream(123,tasks,'balanced')
    replay=m.op.OperationReplay(4,140)
    args=type('Args',(),dict(constraint_stage='continuity',rank=8,residual_scale=.25,
                            buffer_capacity=4,op_width=8,sampler='balanced',seed=123))()
    provenance=m.cutover(source,net,stream,replay,identity,args)
    assert provenance['sha256']==before
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    for key,tensor in old.state_dict().items():
        torch.testing.assert_close(net.source.state_dict()[key],tensor,atol=0,rtol=0)
    assert stream.seen==oldstream.seen
    assert stream.rng.getstate()!=oldstream.rng.getstate()
    assert replay.records
    assert all(not p.requires_grad for p in net.source.parameters())


def test_step_zero_evaluation_bitwise_matched():
    torch.manual_seed(7)
    net=m.ResidualNumeracy(op_width=8,residual_width=8)
    features=np.random.default_rng(8).standard_normal((24,m.h.INPUT_WIDTH)).astype('float32')
    values=np.array([[10,-20],[135000,30],[-550,-12000],[0,0]]*6,dtype=np.int64)
    operations=['sym_add','written_product','written_difference','sym_sub']*6
    family=['symbolic','written','written','symbolic']*6
    report=m.evaluate(net,(features,values,operations,family),torch.device('cpu'))
    for key,ref in [('result_5pct_pass_rate','source_result_5pct_pass_rate'),
                    ('numeric_result_5pct_pass_rate','source_numeric_result_5pct_pass_rate'),
                    ('joint_xy_5pct_pass_rate','source_joint_xy_5pct_pass_rate'),
                    ('boundary_snap_count','source_boundary_snap_count'),
                    ('global_log_mae','source_global_log_mae')]:
        assert report[key]==report[ref]


def test_mocked_branches_train_resume_and_offline_evaluation(tmp_path,monkeypatch):
    identity={'model':'qwen','model_info':{'resolved_commit':'fake','source':'fake','dtype':'float32'}}
    source,network,stream,tasks=_make_source(tmp_path,identity)
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    benchmark_root=tmp_path/'benchmark'
    benchmark_root.mkdir()
    (benchmark_root/'model_identity.json').write_text(json.dumps(identity))
    manifest={'tasks_sha256':'fake'}
    monkeypatch.setattr(m.previous,'read_benchmark',lambda path:(manifest,tasks))
    test_data=(np.random.default_rng(10).normal(size=(4,m.h.INPUT_WIDTH)).astype('float32'),
               np.array([[3,4],[7,8],[9,10],[4,-2]],dtype=np.int64),
               ['sym_add','sym_sub','written_average','sym_multiply'],
               ['symbolic','symbolic','written','symbolic'])
    monkeypatch.setattr(m.previous,'load_eval',lambda *args,**kw:test_data)
    monkeypatch.setattr(m.previous,'verify_capture_alignment',lambda *args,**kw:None)
    monkeypatch.setattr(m.previous,'extract_batched',lambda torch_arg,frozen,tokenizer,prompts,device,max_tokens:
                        torch.randn(len(prompts),m.h.INPUT_WIDTH))
    class FakeFrozen:
        def eval(self):return self
    cassette=types.ModuleType('nanojev_numerate_spine_cassette_smoke')
    cassette.load_source=lambda *args:(None,FakeFrozen(),None,torch.device('cpu'),identity['model_info'])
    monkeypatch.setitem(sys.modules,'nanojev_numerate_spine_cassette_smoke',cassette)
    root=tmp_path/'residual-results'
    common=['--v3-root',str(benchmark_root),'--results-root',str(root),
            '--source-checkpoint',str(source),'--device','cpu','--dtype','float32',
            '--seed','123','--buffer-capacity','4','--per-expert-batch','1',
            '--batch-size','2','--replay-updates','1','--op-width','8',
            '--residual-width','8','--warmup-steps','2','--eval-every','1',
            '--checkpoint-every','1','--report-every','1','--threads','2']
    for branch in ('direct','result'):
        assert m.main(common+['--branch',branch,'--steps','2'])==0
        ck=torch.load(root/branch/'latest.pt',weights_only=True)
        assert ck['step']==2
        assert ck['source_cutover']['sha256']==sha
        assert ck['source_cutover']['source_step']==3000
        assert (root/branch/'best.pt').exists()
        assert (root/branch/'best_joint.pt').exists()
        assert m.main(common+['--branch',branch,'--steps','3','--resume'])==0
        assert torch.load(root/branch/'latest.pt',weights_only=True)['step']==3
        assert m.main(common+['--branch',branch,'--steps','3','--resume','--eval-only'])==0
    first=torch.load(root/'direct'/'latest.pt',weights_only=True)
    second=torch.load(root/'result'/'latest.pt',weights_only=True)
    assert first['random_stream']['seen']==second['random_stream']['seen']
    assert first['source_cutover']['fork_seed']==second['source_cutover']['fork_seed']
    assert first['config']['branch']=='direct' and second['config']['branch']=='result'
    assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
    assert all(torch.equal(first['network']['source.'+key],second['network']['source.'+key])
               for key in network.state_dict())
    assert not all(torch.equal(first['network']['residual.'+key],second['network']['residual.'+key])
                   for key in m.ResidualNumeracy(op_width=8,residual_width=8).residual.state_dict())
    with pytest.raises(RuntimeError,match='CONFIG_DRIFT'):
        m.main(common+['--branch','direct','--steps','3','--resume','--lr','0.9'])


def test_refuses_source_catalog_drift(tmp_path):
    identity={'model':'qwen','model_info':{'resolved_commit':'fake','source':'fake','dtype':'float32'}}
    src,_,stream,tasks=_make_source(tmp_path,identity)
    ck=torch.load(src,weights_only=True)
    ck['config']['operation_catalog']=list(reversed(ck['config']['operation_catalog']))
    torch.save(ck,src)
    network=m.ResidualNumeracy(op_width=8,residual_width=8)
    replay=m.op.OperationReplay(4,140)
    args=type('Args',(),dict(constraint_stage='continuity',rank=8,residual_scale=.25,
                   buffer_capacity=4,op_width=8,sampler='balanced',seed=123))()
    with pytest.raises(RuntimeError,match='SOURCE_ARCHITECTURE_OR_CATALOG_DRIFT'):
        m.cutover(src,network,stream,replay,identity,args)
