"""Verified replacement activation: no new Coolify operation or Mother mutation."""
from copy import deepcopy

import pytest

from main_computer.slh.organization_controller import OrganizationController
from main_computer.slh.seh_autoconnect import SEHConnectionSettings
from tools.seh_control import deployment_state as ds, git_deployment as git, identity as st
from tests.test_seh_deleted_application_recovery import NEW, PUBLISHED, DeletedCoolify, setup
from tests.test_slh_seh_governed_activation import Authority


class FinishedReplacement(DeletedCoolify):
    def __init__(self):
        super().__init__()
        self.build_status = 'queued'
        self.runtime_status = 'running:healthy'

    def request(self, method, path, body=None):
        response = super().request(method, path, body)
        if method == 'GET' and path == '/api/v1/deployments/seh-build-01':
            return {**response, 'status': self.build_status}
        if method == 'GET' and path == f'/api/v1/applications/{NEW}':
            return {**response, 'status': self.runtime_status}
        return response


def staged(tmp_path, *, prior_enabled=True):
    root, slh, _ = setup(tmp_path)
    identity_doc = st.read(root, 'mainnet')[2]
    identity = st.deployment_record(identity_doc, 'mainnet')
    if prior_enabled and not identity['deployment'].get('enabled'):
        enabled = deepcopy(identity['deployment'])
        enabled['enabled'] = True
        ds.write(root, 'mainnet', identity,
                 expected_uuid=enabled['application_uuid'], deployment=enabled)
    remote = FinishedReplacement()
    created = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                            state_dir=slh, client=remote, source=PUBLISHED)
    assert created['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['enabled'] is False
    return root, slh, remote


def healthy(_):
    return {'status': 'PUBLIC_RUNTIME_PRESENT', 'http_status': 200, 'slh_connected': False}


def test_replacement_is_staged_disabled_even_if_previous_was_enabled(tmp_path):
    root, slh, remote = staged(tmp_path)
    assert ds.read(root, 'mainnet', st.deployment_record(st.read(root,'mainnet')[2],'mainnet'))['revision'] == 2
    assert git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                execute=False, client=remote)['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'


def test_verified_build_and_runtime_enable_existing_revision_without_remote_mutation(tmp_path):
    root, slh, remote = staged(tmp_path, prior_enabled=False)
    remote.build_status = 'finished'
    original_mother = st.read(root, 'mainnet')[2]
    original_calls = len(remote.calls)
    before = ds.read(root, 'mainnet', st.deployment_record(original_mother,'mainnet'))
    preview = git.enable_verified_recovery(root, 'mainnet', state_dir=slh,
                                           execute=False, client=remote, probe=healthy)
    assert preview['status'] == 'PLAN_ONLY' and preview['action'] == 'ENABLE_VERIFIED_RECOVERY'
    assert ds.read(root, 'mainnet', st.deployment_record(original_mother,'mainnet')) == before
    result = git.enable_verified_recovery(root, 'mainnet', state_dir=slh,
                                          execute=True, client=remote, probe=healthy)
    assert result['status'] == 'RECOVERY_ENABLEMENT_VERIFIED'
    assert result['seh_deployment_revision'] == before['revision'] + 1
    after = ds.read(root,'mainnet',st.deployment_record(original_mother,'mainnet'))
    assert after['deployment']['enabled'] is True
    assert after['deployment']['application_uuid'] == NEW
    assert st.read(root, 'mainnet')[2] == original_mother
    assert not any(method in ('POST','PATCH','DELETE') for method, _ in remote.calls[original_calls:])
    assert git._recovery_checkpoint(slh)['new_uuid'] == NEW


@pytest.mark.parametrize('status, expected_stage', [
    ('queued', 'recovery_build_not_verified'),
    ('failed', 'recovery_build_not_verified'),
])
def test_unfinished_or_failed_build_never_enables(tmp_path, status, expected_stage):
    root, slh, remote = staged(tmp_path)
    remote.build_status = status
    if status == 'failed':
        # A retry must not be started by the enablement preflight.
        remote.runtime_status = 'running:healthy'
    result = git.enable_verified_recovery(root, 'mainnet', state_dir=slh,
                                          execute=False, client=remote, probe=healthy)
    assert result['status'] == 'INCOMPLETE'
    assert result['failed_stage'] == expected_stage
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['enabled'] is False


def test_public_runtime_or_coolify_unhealthy_never_enables(tmp_path):
    root, slh, remote = staged(tmp_path)
    remote.build_status = 'finished'
    result = git.enable_verified_recovery(root, 'mainnet', state_dir=slh,
        execute=True, client=remote,
        probe=lambda _: {'status': 'PUBLIC_RUNTIME_UNAVAILABLE', 'http_status': 503})
    assert result['failed_stage'] == 'replacement_public_runtime_unverified'
    remote.runtime_status = 'exited:unhealthy'
    result = git.enable_verified_recovery(root, 'mainnet', state_dir=slh,
                                          execute=True, client=remote, probe=healthy)
    assert result['failed_stage'] == 'replacement_runtime_not_healthy'
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['enabled'] is False


def test_intentional_undeploy_and_nonreceipt_disabled_stay_blocked(tmp_path):
    root, slh, remote = staged(tmp_path)
    remote.build_status = 'finished'
    identity = st.deployment_record(st.read(root, 'mainnet')[2], 'mainnet')
    record = ds.read(root, 'mainnet', identity)
    tombstone = deepcopy(record['deployment'])
    tombstone['undeployed'] = True
    ds.write(root, 'mainnet', identity, expected_uuid=NEW,
             expected_revision=record['revision'], deployment=tombstone)
    result = git.enable_verified_recovery(root, 'mainnet', state_dir=slh,
                                          execute=True, client=remote, probe=healthy)
    assert result['error_code'] == 'SEH_INTENTIONALLY_UNDEPLOYED'
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['enabled'] is False
    (slh / git._RECOVERY_CHECKPOINT).unlink()
    assert git.enable_verified_recovery(root, 'mainnet', state_dir=slh,
                                         execute=True, client=remote, probe=healthy) is None


def test_receipt_uuid_conflict_never_enables(tmp_path):
    root, slh, remote = staged(tmp_path)
    receipt = git._recovery_checkpoint(slh)
    git._save_recovery_checkpoint(slh, {**receipt, 'new_uuid': 'unrelated-seh-app'})
    assert git.enable_verified_recovery(root, 'mainnet', state_dir=slh,
                                         execute=True, client=remote, probe=healthy) is None
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['enabled'] is False


def test_existing_controller_activation_preview_then_execute(tmp_path, monkeypatch):
    root, slh, remote = staged(tmp_path)
    remote.build_status = 'finished'
    monkeypatch.setattr('main_computer.slh.storage.machine_root', lambda: root)
    monkeypatch.setattr(git, '_client', lambda controller: remote)
    monkeypatch.setattr(git, '_public_runtime_probe', healthy)
    monkeypatch.setattr('main_computer.slh.seh_canonical_connection.resolve_canonical_connection',
                        lambda *args, **kw: SEHConnectionSettings(
                            True, 'wss://external.greatlibrary.io', False, 2.0))
    controller = OrganizationController(Authority(slh), automatic_start=False)
    try:
        preview = controller.activate_seh(execute=False)
        assert preview['status'] == 'PLAN_ONLY'
        assert git._canonical_selection(root,'mainnet')[0]['deployment']['enabled'] is False
        executed = controller.activate_seh(execute=True, token='verified-admin')
        assert executed['status'] == 'ACTIVATION_STARTED'
        assert executed['application_uuid'] == NEW
        assert git._canonical_selection(root,'mainnet')[0]['deployment']['enabled'] is True
        assert not any(method == 'DELETE' for method, _ in remote.calls)
    finally:
        controller.close()


def test_live_revision_three_shape_is_repaired_without_uuid_change(tmp_path):
    root, slh, remote = staged(tmp_path, prior_enabled=True)
    remote.build_status = 'finished'
    identity = st.deployment_record(st.read(root,'mainnet')[2], 'mainnet')
    current = ds.read(root, 'mainnet', identity)
    # Mirror the user's existing operational revision 3: same replacement UUID,
    # disabled, and no intentional undeployment marker.
    revision_three = ds.write(root, 'mainnet', identity, expected_uuid=NEW,
                              expected_revision=current['revision'],
                              deployment=deepcopy(current['deployment']))
    assert revision_three['revision'] == 3 and revision_three['deployment']['enabled'] is False
    outcome = git.enable_verified_recovery(root, 'mainnet', state_dir=slh,
                                           execute=True, client=remote, probe=healthy)
    assert outcome['status'] == 'RECOVERY_ENABLEMENT_VERIFIED'
    assert outcome['seh_deployment_revision'] == 4
    assert ds.read(root, 'mainnet', identity)['deployment']['application_uuid'] == NEW


def test_signing_trust_mismatch_prevents_enabling(tmp_path):
    root, slh, remote = staged(tmp_path)
    remote.build_status = 'finished'
    remote.envs = [{**row, 'value': 'mismatched-public-signing-key'}
                   if row['key'] == 'SEH_SLH_PUBLIC_KEY_B64' else row
                   for row in remote.envs]
    with pytest.raises(git.SEHGitError, match='signing trust'):
        git.enable_verified_recovery(root, 'mainnet', state_dir=slh,
                                     execute=True, client=remote, probe=healthy)
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['enabled'] is False
