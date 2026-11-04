"""Replacement of a missing Coolify runtime, never of its Mother application."""
from __future__ import annotations

from copy import deepcopy
import pytest

from main_computer.slh import storage
from tools.seh_control import git_deployment as git, identity as st
from tools.slh_seh_connector import service_identity
from seh_mutate_harness import _parser, run, HarnessError
from tests.test_seh_canonical_remote_metadata import installed, FakeCoolify, APP


def setup(tmp_path):
    root = installed(tmp_path)
    _, public = service_identity(tmp_path / 'slh', create=True)
    remote = FakeCoolify()
    remote.app.update(name=git.CANONICAL_APPLICATION_NAME,
                      description=git.CANONICAL_DESCRIPTION, id=42)
    remote.trust[0]['value'] = public
    remote.deployments = []
    original_request = remote.request

    def request(method, path, body=None):
        if method == 'GET' and path == '/api/v1/deployments':
            remote.calls.append((method, path))
            return deepcopy(remote.deployments)
        return original_request(method, path, body)

    remote.request = request
    from main_computer.slh.organization_state import save_config
    save_config(tmp_path / 'slh', {'schema': 'main-computer.slh-organization-config.v1',
        'configured': True, 'enabled': True, 'activation_confirmed': True,
        'host': '127.0.0.1', 'api_port': 19041, 'worker_port': 19042,
        'workers_enabled': True, 'seh_enabled': True})
    return root, remote, tmp_path / 'slh'


def observed_missing(url):
    assert url == 'https://external.greatlibrary.io'
    return {'status': 'PUBLIC_RUNTIME_UNAVAILABLE', 'http_status': 404}


def observed_live(url):
    return {'status': 'PUBLIC_RUNTIME_PRESENT', 'http_status': 200, 'slh_connected': False}


def test_public_runtime_present_does_not_redeploy(tmp_path, monkeypatch):
    root, remote, slh = setup(tmp_path)
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy', lambda *a, **k: pytest.fail('unexpected redeploy'))
    before = st.read(root, 'mainnet')[1].binding
    out = git.recover_canonical_runtime(root, 'mainnet', execute=True, client=remote,
                                        probe=observed_live, state_dir=slh)
    assert out['status'] == 'RUNTIME_PRESENT'
    assert out['relay_reports_slh_connected'] is False
    assert out['resident_tunnel'] == 'NOT_VERIFIED_BY_RUNTIME_PROBE'
    assert st.read(root, 'mainnet')[1].binding == before
    assert all(method == 'GET' for method, _ in remote.calls)


def test_missing_runtime_dry_run_preserves_all_state(tmp_path, monkeypatch):
    root, remote, slh = setup(tmp_path)
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy', lambda *a, **k: pytest.fail('unexpected redeploy'))
    before = st.read(root, 'mainnet')[1].binding
    out = git.recover_canonical_runtime(root, 'mainnet', client=remote, probe=observed_missing, state_dir=slh)
    assert out['status'] == 'PLAN_ONLY'
    assert out['application_uuid'] == APP and out['action'] == 'REDEPLOY_EXISTING_UUID'
    assert out['runtime_observation'] == 'PUBLIC_RUNTIME_UNAVAILABLE'
    assert all(method == 'GET' for method, _ in remote.calls)
    assert st.read(root, 'mainnet')[1].binding == before


def test_missing_runtime_requests_exact_existing_uuid_once(tmp_path, monkeypatch):
    root, remote, slh = setup(tmp_path)
    calls = []

    original = remote.request
    def with_forced_build(method, path, body=None):
        if method == 'POST' and path == f'/api/v1/deploy?uuid={APP}&force=true':
            calls.append((method, path))
            return {'deployments': [{'resource_uuid': APP, 'deployment_uuid': 'deploy123'}]}
        return original(method, path, body)
    remote.request = with_forced_build
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy',
                        lambda *a, **k: pytest.fail('start/restart fallback'))
    before = st.read(root, 'mainnet')[1].binding
    old_app = deepcopy(remote.app)
    old_trust = deepcopy(remote.trust)
    out = git.recover_canonical_runtime(root, 'mainnet', execute=True, client=remote,
                                        probe=observed_missing, state_dir=slh)
    assert out['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert out['outbound_tunnel_authenticated'] is False
    assert out['remote_mutation'] is True
    assert calls == [('POST', f'/api/v1/deploy?uuid={APP}&force=true')]
    assert out['deployment_uuid'] == 'deploy123'
    assert remote.app == old_app and remote.trust == old_trust
    assert st.read(root, 'mainnet')[1].binding == before
    assert not any(path == '/api/v1/applications/public' for _, path in remote.calls)


def test_existing_deploy_in_progress_never_queues_duplicate(tmp_path, monkeypatch):
    root, remote, slh = setup(tmp_path)
    remote.deployments = [{'application_id': 42, 'status': 'in_progress'}]
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy', lambda *a, **k: pytest.fail('duplicate redeploy'))
    out = git.recover_canonical_runtime(root, 'mainnet', execute=True, client=remote,
                                        probe=observed_missing, state_dir=slh)
    assert out['status'] == 'DEPLOYMENT_IN_PROGRESS'
    assert out['replacement_queued'] is True


@pytest.mark.parametrize('failure', ['wrong-uuid', 'wrong-key', 'wrong-repo', 'wrong-marker', 'disabled'])
def test_mismatched_canonical_target_refused_without_deploy(tmp_path, monkeypatch, failure):
    root, remote, slh = setup(tmp_path)
    if failure == 'wrong-uuid': remote.app['uuid'] = 'different-app'
    if failure == 'wrong-key': remote.trust[0]['value'] = 'different-key'
    if failure == 'wrong-repo': remote.app['git_repository'] = 'someone/other'
    if failure == 'wrong-marker': remote.app['description'] = 'untrusted'
    if failure == 'disabled':
        from main_computer.slh.organization_state import read_config, save_config
        save_config(slh, {**read_config(slh), 'seh_enabled': False})
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy', lambda *a, **k: pytest.fail('unsafe redeploy'))
    with pytest.raises((git.SEHGitError, ValueError)):
        git.recover_canonical_runtime(root, 'mainnet', execute=True, client=remote,
                                      probe=observed_missing, state_dir=slh)


def test_unrelated_mother_generation_change_does_not_gate_runtime_plan(tmp_path):
    root, remote, slh = setup(tmp_path)
    def moving_probe(url):
        if not hasattr(moving_probe, 'moved'):
            moving_probe.moved = True
            st.mutate(root, 'mainnet', None, 'concurrent-test-change',
                      lambda d, g, b: d['networks']['mainnet'].update(unrelated_marker=True))
        return observed_missing(url)
    result = git.recover_canonical_runtime(root, 'mainnet', execute=False, client=remote,
                                           probe=moving_probe, state_dir=slh)
    assert result['status'] == 'PLAN_ONLY'
    assert st.read(root, 'mainnet')[2]['networks']['mainnet']['unrelated_marker']


def test_cli_requires_no_name_and_two_approvals(tmp_path, monkeypatch):
    root, remote, slh = setup(tmp_path)
    monkeypatch.setattr(git, '_client', lambda controller: remote)
    monkeypatch.setattr(git, '_public_runtime_probe', observed_missing)
    monkeypatch.setattr(storage, 'slh_state_dir', lambda: slh)
    args = ['recover-runtime', '--runtime-state-root', str(root)]
    assert run(_parser().parse_args(args))['status'] == 'PLAN_ONLY'
    for extra in (['--seh', 'company-primary'], ['--host', 'coolify-c'], ['--slh-id', 'my-slh']):
        with pytest.raises(HarnessError):
            run(_parser().parse_args(args + extra))
    for extra in (['--execute-mutations'], ['--yes-i-know-this-mutates-seh']):
        with pytest.raises(HarnessError):
            run(_parser().parse_args(args + extra))


def test_authorized_activation_recovers_deleted_remote_without_second_identity(monkeypatch):
    from main_computer.slh.authenticator_store import LocalIdentityStore
    from http import client as http_client
    import json

    observed = []
    class Response:
        status = 200
        def __init__(self, value): self.data = json.dumps(value).encode()
        def read(self, count): return self.data

    class Local:
        def __init__(self, host, port, timeout):
            assert host == '127.0.0.1'
        def request(self, method, path, body=None, headers=None):
            observed.append((method, path, headers or {}))
        def getresponse(self):
            path = observed[-1][1]
            if path.endswith('/challenge'):
                return Response({'challenge_id': 'a'*32})
            if path.endswith('/verify'):
                return Response({'access_token': 'do-not-report'})
            if path.endswith('/activate'):
                return Response({'status': 'ACTIVATION_STARTED', 'application_uuid': APP})
            if path.endswith('/status'):
                return Response({'status': 'NOT_CONNECTED', 'resident_outbound_attempts': 10})
            return Response({'ok': True})
        def close(self): pass

    calls = []
    def recover(root, network, *, execute):
        calls.append((root, network, execute))
        return {'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED',
                'runtime_observation': 'PUBLIC_RUNTIME_UNAVAILABLE',
                'remote_mutation': True}
    monkeypatch.setattr(http_client, 'HTTPConnection', Local)
    monkeypatch.setattr(LocalIdentityStore, 'list', lambda s: {'identities': [{'device_id': 'admin-pc'}],
                                                                'invalid_identities': []})
    monkeypatch.setattr(LocalIdentityStore, 'sign', lambda s, d, c: {'signature': 'signed'})
    monkeypatch.setattr(git, 'recover_canonical_runtime', recover)
    answer = run(_parser().parse_args(['activate-seh', '--execute-mutations',
                                      '--yes-i-know-this-mutates-seh']))
    assert answer['status'] == 'INCOMPLETE'
    assert answer['failed_stage'] == 'coolify_runtime_recovery'
    assert answer['remote_recovery'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert calls == [(__import__('pathlib').Path('runtime/state'), 'mainnet', True)]
    assert 'do-not-report' not in str(answer)
    assert observed[-1][1] == '/v1/auth/logout'


def test_coolify_application_building_suppresses_another_deploy(tmp_path, monkeypatch):
    root, remote, slh = setup(tmp_path)
    remote.app['status'] = 'building'
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy', lambda *a, **k: pytest.fail('duplicate deploy'))
    result = git.recover_canonical_runtime(root, 'mainnet', execute=True,
                                           client=remote, state_dir=slh, probe=observed_missing)
    assert result['status'] == 'DEPLOYMENT_IN_PROGRESS'
    assert 'remote_mutation' in result and result['remote_mutation'] is False


def test_unknown_deployment_inventory_refuses_unsafe_retry(tmp_path, monkeypatch):
    root, remote, slh = setup(tmp_path)
    remote.deployments = [{'status': 'in_progress'}]
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy', lambda *a, **k: pytest.fail('unverified deploy'))
    with pytest.raises(git.SEHGitError, match='ambiguous'):
        git.recover_canonical_runtime(root, 'mainnet', execute=True,
                                      client=remote, state_dir=slh, probe=observed_missing)


def test_recover_runtime_rejects_http_405_without_start_fallback(tmp_path, monkeypatch):
    root, remote, slh = setup(tmp_path)
    original = remote.request
    def no_build(method, path, body=None):
        if method == 'POST' and path.startswith('/api/v1/deploy?'):
            from types import SimpleNamespace
            return SimpleNamespace(ok=False, status=405, body={})
        return original(method, path, body)
    remote.request = no_build
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy',
                        lambda *a, **kw: pytest.fail('start/restart fallback'))
    out = git.recover_canonical_runtime(root, 'mainnet', execute=True,
                                        client=remote, probe=observed_missing, state_dir=slh)
    assert out['status'] == 'INCOMPLETE'
    assert out['failed_stage'] == 'existing_uuid_forced_build'
    assert '405' in out['build_submission_reason']
    assert not any('/start' in path or '/restart' in path for _,path in remote.calls)


@pytest.mark.parametrize(('coolify_status', 'expected_status', 'stage'), [
    ('queued', 'DEPLOYMENT_IN_PROGRESS', None),
    ('finished', 'INCOMPLETE', 'public_runtime_missing_after_completed_build'),
    ('failed', 'INCOMPLETE', 'prior_recovery_build_failed'),
])
def test_existing_recovery_build_reconciled_without_duplicate(tmp_path, monkeypatch,
                                                              coolify_status, expected_status, stage):
    root, remote, slh = setup(tmp_path)
    revision = 'a' * 40
    git._save_recovery_checkpoint(slh, {
        'schema': 'seh-coolify-recovery.v1', 'old_uuid': 'previous',
        'new_uuid': APP, 'mother_digest': 'old-binding',
        'git_revision': revision, 'key_sha256': 'preserved',
        'deployment_uuid': 'build123'})
    original = remote.request
    def request(method, path, body=None):
        if method == 'GET' and path == '/api/v1/deployments/build123':
            return {'deployment_uuid': 'build123', 'application_id': 42,
                    'force_rebuild': True, 'is_api': True, 'restart_only': False,
                    'commit': revision, 'status': coolify_status}
        if method == 'POST':
            pytest.fail('Duplicate build request')
        return original(method, path, body)
    remote.request = request
    monkeypatch.setattr(git.hub_coolify, 'trigger_deploy',
                        lambda *a, **k: pytest.fail('legacy fallback'))
    out = git.recover_canonical_runtime(root, 'mainnet', execute=True,
                                        client=remote, probe=observed_missing, state_dir=slh)
    assert out['status'] == expected_status
    assert out['deployment_uuid'] == 'build123'
    if stage:
        assert out['failed_stage'] == stage
    assert out['remote_mutation'] is False


def test_recovery_build_wrong_revision_refuses_second_submit(tmp_path):
    root, remote, slh = setup(tmp_path)
    git._save_recovery_checkpoint(slh, {
        'schema': 'seh-coolify-recovery.v1', 'old_uuid': 'previous',
        'new_uuid': APP, 'mother_digest': 'old-binding',
        'git_revision': 'a'*40, 'key_sha256': 'preserved',
        'deployment_uuid': 'build123'})
    original = remote.request
    def request(method, path, body=None):
        if method == 'GET' and path == '/api/v1/deployments/build123':
            return {'deployment_uuid': 'build123', 'application_id': 42,
                    'force_rebuild': True, 'is_api': True, 'restart_only': False,
                    'commit': 'b'*40, 'status': 'queued'}
        if method == 'POST':
            pytest.fail('Duplicate build request')
        return original(method, path, body)
    remote.request = request
    out = git.recover_canonical_runtime(root, 'mainnet', execute=True,
                                        client=remote, probe=observed_missing, state_dir=slh)
    assert out['status'] == 'INCOMPLETE'
    assert out['build_verification'] == 'CONFLICT'
    assert out['failed_stage'] == 'prior_recovery_build_readback'


def test_completed_prior_build_can_rebuild_later_exited_runtime(tmp_path):
    """Old completed receipt must not permanently disable lost-container recovery."""
    root, remote, slh = setup(tmp_path)
    remote.app['status'] = 'exited'
    git._save_recovery_checkpoint(slh, {
        'schema': 'seh-coolify-recovery.v1', 'old_uuid': 'previous',
        'new_uuid': APP, 'mother_digest': 'old-binding',
        'git_revision': 'a'*40, 'key_sha256': 'preserved',
        'deployment_uuid': 'old-build'})
    original = remote.request
    requests = []
    def request(method, path, body=None):
        requests.append((method, path))
        if method == 'GET' and path == '/api/v1/deployments/old-build':
            return {'deployment_uuid': 'old-build', 'application_id': 42,
                    'force_rebuild': True, 'is_api': True, 'restart_only': False,
                    'commit': 'a'*40, 'status': 'finished'}
        if method == 'POST' and path == f'/api/v1/deploy?uuid={APP}&force=true':
            return {'deployments': [{'resource_uuid': APP, 'deployment_uuid': 'new-build'}]}
        if method == 'GET' and path == '/api/v1/deployments/new-build':
            return {'deployment_uuid': 'new-build', 'application_id': 42,
                    'force_rebuild': True, 'is_api': True, 'restart_only': False,
                    'commit': 'a'*40, 'status': 'queued'}
        return original(method, path, body)
    remote.request = request
    first = git.recover_canonical_runtime(root, 'mainnet', execute=True,
                            client=remote, probe=observed_missing, state_dir=slh)
    assert first['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert first['deployment_uuid'] == 'new-build'
    assert git._recovery_checkpoint(slh)['deployment_uuid'] == 'new-build'
    second = git.recover_canonical_runtime(root, 'mainnet', execute=True,
                            client=remote, probe=observed_missing, state_dir=slh)
    assert second['status'] == 'DEPLOYMENT_IN_PROGRESS'
    assert second['deployment_uuid'] == 'new-build'
    assert len([path for method, path in requests if method == 'POST']) == 1
