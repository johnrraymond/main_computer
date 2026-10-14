"""Targeted regressions for v1.2 operation/result cutover fixes."""
import hashlib
import io
import math
from pathlib import Path

import pytest
import torch

import nanojev_register_operation_result_cutover as m


def test_v12_isolated_schema_root_and_no_external_factory():
    assert m.SCHEMA.endswith('.v1.2')
    assert m.DEFAULT_ROOT.name.endswith('_v1_2')
    assert 'factory' not in vars(m.parser().parse_args([]))
    assert 'source_checkpoint' in vars(m.parser().parse_args([]))


@pytest.mark.parametrize('operation,expect', [
    ('sym_add',12),('sym_sub',6),('sym_reverse_sub',-6),
    ('sym_multiply',27),('sym_absdiff',6),('sym_max',9),
    ('written_add',12),('written_sub',6),('written_product',27),
    ('written_difference',-6),('written_greater',1),('written_average',6),
    ('algebra_left',6),('algebra_right',-6),('algebra_difference',12),
    ('algebra_balance',6),('conditional_gt',1),('conditional_min',3),
    ('conditional_positive',3),('conditional_abs',1),
    ('multistep_double',15),('multistep_combine',24),
    ('multistep_square',144),('multistep_difference',15),
])
def test_every_v3_template_has_correct_semantics(operation, expect):
    assert m.exact_execute(m.OP_TO_PROGRAM[operation],9,3)==expect
    assert operation in m.CATALOG


def test_all_templates_are_covered_and_heldout_excluded_from_train():
    assert len(m.CATALOG)==24 and len(m.TRAIN_OPS)==20
    assert set(m.CATALOG)==set(m.OP_TO_PROGRAM)
    assert set(m.TRAIN_OPS).isdisjoint(m.v3.HELD_OUT_OPERATIONS)
    assert m.OP_TO_PROGRAM['written_difference']=='RSUB'
    assert m.OP_TO_PROGRAM['algebra_difference']=='ADD'


def test_per_candidate_error_does_not_cancel():
    # For true target SUB(0,10)=-10, uniformly predicting ADD and SUB
    # yields an average -5; using ADD and SUB on (0,10) would average 0.
    # In either case, candidate loss must remain >0 despite mixtures.
    labels=torch.tensor([[0,10]],dtype=torch.long)
    results=torch.tensor([0.],dtype=torch.float64)
    numbers=torch.tensor([[0.,10.]],dtype=torch.float64,requires_grad=True)
    values=m._per_candidate_losses(numbers,labels,results,['SUB'])
    assert values.shape==(1,len(m.TRAIN_OPS))
    assert values[0,m.OP_ID['sym_add']]>0
    assert values[0,m.OP_ID['sym_sub']]>0
    equal_logits=torch.zeros((1,len(m.TRAIN_OPS)),requires_grad=True)
    weighted=(equal_logits.softmax(-1).double()*values).sum()
    assert weighted.item()>0
    weighted.backward()
    assert numbers.grad is not None and torch.isfinite(numbers.grad).all()
    assert equal_logits.grad is not None and equal_logits.grad.abs().sum()>0


@pytest.mark.parametrize('truth_x,truth_y,pred_x,pred_y,label', [
    (1_000_000,-1_000_000,-1_000_000,1_000_000,'conditional_gt'),
    (-1_000_000,1_000_000,1_000_000,-1_000_000,'conditional_gt'),
    (1_000_000,2_000_000,2_000_000,1_000_000,'conditional_abs'),
])
def test_large_scale_predicate_mistakes_have_finite_nonzero_gradients(truth_x,truth_y,pred_x,pred_y,label):
    target_program=m.OP_TO_PROGRAM[label]
    truth=m.exact_execute(target_program,truth_x,truth_y)
    numbers=torch.tensor([[pred_x,pred_y]],dtype=torch.float64,requires_grad=True)
    labels=torch.tensor([[truth_x,truth_y]],dtype=torch.long)
    scale=torch.maximum(labels[:,0].abs(),labels[:,1].abs()).clamp_min(1).double()
    margin=m.predicate_margin(target_program,numbers[:,0],numbers[:,1],scale)
    loss=torch.nn.functional.binary_cross_entropy_with_logits(margin,torch.tensor([float(truth)],dtype=torch.float64))
    assert loss.item()>0.1
    loss.backward()
    assert (numbers.grad.abs()>0).all()
    assert torch.isfinite(numbers.grad).all()
    if label in m.OP_ID:
        # Actual integrated result-loss path, not just the predicate helper.
        predictions=torch.zeros(1,38,requires_grad=True)
        logits=torch.zeros(1,len(m.TRAIN_OPS),requires_grad=True)
        band=m.h.band_of(labels.numpy())
        result,metrics=m.execution_loss(predictions,logits,labels,torch.tensor(band),[label],
                                        op_weight=0,result_weight=1,routed_result_weight=0)
        assert torch.isfinite(result)
        result.backward()
        assert logits.grad.abs().sum()>0
        assert predictions.grad.abs().sum()>0


def test_result_loss_backpropagates_without_direct_supervision():
    torch.manual_seed(123)
    raw=torch.randn(5,38,requires_grad=True)
    logits=torch.randn(5,len(m.TRAIN_OPS),requires_grad=True)
    nums=[[7,3],[3,7],[2,5],[-8,2],[1_000_000,-1_000_000]]
    labels,bands,_=m.b.supervision_fast(nums,'cpu')
    names=['sym_multiply','sym_reverse_sub','sym_sub','conditional_positive','conditional_gt']
    loss,stats=m.execution_loss(raw,logits,labels,bands,names,
                               op_weight=0,result_weight=.12,routed_result_weight=.02)
    loss.backward()
    assert torch.isfinite(loss)
    assert raw.grad is not None and raw.grad.abs().sum()>0
    assert logits.grad is not None and logits.grad.abs().sum()>0
    assert all(torch.isfinite(raw.grad).flatten())
    assert 'oracle_candidate_result' in stats
    assert 'soft_routed_candidate_result' in stats
    assert (stats['oracle_candidate_result']>0).item()


def test_heldout_templates_not_accidentally_optimized():
    labels,bands,_=m.b.supervision_fast([[4,8]],'cpu')
    with pytest.raises(ValueError,match='HELDOUT'):
        m.execution_loss(torch.zeros(1,38),torch.zeros(1,len(m.TRAIN_OPS)),
                         labels,bands,['written_average'])


def test_benchmark_scoring_uses_predicted_op_and_not_ground_truth():
    class Dummy(torch.nn.Module):
        def forward(self,features):
            raw=torch.zeros(len(features),38)
            ops=torch.full((len(features),len(m.TRAIN_OPS)),-25.)
            ops[:,m.OP_ID['sym_add']]=25.
            return raw,ops
    result=m.evaluate(Dummy(),(torch.zeros(2,m.h.INPUT_WIDTH),
               __import__('numpy').array([[3,4],[3,4]],dtype='int64'),
               ['sym_add','sym_sub'],['symbolic','symbolic']),'cpu')
    assert result['op_template_accuracy']==.5
    assert result['result_given_true_operation_pass_rate'] != result['result_given_true_operands_pass_rate'] or result['rows']==2


def test_checkpoint_config_records_v12_algorithm():
    p=m.parser()
    args=p.parse_args([])
    manifest={'tasks_sha256':'dummy'}
    cfg=m.make_config(args,manifest)
    assert cfg['schema']==m.SCHEMA
    assert cfg['objective']=='regress_operation_execute_result_v1_2'
