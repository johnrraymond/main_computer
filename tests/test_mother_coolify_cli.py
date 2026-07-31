from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Iterator

import pytest

from tools.mother.common.coolify_state import (
    CoolifyObservationError,
    get_coolify_json,
    list_coolify_controllers,
    resolve_coolify_controller,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "tools" / "mother_coolify.py"
TOKEN = "1|THISISASECRETTOKENVALUE123456"
PRIVATE_KEY = "0x" + "11" * 32


class _CoolifyHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, str]] = []
    oversized = False
    redirect = False

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _send(self, status: int, payload: Any, *, headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802
        type(self).requests.append({"method": "POST", "path": self.path, "authorization": self.headers.get("Authorization", "")})
        self._send(405, {"error": "method not allowed"})

    def do_GET(self) -> None:  # noqa: N802
        type(self).requests.append({"method": "GET", "path": self.path, "authorization": self.headers.get("Authorization", "")})
        if type(self).redirect and self.path == "/api/v1/version":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/steal")
            self.end_headers()
            return
        if type(self).oversized and self.path == "/api/v1/version":
            self._send(200, {"version": "x" * 4096})
            return
        payloads: dict[str, Any] = {
            "/api/health": {"status": "ok"},
            "/api/v1/version": {"version": "4.1.2"},
            "/api/v1/projects": {
                "projects": [
                    {"uuid": "project-1", "name": "My first project", "api_token": TOKEN},
                ]
            },
            "/api/v1/projects/project-1/environments": {
                "environments": [{"uuid": "environment-1", "name": "debug"}],
            },
            "/api/v1/servers": {"servers": [{"uuid": "server-1", "name": "testnet"}]},
            "/api/v1/destinations": {"destinations": [{"uuid": "destination-1", "name": "docker"}]},
            "/api/v1/applications": {
                "applications": [
                    {
                        "uuid": "application-1",
                        "name": "mother-debug-app",
                        "status": "running",
                        "private_key": PRIVATE_KEY,
                    }
                ]
            },
            "/api/v1/services": {"services": [{"uuid": "service-1", "name": "foundationdb"}]},
            "/api/v1/resources": {"resources": [{"uuid": "resource-1", "name": "resource"}]},
        }
        if self.path not in payloads:
            self._send(404, {"error": "not found"})
            return
        self._send(200, payloads[self.path])


@contextmanager
def _server(*, oversized: bool = False, redirect: bool = False) -> Iterator[str]:
    _CoolifyHandler.requests = []
    _CoolifyHandler.oversized = oversized
    _CoolifyHandler.redirect = redirect
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


def _document(base_url: str, *, authority: str = "observe-only") -> dict[str, Any]:
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
                            "project_name": "My first project",
                        },
                        "local-test-coolify": {
                            "coolify_url": "http://127.0.0.1:8000",
                            "enabled": False,
                        },
                    },
                    "mutation_authority": authority,
                },
                "nodes": {},
                "validators": {},
                "wallets": {"deployer": {"private_key": PRIVATE_KEY}},
            }
        },
    }


def _install(tmp_path: Path, base_url: str, *, authority: str = "observe-only") -> Path:
    runtime = tmp_path / "runtime" / "state"
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    operation = _operation("mother-coolify-test-install")
    closure = prepare_private_state_bootstrap(
        paths,
        _document(base_url, authority=authority),
        updated_at="2026-07-30T22:30:00Z",
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    install_verified_private_state(paths, closure, None, operation=operation)
    return runtime


def _read(runtime: Path):
    paths = MotherPaths(runtime_state_root=runtime).resolve_private_state_paths()
    return read_private_state(paths, operation=_operation("mother-coolify-test-read"))


def _run(runtime: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args, "--runtime-state-root", str(runtime)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_controller_resolution_is_mother_bound_and_repr_is_secret_safe(tmp_path: Path) -> None:
    with _server() as base_url:
        runtime = _install(tmp_path, base_url)
        state = _read(runtime)
        controllers = list_coolify_controllers(state)
        assert [(item.network, item.controller_id) for item in controllers] == [
            ("testnet", "coolify-b"),
            ("testnet", "local-test-coolify"),
        ]
        controller = resolve_coolify_controller(state, "testnet", "coolify-b")
        assert controller.base_url == base_url
        assert TOKEN not in repr(controller)
        assert "<redacted>" in repr(controller)
        with pytest.raises(CoolifyObservationError) as caught:
            resolve_coolify_controller(state, "testnet", "local-test-coolify")
        assert caught.value.code == "MOTHER_COOLIFY_CONTROLLER_DISABLED"


def test_non_observe_only_authority_is_rejected_before_network_access(tmp_path: Path) -> None:
    with _server() as base_url:
        runtime = _install(tmp_path, base_url, authority="debug-only")
        result = _run(runtime, "controllers")
        assert result.returncode == 2
        assert "MOTHER_COOLIFY_AUTHORITY_REJECTED" in result.stderr
        assert _CoolifyHandler.requests == []


def test_controllers_command_prints_no_tokens_or_private_keys(tmp_path: Path) -> None:
    with _server() as base_url:
        runtime = _install(tmp_path, base_url)
        result = _run(runtime, "controllers")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["mother_generation"] == 1
        assert payload["controllers"][0]["has_api_token"] is True
        assert TOKEN not in result.stdout
        assert PRIVATE_KEY not in result.stdout
        assert _CoolifyHandler.requests == []


def test_health_is_get_only_unauthenticated_and_writes_bounded_evidence(tmp_path: Path) -> None:
    with _server() as base_url:
        runtime = _install(tmp_path, base_url)
        result = _run(
            runtime,
            "health",
            "--network", "testnet",
            "--controller", "coolify-b",
            "--timeout", "2",
        )
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert output["complete"] is True
        assert output["policy"]["allowed_method"] == "GET"
        assert _CoolifyHandler.requests == [
            {"method": "GET", "path": "/api/health", "authorization": ""}
        ]
        evidence = Path(output["evidence_path"])
        assert evidence.is_file()
        text = evidence.read_text(encoding="utf-8")
        assert TOKEN not in text
        assert PRIVATE_KEY not in text
        assert '"raw_response_persisted":false' in text
        if os.name != "nt":
            assert evidence.stat().st_mode & 0o777 == 0o600
            assert evidence.parent.stat().st_mode & 0o777 == 0o700


def test_inventory_uses_only_predefined_gets_and_persists_safe_identifiers(tmp_path: Path) -> None:
    with _server() as base_url:
        runtime = _install(tmp_path, base_url)
        result = _run(
            runtime,
            "inventory",
            "--network", "testnet",
            "--controller", "coolify-b",
            "--timeout", "2",
            "--max-items", "25",
        )
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert output["complete"] is True
        assert output["counts"]["projects"] == 1
        assert output["counts"]["environments:project-1"] == 1
        assert all(item["method"] == "GET" for item in _CoolifyHandler.requests)
        assert {item["path"] for item in _CoolifyHandler.requests} == {
            "/api/v1/version",
            "/api/v1/projects",
            "/api/v1/projects/project-1/environments",
            "/api/v1/servers",
            "/api/v1/destinations",
            "/api/v1/applications",
            "/api/v1/services",
            "/api/v1/resources",
        }
        assert all(item["authorization"] == f"Bearer {TOKEN}" for item in _CoolifyHandler.requests)

        evidence_path = Path(output["evidence_path"])
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        assert evidence["policy"] == {
            "allowed_method": "GET",
            "mutation_authority": "observe-only",
            "raw_response_persisted": False,
            "redirects_followed": False,
        }
        assert evidence["endpoints"]["projects"]["items"][0]["uuid"] == "project-1"
        assert evidence["endpoints"]["applications"]["items"][0]["uuid"] == "application-1"
        text = evidence_path.read_text(encoding="utf-8")
        assert TOKEN not in text
        assert PRIVATE_KEY not in text
        assert "api_token" not in text
        assert "private_key" not in text

        # Evidence remains compatible with the production private-state security scan.
        state = _read(runtime)
        assert state.binding.generation == 1


def test_oversized_responses_fail_closed_without_persisting_raw_body(tmp_path: Path) -> None:
    with _server(oversized=True) as base_url:
        runtime = _install(tmp_path, base_url)
        result = _run(
            runtime,
            "inventory",
            "--network", "testnet",
            "--controller", "coolify-b",
            "--timeout", "2",
            "--max-response-bytes", "128",
        )
        assert result.returncode == 1, result.stderr
        output = json.loads(result.stdout)
        assert output["complete"] is False
        evidence = json.loads(Path(output["evidence_path"]).read_text(encoding="utf-8"))
        assert evidence["endpoints"]["version"]["error_code"] == "MOTHER_COOLIFY_RESPONSE_TOO_LARGE"
        assert "x" * 256 not in json.dumps(evidence)


def test_redirects_are_not_followed_and_authorization_is_not_forwarded(tmp_path: Path) -> None:
    with _server(redirect=True) as base_url:
        runtime = _install(tmp_path, base_url)
        state = _read(runtime)
        controller = resolve_coolify_controller(state, "testnet", "coolify-b")
        observed = get_coolify_json(controller, "/api/v1/version", authenticated=True, timeout=2)
        assert observed.status == 302
        assert len(_CoolifyHandler.requests) == 1
        assert _CoolifyHandler.requests[0]["path"] == "/api/v1/version"
        assert _CoolifyHandler.requests[0]["authorization"] == f"Bearer {TOKEN}"


def test_observe_all_skips_disabled_controller_and_has_no_mutating_surface(tmp_path: Path) -> None:
    with _server() as base_url:
        runtime = _install(tmp_path, base_url)
        result = _run(runtime, "observe-all", "--timeout", "2")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        skipped = [item for item in payload["results"] if item.get("skipped")]
        assert skipped == [{
            "controller_id": "local-test-coolify",
            "network": "testnet",
            "skipped": "disabled",
        }]
        help_result = subprocess.run(
            [sys.executable, str(CLI), "--help"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert help_result.returncode == 0
        help_text = help_result.stdout.lower()
        for forbidden in ("post", "put", "patch", "delete", "deploy", "start", "stop", "restart", "create"):
            assert forbidden not in help_text
        assert all(item["method"] == "GET" for item in _CoolifyHandler.requests)
