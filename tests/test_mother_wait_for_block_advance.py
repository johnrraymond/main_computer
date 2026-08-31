from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
import urllib.request

import yaml

from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)
from tools.mother_wait_for_block_advance import (
    _watch_compose,
    _watch_script,
    _watch_service_cleanup_candidates,
    resolve_watch_target,
    run_block_advance_watch,
)
from tests.test_mother_deployment_executor import TOKEN_A, _operation, _starter_document


WATCH_UUID = "watchserviceuuid123"
A1_UUID = "kgznwqhti3mp3nlw9fjoande"
C1_UUID = "j612v991dn0jnc5fd9ey9vww"
C2_UUID = "svs97fliveoalq1yxqo9mv8q"



def test_default_opener_is_urlopen_not_module() -> None:
    assert run_block_advance_watch.__kwdefaults__["opener"] is urllib.request.urlopen



def test_watch_script_uses_proof_guardian_style_python_server() -> None:
    script = _watch_script(
        network="mainnet",
        controller_id="coolify-a",
        target_node="mainneta-super1",
        service_uuid=A1_UUID,
        expected_chain_id=42424240,
        endpoint_port=8797,
    )

    assert "http.server.ThreadingHTTPServer" in script
    assert "class BlockHandler(http.server.BaseHTTPRequestHandler)" in script
    assert 'self.path not in ("/block", "/block.json")' in script
    assert "def sample_once()" in script
    assert '"docker", "ps"' in script
    assert "label=com.docker.compose.project=" in script
    assert "label=com.docker.compose.service=" in script
    assert "target_container_selected_by_compose_labels" in script
    assert "docker-cli" in script
    assert "busybox httpd" not in script
    assert "httpd -f" not in script
    assert "WATCH_SCRIPT" not in script
    assert "$" not in script


def test_watch_compose_uses_python_proof_style_command_without_shell_interpolation() -> None:
    script = _watch_script(
        network="mainnet",
        controller_id="coolify-a",
        target_node="mainneta-super1",
        service_uuid=A1_UUID,
        expected_chain_id=42424240,
        endpoint_port=8797,
    )

    compose = _watch_compose("mother-block-advance-watch-test", script, host_port=39303, endpoint_port=8797)
    service = yaml.safe_load(compose)["services"]["mother-block-advance-watch-test"]
    command = service["command"]

    assert service["image"] == "python:3.12-alpine"
    assert command[:3] == ["python", "-u", "-c"]
    assert command[-1] == script
    assert "http.server.ThreadingHTTPServer" in command[-1]
    assert "$" not in command[-1]
    assert re.search(r"(?<!\$)\$(?!\$)", command[-1]) is None


def test_stale_watch_cleanup_candidates_ignore_nested_application_rows() -> None:
    name = "mother-block-advance-watch-coolify-a-mainneta-super1-20260828t002936z"
    payload = {
        "services": [
            {
                "uuid": "parent-service-uuid",
                "name": name,
                "status": "running:unknown",
                "environment_id": 38,
                "destination_id": 0,
                "applications": [
                    {
                        "uuid": "child-application-uuid",
                        "name": name,
                        "status": "running:unknown",
                        "service_id": 333,
                        "image": "python:3.12-alpine",
                        "ports": "39303:8797",
                    }
                ],
            }
        ]
    }

    candidates = _watch_service_cleanup_candidates(
        payload,
        service_name_prefix="mother-block-advance-watch-coolify-a-mainneta-super1-",
    )

    assert candidates == [
        {
            "uuid": "parent-service-uuid",
            "name": name,
            "status": "running:unknown",
            "record_score": 140,
        }
    ]

    child_only = {
        "services": [
            {
                "uuid": "child-application-uuid",
                "name": name,
                "status": "running:unknown",
                "service_id": 333,
                "image": "python:3.12-alpine",
                "ports": "39303:8797",
            }
        ]
    }
    assert (
        _watch_service_cleanup_candidates(
            child_only,
            service_name_prefix="mother-block-advance-watch-coolify-a-mainneta-super1-",
        )
        == []
    )


class _Response:
    def __init__(self, payload, status: int = 200) -> None:  # noqa: ANN001
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self._body = json.dumps(payload).encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        return None


def _install(tmp_path: Path):
    runtime = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    operation = _operation("wait-for-block-advance-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _starter_document(),
        updated_at="2026-08-20T01:00:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    private_state = read_private_state(paths, operation=_operation("wait-for-block-advance-read"))
    evidence_dir = runtime / "mother" / "evidence" / "deployment-node-add-post-admission-observe"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    topology = {
        "kind": "main_computer.mother.deployment_node_add_post_admission_observe.v1",
        "status": "pass",
        "network": "mainnet",
        "observed_at": "2026-08-20T00:00:00Z",
        "summary": {
            "complete": True,
            "clean": True,
            "topology_current": True,
            "final_nodes": ["mainnetc-super1", "mainnetc-super2", "mainneta-super1"],
        },
        "final_topology": {
            "chain_id": 42424240,
            "nodes": ["mainnetc-super1", "mainnetc-super2", "mainneta-super1"],
            "services": {
                "mainnetc-super1": {
                    "controller_id": "coolify-c",
                    "service_uuid": C1_UUID,
                    "p2p_port": 30303,
                    "p2p_endpoint": "203.0.113.12:30303",
                },
                "mainnetc-super2": {
                    "controller_id": "coolify-c",
                    "service_uuid": C2_UUID,
                    "p2p_port": 30304,
                    "p2p_endpoint": "203.0.113.12:30304",
                },
                "mainneta-super1": {
                    "controller_id": "coolify-a",
                    "service_uuid": A1_UUID,
                    "p2p_port": 30305,
                    "p2p_endpoint": "10.116.0.3:30305",
                },
            },
            "validator_set": [],
        },
    }
    topology_path = evidence_dir / "20260820T000000Z-mainnet-topology-finalize-from-mainneta-super1.json"
    topology_path.write_text(json.dumps(topology, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    topology_info = {
        "document": topology,
        "topology": topology["final_topology"],
        "services": [
            {"node": "mainnetc-super1", "controller_id": "coolify-c", "service_uuid": C1_UUID, "p2p_port": 30303, "p2p_endpoint": "203.0.113.12:30303"},
            {"node": "mainnetc-super2", "controller_id": "coolify-c", "service_uuid": C2_UUID, "p2p_port": 30304, "p2p_endpoint": "203.0.113.12:30304"},
            {"node": "mainneta-super1", "controller_id": "coolify-a", "service_uuid": A1_UUID, "p2p_port": 30305, "p2p_endpoint": "10.116.0.3:30305"},
        ],
    }
    return runtime, private_state, topology_path, topology_info


class _WatchOpener:
    def __init__(
        self,
        block_payloads: list[dict],
        *,
        service_detail_statuses: list[str] | None = None,
        detail_http_status: int = 200,
        inventory_candidates: list[dict] | None = None,
        deploy_status: int = 200,
        deploy_payload: dict | None = None,
        diagnostic_payloads: dict[str, dict] | None = None,
        stale_services: list[dict] | None = None,
    ) -> None:
        self.requests: list[dict] = []
        self.block_payloads = list(block_payloads)
        self.service_detail_statuses = list(service_detail_statuses or ["created", "running:healthy"])
        self.detail_http_status = detail_http_status
        self.inventory_candidates = list(inventory_candidates or [])
        self.deploy_status = deploy_status
        self.deploy_payload = deploy_payload if deploy_payload is not None else {"message": "Deployment request queued."}
        self.diagnostic_payloads = dict(diagnostic_payloads or {})
        self.stale_services = list(stale_services or [])
        self.compose = ""
        self.service_name = ""

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": host, "port": parsed.port, "path": path, "query": parsed.query, "body": body})

        assert timeout > 0
        assert host == "coolify-a.invalid"

        if path.startswith("/api/"):
            assert request.headers.get("Authorization") == f"Bearer {TOKEN_A}"

        if method == "GET" and path == "/api/v1/projects/project-a/environments":
            return _Response([{"name": "mainnet", "uuid": "env-a"}])
        if method == "POST" and path == "/api/v1/services":
            assert body["project_uuid"] == "project-a"
            assert body["server_uuid"] == "server-a"
            assert body["environment_uuid"] == "env-a"
            assert body["environment_name"] == "mainnet"
            self.service_name = body["name"]
            self.compose = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
            return _Response({"uuid": WATCH_UUID})
        if method == "GET" and path == f"/api/v1/services/{WATCH_UUID}":
            if self.detail_http_status != 200:
                return _Response({"error": "missing"}, status=self.detail_http_status)
            status = self.service_detail_statuses.pop(0) if self.service_detail_statuses else "running:healthy"
            return _Response(
                {
                    "uuid": WATCH_UUID,
                    "name": self.service_name,
                    "service_status": status,
                    "status": status,
                    "docker_compose": f"services:\n  {self.service_name}:\n    container_name: {self.service_name}-{WATCH_UUID}\n    ports:\n      - '39305:8797'\n",
                    "applications": [
                        {
                            "uuid": "watchappuuid123",
                            "name": self.service_name,
                            "status": status,
                            "image": "python:3.12-alpine",
                            "ports": "39305:8797",
                        }
                    ],
                }
            )
        if method == "GET" and path == "/api/v1/services":
            return _Response({"services": [*self.stale_services, *self.inventory_candidates]})
        if method == "POST" and path == "/api/v1/deploy":
            assert parsed.query == f"uuid={WATCH_UUID}&force=true"
            return _Response(self.deploy_payload, status=self.deploy_status)
        if method == "POST" and path == f"/api/v1/services/{WATCH_UUID}/start":
            return _Response({"message": "Service starting request queued."})
        if method == "GET" and path.startswith("/api/v1/deployments"):
            return _Response(
                self.diagnostic_payloads.get(
                    path,
                    {
                        "deployments": [
                            {
                                "resource_uuid": WATCH_UUID,
                                "status": "failed",
                                "message": "simulated deployment failure",
                            }
                        ]
                    },
                )
            )
        if method == "GET" and path == "/api/v1/servers/server-a/resources":
            return _Response(
                {
                    "resources": [
                        {
                            "uuid": WATCH_UUID,
                            "name": self.service_name,
                            "status": "exited",
                        }
                    ]
                }
            )
        if method == "GET" and path in {
            f"/api/v1/services/{WATCH_UUID}/applications/watchappuuid123/logs",
            "/api/v1/applications/watchappuuid123/logs",
            f"/api/v1/services/{WATCH_UUID}/logs",
        }:
            return _Response({"logs": "compose failed before container create"})
        if method == "GET" and path == "/block":
            payload = self.block_payloads.pop(0) if self.block_payloads else {
                "ok": True,
                "chain_id": 42424240,
                "block_number": 100,
            }
            return _Response(payload)
        if method == "DELETE" and path.startswith("/api/v1/services/"):
            service_uuid = path.rsplit("/", 1)[-1]
            self.stale_services = [item for item in self.stale_services if item.get("uuid") != service_uuid]
            return _Response({"deleted": True})

        raise AssertionError(f"unexpected request: {method} {path}?{parsed.query}")


def test_resolve_watch_target_accepts_controller_slot_alias(tmp_path: Path) -> None:
    _, _, _, topology_info = _install(tmp_path)

    c2 = resolve_watch_target(
        topology_info=topology_info,
        network="mainnet",
        controller_id="coolify-c",
        target="coolify-c-2",
    )
    assert c2["node"] == "mainnetc-super2"
    assert c2["controller_id"] == "coolify-c"
    assert c2["service_uuid"] == C2_UUID
    assert c2["p2p_port"] == 30304

    a1 = resolve_watch_target(
        topology_info=topology_info,
        network="mainnet",
        controller_id="coolify-a",
        target="a1",
    )
    assert a1["node"] == "mainneta-super1"
    assert a1["controller_id"] == "coolify-a"
    assert a1["service_uuid"] == A1_UUID
    assert a1["p2p_port"] == 30305


def test_block_advance_watch_creates_endpoint_service_and_passes_when_endpoint_advances(tmp_path: Path) -> None:
    runtime, private_state, topology_path, _ = _install(tmp_path)
    opener = _WatchOpener(
        [
            {"ok": True, "chain_id": 42424240, "block_number": 100},
            {"ok": True, "chain_id": 42424240, "block_number": 101},
        ]
    )

    result = run_block_advance_watch(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        controller_id="coolify-a",
        target="coolify-a-1",
        topology_evidence=topology_path,
        max_wait_seconds=0.05,
        poll_interval_seconds=0.01,
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["target_node"] == "mainneta-super1"
    assert result["service_uuid"] == A1_UUID
    assert result["summary"]["block_advance_completed"] is True
    assert result["summary"]["baseline_block_number"] == 100
    assert result["summary"]["latest_block_number"] == 101
    assert result["summary"]["temporary_service_deleted"] is True
    assert result["summary"]["block_endpoint_observed"] is True
    assert result["summary"]["temporary_service_readback_resolved"] is True
    assert result["summary"]["temporary_service_running_before_endpoint"] is True
    assert result["block_endpoint"]["host"] == "coolify-a.invalid"
    assert result["block_endpoint"]["route_host"] == "10.116.0.3"
    assert result["block_endpoint"]["url"] == "http://coolify-a.invalid:39305/block"
    assert result["block_endpoint"]["host_port"] == 39305
    assert result["block_endpoint"]["container_port"] == 8797
    assert result["block_endpoint"]["port_source"] == "p2p_port_plus_offset"
    assert "python:3.12-alpine" in opener.compose
    assert "http.server.ThreadingHTTPServer" in opener.compose
    assert "busybox httpd" not in opener.compose
    assert "httpd -f" not in opener.compose
    assert "ports:" in opener.compose
    assert "39305:8797" in opener.compose
    compose_service = yaml.safe_load(opener.compose)["services"][opener.service_name]
    watch_command = compose_service["command"][-1]
    assert '"target_node":"mainneta-super1"' in watch_command
    assert f'"service_uuid":"{A1_UUID}"' in watch_command
    assert any(req["method"] == "POST" and req["path"] == "/api/v1/deploy" and req["query"] == f"uuid={WATCH_UUID}&force=true" for req in opener.requests)
    assert not any(req["method"] == "GET" and req["path"] == "/api/v1/deploy" for req in opener.requests)
    assert not any(req["path"].endswith("/start") for req in opener.requests)
    assert any(req["path"] == "/block" for req in opener.requests)


def test_block_advance_watch_ignores_stale_exited_status_after_queued_start(tmp_path: Path) -> None:
    runtime, private_state, topology_path, _ = _install(tmp_path)
    opener = _WatchOpener(
        [
            {"ok": True, "chain_id": 42424240, "block_number": 100},
            {"ok": True, "chain_id": 42424240, "block_number": 101},
        ],
        service_detail_statuses=["exited", "exited", "running:healthy"],
    )

    result = run_block_advance_watch(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        controller_id="coolify-a",
        target="coolify-a-1",
        topology_evidence=topology_path,
        max_wait_seconds=0.08,
        poll_interval_seconds=0.01,
        start_terminal_grace_seconds=0.08,
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["summary"]["temporary_service_running_before_endpoint"] is True
    assert result["deployment_readiness"]["statuses"][:2] == ["exited", "running:healthy"]
    assert result["deployment_readiness"]["reason"] == "service-running"
    assert result["start"]["selected_operation"] == "generic-deploy"
    assert any(req["path"] == "/block" for req in opener.requests)


def test_block_advance_watch_polls_endpoint_when_service_is_starting_unknown(tmp_path: Path) -> None:
    runtime, private_state, topology_path, _ = _install(tmp_path)
    opener = _WatchOpener(
        [
            {"ok": True, "chain_id": 42424240, "block_number": 100},
            {"ok": True, "chain_id": 42424240, "block_number": 101},
        ],
        service_detail_statuses=["exited", "starting:unknown"],
    )

    result = run_block_advance_watch(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        controller_id="coolify-a",
        target="coolify-a-1",
        topology_evidence=topology_path,
        max_wait_seconds=0.08,
        poll_interval_seconds=0.01,
        start_terminal_grace_seconds=0.08,
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["deployment_readiness"]["running"] is False
    assert result["deployment_readiness"]["endpoint_probe_eligible"] is True
    assert result["deployment_readiness"]["reason"] == "service-endpoint-probe-eligible"
    assert result["deployment_readiness"]["service_status"] == "starting:unknown"
    assert result["summary"]["temporary_service_running_before_endpoint"] is False
    assert result["summary"]["temporary_service_endpoint_probe_eligible_before_endpoint"] is True
    assert any(req["path"] == "/block" for req in opener.requests)



def test_block_advance_watch_deletes_stale_same_target_watchers_before_create(tmp_path: Path) -> None:
    runtime, private_state, topology_path, _ = _install(tmp_path)
    stale_uuid = "oldwatchserviceuuid123"
    stale_name = "mother-block-advance-watch-coolify-a-mainneta-super1-20260828t002936z"
    opener = _WatchOpener(
        [
            {"ok": True, "chain_id": 42424240, "block_number": 100},
            {"ok": True, "chain_id": 42424240, "block_number": 101},
        ],
        service_detail_statuses=["exited", "starting:unknown"],
        stale_services=[
            {
                "uuid": stale_uuid,
                "name": stale_name,
                "status": "running:unknown",
            }
        ],
    )

    result = run_block_advance_watch(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        controller_id="coolify-a",
        target="coolify-a-1",
        topology_evidence=topology_path,
        max_wait_seconds=0.08,
        poll_interval_seconds=0.01,
        start_terminal_grace_seconds=0.08,
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["stale_cleanup"]["candidate_count"] == 1
    assert result["stale_cleanup"]["remaining_count"] == 0
    assert result["stale_cleanup"]["delete_attempts"][0]["service_uuid"] == stale_uuid
    delete_index = next(i for i, req in enumerate(opener.requests) if req["method"] == "DELETE" and req["path"] == f"/api/v1/services/{stale_uuid}")
    create_index = next(i for i, req in enumerate(opener.requests) if req["method"] == "POST" and req["path"] == "/api/v1/services")
    assert delete_index < create_index
    assert any(req["path"] == "/block" for req in opener.requests)

def test_block_advance_watch_fails_when_endpoint_does_not_advance(tmp_path: Path) -> None:
    runtime, private_state, topology_path, _ = _install(tmp_path)
    opener = _WatchOpener(
        [
            {"ok": True, "chain_id": 42424240, "block_number": 100},
            {"ok": True, "chain_id": 42424240, "block_number": 100},
            {"ok": True, "chain_id": 42424240, "block_number": 100},
        ]
    )

    result = run_block_advance_watch(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        controller_id="coolify-a",
        target="mainneta-super1",
        topology_evidence=topology_path,
        max_wait_seconds=0.02,
        poll_interval_seconds=0.01,
        opener=opener,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "block-endpoint-timeout"
    assert result["summary"]["block_advance_completed"] is False
    assert result["summary"]["baseline_block_number"] == 100
    assert result["summary"]["latest_block_number"] == 100
    assert result["summary"]["block_endpoint_observed"] is True
    assert result["summary"]["temporary_service_readback_resolved"] is True
    assert result["summary"]["temporary_service_running_before_endpoint"] is True
    assert result["summary"]["temporary_service_deleted"] is False
    assert result["summary"]["temporary_service_left_for_inspection"] is True
    assert result["summary"]["endpoint_failure_diagnostics_captured"] is True
    assert result["summary"]["endpoint_failure_diagnostics_channel_count"] >= 3
    assert result["summary"]["temporary_service_status_after_endpoint_failure"] == "running:healthy"
    assert result["endpoint_failure_readback"]["phase"] == "temporary-service-post-endpoint-failure-readback"
    assert result["endpoint_failure_diagnostics"]["reason"] == "block-endpoint-timeout"
    channels = {channel["channel"]: channel for channel in result["endpoint_failure_diagnostics"]["channels"]}
    assert "deployment-list" in channels
    assert "service-application-logs" in channels
    assert channels["service-application-logs"]["log_excerpts"][0] == "compose failed before container create"
    assert any(req["method"] == "GET" and req["path"] == f"/api/v1/applications/watchappuuid123/logs" for req in opener.requests)



def test_block_advance_watch_does_not_poll_endpoint_when_created_service_row_is_not_readable(tmp_path: Path) -> None:
    runtime, private_state, topology_path, _ = _install(tmp_path)
    opener = _WatchOpener(
        [{"ok": True, "chain_id": 42424240, "block_number": 100}],
        detail_http_status=404,
        inventory_candidates=[],
    )

    result = run_block_advance_watch(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        controller_id="coolify-a",
        target="mainneta-super1",
        topology_evidence=topology_path,
        max_wait_seconds=0.02,
        poll_interval_seconds=0.01,
        opener=opener,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "created-service-row-not-readable"
    assert result["summary"]["temporary_service_created"] is True
    assert result["summary"]["temporary_service_readback_resolved"] is False
    assert result["summary"]["block_advance_wait_performed"] is False
    assert result["summary"]["block_endpoint_observed"] is False
    assert result["summary"]["temporary_service_deleted"] is False
    assert result["summary"]["temporary_service_left_for_inspection"] is True
    assert not any(req["path"] == "/block" for req in opener.requests)
    assert not any(req["path"].endswith("/start") for req in opener.requests)

def test_block_advance_watch_accepts_coolify_deployments_array_response(tmp_path: Path) -> None:
    runtime, private_state, topology_path, _ = _install(tmp_path)
    opener = _WatchOpener(
        [
            {"ok": True, "chain_id": 42424240, "block_number": 100},
            {"ok": True, "chain_id": 42424240, "block_number": 101},
        ],
        service_detail_statuses=["exited", "running:healthy"],
        deploy_payload={
            "deployments": [
                {
                    "message": "Service mother-block-advance-watch started. It could take a while, be patient.",
                    "resource_uuid": WATCH_UUID,
                }
            ]
        },
    )

    result = run_block_advance_watch(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        controller_id="coolify-a",
        target="mainneta-super1",
        topology_evidence=topology_path,
        max_wait_seconds=0.05,
        poll_interval_seconds=0.01,
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["start"]["ok"] is True
    assert result["start"]["accepted"] is True
    assert result["start"]["attempts"][0]["accepted"] is True
    assert result["start"]["attempts"][0]["payload"]["deployments"][0]["resource_uuid"] == WATCH_UUID
    assert result["summary"]["temporary_service_running_before_endpoint"] is True
    assert result["summary"]["block_endpoint_observed"] is True
    assert any(req["method"] == "POST" and req["path"] == "/api/v1/deploy" for req in opener.requests)
    assert not any(req["path"].endswith("/start") for req in opener.requests)
    assert any(req["path"] == "/block" for req in opener.requests)


def test_block_advance_watch_fails_loudly_when_generic_deploy_is_not_accepted(tmp_path: Path) -> None:
    runtime, private_state, topology_path, _ = _install(tmp_path)
    opener = _WatchOpener(
        [{"ok": True, "chain_id": 42424240, "block_number": 100}],
        deploy_status=405,
        deploy_payload={"message": "This endpoint has changed to a POST request."},
    )

    result = run_block_advance_watch(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        controller_id="coolify-a",
        target="mainneta-super1",
        topology_evidence=topology_path,
        max_wait_seconds=0.02,
        poll_interval_seconds=0.01,
        opener=opener,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "deploy-trigger-failed"
    assert result["start"]["attempts"][0]["operation"] == "generic-deploy"
    assert result["start"]["attempts"][0]["method"] == "POST"
    assert result["start"]["attempts"][0]["status"] == 405
    assert result["summary"]["block_advance_wait_performed"] is False
    assert result["summary"]["block_endpoint_observed"] is False
    assert result["summary"]["temporary_service_deleted"] is False
    assert result["summary"]["temporary_service_left_for_inspection"] is True
    assert not any(req["path"].endswith("/start") for req in opener.requests)
    assert not any(req["path"] == "/block" for req in opener.requests)



def test_block_advance_watch_collects_failure_diagnostics_and_keeps_runtime_alive(tmp_path: Path) -> None:
    runtime, private_state, topology_path, _ = _install(tmp_path)
    opener = _WatchOpener(
        [{"ok": True, "chain_id": 42424240, "block_number": 100}],
        service_detail_statuses=["exited", "exited", "exited", "exited"],
        deploy_payload={
            "deployments": [
                {
                    "message": "Service mother-block-advance-watch started. It could take a while, be patient.",
                    "resource_uuid": WATCH_UUID,
                }
            ]
        },
    )

    result = run_block_advance_watch(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        controller_id="coolify-a",
        target="mainneta-super1",
        topology_evidence=topology_path,
        max_wait_seconds=0.04,
        poll_interval_seconds=0.01,
        start_terminal_grace_seconds=0.02,
        opener=opener,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "service-terminal-after-start-grace-before-endpoint"
    diagnostics = result["deployment_readiness"]["diagnostics"]
    assert diagnostics["runtime_identity"]["selected_application_uuid"] == "watchappuuid123"
    assert diagnostics["runtime_identity"]["expected_container_name"] == f"{opener.service_name}-{WATCH_UUID}"
    channels = {channel["channel"]: channel for channel in diagnostics["channels"]}
    assert "deployment-list" in channels
    assert "service-application-logs" in channels
    assert channels["service-application-logs"]["log_excerpts"][0] == "compose failed before container create"
    assert any(req["method"] == "GET" and req["path"] == "/api/v1/deployments" for req in opener.requests)
    assert any(req["method"] == "GET" and req["path"] == f"/api/v1/applications/watchappuuid123/logs" for req in opener.requests)
    assert "http.server.ThreadingHTTPServer" in opener.compose
    assert "busybox httpd" not in opener.compose
