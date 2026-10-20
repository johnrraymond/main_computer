"""Hub bootstrap's FDB child must not forge a birth or accept a running parent."""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

import fdb_mutate_harness
from tools.fdb_control.common.coolify import CoolifyResponse, wait_for_running
from tools.fdb_control.common.errors import FdbControlError
from tools.fdb_control.common import state
from tools.fdb_control import create_cluster, observer_diagnostics


def args(root: Path):
    return argparse.Namespace(
        operation="create-cluster", network="testnet", service="testnetb-fdb1",
        repo_root=root, allow_full_deletion=False,
        execute_mutations=True, yes_i_know_this_mutates_fdb=True,
    )


def test_orphan_unaccepted_birth_reuses_frozen_cluster_and_operation(tmp_path, monkeypatch):
    h = fdb_mutate_harness.Harness(args(tmp_path))
    h.state.update({"last_completed_step": "do", "operation_id": "op1"})
    h._write_state()
    recorded = []
    monkeypatch.setattr(state, "read_accepted_state", lambda *_: None)
    monkeypatch.setattr(create_cluster, "plan_from_operation", lambda ctx, net, op: (
        SimpleNamespace(services=[SimpleNamespace(service_id="testnetb-fdb1")],
                        cluster=SimpleNamespace(cluster_id=h.state["cluster_id"])),
        {"kind": "create-cluster", "stage": "deployed"},
    ))
    monkeypatch.setattr(observer_diagnostics, "diagnose_observer", lambda *a: {"status":"service-not-found"})
    monkeypatch.setattr(state, "update_operation", lambda *a, **kw: recorded.append(kw))
    h._recover_deleted_unaccepted_birth()
    assert h.state["last_completed_step"] == "prep"
    assert h.state["operation_id"] == "op1"
    assert h.state["birth_service_recovery"] == "missing-service-reuse-frozen-birth"
    assert recorded == [{"stage": "prepared", "deployment_result": None,
                         "cleanup_result": {"status": "externally-deleted", "service_name": "main-computer-testnetb-fdb1"}}]


def test_existing_service_does_not_get_rewound(tmp_path, monkeypatch):
    h = fdb_mutate_harness.Harness(args(tmp_path))
    h.state.update({"last_completed_step": "do", "operation_id": "op1"})
    monkeypatch.setattr(state, "read_accepted_state", lambda *_: None)
    monkeypatch.setattr(create_cluster, "plan_from_operation", lambda ctx, net, op: (
        SimpleNamespace(services=[SimpleNamespace(service_id="testnetb-fdb1")],
                        cluster=SimpleNamespace(cluster_id=h.state["cluster_id"])),
        {"kind": "create-cluster", "stage": "deployed"},
    ))
    monkeypatch.setattr(observer_diagnostics, "diagnose_observer", lambda *a: {"status":"observed"})
    monkeypatch.setattr(state, "update_operation", lambda *a, **kw: pytest.fail("must preserve deployed FDB"))
    h._recover_deleted_unaccepted_birth()
    assert h.state["last_completed_step"] == "do"


def test_accepted_fdb_cannot_be_rewound(tmp_path, monkeypatch):
    h = fdb_mutate_harness.Harness(args(tmp_path))
    h.state.update({"last_completed_step": "do", "operation_id": "op1"})
    monkeypatch.setattr(state, "read_accepted_state", lambda *_: {"accepted": True})
    monkeypatch.setattr(create_cluster, "plan_from_operation", lambda *a: pytest.fail("must not inspect a frozen op"))
    h._recover_deleted_unaccepted_birth()
    assert h.state["last_completed_step"] == "do"


class FakeMaterialization:
    def __init__(self, *, partial=False):
        self.partial = partial
        self.force_dispatches = 0
        self.requests = []

    def request(self, method, path, payload=None):
        self.requests.append((method, path, payload))
        if method == "POST":
            assert path == "/api/v1/deploy"
            assert payload == {"uuid":"svc1", "force":True}
            self.force_dispatches += 1
            return CoolifyResponse(True, 202, method, path, {"queued": True})
        assert method == "GET"
        assert path == "/api/v1/services/svc1"
        running = self.force_dispatches > 0
        services = [
            {"name":"testnetb-fdb1", "status":"running" if running or self.partial else "exited"},
            {"name":"testnetb-fdb1-observer", "status":"running" if running else "exited"},
        ]
        return CoolifyResponse(True, 200, method, path, {"status":"running", "applications":services})


def test_parent_running_children_exited_never_counts_as_deployed():
    client = FakeMaterialization()
    with pytest.raises(FdbControlError, match="did not materialize"):
        wait_for_running(client, "svc1", timeout_s=0,
                         required_subservices=("testnetb-fdb1", "testnetb-fdb1-observer"))
    assert client.force_dispatches == 0


def test_one_controlled_forced_dispatch_recovers_empty_container_stack():
    client = FakeMaterialization()
    result = wait_for_running(client, "svc1", timeout_s=2, poll_s=0,
                             required_subservices=("testnetb-fdb1", "testnetb-fdb1-observer"),
                             retry_unmaterialized_after_s=0)
    assert result["applications"][0]["status"] == "running"
    assert client.force_dispatches == 1


def test_partially_running_stack_is_not_restarted_by_auto_recovery():
    client = FakeMaterialization(partial=True)
    with pytest.raises(FdbControlError, match="did not materialize"):
        wait_for_running(client, "svc1", timeout_s=0,
                         required_subservices=("testnetb-fdb1", "testnetb-fdb1-observer"),
                         retry_unmaterialized_after_s=0)
    assert client.force_dispatches == 0
