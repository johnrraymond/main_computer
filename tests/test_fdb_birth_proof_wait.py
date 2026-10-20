"""FDB proof polling is bounded, operation-scoped and fails closed."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.fdb_control import __main__ as control

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fdb_harness_birth_wait", ROOT / "fdb_mutate_harness.py")
assert SPEC and SPEC.loader
HARNESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HARNESS)


@pytest.mark.parametrize("network,service", [("testnet", "testnetb-fdb1"), ("mainnet", "mainneta-fdb1")])
@pytest.mark.parametrize("operation", ["create-cluster", "add-service"])
def test_harness_only_waits_for_proof_for_async_deployments(tmp_path, network, service, operation):
    args = argparse.Namespace(operation=operation, network=network, service=service,
        repo_root=tmp_path, allow_full_deletion=False,
        execute_mutations=True, yes_i_know_this_mutates_fdb=True)
    h = HARNESS.Harness(args)
    h.state["operation_id"] = "fdb-test-operation-id"
    assert h.operation_inspect_cmd()[-6:] == [
        "inspect", network, "--operation-id", "fdb-test-operation-id", "--wait-timeout-seconds", "300"
    ]
    assert "--wait-timeout-seconds" not in h.inspect_cmd()
    assert "--wait-timeout-seconds" not in h.prep_cmd()
    assert "--wait-timeout-seconds" not in h.finalize_cmd()


def _verification():
    return SimpleNamespace(verified=True, service_id="testnetb-fdb1", host_id="coolify-b",
        coolify_service_name="main-computer-testnetb-fdb1", coolify_service_uuid="uuid",
        coolify_status="running", reason="fdb-observer-proof-satisfied")


@pytest.mark.parametrize("kind", ["create-cluster", "add-service"])
@pytest.mark.parametrize("requested_wait", ["0", "300"])
def test_control_inspect_forwards_bounded_proof_wait(monkeypatch, kind, requested_wait):
    captured = []
    monkeypatch.setattr(control, "require_operation", lambda *_: {
        "kind": kind, "stage": "deployed", "target": {"service_name": "main-computer-testnetb-fdb1"},
        "deployment_result": {"service_uuid": "uuid"},
    })
    monkeypatch.setattr(control, "plan_from_operation" if kind == "create-cluster" else "add_plan_from_operation", lambda *_: (object(), object()))
    def verifier(*_args, **kwargs):
        captured.append(kwargs)
        return _verification()
    monkeypatch.setattr(control, "verify_birth" if kind == "create-cluster" else "verify_add_service", verifier)
    args = control._parser().parse_args(["inspect", "testnet", "--operation-id", "op1", "--wait-timeout-seconds", requested_wait])
    result = control._dispatch(object(), args)
    assert captured[0]["wait_timeout_s"] == float(requested_wait)
    assert result["birth_verification" if kind == "create-cluster" else "add_service_verification"]["verified"] is True


@pytest.mark.parametrize("value", ["-1", "301", "nan", "inf"])
def test_control_inspect_rejects_invalid_wait(value):
    args = control._parser().parse_args(["inspect", "testnet", "--operation-id", "op1", "--wait-timeout-seconds", value])
    with pytest.raises(ValueError, match="between 0 and 300"):
        control._dispatch(object(), args)


def test_read_only_network_inspection_cannot_request_proof_wait():
    args = control._parser().parse_args(["inspect", "testnet", "--wait-timeout-seconds", "300"])
    with pytest.raises(ValueError, match="requires --operation-id"):
        control._dispatch(object(), args)


def test_remove_service_proof_inspection_cannot_request_wait(monkeypatch):
    monkeypatch.setattr(control, "require_operation", lambda *_: {"kind": "remove-service"})
    args = control._parser().parse_args(["inspect", "testnet", "--operation-id", "op1", "--wait-timeout-seconds", "300"])
    with pytest.raises(ValueError, match="only supports"):
        control._dispatch(object(), args)


def test_unverified_observer_is_still_blocked_at_harness_boundary(tmp_path):
    args = argparse.Namespace(operation="create-cluster", network="testnet", service="testnetb-fdb1",
        repo_root=tmp_path, allow_full_deletion=False,
        execute_mutations=True, yes_i_know_this_mutates_fdb=True)
    h = HARNESS.Harness(args)
    with pytest.raises(HARNESS.HarnessError, match="did not verify"):
        h._validate_step("operation-inspect", {
            "birth_verification": {"verified": False, "reason": "fdb-observer-proof-not-yet-observed"},
        })
    assert h.state["last_completed_step"] is None
