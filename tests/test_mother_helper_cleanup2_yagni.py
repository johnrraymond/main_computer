from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)
from tools.mother_helper_cleanup2_yagni import run_helper_cleanup2_yagni
from tests.test_mother_deployment_executor import TOKEN_C, _operation, _starter_document


SERVICE_UUID = "hbu0v62iaea6uuy2360x29ba"
CLEANUP2_UUID = "cleanup2serviceuuid"


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
    operation = _operation("helper-cleanup2-yagni-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _starter_document(),
        updated_at="2026-08-20T01:00:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    private_state = read_private_state(paths, operation=_operation("helper-cleanup2-yagni-read"))
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
            "final_nodes": ["mainnetc-super2"],
        },
        "final_topology": {
            "chain_id": 42424240,
            "nodes": ["mainnetc-super2"],
            "services": {
                "mainnetc-super2": {
                    "controller_id": "coolify-c",
                    "service_uuid": SERVICE_UUID,
                }
            },
            "validator_set": [],
        },
    }
    topology_path = evidence_dir / "20260820T000000Z-mainnet-topology-finalize-from-mainnetc-super2.json"
    topology_path.write_text(json.dumps(topology, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return runtime, private_state, topology_path


def _compose() -> str:
    return """services:
  mainnetc-super2:
    image: hyperledger/besu:latest
    command:
      - besu
  mother-super-node-fdb:
    image: foundationdb/foundationdb:7.4.6
  mother-super-node-hub:
    image: mainnetc-super2-hub:c56a20a0fd05
  mother-replica-sync-guardian:
    image: python:3.12-alpine
  mother-add-node-validator-activation-guardian:
    image: python:3.12-alpine
  mother-genesis-proof-guardian:
    image: python:3.12-alpine
  mother-node-remove-voter-mainnetc_super1:
    image: python:3.12-alpine
"""


class _Cleanup2Opener:
    def __init__(self, *, service_detail_as_json_string: bool = False, service_detail_status: int = 200) -> None:
        self.requests: list[dict] = []
        self.patched_compose: str | None = None
        self.cleanup2_compose: str | None = None
        self.service_detail_as_json_string = service_detail_as_json_string
        self.service_detail_status = service_detail_status

    def _parent_service_payload(self):
        payload = {
            "uuid": SERVICE_UUID,
            "name": "mainnetc-super2",
            "status": "degraded:unhealthy",
            "applications": [
                {
                    "name": "mainnetc-super2",
                    "uuid": "koxea8fwva9froj4jcese857",
                    "status": "running:healthy",
                    "image": "hyperledger/besu:latest",
                },
                {
                    "name": "mother-super-node-fdb",
                    "uuid": "fdbbbbbbbbbbbbbbbbbbbbbb",
                    "status": "running:healthy",
                    "image": "foundationdb/foundationdb:7.4.6",
                },
                {
                    "name": "mother-add-node-validator-activation-guardian",
                    "uuid": "fn56xb2ew0z1mf3fwcz4n56s",
                    "status": "exited",
                    "image": "python:3.12-alpine",
                },
                {
                    "name": "mother-genesis-proof-guardian",
                    "uuid": "q10fqh85lqjon9r89rq5ni7x",
                    "status": "exited",
                    "image": "python:3.12-alpine",
                },
                {
                    "name": "mother-node-remove-voter-mainnetc_super1",
                    "uuid": "removevoterhelperuuid",
                    "status": "exited",
                    "image": "python:3.12-alpine",
                },
            ],
            "docker_compose_raw": base64.b64encode(_compose().encode("utf-8")).decode("ascii"),
        }
        if self.service_detail_as_json_string:
            return json.dumps(payload)
        return payload

    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        host = parsed.hostname or ""
        path = parsed.path
        method = request.get_method()
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append({"method": method, "host": host, "path": path, "query": parsed.query, "body": body})

        assert host == "coolify-c.invalid"
        assert request.headers.get("Authorization") == f"Bearer {TOKEN_C}"
        assert timeout > 0

        if method == "GET" and path == f"/api/v1/services/{SERVICE_UUID}":
            if self.service_detail_status == 404:
                return _Response({"message": "Service not found"}, status=404)
            return _Response(self._parent_service_payload())
        if method == "PATCH" and path == f"/api/v1/services/{SERVICE_UUID}":
            assert body["instant_deploy"] is False
            self.patched_compose = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
            return _Response({"uuid": SERVICE_UUID, "updated": True})
        if method == "GET" and path == "/api/v1/projects/project-c/environments":
            return _Response([{"name": "mainnet", "uuid": "env-c"}])
        if method == "POST" and path == "/api/v1/services":
            assert body["project_uuid"] == "project-c"
            assert body["server_uuid"] == "server-c"
            assert body["environment_uuid"] == "env-c"
            self.cleanup2_compose = base64.b64decode(body["docker_compose_raw"]).decode("utf-8")
            return _Response({"uuid": CLEANUP2_UUID})
        if method == "POST" and path == f"/api/v1/services/{CLEANUP2_UUID}/start":
            return _Response({"message": "Service starting request queued."})
        if method == "GET" and path == f"/api/v1/services/{CLEANUP2_UUID}":
            return _Response({"uuid": CLEANUP2_UUID, "name": "mother-helper-cleanup2-coolify-c", "status": "exited"})
        if method == "DELETE" and path == f"/api/v1/services/{CLEANUP2_UUID}":
            return _Response({"deleted": True})

        raise AssertionError(f"unexpected request: {method} {path}?{parsed.query}")


def test_cleanup2_yagni_patches_helpers_and_runs_one_host_local_cleanup2_service(tmp_path: Path) -> None:
    runtime, private_state, topology_path = _install(tmp_path)
    opener = _Cleanup2Opener()

    result = run_helper_cleanup2_yagni(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology_path,
        mode="execute",
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["summary"]["topology_imported"] is True
    assert result["summary"]["targeted_super_node_count"] == 1
    assert result["summary"]["targeted_helper_service_count"] == 3
    assert result["summary"]["compose_patch_accepted_count"] == 1
    assert result["summary"]["cleanup2_controller_count"] == 1
    assert result["summary"]["cleanup2_passed"] is True
    assert result["cleanup2_steps"][0]["completion"]["completed"] is True
    assert result["cleanup2_steps"][0]["completion"]["reason"] == "cleanup2-exited"
    assert result["cleanup2_steps"][0]["health"] is None
    assert result["summary"]["parent_redeploy_performed"] is False
    assert result["summary"]["parent_restart_performed"] is False
    assert result["summary"]["child_public_api_restart_attempted"] is False
    assert result["summary"]["temporary_helper_apply_service_created"] is False
    assert result["summary"]["post_restart_health_poll_performed"] is False
    assert result["summary"]["chain_touched"] is False

    assert opener.patched_compose is not None
    patched = yaml.safe_load(opener.patched_compose)
    services = patched["services"]
    for name in (
        "mother-add-node-validator-activation-guardian",
        "mother-genesis-proof-guardian",
        "mother-node-remove-voter-mainnetc_super1",
    ):
        helper = services[name]
        assert helper["image"] == "alpine:3.20"
        assert helper["restart"] == "unless-stopped"
        assert helper["healthcheck"]["test"] == ["CMD-SHELL", "echo ok"]
        assert "while true" in " ".join(helper["command"])
        assert helper["labels"]["main_computer.mother.retired_helper_mimic"] == "true"

    assert services["mainnetc-super2"]["image"] == "hyperledger/besu:latest"
    assert services["mother-super-node-fdb"]["image"] == "foundationdb/foundationdb:7.4.6"
    assert services["mother-super-node-hub"]["image"] == "mainnetc-super2-hub:c56a20a0fd05"

    assert opener.cleanup2_compose is not None
    cleanup2 = opener.cleanup2_compose
    assert "docker:27-cli" in cleanup2
    assert "/var/run/docker.sock:/var/run/docker.sock" in cleanup2
    assert "docker compose -p" in cleanup2
    assert "--no-deps --force-recreate" in cleanup2
    assert "echo mother-helper-cleanup2-complete" in cleanup2
    assert "while true; do sleep 3600; done" not in cleanup2
    assert "mother-add-node-validator-activation-guardian" in cleanup2
    assert "mother-genesis-proof-guardian" in cleanup2
    assert "mother-node-remove-voter-mainnetc_super1" in cleanup2

    paths = [(item["method"], item["path"]) for item in opener.requests]
    assert ("GET", f"/api/v1/services/{SERVICE_UUID}") in paths
    assert ("PATCH", f"/api/v1/services/{SERVICE_UUID}") in paths
    assert ("POST", "/api/v1/services") in paths
    assert ("POST", f"/api/v1/services/{CLEANUP2_UUID}/start") in paths
    assert ("DELETE", f"/api/v1/services/{CLEANUP2_UUID}") in paths
    assert all(path != "/api/v1/deploy" for _method, path in paths)
    assert all(path != f"/api/v1/services/{SERVICE_UUID}/start" for _method, path in paths)
    assert all(path != f"/api/v1/services/{SERVICE_UUID}/restart" for _method, path in paths)
    assert all("/applications/" not in path for _method, path in paths)


def test_cleanup2_yagni_decodes_service_detail_returned_as_json_string(tmp_path: Path) -> None:
    runtime, private_state, topology_path = _install(tmp_path)
    opener = _Cleanup2Opener(service_detail_as_json_string=True)

    result = run_helper_cleanup2_yagni(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology_path,
        mode="execute",
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["summary"]["targeted_helper_service_count"] == 3
    assert opener.patched_compose is not None
    assert opener.cleanup2_compose is not None


def test_cleanup2_yagni_inspect_imports_topology_without_mutation(tmp_path: Path) -> None:
    runtime, private_state, topology_path = _install(tmp_path)
    opener = _Cleanup2Opener()

    result = run_helper_cleanup2_yagni(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology_path,
        mode="inspect",
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["summary"]["topology_imported"] is True
    assert result["summary"]["compose_patch_performed"] is False
    assert result["summary"]["cleanup2_performed"] is False
    assert opener.patched_compose is None
    assert opener.cleanup2_compose is None
    assert [(item["method"], item["path"]) for item in opener.requests] == [
        ("GET", f"/api/v1/services/{SERVICE_UUID}")
    ]


def test_cleanup2_yagni_skips_missing_topology_service_404(tmp_path: Path) -> None:
    runtime, private_state, topology_path = _install(tmp_path)
    opener = _Cleanup2Opener(service_detail_status=404)

    result = run_helper_cleanup2_yagni(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology_path,
        mode="execute",
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["patch_steps"] == [
        {
            "node": "mainnetc-super2",
            "controller_id": "coolify-c",
            "service_uuid": SERVICE_UUID,
            "status": "skipped",
            "reason": "service-detail-404",
            "helper_names": [],
            "patch_receipt": None,
            "source_field": None,
            "source_encoding": None,
        }
    ]
    assert result["summary"]["skipped_missing_service_count"] == 1
    assert result["summary"]["compose_patch_accepted_count"] == 0
    assert result["summary"]["cleanup2_performed"] is False
    assert result["summary"]["cleanup2_controller_count"] == 0
    assert opener.patched_compose is None
    assert opener.cleanup2_compose is None
    assert [(item["method"], item["path"]) for item in opener.requests] == [
        ("GET", f"/api/v1/services/{SERVICE_UUID}")
    ]

