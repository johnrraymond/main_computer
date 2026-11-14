"""CPU regressions for route-aware, source-preserving NanoJev cutover v3."""
import hashlib
import json
import sys
import types
import numpy as np
import pytest
import torch

import nanojev_register_magnitude_route_cutover as m


def test_step_zero_reproduces_source_and_source_is_frozen():
    torch.manual_seed(2)
    net=m.RouteAwareNumeracy(op_width=8,route_width=8)
    for batch in (1,4,13):
        features=torch.randn(batch,m.h.INPUT_WIDTH)
        net.train()
        p,base,delta,logits,aux=net.forward_with_route(features)
        torch.testing.assert_close(delta,torch.zeros_like(delta),atol=0,rtol=0)
        assert torch.equal(m.decode_int(p),base.long())
        assert torch.equal(m.RouteAwareNumeracy.route_classes(base),aux['base_route_classes'])
        torch.testing.assert_close(aux['route_probabilities'].sum(-1),torch.ones_like(base),rtol=1e-12,atol=1e-12)
        assert net.source.training is False
        assert all(not v.requires_grad for v in net.source.parameters())


def test_route_prior_is_normalized_and_symmetric_for_zero_logits():
    raw=torch.zeros(4,2*m.h.HEADS_PER_REGISTER)
    p=m.RouteAwareNumeracy.prior_route_logits(raw).exp()
    torch.testing.assert_close(p.sum(-1),torch.ones_like(p[:,:,0]),atol=1e-6,rtol=0)
    torch.testing.assert_close(p[:,:,0],torch.full((4,2),.5),atol=1e-6,rtol=0)
    assert p.shape==(4,2,13)
    # sign symmetry for every paired class
    torch.testing.assert_close(p[:,:,1::2],p[:,:,2::2],atol=1e-6,rtol=0)


def test_route_class_boundaries_and_zero_and_sign():
    num=torch.tensor([[0,-1],[1,20],[21,-20],[200,201],[20000,20001],[-200000,-200001]])
    classes=m.RouteAwareNumeracy.route_classes(num)
    assert classes.tolist()==[[0,1],[2,2],[4,1],[4,6],[8,10],[9,11]]


def test_all_experts_decoded_against_source_regime_geometry():
    raw=torch.zeros(1,2*m.h.HEADS_PER_REGISTER)
    # neutral expert coordinate .0 => midpoint of each regime
    values=m.RouteAwareNumeracy.expert_candidates(raw)[0,0].numpy()
    assert values[0]==0
    assert values[1]<0 and values[2]>0
    for band in range(6):
        expected=m.h.expert_decode(np.array([0.]),band)[0]
        assert np.isclose(np.sinh(values[1+2*band]),-expected,rtol=1e-10,atol=1e-10)
        assert np.isclose(np.sinh(values[2+2*band]),expected,rtol=1e-10,atol=1e-10)


def test_route_selects_large_jump_not_just_boundary_nudge():
    net=m.RouteAwareNumeracy(op_width=8,route_width=8)
    raw=torch.zeros(1,2*m.h.HEADS_PER_REGISTER)
    z=raw.view(1,2,m.h.HEADS_PER_REGISTER)
    z[:,:,0]=-20
    z[:,:,1]=20
    z[:,:,2:5]=20  # survive 20, 200, 2000
    z[:,:,5]=-20    # stop at 20000 => band 3
    z[:,:,6]=-20
    z[:,:,7:]=-1
    z[:,:,7+2*3+1]=1 # source hard decoded +20000
    # Alternate positive band 4 candidate must equal ~135000
    target_coord=m.h.expert_encode(np.asarray([135000.]),np.asarray([4]))[0]
    z[:,:,7+2*4+1]=float(target_coord)
    # Force source output for test; route logits/experts still drawn from it.
    def fake_source(features):
        return raw.expand(len(features),-1),torch.zeros(len(features),len(m.op.TRAIN_OPS))
    net.source.forward=fake_source
    with torch.no_grad():
        # 1+2*4+1 => positive bucket 4, versus source positive bucket 3.
        net.route.bias[10]=100
        net.gate.bias[:]=5
    features=torch.zeros(1,m.h.INPUT_WIDTH)
    p,base,delta,_,aux=net.forward_with_route(features)
    assert (base==20000).all()
    assert p[0,0]>100000
    assert p[0,1]>0
    assert int(aux['route_logits'][0,0].argmax())==10
    assert torch.isfinite(delta).all()


def test_explicit_route_supervision_updates_route_and_jump_gates():
    torch.manual_seed(3)
    net=m.RouteAwareNumeracy(op_width=8,route_width=8)
    features=torch.randn(4,m.h.INPUT_WIDTH)
    p,base,delta,op_logits,aux=net.forward_with_route(features)
    # Force some examples to require cross-bucket / sign / zero correction.
    truth=torch.tensor([[135000,30],[-100000,-20000],[0,33],[-7,900000]],dtype=torch.long)
    loss,metrics=m.route_loss(p,base,delta,op_logits,aux,truth,
          ['written_product','written_sub','conditional_min','sym_reverse_sub'],
          branch='direct',result_weight=0,candidate_weight=0,
          route_weight=1,wrong_route_weight=5,jump_weight=1,gate_weight=.2)
    loss.backward()
    assert torch.isfinite(loss)
    assert net.route.bias.grad.abs().sum()>0
    assert net.gate.bias.grad.abs().sum()>0
    assert net.fine.bias.grad.abs().sum()>0
    assert all(param.grad is None for param in net.source.parameters())
    assert metrics['route_ce']>0 and metrics['hard_jump']>=0


def test_no_result_gradient_for_direct_and_present_for_result():
    net=m.RouteAwareNumeracy(op_width=8,route_width=8)
    x=torch.randn(4,m.h.INPUT_WIDTH)
    pred,base,delta,logits,aux=net.forward_with_route(x)
    labels=torch.tensor([[135000,30],[0,40],[-2000,-3],[2500,2500]],dtype=torch.long)
    kw=dict(route_weight=0,gate_weight=0,jump_weight=0,anchor_weight=0,magnitude_weight=0,
            result_weight=1,candidate_weight=1)
    a,parts_a=m.route_loss(pred,base,delta,logits,aux,labels,
              ['written_product','written_sub','conditional_min','sym_add'],branch='direct',**kw)
    b,parts_b=m.route_loss(pred,base,delta,logits,aux,labels,
              ['written_product','written_sub','conditional_min','sym_add'],branch='result',**kw)
    assert a==0
    assert b>0
    b.backward()
    assert net.fine.bias.grad.abs().sum()>0


def test_source_identical_at_step_zero_on_same_validation_rows():
    net=m.RouteAwareNumeracy(op_width=8,route_width=8)
    rng=np.random.default_rng(29)
    features=rng.normal(size=(16,m.h.INPUT_WIDTH)).astype('float32')
    numeric=np.asarray([[135000,30],[-550,-12000],[200000,2],[0,-8]]*4)
    ops=['written_product','written_difference','sym_sub','sym_add']*4
    families=['written','written','symbolic','symbolic']*4
    report=m.evaluate(net,(features,numeric,ops,families),torch.device('cpu'))
    for name,reference in [('result_5pct_pass_rate','source_result_5pct_pass_rate'),
           ('joint_xy_5pct_pass_rate','source_joint_xy_5pct_pass_rate'),
           ('numeric_result_5pct_pass_rate','source_numeric_result_5pct_pass_rate'),
           ('global_log_mae','source_global_log_mae'),('boundary_snap_count','source_boundary_snap_count')]:
        assert report[name]==report[reference]
    assert report['source_wrong_route_operands']>=0
    assert report['severe_source_error_operands']>=0
    assert 'corrected_route_top1_accuracy' in report
    assert 'mean_gate_source_wrong' in report


def _make_source(tmp_path,identity,seed=123):
    tasks=[{'a':3,'b':4,'split':'val','operation':'sym_add','family':'symbolic'},
           {'a':7,'b':8,'split':'test','operation':'sym_sub','family':'symbolic'},
           {'a':9,'b':10,'split':'control','operation':'written_average','family':'written'}]
    network=m.op.ExecutableNumeracy(op_width=8)
    optimizer=torch.optim.AdamW(network.parameters())
    stream=m.h.make_stream(seed,tasks,'balanced')
    rows=stream.batch(5)
    replay=m.op.OperationReplay(4,seed+17)
    replay.add(torch.randn(5,m.h.INPUT_WIDTH),rows)
    source=tmp_path/'source'/'latest.pt';source.parent.mkdir(parents=True)
    config={'constraint_stage':'continuity','rank':8,'residual_scale':.25,
            'buffer_capacity':4,'op_width':8,'operation_catalog':list(m.op.TRAIN_OPS),
            'operation_programs':m.op.OP_TO_PROGRAM,'sampler':'balanced','seed':seed}
    m.op.save(source,network,optimizer,3000,config,stream,replay,
             {'result':.28,'op_accuracy':1.},identity,{'source_step':43900})
    return source,network,stream,tasks


def test_mocked_train_resume_evaluate_both_branches_source_unchanged(tmp_path,monkeypatch):
    identity={'model':'qwen','model_info':{'resolved_commit':'fake','source':'fake','dtype':'float32'}}
    source,old,_,tasks=_make_source(tmp_path,identity)
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    bench=tmp_path/'benchmark';bench.mkdir()
    (bench/'model_identity.json').write_text(json.dumps(identity))
    monkeypatch.setattr(m.previous,'read_benchmark',lambda *args:({'tasks_sha256':'fake'},tasks))
    data=(np.random.default_rng(10).normal(size=(4,m.h.INPUT_WIDTH)).astype('float32'),
          np.array([[3,4],[7,8],[9,10],[4,-2]],dtype=np.int64),
          ['sym_add','sym_sub','written_average','sym_multiply'],
          ['symbolic','symbolic','written','symbolic'])
    monkeypatch.setattr(m.previous,'load_eval',lambda *args,**kw:data)
    monkeypatch.setattr(m.previous,'verify_capture_alignment',lambda *args,**kw:None)
    monkeypatch.setattr(m.previous,'extract_batched',lambda torch_arg,frozen,tokenizer,prompts,device,max_tokens:
                        torch.randn(len(prompts),m.h.INPUT_WIDTH))
    class FakeFrozen:
        def eval(self):return self
    cassette=types.ModuleType('nanojev_numerate_spine_cassette_smoke')
    cassette.load_source=lambda *args:(None,FakeFrozen(),None,torch.device('cpu'),identity['model_info'])
    monkeypatch.setitem(sys.modules,'nanojev_numerate_spine_cassette_smoke',cassette)
    root=tmp_path/'route-v3'
    common=['--v3-root',str(bench),'--results-root',str(root),
            '--source-checkpoint',str(source),'--device','cpu','--dtype','float32',
            '--seed','123','--buffer-capacity','4','--per-expert-batch','1',
            '--batch-size','2','--replay-updates','1','--op-width','8',
            '--route-width','8','--warmup-steps','2','--eval-every','1',
            '--checkpoint-every','1','--report-every','1','--threads','2']
    for branch in ('direct','result'):
        assert m.main(common+['--branch',branch,'--steps','2'])==0
        ck=torch.load(root/branch/'latest.pt',weights_only=True)
        assert ck['step']==2 and ck['source_cutover']['sha256']==sha
        assert ck['schema']==m.SCHEMA
        assert all((root/branch/name).exists() for name in ('best.pt','best_joint.pt','best_route.pt'))
        assert m.main(common+['--branch',branch,'--steps','3','--resume'])==0
        assert m.main(common+['--branch',branch,'--steps','3','--resume','--eval-only'])==0
    ck_a=torch.load(root/'direct'/'latest.pt',weights_only=True)
    ck_b=torch.load(root/'result'/'latest.pt',weights_only=True)
    assert ck_a['random_stream']['seen']==ck_b['random_stream']['seen']
    assert ck_a['source_cutover']['fork_seed']==ck_b['source_cutover']['fork_seed']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==sha
    with pytest.raises(RuntimeError,match='CONFIG_DRIFT'):
        m.main(common+['--branch','direct','--steps','3','--resume','--route-weight','.8'])


def test_argument_validation_and_schema():
    p=m.parser()
    assert m.SCHEMA!=m.v2.SCHEMA
    opts=p.parse_args([])
    assert opts.route_width==128
    assert opts.wrong_route_weight>=1
    with pytest.raises(ValueError,match='ROUTE_INVALID_ARGUMENTS'):
        m.main(['--route-weight','-1'])
