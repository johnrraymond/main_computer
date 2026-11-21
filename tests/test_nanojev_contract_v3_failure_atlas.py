import json
from pathlib import Path
import pytest
from tools import nanojev_contract_v3_failure_atlas as atlas
from tools import nanojev_contract_v3_generalization_eval as ge

BASELINE=Path(__file__).resolve().parents[1]/'tools'/'fixtures'/'nanojev_v3_semantic'/'observed_40_minimal.json'

def test_historical_measured_live_atlas():
    r=atlas.build(json.loads(BASELINE.read_text()),ge.DEFAULT_CORPUS)
    assert r['baseline_correct']==20
    assert r['baseline_total']==40
    assert r['unsafe_acceptance_count']==6
    assert r['unsafe_reusing_original_ir']==5
    assert r['wrong_ir_count']==4
    assert r['wrong_refusal_reason_count']==5
    assert len(r['failures'])==20
    assert r['status']=='DESCRIPTIVE_BASELINE_NO_NEW_INFERENCE'
