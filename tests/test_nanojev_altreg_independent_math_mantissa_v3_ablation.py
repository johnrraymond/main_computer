"""CPU-only ablation and transition tests."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import numpy as np
import pytest
from nanojev_altreg_independent_math_mantissa_v3_ablation import coordinates, hit, status, build_rows, aggregate


def test_identity_and_single_component_isolation():
    p=np.array([[135.,-27.]])
    t=np.array([[150.,-30.]])
    de=np.array([[0.1,0.]])
    dm=np.array([[0.,0.2]])
    v=coordinates(p,de,dm,t,1e8)
    assert np.allclose(v['parent'],p)
    assert np.isclose(v['exponent_only'][0,1],p[0,1])
    assert np.isclose(v['mantissa_only'][0,0],p[0,0])
    assert v['full'][0,0]>p[0,0]
    assert abs(v['full'][0,1])>abs(p[0,1])


def test_decimal_carry_normalizes_oracle_reconstruction():
    p=np.array([[9.8]])
    t=np.array([[10.2]])
    v=coordinates(p,np.array([[0.03]]),np.zeros_like(p),t,1e6)
    assert v['full'][0,0]>10
    assert 10<=v['oracle_exponent'][0,0]<100
    assert 10<=v['oracle_mantissa'][0,0]<100


def test_sign_and_subunit_inheritance():
    p=np.array([[-27.,0.2,0.]])
    t=np.array([[-35.,1.,0.]])
    v=coordinates(p,np.ones_like(p),np.ones_like(p),t,1e6)
    assert v['full'][0,0]<0
    assert v['full'][0,1]==0.2 and v['full'][0,2]==0


def test_input_validation():
    with pytest.raises(ValueError,match='SHAPE'):
        coordinates(np.zeros((1,2)),np.zeros((2,2)),np.zeros((1,2)),np.ones((1,2)),1e6)
    with pytest.raises(ValueError,match='NONFINITE'):
        coordinates(np.array([[np.nan]]),np.zeros((1,1)),np.zeros((1,1)),np.ones((1,1)),1e6)


def test_transition_categories():
    assert status(False,True)=='repaired'
    assert status(True,False)=='regressed'
    assert status(True,True)=='preserved_success'
    assert status(False,False)=='unchanged_failure'
    assert bool(hit(104.,100.)) and not bool(hit(106.,100.))


def test_rows_and_joint_counts():
    class Pack(dict): pass
    pack={'examples':[{'a':100,'b':20,'operation':'add'},{'a':100,'b':20,'operation':'add'}]}
    truth=np.array([[100.,20.],[100.,20.]])
    parent=np.array([[100.,30.],[120.,20.]])
    full=np.array([[100.,20.],[120.,25.]])
    outputs={o:{k: (parent if k=='parent' else full) for k in (
        'parent','full','exponent_only','mantissa_only','oracle_exponent','oracle_mantissa')}
        for o in ('original','reversed','averaged')}
    rows=build_rows('validation','latest',pack,truth,outputs)
    summary=aggregate(rows)
    assert len(rows)==12
    item=next(s for s in summary if s['orientation']=='original')
    assert item['modes']['parent']['joint_hits']==0
    assert item['modes']['full']['joint_hits']==1
    assert item['modes']['full_vs_parent']['repaired']==1
    assert item['modes']['full_vs_parent']['regressed']==1
