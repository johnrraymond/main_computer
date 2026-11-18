"""Two operator SEH actions: no hidden remote operations in preview, no duplicate CREATE.

The Coolify and resident boundaries are mocked. No live networking or service
state is touched. Existing lower-level SEH tests cover cryptographic readbacks.
"""
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

import seh_mutate_harness as harness
from tools.seh_control import git_deployment as git, deployment_state as ds, identity as identity_state
from main_computer.slh import organization_state

OLD = 'rzwldrq1npiqwrztpfnkbj5q'
NEW = 'hz1gjzytcimhh6phamgcjxxi'


def args(*flags):
    return harness._parser().parse_args(['deploy-seh', '--network', 'mainnet', *flags])


def undeploy_args(*flags):
    return harness._parser().parse_args(['undeploy-seh', '--network', 'mainnet', *flags])


def make_deploy(monkeypatch, *, deleted=True, enabled=False, receipt=True, slh_enabled=False):
    control = SimpleNamespace(root=Path('/tmp/isolated-seh-harness'))
    current = {'application_uuid': OLD if deleted else NEW, 'enabled': enabled}
    if deleted:
        current['undeployed'] = True
    monkeypatch.setattr(identity_state, 'read', lambda *a, **kw: (None, None, {}))
    monkeypatch.setattr(identity_state, 'deployment_record', lambda *a, **kw: {'deployment': {'application_uuid': OLD}})
    monkeypatch.setattr(ds, 'selected', lambda *a: {'deployment': current})
    monkeypatch.setattr(ds, 'identity_binding', lambda *a: 'known-binding')
    monkeypatch.setattr(git, '_undeploy_receipt', lambda *a: {
        'phase': 'VERIFIED', 'identity_binding': 'known-binding'} if receipt else None)
    monkeypatch.setattr(organization_state, 'read_config',
                        lambda *a: {'seh_enabled': slh_enabled})
    return control, current


def test_deploy_plan_never_creates_or_activates(monkeypatch):
    control, _ = make_deploy(monkeypatch)
    calls = []
    monkeypatch.setattr(git, 'restore_deleted_canonical_application',
                        lambda *a, **kw: calls.append(kw['execute']) or
                        {'status': 'PLAN_ONLY', 'action': 'RECREATE_DELETED_APPLICATION'})
    monkeypatch.setattr(harness, '_activate_via_resident_slh',
                        lambda *a, **kw: pytest.fail('dry run attempted activation'))
    result = harness.run(args(), control=control)
    assert result['status'] == 'PLAN_ONLY' and calls == [False]


def test_deploy_creates_once_waits_for_build_and_activates(monkeypatch):
    control, current = make_deploy(monkeypatch)
    calls = []

    def create(*a, **kw):
        calls.append('create')
        assert kw['execute'] is True
        current.update(application_uuid=NEW)
        current.pop('undeployed')
        return {'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED',
                'application_uuid': NEW, 'remote_mutation': True}

    def poll(*a, **kw):
        calls.append('build')
        assert current['application_uuid'] == NEW
        return {'status': 'REPLACEMENT_BUILD_VERIFIED', 'application_uuid': NEW}

    def public(*a, **kw):
        calls.append('runtime')

    def activation(a, **kw):
        calls.append('activate' if a.execute_mutations else 'preflight-activate')
        assert a.slh_state_dir is None
        return ({'status': 'CONNECTED_VERIFIED', 'outbound_tunnel_authenticated': True,
                 'remote_mutation': False} if a.execute_mutations
                else {'status': 'PLAN_ONLY'})

    monkeypatch.setattr(git, 'restore_deleted_canonical_application', create)
    monkeypatch.setattr(harness, '_wait_for_verified_build', poll)
    monkeypatch.setattr(harness, '_wait_for_runtime', public)
    monkeypatch.setattr(harness, '_activate_via_resident_slh', activation)
    result = harness.run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert result['status'] == 'CONNECTED_VERIFIED'
    assert result['application_uuid'] == NEW
    assert result['deployment_remote_mutation'] is True
    assert calls == ['create', 'build', 'runtime', 'preflight-activate', 'activate']


def test_deploy_resumes_registered_recovery_without_second_create(monkeypatch):
    control, current = make_deploy(monkeypatch, deleted=False)
    seen = []
    monkeypatch.setattr(git, 'restore_deleted_canonical_application', lambda *a, **k:
                        pytest.fail('registered application must not be recreated'))
    monkeypatch.setattr(git, 'reconcile_pending_recovery', lambda *a, **kw:
                        seen.append(kw['execute']) or
                        {'status': 'REPLACEMENT_BUILD_VERIFIED', 'application_uuid': NEW})
    monkeypatch.setattr(harness, '_wait_for_runtime', lambda *a, **kw: None)
    monkeypatch.setattr(harness, '_activate_via_resident_slh', lambda a, **kw:
                        {'status': 'CONNECTED_VERIFIED'} if a.execute_mutations else {'status': 'PLAN_ONLY'})
    result = harness.run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert result['status'] == 'CONNECTED_VERIFIED'
    assert result['application_uuid'] == NEW
    assert seen == [False]


def test_deploy_reconciles_receipt_missing_build_id(monkeypatch):
    control, _ = make_deploy(monkeypatch, deleted=False)
    seen = []

    def reconcile(*a, **kw):
        seen.append(kw['execute'])
        return ({'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED',
                 'application_uuid': NEW, 'remote_mutation': True}
                if kw['execute'] else {'status': 'PLAN_ONLY', 'action': 'RECONCILE_RECOVERY_BUILD'})

    monkeypatch.setattr(git, 'reconcile_pending_recovery', reconcile)
    monkeypatch.setattr(harness, '_wait_for_verified_build', lambda *a, **kw:
                        {'status': 'REPLACEMENT_BUILD_VERIFIED', 'application_uuid': NEW})
    monkeypatch.setattr(harness, '_wait_for_runtime', lambda *a, **kw: None)
    monkeypatch.setattr(harness, '_activate_via_resident_slh', lambda a, **kw:
                        {'status': 'CONNECTED_VERIFIED'} if a.execute_mutations else {'status': 'PLAN_ONLY'})
    result = harness.run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert result['status'] == 'CONNECTED_VERIFIED'
    assert seen == [False, True]


def test_unverified_build_cannot_activate_or_recreate(monkeypatch):
    control, _ = make_deploy(monkeypatch, deleted=False)
    monkeypatch.setattr(git, 'reconcile_pending_recovery', lambda *a, **k:
                        {'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED', 'application_uuid': NEW})
    monkeypatch.setattr(harness, '_wait_for_verified_build', lambda *a, **kw:
                        {'status': 'INCOMPLETE', 'failed_stage': 'recovery_build_wait_timeout'})
    monkeypatch.setattr(harness, '_activate_via_resident_slh', lambda *a, **kw:
                        pytest.fail('build unverified must not activate'))
    result = harness.run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert result['status'] == 'INCOMPLETE'


def test_deploy_refuses_unverified_prior_removal(monkeypatch):
    control, _ = make_deploy(monkeypatch, receipt=False)
    with pytest.raises(harness.HarnessError, match='Verified intentional undeploy receipt'):
        harness.run(args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)


def test_missing_one_approval_refuses(monkeypatch):
    control, _ = make_deploy(monkeypatch)
    with pytest.raises(harness.HarnessError, match='Both mutation approvals'):
        harness.run(args('--execute-mutations'), control=control)


def test_undeploy_plan_only_never_disconnects(monkeypatch):
    control = SimpleNamespace(root=Path('/tmp/isolated-seh-harness'))
    monkeypatch.setattr(git, 'undeploy_canonical_runtime', lambda *a, **kw:
                        {'status': 'PLAN_ONLY', 'action': 'DELETE_CANONICAL_SEH_APPLICATION',
                         'outbound_disconnected_verified': False, 'ready_for_undeploy': False})
    monkeypatch.setattr(harness, '_disconnect_via_resident_slh', lambda *a, **kw:
                        pytest.fail('preview may not disconnect'))
    result = harness.run(undeploy_args(), control=control)
    assert result['status'] == 'PLAN_ONLY'
    assert result['disconnect_required'] is True
    assert result['action'] == 'DISCONNECT_VERIFY_AND_DELETE_CANONICAL_SEH'


def test_undeploy_disconnects_and_waits_before_deleting(monkeypatch):
    control = SimpleNamespace(root=Path('/tmp/isolated-seh-harness'))
    cfg = {'seh_enabled': True}
    calls = []
    monkeypatch.setattr(organization_state, 'read_config', lambda *a: cfg)

    def stage(*a, **kw):
        if not kw['execute']:
            calls.append('preflight')
            ready = cfg['seh_enabled'] is False
            return {'status': 'PLAN_ONLY', 'action': 'DELETE_CANONICAL_SEH_APPLICATION',
                    'outbound_disconnected_verified': ready, 'ready_for_undeploy': ready}
        calls.append('delete')
        assert cfg['seh_enabled'] is False
        return {'status': 'SEH_UNDEPLOYED', 'application_removed_verified': True}

    def disconnect(*a, **kw):
        calls.append('disconnect')
        cfg['seh_enabled'] = False
        return {'status': 'DISCONNECTION_REQUESTED', 'connection_enabled': False}

    monkeypatch.setattr(git, 'undeploy_canonical_runtime', stage)
    monkeypatch.setattr(harness, '_disconnect_via_resident_slh', disconnect)
    result = harness.run(undeploy_args('--execute-mutations', '--yes-i-know-this-mutates-seh'), control=control)
    assert result['status'] == 'SEH_UNDEPLOYED'
    assert calls == ['preflight', 'disconnect', 'preflight', 'delete']


def test_undeploy_never_deletes_if_disconnect_unverified(monkeypatch):
    control = SimpleNamespace(root=Path('/tmp/isolated-seh-harness'))
    monkeypatch.setattr(organization_state, 'read_config', lambda *a: {'seh_enabled': False})
    monkeypatch.setattr(git, 'undeploy_canonical_runtime', lambda *a, **kw:
                        ({'status': 'PLAN_ONLY', 'action': 'DELETE_CANONICAL_SEH_APPLICATION',
                          'outbound_disconnected_verified': False} if not kw['execute']
                         else pytest.fail('delete must remain blocked')))
    monkeypatch.setattr(harness, '_disconnect_via_resident_slh', lambda *a, **kw:
                        pytest.fail('already-disabled config must not be written'))
    import time
    monkeypatch.setattr(time, 'sleep', lambda n: None)
    original = time.monotonic
    counter = iter([0, 10, 20, 30, 50, 60])
    monkeypatch.setattr(time, 'monotonic', lambda: next(counter))
    result = harness.run(undeploy_args('--activation-timeout', '1', '--execute-mutations',
                                       '--yes-i-know-this-mutates-seh'), control=control)
    assert result['status'] == 'INCOMPLETE'
    assert result['failed_stage'] == 'outbound_disconnection_unverified'


def test_undeploy_idempotent_verified_tombstone(monkeypatch):
    control = SimpleNamespace(root=Path('/tmp/isolated-seh-harness'))
    seen = []
    def result(*a, **kw):
        seen.append(kw['execute'])
        return ({'status': 'ALREADY_UNDEPLOYED', 'application_removed_verified': True}
                if kw['execute'] else {'status': 'PLAN_ONLY',
                                      'action': 'VERIFY_PRIOR_UNDEPLOY'})
    monkeypatch.setattr(git, 'undeploy_canonical_runtime', result)
    monkeypatch.setattr(harness, '_disconnect_via_resident_slh', lambda *a, **kw:
                        pytest.fail('already removed must not stop anything'))
    answer = harness.run(undeploy_args('--execute-mutations',
                                      '--yes-i-know-this-mutates-seh'), control=control)
    assert answer['status'] == 'ALREADY_UNDEPLOYED'
    assert seen == [False, True]
