"""Canonical SEH forced deploy: dry-run, approval, receipt, idempotency, live trust."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from seh_mutate_harness import _parser, run, HarnessError
from tools.seh_control import git_deployment as git, identity as st, deployment_state as ds
from tests.test_seh_canonical_remote_metadata import installed, FakeCoolify, APP

REVISION = 'e6decafc8e8c9622d3e5ddf2dd98dbe32a011302'
DEPLOYMENT = 'sehforceddeployment0001'


class Coolify(FakeCoolify):
    def __init__(self):
        super().__init__()
        self.app.update(name=git.CANONICAL_APPLICATION_NAME,
                        description=git.CANONICAL_DESCRIPTION, id=42,
                        status='running:healthy', git_commit_sha=REVISION)
        self.pending = []
        self.submit_count = 0
        self.build_state = 'finished'
        self.fail_submit = False
        self.receipt_revision = REVISION

    def request(self, method, path, body=None):
        if method == 'GET' and path == '/api/v1/deployments':
            self.calls.append((method, path))
            return deepcopy(self.pending)
        if method == 'POST' and path == f'/api/v1/deploy?uuid={APP}&force=true':
            self.calls.append((method, path))
            self.submit_count += 1
            if self.fail_submit:
                raise ConnectionError('network acknowledgment lost')
            return {'deployments': [{'resource_uuid': APP, 'deployment_uuid': DEPLOYMENT}]}
        if method == 'GET' and path == '/api/v1/deployments/' + DEPLOYMENT:
            self.calls.append((method, path))
            return {'deployment_uuid': DEPLOYMENT, 'resource_uuid': APP,
                    'application_id': 42, 'force_rebuild': True,
                    'is_api': True, 'restart_only': False,
                    'commit': self.receipt_revision, 'status': self.build_state}
        return super().request(method, path, body)


def ctx(tmp_path):
    root = installed(tmp_path)
    return root, Coolify()


def live(url):
    assert url == 'https://external.greatlibrary.io'
    return {'status': 'PUBLIC_RUNTIME_PRESENT', 'http_status': 200, 'slh_connected': False}


def execute(root, remote, **kwargs):
    return git.redeploy_canonical_runtime(root, 'mainnet', execute=True,
                                          client=remote, probe=live, poll_s=0.01, **kwargs)


def test_plan_only_is_read_only_and_does_not_need_resident_slh(tmp_path):
    root, remote = ctx(tmp_path)
    before = st.read(root, 'mainnet')[1].binding
    result = git.redeploy_canonical_runtime(root, 'mainnet', client=remote)
    assert result['status'] == 'PLAN_ONLY'
    assert result['git_revision'] == REVISION
    assert result['application_uuid'] == APP
    assert all(method == 'GET' for method, _ in remote.calls)
    assert not (ds.directory(root, 'mainnet') / git._REDEPLOY_CHECKPOINT).exists()
    assert st.read(root, 'mainnet')[1].binding == before


def test_force_redeploy_verified_healthy_runtime_preserves_canonical_identity(tmp_path):
    root, remote = ctx(tmp_path)
    original_app = deepcopy(remote.app)
    original_trust = deepcopy(remote.trust)
    before = st.read(root, 'mainnet')[1].binding
    result = execute(root, remote)
    assert result['status'] == 'SEH_REDEPLOY_HARNESS_COMPLETE'
    assert result['deployment_uuid'] == DEPLOYMENT
    assert result['git_revision'] == REVISION
    assert result['signing_key_trust'] == 'MATCHED'
    assert result['public_health'] == 'VERIFIED'
    assert result['slh_connected'] is False
    assert remote.submit_count == 1
    assert ('POST', f'/api/v1/deploy?uuid={APP}&force=true') in remote.calls
    assert remote.app == original_app
    assert remote.trust == original_trust
    assert st.read(root, 'mainnet')[1].binding == before
    assert git._redeploy_checkpoint(root, 'mainnet')['phase'] == 'VERIFIED'
    assert all('/api/v1/applications/public' not in path for _, path in remote.calls)


def test_waiting_deployment_resumes_same_uuid_without_second_post(tmp_path):
    root, remote = ctx(tmp_path)
    remote.build_state = 'in_progress'
    clock = [0.0]
    def monotonic(): return clock[0]
    def wait(seconds): clock[0] += seconds
    first = execute(root, remote, clock=monotonic, sleep=wait, timeout_s=1)
    assert first['status'] == 'INCOMPLETE'
    assert first['failed_stage'] == 'deployment_wait_timeout'
    assert git._redeploy_checkpoint(root, 'mainnet')['phase'] == 'ACKNOWLEDGED'
    remote.build_state = 'finished'
    second = execute(root, remote)
    assert second['status'] == 'SEH_REDEPLOY_HARNESS_COMPLETE'
    assert remote.submit_count == 1


def test_uncertain_acknowledgment_is_frozen_without_duplicate_post(tmp_path):
    root, remote = ctx(tmp_path)
    remote.fail_submit = True
    first = execute(root, remote)
    assert first['status'] == 'INCOMPLETE'
    assert first['remote_mutation'] == 'UNKNOWN'
    assert first['failed_stage'] == 'forced_build_submission'
    assert git._redeploy_checkpoint(root, 'mainnet')['phase'] == 'INTENT'
    second = execute(root, remote)
    assert second['failed_stage'] == 'unacknowledged_prior_deployment'
    assert remote.submit_count == 1


def test_failed_build_does_not_return_success_or_repeat_post(tmp_path):
    root, remote = ctx(tmp_path)
    remote.build_state = 'failed'
    first = execute(root, remote)
    assert first['status'] == 'INCOMPLETE'
    assert first['failed_stage'] == 'deployment_build_failed'
    assert git._redeploy_checkpoint(root, 'mainnet')['phase'] == 'FAILED'
    second = execute(root, remote)
    assert second['failed_stage'] == 'prior_deployment_failed'
    assert remote.submit_count == 1


def test_mismatched_deployment_receipt_rejected(tmp_path):
    root, remote = ctx(tmp_path)
    remote.receipt_revision = 'f' * 40
    first = execute(root, remote)
    assert first['status'] == 'INCOMPLETE'
    assert first['failed_stage'] == 'deployment_receipt_conflict'
    assert remote.submit_count == 1


@pytest.mark.parametrize('field,value', [
    ('uuid', 'another-app'), ('description', 'changed'),
    ('git_repository', 'other-company/other-repo'),
    ('git_commit_sha', None), ('domains', 'https://not-our-seh.example.org')])
def test_bad_application_refused_before_any_mutation(tmp_path, field, value):
    root, remote = ctx(tmp_path)
    remote.app[field] = value
    with pytest.raises((git.SEHGitError, ValueError)):
        execute(root, remote)
    assert remote.submit_count == 0
    assert not (ds.directory(root, 'mainnet') / git._REDEPLOY_CHECKPOINT).exists()


def test_missing_trust_refuses_before_any_post(tmp_path):
    root, remote = ctx(tmp_path)
    remote.trust = [row for row in remote.trust if row['key'] != 'SEH_SLH_PUBLIC_KEY_B64']
    with pytest.raises(git.SEHGitError, match='trust'):
        execute(root, remote)
    assert remote.submit_count == 0


def test_active_build_refused_before_any_post(tmp_path):
    root, remote = ctx(tmp_path)
    remote.pending = [{'resource_uuid': APP, 'status': 'in_progress'}]
    with pytest.raises(git.SEHGitError, match='active Coolify deployment'):
        execute(root, remote)
    assert remote.submit_count == 0


def test_cli_guarded_target_selection_and_approvals(tmp_path, monkeypatch):
    from tools.seh_control.commands import SEHControl
    root, remote = ctx(tmp_path)
    monkeypatch.setattr(git, '_client', lambda controller: remote)
    monkeypatch.setattr(git, '_public_runtime_probe', live)
    base = ['redeploy-runtime', '--network', 'mainnet', '--runtime-state-root', str(root)]
    assert run(_parser().parse_args(base))['status'] == 'PLAN_ONLY'
    for bad in ('--seh', '--host', '--slh-id', '--public-url', '--admin-device'):
        with pytest.raises(HarnessError):
            run(_parser().parse_args(base + [bad, 'untrusted']))
    for partial in ('--execute-mutations', '--yes-i-know-this-mutates-seh'):
        with pytest.raises(HarnessError):
            run(_parser().parse_args(base + [partial]))
    result = run(_parser().parse_args(base + [
        '--execute-mutations', '--yes-i-know-this-mutates-seh']))
    assert result['status'] == 'SEH_REDEPLOY_HARNESS_COMPLETE'
    assert remote.submit_count == 1
    assert 'COOLIFY-PRIVATE-SECRET' not in json.dumps(result)


def test_http_probe_uses_compatible_user_agent_and_classifies_edge_rejection(monkeypatch):
    import urllib.request
    import urllib.error
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, count):
            return b'{"service":"main_computer.seh","live":true,"ready":false}'
    class Opener:
        def open(self, request, timeout):
            assert request.get_header('User-agent') == 'Mozilla/5.0'
            return Response()
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    assert git._public_runtime_probe('https://external.greatlibrary.io')['status'] == 'PUBLIC_RUNTIME_PRESENT'
    class Block:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, None)
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Block())
    with pytest.raises(git.SEHGitError, match='HTTP 403'):
        git._public_runtime_probe('https://external.greatlibrary.io')


def test_completed_historical_deployments_do_not_block_new_forced_build(tmp_path):
    root, remote = ctx(tmp_path)
    remote.pending = [{'resource_uuid': APP, 'status': 'finished'}]
    assert execute(root, remote)['status'] == 'SEH_REDEPLOY_HARNESS_COMPLETE'
    assert remote.submit_count == 1


def test_public_health_unavailable_is_not_a_success_and_resumes_without_new_post(tmp_path):
    root, remote = ctx(tmp_path)
    clock = [0.0]
    def monotonic(): return clock[0]
    def wait(seconds): clock[0] += seconds
    failure = lambda url: {'status': 'PUBLIC_RUNTIME_UNAVAILABLE', 'http_status': 503}
    first = git.redeploy_canonical_runtime(root, 'mainnet', execute=True,
                  client=remote, probe=failure, clock=monotonic, sleep=wait,
                  timeout_s=1, poll_s=0.01)
    assert first['status'] == 'INCOMPLETE'
    assert first['failed_stage'] == 'public_runtime_verification'
    assert git._redeploy_checkpoint(root, 'mainnet')['phase'] == 'ACKNOWLEDGED'
    assert execute(root, remote)['status'] == 'SEH_REDEPLOY_HARNESS_COMPLETE'
    assert remote.submit_count == 1


def test_postdeployment_signing_key_drift_is_not_accepted(tmp_path):
    root, remote = ctx(tmp_path)
    def corrupt_runtime(url):
        remote.trust[0]['value'] = 'DIFFERENT-SIGNING-KEY'
        return live(url)
    with pytest.raises(git.SEHGitError, match='signing trust'):
        git.redeploy_canonical_runtime(root, 'mainnet', execute=True,
                                       client=remote, probe=corrupt_runtime)
    assert remote.submit_count == 1
    assert git._redeploy_checkpoint(root, 'mainnet')['phase'] == 'ACKNOWLEDGED'


def test_lost_acknowledgment_reconciles_verified_coolify_deployment_without_second_post(tmp_path):
    from datetime import datetime, timedelta, timezone
    root, remote = ctx(tmp_path)
    remote.fail_submit = True
    first = execute(root, remote)
    assert first['status'] == 'INCOMPLETE'
    intent = git._redeploy_checkpoint(root, 'mainnet')
    remote.pending = [{
        'resource_uuid': APP, 'deployment_uuid': DEPLOYMENT,
        'created_at': (datetime.fromisoformat(intent['created_utc']) +
                       timedelta(seconds=1)).isoformat(), 'status': 'finished'}]
    remote.fail_submit = False
    second = execute(root, remote)
    assert second['status'] == 'SEH_REDEPLOY_HARNESS_COMPLETE'
    assert second['deployment_uuid'] == DEPLOYMENT
    assert remote.submit_count == 1


def test_lost_acknowledgment_never_adopts_old_build_or_wrong_revision(tmp_path):
    from datetime import datetime, timedelta
    root, remote = ctx(tmp_path)
    remote.fail_submit = True
    assert execute(root, remote)['failed_stage'] == 'forced_build_submission'
    intent = git._redeploy_checkpoint(root, 'mainnet')
    remote.pending = [{
        'resource_uuid': APP, 'deployment_uuid': DEPLOYMENT,
        'created_at': (datetime.fromisoformat(intent['created_utc']) -
                       timedelta(minutes=10)).isoformat(), 'status': 'finished'}]
    assert execute(root, remote)['failed_stage'] == 'unacknowledged_prior_deployment'
    assert remote.submit_count == 1
    remote.pending[0]['created_at'] = datetime.fromisoformat(intent['created_utc']).isoformat()
    remote.receipt_revision = 'f' * 40
    assert execute(root, remote)['failed_stage'] == 'unacknowledged_prior_deployment'
    assert remote.submit_count == 1


def test_public_edge_403_reported_without_second_build_or_long_wait(tmp_path):
    root, remote = ctx(tmp_path)
    def edge_denied(url):
        raise git.SEHGitError('Public SEH health returned HTTP 403; refusing speculative redeployment')
    first = git.redeploy_canonical_runtime(root, 'mainnet', execute=True,
              client=remote, probe=edge_denied, timeout_s=900)
    assert first['status'] == 'INCOMPLETE'
    assert first['failed_stage'] == 'public_health_edge_denied'
    assert first['http_status'] == 403
    assert remote.submit_count == 1
    second = execute(root, remote)
    assert second['status'] == 'SEH_REDEPLOY_HARNESS_COMPLETE'
    assert remote.submit_count == 1
