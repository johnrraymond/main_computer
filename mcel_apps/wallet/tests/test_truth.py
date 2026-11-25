import json
from pathlib import Path

def test_wallet_cutover_does_not_claim_live_evidence():
    manifest = json.loads((Path(__file__).resolve().parents[1]/'mcel.app.json').read_text())
    assert manifest['promotion']['promotionExecuted'] is True
    assert manifest['conformance']['currentMode'] == 'structural-only'
    assert manifest['conformance']['missingBridges']
