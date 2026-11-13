"""Deleted Coolify application: one signed SLH activation, no alias or new key."""
from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from tools.mother.common.errors import MotherError

from tools.seh_control import git_deployment as git, identity as st, deployment_state as ds
from tools.slh_seh_connector import service_identity
from tests.test_seh_canonical_remote_metadata import installed, APP, PROJECT, ENV, SERVER
from tests.test_slh_seh_governed_activation import ready, record
from main_computer.slh.seh_canonical_connection import (
    resolve_canonical_connection, SEHApplicationDeleted)


PUBLISHED = {'repository': 'https://github.com/johnrraymond/main_computer',
             'branch': 'main', 'revision': 'a'*40,
             'dockerfile_location': git.DOCKERFILE}
NEW = 'new-coolify-uuid-123'


class DeletedCoolify:
    def __init__(self):
        self.calls = []
        self.created = None
        self.envs = []
        self.apps = []
        self.force_failure = None

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        if method == 'GET' and path == f'/api/v1/applications/{APP}':
            return SimpleNamespace(ok=False, status=404, body={'message': 'not found'})
        if method == 'GET' and path == '/api/v1/applications':
            return deepcopy(self.apps)
        if method == 'GET' and path == f'/api/v1/projects/{PROJECT}/environments':
            return [{'uuid': ENV, 'name': 'mainnet-hubs'}]
        if method == 'GET' and path == f'/api/v1/servers/{SERVER}':
            return {'uuid': SERVER, 'is_usable': True, 'is_reachable': True}
        if method == 'POST' and path == '/api/v1/applications/public':
            assert self.created is None
            assert body['name'] == git.CANONICAL_APPLICATION_NAME
            assert body['description'] == git.CANONICAL_DESCRIPTION
            assert body['git_repository'] == PUBLISHED['repository']
            self.created = deepcopy(body)
            self.apps = [{'uuid': NEW, 'name': git.CANONICAL_APPLICATION_NAME,
                          'description': git.CANONICAL_DESCRIPTION,
                          'domains': body['domains']}]
            return {'uuid': NEW}
        if method == 'GET' and path == f'/api/v1/applications/{NEW}':
            if self.created is None:
                return SimpleNamespace(ok=False, status=404, body={})
            return {'uuid': NEW, 'name': git.CANONICAL_APPLICATION_NAME,
                    'description': git.CANONICAL_DESCRIPTION,
                    'project_uuid': PROJECT, 'server_uuid': SERVER,
                    'environment_uuid': ENV, 'environment_name': 'mainnet-hubs',
                    'git_repository': PUBLISHED['repository'],
                    'dockerfile_location': git.DOCKERFILE,
                    'domains': self.created['domains']}
        if method == 'GET' and path == f'/api/v1/applications/{NEW}/envs':
            return deepcopy(self.envs)
        if method == 'POST' and path == f'/api/v1/deploy?uuid={NEW}&force=true':
            if self.force_failure is not None:
                return SimpleNamespace(ok=False, status=self.force_failure, body={})
            return {'deployments': [{'deployment_uuid': 'seh-build-01', 'resource_uuid': NEW}]}
        if method == 'GET' and path == '/api/v1/deployments/seh-build-01':
            return {'deployment_uuid': 'seh-build-01', 'resource_uuid': NEW,
                    'force_rebuild': True, 'is_api': True, 'restart_only': False,
                    'commit': PUBLISHED['revision'], 'status': 'queued'}
        if method == 'PATCH' and path == f'/api/v1/applications/{NEW}/envs/bulk':
            assert self.created is not None
            self.envs = [{'key': entry['key'], 'value': entry['value'],
                          'uuid': f'env-{i}'} for i, entry in enumerate(body['data'])]
            return {'ok': True}
        raise AssertionError(f'Unexpected operation {method} {path}')


def setup(tmp_path):
    root = installed(tmp_path)
    slh = tmp_path / 'slh'
    service_identity(slh, create=True)
    from main_computer.slh.organization_state import save_config
    save_config(slh, {'schema': 'main-computer.slh-organization-config.v1',
        'configured': True, 'enabled': True, 'activation_confirmed': True,
        'host': '127.0.0.1', 'api_port': 19041, 'worker_port': 19042,
        'workers_enabled': True, 'seh_enabled': True})
    return root, slh, DeletedCoolify()


def test_explicit_404_is_only_missing_state(tmp_path):
    root, slh, remote = setup(tmp_path)
    with pytest.raises(SEHApplicationDeleted):
        resolve_canonical_connection(slh, mother_root=root, client=remote)
    class Wrong:
        def request(self, *args):
            return SimpleNamespace(ok=False, status=403, body={})
    with pytest.raises(git.SEHGitError, match='HTTP 403'):
        resolve_canonical_connection(slh, mother_root=root, client=Wrong())
    class Mismatch:
        def request(self, *args):
            return {'uuid': 'some-other-app'}
    with pytest.raises(git.SEHGitError, match='conflicting'):
        resolve_canonical_connection(slh, mother_root=root, client=Mismatch())


def test_deleted_app_dry_run_never_modifies_anything(tmp_path, monkeypatch):
    root, slh, remote = setup(tmp_path)
    before = st.read(root, 'mainnet')[1].binding
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy', lambda *a, **k: pytest.fail('triggered'))
    result = git.restore_deleted_canonical_application(
        root, 'mainnet', execute=False, state_dir=slh, client=remote, source=PUBLISHED)
    assert result['status'] == 'PLAN_ONLY'
    assert result['former_application_uuid'] == APP
    assert st.read(root, 'mainnet')[1].binding == before
    assert not any(op in ('POST', 'PATCH') for op, _ in remote.calls)


def test_deleted_app_create_bind_cas_and_queue(tmp_path, monkeypatch):
    root, slh, remote = setup(tmp_path)
    before = st.read(root, 'mainnet')[1].binding.generation
    doc_before = deepcopy(st.read(root, 'mainnet')[2])
    key_before = (slh / 'seh-service-signing-key.pem').read_bytes()
    deployed = []
    def queue(client, *, application_uuid, force, tried):
        deployed.append(application_uuid)
        return {'ok': True, 'body': {'deployments': [{'resource_uuid': NEW}]}}
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy', queue)
    result = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                           state_dir=slh, client=remote, source=PUBLISHED)
    assert result['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert result['application_uuid'] == NEW
    assert result['mother_generation'] == before
    assert result['signing_key_untouched']
    assert ('POST', f'/api/v1/deploy?uuid={NEW}&force=true') in remote.calls
    current = st.read(root, 'mainnet')[2]
    assert current == doc_before  # Mother is read-only during SEH recovery
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['application_uuid'] == NEW
    assert ds.read(root, 'mainnet', st.deployment_record(current, 'mainnet'))['revision'] == 1
    assert (slh / 'seh-service-signing-key.pem').read_bytes() == key_before
    assert 'SEH_ORGANIZATION_ID' not in [env['key'] for env in remote.envs]
    assert remote.created['domains'] == 'https://external.greatlibrary.io:8080'
    _, public = service_identity(slh, create=False)
    assert next(row['value'] for row in remote.envs if row['key'] == 'SEH_SLH_PUBLIC_KEY_B64') == public


def test_recovery_refuses_collision_and_does_not_create(tmp_path):
    root, slh, remote = setup(tmp_path)
    remote.apps = [{'name': git.CANONICAL_APPLICATION_NAME, 'uuid': 'unknown'}]
    with pytest.raises(git.SEHGitError, match='duplicate'):
        git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                             state_dir=slh, client=remote, source=PUBLISHED)
    assert remote.created is None
    assert st.read(root, 'mainnet')[2]['networks']['mainnet']['seh_deployment']['deployment']['application_uuid'] == APP


def test_unpublished_source_refused_before_creation(tmp_path):
    root, slh, remote = setup(tmp_path)
    with pytest.raises(git.SEHGitError, match='source cannot be verified'):
        git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
            state_dir=slh, client=remote, source={**PUBLISHED, 'repository': 'https://github.com/other/repo'})
    assert remote.created is None


def test_unrelated_mother_update_after_create_does_not_gate_seh_deploy(tmp_path):
    root, slh, remote = setup(tmp_path)
    old_request = remote.request
    def moving(method, path, body=None):
        response = old_request(method, path, body)
        if method == 'PATCH' and path.endswith('/envs/bulk'):
            st.mutate(root, 'mainnet', None, 'unrelated-concurrent-change',
                      lambda d, g, b: d['networks']['mainnet'].update(test_only=True))
        return response
    remote.request = moving
    result = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                         state_dir=slh, client=remote, source=PUBLISHED)
    assert result['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert st.read(root, 'mainnet')[2]['networks']['mainnet']['test_only']
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['application_uuid'] == NEW
    assert len([op for op in remote.calls if op == ('POST', '/api/v1/applications/public')]) == 1


def test_resident_activation_handles_only_explicit_deleted_application(tmp_path, monkeypatch):
    root, slh, controller, resolver = ready(tmp_path, monkeypatch)
    from main_computer.slh.seh_autoconnect import SEHConnectionSettings
    resolver.side_effect = [SEHApplicationDeleted('gone'),
        SEHConnectionSettings(True, 'wss://external.greatlibrary.io',
                               False, 2.0)]
    restore = []
    def fake_restore(mother_root, network, *, execute, state_dir):
        restore.append((mother_root, network, execute, state_dir))
        current = st.read(root, 'mainnet')[1].binding.generation
        return {'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED', 'application_uuid': NEW,
                'former_application_uuid': APP,
                'mother_generation': current, 'remote_mutation': True}
    monkeypatch.setattr('tools.seh_control.git_deployment.restore_deleted_canonical_application',fake_restore)
    result = controller.activate_seh(execute=True, token='verified-admin')
    assert result['status'] == 'ACTIVATION_STARTED'
    assert result['remote_recovery'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert result['application_uuid'] == NEW
    assert len(restore) == 1 and restore[0][2] is True
    assert controller.config()['seh_enabled'] is True
    controller.close()


def test_git_source_refusal_remains_diagnostic_not_generic_trust_error(tmp_path, monkeypatch):
    root, slh, controller, resolver = ready(tmp_path, monkeypatch)
    resolver.side_effect = SEHApplicationDeleted('gone')
    def source_refusal(*a, **kw):
        raise git.SEHGitPublicationError(
            'Local HEAD diverged from published source; no push is safe')
    monkeypatch.setattr(git, 'restore_deleted_canonical_application', source_refusal)
    result = controller.activate_seh(execute=True, token='verified-admin')
    assert result['status'] == 'INCOMPLETE'
    assert result['failed_stage'] == 'git_source_publication'
    assert result['git_source_reason'].endswith('no push is safe')
    assert 'git_repair_command' not in result
    controller.close()


def _mother_error():
    return MotherError(code='MOTHER_STATE_PRIVATE_STATE_CONFLICT',
                       message='simulated verified-state installation failure',
                       operation_id='seh-recover-test', module_id='MOTHER-OFM-STATE-004',
                       retry_class='operator-decision', authority_effect='none')


def test_mother_operational_writes_are_not_called_during_recovery(tmp_path, monkeypatch):
    root, slh, remote = setup(tmp_path)
    before = st.read(root, 'mainnet')[1].binding
    monkeypatch.setattr(st, 'mutate', lambda *args, **kwargs:
                        pytest.fail('SEH recovery must never mutate Mother'))
    result = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                        state_dir=slh, client=remote, source=PUBLISHED)
    assert result['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert result['mother_mutation'] == 'UNCHANGED'
    assert st.read(root, 'mainnet')[1].binding == before
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['application_uuid'] == NEW
    assert len([op for op in remote.calls if op == ('POST', '/api/v1/applications/public')]) == 1


def test_seh_state_write_failure_adopts_once_on_retry(tmp_path, monkeypatch):
    root, slh, remote = setup(tmp_path)
    actual_write = ds.write
    attempts = []
    def fail_first(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError('SEH local state fault')
        return actual_write(*args, **kwargs)
    monkeypatch.setattr(ds, 'write', fail_first)
    first = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                         state_dir=slh, client=remote, source=PUBLISHED)
    assert first['status'] == 'INCOMPLETE'
    assert first['failed_stage'] == 'seh_deployment_state_commit_or_trust'
    assert not any(path.startswith('/api/v1/deploy?') for method, path in remote.calls)
    second = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                         state_dir=slh, client=remote, source=PUBLISHED)
    assert second['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert second['recovery_action'] == 'ADOPT_VERIFIED_APPLICATION'
    assert len([op for op in remote.calls if op == ('POST', '/api/v1/applications/public')]) == 1


def test_untrusted_collision_never_adopted(tmp_path, monkeypatch):
    root, slh, remote = setup(tmp_path)
    remote.created = {'domains':'https://external.greatlibrary.io:8080'}
    remote.apps = [{'name':git.CANONICAL_APPLICATION_NAME, 'uuid':NEW,
                    'domains': 'https://external.greatlibrary.io:8080'}]
    before = st.read(root, 'mainnet')[1].binding
    with pytest.raises(git.SEHGitError, match='Partial trust cannot be adopted'):
        git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                             state_dir=slh, client=remote, source=PUBLISHED)
    assert st.read(root, 'mainnet')[1].binding == before
    assert ('POST', '/api/v1/applications/public') not in remote.calls


def test_partial_env_patch_failure_is_resumed_with_exact_creation_receipt(tmp_path, monkeypatch):
    """POST acknowledged, env mutation fails after one trust key; retry completes."""
    root, slh, remote = setup(tmp_path)
    original_request = remote.request
    stopped = [False]
    def partial(method, path, body=None):
        if method == 'PATCH' and path == f'/api/v1/applications/{NEW}/envs/bulk' and not stopped[0]:
            stopped[0] = True
            # The obsolete SEH_SLH_ID route no longer exists. Simulate a
            # partial write of the remaining binding/public-origin env instead.
            row = next(item for item in body['data'] if item['key'] == 'SEH_PUBLIC_ORIGIN')
            remote.envs = [{'key': 'SEH_PUBLIC_ORIGIN', 'value': row['value'], 'uuid': 'env-partial'}]
            raise ConnectionError('simulated lost partial environment transaction')
        if method == 'PATCH' and path.endswith('/envs/bulk'):
            previous = deepcopy(remote.envs)
            result = original_request(method, path, body)
            remote.envs = previous + deepcopy(remote.envs)
            return result
        return original_request(method, path, body)
    remote.request = partial
    first = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                           state_dir=slh, client=remote, source=PUBLISHED)
    assert first['status'] == 'INCOMPLETE'
    assert first['failed_stage'] == 'seh_deployment_state_commit_or_trust'
    assert (slh / git._RECOVERY_CHECKPOINT).is_file()
    second = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                           state_dir=slh, client=remote, source=PUBLISHED)
    assert second['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert second['recovery_action'] == 'ADOPT_VERIFIED_APPLICATION'
    assert second['deployment_uuid'] == 'seh-build-01'
    assert remote.envs[0]['key'] == 'SEH_PUBLIC_ORIGIN'
    assert len([op for op in remote.calls if op == ('POST','/api/v1/applications/public')]) == 1


def test_partial_trust_with_wrong_local_receipt_is_never_adopted(tmp_path):
    root, slh, remote = setup(tmp_path)
    remote.created = {'domains': 'https://external.greatlibrary.io:8080'}
    remote.apps = [{'name': git.CANONICAL_APPLICATION_NAME, 'uuid': NEW,
                    'domains': 'https://external.greatlibrary.io:8080'}]
    (slh / git._RECOVERY_CHECKPOINT).write_text(json.dumps({
        'schema': 'seh-coolify-recovery.v1', 'old_uuid': APP, 'new_uuid': 'different',
        'mother_digest': 'wrong', 'git_revision': PUBLISHED['revision'],
        'key_sha256': 'wrong'}))
    with pytest.raises(git.SEHGitError, match='receipt conflicts'):
        git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                             state_dir=slh, client=remote, source=PUBLISHED)
    assert ('POST', '/api/v1/applications/public') not in remote.calls


def test_forced_build_http_405_does_not_fall_back_to_start(tmp_path, monkeypatch):
    root, slh, remote = setup(tmp_path)
    remote.force_failure = 405
    def unsafe_fallback(*a, **kw):
        pytest.fail('legacy start/restart fallback must never run')
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy', unsafe_fallback)
    out = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                         state_dir=slh, client=remote, source=PUBLISHED)
    assert out['status'] == 'INCOMPLETE'
    assert out['failed_stage'] == 'new_application_forced_build'
    assert 'HTTP 405' in out['build_submission_reason']
    assert not any('/start' in path or '/restart' in path for _,path in remote.calls)
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['application_uuid'] == NEW


def test_recovery_receipt_prevents_duplicate_create_when_mother_writes_blocked(tmp_path, monkeypatch):
    root, slh, remote = setup(tmp_path)
    monkeypatch.setattr(st, 'mutate', lambda *a, **k: pytest.fail('Mother write forbidden'))
    first = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                         state_dir=slh, client=remote, source=PUBLISHED)
    assert first['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    checkpoint = git._recovery_checkpoint(slh)
    assert checkpoint['new_uuid'] == NEW and checkpoint['deployment_uuid'] == 'seh-build-01'
    again = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                           execute=True, client=remote)
    assert again['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert len([op for op in remote.calls if op == ('POST', '/api/v1/applications/public')]) == 1
    assert len([op for op in remote.calls if op[0] == 'POST' and op[1].startswith('/api/v1/deploy?')]) == 1


def test_build_receipt_wrong_commit_rejected_after_one_post(tmp_path):
    root, slh, remote = setup(tmp_path)
    original = remote.request
    def mismatch(method, path, body=None):
        result = original(method, path, body)
        if path == '/api/v1/deployments/seh-build-01':
            result['commit'] = 'b' * 40
        return result
    remote.request = mismatch
    result = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                          state_dir=slh, client=remote, source=PUBLISHED)
    assert result['status'] == 'INCOMPLETE'
    assert result['failed_stage'] == 'new_application_build_readback'
    assert result['build_verification'] == 'CONFLICT'
    assert result['build_verification_reason'] == 'git_revision_unverified'
    assert result['deployment_uuid'] == 'seh-build-01'
    assert len([op for op in remote.calls if op[0] == 'POST' and op[1].startswith('/api/v1/deploy?')]) == 1


def test_build_receipt_not_yet_visible_is_not_false_success(tmp_path):
    root, slh, remote = setup(tmp_path)
    original = remote.request
    def not_yet(method, path, body=None):
        if method == 'GET' and path == '/api/v1/deployments/seh-build-01':
            return SimpleNamespace(ok=False, status=404, body={})
        return original(method, path, body)
    remote.request = not_yet
    result = git.restore_deleted_canonical_application(root, 'mainnet', execute=True,
                          state_dir=slh, client=remote, source=PUBLISHED)
    assert result['status'] == 'INCOMPLETE'
    assert result['build_verification'] == 'NOT_YET_OBSERVED'
    receipt = git._recovery_checkpoint(slh)
    assert receipt['deployment_uuid'] == 'seh-build-01'
