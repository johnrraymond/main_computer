from __future__ import annotations

import base64
import hashlib
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
  mother-genesis-init:
    image: alpine:3.20
    exclude_from_hc: true
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
        self.parent_restart_requested = False
        self.service_detail_as_json_string = service_detail_as_json_string
        self.service_detail_status = service_detail_status

    def _parent_service_payload(self):
        payload = {
            "uuid": SERVICE_UUID,
            "name": "mainnetc-super2",
            "status": "running:healthy" if self.parent_restart_requested else "degraded:unhealthy",
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
            "docker_compose_raw": base64.b64encode((self.patched_compose or _compose()).encode("utf-8")).decode("ascii"),
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
        if method == "POST" and path == f"/api/v1/services/{SERVICE_UUID}/restart":
            self.parent_restart_requested = True
            return _Response({"message": "Service restarting request queued."})
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
        if method == "GET" and path == f"/api/v1/services/{CLEANUP2_UUID}/logs":
            if not parsed.query.startswith("sub_service_name=mother-helper-cleanup2-coolify-c"):
                return _Response({"message": "sub_service_name is required"}, status=400)
            return _Response(
                {
                    "logs": "\n".join(
                        [
                            "MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC phase=script_start",
                            f"MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC phase=target_payload service_uuid={SERVICE_UUID}",
                            f"MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC phase=find_project service_uuid={SERVICE_UUID}",
                            f"MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC phase=find_project_selected service_uuid={SERVICE_UUID} project={SERVICE_UUID} workdir=/data/coolify/services/{SERVICE_UUID}",
                            f"MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC phase=target_begin service_uuid={SERVICE_UUID} project={SERVICE_UUID}",
                            f"MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC phase=remove_expected_container_before_recreate expected_container=mother-node-remove-voter-mainnetc_super1-{SERVICE_UUID} helper=mother-node-remove-voter-mainnetc_super1 project={SERVICE_UUID} removing=true",
                            f"MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC phase=docker_compose_exit service_uuid={SERVICE_UUID} project={SERVICE_UUID} exit_code=0",
                            f"MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC phase=after expected_container=mother-node-remove-voter-mainnetc_super1-{SERVICE_UUID} helper=mother-node-remove-voter-mainnetc_super1 project={SERVICE_UUID} is_cleanup2_mimic=true",
                            "MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC phase=script_complete",
                        ]
                    )
                }
            )
        if method == "DELETE" and path == f"/api/v1/services/{CLEANUP2_UUID}":
            return _Response({"deleted": True})

        raise AssertionError(f"unexpected request: {method} {path}?{parsed.query}")


def test_cleanup2_yagni_patches_helpers_and_runs_one_host_local_cleanup2_service(tmp_path: Path) -> None:
    runtime, private_state, topology_path = _install(tmp_path)
    opener = _Cleanup2Opener()
    sleep_calls: list[float] = []

    result = run_helper_cleanup2_yagni(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology_path,
        mode="execute",
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        sleeper=sleep_calls.append,
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
    assert result["cleanup2_steps"][0]["completion"]["runtime_diagnostics_observed"] is True
    assert result["cleanup2_steps"][0]["completion"]["remove_node_helper_cleanup_reached"] is True
    assert result["cleanup2_steps"][0]["completion"]["why_cleanup_action_not_proven"] == "runtime-diagnostics-observed-without-runtime-failure"
    runtime_diag = result["cleanup2_steps"][0]["completion"]["runtime_diagnostics"]
    assert runtime_diag["expected_phase_observed"]["remove_expected_container_before_recreate"] is True
    assert runtime_diag["expected_helper_remove_reached"]["mother-node-remove-voter-mainnetc_super1"] is True
    assert runtime_diag["expected_container_remove_reached"][f"mother-node-remove-voter-mainnetc_super1-{SERVICE_UUID}"] is True
    assert result["summary"]["cleanup2_runtime_diagnostics_observed_count"] == 1
    assert result["summary"]["cleanup2_remove_node_helper_cleanup_reached"] is True
    assert result["cleanup2_steps"][0]["health"] is None
    assert result["summary"]["parent_redeploy_performed"] is False
    assert result["summary"]["parent_restart_performed"] is True
    assert result["summary"]["parent_restart_request_count"] == 1
    assert result["summary"]["parent_restart_wait_count"] == 1
    assert result["summary"]["parent_restart_waits_all_running_healthy"] is True
    assert result["summary"]["child_public_api_restart_attempted"] is False
    assert result["summary"]["temporary_helper_apply_service_created"] is False
    assert result["summary"]["post_restart_health_poll_performed"] is True
    assert sleep_calls == [90.0]
    first_step = result["patch_steps"][0]
    diagnostics = first_step["diagnostics"]
    assert diagnostics["pre_patch_parent"]["parent_status"] == "degraded:unhealthy"
    assert diagnostics["pre_patch_parent"]["helper_application_count"] == 3
    assert any(
        item["helper_name"] == "mother-genesis-proof-guardian" and item["is_cleanup2_mimic_definition"] is False
        for item in diagnostics["pre_patch_parent"]["helper_definitions"]
    )
    assert any(
        item["helper_name"] == "mother-genesis-proof-guardian" and item["is_cleanup2_mimic_definition"] is True
        for item in diagnostics["rewritten_helper_definitions"]
    )
    assert diagnostics["patch_readback"]["compose_matches_expected_patch"] is True
    assert diagnostics["patch_readback"]["all_target_helpers_are_cleanup2_mimics_in_saved_compose"] is True
    assert result["summary"]["debug_destructive_short_circuit_after_proof_guardian_cleanup2"] is False
    left_for_inspection = result["summary"]["debug_destructive_cleanup2_services_left_for_inspection"]
    assert left_for_inspection[0]["controller_id"] == "coolify-c"
    assert left_for_inspection[0]["service_uuid"] == CLEANUP2_UUID
    assert left_for_inspection[0]["service_name"].startswith("mother-helper-cleanup2-coolify-c-")
    assert len(result["post_cleanup_readbacks"]) == 1
    assert result["post_cleanup_readbacks"][0]["all_target_helpers_are_cleanup2_mimics_in_saved_compose"] is True
    assert result["parent_restart_receipts"][0]["endpoint"] == f"/api/v1/services/{SERVICE_UUID}/restart"
    assert result["parent_restart_receipts"][0]["ok"] is True
    assert result["parent_restart_receipts"][0]["restart_scope"] == "post-cleanup2-parent-status-reconcile"
    assert result["parent_restart_waits"][0]["completed"] is True
    assert result["parent_restart_waits"][0]["initial_settle_seconds"] == 90.0
    assert result["parent_restart_waits"][0]["final_status"] == "running:healthy"
    assert result["cleanup2_steps"][0]["debug_destructive_exit_after_proof_guardian_cleanup2"] is False
    assert result["cleanup2_steps"][0]["debug_destructive_cleanup2_service_left_for_inspection"] is True
    assert result["cleanup2_steps"][0]["completion"]["temporary_service_delete"]["skipped"] is True
    assert (
        result["cleanup2_steps"][0]["completion"]["temporary_service_delete"]["cleanup_scope"]
        == "cleanup2-temporary-service-delete-disabled"
    )
    cleanup2_diag = result["cleanup2_steps"][0]["diagnostics"]
    assert cleanup2_diag["target_diagnostics"][0]["docker_compose_cli_removed_service_keys"] == {
        "mother-genesis-init": ["exclude_from_hc"]
    }
    assert cleanup2_diag["target_diagnostics"][0]["docker_compose_cli_compose_sha256"]
    assert cleanup2_diag["runtime_diagnostics_log_prefix"] == "MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC"
    assert "actual discovered compose project" in cleanup2_diag["runtime_diagnostics_include"]
    assert "docker compose stdout base64" in cleanup2_diag["runtime_diagnostics_include"]
    assert "docker ps snapshots before and after" in cleanup2_diag["runtime_diagnostics_include"]
    assert cleanup2_diag["target_diagnostics"][0]["expected_container_names"] == [
        f"mother-add-node-validator-activation-guardian-{SERVICE_UUID}",
        f"mother-genesis-proof-guardian-{SERVICE_UUID}",
        f"mother-node-remove-voter-mainnetc_super1-{SERVICE_UUID}",
    ]
    assert "docker compose -p <discovered_project>" in cleanup2_diag["target_diagnostics"][0]["docker_compose_up_template"]
    assert cleanup2_diag["target_diagnostics"][0]["runtime_diagnostics_expected_phases"] == [
        "target_payload",
        "compose_decoded",
        "find_project",
        "find_project_candidate",
        "find_project_selected",
        "target_begin",
        "before",
        "remove_expected_container_before_recreate",
        "docker_compose_start",
        "docker_compose_exit",
        "docker_compose_stdout",
        "docker_compose_stderr",
        "after",
    ]

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
    assert services["mother-genesis-init"]["exclude_from_hc"] is True
    assert services["mother-super-node-fdb"]["image"] == "foundationdb/foundationdb:7.4.6"
    assert services["mother-super-node-hub"]["image"] == "mainnetc-super2-hub:c56a20a0fd05"

    assert opener.cleanup2_compose is not None
    cleanup2 = opener.cleanup2_compose
    assert "docker:27-cli" in cleanup2
    assert "/var/run/docker.sock:/var/run/docker.sock" in cleanup2
    assert "docker compose -p" in cleanup2
    assert "--no-deps --force-recreate" in cleanup2
    assert "MOTHER_HELPER_CLEANUP2_RUNTIME_DIAGNOSTIC" in cleanup2
    assert "MOTHER_HELPER_CLEANUP2_BRANCH" in cleanup2
    assert "phase=target_begin_before_find_project" in cleanup2
    assert "phase=remove_loop_before_call" in cleanup2
    assert "phase=remove_expected_container_before_recreate_enter" in cleanup2
    assert "phase=remove_expected_container_before_recreate_before_rm" in cleanup2
    assert "phase=docker_compose_before_start" in cleanup2
    assert "phase=docker_compose_start" in cleanup2
    assert "phase=remove_expected_container_before_recreate" in cleanup2
    assert "docker rm -f" in cleanup2
    assert "$$cid" in cleanup2
    assert "phase=docker_compose_exit" in cleanup2
    assert "inspect_expected" in cleanup2
    assert "after" in cleanup2
    assert "is_cleanup2_mimic=$$is_mimic" in cleanup2
    cleanup2_service = next(iter(yaml.safe_load(cleanup2)["services"].values()))
    cleanup2_script = cleanup2_service["command"][2]
    b64_marker = "MOTHER_HELPER_CLEANUP2_COMPOSE_1"
    embedded_b64 = cleanup2_script.split(f"<<'{b64_marker}'", 1)[1].split(b64_marker, 1)[0].strip()
    cleanup2_apply_compose = yaml.safe_load(base64.b64decode(embedded_b64).decode("utf-8"))
    assert "exclude_from_hc" not in cleanup2_apply_compose["services"]["mother-genesis-init"]
    assert 'info="$$(find_project "$$uuid")"' in cleanup2_script
    assert 'docker ps -aq --filter "name=$$uuid"' in cleanup2_script
    assert 'docker compose -p "$$project" -f "$$compose_file"' in cleanup2_script
    assert 'find_project ""' not in cleanup2_script
    assert '--filter "name="' not in cleanup2_script
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
    assert ("GET", f"/api/v1/services/{CLEANUP2_UUID}/logs") in paths
    assert ("POST", f"/api/v1/services/{SERVICE_UUID}/restart") in paths
    assert ("DELETE", f"/api/v1/services/{CLEANUP2_UUID}") not in paths
    log_request = next(item for item in opener.requests if item["path"] == f"/api/v1/services/{CLEANUP2_UUID}/logs")
    assert log_request["query"].startswith("sub_service_name=mother-helper-cleanup2-coolify-c")
    assert all(path != "/api/v1/deploy" for _method, path in paths)
    assert all(path != f"/api/v1/services/{SERVICE_UUID}/start" for _method, path in paths)
    assert paths.index(("GET", f"/api/v1/services/{CLEANUP2_UUID}/logs")) < paths.index(("POST", f"/api/v1/services/{SERVICE_UUID}/restart"))
    assert all("/applications/" not in path for _method, path in paths)



class _Cleanup2LogsUnavailableOpener(_Cleanup2Opener):
    def open(self, request, timeout: float):  # noqa: ANN001
        parsed = urlsplit(request.full_url)
        if request.get_method() == "GET" and parsed.path == f"/api/v1/services/{CLEANUP2_UUID}/logs":
            self.requests.append(
                {
                    "method": request.get_method(),
                    "host": parsed.hostname or "",
                    "path": parsed.path,
                    "query": parsed.query,
                    "body": None,
                }
            )
            return _Response({"message": "sub_service_name is required"}, status=400)
        return super().open(request, timeout)


def test_cleanup2_yagni_reports_unknown_when_runtime_logs_are_unavailable(tmp_path: Path) -> None:
    runtime, private_state, topology_path = _install(tmp_path)
    opener = _Cleanup2LogsUnavailableOpener()
    sleep_calls: list[float] = []

    result = run_helper_cleanup2_yagni(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology_path,
        mode="execute",
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        sleeper=sleep_calls.append,
    )

    assert sleep_calls == [90.0]
    completion = result["cleanup2_steps"][0]["completion"]
    assert completion["runtime_diagnostics_observed"] is False
    assert completion["remove_node_helper_cleanup_reached"] is False
    assert completion["why_cleanup_action_not_proven"] == "runtime-logs-unavailable"
    selected_endpoint = completion["runtime_diagnostics"]["why_cleanup_action_not_proven_detail"]["selected_logs_endpoint"]
    assert selected_endpoint.startswith(f"/api/v1/services/{CLEANUP2_UUID}/logs?sub_service_name=mother-helper-cleanup2-coolify-c-")
    assert result["summary"]["cleanup2_runtime_diagnostics_observed_count"] == 0
    assert result["summary"]["cleanup2_remove_node_helper_cleanup_reached"] is False


def test_cleanup2_yagni_decodes_service_detail_returned_as_json_string(tmp_path: Path) -> None:
    runtime, private_state, topology_path = _install(tmp_path)
    opener = _Cleanup2Opener(service_detail_as_json_string=True)
    sleep_calls: list[float] = []

    result = run_helper_cleanup2_yagni(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology_path,
        mode="execute",
        max_wait_seconds=0,
        poll_interval_seconds=0,
        opener=opener,
        sleeper=sleep_calls.append,
    )

    assert sleep_calls == [90.0]
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


def test_cleanup2_yagni_accepts_current_topology_marked_by_evidence(tmp_path: Path) -> None:
    runtime, private_state, topology_path = _install(tmp_path)
    document = json.loads(topology_path.read_text(encoding="utf-8"))
    document["summary"].pop("topology_current")
    document["summary"]["current_topology_marked_by_evidence"] = True
    body = json.dumps(document, indent=2, sort_keys=True) + "\n"
    topology_path.write_text(body, encoding="utf-8")
    topology_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    opener = _Cleanup2Opener()

    result = run_helper_cleanup2_yagni(
        private_state,
        runtime_state_root=runtime,
        network="mainnet",
        topology_evidence=topology_path,
        acknowledged_topology_evidence_sha256=topology_sha,
        mode="inspect",
        opener=opener,
    )

    assert result["status"] == "pass"
    assert result["summary"]["topology_imported"] is True
    assert result["topology_evidence"]["sha256"] == topology_sha


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

