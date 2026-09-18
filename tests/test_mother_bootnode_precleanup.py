from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import tools.mother_bootnode_precleanup as bootnode
from tools.mother_bootnode_precleanup import (
    STATIC_NODES_PATH,
    build_bootnode_precleanup_plan,
    run_bootnode_precleanup,
)


NODE_ID_A = "a" * 128
NODE_ID_C1 = "b" * 128
NODE_ID_C2 = "c" * 128
SERVICE_A = "aaaaaaaaaaaaaaaaaaaaaaaa"
SERVICE_C1 = "bbbbbbbbbbbbbbbbbbbbbbbb"
SERVICE_C2 = "cccccccccccccccccccccccc"


class _Runner:
    def __init__(self, node_ids: dict[str, str] | None = None) -> None:
        self.calls: list[dict] = []
        self.node_ids = node_ids or {}

    def __call__(self, argv, **kwargs):  # noqa: ANN001
        args = list(argv)
        self.calls.append({"argv": args, "kwargs": dict(kwargs)})
        if args[:3] == ["docker", "run", "--rm"]:
            network = args[args.index("--network") + 1]
            container = network.removeprefix("container:")
            node_id = self.node_ids.get(container)
            if node_id is None:
                return subprocess.CompletedProcess(args, 1, "", f"unknown container: {container}")
            payload = {"jsonrpc": "2.0", "id": 1, "result": {"id": node_id, "enode": f"enode://{node_id}@127.0.0.1:30303"}}
            return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")
        if args[:3] == ["docker", "exec", "-i"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["docker", "exec"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected command: {args!r}")


class _BytesResponse:
    def __init__(self, payload: object, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = {}

    def getcode(self) -> int:
        return self.status

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def close(self) -> None:
        return None


def _json_response(payload: object, status: int = 200) -> _BytesResponse:
    return _BytesResponse(payload, status=status)


class _WriterRetryOpener:
    def __init__(
        self,
        *,
        second_attempt_healthy: bool = True,
        completion_marker: str | None = None,
    ) -> None:
        self.second_attempt_healthy = second_attempt_healthy
        self.completion_marker = completion_marker
        self.created: list[str] = []
        self.started: list[str] = []
        self.deleted: list[str] = []

    def __call__(self, request, timeout=0):  # noqa: ANN001, ARG002
        method = request.get_method()
        url = request.full_url

        if method == "GET" and url.endswith("/api/v1/projects/project-uuid/environments"):
            return _json_response([{"uuid": "environment-uuid", "name": "mainnet"}])

        if method == "POST" and url.endswith("/api/v1/services"):
            service_uuid = "writer-attempt-1" if not self.created else "writer-attempt-2"
            self.created.append(service_uuid)
            return _json_response({"uuid": service_uuid}, status=201)

        if method == "POST" and url.endswith("/start"):
            service_uuid = url.rsplit("/", 2)[-2]
            self.started.append(service_uuid)
            return _json_response({"message": "started"}, status=200)

        if method == "DELETE" and "/api/v1/services/" in url:
            service_uuid = url.rsplit("/", 1)[-1]
            self.deleted.append(service_uuid)
            return _json_response({"message": "deleted"}, status=200)

        if method == "GET" and "/applications/" in url and "/logs?" in url:
            logs = self.completion_marker or ""
            return _json_response({"logs": logs}, status=200)

        if method == "GET" and "/api/v1/services/" in url:
            service_uuid = url.rsplit("/", 1)[-1]
            if service_uuid == "writer-attempt-2" and self.second_attempt_healthy:
                status = "running:healthy"
            else:
                status = "exited"
            return _json_response(
                {
                    "uuid": service_uuid,
                    "name": "writer",
                    "status": status,
                    "applications": [
                        {
                            "uuid": f"{service_uuid}-app",
                            "name": "writer",
                            "status": status,
                        }
                    ],
                },
                status=200,
            )

        raise AssertionError(f"unexpected request: {method} {url!r}")



def _patch_coolify_writer_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = bootnode.CoolifyController(
        network="mainnet",
        controller_id="coolify-c",
        base_url="http://coolify.example",
        api_token="token",
        enabled=True,
        project_name_hint="my-first-project",
        mutation_authority="observe-only",
    )
    monkeypatch.setattr(bootnode, "_load_private_state", lambda *args, **kwargs: object())
    monkeypatch.setattr(bootnode, "_controller", lambda *args, **kwargs: controller)
    monkeypatch.setattr(
        bootnode,
        "_controller_config",
        lambda *args, **kwargs: {
            "project_uuid": "project-uuid",
            "server_uuid": "server-uuid",
        },
    )

def _service(node: str, uuid: str, node_id: str, host: str, port: int) -> dict:
    return {
        "node": node,
        "controller_id": "coolify-c",
        "service_uuid": uuid,
        "validator_route": {
            "advertised_host": host,
            "vpn_ip": host,
            "p2p_port": port,
            # The old live bug can still report a loopback enode.  The script
            # must preserve the node id but replace host/port with the route.
            "enode": f"enode://{node_id}@127.0.0.1:{port}",
        },
    }


def _write_topology(
    tmp_path: Path,
    *,
    nodes: list[str],
    services: dict[str, dict],
    subdir: str = "deployment-node-add-post-admission-observe",
    completed_at: str = "2026-09-02T21:00:00Z",
    filename: str = "20260902T210000Z-mainnet-topology-finalize.json",
    kind: str = "main_computer.mother.deployment_node_add_post_admission_observe.v1",
) -> Path:
    runtime = tmp_path / "runtime" / "state"
    evidence_dir = runtime / "mother" / "evidence" / subdir
    evidence_dir.mkdir(parents=True, exist_ok=True)
    document = {
        "kind": kind,
        "status": "pass",
        "network": "mainnet",
        "completed_at": completed_at,
        "summary": {
            "clean": True,
            "complete": True,
            "topology_current": True,
            "final_nodes": nodes,
        },
        "final_topology": {
            "chain_id": 42424240,
            "nodes": nodes,
            "services": services,
            "validator_set": ["0x" + str(i + 1).zfill(40) for i in range(len(nodes))],
        },
    }
    path = evidence_dir / filename
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return runtime


def test_plan_excludes_removed_node_and_self_and_rewrites_host_from_route() -> None:
    services = [
        _service("mainneta-super1", SERVICE_A, NODE_ID_A, "10.116.0.3", 30303),
        _service("mainnetc-super1", SERVICE_C1, NODE_ID_C1, "10.116.0.4", 30303),
        _service("mainnetc-super2", SERVICE_C2, NODE_ID_C2, "10.116.0.5", 30304),
    ]

    plan = build_bootnode_precleanup_plan(services, exclude_nodes=["mainneta-super1"])

    assert plan["status"] == "pass"
    actions = {action["node"]: action for action in plan["actions"]}
    assert set(actions) == {"mainnetc-super1", "mainnetc-super2"}
    assert actions["mainnetc-super1"]["action"] == "write-static-nodes"
    assert actions["mainnetc-super1"]["static_nodes"] == [f"enode://{NODE_ID_C2}@10.116.0.5:30304"]
    assert actions["mainnetc-super2"]["static_nodes"] == [f"enode://{NODE_ID_C1}@10.116.0.4:30303"]
    assert all("mainneta-super1" not in action.get("peer_nodes", []) for action in actions.values())
    assert all("127.0.0.1" not in node for action in actions.values() for node in action["static_nodes"])


def test_sole_survivor_deletes_static_nodes_instead_of_writing_empty_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _write_topology(
        tmp_path,
        nodes=["mainneta-super1", "mainnetc-super1"],
        services={
            "mainneta-super1": _service("mainneta-super1", SERVICE_A, NODE_ID_A, "10.116.0.3", 30303),
            "mainnetc-super1": _service("mainnetc-super1", SERVICE_C1, NODE_ID_C1, "10.116.0.4", 30303),
        },
    )
    writer_actions: list[dict] = []
    monkeypatch.setattr(bootnode, "_load_private_state", lambda *args, **kwargs: object())

    def fake_run_writer_service(private_state, *, network, action, **kwargs):  # noqa: ANN001
        writer_actions.append(dict(action))
        return {
            "status": "pass",
            "node": action["node"],
            "action": action["action"],
            "writer_service_created": True,
            "writer_service_deleted": True,
            "writer_service_preserved": False,
        }

    monkeypatch.setattr(bootnode, "_run_writer_service", fake_run_writer_service)

    result = run_bootnode_precleanup(
        network="mainnet",
        runtime_state_root=runtime,
        exclude_nodes=["mainneta-super1"],
        execute=True,
        allow_mutation=True,
        probe_node_info=False,
        write_evidence=False,
        runner=_Runner(),
    )

    assert result["status"] == "pass"
    action = result["plan"]["actions"][0]
    assert action["node"] == "mainnetc-super1"
    assert action["action"] == "delete-static-nodes"
    assert action["static_nodes"] == []
    assert "sole-survivor" in action["reason"]
    assert writer_actions == [action]
    assert result["policy"]["local_docker_static_nodes_mutation_performed"] is False


def test_execute_writes_per_node_static_nodes_without_compose_patch_or_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _write_topology(
        tmp_path,
        nodes=["mainnetc-super1", "mainnetc-super2"],
        services={
            "mainnetc-super1": _service("mainnetc-super1", SERVICE_C1, NODE_ID_C1, "10.116.0.4", 30303),
            "mainnetc-super2": _service("mainnetc-super2", SERVICE_C2, NODE_ID_C2, "10.116.0.5", 30304),
        },
    )
    writer_actions: list[dict] = []
    monkeypatch.setattr(bootnode, "_load_private_state", lambda *args, **kwargs: object())

    def fake_run_writer_service(private_state, *, network, action, **kwargs):  # noqa: ANN001
        writer_actions.append(dict(action))
        return {
            "status": "pass",
            "node": action["node"],
            "action": action["action"],
            "writer_service_created": True,
            "writer_service_deleted": True,
            "writer_service_preserved": False,
        }

    monkeypatch.setattr(bootnode, "_run_writer_service", fake_run_writer_service)

    result = run_bootnode_precleanup(
        network="mainnet",
        runtime_state_root=runtime,
        execute=True,
        allow_mutation=True,
        probe_node_info=False,
        write_evidence=False,
        runner=_Runner(),
    )

    assert result["status"] == "pass"
    assert result["policy"]["coolify_compose_patch_performed"] is False
    assert result["policy"]["coolify_restart_performed"] is False
    assert result["policy"]["local_docker_static_nodes_mutation_performed"] is False
    assert len(writer_actions) == 2
    assert writer_actions[0]["static_nodes"] == [f"enode://{NODE_ID_C2}@10.116.0.5:30304"]
    assert writer_actions[1]["static_nodes"] == [f"enode://{NODE_ID_C1}@10.116.0.4:30303"]
    assert "docker_compose_raw" not in json.dumps(result)



def test_writer_service_accepts_script_complete_proof_when_status_is_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_coolify_writer_controller(monkeypatch)
    action = {
        "node": "mainnetc-super1",
        "controller_id": "coolify-c",
        "container_name": f"mainnetc-super1-{SERVICE_C1}",
        "action": "write-static-nodes",
        "path": STATIC_NODES_PATH,
        "static_nodes": [f"enode://{NODE_ID_C2}@10.116.0.5:30304"],
        "static_node_count": 1,
        "static_nodes_sha256": "d" * 64,
    }
    completion_marker = (
        "MOTHER_BOOTNODE_PRECLEANUP_WRITER_DIAGNOSTIC "
        "phase=script_complete "
        f"node={action['node']} "
        f"target_container={action['container_name']} "
        f"operation={action['action']} "
        f"path={action['path']}"
    )
    opener = _WriterRetryOpener(
        second_attempt_healthy=False,
        completion_marker=completion_marker,
    )

    result = bootnode._run_writer_service_once(
        object(),
        network="mainnet",
        action=action,
        timeout=1.0,
        max_response_bytes=1024 * 1024,
        max_wait_seconds=0,
        poll_interval_seconds=0.01,
        opener=opener,
        sleeper=lambda _seconds: None,
        preserve_services=False,
    )

    assert result["status"] == "pass"
    assert result["reason"] is None
    assert result["writer_service_healthy"] is False
    assert result["writer_service_final_status"] == "exited"
    assert result["writer_service_proof_observed"] is True
    assert result["writer_service_proof_source"] == "script-complete-log"
    assert result["writer_service_attempt"] == 1
    assert opener.created == ["writer-attempt-1"]
    assert opener.started == ["writer-attempt-1"]
    assert opener.deleted == ["writer-attempt-1"]
    proof_poll = next(
        item
        for item in result["observations"]
        if item.get("phase") == "bootnode-precleanup-writer-completion-proof-poll"
    )
    assert proof_poll["proof_observed"] is True


def test_writer_service_retries_when_started_but_no_proof_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _write_topology(
        tmp_path,
        nodes=["mainnetc-super1"],
        services={
            "mainnetc-super1": _service("mainnetc-super1", SERVICE_C1, NODE_ID_C1, "10.116.0.4", 30303),
        },
    )
    _patch_coolify_writer_controller(monkeypatch)
    opener = _WriterRetryOpener()

    result = run_bootnode_precleanup(
        network="mainnet",
        runtime_state_root=runtime,
        execute=True,
        allow_mutation=True,
        probe_node_info=False,
        write_evidence=False,
        runner=_Runner(),
        opener=opener,
        max_wait_seconds=0,
        writer_service_attempts=2,
    )

    assert result["status"] == "pass"
    writer = result["mutation_results"][0]
    assert writer["writer_service_attempt_count"] == 2
    assert [item["writer_service_uuid"] for item in writer["writer_service_attempts"]] == [
        "writer-attempt-1",
        "writer-attempt-2",
    ]
    assert writer["writer_service_attempts"][0]["status"] == "failed"
    assert writer["writer_service_attempts"][0]["reason"] == "writer-service-not-healthy"
    assert writer["writer_service_attempts"][0]["writer_service_proof_observed"] is False
    first_debug = writer["writer_service_attempts"][0]["writer_materialization_debug"]
    assert first_debug["boundary"] == "Coolify temporary writer service start acknowledgement to Docker container/proof materialization"
    assert first_debug["failure_class_when_unhealthy_without_proof"] == "writer-service-not-materialized-or-not-proven"
    assert first_debug["start"]["response_payload_summary"]["message"] == "started"
    assert first_debug["host_manual_diagnostic"]["expected_service_directory"] == "/data/coolify/services/writer-attempt-1"
    assert first_debug["host_manual_diagnostic"]["safe_to_paste"] is True
    assert first_debug["host_manual_diagnostic"]["secrets_printed"] is False
    assert any("docker compose --env-file .env -f docker-compose.yml -p $WRITER_UUID ps -a" in command for command in first_debug["host_manual_diagnostic"]["commands"])
    poll_observation = next(
        item for item in writer["writer_service_attempts"][0]["observations"]
        if item.get("phase") == "bootnode-precleanup-writer-health-poll"
    )
    assert poll_observation["service_detail_snapshot"]["service_status"] == "exited"
    assert poll_observation["service_detail_snapshot"]["matching_service_records"][0]["uuid"] == "writer-attempt-1"
    assert writer["writer_service_attempts"][1]["status"] == "pass"
    assert opener.deleted == ["writer-attempt-1", "writer-attempt-2"]
    assert result["summary"]["writer_service_attempt_count"] == 2
    assert result["summary"]["writer_service_retry_performed"] is True


def test_writer_service_retry_is_bounded_when_no_attempt_proves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _write_topology(
        tmp_path,
        nodes=["mainnetc-super1"],
        services={
            "mainnetc-super1": _service("mainnetc-super1", SERVICE_C1, NODE_ID_C1, "10.116.0.4", 30303),
        },
    )
    _patch_coolify_writer_controller(monkeypatch)
    opener = _WriterRetryOpener(second_attempt_healthy=False)

    result = run_bootnode_precleanup(
        network="mainnet",
        runtime_state_root=runtime,
        execute=True,
        allow_mutation=True,
        probe_node_info=False,
        write_evidence=False,
        runner=_Runner(),
        opener=opener,
        max_wait_seconds=0,
        writer_service_attempts=2,
    )

    assert result["status"] == "failed"
    writer = result["mutation_results"][0]
    assert writer["writer_service_attempt_count"] == 2
    assert writer["reason"] == "writer-service-not-materialized-or-not-proven"
    assert [item["writer_service_uuid"] for item in writer["writer_service_attempts"]] == [
        "writer-attempt-1",
        "writer-attempt-2",
    ]
    assert all(item["writer_service_proof_observed"] is False for item in writer["writer_service_attempts"])
    assert opener.deleted == ["writer-attempt-1", "writer-attempt-2"]
    assert result["summary"]["writer_service_retry_performed"] is True

def test_live_node_info_supplies_node_id_when_topology_only_has_route(tmp_path: Path) -> None:
    service = {
        "node": "mainnetc-super1",
        "controller_id": "coolify-c",
        "service_uuid": SERVICE_C1,
        "validator_route": {"advertised_host": "10.116.0.4", "p2p_port": 30303},
    }
    peer = {
        "node": "mainnetc-super2",
        "controller_id": "coolify-c",
        "service_uuid": SERVICE_C2,
        "validator_route": {"advertised_host": "10.116.0.5", "p2p_port": 30304},
    }
    runtime = _write_topology(
        tmp_path,
        nodes=["mainnetc-super1", "mainnetc-super2"],
        services={"mainnetc-super1": service, "mainnetc-super2": peer},
    )
    runner = _Runner({
        f"mainnetc-super1-{SERVICE_C1}": NODE_ID_C1,
        f"mainnetc-super2-{SERVICE_C2}": NODE_ID_C2,
    })

    result = run_bootnode_precleanup(
        network="mainnet",
        runtime_state_root=runtime,
        execute=False,
        allow_mutation=False,
        probe_node_info=True,
        write_evidence=False,
        runner=runner,
    )

    assert result["status"] == "pass"
    assert result["plan"]["eligible_seed_nodes"] == ["mainnetc-super1", "mainnetc-super2"]
    assert result["plan"]["actions"][0]["static_nodes"] == [f"enode://{NODE_ID_C2}@10.116.0.5:30304"]
    assert any(call["argv"][:3] == ["docker", "run", "--rm"] for call in runner.calls)


def test_auto_discovers_latest_current_topology_across_topology_streams(tmp_path: Path) -> None:
    old_runtime = _write_topology(
        tmp_path,
        nodes=["mainneta-super1", "mainnetc-super1", "mainnetc-super2"],
        services={
            "mainneta-super1": _service("mainneta-super1", SERVICE_A, NODE_ID_A, "10.116.0.3", 30303),
            "mainnetc-super1": _service("mainnetc-super1", SERVICE_C1, NODE_ID_C1, "10.116.0.4", 30303),
            "mainnetc-super2": _service("mainnetc-super2", SERVICE_C2, NODE_ID_C2, "10.116.0.5", 30304),
        },
        subdir="deployment-node-add-post-admission-observe",
        completed_at="2026-09-02T20:33:10Z",
        filename="20260902T203310Z-mainnet-topology-finalize-from-mainnetc-super2.json",
    )
    runtime = _write_topology(
        tmp_path,
        nodes=["mainneta-super1"],
        services={
            "mainneta-super1": _service("mainneta-super1", "z5diks9yfhpdw8cvbrkmecet", NODE_ID_A, "10.116.0.3", 30303),
        },
        subdir="deployment-node-add-single-node-chain-and-hub-proof",
        completed_at="2026-09-02T23:03:16Z",
        filename="20260902T230316Z-mainneta-super1-3f1e64a7f8273629.json",
        kind="main_computer.mother.deployment_node_add_single_node_chain_and_hub_proof_evidence.v1",
    )

    assert runtime == old_runtime
    result = run_bootnode_precleanup(
        network="mainnet",
        runtime_state_root=runtime,
        execute=False,
        allow_mutation=False,
        probe_node_info=False,
        write_evidence=False,
    )

    assert result["status"] == "pass"
    assert result["topology_evidence"]["discovered"] is True
    assert "deployment-node-add-single-node-chain-and-hub-proof" in result["topology_evidence"]["path"]
    assert result["plan"]["survivor_nodes"] == ["mainneta-super1"]
    assert len(result["plan"]["actions"]) == 1
    action = result["plan"]["actions"][0]
    assert action["node"] == "mainneta-super1"
    assert action["container_name"] == "mainneta-super1-z5diks9yfhpdw8cvbrkmecet"
    assert action["action"] == "delete-static-nodes"
    assert action["path"] == STATIC_NODES_PATH
    assert action["survivor_count"] == 1
    assert action["static_nodes"] == []
    assert action["static_node_count"] == 0
    assert action["reason"] == "sole-survivor-has-no-valid-non-self-static-peers"



def test_private_state_supplies_node_ids_from_real_mother_node_shape_before_live_docker_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = {
        "node": "mainneta-super1",
        "controller_id": "coolify-a",
        "service_uuid": SERVICE_A,
        "validator_route": {"advertised_host": "10.116.0.3", "p2p_port": 30303},
    }
    peer = {
        "node": "mainnetc-super1",
        "controller_id": "coolify-c",
        "service_uuid": SERVICE_C1,
        "validator_route": {"advertised_host": "10.116.0.2", "p2p_port": 30303},
    }
    runtime = _write_topology(
        tmp_path,
        nodes=["mainneta-super1", "mainnetc-super1"],
        services={"mainneta-super1": service, "mainnetc-super1": peer},
    )
    private_keys = {
        "mainneta-super1": "0x" + "01".zfill(64),
        "mainnetc-super1": "0x" + "02".zfill(64),
    }
    node_ids = {
        "mainneta-super1": "d" * 128,
        "mainnetc-super1": "e" * 128,
    }
    private_state_document = {
        "kind": "main_computer.mother.private_state.v1",
        "schema_version": 1,
        "networks": {
            "mainnet": {
                "nodes": {
                    "mainneta-super1": {
                        "host": "coolify-a",
                        "guard_route_reservation": "mainneta-super1.guard",
                        "rpc_route_reservation": "mainneta-super1.rpc",
                        "hub_route_reservation": "mainneta-super1.hub",
                        "validator_ref": "networks.mainnet.validators.mainneta-super1",
                    },
                    "mainnetc-super1": {
                        "host": "coolify-c",
                        "guard_route_reservation": "mainnetc-super1.guard",
                        "rpc_route_reservation": "mainnetc-super1.rpc",
                        "hub_route_reservation": "mainnetc-super1.hub",
                        "validator_ref": "networks.mainnet.validators.mainnetc-super1",
                    },
                },
                "validators": {
                    "mainneta-super1": {
                        "address": "0x" + "A1" * 20,
                        "private_key": private_keys["mainneta-super1"],
                    },
                    "mainnetc-super1": {
                        "address": "0x" + "C1" * 20,
                        "private_key": private_keys["mainnetc-super1"],
                    },
                },
            },
        },
    }

    def fake_private_key_to_node_id(private_key: str) -> str:
        for node, expected_key in private_keys.items():
            if private_key.lower() == expected_key.lower():
                return node_ids[node]
        raise AssertionError("unexpected private key")

    runner = _Runner()
    fake_private_state = SimpleNamespace(
        canonical_object_bytes=json.dumps(private_state_document, sort_keys=True).encode("utf-8")
    )
    monkeypatch.setattr(bootnode, "_load_private_state", lambda *args, **kwargs: fake_private_state)
    monkeypatch.setattr(bootnode, "private_key_to_node_id", fake_private_key_to_node_id)

    result = run_bootnode_precleanup(
        network="mainnet",
        runtime_state_root=runtime,
        execute=False,
        allow_mutation=False,
        probe_node_info=True,
        write_evidence=False,
        runner=runner,
    )

    assert result["status"] == "pass"
    assert result["plan"]["eligible_seed_nodes"] == ["mainneta-super1", "mainnetc-super1"]
    assert result["plan"]["actions"][0]["static_nodes"] == [f"enode://{node_ids['mainnetc-super1']}@10.116.0.2:30303"]
    assert result["plan"]["actions"][1]["static_nodes"] == [f"enode://{node_ids['mainneta-super1']}@10.116.0.3:30303"]
    assert all(call["argv"][:3] != ["docker", "run", "--rm"] for call in runner.calls)
    assert result["private_state_node_info"]["read"]["ok"] is True
    assert {item["node"] for item in result["private_state_node_info"]["observations"] if item["ok"]} == {
        "mainneta-super1",
        "mainnetc-super1",
    }
    private_state_observations = result["private_state_node_info"]["observations"]
    assert {
        item["node"]: item["node_id_source"]
        for item in private_state_observations
        if item["ok"]
    } == {
        "mainneta-super1": "mother_private_state.validator_identity",
        "mainnetc-super1": "mother_private_state.validator_identity",
    }
    assert all("private_key" not in item.get("node_id_source", "") for item in private_state_observations)
    assert {item.get("skipped") for item in result["live_node_info_observations"]} == {True}


def test_loopback_route_is_rejected_and_multi_node_plan_is_unsafe() -> None:
    services = [
        _service("mainnetc-super1", SERVICE_C1, NODE_ID_C1, "127.0.0.1", 30303),
        _service("mainnetc-super2", SERVICE_C2, NODE_ID_C2, "10.116.0.5", 30304),
    ]

    plan = build_bootnode_precleanup_plan(services)

    assert plan["status"] == "failed"
    assert plan["clean"] is False
    assert plan["rejected_seed_nodes"][0]["node"] == "mainnetc-super1"
    assert "loopback/wildcard" in plan["rejected_seed_nodes"][0]["rejection_reasons"][0]
    actions = {action["node"]: action for action in plan["actions"]}
    assert actions["mainnetc-super2"]["action"] == "delete-static-nodes"
    assert actions["mainnetc-super2"]["reason"] == "no-eligible-non-self-survivor-peer-seeds"
