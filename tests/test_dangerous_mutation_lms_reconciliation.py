"""Only public read-only LMS evidence; no mutation on reconciliation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import dangerous_mutation as dm


ID = 'mcel-seh-' + 'a'*32
UUID = '9fify95nvix0trm6heouolyu'


def previous_report(path):
    document = {
        'schema': 'mcel.slh.dangerous-mutation.v1',
        'mode': 'live', 'scope': 'seh', 'status': 'INCOMPLETE',
        'groups': {'seh': {'failed_gate': 'PAGE_LMS_OUTCOME_UNVERIFIED_UNDEPLOY',
                           'submitted_operations': [{'operation': 'undeploy', 'operation_id': ID}]}}
    }
    path.write_text(json.dumps(document), encoding='utf-8')
    return document


def test_receipt_summary_rejects_untrusted_strings_and_streams():
    summary = dm.safe_receipt_summary({
        'operation_id': ID, 'phase': 'RECORDED', 'stage': 'HARNESS_RETURNED',
        'result': {'status': 'INCOMPLETE', 'error_code': 'HARNESS_OUTCOME_UNVERIFIED',
                   'message': 'secret: never display'},
        'output': [{'text': 'credentials: never display'}],
        'causal_diagnostic': {'error_code': 'LMS_RESULT_UNCONFIRMED',
                              'apiToken': 'never display'},
    })
    assert summary == {
        'operation_id': ID, 'phase': 'RECORDED', 'stage': 'HARNESS_RETURNED',
        'result_status': 'INCOMPLETE', 'error_code': 'HARNESS_OUTCOME_UNVERIFIED',
        'causal_error_code': 'LMS_RESULT_UNCONFIRMED',
    }
    assert 'never display' not in json.dumps(summary)


def test_reconcile_prior_failed_undeploy_only_reads_lms_and_canonical(tmp_path):
    source = tmp_path / 'prior.json'
    previous_report(source)
    calls = []
    def receipt_lookup(op, args):
        calls.append((op,args))
        return {'operation_id': ID, 'phase': 'RECORDED', 'stage': 'HARNESS_RETURNED',
                'result': {'status':'REFUSED','reason':'too much internal detail'}}
    def canonical(root, port):
        calls.append(('canonical', port))
        return {'deployment': {'application_uuid': UUID, 'undeployed': None},'generation':33}
    def lms():
        calls.append(('lms', None))
        return {'applicationUuid': UUID, 'generation':33,'selectedController':'coolify-c',
                'deploymentStatus':'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED'}
    result = dm.reconcile_recorded_operation(source_report=source, state_root=tmp_path,
                local_port=8897, receipt_lookup=receipt_lookup,inspect=canonical,lms=lms)
    assert result['status'] == 'READ_ONLY_EVIDENCE_CAPTURED'
    assert result['mutation_submitted'] is False
    assert result['lms_receipt']['result_status'] == 'REFUSED'
    assert result['receipt_matches_expected'] is False
    assert result['authoritative_effect_verified'] is False
    assert result['canonical']['uuid'] == UUID
    assert result['lms_deployment']['selected_controller']=='coolify-c'
    assert calls == [('operation-status', {'operationId':ID}),('canonical',8897),('lms',None)]
    assert json.loads(source.read_text())['status'] == 'INCOMPLETE'


def test_reconcile_no_submissions_fails_without_accessing_lms(tmp_path):
    source = tmp_path/'prior.json'
    previous = previous_report(source)
    previous['groups']['seh']['submitted_operations'] = []
    source.write_text(json.dumps(previous), encoding='utf-8')
    result = dm.reconcile_recorded_operation(source_report=source,state_root=tmp_path,
                        local_port=8897,receipt_lookup=lambda *_:pytest.fail('must not call LMS'))
    assert result['status'] == 'READ_ONLY_UNVERIFIED'
    assert result['failed_gate'] == 'NO_SUBMITTED_OPERATION_TO_RECONCILE'


def test_cli_reconcile_preserves_source_and_disallows_mutation_flags(tmp_path, monkeypatch):
    source = tmp_path/'prior.json'
    previous_report(source)
    target = tmp_path/'reconcile.json'
    result = {'status':'READ_ONLY_EVIDENCE_CAPTURED','lms_receipt':{'result_status':'REFUSED'},
              'mutation_submitted':False}
    monkeypatch.setattr(dm,'reconcile_recorded_operation',lambda **kwargs:result)
    assert dm.main(['--mode','reconcile','--scope','seh','--report',str(source),
                    '--reconcile-report',str(target)]) == 0
    assert json.loads(source.read_text())['status'] == 'INCOMPLETE'
    assert json.loads(target.read_text()) == result
    with pytest.raises(SystemExit):
        dm.main(['--mode','reconcile','--scope','seh','--report',str(source),
                 '--execute-mutations'])
    with pytest.raises(SystemExit):
        dm.main(['--mode','reconcile','--scope','seh','--report',str(source),
                 '--reconcile-report',str(source)])
