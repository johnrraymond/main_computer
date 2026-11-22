"""Guardrails for the Task Manager/SLH-style Worker full functional runner."""
from __future__ import annotations

import json
from pathlib import Path

from tools import mcel_worker_full_functional_acceptance as acceptance


def test_worker_inventory_tracks_every_compiled_intent_without_stale_case_ids():
    catalog = acceptance.inventory()
    assert catalog['count'] >= 25
    names = {item['id'] for item in catalog['intents']}
    assert {'intent:choose-identity', 'intent:register-offer', 'intent:work-now',
            'intent:save-requester', 'intent:pause-selling'} <= names
    assert len(names) == catalog['count']
    assert all(set(item.intents) <= names for item in acceptance.CASES)


def test_worker_missing_ui_execution_cannot_be_certified_by_adapter_or_static_button():
    # A functioning backend signer is not proof that the new MCEL button invokes it.
    all_adapter_cases = {case.id: {'status': 'PASS', 'passedTests': 1}
                         for case in acceptance.CASES if case.lane == 'adapter'}
    report = acceptance.summarize(all_adapter_cases, profile='full',
                                  selected=list(acceptance.CASES), mode='isolated')
    checks = {item['id']: item for item in report['intentCoverage']['intents']}
    assert checks['intent:register-offer']['lanes']['adapter']['status'] == 'PASS'
    assert checks['intent:register-offer']['lanes']['ui']['status'] == 'NOT_TESTED'
    assert checks['intent:register-offer']['status'] == 'NOT_TESTED'
    assert checks['intent:pause-selling']['status'] == 'NOT_TESTED'
    assert report['status'] == 'INCOMPLETE'
    assert report['cutover']['status'] == 'BLOCKED'
    assert all(gate['status'] == 'NOT_TESTED' for gate in report['liveGates'])


def test_worker_skipped_mock_browser_does_not_count_as_pass():
    fake = {item.id: {'status': 'PASS', 'passedTests': 1}
            for item in acceptance.CASES}
    fake['wallet-msk-ui'] = {'status': 'UNVERIFIED', 'passedTests': 0,
                             'skippedTests': 1}
    report = acceptance.summarize(fake, profile='full',
                                  selected=list(acceptance.CASES), mode='isolated')
    assert report['executionStatus'] == 'UNVERIFIED'
    assert report['cutover']['status'] == 'BLOCKED'
    assert report['status'] == 'INCOMPLETE'


def test_inventory_mode_writes_non_certifying_report(tmp_path: Path):
    report = tmp_path / 'inventory.json'
    assert acceptance.main(['--mode', 'inventory', '--report', str(report)]) == 0
    data = json.loads(report.read_text(encoding='utf-8'))
    assert data['schema'] == acceptance.SCHEMA
    assert data['mode'] == 'inventory'
    assert data['executionStatus'] == 'NOT_RUN'
    assert data['intentCoverage']['status'] == 'INCOMPLETE'
    assert data['cutover']['canonicalWorkerRouteChanged'] is False


def test_resume_refuses_stale_source_fingerprint(tmp_path: Path):
    path = tmp_path / 'old.json'
    path.write_text(json.dumps({'schema': acceptance.SCHEMA,
                                'source_fingerprint': 'WRONG', 'cases': []}), encoding='utf-8')
    assert acceptance.main(['--mode', 'isolated', '--profile', 'smoke',
                            '--resume', '--report', str(path)]) == 2


def test_complete_isolated_coverage_does_not_claim_live_cutover():
    # Even when every actual isolated case succeeds, no real wallet signature,
    # Hub offer, work cycle, or requester spending enforcement has been observed.
    cases = {case.id: {'status': 'PASS', 'passedTests': 1}
             for case in acceptance.CASES}
    report = acceptance.summarize(cases, profile='full',
                                  selected=list(acceptance.CASES), mode='isolated')
    assert report['intentCoverage']['declared'] == 25
    assert report['intentCoverage']['counts'] == {
        'PASS': 25, 'FAIL': 0, 'NOT_TESTED': 0, 'UNVERIFIED': 0,
    }
    assert report['status'] == 'PASS_ISOLATED_FUNCTIONAL'
    assert report['cutover']['status'] == 'BLOCKED'
    assert report['cutover']['canonicalWorkerRouteChanged'] is False
    assert all(gate['status'] == 'NOT_TESTED' for gate in report['liveGates'])
