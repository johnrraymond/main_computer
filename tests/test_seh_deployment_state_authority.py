"""SEH operational authority is independent of Mother mutation and generation."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from tools.seh_control import deployment_state as ds, git_deployment as git, identity as st
from tests.test_seh_deleted_application_recovery import setup, PUBLISHED, NEW
from tests.test_seh_canonical_remote_metadata import APP


def _identity(root):
    return st.deployment_record(st.read(root, 'mainnet')[2], 'mainnet')


def test_local_seh_state_cas_is_authoritative_and_mother_unchanged(tmp_path):
    root, slh, remote = setup(tmp_path)
    before = st.read(root, 'mainnet')[1].binding
    identity = _identity(root)
    changed = deepcopy(identity['deployment'])
    changed['application_uuid'] = NEW
    with ds.mutation_lock(root, 'mainnet'):
        committed = ds.write(root, 'mainnet', identity, expected_uuid=APP, deployment=changed)
    assert committed['revision'] == 1
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['application_uuid'] == NEW
    assert st.read(root, 'mainnet')[1].binding == before
    with ds.mutation_lock(root, 'mainnet'):
        with pytest.raises(ds.SEHDeploymentStateError, match='changed concurrently'):
            ds.write(root, 'mainnet', identity, expected_uuid=APP, deployment=changed)
    assert ds.read(root, 'mainnet', identity)['revision'] == 1


def test_unrelated_mother_change_does_not_change_seh_deployment(tmp_path):
    root, slh, remote = setup(tmp_path)
    identity = _identity(root)
    changed = deepcopy(identity['deployment'])
    changed['application_uuid'] = NEW
    with ds.mutation_lock(root, 'mainnet'):
        ds.write(root, 'mainnet', identity, expected_uuid=APP, deployment=changed)
    st.mutate(root, 'mainnet', None, 'external-unrelated-update',
              lambda d, g, b: d['networks']['mainnet'].update(unrelated='value'))
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['application_uuid'] == NEW


def test_changed_controller_identity_refuses_operational_state(tmp_path):
    root, slh, remote = setup(tmp_path)
    identity = _identity(root)
    changed = deepcopy(identity['deployment'])
    changed['application_uuid'] = NEW
    with ds.mutation_lock(root, 'mainnet'):
        ds.write(root, 'mainnet', identity, expected_uuid=APP, deployment=changed)
    st.mutate(root, 'mainnet', None, 'different-controller-identity',
              lambda d, g, b: d['networks']['mainnet']['seh_deployment']['coolify']['controller']
              .__setitem__('url', 'https://other-controller.example.org/'))
    with pytest.raises(ds.SEHDeploymentStateError, match='Coolify identity changed'):
        git._canonical_selection(root, 'mainnet')


def test_corrupted_or_unsafe_operational_state_refuses_fallback(tmp_path):
    root, slh, remote = setup(tmp_path)
    identity = _identity(root)
    changed = deepcopy(identity['deployment'])
    changed['application_uuid'] = NEW
    with ds.mutation_lock(root, 'mainnet'):
        ds.write(root, 'mainnet', identity, expected_uuid=APP, deployment=changed)
    path = ds.directory(root, 'mainnet') / 'deployment-state-v1.json'
    path.write_text('{', encoding='utf-8')
    with pytest.raises(ds.SEHDeploymentStateError):
        git._canonical_selection(root, 'mainnet')
    path.write_text(json.dumps({'schema': 'wrong'}), encoding='utf-8')
    with pytest.raises(ds.SEHDeploymentStateError):
        git._canonical_selection(root, 'mainnet')


def test_unacknowledged_forced_build_never_reposted(tmp_path):
    root, slh, remote = setup(tmp_path)
    remote.force_failure = 405
    first = git.restore_deleted_canonical_application(
        root, 'mainnet', execute=True, state_dir=slh, client=remote, source=PUBLISHED)
    assert first['failed_stage'] == 'new_application_forced_build'
    assert git._canonical_selection(root, 'mainnet')[0]['deployment']['application_uuid'] == NEW
    assert git._recovery_checkpoint(slh)['build_submission'] == 'INTENT'
    remote.force_failure = None
    retry = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                           execute=True, client=remote)
    assert retry['status'] == 'INCOMPLETE'
    assert retry['failed_stage'] == 'unacknowledged_build_submission'
    assert len([op for op in remote.calls if op == ('POST', '/api/v1/applications/public')]) == 1
    assert len([op for op in remote.calls if op[0] == 'POST' and op[1].startswith('/api/v1/deploy?')]) == 1


def test_pending_receipt_without_submission_queues_only_existing_uuid(tmp_path):
    root, slh, remote = setup(tmp_path)
    actual = ds.write
    def fail_after_post(*args, **kwargs):
        # Test the safe resume path after operational UUID adoption but before
        # the build request: state is durable and receipt has no submission intent.
        return actual(*args, **kwargs)
    with ds.mutation_lock(root, 'mainnet'):
        identity = _identity(root)
        changed = deepcopy(identity['deployment'])
        changed['application_uuid'] = NEW
        ds.write(root, 'mainnet', identity, expected_uuid=APP, deployment=changed)
    checkpoint = {'schema': 'seh-coolify-recovery.v1', 'old_uuid': APP, 'new_uuid': NEW,
                  'mother_digest': st.read(root, 'mainnet')[1].binding.content_hash.digest,
                  'git_revision': PUBLISHED['revision']}
    from tools.slh_seh_connector import service_identity
    import base64, hashlib
    _, public = service_identity(slh, create=False)
    digest = hashlib.sha256(base64.b64decode(public)).hexdigest()
    checkpoint['key_sha256'] = digest
    git._save_recovery_checkpoint(slh, checkpoint)
    remote.created = {'domains': 'https://external.greatlibrary.io:8080'}
    remote.apps = [{'name': git.CANONICAL_APPLICATION_NAME, 'uuid': NEW,
                    'domains': 'https://external.greatlibrary.io:8080'}]
    remote.envs = [
        {'key': 'SEH_SLH_ID', 'value': 'slh-' + digest[:32]},
        {'key': 'SEH_SLH_PUBLIC_KEY_B64', 'value': public},
    ]
    original = remote.request
    remote.request = lambda method, path, body=None: (
        [] if (method, path) == ('GET', '/api/v1/deployments') else original(method, path, body))
    outcome = git.reconcile_pending_recovery(root, 'mainnet', state_dir=slh,
                                             execute=True, client=remote)
    assert outcome['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert outcome['application_uuid'] == NEW
    assert git._recovery_checkpoint(slh)['deployment_uuid'] == 'seh-build-01'
    assert ('POST', '/api/v1/applications/public') not in remote.calls
