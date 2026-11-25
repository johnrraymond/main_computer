"""Regression for the first Worker-to-SLH/SEH approval integration gate."""

import pytest

from tools.mcel_worker_identity_live_acceptance import ProbeFailure, verify_enrollment_entry


def test_no_request_authority_is_a_blocker():
    ir = {'application': {'appId': 'worker'}, 'intents': [], 'capabilities': [], 'effects': [], 'surfaces': []}
    result = {'checks': {}}
    with pytest.raises(ProbeFailure, match='^worker-enrollment-request-not-implemented$'):
        verify_enrollment_entry(ir, result)
    assert result['stage'] == 'worker-enrollment-request'
    assert result['checks'] == {'workerEnrollmentCapabilityDeclared': False}


def declared_request():
    return {
        'application': {'appId': 'worker'},
        'capabilities': [{'id': 'capability:worker-enrollment', 'operations': [
            {'name': 'request', 'runtimeMethod': 'requestWorkerEnrollment', 'risk': 'consequential'}]}],
        'intents': [{'id': 'intent:request-enrollment', 'operationKind': 'capability',
                     'runtimeMethod': 'requestWorkerEnrollment', 'operation': 'request',
                     'risk': 'consequential', 'effectRefs': [{'ref': 'effect:request-enrollment.request'}]}],
        'effects': [{'id': 'effect:request-enrollment.request', 'effectKind': 'capability-request',
                     'owner': {'ref': 'intent:request-enrollment'},
                     'target': {'ref': 'capability:worker-enrollment'}}],
        'surfaces': [{'nodes': [{'nodeKind': 'control',
                                 'intent': {'ref': 'intent:request-enrollment'}}]}],
    }


def test_declaration_cannot_certify_execution():
    result = {'checks': {}}
    assert verify_enrollment_entry(declared_request(), result) == {'intent:request-enrollment'}
    assert all(result['checks'].values())
    assert result.get('status') != 'PASS'


@pytest.mark.parametrize('missing', ['effects', 'capabilities', 'intents'])
def test_dangling_declaration_cannot_pass(missing):
    ir = declared_request()
    ir[missing] = []
    with pytest.raises(ProbeFailure, match='^worker-enrollment-request-not-implemented$'):
        verify_enrollment_entry(ir, {'checks': {}})


def test_unbound_control_is_a_distinct_failure():
    ir = declared_request()
    ir['surfaces'] = []
    with pytest.raises(ProbeFailure, match='^worker-enrollment-control-not-bound$'):
        verify_enrollment_entry(ir, {'checks': {}})
