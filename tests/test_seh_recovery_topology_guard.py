"""SEH-only Coolify topology gate and durable LMS refusal code.

Isolated fake Coolify state; no real deployment, network, or Mother mutation.
"""
from __future__ import annotations

from copy import deepcopy
import json
import time

import pytest

from main_computer.slh.seh_management_journal import ManagementJournal
from seh_mutate_harness import main
from tools.seh_control import git_deployment as git
from tests.test_seh_deleted_application_recovery import (
    NEW, PUBLISHED, DeletedCoolify, setup,
)
from tests.test_seh_canonical_remote_metadata import APP


class ChangedInventory(DeletedCoolify):
    """Changes the inventory exactly between the first and pre-mutation GETs."""
    def __init__(self, initial=(), later=()):
        super().__init__()
        self.apps = deepcopy(list(initial))
        self.later = deepcopy(list(later))
        self.list_reads = 0

    def request(self, method, path, body=None):
        if (method, path) == ('GET', '/api/v1/applications'):
            self.list_reads += 1
            if self.list_reads == 2:
                self.apps = deepcopy(self.later)
        return super().request(method, path, body)


def test_unrelated_inventory_changes_do_not_block_seh_recovery(tmp_path):
    root, slh, _ = setup(tmp_path)
    initial = [{'uuid': 'other-1', 'name': 'Some other application',
                'status': 'starting'},
               {'uuid': 'other-2', 'name': 'Another service',
                'status': 'running'}]
    later = [{'uuid': 'other-3', 'name': 'Unrelated new app'},
             {'uuid': 'other-2', 'name': 'Another service',
              'status': 'stopped'},
             {'uuid': 'other-1', 'name': 'Some other application',
              'status': 'running'}]
    remote = ChangedInventory(initial, later)
    result = git.restore_deleted_canonical_application(root, 'mainnet',
        execute=True, state_dir=slh, client=remote, source=PUBLISHED)
    assert result['status'] == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
    assert result['application_uuid'] == NEW
    assert remote.list_reads == 2
    assert remote.calls.count(('POST', '/api/v1/applications/public')) == 1


@pytest.mark.parametrize('new_app', [
    {'uuid': 'rival-seh-1', 'name': git.CANONICAL_APPLICATION_NAME},
    {'uuid': 'rival-seh-2', 'name': 'not-our-name',
     'domains': 'https://external.greatlibrary.io'},
    {'uuid': APP, 'name': 'Non-SEH old UUID reappeared'},
])
def test_concurrent_seh_or_old_uuid_changes_refuse_before_creation(tmp_path, new_app):
    root, slh, _ = setup(tmp_path)
    remote = ChangedInventory([], [new_app])
    with pytest.raises(git.SEHGitError, match='(topology changed|UUID reappeared)'):
        git.restore_deleted_canonical_application(root, 'mainnet',
            execute=True, state_dir=slh, client=remote, source=PUBLISHED)
    assert remote.created is None
    assert not any(method in ('POST','PATCH','DELETE') for method, _ in remote.calls)


def test_unrelated_change_does_not_hide_existing_candidate_change(tmp_path):
    root, slh, _ = setup(tmp_path)
    before = [{'uuid': 'candidate-a', 'name': git.CANONICAL_APPLICATION_NAME}]
    after = [{'uuid': 'candidate-b', 'name': git.CANONICAL_APPLICATION_NAME},
             {'uuid': 'unrelated', 'name': 'Another service'}]
    remote = ChangedInventory(before, after)
    with pytest.raises(git.SEHGitError, match='topology changed'):
        git.restore_deleted_canonical_application(root, 'mainnet',
            execute=True, state_dir=slh, client=remote, source=PUBLISHED)
    assert remote.created is None
    assert not any(method in ('POST','PATCH','DELETE') for method, _ in remote.calls)


def test_reordered_candidate_inventory_is_not_a_topology_change():
    first = [
        {'uuid': 'another', 'name': git.CANONICAL_APPLICATION_NAME},
        {'uuid': 'one', 'domains': 'https://external.greatlibrary.io'},
    ]
    second = [first[1] | {'status': 'starting'}, first[0] | {'status': 'running'}]
    assert (git._recovery_candidate_ids(git._recovery_candidates(first)) ==
            git._recovery_candidate_ids(git._recovery_candidates(second)))


def test_cli_returns_fixed_topology_code_in_report(tmp_path, monkeypatch, capsys):
    import seh_mutate_harness as harness
    def refused(_):
        raise git.SEHGitError('Coolify SEH application topology changed during recovery')
    monkeypatch.setattr(harness, 'run', refused)
    report = tmp_path / 'receipt.json'
    assert main(['deploy-seh', '--network', 'mainnet', '--report', str(report)]) == 2
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['status'] == 'REFUSED'
    assert result['error_code'] == 'SEH_RECOVERY_TOPOLOGY_CONFLICT'
    assert result['error_type'] == 'SEHGitError'
    assert 'SEH_RECOVERY_TOPOLOGY_CONFLICT' in capsys.readouterr().out


def test_lms_persists_specific_topology_code_after_restart(tmp_path):
    journal = ManagementJournal(tmp_path, 'mainnet')
    operation_id = 'mcel-seh-topology-conflict-0000001'
    def task():
        journal.update_stage(operation_id, 'MUTATION_EXECUTING')
        return {'status': 'REFUSED', 'error_type': 'SEHGitError',
                'error_code': 'SEH_RECOVERY_TOPOLOGY_CONFLICT',
                'remote_mutation': False}
    journal.submit(operation_id, {'action': 'deploy-seh', 'principal': 'local-machine'}, task)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        row = journal.status(operation_id)
        if row['phase'] == 'RECORDED':
            break
        time.sleep(.02)
    else:
        raise AssertionError('LMS did not record the refusal')
    reopened = ManagementJournal(tmp_path, 'mainnet').status(operation_id)
    assert reopened['result']['error_code'] == 'SEH_RECOVERY_TOPOLOGY_CONFLICT'
    assert reopened['causal_diagnostic']['error_code'] == 'SEH_RECOVERY_TOPOLOGY_CONFLICT'
    assert reopened['causal_diagnostic']['failed_stage'] == 'MUTATION_EXECUTING'
