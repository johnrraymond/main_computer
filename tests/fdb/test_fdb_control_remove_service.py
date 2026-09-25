from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from tools.fdb_control.add_service import plan_from_accepted
from tools.fdb_control.common.coolify import CoolifyResponse
from tools.fdb_control.common.errors import FdbControlError
from tools.fdb_control.common.evacuation import (
    removal_complete_proof_marker,
    removal_drain_proof_marker,
)
from tools.fdb_control.common.models import (
    AcceptedClusterState,
    ClusterIdentity,
    CoordinatorEndpoint,
    FdbContext,
    RemoveServiceDeployment,
    RemoveServiceRequest,
    ServicePlacement,
)
from tools.fdb_control.common.service_descriptors import (
    cluster_state_proof_marker,
    render_remove_service_helper_descriptor,
)
from tools.fdb_control.common.state import publish_accepted_state, read_accepted_state
from tools.fdb_control.live_inspect import verify_remove_service
from tools.fdb_control.remove_service import build_remove_service_plan, do, finalize, prep


class FakeCoolifyClient:
    def __init__(
        self,
        cluster_marker: str,
        *,
        target_uuid: str = "svc-2",
        include_third: bool = False,
    ) -> None:
        self.services: dict[str, dict[str, object]] = {
            "svc-1": {"uuid": "svc-1", "name": "main-computer-mainnet-fdb1", "status": "running:healthy"},
            "svc-2": {"uuid": "svc-2", "name": "main-computer-mainnet-fdb2", "status": "running:healthy"},
        }
        if include_third:
            self.services["svc-3"] = {
                "uuid": "svc-3",
                "name": "main-computer-mainnetc-fdb3",
                "status": "running:healthy",
            }
        self.cluster_marker = cluster_marker
        self.target_uuid = target_uuid
        self.helper_uuid: str | None = None
        self.helper_drain_marker = ""
        self.helper_complete_marker = ""
        self.created_payload: dict[str, object] | None = None
        self.deleted: list[str] = []
        self.events: list[str] = []
        self.guardian_uuid: str | None = None
        self.guardian_transition_marker = ""
        self.guardian_proof_marker = ""
        self.guardian_connection = "main_computer_mainnet:newcoord123@10.116.0.3:4551"

    def request(self, method: str, path: str, payload=None) -> CoolifyResponse:
        method = method.upper()
        if method == "GET" and path.startswith("/api/v1/projects/") and path.endswith("/environments"):
            return self._ok(method, path, [{"uuid": "environment-1", "name": "mainnet-fdb"}])
        if method == "GET" and path == "/api/v1/services":
            return self._ok(method, path, list(self.services.values()))
        if method == "POST" and path == "/api/v1/services":
            self.created_payload = dict(payload)
            compose = base64.b64decode(str(payload["docker_compose_raw"])).decode("utf-8")
            if str(payload["name"]).endswith("fdb-coordinator-guardian"):
                transition = re.search(r"FDB_COORDINATOR_TRANSITION_PROOF_V1 [0-9a-f]{64}", compose)
                assert transition
                self.guardian_transition_marker = transition.group(0)
                self.guardian_uuid = "guardian-1"
                self.services[self.guardian_uuid] = {"uuid": self.guardian_uuid, "name": payload["name"], "status": "created"}
                self.events.append("create-guardian")
                return self._ok(method, path, {"uuid": self.guardian_uuid})
            drain = re.search(r"FDB_REMOVE_SERVICE_DRAINED_V1 [0-9a-f]{64}", compose)
            complete = re.search(r"FDB_REMOVE_SERVICE_PROOF_V1 [0-9a-f]{64}", compose)
            assert drain and complete
            self.helper_drain_marker = drain.group(0)
            self.helper_complete_marker = complete.group(0)
            self.helper_uuid = "helper-1"
            self.services[self.helper_uuid] = {
                "uuid": self.helper_uuid,
                "name": payload["name"],
                "status": "created",
            }
            self.events.append("create-helper")
            return self._ok(method, path, {"uuid": self.helper_uuid})
        if method in {"PATCH", "PUT"} and path.startswith("/api/v1/services/"):
            uuid = path.split("/api/v1/services/", 1)[1].split("/", 1)[0]
            item = self.services.get(uuid)
            if item is None:
                return CoolifyResponse(False, 404, method, path, {"error": "not found"})
            if payload and payload.get("docker_compose_raw"):
                compose = base64.b64decode(str(payload["docker_compose_raw"])).decode("utf-8")
                guardian = re.search(r"FDB_COORDINATOR_GUARDIAN_PROOF_V1 [0-9a-f]{64}", compose)
                if guardian:
                    self.guardian_proof_marker = guardian.group(0)
                    self.events.append("update-guardian-proof")
            return self._ok(method, path, item)
        if method == "POST" and (path.startswith("/api/v1/deploy?") or "/start" in path or "/restart" in path or "/deploy" in path):
            uuid = None
            if path.startswith("/api/v1/deploy?"):
                match = re.search(r"uuid=([^&]+)", path)
                uuid = match.group(1) if match else None
            elif "/api/v1/services/" in path:
                uuid = path.split("/api/v1/services/", 1)[1].split("/", 1)[0]
            if uuid in self.services:
                self.services[uuid]["status"] = "running:healthy"
            if uuid == "guardian-1":
                self.events.append("deploy-guardian")
            elif uuid == "helper-1":
                self.events.append("deploy-helper")
            return self._ok(method, path, {"ok": True})
        if method == "DELETE" and path.startswith("/api/v1/services/"):
            uuid = path.rsplit("/", 1)[-1]
            self.deleted.append(uuid)
            self.events.append(f"delete-{uuid}")
            self.services.pop(uuid, None)
            return self._ok(method, path, {"ok": True})
        if method == "GET" and path.startswith("/api/v1/services/") and "/logs?" not in path:
            uuid = path.rsplit("/", 1)[-1]
            item = self.services.get(uuid)
            if item is None:
                return CoolifyResponse(False, 404, method, path, {"error": "not found"})
            return self._ok(method, path, item)
        if method == "GET" and "/logs?" in path:
            uuid = path.split("/api/v1/services/", 1)[1].split("/", 1)[0]
            if uuid in {"svc-1", "svc-2", "svc-3"}:
                return self._ok(method, path, {"logs": self.cluster_marker + "\n"})
            if uuid == "guardian-1":
                if self.guardian_proof_marker:
                    return self._ok(method, path, {"logs": self.guardian_proof_marker + "\n"})
                return self._ok(method, path, {"logs": self.guardian_transition_marker + "\nFDB_COORDINATOR_CONNECTION_V1 " + self.guardian_connection + "\n"})
            if uuid == "helper-1":
                logs = self.helper_drain_marker + "\n"
                if self.target_uuid not in self.services:
                    logs += self.helper_complete_marker + "\n"
                return self._ok(method, path, {"logs": logs})
            return self._ok(method, path, {"logs": ""})
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
          project_uuid: project-a
          server_uuid: server-a
        coolify-c:
          url: http://coolify-c.invalid:8000
          api_token: test-token-c
          vpn_ip: 10.116.0.2
          project_uuid: project-c
          server_uuid: server-c
""".lstrip(),
        encoding="utf-8",
    )
    return FdbContext.from_repo(tmp_path)


def _service1() -> ServicePlacement:
    return ServicePlacement(
        service_id="mainnet-fdb1",
        host_id="coolify-a",
        address="10.116.0.3",
        port=4550,
        machine_id="coolify-a",
        zone_id="coolify-a",
    )


def _service2() -> ServicePlacement:
    return ServicePlacement(
        service_id="mainnet-fdb2",
        host_id="coolify-a",
        address="10.116.0.3",
        port=4551,
        machine_id="coolify-a",
        zone_id="coolify-a",
    )


def _accepted() -> AcceptedClusterState:
    first = _service1()
    return AcceptedClusterState(
        network="mainnet",
        generation=2,
        cluster=ClusterIdentity("main_computer_mainnet", "ac826580a04d022d"),
        services=(first, _service2()),
        coordinators=(
            CoordinatorEndpoint(
                service_id=first.service_id,
                host_id=first.host_id,
                address=first.address,
                port=first.port,
            ),
        ),
        redundancy_mode="single",
        storage_engine="ssd",
        retired=False,
    )


def _service3() -> ServicePlacement:
    return ServicePlacement(
        service_id="mainnetc-fdb3",
        host_id="coolify-c",
        address="10.116.0.2",
        port=4550,
        machine_id="coolify-c",
        zone_id="coolify-c",
    )


def _accepted_three() -> AcceptedClusterState:
    accepted = _accepted()
    return AcceptedClusterState(
        network=accepted.network,
        generation=3,
        cluster=accepted.cluster,
        services=accepted.services + (_service3(),),
        coordinators=accepted.coordinators,
        redundancy_mode=accepted.redundancy_mode,
        storage_engine=accepted.storage_engine,
        retired=False,
    )


def _deployment() -> RemoveServiceDeployment:
    return RemoveServiceDeployment(
        project_uuid="project-1",
        environment_name="mainnet-fdb",
        environment_uuid="environment-1",
        server_uuid="server-1",
    )


def test_remove_plan_preserves_coordinator_and_only_drops_target() -> None:
    plan = build_remove_service_plan(_accepted(), RemoveServiceRequest("mainnet", "mainnet-fdb2"))
    assert [item.service_id for item in plan.services] == ["mainnet-fdb1"]
    assert [item.endpoint for item in plan.coordinators] == ["10.116.0.3:4550"]
    assert plan.removed_service.service_id == "mainnet-fdb2"
    assert plan.removed_service.endpoint == "10.116.0.3:4551"
    assert plan.cluster_file_contents == "main_computer_mainnet:ac826580a04d022d@10.116.0.3:4550"


def test_remove_coordinator_derives_surviving_replacement_without_manual_prestep() -> None:
    plan = build_remove_service_plan(_accepted(), RemoveServiceRequest("mainnet", "mainnet-fdb1"))
    assert [item.service_id for item in plan.services] == ["mainnet-fdb2"]
    assert [item.service_id for item in plan.source_coordinators] == ["mainnet-fdb1"]
    assert [item.service_id for item in plan.coordinators] == ["mainnet-fdb2"]
    assert plan.source_cluster_file_contents == "main_computer_mainnet:ac826580a04d022d@10.116.0.3:4550"


def test_remove_helper_uses_blocking_exclude_and_terminal_fdb_proof() -> None:
    plan = build_remove_service_plan(_accepted(), RemoveServiceRequest("mainnet", "mainnet-fdb2"))
    descriptor = render_remove_service_helper_descriptor(
        plan,
        helper_service_name="main-computer-mainnet-remove-mainnet-fdb2-test",
    )
    assert "exclude 10.116.0.3:4551" in descriptor.compose
    assert "FDB_REMOVE_SERVICE_DRAINED_V1" in descriptor.compose
    assert "FDB_REMOVE_SERVICE_PROOF_V1" in descriptor.compose
    assert "10.116.0.3:4550" in descriptor.compose
    assert "10.116.0.3:4551" in descriptor.compose
    assert "include 10.116.0.3:4551" in descriptor.compose


def test_remove_round_trip_excludes_deletes_verifies_and_advances_generation(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    accepted = _accepted()
    publish_accepted_state(ctx, accepted)
    cluster_marker = cluster_state_proof_marker(plan_from_accepted(accepted))
    client = FakeCoolifyClient(cluster_marker)
    factory = FakeFactory(client)

    prepared = prep(
        ctx,
        RemoveServiceRequest("mainnet", "mainnet-fdb2"),
        _deployment(),
        client_factory=factory,
    )
    operation_id = str(prepared.details["operation_id"])
    assert prepared.status == "prepared"
    assert prepared.details["accepted_generation"] == 2
    assert prepared.details["target_generation"] == 3
    assert prepared.details["safe_withdrawal"] == "coordinator-first-then-fdbcli-exclude-blocking"

    removed = do(ctx, "mainnet", operation_id, client_factory=factory)
    assert removed.status == "removed"
    assert removed.details["safe_withdrawal_verified"] is True
    assert removed.details["endpoint_exclusion_cleared"] is True
    assert "svc-2" not in client.services
    assert "helper-1" in client.services

    plan = build_remove_service_plan(accepted, RemoveServiceRequest("mainnet", "mainnet-fdb2"))
    assert client.helper_drain_marker == removal_drain_proof_marker(plan)
    assert client.helper_complete_marker == removal_complete_proof_marker(plan)

    verification = verify_remove_service(
        ctx,
        plan,
        target_service_name="main-computer-mainnet-fdb2",
        target_service_uuid="svc-2",
        helper_service_name=str(client.services["helper-1"]["name"]),
        helper_service_uuid="helper-1",
        client_factory=factory,
    )
    assert verification.verified is True
    assert verification.target_status == "missing"
    assert verification.reason == "fdb-remove-service-proof-satisfied"

    finished = finalize(ctx, "mainnet", operation_id, client_factory=factory)
    assert finished.status == "finalized"
    assert finished.details["accepted_generation"] == 3
    assert finished.details["coordinators_changed"] is False
    assert finished.details["consumer_contract_changed"] is False
    assert finished.details["hub_fdb_rectification_required"] is False
    assert "helper-1" not in client.services

    current = read_accepted_state(ctx, "mainnet")
    assert current is not None
    assert current.generation == 3
    assert [item.service_id for item in current.services] == ["mainnet-fdb1"]
    assert [item.endpoint for item in current.coordinators] == ["10.116.0.3:4550"]


def test_remove_plan_supports_three_to_two_without_changing_coordinators() -> None:
    plan = build_remove_service_plan(
        _accepted_three(),
        RemoveServiceRequest("mainnet", "mainnetc-fdb3"),
    )
    assert [item.service_id for item in plan.services] == ["mainnet-fdb1", "mainnet-fdb2"]
    assert [item.endpoint for item in plan.coordinators] == ["10.116.0.3:4550"]
    assert plan.removed_service.service_id == "mainnetc-fdb3"
    assert plan.removed_service.endpoint == "10.116.0.2:4550"


def test_remove_three_to_two_round_trip_deletes_exact_third_service_and_advances_generation(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    accepted = _accepted_three()
    publish_accepted_state(ctx, accepted)
    cluster_marker = cluster_state_proof_marker(plan_from_accepted(accepted))
    client = FakeCoolifyClient(cluster_marker, target_uuid="svc-3", include_third=True)
    factory = FakeFactory(client)

    prepared = prep(
        ctx,
        RemoveServiceRequest("mainnet", "mainnetc-fdb3"),
        _deployment(),
        client_factory=factory,
    )
    operation_id = str(prepared.details["operation_id"])
    assert prepared.status == "prepared"
    assert prepared.details["accepted_generation"] == 3
    assert prepared.details["target_generation"] == 4
    assert prepared.details["host_id"] == "coolify-c"
    assert prepared.details["service_endpoint"] == "10.116.0.2:4550"

    removed = do(ctx, "mainnet", operation_id, client_factory=factory)
    assert removed.status == "removed"
    assert "svc-3" not in client.services
    assert "svc-1" in client.services
    assert "svc-2" in client.services
    assert client.deleted == ["svc-3"]

    finished = finalize(ctx, "mainnet", operation_id, client_factory=factory)
    assert finished.status == "finalized"
    assert finished.details["accepted_generation"] == 4
    assert finished.details["coordinators_changed"] is False
    assert client.deleted == ["svc-3", "helper-1"]

    current = read_accepted_state(ctx, "mainnet")
    assert current is not None
    assert current.generation == 4
    assert [item.service_id for item in current.services] == ["mainnet-fdb1", "mainnet-fdb2"]
    assert [item.endpoint for item in current.coordinators] == ["10.116.0.3:4550"]


def test_remove_current_coordinator_three_to_two_moves_overlay_before_deleting_service(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    accepted = _accepted_three()
    publish_accepted_state(ctx, accepted)
    cluster_marker = cluster_state_proof_marker(plan_from_accepted(accepted))
    client = FakeCoolifyClient(cluster_marker, target_uuid="svc-1", include_third=True)
    factory = FakeFactory(client)

    prepared = prep(
        ctx,
        RemoveServiceRequest("mainnet", "mainnet-fdb1"),
        _deployment(),
        client_factory=factory,
    )
    operation_id = str(prepared.details["operation_id"])
    assert prepared.details["coordinators_changed"] is True
    assert prepared.details["target_coordinators"] == ["10.116.0.3:4551"]

    removed = do(ctx, "mainnet", operation_id, client_factory=factory)
    assert removed.status == "removed"
    assert client.events.index("deploy-guardian") < client.events.index("create-helper")
    assert client.events.index("create-helper") < client.events.index("delete-svc-1")

    finished = finalize(ctx, "mainnet", operation_id, client_factory=factory)
    assert finished.status == "finalized"
    current = read_accepted_state(ctx, "mainnet")
    assert current is not None
    assert current.generation == 4
    assert current.cluster.cluster_id == "newcoord123"
    assert [item.service_id for item in current.services] == ["mainnet-fdb2", "mainnetc-fdb3"]
    assert [item.service_id for item in current.coordinators] == ["mainnet-fdb2"]


def test_remove_current_coordinator_moves_overlay_before_deleting_service(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    accepted = _accepted()
    publish_accepted_state(ctx, accepted)
    cluster_marker = cluster_state_proof_marker(plan_from_accepted(accepted))
    client = FakeCoolifyClient(cluster_marker, target_uuid="svc-1")
    factory = FakeFactory(client)

    prepared = prep(
        ctx,
        RemoveServiceRequest("mainnet", "mainnet-fdb1"),
        _deployment(),
        client_factory=factory,
    )
    operation_id = str(prepared.details["operation_id"])
    assert prepared.details["coordinators_changed"] is True
    assert prepared.details["target_coordinators"] == ["10.116.0.3:4551"]

    removed = do(ctx, "mainnet", operation_id, client_factory=factory)
    assert removed.status == "removed"
    assert removed.details["coordinators_changed"] is True
    assert "svc-1" not in client.services
    assert client.events.index("deploy-guardian") < client.events.index("create-helper")
    assert client.events.index("create-helper") < client.events.index("delete-svc-1")

    finished = finalize(ctx, "mainnet", operation_id, client_factory=factory)
    assert finished.status == "finalized"
    assert finished.details["coordinators_changed"] is True
    assert finished.details["consumer_contract_changed"] is True
    assert finished.details["hub_fdb_rectification_required"] is True

    current = read_accepted_state(ctx, "mainnet")
    assert current is not None
    assert current.generation == 3
    assert current.cluster.description == accepted.cluster.description
    assert current.cluster.cluster_id == "newcoord123"
    assert [item.service_id for item in current.services] == ["mainnet-fdb2"]
    assert [item.service_id for item in current.coordinators] == ["mainnet-fdb2"]


def test_remove_refuses_to_retire_final_service() -> None:
    first = _service1()
    accepted = AcceptedClusterState(
        network="mainnet",
        generation=9,
        cluster=ClusterIdentity("main_computer_mainnet", "ac826580a04d022d"),
        services=(first,),
        coordinators=(),
        redundancy_mode="single",
        storage_engine="ssd",
        retired=False,
    )
    with pytest.raises(FdbControlError) as exc:
        build_remove_service_plan(accepted, RemoveServiceRequest("mainnet", first.service_id))
    assert exc.value.code == "FDB_REMOVE_SERVICE_TOPOLOGY_UNSUPPORTED"
