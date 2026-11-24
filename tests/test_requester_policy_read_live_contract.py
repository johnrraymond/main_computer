"""The live gate must accept an absent persisted policy but never invent spending permission."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'tools' / 'worker_browser_acceptance' / 'requester_policy_read.mjs'
HARNESS = ROOT / 'tools' / 'worker_browser_acceptance' / 'mcel_live_acceptance.mjs'
RUNNER = ROOT / 'tools' / 'mcel_worker_live_functional_acceptance.py'


def test_live_policy_response_variants_are_fail_closed_and_secret_free():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js needed to execute native harness response classification')
    cases = [
        [200, {'ok': True, 'settings': {}}, True, 'unsaved-defaults'],
        [200, {'ok': True, 'settings': {'remoteEnabled': False, 'remoteDailyLimit': 0}}, True, 'saved-policy'],
        [200, {'ok': True, 'settings': {'remoteEnabled': True, 'remoteDailyLimit': 500}}, True, 'saved-policy'],
        [200, {'ok': True, 'settings': {'remoteEnabled': True}}, True, 'partial-defaults'],
        [200, {'ok': True, 'settings': {'remoteDailyLimit': 0}}, True, 'partial-defaults'],
        [200, {'ok': True, 'settings': {'remoteEnabled': 'true'}}, False, 'invalid-enabled-type'],
        [200, {'ok': True, 'settings': {'remoteDailyLimit': '1000'}}, False, 'invalid-budget-type'],
        [200, {'ok': True, 'settings': {'remoteDailyLimit': -1}}, False, 'invalid-budget-type'],
        [200, {'ok': True, 'settings': {'remoteDailyLimit': 2**53}}, False, 'invalid-budget-type'],
        [200, {'ok': True, 'settings': []}, False, 'invalid-settings-object'],
        [200, {'ok': True}, False, 'invalid-settings-object'],
        [200, {'ok': False, 'settings': {}}, False, 'backend-refused'],
        [403, {'ok': False, 'error': 'sensitive-route-data'}, False, 'http-rejected'],
        [0, None, False, 'transport-failed'],
    ]
    script = '''
      const {classifyRequesterPolicyRead} = await import(process.argv[1]);
      const cases = JSON.parse(process.argv[2]);
      const observed = cases.map(([http, body]) => classifyRequesterPolicyRead({http, body}));
      console.log(JSON.stringify(observed));
    '''
    result = subprocess.run([node, '--input-type=module', '-e', script,
                             MODULE.resolve().as_uri(), json.dumps(cases)],
                            capture_output=True, text=True, timeout=20, check=True)
    observed = json.loads(result.stdout)
    assert len(observed) == len(cases)
    for item, (_, _, accepted, outcome) in zip(observed, cases):
        assert item['ok'] is accepted
        assert item['diagnostic'] == {
            'version': 'requester-policy-read-v1',
            'httpStatus': item['diagnostic']['httpStatus'],
            'result': outcome,
        }
        assert isinstance(item['diagnostic']['httpStatus'], int)
        assert len(item['diagnostic']) == 3
    assert 'sensitive-route-data' not in result.stdout
    assert '500' not in result.stdout


def test_harness_proves_backend_read_and_never_writes_settings():
    source = HARNESS.read_text(encoding='utf-8')
    start = source.index('async function testRequester(page)')
    end = source.index('async function testMarketplace(', start)
    section = source[start:end]
    assert 'classifyRequesterPolicyRead(read)' in section
    assert "mark('requesterPolicyRead')" in section
    assert "proof.stage='requesterGuardVerify'" in section
    assert "'/api/applications/worker/settings'" in section
    assert "'/api/applications/chat-console/ai/remote-overflow/requester-verify'" in section
    assert "method:'POST'" in section
    assert 'changed_fields' not in section
    assert 'Save Requester Limits' not in section
    assert 'budgetLimitDenied' in section
    assert 'noPaidSubmission' in section


def test_live_runner_reports_only_allowlisted_requester_policy_diagnostics():
    source = RUNNER.read_text(encoding='utf-8')
    assert "'WORKER_LIVE_REQUESTER_' + kind" in source
    assert "'unclassified'" in source
