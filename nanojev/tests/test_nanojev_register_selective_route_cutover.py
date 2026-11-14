"""Numerical semantics, provenance and staged integration for v3.1 selective route cutover."""
import hashlib
import json
import sys
import types
import numpy as np
import pytest
import torch
import nanojev_register_selective_route_cutover as m


def net():
    return m.SelectiveRouteNumeracy(op_width=8,width=8)


def example_data(count=20):
    rng=np.random.default_rng(84)
    features=rng.normal(size=(count,m.h.INPUT_WIDTH)).astype('float32')
    vals=np.asarray([[135000,30],[-550,-12000],[180000,-1050],[12,1312273],[0,12]],dtype=np.int64)
    ops=['written_product','written_difference','written_sub','sym_reverse_sub','sym_add']
    families=['written','written','written','symbolic','symbolic']
    indexes=np.arange(count)%len(vals)
    return features, vals[indexes], [ops[i] for i in indexes], [families[i] for i in indexes]


def test_source_preserved_on_zero_step_and_unselected():
    model=net()
    for count in (1,7):
        x=torch.randn(count,m.h.INPUT_WIDTH)
        p,base,oplogits,aux=model(x)
        assert torch.equal(p,base)
        assert not aux['replaced'].any()
        assert model.source.training is False
        assert all(not p.requires_grad for p in model.source.parameters())
    v=example_data()
    report=m.initial_report(v,model,torch.device('cpu'),oracle=True)
    assert report['result_5pct_pass_rate']==report['source_result_5pct_pass_rate']
    assert report['joint_xy_5pct_pass_rate']==report['source_joint_xy_5pct_pass_rate']
    assert report['correct_route_retention']==1
    assert report['replacements']==0
    assert report['oracle_route_operand_5pct']>=0


def test_oracle_route_is_training_only_and_selector_does_not_receive_truth():
    model=net()
    x=torch.randn(4,m.h.INPUT_WIDTH)
    enc=model.encode(x)
    numbers=torch.tensor([[135000,30],[180000,-1050],[-550,-12000],[0,12]])
    truth=m.v3.RouteAwareNumeracy.route_classes(numbers)
    result,delta,candidate=model.reconstruct(enc[0],enc[2],truth)
    assert result.shape==(4,2)
    assert torch.isfinite(result).all()
    # Oracle reconstruction uses only the selected expert and its own residual.
    torch.testing.assert_close(torch.asinh(result),candidate,atol=1e-12,rtol=0)
    # The production forward path cannot see the above labels.
    a=model(x)[0]
    b=model(x)[0]
    assert torch.equal(a,b)


def test_train_objectives_are_separate_and_frozen_source():
    model=net()
    features=torch.randn(4,m.h.INPUT_WIDTH)
    labels=torch.tensor([[135000,30],[180000,-1050],[-550,-12000],[0,12]])
    names=['written_product','written_sub','written_difference','sym_add']
    args=m.parser().parse_args(['--selective-width','8'])
    for stage,expected in [('detector','detector'),('selector','selector'),('reconstruct','reconstructor')]:
        model.zero_grad(set_to_none=True)
        model.freeze_for(stage)
        encoded=model.encode(features)
        loss,parts=m.supervised_loss(model,encoded,labels,names,stage,args)
        if loss.requires_grad:loss.backward()
        assert torch.isfinite(loss)
        for name in ('detector','selector','reconstructor'):
            assert all(p.requires_grad==(name==expected) for p in getattr(model,name).parameters())
        assert all(p.grad is None for p in model.source.parameters())


def test_hard_replacement_never_averages_and_selects_single_expert():
    model=net()
    x=torch.randn(3,m.h.INPUT_WIDTH)
    with torch.no_grad():
        model.selector[-1].bias[:]=-100
        model.selector[-1].bias[10]=100  # x: positive band 4
        model.selector[-1].bias[13+10]=100  # y: positive band 4
        model.detector[-1].bias[:]=15
    encoded=model.encode(x)
    model.threshold=.9;model.route_threshold=.9
    p,b,_,aux=model.infer_from_encoded(encoded)
    chosen,_,candidate=model.reconstruct(encoded[0],encoded[2],aux['selected_route'])
    torch.testing.assert_close(p,torch.where(aux['replaced'],chosen,b),atol=0,rtol=0)
    assert (aux['selected_route']==10).all()
    assert aux['replaced'].any()
    assert not torch.allclose(p,b)
    # Calibrated disabled mode must retain source exactly despite large logits.
    p2,b2,_,a2=model.infer_from_encoded(encoded,1.01,1.01)
    assert torch.equal(p2,b2)
    assert a2['replaced'].sum()==0


def test_large_cross_bucket_candidate_is_used_without_soft_averaging():
    model=net()
    raw=torch.zeros(1,2*m.h.HEADS_PER_REGISTER)
    z=raw.view(1,2,m.h.HEADS_PER_REGISTER)
    z[:,:,0]=-20  # nonzero
    z[:,:,1]=20   # positive
    z[:,:,2:5]=20
    z[:,:,5]=-20  # source band 3, original +20,000 boundary
    z[:,:,6]=-20
    z[:,:,7:]=-1
    z[:,:,7+2*3+1]=1  # source band 3's upper edge
    target_coord=m.h.expert_encode(np.asarray([135000.]),np.asarray([4]))[0]
    z[:,:,7+2*4+1]=float(target_coord)
    model.source.forward=lambda features: (raw.expand(len(features),-1),
                                            torch.zeros(len(features),len(m.op.TRAIN_OPS)))
    with torch.no_grad():
        model.detector[-1].bias[:]=20
        model.selector[-1].bias[:]=-100
        model.selector[-1].bias[10]=100
    model.threshold=.95;model.route_threshold=.95
    predicted,base,_,aux=model(torch.zeros(1,m.h.INPUT_WIDTH))
    assert int(base[0,0])==20000
    assert bool(aux['replaced'][0,0])
    assert int(aux['selected_route'][0,0])==10
    assert abs(float(predicted[0,0].detach())-135000)<500
    # This is a forced synthetic path, not trained model validation.


def test_validation_partition_does_not_overlap():
    data=example_data(24)
    a,b=m.validation_partition(data)
    assert len(a[0])==12 and len(b[0])==12
    fingerprints={row.tobytes() for row in a[0]}
    assert all(row.tobytes() not in fingerprints for row in b[0])
    assert len(a[2])+len(b[2])==len(data[2])


def test_calibration_fails_closed_and_respects_retention(monkeypatch):
    model=net()
    v=example_data(16)
    # With neutral detector and selector, no route is confidence-qualified.
    result=m.calibrate(model,v,torch.device('cpu'),1.0)
    assert result['chosen_is_source_only']
    assert result['detector_threshold']>1
    # Substituting a held-out validation set does not modify this result.
    assert result['calibration_selected']==result['calibration_baseline']
    with torch.no_grad():
        model.detector[-1].bias[:]=20
        model.selector[-1].bias[:]=-30
        model.selector[-1].bias[10]=30
    result=m.calibrate(model,v,torch.device('cpu'),1.0)
    model.threshold=result['detector_threshold'];model.route_threshold=result['selector_threshold']
    report=m.initial_report(v,model,torch.device('cpu'))
    assert report['correct_route_retention']>=1.0
    assert report['numeric_result_5pct_pass_rate']>=report['source_numeric_result_5pct_pass_rate']


def fake_source(tmp_path,identity):
    tasks=[{'a':3,'b':4,'split':'val','operation':'sym_add','family':'symbolic'},
           {'a':7,'b':8,'split':'test','operation':'sym_sub','family':'symbolic'},
           {'a':9,'b':10,'split':'control','operation':'written_average','family':'written'}]
    network=m.op.ExecutableNumeracy(op_width=8)
    optimizer=torch.optim.AdamW(network.parameters())
    stream=m.h.make_stream(123,tasks,'balanced')
    rows=stream.batch(5)
    replay=m.op.OperationReplay(4,140)
    replay.add(torch.randn(5,m.h.INPUT_WIDTH),rows)
    path=tmp_path/'source'/'latest.pt';path.parent.mkdir(parents=True)
    config={'constraint_stage':'continuity','rank':8,'residual_scale':.25,
            'buffer_capacity':4,'op_width':8,'operation_catalog':list(m.op.TRAIN_OPS),
            'operation_programs':m.op.OP_TO_PROGRAM,'sampler':'balanced','seed':123}
    m.op.save(path,network,optimizer,3000,config,stream,replay,
            {'result':.28,'op_accuracy':1.},identity,{'source_step':44000})
    return path,tasks


def test_end_to_end_phases_and_resume(tmp_path,monkeypatch):
    identity={'model':'qwen','model_info':{'resolved_commit':'fake','source':'fake','dtype':'float32'}}
    source,tasks=fake_source(tmp_path,identity)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    bench=tmp_path/'benchmark';bench.mkdir()
    (bench/'model_identity.json').write_text(json.dumps(identity))
    monkeypatch.setattr(m.previous,'read_benchmark',lambda *args:({'tasks_sha256':'fake'},tasks))
    data=example_data(12)
    monkeypatch.setattr(m.previous,'load_eval',lambda *args,**kw:data)
    monkeypatch.setattr(m.previous,'verify_capture_alignment',lambda *args,**kw:None)
    monkeypatch.setattr(m.previous,'extract_batched',lambda torch_arg,frozen,tokenizer,prompts,device,max_tokens:
                        torch.randn(len(prompts),m.h.INPUT_WIDTH))
    class FakeFrozen:
        def eval(self):return self
    cassette=types.ModuleType('nanojev_numerate_spine_cassette_smoke')
    cassette.load_source=lambda *args:(None,FakeFrozen(),None,torch.device('cpu'),identity['model_info'])
    monkeypatch.setitem(sys.modules,'nanojev_numerate_spine_cassette_smoke',cassette)
    root=tmp_path/'v31'
    common=['--v3-root',str(bench),'--results-root',str(root),
      '--source-checkpoint',str(source),'--device','cpu','--dtype','float32',
      '--seed','123','--buffer-capacity','4','--per-expert-batch','1',
      '--batch-size','2','--replay-updates','1','--op-width','8',
      '--selective-width','8','--eval-every','1','--checkpoint-every','1',
      '--report-every','1','--threads','2']
    with pytest.raises(FileNotFoundError,match='PREVIOUS_STAGE_REQUIRED'):
        m.main(common+['--stage','selector','--steps','1'])
    for phase in ('detector','selector','reconstruct','calibrate','direct','result'):
        assert m.main(common+['--stage',phase,'--steps','1'])==0
        ck=torch.load(root/phase/'latest.pt',weights_only=True)
        if phase=='detector':
            assert ck['complete']
            trial=dict(ck,complete=False)
            torch.save(trial,root/phase/'latest.pt')
            with pytest.raises(RuntimeError,match='PARENT_STAGE_INCOMPLETE'):
                m.main(common+['--stage','selector','--steps','1'])
            torch.save(ck,root/phase/'latest.pt')
        assert ck['schema']==m.SCHEMA
        assert ck['source_cutover']['sha256']==digest
        if phase=='calibrate':
            assert ck['calibration'] is not None
        else:
            assert (root/phase/'best.pt').exists()
    for phase in ('direct','result'):
        ck=torch.load(root/phase/'latest.pt',weights_only=True)
        assert ck['calibration']==torch.load(root/'calibrate'/'latest.pt',weights_only=True)['calibration']
        assert m.main(common+['--stage',phase,'--steps','2','--resume'])==0
        assert m.main(common+['--stage',phase,'--steps','2','--eval-only'])==0
    a=torch.load(root/'direct'/'latest.pt',weights_only=True)
    b=torch.load(root/'result'/'latest.pt',weights_only=True)
    assert a['random_stream']['seen']==b['random_stream']['seen']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    with pytest.raises(RuntimeError,match='CONFIG_DRIFT'):
        m.main(common+['--stage','direct','--steps','2','--resume','--detector-pos-weight','50'])


def test_self_test():
    m.self_test()
