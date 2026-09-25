from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from tools.fdb_control.add_service import (
    build_add_service_plan,
    do,
    finalize,
    plan_from_accepted,
    prep,
)
from tools.fdb_control.common.coolify import CoolifyResponse
from tools.fdb_control.common.errors import FdbControlError
from tools.fdb_control.common.models import (
    AcceptedClusterState,
    AddServiceDeployment,
    AddServiceRequest,
    ClusterIdentity,
    CoordinatorEndpoint,
    FdbContext,
    ServicePlacement,
)
from tools.fdb_control.common.service_descriptors import (
    add_service_proof_marker,
    cluster_state_proof_marker,
    render_add_service_descriptor,
)
from tools.fdb_control.common.state import publish_accepted_state, read_accepted_state
from tools.fdb_control.live_inspect import verify_accepted_cluster, verify_add_service


class FakeCoolifyClient:
    def __init__(self) -> None:
        self.services: dict[str, dict[str, object]] = {
            "svc-1": {
                "uuid": "svc-1",
                "name": "main-computer-mainnet-fdb1",
                "status": "running:healthy",
            }
        }
        self.created_payload: dict[str, object] | None = None
        self.logs: dict[str, str] = {}
        self.deployed: set[str] = set()

    def request(self, method: str, path: str, payload=None) -> CoolifyResponse:
        method = method.upper()
        if method == "GET" and path == "/api/v1/services":
            return self._ok(method, path, list(self.services.values()))
        if method == "POST" and path == "/api/v1/services":
            self.created_payload = dict(payload)
            item = {"uuid": "svc-2", "name": payload["name"], "status": "created"}
            self.services["svc-2"] = item
            compose = base64.b64decode(str(payload["docker_compose_raw"])).decode("utf-8")
            markers = re.findall(r"FDB_(?:ADD_SERVICE_PROOF|CLUSTER_STATE_PROOF)_V1 [0-9a-f]{64}", compose)
            self.logs["svc-2"] = "\n".join(markers) + ("\n" if markers else "")
            return self._ok(method, path, {"uuid": "svc-2"})
        if method in {"PATCH", "PUT"} and path.startswith("/api/v1/services/svc-2"):
            item = self.services["svc-2"]
            item["name"] = payload.get("name", item["name"])
            return self._ok(method, path, item)
        if method == "POST" and (
            path.startswith("/api/v1/deploy?")
            or path in {
                "/api/v1/services/svc-2/start",
                "/api/v1/services/svc-2/restart",
                "/api/v1/services/svc-2/deploy",
            }
        ):
            self.deployed.add("svc-2")
            self.services["svc-2"]["status"] = "running:healthy"
            return self._ok(method, path, {"ok": True})
        if method == "GET" and path in {"/api/v1/services/svc-1", "/api/v1/services/svc-2"}:
            uuid = path.rsplit("/", 1)[-1]
            item = self.services.get(uuid)
            if item is None:
                return CoolifyResponse(False, 404, method, path, {"error": "not found"})
            return self._ok(method, path, item)
        if method == "GET" and "/logs?" in path:
            uuid = path.split("/api/v1/services/", 1)[1].split("/", 1)[0]
            return self._ok(method, path, {"logs": self.logs.get(uuid, "")})
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


def _service1() -> ServicePlacement:
    return ServicePlacement(
        service_id="mainnet-fdb1",
        host_id="coolify-a",
        address="10.116.0.3",
        port=4550,
        machine_id="coolify-a",
        zone_id="coolify-a",
    )


def _accepted() -> AcceptedClusterState:
    service = _service1()
    return AcceptedClusterState(
        network="mainnet",
        generation=1,
        cluster=ClusterIdentity("main_computer_mainnet", "ac826580a04d022d"),
        services=(service,),
        coordinators=(
            CoordinatorEndpoint(
                service_id=service.service_id,
                host_id=service.host_id,
                address=service.address,
                port=service.port,
            ),
        ),
        redundancy_mode="single",
        storage_engine="ssd",
        retired=False,
    )


def _request() -> AddServiceRequest:
    return AddServiceRequest(
        network="mainnet",
        service=ServicePlacement(
            service_id="mainnet-fdb2",
            host_id="coolify-a",
            address="10.116.0.3",
            port=4551,
            machine_id="coolify-a",
            zone_id="coolify-a",
        ),
    )


def _deployment() -> AddServiceDeployment:
    return AddServiceDeployment(
        project_uuid="project-1",
        environment_name="mainnet-fdb",
        environment_uuid="environment-1",
        server_uuid="server-1",
    )


def test_add_service_plan_keeps_coordinators_unchanged_on_same_host() -> None:
    accepted = _accepted()
    plan = build_add_service_plan(accepted, _request())

    assert [item.service_id for item in plan.services] == ["mainnet-fdb1", "mainnet-fdb2"]
    assert [item.host_id for item in plan.services] == ["coolify-a", "coolify-a"]
    assert [item.endpoint for item in plan.coordinators] == ["10.116.0.3:4550"]
    assert plan.cluster_file_contents == "main_computer_mainnet:ac826580a04d022d@10.116.0.3:4550"
    assert plan.added_service.machine_id == "coolify-a"
    assert plan.added_service.zone_id == "coolify-a"


def test_add_service_descriptor_joins_existing_cluster_without_reconfiguring_it() -> None:
    plan = build_add_service_plan(_accepted(), _request())
    descriptor = render_add_service_descriptor(plan, plan.added_service)

    assert descriptor.service_name == "main-computer-mainnet-fdb2"
    assert '"10.116.0.3:4551:4551/tcp"' in descriptor.compose
    assert "configure new" not in descriptor.compose
    assert "main_computer_mainnet:ac826580a04d022d@10.116.0.3:4550" in descriptor.compose
    assert "10.116.0.3:4550" in descriptor.compose
    assert "10.116.0.3:4551" in descriptor.compose
    assert "FDB_ADD_SERVICE_PROOF_V1" in descriptor.compose
    assert "FDB_CLUSTER_STATE_PROOF_V1" in descriptor.compose


def test_add_service_round_trip_advances_generation_and_preserves_coordinators(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    publish_accepted_state(ctx, _accepted())
    client = FakeCoolifyClient()
    factory = FakeFactory(client)

    prepared = prep(ctx, _request(), _deployment(), client_factory=factory)
    operation_id = str(prepared.details["operation_id"])
    assert prepared.status == "prepared"
    assert prepared.details["accepted_generation"] == 1
    assert prepared.details["target_generation"] == 2
    assert prepared.details["coordinators_changed"] is False
    assert prepared.details["target_coordinators"] == ["10.116.0.3:4550"]

    deployed = do(ctx, "mainnet", operation_id, client_factory=factory)
    assert deployed.status == "deployed"
    assert deployed.details["service_uuid"] == "svc-2"
    assert deployed.details["coordinators_changed"] is False
    assert client.created_payload is not None

    compose = base64.b64decode(str(client.created_payload["docker_compose_raw"])).decode("utf-8")
    assert "configure new" not in compose
    assert "mainnet-fdb2-observer" in compose

    plan = build_add_service_plan(_accepted(), _request())
    client.logs["svc-2"] = add_service_proof_marker(plan, plan.added_service) + "\n" + cluster_state_proof_marker(plan) + "\n"

    verification = verify_add_service(
        ctx,
        plan,
        service_name="main-computer-mainnet-fdb2",
        service_uuid="svc-2",
        client_factory=factory,
    )
    assert verification.verified is True
    assert verification.reason == "fdb-add-service-proof-satisfied"

    finished = finalize(ctx, "mainnet", operation_id, client_factory=factory)
    assert finished.status == "finalized"
    assert finished.details["accepted_generation"] == 2
    assert finished.details["consumer_contract_changed"] is False
    assert finished.details["hub_fdb_rectification_required"] is False

    accepted = read_accepted_state(ctx, "mainnet")
    assert accepted is not None
    assert accepted.generation == 2
    assert [item.service_id for item in accepted.services] == ["mainnet-fdb1", "mainnet-fdb2"]
    assert [item.endpoint for item in accepted.coordinators] == ["10.116.0.3:4550"]
    assert accepted.redundancy_mode == "single"

    cluster_verification = verify_accepted_cluster(ctx, plan_from_accepted(accepted), client_factory=factory)
    assert cluster_verification.verified is True
    assert cluster_verification.proof_service_id == "mainnet-fdb2"
    assert cluster_verification.reason == "fdb-cluster-state-proof-satisfied"


def test_add_service_rejects_endpoint_collision() -> None:
    accepted = _accepted()
    request = AddServiceRequest(
        network="mainnet",
        service=ServicePlacement(
            service_id="mainnet-fdb2",
            host_id="coolify-a",
            address="10.116.0.3",
            port=4550,
            machine_id="coolify-a",
            zone_id="coolify-a",
        ),
    )
    with pytest.raises(FdbControlError) as exc:
        build_add_service_plan(accepted, request)
    assert exc.value.code == "FDB_SERVICE_ENDPOINT_COLLISION"


def test_add_service_rejects_already_accepted_service_id() -> None:
    accepted = _accepted()
    request = AddServiceRequest(network="mainnet", service=_service1())
    with pytest.raises(FdbControlError) as exc:
        build_add_service_plan(accepted, request)
    assert exc.value.code == "FDB_SERVICE_ALREADY_ACCEPTED"


def test_topology_derived_coordinators_expand_only_across_distinct_zones() -> None:
    first = _service1()
    second = ServicePlacement(
        service_id="mainnetb-fdb2",
        host_id="coolify-b",
        address="10.116.0.4",
        port=4550,
        machine_id="coolify-b",
        zone_id="coolify-b",
    )
    accepted_two_zones = AcceptedClusterState(
        network="mainnet",
        generation=7,
        cluster=ClusterIdentity("main_computer_mainnet", "ac826580a04d022d"),
        services=(first, second),
        coordinators=(CoordinatorEndpoint(first.service_id, first.host_id, first.address, first.port),),
        redundancy_mode="single",
        storage_engine="ssd",
        retired=False,
    )
    third = ServicePlacement(
        service_id="mainnetc-fdb3",
        host_id="coolify-c",
        address="10.116.0.2",
        port=4550,
        machine_id="coolify-c",
        zone_id="coolify-c",
    )
    plan = build_add_service_plan(accepted_two_zones, AddServiceRequest("mainnet", third))
    assert [item.service_id for item in plan.source_coordinators] == ["mainnet-fdb1"]
    assert [item.service_id for item in plan.coordinators] == [
        "mainnet-fdb1",
        "mainnetb-fdb2",
        "mainnetc-fdb3",
    ]


def test_second_service_in_same_zone_does_not_create_another_coordinator() -> None:
    plan = build_add_service_plan(_accepted(), _request())
    assert [item.service_id for item in plan.coordinators] == ["mainnet-fdb1"]
