"""Retirement timer, selection continuity, and reused-identity browser acceptance."""
from pathlib import Path
from tools.mcel_patch14_7_3_retirement_frob import verify

def test_mcel_retirement(tmp_path:Path):
    report=verify(tmp_path/'retirement')
    assert report['status']=='PASS',report['cases']
    assert len(report['cases'])==4
