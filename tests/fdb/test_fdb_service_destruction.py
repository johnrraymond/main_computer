from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.fdb_control.common import coolify
from tools.fdb_control.common.coolify import CoolifyResponse, wait_for_destruction
from tools.fdb_control.common.docker_destruction import verify_service_destruction
from tools.fdb_control.common.errors import FdbControlError


class CoolifyDeletion:
    def __init__(self, *, details=(), lists=()) -> None:
        self.details = list(details)
        self.lists = list(lists)
        self.detail_reads = 0
        self.list_reads = 0

    def request(self, method, path, payload=None):
        assert method == 'GET'
        if path == '/api/v1/services/svc-deleted':
            self.detail_reads += 1
            found = self.details.pop(0) if self.details else False
            if found:
                return CoolifyResponse(True, 200, method, path, {'uuid': 'svc-deleted'})
            return CoolifyResponse(False, 404, method, path, {'message': 'not found'})
        if path == '/api/v1/services':
            self.list_reads += 1
            found = self.lists.pop(0) if self.lists else False
            return CoolifyResponse(True, 200, method, path,
                                   [{'uuid': 'svc-deleted'}] if found else [])
        raise AssertionError(path)


def test_coolify_404_alone_does_not_complete_while_listing_stale(monkeypatch):
    monkeypatch.setattr(coolify.time, 'sleep', lambda _: None)
    client = CoolifyDeletion(lists=[True, True, False, False])
    wait_for_destruction(client, 'svc-deleted', timeout_s=10, poll_s=0.1)
    assert client.detail_reads == client.list_reads == 4


def test_coolify_listing_absence_alone_does_not_complete(monkeypatch):
    monkeypatch.setattr(coolify.time, 'sleep', lambda _: None)
    client = CoolifyDeletion(details=[True, False, False], lists=[False, False, False])
    wait_for_destruction(client, 'svc-deleted', timeout_s=10, poll_s=0.1)
    assert client.detail_reads == 3


def test_coolify_missing_requires_two_consistent_polls(monkeypatch):
    monkeypatch.setattr(coolify.time, 'sleep', lambda _: None)
    client = CoolifyDeletion(details=[False, False], lists=[False, False])
    wait_for_destruction(client, 'svc-deleted', timeout_s=10, poll_s=0.1)
    assert client.list_reads == 2


def test_still_listed_times_out_and_blocks_finalize():
    client = CoolifyDeletion(lists=[True])
    with pytest.raises(FdbControlError) as exc:
        wait_for_destruction(client, 'svc-deleted', timeout_s=0)
    assert exc.value.code == 'FDB_COOLIFY_SERVICE_DESTRUCTION_TIMEOUT'


def test_coolify_list_error_fails_closed():
    class FailedList(CoolifyDeletion):
        def request(self, method, path, payload=None):
            if path == '/api/v1/services':
                return CoolifyResponse(False, 503, method, path, {'error': 'offline'})
            return super().request(method, path, payload)

    with pytest.raises(FdbControlError) as exc:
        wait_for_destruction(FailedList(), 'svc-deleted', timeout_s=0)
    assert exc.value.code == 'FDB_COOLIFY_LIST_FAILED'


def test_verify_uses_only_coolify_and_marks_post_delete_errors_as_mutating(monkeypatch):
    monkeypatch.setattr(coolify.time, 'sleep', lambda _: None)
    client = CoolifyDeletion()
    verify_service_destruction(client, {}, 'testnet', 'coolify-b', 'svc-deleted', poll_s=0.1)
    assert client.list_reads == 2

    class FailedDetail(CoolifyDeletion):
        def request(self, method, path, payload=None):
            if path == '/api/v1/services/svc-deleted':
                return CoolifyResponse(False, 500, method, path, {'error': 'offline'})
            return super().request(method, path, payload)

    with pytest.raises(FdbControlError) as exc:
        verify_service_destruction(FailedDetail(), {}, 'testnet', 'coolify-b', 'svc-deleted', timeout_s=0)
    assert exc.value.code == 'FDB_COOLIFY_SERVICE_READ_FAILED'
    assert exc.value.envelope.effect_class == 'live-deployment'


def test_no_ssh_or_docker_probe_is_required():
    from tools.fdb_control.common import docker_destruction
    assert not hasattr(docker_destruction, 'docker_project_containers_via_ssh')
    assert not hasattr(docker_destruction, 'require_docker_proof_access')
