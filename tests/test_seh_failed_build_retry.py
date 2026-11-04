"""A verified failed SEH build may be retried only with a newer published SHA.

No live Coolify or Mother mutation: all remote operations use an in-memory fake.
"""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from tools.seh_control import git_deployment as git, identity as st
from tests.test_seh_deleted_application_recovery import (
    DeletedCoolify, setup as base_setup, PUBLISHED, NEW,
)

UPDATED = {**PUBLISHED, 'revision': 'b' * 40}


class FailedBuildCoolify(DeletedCoolify):
    def __init__(self):
        super().__init__()
        self.commit = PUBLISHED['revision']
        self.app_status = 'exited'
        self.second_build = 'queued'
        self.active_deployments = []
        self.fail_patch_after_update = False
        self.fail_deploy_after_submission = False
        self.retry_submit_count = 0
        self.retry_ready = False
        self.git_repository = PUBLISHED['repository']
        self.git_branch = 'main'

    def request(self, method, path, body=None):
        if method == 'GET' and path == f'/api/v1/applications/{NEW}':
            result = super().request(method, path, body)
            return {**result, 'git_commit_sha': self.commit,
                    'git_branch': self.git_branch, 'git_repository': self.git_repository,
                    'status': self.app_status, 'id': 42}
        if method == 'GET' and path == '/api/v1/deployments':
            self.calls.append((method, path))
            return deepcopy(self.active_deployments)
        if self.retry_ready and method == 'GET' and path == '/api/v1/deployments/seh-build-01':
            self.calls.append((method, path))
            return {'deployment_uuid': 'seh-build-01', 'resource_uuid': NEW,
                    'application_id': 42, 'force_rebuild': True,
                    'is_api': True, 'restart_only': False,
                    'commit': PUBLISHED['revision'], 'status': 'failed'}
        if method == 'GET' and path == '/api/v1/deployments/seh-build-02':
            self.calls.append((method, path))
            return {'deployment_uuid': 'seh-build-02', 'resource_uuid': NEW,
                    'application_id': 42, 'force_rebuild': True,
                    'is_api': True, 'restart_only': False,
                    'commit': self.commit, 'status': self.second_build}
        if method == 'PATCH' and path == f'/api/v1/applications/{NEW}':
            self.calls.append((method, path))
            assert body == {'git_commit_sha': UPDATED['revision']}
            self.commit = body['git_commit_sha']
            if self.fail_patch_after_update:
                self.fail_patch_after_update = False
                raise TimeoutError('PATCH response lost after commit')
            return {'uuid': NEW}
        if method == 'POST' and path == f'/api/v1/deploy?uuid={NEW}&force=true' and self.retry_ready:
            self.retry_submit_count += 1
            if self.retry_submit_count > 1:
                raise AssertionError('Duplicate retry deployment POST')
            self.calls.append((method, path))
            if self.fail_deploy_after_submission:
                return SimpleNamespace(ok=False, status=504, body={})
            return {'deployments': [{'deployment_uuid': 'seh-build-02',
                                     'resource_uuid': NEW}]}
        return super().request(method, path, body)


def setup(tmp_path):
    root, slh, _ = base_setup(tmp_path)
    remote = FailedBuildCoolify()
    # Initial create/queue is already acknowledged. The subsequent observation
    # of that exact deployment is a verified terminal failure.
    initial = git.restore_deleted_canonical_application(
        root, 'mainnet', execute=True, state_dir=slh,
        client=remote, source=PUBLISHED)
    assert initial['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert git._recovery_checkpoint(slh)['deployment_uuid'] == 'seh-build-01'
    remote.retry_ready = True
    return root, slh, remote


def actions(remote, op):
    return [path for method, path in remote.calls if method == op]


def test_verified_failed_build_new_published_revision_retries_same_uuid_once(tmp_path, monkeypatch):
    root, slh, remote = setup(tmp_path)
    original = st.read(root, 'mainnet')[2]
    original_key = (slh / 'seh-service-signing-key.pem').read_bytes()
    monkeypatch.setattr(st, 'mutate', lambda *a, **kw: pytest.fail('Mother mutation'))
    out = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                         execute=True, client=remote, source=UPDATED)
    assert out['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert out['application_uuid'] == NEW and out['remote_mutation'] is True
    assert out['deployment_uuid'] == 'seh-build-02'
    assert out['git_revision'] == UPDATED['revision']
    assert remote.commit == UPDATED['revision']
    receipt = git._recovery_checkpoint(slh)
    assert receipt['git_revision'] == UPDATED['revision']
    assert receipt['deployment_uuid'] == 'seh-build-02'
    assert {'deployment_uuid': 'seh-build-01',
            'git_revision': PUBLISHED['revision']} in receipt['previous_failed_builds']
    assert len(actions(remote, 'PATCH')) == 2  # initial env bind, then git pin
    assert actions(remote, 'POST').count('/api/v1/applications/public') == 1
    assert actions(remote, 'POST').count(f'/api/v1/deploy?uuid={NEW}&force=true') == 2
    assert st.read(root, 'mainnet')[2] == original
    assert (slh / 'seh-service-signing-key.pem').read_bytes() == original_key
    follow = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                            execute=True, client=remote)
    assert follow['deployment_uuid'] == 'seh-build-02'
    assert remote.retry_submit_count == 1


def test_failed_build_same_revision_must_not_rebuild(tmp_path):
    root, slh, remote = setup(tmp_path)
    out = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                         execute=True, client=remote, source=PUBLISHED)
    assert out['failed_stage'] == 'corrected_source_not_published'
    assert remote.commit == PUBLISHED['revision']
    assert remote.retry_submit_count == 0
    assert git._recovery_checkpoint(slh).get('retry_revision') is None


def test_source_not_published_gives_actionable_nonmutating_refusal(tmp_path, monkeypatch):
    root, slh, remote = setup(tmp_path)
    def unavailable(*a, **k):
        raise git.SEHGitPublicationError('source differs from origin',
                                         git_command='git publish-only-seh')
    monkeypatch.setattr(git, 'git_source', unavailable)
    out = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                         execute=True, client=remote)
    assert out['failed_stage'] == 'seh_git_source_not_published'
    assert out['git_repair_command'] == 'git publish-only-seh'
    assert remote.retry_submit_count == 0


def test_failed_build_read_only_plan_never_changes_pin(tmp_path):
    root, slh, remote = setup(tmp_path)
    out = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                         execute=False, client=remote, source=UPDATED)
    assert out['status'] == 'PLAN_ONLY'
    assert out['retry_git_revision'] == UPDATED['revision']
    assert remote.commit == PUBLISHED['revision']
    assert remote.retry_submit_count == 0


def test_lost_pin_patch_acknowledgment_resumes_without_repin_or_recreate(tmp_path):
    root, slh, remote = setup(tmp_path)
    remote.fail_patch_after_update = True
    first = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                           execute=True, client=remote, source=UPDATED)
    assert first['failed_stage'] == 'retry_git_pin_update_unconfirmed'
    assert remote.commit == UPDATED['revision']
    assert git._recovery_checkpoint(slh)['retry_revision'] == UPDATED['revision']
    second = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                            execute=True, client=remote, source=UPDATED)
    assert second['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert remote.retry_submit_count == 1
    assert actions(remote, 'POST').count('/api/v1/applications/public') == 1


def test_lost_build_post_acknowledgment_blocks_duplicate_submission(tmp_path):
    root, slh, remote = setup(tmp_path)
    remote.fail_deploy_after_submission = True
    first = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                           execute=True, client=remote, source=UPDATED)
    assert first['failed_stage'] == 'retry_build_submission_unconfirmed'
    assert git._recovery_checkpoint(slh)['build_submission'] == 'INTENT'
    again = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                           execute=True, client=remote, source=UPDATED)
    assert again['failed_stage'] == 'unacknowledged_build_submission'
    assert remote.retry_submit_count == 1


@pytest.mark.parametrize('issue', ['wrong-pin', 'wrong-trust', 'pending-job',
                                    'bad-origin', 'wrong-repo', 'wrong-branch'])
def test_invalid_retry_state_never_patches_or_rebuilds(tmp_path, issue):
    root, slh, remote = setup(tmp_path)
    if issue == 'wrong-pin':
        remote.commit = 'c' * 40
    elif issue == 'wrong-trust':
        remote.envs[-1]['value'] = 'unexpected'
    elif issue == 'pending-job':
        remote.active_deployments = [{'deployment_uuid': 'other',
                                      'resource_uuid': NEW, 'status': 'queued'}]
    elif issue == 'bad-origin':
        remote.created['domains'] = 'https://not-our-seh.io:8080'
    elif issue == 'wrong-repo':
        remote.git_repository = 'https://github.com/other/repo'
    elif issue == 'wrong-branch':
        remote.git_branch = 'unexpected'
    if issue in ('wrong-pin', 'wrong-trust', 'bad-origin', 'wrong-repo'):
        with pytest.raises(git.SEHGitError):
            git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                           execute=True, client=remote, source=UPDATED)
    else:
        out = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                             execute=True, client=remote, source=UPDATED)
        assert out['status'] == 'INCOMPLETE'
    assert remote.retry_submit_count == 0
    assert remote.commit != UPDATED['revision']


def test_failed_build_with_unverified_new_source_cannot_retry(tmp_path):
    root, slh, remote = setup(tmp_path)
    with pytest.raises(git.SEHGitError, match='invalid repository'):
        git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                       execute=True, client=remote,
                                       source={**UPDATED, 'repository': 'https://elsewhere'})
    assert remote.retry_submit_count == 0
