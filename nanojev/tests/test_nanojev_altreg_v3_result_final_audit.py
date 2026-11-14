"""Contract tests for read-only RESULT/FINAL diagnostic."""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_altreg_v3_result_final_audit as audit


def test_operations_complete_against_legacy_truth():
    # Test 21 existing operation aliases with signed, zero and asymmetric cases.
    names=('sym_add','written_add','algebra_difference','sym_sub','written_sub',
        'algebra_left','algebra_balance','sym_reverse_sub','written_difference',
        'algebra_right','sym_multiply','written_product','sym_absdiff','sym_max',
        'written_greater','conditional_gt','written_average','conditional_min',
        'conditional_positive','conditional_abs','multistep_double',
        'multistep_difference','multistep_combine','multistep_square')
    def reference(a,b,op):
        if op in names[:3]:return a+b
        if op in names[3:7]:return a-b
        if op in names[7:10]:return b-a
        if op in names[10:12]:return a*b
        if op=='sym_absdiff':return abs(a-b)
        if op=='sym_max':return max(a,b)
        if op in ('written_greater','conditional_gt'):return int(a>b)
        if op=='written_average':return (a+b)/2
        if op=='conditional_min':return min(a,b)
        if op=='conditional_positive':return b if a>0 else a
        if op=='conditional_abs':return int(abs(a)>abs(b))
        if op in ('multistep_double','multistep_difference'):return 2*a-b
        if op=='multistep_combine':return 2*(a+b)
        if op=='multistep_square':return (a+b)**2
        raise ValueError(op)
    for a,b in [(-200,75),(0,0),(34,-54),(125,125),(1500000,-999000),(-5,-2)]:
        for op in names:
            assert audit.operation_float(a,b,op)==reference(a,b,op),(a,b,op)


def test_averaged_xy_must_execute_after_averaging():
    # Multiplication of two averaged operands is NOT average of products.
    first=(3.,8.); second=(5.,4.)
    avg=((first[0]+second[0])/2,(first[1]+second[1])/2)
    assert audit.operation_float(*avg,'sym_multiply')==24.
    assert (audit.operation_float(*first,'sym_multiply')+audit.operation_float(*second,'sym_multiply'))/2==22.


def test_boolean_exact_vs_numeric_tolerance():
    gold=np.asarray([1.,0.,200.,-100.]);pred=np.asarray([.6,.49,205.,-100.])
    result=audit.evaluate_values(pred,gold,np.asarray([True,True,False,False]),30.)
    assert result['overall_pass']==1.0
    assert result['boolean_exact']==1.0
    assert result['numeric_5pct']==1.0
    assert result['numeric_examples']==2 and result['boolean_examples']==2
    assert result['asinh_mse']>=0


def test_nonfinite_and_shape_refused():
    for pred,gold,bools in [([1.,float('nan')],[1.,2.],[False,False]),
                            ([1.],[1.,2.],[False,False])]:
        try:audit.evaluate_values(pred,gold,bools,31.)
        except ValueError:pass
        else:raise AssertionError('invalid metric data accepted')


def test_unknown_operation_refused():
    try:audit.operation_float(1,2,'unregistered')
    except ValueError:pass
    else:raise AssertionError('unknown operation accepted')


def test_full_read_only_audit_logic_with_fake_checkpoints(monkeypatch, tmp_path):
    import math
    import types
    import torch
    base=types.ModuleType('nanojev_altreg_independent_math_qwen_precision_v2')
    base.OP_IDS={'sym_add':0};base.BOOL_OPS=set()
    base.X_SCALE=math.asinh(2000000);base.R_SCALE=math.asinh(20000000000000)
    base.operation_truth=lambda a,b,op:a+b
    v3=types.ModuleType('nanojev_altreg_math_v3_decoder_experiment')
    v3.predict=lambda out,base,arm:out['xy']
    monkeypatch.setitem(sys.modules,base.__name__,base)
    monkeypatch.setitem(sys.modules,v3.__name__,v3)
    rows=[{'a':100,'b':30,'operation':'sym_add'},
          {'a':-200,'b':5,'operation':'sym_add'},
          {'a':10,'b':-15,'operation':'sym_add'}]
    vals=torch.as_tensor([[r['a'],r['b']] for r in rows],dtype=torch.float32)
    seq=torch.zeros(3,4,2)
    seq[:,0,0]=vals[:,0];seq[:,1,0]=vals[:,1]
    attn={'validation':{k:{'sequence':seq,'mask':torch.ones(3,4,dtype=torch.bool)}
                        for k in ('original','reversed')}}
    cache={'validation':{'examples':rows,'original':torch.zeros(3,4),
                         'reversed':torch.zeros(3,4)}}
    class Parent:
        def extract(self,h):return h
        def xy_coord(self,h):return torch.zeros(h.shape[0],2)
        def result_coord(self,h,xy,op):
            true_xy=torch.sinh(xy*base.X_SCALE)
            return torch.asinh(true_xy.sum(dim=1,keepdim=True))/base.R_SCALE
        def final_coord(self,h,xy,op,r):return r
    class Decoder:
        def __call__(self,h,pxy,op,token,mask):
            return {'xy':torch.stack((token[:,0,0],token[:,1,0]),dim=1)}
    def fake_loader(*args):return ({'step':200,'config':{'parent_step':13}},
       {'parent_step':13},Parent(),Decoder(),cache,attn)
    monkeypatch.setattr(audit,'load_models',fake_loader)
    report=audit.audit(tmp_path,'best','validation','cpu',2)
    m=report['metrics']['averaged']
    assert m['graft_result']['numeric_5pct']==1.
    assert m['graft_final']['numeric_5pct']==1.
    assert m['oracle_xy_result']['numeric_5pct']==1.
    assert m['executed_attention_xy']['numeric_5pct']==1.
    assert m['legacy_result']['numeric_5pct']==0.
    assert report['step']==200 and report['examples']==3
