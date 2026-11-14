"""Regression tests for non-mutating op/result cutover and differentiable execution."""
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import pytest
import torch

import nanojev_register_operation_result_cutover as m


def test_catalog_matches_real_v3_and_excludes_heldout():
    assert len(m.TRAIN_OPS)==20
    assert len(m.CATALOG)==24
    assert set(m.CATALOG)==set(m.OP_TO_PROGRAM)
    assert not (set(m.TRAIN_OPS)&set(m.v3.HELD_OUT_OPERATIONS))
    assert [op for op in m.v3.HELD_OUT_OPERATIONS if op not in m.OP_ID]


@pytest.mark.parametrize('op,x,y,want',[
    ('sym_add',3847,2916,6763),('sym_sub',3847,2916,931),
    ('sym_reverse_sub',3847,2916,-931),('sym_multiply',3847,2916,11217852),
    ('sym_absdiff',-3,4,7),('sym_max',-3,4,4),
    ('written_sub',3,4,-1),('written_difference',3,4,1),
    ('written_greater',3,4,0),('conditional_gt',3,4,0),
    ('conditional_min',-3,4,-3),('conditional_positive',-3,4,-3),
    ('conditional_positive',3,4,4),('algebra_left',3,4,-1),
    ('algebra_right',3,4,1),('algebra_difference',3,4,7),
    ('algebra_balance',3,4,-1),('multistep_double',3,4,2),
    ('multistep_combine',3,4,14),('multistep_difference',3,4,2),
    ('written_average',3,4,3.5),('multistep_square',3,4,49),
    ('conditional_abs',-3,4,0),('conditional_abs',-7,4,1),
])
def test_template_semantics(op,x,y,want):
    assert m.exact_execute(m.OP_TO_PROGRAM[op],x,y)==want
    t=m.execute_tensor(m.OP_TO_PROGRAM[op],torch.tensor([float(x)]),
                       torch.tensor([float(y)]),smooth=False)
    assert float(t[0])==pytest.approx(want)


def test_exact_arithmetic_not_free_language_model_prediction():
    assert m.exact_execute('MUL',2_000_000,-2_000_000)==-4_000_000_000_000
    assert m.exact_execute('SQUARE_SUM',2_000_000,2_000_000)==16_000_000_000_000
    assert m.exact_execute('GT',42,42)==0
    assert m.exact_execute('ABS_X_GE_Y',-42,42)==1


def test_teacher_decode_matches_38_head_hard_oracle():
    numbers=np.asarray([[-20,21],[-200,201],[2000,-2001],[200000,200001],
                        [-2000000,2000000],[0,0]],dtype=np.int64)
    labels,bands,_=m.b.supervision_fast(numbers,'cpu')
    raw=torch.randn((len(numbers),38),requires_grad=True)
    tensor=m.teacher_numbers(raw,labels,bands)
    hard=m.c.decode(raw.detach().numpy(),numbers)
    assert np.all(np.abs(tensor.detach().numpy()-hard)<=.55)
    assert tensor[-1].tolist()==[0,0]
    tensor.sum().backward()
    assert raw.grad.abs().sum()>0


def test_inference_soft_routing_is_finite_and_differentiable():
    raw=torch.randn(7,38,requires_grad=True)
    nums=m.soft_numbers(raw)
    assert nums.shape==(7,2)
    assert torch.isfinite(nums).all()
    nums.square().mean().backward()
    for idx in (0,1,2,7):
        assert raw.grad[:,idx].abs().sum()>0


def test_result_supervision_alone_reaches_both_operation_and_register_weights():
    net=m.ExecutableNumeracy(op_width=8)
    raw,ops=net(torch.randn(4,m.h.INPUT_WIDTH))
    numbers=np.asarray([[3847,2916],[-3,4],[7,-1],[20,3]],dtype=np.int64)
    labels,bands,_=m.b.supervision_fast(numbers,'cpu')
    loss,stats=m.execution_loss(raw,ops,labels,bands,
            ['sym_multiply','sym_sub','conditional_gt','sym_add'],
            op_weight=0,result_weight=1,routed_result_weight=0)
    loss.backward()
    assert torch.isfinite(loss)
    assert net.registers.common_magnitude.weight.grad.abs().sum()>0
    assert net.operations[1].weight.grad.abs().sum()>0
    assert stats['op_ce']>0


def test_soft_routed_loss_reaches_classification_routing_heads():
    net=m.ExecutableNumeracy(op_width=8)
    raw,ops=net(torch.randn(4,m.h.INPUT_WIDTH))
    labels,bands,_=m.b.supervision_fast([[3847,2916],[-3,4],[7,-1],[20,3]],'cpu')
    loss,_=m.execution_loss(raw,ops,labels,bands,
            ['sym_multiply','sym_sub','conditional_gt','sym_add'],
            op_weight=0,result_weight=0,routed_result_weight=1)
    loss.backward()
    assert net.registers.classifiers.weight.grad.abs().sum()>0
    assert net.registers.common_magnitude.weight.grad.abs().sum()>0
    assert net.operations[1].weight.grad.abs().sum()>0


def test_truth_result_and_boolean_scoring_have_correct_tolerances():
    np.testing.assert_array_equal(m.relative_pass([1,0,105,105],[0,1,100,0],
                                                [True,True,False,False]),
                                  [False,False,True,False])


def test_structured_contract_computes_from_predicted_values():
    class Dummy(torch.nn.Module):
        def forward(self,x):
            raw=torch.zeros(len(x),38,device=x.device)
            raw[:,0]=-100; raw[:,19]=-100
            raw[:,1]=100;raw[:,20]=100
            raw[:,7+1]=-1;raw[:,19+7+1]=-1
            op=torch.full((len(x),len(m.TRAIN_OPS)),-40.,device=x.device)
            op[:,m.OP_ID['sym_multiply']]=40.
            return raw,op
    output=m.predict_batch(Dummy(),np.zeros((2,m.h.INPUT_WIDTH),np.float32),'cpu')
    assert len(output)==2
    assert output[0]['operation']=='sym_multiply'
    assert output[0]['result']==output[0]['x']*output[0]['y']
    assert output[0]['result_kind']=='number'
    assert output[0]['operation_probability']>.999


def test_eval_heldout_operation_not_spuriously_classified_correct():
    class Dummy(torch.nn.Module):
        def forward(self,x):
            return torch.zeros(len(x),38),torch.zeros(len(x),len(m.TRAIN_OPS))
    evaluation=(np.zeros((2,m.h.INPUT_WIDTH),dtype=np.float32),
                np.array([[5,3],[5,3]],dtype=np.int64),
                ['sym_add','written_average'],['symbolic','written'])
    output=m.evaluate(Dummy(),evaluation,'cpu')
    assert output['unsupported_heldout_operation_rows']==1
    assert output['rows']==2
    assert output['op_template_accuracy'] is not None
    assert output['result_5pct_pass_rate']>=0
    assert output['true_bucket_expert_normalized_mae']>=0


def test_cutover_preserves_source_and_clones_weights_replay_and_pair_history(tmp_path):
    identity={'model':'qwen','model_info':{'resolved_commit':'test','source':'test','dtype':'float16'}}
    seed=123
    samples=[{'a':3,'b':5,'operation':'sym_add','family':'symbolic','prompt':'x'}]
    oldstream=m.h.make_stream(seed,samples,'balanced')
    oldstream.batch(3)
    oldmodel=m.c.model_factory('continuity',8,.25)
    oldoptimizer=torch.optim.AdamW(oldmodel.parameters())
    oldreplay=m.b.BucketReplay(4,seed+17)
    incoming=[{'a':3,'b':5,'operation':'sym_add','family':'symbolic'}]
    oldreplay.add(torch.ones(1,m.h.INPUT_WIDTH),incoming)
    source_config={'constraint_stage':'continuity','rank':8,'residual_scale':.25,'buffer_capacity':4}
    source=tmp_path/'original'/'best.pt';source.parent.mkdir()
    m.b.save_checkpoint(source,oldmodel,oldoptimizer,25,source_config,oldstream,oldreplay,{'joint':.1},identity)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    args=type('Args',(),dict(constraint_stage='continuity',rank=8,residual_scale=.25,buffer_capacity=4,sampler='balanced',seed=123))()
    newmodel=m.ExecutableNumeracy('continuity',8,.25,op_width=8)
    newstream=m.h.make_stream(seed,samples,'balanced')
    newreplay=m.OperationReplay(4,seed+17)
    snapshot=m.cutover(source,newmodel,newstream,newreplay,identity,args)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    assert snapshot['sha256']==digest
    assert snapshot['source_step']==25
    assert snapshot['source_seen_pairs']==3
    assert len(newreplay.records)==len(oldreplay.records)
    for name,weight in oldmodel.state_dict().items():
        torch.testing.assert_close(newmodel.registers.state_dict()[name],weight)
    newreplay.sample_labeled(1)
    assert newstream.seen==oldstream.seen
    assert newstream.rng.getstate()!=oldstream.rng.getstate()
    assert len(newstream.batch(3))==3
    assert newstream.seen.isdisjoint(newstream.forbidden)


def test_checkpoint_resume_restores_new_optimizer_and_replay(tmp_path):
    identity={'model':'qwen'}
    config={'schema':m.SCHEMA,'source_checkpoint':'/dummy'}
    tasks=[{'a':3,'b':4}]
    net=m.ExecutableNumeracy(op_width=8)
    opt=torch.optim.AdamW(net.parameters())
    stream=m.h.make_stream(3,tasks,'balanced')
    replay=m.OperationReplay(3,20)
    replay.add(torch.ones(1,m.h.INPUT_WIDTH),[{'a':2,'b':5,'operation':'sym_add','family':'symbolic'}])
    path=tmp_path/'latest.pt'
    m.save(path,net,opt,99,config,stream,replay,{'result':.12},identity,{'sha256':'abc','source_step':77,'source_seen_pairs':12})
    second=m.ExecutableNumeracy(op_width=8)
    opt2=torch.optim.AdamW(second.parameters())
    stream2=m.h.make_stream(3,tasks,'balanced')
    replay2=m.OperationReplay(3,20)
    step,best,source=m.restore(path,second,opt2,stream2,replay2,config,identity)
    assert step==99 and best['result']==.12 and source['sha256']=='abc'
    assert len(replay2.records)==1
    for k,v in net.state_dict().items():torch.testing.assert_close(second.state_dict()[k],v)
    with pytest.raises(RuntimeError,match='CONFIG_OR_IDENTITY_DRIFT'):
        m.restore(path,second,opt2,stream2,replay2,{'schema':'wrong'},identity)


def test_end_to_end_train_and_resume_with_fake_frozen_capture(tmp_path,monkeypatch):
    """Exercise the actual CLI orchestration, optimizer, checkpoint and evaluation.

    The 0.6B Qwen weights are intentionally not downloaded by this test.
    """
    import types
    manifest={'tasks_sha256':'dummy-hash'}
    benchmark=[{'a':3,'b':4,'split':'val','operation':'sym_add','family':'symbolic'},
               {'a':7,'b':8,'split':'test','operation':'sym_sub','family':'symbolic'},
               {'a':9,'b':10,'split':'control','operation':'written_average','family':'written'}]
    identity={'model':'qwen','model_info':{'resolved_commit':'test-commit','source':'mock-qwen','dtype':'float32'}}
    v3_root=tmp_path/'benchmark';v3_root.mkdir()
    (v3_root/'model_identity.json').write_text(json.dumps(identity))
    src=tmp_path/'running-experiment'/'best.pt';src.parent.mkdir()
    oldmodel=m.c.model_factory('continuity',8,.25)
    oldoptimizer=torch.optim.AdamW(oldmodel.parameters())
    oldstream=m.h.make_stream(123,benchmark,'balanced')
    incoming=oldstream.batch(5)
    oldreplay=m.b.BucketReplay(4,140)
    oldreplay.add(torch.randn(5,m.h.INPUT_WIDTH),incoming)
    source_config={'constraint_stage':'continuity','rank':8,'residual_scale':.25,
                   'buffer_capacity':4,'sampler':'balanced','seed':123}
    m.b.save_checkpoint(src,oldmodel,oldoptimizer,25,source_config,oldstream,oldreplay,
                        {'joint':.001,'loss':.1},identity)
    orig_digest=hashlib.sha256(src.read_bytes()).hexdigest()
    monkeypatch.setattr(m.previous,'read_benchmark',lambda root:(manifest,benchmark))
    fake_eval=(np.random.default_rng(10).normal(size=(3,m.h.INPUT_WIDTH)).astype('float32'),
               np.asarray([[3,4],[5,6],[7,8]],dtype=np.int64),
               ['sym_add','sym_sub','written_average'],['symbolic','symbolic','written'])
    monkeypatch.setattr(m.previous,'load_eval',lambda *a,**k:fake_eval)
    monkeypatch.setattr(m.previous,'verify_capture_alignment',lambda *a,**k:None)
    monkeypatch.setattr(m.previous,'extract_batched',
                        lambda torch_arg,frozen,tokenizer,prompts,device,max_tokens: torch.randn(len(prompts),m.h.INPUT_WIDTH))
    class FakeFrozen:
        def eval(self):return self
    fake_cassette=types.ModuleType('nanojev_numerate_spine_cassette_smoke')
    fake_cassette.load_source=lambda *a:(None,FakeFrozen(),None,torch.device('cpu'),identity['model_info'])
    monkeypatch.setitem(sys.modules,'nanojev_numerate_spine_cassette_smoke',fake_cassette)
    root=tmp_path/'new-experiment'
    base=['--v3-root',str(v3_root),'--results-root',str(root),
          '--source-checkpoint',str(src),'--device','cpu','--dtype','float32',
          '--seed','123','--buffer-capacity','4','--per-expert-batch','1',
          '--batch-size','2','--replay-updates','1','--op-width','8',
          '--eval-every','1','--checkpoint-every','1','--report-every','1']
    assert m.main(base+['--steps','2'])==0
    assert (root/'latest.pt').exists() and (root/'best.pt').exists()
    ck=torch.load(root/'latest.pt',weights_only=True)
    assert ck['step']==2
    assert ck['source_cutover']['source_step']==25
    assert len(ck['random_stream']['seen'])==9  # 5 imported + 4 new
    assert hashlib.sha256(src.read_bytes()).hexdigest()==orig_digest
    assert m.main(base+['--steps','3','--resume'])==0
    assert torch.load(root/'latest.pt',weights_only=True)['step']==3
    assert m.main(base+['--steps','3','--resume','--eval-only'])==0
    assert hashlib.sha256(src.read_bytes()).hexdigest()==orig_digest
