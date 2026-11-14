"""Regressions for the additive continuous-magnitude cutover."""
import hashlib
import json
import math
import sys
import types

import numpy as np
import pytest
import torch

import nanojev_register_continuous_magnitude_cutover as m


def test_new_schema_and_separate_results():
    assert m.SCHEMA != m.op.SCHEMA
    assert m.DEFAULT_ROOT != m.op.DEFAULT_ROOT
    args=m.parser().parse_args([])
    assert args.source_checkpoint.name == 'latest.pt'
    assert args.bootstrap_updates > 0
    assert args.warmup_steps > 0


def test_continuous_output_can_cross_every_old_bucket_boundary():
    for cut in m.h.CUTS:
        # Choose raw log magnitude producing values on both sides of cut.
        target=torch.tensor([[float(cut)*0.99,float(cut)*1.01]],dtype=torch.float64)
        raw=torch.log(torch.expm1(torch.log1p(target))).requires_grad_()
        signs=torch.tensor([[[-30.,30.],[-30.,30.]]],dtype=torch.float64)
        result=m.decode_continuous(raw,signs,differentiable=True)
        assert result[0,0] < cut < result[0,1]
        result.sum().backward()
        assert torch.isfinite(raw.grad).all()
        assert (raw.grad.abs()>0).all()


def test_global_log_target_and_magnitude_executed_result_gradients():
    torch.manual_seed(8)
    network=m.ContinuousNumeracy(op_width=8,width=8)
    labels=torch.tensor([[3847,2916],[-125_000,25],[21_000,-200],[-9,2]])
    magnitude,sign,ops,_=network(torch.randn(4,m.h.INPUT_WIDTH))
    loss,component=m.continuous_loss(magnitude,sign,ops,labels,
      ['sym_multiply','written_sub','sym_add','conditional_min'],
      magnitude_weight=0,sign_weight=0,result_weight=1,candidate_weight=1)
    loss.backward()
    assert torch.isfinite(loss)
    assert network.magnitude[-1].bias.grad.abs().sum()>0
    assert network.sign_zero[1].weight.grad.abs().sum()>0
    assert all(parameter.grad is None for parameter in network.source.parameters())
    assert component['oracle_result']>=0


def test_hard_decode_uses_sign_and_zero_without_bucket_routing():
    raw=torch.tensor([[12.,10.]])
    signs=torch.tensor([[[-20.,20.],[-20.,-20.]]])
    r=m.decode_continuous(raw,signs,differentiable=False)
    assert int(r[0,0])>100000
    assert int(r[0,1])<0
    signs[0,0,0]=20.
    r=m.decode_continuous(raw,signs,differentiable=False)
    assert r[0,0]==0


def test_zero_sign_weights_copied_from_source_and_frozen():
    net=m.ContinuousNumeracy(width=8,op_width=8)
    with torch.no_grad():
        layer=net.source.registers.classifiers
        layer.weight.copy_(torch.arange(layer.weight.numel()).reshape_as(layer.weight))
        layer.bias.copy_(torch.arange(14))
    net.initialize_sign_zero()
    for j,index in enumerate([0,1,7,8]):
        torch.testing.assert_close(net.sign_zero[1].weight[j],layer.weight[index])
        torch.testing.assert_close(net.sign_zero[1].bias[j],layer.bias[index])
    net.train()
    assert not net.source.training
    assert all(not p.requires_grad for p in net.source.parameters())


def _make_source(tmp_path, identity, seed=123):
    tasks=[{'a':3,'b':4,'split':'val','operation':'sym_add','family':'symbolic'},
           {'a':7,'b':8,'split':'test','operation':'sym_sub','family':'symbolic'},
           {'a':9,'b':10,'split':'control','operation':'written_average','family':'written'}]
    network=m.op.ExecutableNumeracy(op_width=8)
    optim=torch.optim.AdamW(network.parameters())
    stream=m.h.make_stream(seed,tasks,'balanced')
    batch=stream.batch(5)
    replay=m.op.OperationReplay(4,seed+17)
    replay.add(torch.randn(5,m.h.INPUT_WIDTH),batch)
    source_path=tmp_path/'old-v1-2'/'latest.pt'
    source_path.parent.mkdir(parents=True)
    config={'constraint_stage':'continuity','rank':8,'residual_scale':.25,
            'buffer_capacity':4,'op_width':8,'operation_catalog':list(m.op.TRAIN_OPS),
            'operation_programs':m.op.OP_TO_PROGRAM,'sampler':'balanced','seed':seed}
    m.op.save(source_path,network,optim,3000,config,stream,replay,
              {'result':.28,'op_accuracy':1.},identity,{'source_step':43900})
    return source_path,network,stream,tasks


def test_cutover_immutable_source_imports_v12_weights_and_replay(tmp_path):
    identity={'model':'qwen','model_info':{'resolved_commit':'test','source':'fake','dtype':'float32'}}
    source,old,oldstream,tasks=_make_source(tmp_path,identity)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    network=m.ContinuousNumeracy(op_width=8,width=8)
    stream=m.h.make_stream(123,tasks,'balanced')
    replay=m.op.OperationReplay(4,140)
    args=type('Args',(),dict(constraint_stage='continuity',rank=8,residual_scale=.25,
                buffer_capacity=4,op_width=8,sampler='balanced',seed=123))()
    proof=m.cutover(source,network,stream,replay,identity,args)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    assert proof['sha256']==digest
    assert proof['source_step']==3000
    assert proof['source_seen_pairs']==5
    assert stream.seen==oldstream.seen
    assert stream.rng.getstate()!=oldstream.rng.getstate()
    assert len(replay.records)>0
    for name,parameter in old.state_dict().items():
        torch.testing.assert_close(network.source.state_dict()[name],parameter)
    assert all(not p.requires_grad for p in network.source.parameters())
    replay.sample_labeled(1)


def test_rejects_unrelated_or_drifted_checkpoint(tmp_path):
    identity={'model':'qwen','model_info':{'resolved_commit':'test','source':'fake','dtype':'float32'}}
    src,old,stream,tasks=_make_source(tmp_path,identity)
    ck=torch.load(src,weights_only=True)
    ck['config']['op_width']=111
    torch.save(ck,src)
    new=m.ContinuousNumeracy(op_width=8,width=8)
    replay=m.op.OperationReplay(4,140)
    args=type('Args',(),dict(constraint_stage='continuity',rank=8,residual_scale=.25,
                buffer_capacity=4,op_width=8,sampler='balanced',seed=123))()
    with pytest.raises(RuntimeError,match='ARCHITECTURE_OR_CATALOG_DRIFT'):
        m.cutover(src,new,stream,replay,identity,args)


def test_completed_mock_train_resume_and_offline_eval(tmp_path,monkeypatch):
    identity={'model':'qwen','model_info':{'resolved_commit':'fake','source':'fake','dtype':'float32'}}
    source,old,stream,tasks=_make_source(tmp_path,identity)
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    benchmark_root=tmp_path/'benchmark';benchmark_root.mkdir()
    (benchmark_root/'model_identity.json').write_text(json.dumps(identity))
    manifest={'tasks_sha256':'fake'}
    monkeypatch.setattr(m.previous,'read_benchmark',lambda path:(manifest,tasks))
    eval_rows=(np.random.default_rng(10).normal(size=(3,m.h.INPUT_WIDTH)).astype(np.float32),
               np.array([[3,4],[7,8],[9,10]],dtype=np.int64),
               ['sym_add','sym_sub','written_average'],['symbolic','symbolic','written'])
    monkeypatch.setattr(m.previous,'load_eval',lambda *args,**kw:eval_rows)
    monkeypatch.setattr(m.previous,'verify_capture_alignment',lambda *args,**kw:None)
    monkeypatch.setattr(m.previous,'extract_batched',
                        lambda torch_arg,frozen,tokenizer,prompts,device,max_tokens:
                        torch.randn(len(prompts),m.h.INPUT_WIDTH))
    class Frozen:
        def eval(self):return self
    cassette=types.ModuleType('nanojev_numerate_spine_cassette_smoke')
    cassette.load_source=lambda *args:(None,Frozen(),None,torch.device('cpu'),identity['model_info'])
    monkeypatch.setitem(sys.modules,'nanojev_numerate_spine_cassette_smoke',cassette)
    results=tmp_path/'continuous-results'
    args=['--v3-root',str(benchmark_root),'--results-root',str(results),
          '--source-checkpoint',str(source),'--device','cpu','--dtype','float32',
          '--seed','123','--buffer-capacity','4','--per-expert-batch','1',
          '--batch-size','2','--replay-updates','1','--op-width','8',
          '--continuous-width','8','--bootstrap-updates','2','--warmup-steps','2',
          '--eval-every','1','--checkpoint-every','1','--report-every','1']
    assert m.main(args+['--steps','2'])==0
    checkpoint=torch.load(results/'latest.pt',weights_only=True)
    assert checkpoint['step']==2
    assert checkpoint['source_cutover']['source_step']==3000
    assert len(checkpoint['random_stream']['seen'])==9
    assert (results/'best.pt').exists()
    assert (results/'best_joint.pt').exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
    assert m.main(args+['--steps','3','--resume'])==0
    assert torch.load(results/'latest.pt',weights_only=True)['step']==3
    assert m.main(args+['--steps','3','--resume','--eval-only'])==0
    assert hashlib.sha256(source.read_bytes()).hexdigest()==sha


def test_resume_config_or_identity_drift(tmp_path):
    net=m.ContinuousNumeracy(op_width=8,width=8)
    opt=torch.optim.AdamW([p for p in net.parameters() if p.requires_grad])
    stream=m.h.make_stream(123,[{'a':1,'b':2}],'balanced')
    replay=m.op.OperationReplay(4,140)
    replay.add(torch.zeros(1,m.h.INPUT_WIDTH),
               [{'a':1,'b':2,'operation':'sym_add','family':'symbolic'}])
    identity={'model':'qwen'}
    path=tmp_path/'c.pt';cfg={'schema':m.SCHEMA,'width':8}
    m.save(path,net,opt,12,cfg,stream,replay,{'result':.2,'joint':.05},identity,{'sha256':'hash'})
    with pytest.raises(RuntimeError,match='RESUME_CONFIG_OR_IDENTITY_DRIFT'):
        m.restore(path,net,opt,stream,replay,{'schema':'wrong'},identity)
    step,best,source=m.restore(path,net,opt,stream,replay,cfg,identity)
    assert step==12 and best['joint']==.05
