"""14.7.2: semantic text mutations without browser selection destruction."""
from pathlib import Path
from tools.mcel_patch14_7_2_selection_continuity_frob import verify,SCHEMA

def test_selection_aware_mutation_real_task_manager(tmp_path:Path):
    result=verify(tmp_path/'selection')
    assert result['schema']==SCHEMA
    assert result['readOnly'] and not result['productionPromotion']
    assert result['status']=='PASS',result['cases']
    assert len(result['cases'])==4
    assert all(c['status']=='PASS' for c in result['cases'])
