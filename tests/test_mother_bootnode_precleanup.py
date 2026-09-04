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
