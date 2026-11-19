"""Safety/effect tests for the real-mutation acceptance runner.

No test in this file connects to a production SLH or Coolify. Real HTTP
mutations run only against a disposable authority. SEH mutation orchestration
is fake-invoked here; fake receipts NEVER certify a deployed service.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import mcel_slh_dangerous_functional_acceptance as dangerous
from main_computer.slh.authority import SLHAuthority


UUID = 'canonical-uuid-0001'
NEW = 'replacement-uuid-0002'


def test_real_http_mutation_disposable_including_admin_and_local_authenticator(tmp_path):
    report = tmp_path / 'report.json'
    code = dangerous.main(['--mode', 'isolated', '--report', str(report)])
    assert code == 0
    data = json.loads(report.read_text())
    assert data['status'] == 'PASS_ISOLATED_IDENTITY'
    assert data['groups']['seh']['status'] == 'NOT_RUN'
    assert data['groups']['canonical-local-authenticator']['status'] == 'PASS'
    identities = data['groups']['identities']
    assert identities['status'] == 'PASS'
    assert identities['cleanup'] == 'VERIFIED_REMOVED'
    assert len(identities['created']) == 5
    assert {'worker_enroll_revoke_reactivate_session_invalidation',
            'requester_enroll_and_authorization',
            'admin_enroll_elevation_and_delegated_approval',
            'enrollment_denial', 'durable_audit'} <= set(identities['proved'])
    # A denial is not a registered/removed device; all four approved records are removed.
    assert sum(bool(x['removed']) for x in identities['created']) == 4
    assert data['safety']['nonowned_identity_mutation'] == 'FORBIDDEN'


def test_missing_live_approvals_refuse_before_touching_any_service(tmp_path):
    with pytest.raises(SystemExit) as err:
        dangerous.main(['--mode', 'live', '--scope', 'identities',
                        '--admin-device', 'existing-admin', '--report', str(tmp_path/'x.json')])
    assert err.value.code == 2
    assert not (tmp_path/'x.json').exists()
    with pytest.raises(SystemExit) as err:
        dangerous.main(['--mode', 'live', '--scope', 'seh', '--network', 'mainnet',
                        '--execute-mutations', '--acknowledge-real-mutations'])
    assert err.value.code == 2
    with pytest.raises(SystemExit) as err:
        dangerous.main(['--mode', 'preflight', '--scope', 'seh', '--cycle-active-seh'])
    assert err.value.code == 2


def fake_seh(initial_undeployed=True, bad_deploy=False):
    state = {'uuid': UUID, 'undeployed': initial_undeployed}
    operations = []
    def invoke(args):
        operations.append(args.operation)
        if args.operation == 'inspect':
            return {'status': 'READ_ONLY', 'network': args.network, 'registered': True,
                    'deployment': {'application_uuid': state['uuid'],
                                   'undeployed': state['undeployed']}}
        # All mutation commands must retain the two existing harness approvals.
        assert args.execute_mutations and args.yes_i_know_this_mutates_seh
        if args.operation == 'undeploy-seh':
            if state['undeployed']:
                raise AssertionError('undeploy called when already undeployed')
            state['undeployed'] = True
            return {'status': 'SEH_UNDEPLOYED', 'application_removed_verified': True,
                    'application_uuid': state['uuid']}
        if args.operation == 'deploy-seh':
            if bad_deploy:
                return {'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED',
                        'application_uuid': NEW, 'deployment_remote_mutation': True}
            assert state['undeployed']
            state['uuid'] = NEW
            state['undeployed'] = False
            return {'status': 'CONNECTED_VERIFIED', 'application_uuid': NEW,
                    'deployment_remote_mutation': True}
        raise AssertionError('unexpected operation: ' + args.operation)
    return invoke, operations, state


def test_seh_cycle_deploy_then_undeploy_only_when_initially_tombstoned(tmp_path):
    invoke, ops, state = fake_seh(initial_undeployed=True)
    result = dangerous.seh_cycle(expected_uuid=UUID, state_root=tmp_path,
                                 invoke=invoke, authorize_mutations=True)
    assert result['status'] == 'PASS'
    assert result['phases'] == ['deploy_and_activate', 'undeploy_and_verify_tombstone']
    assert ops == ['inspect', 'deploy-seh', 'inspect', 'undeploy-seh', 'inspect']
    assert state == {'uuid': NEW, 'undeployed': True}


def test_preexisting_active_seh_requires_explicit_outage_flag_and_uuid_pin(tmp_path):
    invoke, ops, state = fake_seh(initial_undeployed=False)
    with pytest.raises(dangerous.AcceptanceError, match='ACTIVE_SEH_CYCLE_REQUIRES_EXPLICIT'):
        dangerous.seh_cycle(expected_uuid=UUID, state_root=tmp_path, invoke=invoke, authorize_mutations=True)
    assert ops == ['inspect']
    invoke2, ops2, _ = fake_seh(initial_undeployed=True)
    with pytest.raises(dangerous.AcceptanceError, match='SEH_UUID_MISMATCH'):
        dangerous.seh_cycle(expected_uuid=NEW, state_root=tmp_path, invoke=invoke2, authorize_mutations=True)
    assert ops2 == ['inspect']


def test_preexisting_active_seh_cycle_restores_deployed_state(tmp_path):
    invoke, ops, state = fake_seh(initial_undeployed=False)
    result = dangerous.seh_cycle(expected_uuid=UUID, state_root=tmp_path,
                                 cycle_active=True, invoke=invoke, authorize_mutations=True)
    assert result['status'] == 'PASS'
    assert ops == ['inspect', 'undeploy-seh', 'inspect', 'deploy-seh', 'inspect']
    assert state == {'uuid': NEW, 'undeployed': False}
    assert result['initial_uuid'] != result['final_uuid']


def test_unverified_remote_deploy_never_triggers_blind_undeploy(tmp_path):
    invoke, ops, state = fake_seh(initial_undeployed=True, bad_deploy=True)
    result = dangerous.seh_cycle(expected_uuid=UUID, state_root=tmp_path, invoke=invoke, authorize_mutations=True)
    assert result['status'] == 'INCOMPLETE'
    assert result['automatic_remote_cleanup'] == 'REFUSED_ON_UNVERIFIED_STATE'
    assert ops == ['inspect', 'deploy-seh', 'inspect']
    assert state['undeployed'] is True  # Fake leaves unknown deployment unaccepted.


def test_refuse_invalid_uuid_before_touching_target(tmp_path):
    invoke, ops, _ = fake_seh()
    with pytest.raises(dangerous.AcceptanceError, match='EXPECTED_UUID_REQUIRED'):
        dangerous.seh_cycle(expected_uuid='bad', state_root=tmp_path, invoke=invoke, authorize_mutations=True)
    assert ops == []


def test_physical_inspection_policy_prevents_automatic_admin_approval(tmp_path):
    from main_computer.slh.api import SLHAPI
    from tools.slh_authenticator_cli import save_identity, bootstrap_admin
    from tools.slh_seh_connector import service_identity
    from wsgiref.simple_server import make_server
    import threading
    state = tmp_path/'authority'
    identities = tmp_path/'identities'
    save_identity(identities, 'root-admin')
    service_identity(state, create=True)
    authority = SLHAuthority(state)
    server = make_server('127.0.0.1', 0, SLHAPI(authority).wsgi_app,
                         handler_class=dangerous._QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        bootstrap_admin(port=server.server_port, state_dir=state,
                        identity_dir=identities, device_id='root-admin',
                        admin_id='owner', admin_name='Owner')
        authority.policy(True, 'owner')
        result = dangerous.identity_cycle(port=server.server_port,
                                          admin_identity_dir=identities, admin_device='root-admin',
                                          authorize_mutations=True)
        assert result['status'] == 'INCOMPLETE'
        assert result['failed_gate'] == 'PHYSICAL_INSPECTION_REQUIRES_HUMAN_ATTESTATION'
        assert len(authority.devices()) == 1
        assert len(authority.queue()) == 0
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_direct_seh_helper_does_not_mutate_without_explicit_approval(tmp_path):
    invoke, ops, _ = fake_seh()
    with pytest.raises(dangerous.AcceptanceError, match='EXPLICIT_SEH_MUTATION_APPROVAL_REQUIRED'):
        dangerous.seh_cycle(expected_uuid=UUID, state_root=tmp_path, invoke=invoke)
    assert ops == []
