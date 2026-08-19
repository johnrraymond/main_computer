from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Iterator

import pytest

from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
)
from tools.mother_coolify_transition_watch import Subject, _redact, _validator_admission_process_details, diff_subjects


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "tools" / "mother_coolify_transition_watch.py"
TOKEN = "1|THISISASECRETTOKENVALUE123456"
PRIVATE_KEY = "0x" + "22" * 32


class _CoolifyHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, str]] = []
    candidate_service_status = "running:healthy"
    candidate_guardian_status = "running:healthy"
    public_proof_payload: dict[str, Any] | None = None

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _send(self, status: int, payload: Any) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        type(self).requests.append({"method": "POST", "path": self.path, "authorization": self.headers.get("Authorization", "")})
        self._send(405, {"error": "method not allowed"})

    def do_DELETE(self) -> None:  # noqa: N802
        type(self).requests.append({"method": "DELETE", "path": self.path, "authorization": self.headers.get("Authorization", "")})
        self._send(405, {"error": "method not allowed"})

    def do_GET(self) -> None:  # noqa: N802
        type(self).requests.append({"method": "GET", "path": self.path, "authorization": self.headers.get("Authorization", "")})
        payloads: dict[str, Any] = {
            "/api/health": {"status": "ok"},
            "/api/v1/version": {"version": "4.1.2"},
            "/api/v1/projects": {"projects": [{"uuid": "project-1", "name": "Mother Project"}]},
            "/api/v1/projects/project-1/environments": {"environments": [{"uuid": "env-1", "name": "mainnet"}]},
            "/api/v1/servers": {"servers": [{"uuid": "server-1", "name": "coolify-b", "status": "ready"}]},
            "/api/v1/destinations": {"destinations": [{"uuid": "destination-1", "name": "docker"}]},
            "/api/v1/applications": {
                "applications": [
                    {
                        "uuid": "application-1",
                        "name": "mainnet-hub3",
                        "status": "running",
                        "private_key": PRIVATE_KEY,
                    }
                ]
            },
            "/api/v1/services": {
                "services": [
                    {"uuid": "service-1", "name": "foundationdb", "status": "running:healthy"},
                    {"uuid": "candidate-svc", "name": "mainneta-super1", "status": type(self).candidate_service_status},
                ]
            },
            "/api/v1/services/candidate-svc": {
                "uuid": "candidate-svc",
                "name": "mainneta-super1",
                "status": type(self).candidate_service_status,
                "children": [
                    {
                        "name": "mother-add-node-validator-activation-guardian",
                        "status": type(self).candidate_guardian_status,
                    }
                ],
            },
            "/api/v1/resources": {
                "resources": [
                    {"uuid": "resource-1", "name": "mainnet-hub3", "status": "running"}
                ]
            },
        }
        if self.path == "/proof" and type(self).public_proof_payload is not None:
            self._send(200, type(self).public_proof_payload)
            return
        if self.path not in payloads:
            self._send(404, {"error": "not found"})
            return
        self._send(200, payloads[self.path])


@contextmanager
def _server() -> Iterator[str]:
    _CoolifyHandler.requests = []
    _CoolifyHandler.candidate_service_status = "running:healthy"
    _CoolifyHandler.candidate_guardian_status = "running:healthy"
    _CoolifyHandler.public_proof_payload = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CoolifyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _operation(name: str) -> OperationIdentity:
    return OperationIdentity(
        operation_id=name,
        request_id=f"{name}-request",
        network="local",
        operation_kind="MOTHER-OP-DIAGNOSE",
    )


def _document(base_url: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "main_computer.mother.private_state.v1",
        "networks": {
            "testnet": {
                "coolify": {
                    "controllers": {
                        "coolify-b": {
                            "url": base_url + "/",
                            "api_token": TOKEN,
                            "project_name": "Mother Project",
                            "project_uuid": "project-1",
                            "server_uuid": "server-1",
                        }
                    },
                    "mutation_authority": "observe-only",
                },
                "nodes": {},
                "validators": {},
                "wallets": {"deployer": {"private_key": PRIVATE_KEY}},
            }
        },
    }


def _install(tmp_path: Path, base_url: str) -> Path:
    runtime = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    operation = _operation("mother-transition-watch-test-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _document(base_url),
        updated_at="2026-08-17T19:29:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    return runtime


def _run(runtime: Path, *args: str, session_root: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(CLI), *args, "--runtime-state-root", str(runtime)]
    if session_root is not None:
        command.extend(["--session-root", str(session_root)])
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _canonical_proof_payload() -> dict[str, Any]:
    validators = [
        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
    ]
    return {
        "canonical_history_proof_contract": "mother-add-node-validator-admission-canonical-block-history-v1",
        "first_block_number": 100,
        "first_block_hash": "0x" + "11" * 32,
        "first_block_parent_hash": "0x" + "10" * 32,
        "first_block_validator_set": validators,
        "second_block_number": 102,
        "second_block_hash": "0x" + "22" * 32,
        "second_block_parent_hash": "0x" + "21" * 32,
        "second_block_validator_set": validators,
        "latest_block_number": 103,
        "latest_block_hash": "0x" + "33" * 32,
        "latest_block_parent_hash": "0x" + "32" * 32,
        "latest_validator_set": validators,
    }




def test_plan_is_god_observer_by_default_without_network_access(tmp_path: Path) -> None:
    with _server() as base_url:
        runtime = _install(tmp_path, base_url)
        result = _run(runtime, "plan", "--network", "testnet")
        assert result.returncode == 0, result.stderr
        assert TOKEN not in result.stdout
        assert PRIVATE_KEY not in result.stdout
        payload = json.loads(result.stdout)
        assert payload["profile"] == "mother-action-observer-default"
        assert payload["policy"]["mother_action_observer_default"] is True
        assert payload["policy"]["coolify_mutations_in_this_patch"] is False
        assert payload["controllers"] == [
            {
                "base_url": base_url,
                "controller_id": "coolify-b",
                "enabled": True,
                "has_api_token": True,
                "mutation_authority": "observe-only",
                "network": "testnet",
                "project_name_hint": "Mother Project",
            }
        ]
        assert payload["observer_layers"]["active_validator_admission_dashboard"] == "enabled-by-default"
        assert "sentinel_compose_preview" not in payload
        assert _CoolifyHandler.requests == []


def test_watch_once_logs_mother_actions_and_coolify_transitions_without_local_app_noise(tmp_path: Path) -> None:
    with _server() as base_url:
        runtime = _install(tmp_path, base_url)
        release = (
            runtime
            / "mother"
            / "actions"
            / "deployment-node-add-validator-admission-releases"
            / "20260817T192059Z-mainnet-mainnetc-super2-27a3f2c4c985a8c0.json"
        )
        release.parent.mkdir(parents=True, exist_ok=True)
        release.write_text(json.dumps({"kind": "validator-admission-release", "status": "prepared"}), encoding="utf-8")
        session_parent = tmp_path / "observer-sessions"
        result = _run(
            runtime,
            "watch",
            "--network", "testnet",
            "--once",
            "--admission-release", str(release),
            "--mother-process-limit", "0",
            "--screen-baseline-limit", "20",
            session_root=session_parent,
        )
        assert result.returncode == 0, result.stderr
        assert TOKEN not in result.stdout
        assert PRIVATE_KEY not in result.stdout
        assert "Mother Action Observer default" in result.stdout

        methods = {item["method"] for item in _CoolifyHandler.requests}
        assert methods == {"GET"}
        assert any(item["path"] == "/api/health" for item in _CoolifyHandler.requests)
        assert any(item["path"] == "/api/v1/applications" for item in _CoolifyHandler.requests)

        sessions = sorted(session_parent.glob("*-testnet"))
        assert len(sessions) == 1
        events_path = sessions[0] / "events.ndjson"
        latest_path = sessions[0] / "latest.json"
        assert events_path.exists()
        assert latest_path.exists()

        events = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
        assert events
        assert TOKEN not in events_path.read_text()
        assert PRIVATE_KEY not in events_path.read_text()

        appeared_subjects = {(event["source"], event["subject_type"], event["subject_id"]) for event in events}
        assert ("coolify-api", "application", "application-1") in appeared_subjects
        assert ("coolify-api", "service", "service-1") in appeared_subjects
        latest = json.loads(latest_path.read_text())
        assert latest["profile"] == "mother-action-observer-default"
        assert latest["visibility_gap_count"] >= 1
        latest_subjects = latest["subjects"]
        assert all(subject["source"] != "local-system" for subject in latest_subjects)
        forbidden_local_subjects = {"process", "docker-container", "docker-network", "load", "host"}
        assert not any(subject["subject_type"] in forbidden_local_subjects for subject in latest_subjects)
        assert any(
            subject["source"] == "mother-execution"
            and subject["subject_type"] == "mother-file"
            and "deployment-node-add-validator-admission-releases" in subject["subject_id"]
            for subject in latest_subjects
        )
        assert any(
            subject["source"] == "mother-execution"
            and subject["subject_type"] == "focused-artifact"
            for subject in latest_subjects
        )


def test_redaction_and_transition_diff_are_secret_safe() -> None:
    old = Subject("coolify-api", "coolify-b", "service", "svc", {"status": "starting", "api_token": TOKEN})
    new = Subject("coolify-api", "coolify-b", "service", "svc", {"status": "running", "private_key": PRIVATE_KEY})
    events = diff_subjects({old.key: old}, {new.key: new}, observed_at="2026-08-17T19:29:00Z")
    assert len(events) == 1
    rendered = json.dumps(events)
    assert TOKEN not in rendered
    assert PRIVATE_KEY not in rendered
    assert "<redacted>" in rendered
    assert _redact({"nested": {"password": "abc"}}) == {"nested": {"password": "<redacted>"}}


def test_watch_decodes_validator_admission_diagnostics_and_flags_missing_proof_payload(tmp_path: Path) -> None:
    with _server() as base_url:
        runtime = _install(tmp_path, base_url)
        evidence = (
            runtime
            / "mother"
            / "evidence"
            / "deployment-node-add-validator-admission"
            / "20260817T191657Z-mainneta-super1-db70a777645c7233.json"
        )
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(
                {
                    "kind": "main_computer.mother.deployment_node_add_validator_admission_evidence.v1",
                    "network": "testnet",
                    "status": "pass",
                    "completed_at": "2026-08-17T19:16:57Z",
                    "candidate_node": "mainneta-super1",
                    "candidate_validator_address": "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                    "current_validator_set": ["0x9b809f05f8d68da17e697cd6ab040d4320494611"],
                    "desired_validator_set": [
                        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
                    ],
                    "final_validator_set": [
                        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
                    ],
                    "canonical_validator_history_proof": {
                        "contract": "mother-add-node-validator-admission-canonical-block-history-v1",
                        "proof_payload_required": True,
                    },
                    "health_observations": [
                        {
                            "node": "mainneta-super1",
                            "guardian_role": "candidate_activation",
                            "proof_guardian_name": "mother-add-node-validator-activation-guardian",
                            "proof_guardian_status": "running:healthy",
                            "proof_guardian_healthy": True,
                            "observation_phase": "admission-proof-terminal-durable",
                            "durable_sample_index": 3,
                            "response_sha256": "b" * 64,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = _run(
            runtime,
            "status",
            "--network",
            "testnet",
            "--mother-process-limit",
            "0",
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        diagnostics = [
            subject
            for subject in payload["subjects"]
            if subject["subject_type"] == "validator-admission-evidence"
        ]
        assert diagnostics
        state = diagnostics[0]["state"]
        assert state["contract_marker_without_payload"] is True
        assert state["has_actual_candidate_activation_proof_payload"] is False
        assert "first_block_hash" in state["candidate_activation_proof_payload_missing_fields"]


def test_process_details_extract_active_validator_admission_release() -> None:
    command = [
        "C:\\Users\\subsi\\.venv\\scripts\\python.exe",
        "C:\\Users\\subsi\\main_computer\\tools\\mother_deploy.py",
        "add-node",
        "validator-admission",
        "mainnet",
        "--runtime-state-root",
        "C:\\Users\\subsi\\main_computer\\runtime\\state",
        "--release",
        "C:\\Users\\subsi\\main_computer\\runtime\\state\\mother\\actions\\deployment-node-add-validator-admission-releases\\20260817T212929Z-mainnet-mainneta-super1-8e7391a984e3eeaf.json",
        "--acknowledge-release-sha256",
        "8e7391a984e3eeafc5cfb228d389e458e0e7a656e335d01f66612d7364c00d65",
        "--max-wait-seconds",
        "900.0",
        "--poll-interval-seconds",
        "5.0",
        "--execute",
    ]

    details = _validator_admission_process_details(command)

    assert details["role"] == "execute-validator-admission"
    assert details["network"] == "mainnet"
    assert details["execute"] is True
    assert details["release_basename"] == "20260817T212929Z-mainnet-mainneta-super1-8e7391a984e3eeaf.json"
    assert details["acknowledge_release_sha256"] == "8e7391a984e3eeafc5cfb228d389e458e0e7a656e335d01f66612d7364c00d65"
    assert details["max_wait_seconds"] == "900.0"
    assert details["poll_interval_seconds"] == "5.0"


def test_active_validator_admission_dashboard_flags_healthy_guardian_without_visible_proof(tmp_path: Path) -> None:
    with _server() as base_url:
        runtime = _install(tmp_path, base_url)
        mother = runtime / "mother"
        replica = (
            mother
            / "evidence"
            / "deployment-node-add-replica-sync"
            / "20260817T212928Z-mainneta-super1-replica.json"
        )
        replica.parent.mkdir(parents=True, exist_ok=True)
        replica.write_text(
            json.dumps(
                {
                    "kind": "main_computer.mother.deployment_node_add_replica_sync_evidence.v1",
                    "network": "testnet",
                    "status": "pass",
                    "completed_at": "2026-08-17T21:29:28Z",
                    "target": {
                        "node": "mainneta-super1",
                        "controller_id": "coolify-b",
                        "created_service_uuid": "candidate-svc",
                        "validator_address": "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                    },
                    "current_topology": {
                        "nodes": ["mainnetc-super1"],
                        "validator_set": ["0x9b809f05f8d68da17e697cd6ab040d4320494611"],
                        "services": {
                            "mainnetc-super1": {
                                "controller_id": "coolify-b",
                                "service_uuid": "service-1",
                                "service_status": "running:healthy",
                            }
                        },
                    },
                    "prepared_post_add_topology": {
                        "added_node": "mainneta-super1",
                        "nodes": ["mainnetc-super1", "mainneta-super1"],
                        "validator_set": [
                            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
                            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                        ],
                        "services": {
                            "mainneta-super1": {
                                "controller_id": "coolify-b",
                                "service_uuid": "candidate-svc",
                                "service_status": "pending-add-node-do",
                            }
                        },
                    },
                    "source_baseline_evidence": {
                        "locator": "evidence/deployment-node-add-post-admission-observe/baseline.json",
                        "sha256": "b" * 64,
                    },
                }
            ),
            encoding="utf-8",
        )
        release = (
            mother
            / "actions"
            / "deployment-node-add-validator-admission-releases"
            / "20260817T212929Z-mainnet-mainneta-super1-8e7391a984e3eeaf.json"
        )
        release.parent.mkdir(parents=True, exist_ok=True)
        release.write_text(
            json.dumps(
                {
                    "kind": "main_computer.mother.deployment_node_add_validator_admission_release.v1",
                    "network": "testnet",
                    "node_add_validator_admission_release_sha256": "8e7391a984e3eeafc5cfb228d389e458e0e7a656e335d01f66612d7364c00d65",
                    "source_replica_sync_evidence": {
                        "locator": "evidence/deployment-node-add-replica-sync/20260817T212928Z-mainneta-super1-replica.json",
                        "sha256": "r" * 64,
                    },
                }
            ),
            encoding="utf-8",
        )
        stale_evidence = (
            mother
            / "evidence"
            / "deployment-node-add-validator-admission"
            / "20260817T191657Z-mainneta-super1-db70a777645c7233.json"
        )
        stale_evidence.parent.mkdir(parents=True, exist_ok=True)
        stale_evidence.write_text(
            json.dumps(
                {
                    "kind": "main_computer.mother.deployment_node_add_validator_admission_evidence.v1",
                    "network": "testnet",
                    "status": "pass",
                    "completed_at": "2026-08-17T19:16:57Z",
                    "candidate_node": "mainneta-super1",
                    "candidate_validator_address": "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                    "current_validator_set": ["0x9b809f05f8d68da17e697cd6ab040d4320494611"],
                    "desired_validator_set": [
                        "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                        "0x9b809f05f8d68da17e697cd6ab040d4320494611",
                    ],
                    "source_replica_sync_evidence": {"sha256": "old" * 20},
                    "canonical_validator_history_proof": {
                        "contract": "mother-add-node-validator-admission-canonical-block-history-v1"
                    },
                    "health_observations": [
                        {
                            "guardian_role": "candidate_activation",
                            "node": "mainneta-super1",
                            "observed_at": "2026-08-17T19:16:57Z",
                            "proof_guardian_status": "running:healthy",
                            "service_status": "running:healthy",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = _run(
            runtime,
            "status",
            "--network",
            "testnet",
            "--admission-release",
            str(release),
            "--mother-process-limit",
            "0",
        )

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        dashboards = [
            subject
            for subject in payload["subjects"]
            if subject["subject_type"] == "active-validator-admission-dashboard"
        ]
        assert dashboards
        state = dashboards[0]["state"]
        assert state["candidate_node"] == "mainneta-super1"
        assert state["operator_decision"] == "BLOCKED_ACTIVE_GUARDIAN_HEALTHY_BUT_NO_FRESH_CANONICAL_PROOF_PAYLOAD"
        candidate = state["coolify_service_detail"]["candidate_activation_guardian"]
        assert candidate["diagnosis"] == "COOLIFY_HEALTH_WITHOUT_VISIBLE_CANONICAL_PROOF_PAYLOAD"
        assert state["latest_matching_admission_evidence"]["available"] is False
        assert state["latest_matching_admission_evidence"]["diagnosis"] == "NO_ADMISSION_EVIDENCE_AFTER_RELEASE_YET"
        assert state["stale_prior_admission_evidence"]["path"].endswith("20260817T191657Z-mainneta-super1-db70a777645c7233.json")
        assert state["stale_prior_admission_evidence"]["relationship_to_release"] == "before_release"
        assert candidate["guardian_states"]["mother-add-node-validator-activation-guardian"]["component_status"] == "running:healthy"
        assert any(item["path"] == "/api/v1/services/candidate-svc" for item in _CoolifyHandler.requests)


def test_active_validator_admission_dashboard_accepts_public_proof_when_guardian_unhealthy(tmp_path: Path) -> None:
    with _server() as base_url:
        _CoolifyHandler.candidate_service_status = "running:unhealthy"
        _CoolifyHandler.candidate_guardian_status = "running:unhealthy"
        _CoolifyHandler.public_proof_payload = _canonical_proof_payload()
        runtime = _install(tmp_path, base_url)
        mother = runtime / "mother"
        replica = (
            mother
            / "evidence"
            / "deployment-node-add-replica-sync"
            / "20260817T212928Z-mainneta-super1-replica.json"
        )
        replica.parent.mkdir(parents=True, exist_ok=True)
        replica.write_text(
            json.dumps(
                {
                    "kind": "main_computer.mother.deployment_node_add_replica_sync_evidence.v1",
                    "network": "testnet",
                    "status": "pass",
                    "completed_at": "2026-08-17T21:29:28Z",
                    "target": {
                        "node": "mainneta-super1",
                        "controller_id": "coolify-b",
                        "created_service_uuid": "candidate-svc",
                        "validator_address": "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                    },
                    "current_topology": {
                        "nodes": ["mainnetc-super1"],
                        "validator_set": ["0x9b809f05f8d68da17e697cd6ab040d4320494611"],
                        "services": {
                            "mainnetc-super1": {
                                "controller_id": "coolify-b",
                                "service_uuid": "service-1",
                                "service_status": "running:healthy",
                            }
                        },
                    },
                    "prepared_post_add_topology": {
                        "added_node": "mainneta-super1",
                        "nodes": ["mainnetc-super1", "mainneta-super1"],
                        "validator_set": [
                            "0xc539f2b771eea73fe61ae4251ef5ba861d9745f6",
                            "0x9b809f05f8d68da17e697cd6ab040d4320494611",
                        ],
                        "services": {
                            "mainneta-super1": {
                                "controller_id": "coolify-b",
                                "service_uuid": "candidate-svc",
                                "service_status": "pending-add-node-do",
                            }
                        },
                    },
                    "source_baseline_evidence": {
                        "locator": "evidence/deployment-node-add-post-admission-observe/baseline.json",
                        "sha256": "b" * 64,
                    },
                }
            ),
            encoding="utf-8",
        )
        release = (
            mother
            / "actions"
            / "deployment-node-add-validator-admission-releases"
            / "20260817T212929Z-mainnet-mainneta-super1-8e7391a984e3eeaf.json"
        )
        release.parent.mkdir(parents=True, exist_ok=True)
        release.write_text(
            json.dumps(
                {
                    "kind": "main_computer.mother.deployment_node_add_validator_admission_release.v1",
                    "network": "testnet",
                    "node_add_validator_admission_release_sha256": "8e7391a984e3eeafc5cfb228d389e458e0e7a656e335d01f66612d7364c00d65",
                    "source_replica_sync_evidence": {
                        "locator": "evidence/deployment-node-add-replica-sync/20260817T212928Z-mainneta-super1-replica.json",
                        "sha256": "r" * 64,
                    },
                    "admission_plan": {
                        "candidate_activation_proof_endpoint": {
                            "kind": "mother-add-node-validator-admission-public-proof-endpoint.v1",
                            "transport": "http-public-controller",
                            "public_http_endpoint_created": True,
                            "url": base_url + "/proof",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        result = _run(
            runtime,
            "status",
            "--network",
            "testnet",
            "--admission-release",
            str(release),
            "--mother-process-limit",
            "0",
        )

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        dashboards = [
            subject
            for subject in payload["subjects"]
            if subject["subject_type"] == "active-validator-admission-dashboard"
        ]
        assert dashboards
        state = dashboards[0]["state"]
        assert state["operator_decision"] == "CANONICAL_PROOF_VISIBLE_AND_MATCHING"
        candidate = state["coolify_service_detail"]["candidate_activation_guardian"]
        guardian = candidate["guardian_states"]["mother-add-node-validator-activation-guardian"]
        assert guardian["component_status"] == "running:unhealthy"
        assert guardian["canonical_proof_transport"] == "mother-public-proof-endpoint"
        assert guardian["canonical_proof"]["all_sets_match_expected"] is True
        assert any(item["path"] == "/proof" for item in _CoolifyHandler.requests)

