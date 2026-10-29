"""Observation/order, exception rollback, and cross-lifecycle browser gates."""
from pathlib import Path
from tools.mcel_patch14_7_5_adversarial_lifecycle_frob import verify

def test_mcel_patch14_7_5(tmp_path:Path):
    result=verify(tmp_path/'adversarial',steps=35,seeds=(1,7))
    assert result['status']=='PASS',[(case['state'],case['width'],case.get('seed'),case.get('result',{}).get('failure')) for case in result['cases'] if not case['pass']]
