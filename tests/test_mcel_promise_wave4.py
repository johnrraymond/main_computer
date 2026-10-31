"""Patch 04's independent product-behavior tests, distinct from aesthetic review."""
from __future__ import annotations
import json
from pathlib import Path
from tools.mcel_promise_wave4_frob import run, source_status, authoring_economy


def test_source_provenance_reports_truth_not_invented_recovery():
    source = source_status()
    assert source['status'] in {'PASS','BLOCKED','FAIL'}
    if not authoring_economy()['sourceAvailable']:
        assert source['status']=='BLOCKED'
        assert 'browserArtifactSha256' in source


def test_second_design_requires_no_second_app_source_or_adapter():
    metrics=authoring_economy()
    assert metrics['additionalAppSemanticDefinitionsForSecondDesign']==0
    assert metrics['additionalAppSpecificCSSForSecondDesign']==0
    assert metrics['additionalDomainAdaptersForSecondDesign']==0
    assert metrics['designSelectionValues']==['operational','analytical']
    assert metrics['humanEffortComparison']=='UNVERIFIED'


def test_wave4_browser_profiles_shared_ir_live_lifecycle_and_stress(tmp_path:Path):
    result=run(tmp_path)
    assert result['offline']=='PASS'
    assert len(result['cases'])==10
    assert all(c['status']=='PASS' for c in result['cases'])
    assert result['independentApp']['good']
    assert result['longText']['contentChanged']
    assert result['longText']['documentWidth']<=320
    assert result['acceptance']=='INCOMPLETE'  # Never substitute geometry for human judgment.
    assert result['hosted']=='BLOCKED'
    assert len(list(tmp_path.glob('*.png')))==4
    assert json.loads((tmp_path/'report.json').read_text())['offline']=='PASS'
