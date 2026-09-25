from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from tools.fdb_control.common.coolify import CoolifyResponse, service_health_status
from tools.fdb_control.common.models import (
    ClusterIdentity,
    CreateClusterDeployment,
    CreateClusterRequest,
    FdbContext,
    ServicePlacement,
)
from tools.fdb_control.common.service_descriptors import birth_proof_marker, render_birth_service_descriptor
from tools.fdb_control.common.state import read_accepted_state
from tools.fdb_control.create_cluster import build_birth_plan, do, finalize, prep
from tools.fdb_control.delete_birth_service import delete_unaccepted_birth
from tools.fdb_control.live_inspect import verify_birth


class FakeCoolifyClient:
    def __init__(self, *, detail_reads_before_running: int = 0) -> None:
        self.services: dict[str, dict[str, object]] = {}
        self.created_payload: dict[str, object] | None = None
        self.deployed = False
        self.detail_reads_before_running = max(0, int(detail_reads_before_running))
        self.detail_reads = 0
        self.logs = ""

    def request(self, method: str, path: str, payload=None) -> CoolifyResponse:
        method = method.upper()
        if method == "GET" and path == "/api/v1/services":
            return self._ok(method, path, list(self.services.values()))
        if method == "POST" and path == "/api/v1/services":
            self.created_payload = dict(payload)
            item = {"uuid": "svc-1", "name": payload["name"], "status": "created"}
            self.services["svc-1"] = item
            return self._ok(method, path, {"uuid": "svc-1"})
        if method in {"PATCH", "PUT"} and path.startswith("/api/v1/services/svc-1"):
            item = self.services["svc-1"]
            item["name"] = payload.get("name", item["name"])
            return self._ok(method, path, item)
        if (method == "POST" and path.startswith("/api/v1/deploy?")) or (
            method == "POST" and path in {
                "/api/v1/services/svc-1/start",
                "/api/v1/services/svc-1/restart",
                "/api/v1/services/svc-1/deploy",
            }
        ):
            self.deployed = True
            self.services["svc-1"]["status"] = (
                "starting" if self.detail_reads_before_running else "running"
            )
            return self._ok(method, path, {"ok": True})
        if method == "DELETE" and path == "/api/v1/services/svc-1":
            self.services.pop("svc-1", None)
            return self._ok(method, path, {"ok": True})
        if method == "GET" and path == "/api/v1/services/svc-1":
            item = self.services.get("svc-1")
            if item is None:
                return CoolifyResponse(False, 404, method, path, {"error": "not found"})
            self.detail_reads += 1
            if self.deployed and self.detail_reads > self.detail_reads_before_running:
                item["status"] = "running"
            return self._ok(method, path, item)
        if method == "GET" and path.startswith("/api/v1/services/svc-1/logs?"):
            return self._ok(method, path, {"logs": self.logs})
        return CoolifyResponse(False, 404, method, path, {"error": "not found"})

    @staticmethod
    def _ok(method: str, path: str, body) -> CoolifyResponse:
        return CoolifyResponse(True, 200, method, path, body)


class FakeFactory:
    def __init__(self, client: FakeCoolifyClient) -> None:
        self.client = client

    def __call__(self, _binding):
        return self.client


def _ctx(tmp_path: Path) -> FdbContext:
    private_path = tmp_path / "runtime" / "state" / "mother" / "identity.private.yaml"
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_text(
        """
schema_version: 1
kind: main_computer.mother.private_state.v1
networks:
  mainnet:
    coolify:
      controllers:
        coolify-a:
          url: http://coolify-a.invalid:8000
          api_token: test-token
          vpn_ip: 10.116.0.3
""".lstrip(),
        encoding="utf-8",
    )
    return FdbContext.from_repo(tmp_path)


def _request() -> CreateClusterRequest:
    service = ServicePlacement(
        service_id="mainnet-fdb1",
        host_id="coolify-a",
        address="10.116.0.3",
        port=4550,
        machine_id="coolify-a",
        zone_id="coolify-a",
    )
    return CreateClusterRequest(
        network="mainnet",
        cluster=ClusterIdentity("main_computer_mainnet", "ac826580a04d022d"),
        services=(service,),
        coordinator_service_ids=(service.service_id,),
        redundancy_mode="single",
        storage_engine="ssd",
    )


def _deployment() -> CreateClusterDeployment:
    return CreateClusterDeployment(
        project_uuid="project-1",
        environment_name="mainnet-fdb",
        environment_uuid="environment-1",
        server_uuid="server-1",
    )


def test_birth_descriptor_has_real_fdb_observer_proof() -> None:
    plan = build_birth_plan(_request())
    descriptor = render_birth_service_descriptor(plan, plan.services[0])

    assert descriptor.service_name == "main-computer-mainnet-fdb1"
    assert '"10.116.0.3:4550:4550/tcp"' in descriptor.compose
    assert "configure new single ssd" in descriptor.compose
    assert "status json" in descriptor.compose
    assert "database_status" in descriptor.compose
    assert "connection_string" in descriptor.compose
    assert "10.116.0.3:4550" in descriptor.compose
    assert "quorum_reachable" in descriptor.compose
    assert "FDB_BIRTH_PROOF_V1" in descriptor.compose


def test_prep_do_verify_finalize_birth_round_trip(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    client = FakeCoolifyClient()
    factory = FakeFactory(client)

    prepared = prep(ctx, _request(), _deployment(), client_factory=factory)
    operation_id = str(prepared.details["operation_id"])
    assert prepared.status == "prepared"
    assert client.created_payload is None

    deployed = do(ctx, "mainnet", operation_id, client_factory=factory)
    assert deployed.status == "deployed"
    assert deployed.details["service_uuid"] == "svc-1"
    assert client.deployed is True
    assert client.created_payload is not None

    compose = base64.b64decode(str(client.created_payload["docker_compose_raw"])).decode("utf-8")
    assert "mainnet-fdb1-observer" in compose
    assert "status json" in compose

    plan = build_birth_plan(_request())
    client.logs = birth_proof_marker(plan, plan.services[0]) + "\n"
    observed = verify_birth(
        ctx,
        plan,
        service_name="main-computer-mainnet-fdb1",
        service_uuid="svc-1",
        client_factory=factory,
    )
    assert observed.verified is True
    assert observed.coolify_status == "running"

    finished = finalize(ctx, "mainnet", operation_id, client_factory=factory)
    assert finished.status == "finalized"
    assert finished.details["verified"] is True

    accepted = read_accepted_state(ctx, "mainnet")
    assert accepted is not None
    assert accepted.cluster == _request().cluster
    assert accepted.services == _request().services
    assert [item.endpoint for item in accepted.coordinators] == ["10.116.0.3:4550"]


def test_verifier_requires_exact_observer_proof_marker(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    client = FakeCoolifyClient()
    client.services["svc-1"] = {"uuid": "svc-1", "name": "main-computer-mainnet-fdb1", "status": "running"}
    plan = build_birth_plan(_request())

    missing = verify_birth(
        ctx,
        plan,
        service_name="main-computer-mainnet-fdb1",
        service_uuid="svc-1",
        client_factory=FakeFactory(client),
    )
    assert missing.verified is False
    assert missing.reason == "fdb-observer-proof-not-yet-observed"

    client.logs = "FDB_BIRTH_PROOF_V1 deadbeef\n"
    wrong = verify_birth(
        ctx,
        plan,
        service_name="main-computer-mainnet-fdb1",
        service_uuid="svc-1",
        client_factory=FakeFactory(client),
    )
    assert wrong.verified is False

    client.logs = birth_proof_marker(plan, plan.services[0]) + "\n"
    verified = verify_birth(
        ctx,
        plan,
        service_name="main-computer-mainnet-fdb1",
        service_uuid="svc-1",
        client_factory=FakeFactory(client),
    )
    assert verified.verified is True
    assert verified.reason == "fdb-observer-proof-satisfied"


def test_health_aggregation_never_lets_healthy_child_hide_unhealthy_child() -> None:
    payload = {
        "status": "running",
        "services": [
            {"status": "running:healthy"},
            {"status": "running:unhealthy"},
        ],
    }
    assert service_health_status(payload) == "running:unhealthy"


def test_do_waits_for_materialized_running_service_for_up_to_five_minutes(tmp_path: Path, monkeypatch) -> None:
    ctx = _ctx(tmp_path)
    client = FakeCoolifyClient(detail_reads_before_running=2)
    factory = FakeFactory(client)
    prepared = prep(ctx, _request(), _deployment(), client_factory=factory)
    operation_id = str(prepared.details["operation_id"])

    monkeypatch.setattr("tools.fdb_control.common.coolify.time.sleep", lambda _seconds: None)
    result = do(ctx, "mainnet", operation_id, client_factory=factory)

    assert result.status == "deployed"
    assert result.details["waited_for_running"] is True
    assert result.details["wait_timeout_seconds"] == 300
    assert client.detail_reads >= 3


def test_delete_unaccepted_birth_resets_operation_for_retry(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    client = FakeCoolifyClient()
    factory = FakeFactory(client)
    prepared = prep(ctx, _request(), _deployment(), client_factory=factory)
    operation_id = str(prepared.details["operation_id"])
    do(ctx, "mainnet", operation_id, client_factory=factory)
    assert "svc-1" in client.services

    result = delete_unaccepted_birth(
        ctx,
        "mainnet",
        operation_id,
        timeout_s=300.0,
        client_factory=factory,
    )

    assert result["status"] == "deleted"
    assert result["reusable_operation"] is True
    assert client.services == {}

    # The exact same prepared operation can now perform a clean birth retry.
    retried = do(ctx, "mainnet", operation_id, client_factory=factory)
    assert retried.status == "deployed"
    assert retried.details["service_uuid"] == "svc-1"
