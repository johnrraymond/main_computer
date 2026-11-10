"""Negative-controlled SLH UI oracle: source evidence and faithful observations."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import mcel_slh_oracle as oracle


def local_payload(*, enabled=False, initialized=True, org='disabled'):
    return {
        'schema':'main-computer.slh-local-status.v1',
        'local_service':{'state':'running','binding':'loopback','host':'127.0.0.1','port':8897},
        'initialization':{'initialized':initialized,'setup_phase':'INITIALIZED' if initialized else 'UNCONFIGURED'},
        'organization_service':{'state':org,'enabled':enabled,'listening':False,
                                'workers_enabled':False,'seh_enabled':True},
        'secret_should_not_leak':'never',
    }


def scheduler_payload(*, status='NOT_OBSERVED', live=False, snapshot=None):
    return {
        'policy':{'schema':'main-computer.slh-scheduler-policy.v1','revision':7,
                  'policy':{'mode':'private_first','min_organization_percent':20.0,
                            'max_public_percent':80.0,'min_private_idle_percent':0.0}},
        'runtime':{'status':status,'live':live,'snapshot':snapshot},
        'credit_meter':'NOT_CONNECTED',
    }


def seh_payload(connected=False):
    return {'status': 'CONNECTED_VERIFIED' if connected else 'NOT_CONNECTED',
            'outbound_tunnel_authenticated':connected,'organization_enabled':True,
            'seh_enabled':True,'workers_enabled':True,'resident_child_observed':connected,
            'private_sensitive_field':'never'}


def test_source_contract_and_stubs_are_real():
    report = oracle.verify_source()
    assert report['status'] == 'PASS'
    assert report['contracts']['GET /v1/workers'] == 'MATCH'
    assert report['contracts']['GET /v1/org/admin/marketplace/status'] == 'MATCH'
    assert report['policy_default']['min_organization_percent'] == 20
    assert len(report['source_sha256']) == len(oracle.SOURCE_FILES)


def test_oracle_refuses_mutation_authority(tmp_path):
    source = oracle.SPEC.read_text(encoding='utf-8')
    original = json.loads(source)
    original['presentation_contract']['mutations_enabled'] = True
    (tmp_path / 'tests/fixtures/mcel_oracles/slh').mkdir(parents=True)
    (tmp_path / 'tests/fixtures/mcel_oracles/slh/oracle.v1.json').write_text(json.dumps(original))
    with pytest.raises(oracle.OracleError, match='mutations-forbidden'):
        oracle.verify_source(tmp_path)


def test_no_local_service_is_incomplete_not_disabled_or_zero():
    def absent(*args, **kwargs):
        raise ConnectionRefusedError('offline')
    observed = oracle.observe(get=absent)
    assert observed['status'] == 'INCOMPLETE_NO_LOCAL_SERVICE'
    assert observed['observations']['organization_service']['state'] == 'NOT_OBSERVED'
    assert observed['observations']['live_worker_count']['value'] is None


def test_local_public_observation_truthful_and_sanitized():
    calls = []
    def get(path, **kwargs):
        calls.append(path)
        return 200, local_payload()
    result = oracle.observe(get=get)
    assert calls == ['/v1/local/status']
    assert result['status'] == 'PASS_PUBLIC_OBSERVATION'
    obs = result['observations']
    assert obs['local_service']['state'] == 'RUNNING'
    assert obs['organization_service']['state'] == 'DISABLED'
    assert obs['organization_service']['value']['seh_enabled'] is True
    assert obs['seh_connection']['state'] == 'NOT_AUTHORIZED'
    assert 'never' not in json.dumps(result)
    assert obs['worker_runtime']['value'] is None


def test_running_service_not_equal_running_organization():
    obs = oracle._local_projection(local_payload(enabled=True, org='stopped_unexpectedly'))
    assert obs['local_service']['state'] == 'RUNNING'
    assert obs['organization_service']['state'] == 'STOPPED_UNEXPECTEDLY'


def test_invalid_public_status_fails_closed():
    wrong = local_payload()
    wrong['local_service']['binding'] = 'public'
    with pytest.raises(oracle.OracleError, match='local-status-boundary-invalid'):
        oracle._local_projection(wrong)


def test_no_admin_secret_means_no_privileged_network_requests():
    def get(path, **kwargs):
        assert path == '/v1/local/status'
        return 200, local_payload()
    result = oracle.observe(get=get, admin_token=None)
    assert result['observations']['scheduler_policy']['state'] == 'NOT_AUTHORIZED'


def test_authenticated_observations_preserve_staleness_and_never_claim_worker_count():
    def get(path, **kwargs):
        if path == '/v1/local/status':
            return 200, local_payload()
        if path == '/v1/admin/scheduler/status':
            assert kwargs['token'] == 'secret-session'
            return 200, scheduler_payload(status='STALE', live=False,
                           snapshot={'live_public_sessions': 14, 'private_idle_ready': 0})
        if path == '/v1/local/seh/status':
            return 200, seh_payload()
        raise AssertionError('Unexpected request ' + path)
    result = oracle.observe(get=get, admin_token='secret-session')
    assert result['status'] == 'PASS_ADMIN_OBSERVATION'
    assert result['observations']['worker_runtime']['state'] == 'STALE'
    assert result['observations']['worker_runtime']['value'] is None
    assert result['observations']['live_worker_count']['value'] is None
    assert result['observations']['credit_meter']['state'] == 'NOT_CONNECTED'
    assert result['observations']['seh_connection']['state'] == 'NOT_CONNECTED'
    assert 'secret-session' not in json.dumps(result)


def test_fresh_scheduler_runtime_is_snapshot_not_complete_census():
    payload = scheduler_payload(status='OBSERVED', live=True,
        snapshot={'private_idle_ready':2, 'live_public_sessions':3,
                  'desired_public_workers':['private_device_id_must_not_leak']})
    projected = oracle._scheduler_projection(payload)
    assert projected['worker_runtime']['state'] == 'OBSERVED'
    assert projected['worker_runtime']['value']['private_idle_ready'] == 2
    assert 'desired_public_workers' not in projected['worker_runtime']['value']
    assert projected['live_worker_count']['state'] == 'NOT_OBSERVED'


def test_runtime_claiming_observed_but_not_live_is_invalid():
    with pytest.raises(oracle.OracleError, match='freshness-contradiction'):
        oracle._scheduler_projection(scheduler_payload(status='OBSERVED', live=False, snapshot={}))


def test_bad_scheduler_policy_is_rejected():
    payload = scheduler_payload()
    payload['policy']['policy']['min_organization_percent'] = -5
    with pytest.raises(oracle.OracleError, match='scheduler-policy-percent-invalid'):
        oracle._scheduler_projection(payload)


def test_privileged_401_is_not_zero_workers():
    def get(path, **kwargs):
        return (200, local_payload()) if path == '/v1/local/status' else (401,{})
    result = oracle.observe(get=get, admin_token='session')
    assert result['status'] == 'INCOMPLETE_ADMIN_OBSERVATION'
    assert result['observations']['worker_runtime']['state'] == 'NOT_AUTHORIZED'
    assert result['observations']['live_worker_count']['value'] is None


def test_seh_enabled_is_not_connected():
    status = oracle._seh_projection(seh_payload(False))
    assert status['state'] == 'NOT_CONNECTED'
    with pytest.raises(oracle.OracleError, match='seh-verified-without-connection-proof'):
        oracle._seh_projection({**seh_payload(True), 'outbound_tunnel_authenticated':False})


def test_contract_report_is_read_only_and_repeatable(tmp_path):
    out = tmp_path/'report.json'
    first = oracle.run(mode='contract', output=out)
    second = oracle.run(mode='contract', output=out)
    assert first == second
    assert first['status'] == 'PASS_CONTRACT'
    assert first['production_promoted'] is False
    assert json.loads(out.read_text()) == first


def test_observer_only_uses_get_and_loopback():
    with patch.object(oracle, 'build_opener') as opener:
        response = opener.return_value.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b'{"hello": "world"}'
        code, payload = oracle._local_get('/v1/local/status', port=8897)
        req = opener.return_value.open.call_args.args[0]
        assert req.get_method() == 'GET'
        assert req.full_url == 'http://127.0.0.1:8897/v1/local/status'
        assert (code, payload) == (200, {'hello':'world'})


def test_oracle_cannot_silently_delete_source_requirements(tmp_path):
    source = json.loads(oracle.SPEC.read_text(encoding='utf-8'))
    source['source_contracts'] = [item for item in source['source_contracts']
                                  if item['path'] != '/v1/workers']
    (tmp_path / 'tests/fixtures/mcel_oracles/slh').mkdir(parents=True)
    (tmp_path / 'tests/fixtures/mcel_oracles/slh/oracle.v1.json').write_text(json.dumps(source))
    with pytest.raises(oracle.OracleError, match='required-contracts-changed'):
        oracle.verify_source(tmp_path)


def test_oracle_does_not_invalidate_existing_application_catalog():
    from main_computer.mcel_application_packages import build_application_package_catalog
    catalog = build_application_package_catalog(oracle.ROOT)
    assert catalog.ok and catalog.invalid_count == 0


def test_live_observation_incomplete_is_not_a_success_exit(tmp_path, capsys):
    with patch.object(oracle, 'run', return_value={
        'status': 'INCOMPLETE_NO_LOCAL_SERVICE',
        'observed': {'observations': {
            'local_service': {'state': 'UNAVAILABLE'},
        }}
    }):
        assert oracle.main(['--mode', 'observe', '--output', str(tmp_path/'report.json')]) == 2
    assert 'INCOMPLETE_NO_LOCAL_SERVICE' in capsys.readouterr().out


def test_slh_navigation_contract_before_synthesis():
    report = oracle.verify_source()
    assert report['navigation']['stage'] == 'planned-not-hosted'
    assert report['navigation']['destinations'] == ['slh.overview', 'slh.worker-bank', 'slh.worker']


@pytest.mark.parametrize('field,value,error', [
    ('href', '/applications/slh/workers?mcel-shell-child=1', 'destination-invalid'),
    ('openModes', ['same-tab'], 'destination-invalid'),
    ('href', '/applications/slh/workers-copy', 'address-drift'),
])
def test_slh_oracle_denies_unopenable_or_misrouted_destination(tmp_path, field, value, error):
    spec = json.loads(oracle.SPEC.read_text(encoding='utf-8'))
    spec['navigation_contract']['destinations'][1][field] = value
    (tmp_path / 'tests/fixtures/mcel_oracles/slh').mkdir(parents=True)
    (tmp_path / 'tests/fixtures/mcel_oracles/slh/oracle.v1.json').write_text(json.dumps(spec), encoding='utf-8')
    with pytest.raises(oracle.OracleError, match=error):
        oracle.verify_source(tmp_path)
