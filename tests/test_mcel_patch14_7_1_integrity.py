"""Patch 14.7.1 regression for canonical slot, atomic fallback and live details."""
from pathlib import Path
from tools.mcel_patch14_7_1_integrity_frob import verify


def test_integrity_and_live_continuity_in_real_browser(tmp_path:Path):
    report=verify(tmp_path/'proof')
    assert report['status']=='PASS',report['cases']
    assert report['readOnly'] and not report['productionPromotion']
    assert len(report['cases'])==4
    for case in report['cases']:
        assert case['status']=='PASS',case
        assert case['layoutValid'] and case['treeValid'] and case['rejectsForged']
        assert case['deniedKeepsCommitted'] and case['fallbackConsistent']
        assert case['deferred'] and case['refreshedAfterSelection']
        assert case['copyUpdated'] and case['overlayRetained'] and case['anchorStable']
