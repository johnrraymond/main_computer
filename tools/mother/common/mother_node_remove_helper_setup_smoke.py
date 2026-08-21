#!/usr/bin/env python3
"""Set up a node-remove helper smoke container on the real Besu Docker network.

This is intentionally narrow and non-finalizing:

* creates a temporary Coolify Docker-control runner service;
* the runner finds the exact Besu container for one survivor node;
* the runner starts/replaces one smoke helper container on that Besu network;
* the helper serves /proof on the requested public host port after it proves:
  - RPC DNS/reachability from the helper network,
  - chain id/block/validator RPCs,
  - /config/genesis.json is visible through --volumes-from the Besu container.

It does not call qbft_proposeValidatorVote, does not delete any target service,
does not rewrite topology, and does not clean old shims.
"""

from __future__ import annotations

import argparse
import base64
from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import yaml


def _install_repo_import_path(explicit_repo_root: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit_repo_root:
        candidates.append(Path(explicit_repo_root).expanduser().resolve(strict=False))
    candidates.append(Path.cwd().resolve(strict=False))
    here = Path(__file__).resolve(strict=False)
    candidates.extend([here.parent, *(here.parents[:4])])
    for candidate in candidates:
        if (candidate / "tools" / "mother" / "common").is_dir():
            text = str(candidate)
            if text not in sys.path:
                sys.path.insert(0, text)
            return candidate
    raise SystemExit(
        "Could not find repo root containing tools/mother/common. "
        "Run from C:\\Users\\subsi\\main_computer or pass --repo-root."
    )


def _preparse_repo_root(argv: list[str]) -> str | None:
    for index, item in enumerate(argv):
        if item == "--repo-root" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--repo-root="):
            return item.split("=", 1)[1]
    return None


REPO_ROOT = _install_repo_import_path(_preparse_repo_root(sys.argv[1:]))

from tools.mother.common.coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, resolve_coolify_controller
from tools.mother.common.deployment_completed_helper_cleanup import (
    _application_uuid,
    _controller_config,
    _http,
    _resolve_environment_uuid,
    _temporary_service_body,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state


KIND = "main_computer.mother.node_remove_helper_setup_smoke.v1"
EVIDENCE_SUBDIR = "node-remove-helper-setup-smoke"
PROOF_CONTAINER_PORT = 8798
PROOF_PORT_OFFSET = 9100
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")


class MotherNodeRemoveHelperSetupSmokeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> MotherNodeRemoveHelperSetupSmokeError:
    return MotherNodeRemoveHelperSetupSmokeError(code, message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise _fail("MOTHER_NODE_REMOVE_HELPER_SETUP_INVALID_ARGUMENT", f"invalid {label}: {value!r}")
    if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise _fail("MOTHER_NODE_REMOVE_HELPER_SETUP_INVALID_ARGUMENT", f"unsafe {label}: {value!r}")
    return value


def _uuid(value: Any, label: str) -> str:
    text = _identifier(value, label)
    if not UUID_RE.fullmatch(text):
        raise _fail("MOTHER_NODE_REMOVE_HELPER_SETUP_INVALID_ARGUMENT", f"invalid {label}: {value!r}")
    return text


def _safe_node_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _operation(network: str, mode: str) -> OperationIdentity:
    network_id = _identifier(network, "network")
    mode_id = _identifier(mode, "mode")
    operation_id = f"mother-node-remove-helper-setup-smoke-{mode_id}-{network_id}-{_stamp()}"
    return OperationIdentity(
        operation_id=operation_id,
        request_id=f"{operation_id}-request",
        network=network_id,
        operation_kind="MOTHER-OP-RESTORE-SERVICE",
    )


def _load_private_state(runtime_state_root: str | Path, *, network: str, mode: str) -> PrivateStateReadResult:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root)).resolve_private_state_paths()
    return read_private_state(paths, operation=_operation(network, mode))


def _controller_public_host(controller: Any) -> str:
    parsed = urllib.parse.urlsplit(str(controller.base_url))
    host = parsed.hostname
    if not isinstance(host, str) or not host.strip() or host.strip() in {"0.0.0.0", "::"}:
        raise _fail(
            "MOTHER_NODE_REMOVE_HELPER_SETUP_PROOF_HOST_UNAVAILABLE",
            "Coolify controller base URL does not provide a usable public proof host",
        )
    return host.strip()


def _compose_text(record: Mapping[str, Any]) -> str:
    for key in ("docker_compose_raw", "docker_compose", "dockerComposeRaw", "dockerCompose"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if "\n" in text or text.startswith(("name:", "services:", "version:")):
            return text
        try:
            decoded = base64.b64decode(text, validate=True).decode("utf-8")
        except Exception:
            continue
        if "services:" in decoded:
            return decoded
    raise _fail("MOTHER_NODE_REMOVE_HELPER_SETUP_COMPOSE_MISSING", "Coolify service record has no Compose text")


def _parse_port(value: Any) -> int | None:
    if isinstance(value, Mapping):
        for key in ("published", "host_port", "published_port"):
            if key in value:
                try:
                    port = int(str(value[key]))
                except (TypeError, ValueError):
                    continue
                if 1 <= port <= 65535:
                    return port
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 65535 else None
    if not isinstance(value, str):
        return None
    text = value.strip().strip('"').strip("'").split("/", 1)[0]
    if not text:
        return None
    parts = text.split(":")
    candidate = parts[0] if len(parts) <= 2 else parts[-2]
    try:
        port = int(candidate)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def _first_published_host_port(compose_text: str, *, service_name: str) -> int:
    try:
        document = yaml.safe_load(compose_text)
    except yaml.YAMLError as exc:
        raise _fail("MOTHER_NODE_REMOVE_HELPER_SETUP_COMPOSE_INVALID", "survivor Compose cannot be parsed") from exc
    if not isinstance(document, Mapping) or not isinstance(document.get("services"), Mapping):
        raise _fail("MOTHER_NODE_REMOVE_HELPER_SETUP_COMPOSE_INVALID", "survivor Compose lacks services")
    service = document["services"].get(service_name)
    if not isinstance(service, Mapping):
        raise _fail("MOTHER_NODE_REMOVE_HELPER_SETUP_COMPOSE_INVALID", f"survivor service {service_name!r} is missing")
    ports = service.get("ports")
    if isinstance(ports, list):
        for item in ports:
            port = _parse_port(item)
            if port is not None:
                return port
    elif ports is not None:
        port = _parse_port(ports)
        if port is not None:
            return port
    raise _fail(
        "MOTHER_NODE_REMOVE_HELPER_SETUP_PROOF_PORT_UNAVAILABLE",
        f"survivor service {service_name!r} has no published host port",
    )


def _helper_python() -> str:
    return """
import hashlib
import http.server
import json
import os
import threading
import time
import traceback
import urllib.request

NODE = os.environ["MOTHER_NODE_NAME"]
RPC = os.environ.get("MOTHER_RPC_URL", "http://" + NODE + ":8545")
PORT = int(os.environ.get("MOTHER_PROOF_PORT", "8798"))
SERVICE_UUID = os.environ.get("MOTHER_SERVICE_UUID", "")
CONTROLLER_ID = os.environ.get("MOTHER_CONTROLLER_ID", "")
BESU_NETWORK = os.environ.get("MOTHER_BESU_NETWORK", "")
BESU_CONTAINER = os.environ.get("MOTHER_BESU_CONTAINER", "")
HELPER_NAME = os.environ.get("MOTHER_HELPER_NAME", "")
PROOF_PATH = "/tmp/mother-node-remove-helper-setup-proof.json"
ERROR_PATH = "/tmp/mother-node-remove-helper-setup-error.json"
HEALTH_PATH = "/tmp/mother-node-remove-helper-setup-healthy"

def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

def write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    os.replace(tmp, path)

def rpc(method, params):
    body = encoded({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    req = urllib.request.Request(
        RPC,
        data=body,
        headers={"Content-Type": "application/json", "Host": "localhost"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=6) as response:
        payload = json.loads(response.read(1048576).decode())
    if payload.get("error") is not None or "result" not in payload:
        raise RuntimeError(method + " failed: " + json.dumps(payload, sort_keys=True))
    return payload["result"]

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send_json_file(self, path, missing_status=404):
        try:
            with open(path, "rb") as handle:
                raw = handle.read(131072)
        except FileNotFoundError:
            self.send_response(missing_status)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path in ("/proof", "/proof.json"):
            self._send_json_file(PROOF_PATH)
            return
        if self.path in ("/health", "/healthz"):
            if os.path.exists(HEALTH_PATH):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok\\n")
            else:
                self._send_json_file(ERROR_PATH, missing_status=503)
            return
        if self.path in ("/last-error", "/last-error.json"):
            self._send_json_file(ERROR_PATH)
            return
        self.send_response(404)
        self.end_headers()

def serve():
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()

threading.Thread(target=serve, daemon=True).start()

def collect():
    chain_id = int(rpc("eth_chainId", []), 16)
    block_number = int(rpc("eth_blockNumber", []), 16)
    validators = [str(item).lower() for item in rpc("qbft_getValidatorsByBlockNumber", ["latest"])]
    try:
        pending_votes = rpc("qbft_getPendingVotes", [])
    except Exception as exc:
        pending_votes = {"unavailable": True, "error": str(exc)[:200]}
    genesis = rpc("eth_getBlockByNumber", ["0x0", False])
    if not isinstance(genesis, dict) or not genesis.get("hash"):
        raise RuntimeError("genesis block missing from RPC")
    latest = rpc("eth_getBlockByNumber", ["latest", False])
    if not isinstance(latest, dict) or not latest.get("hash"):
        raise RuntimeError("latest block missing from RPC")
    config_path = "/config/genesis.json"
    if not os.path.exists(config_path):
        raise RuntimeError("/config/genesis.json is not visible in helper container")
    with open(config_path, "rb") as handle:
        config_genesis_sha256 = hashlib.sha256(handle.read()).hexdigest()
    now = int(time.time())
    payload = {
        "kind": "main_computer.mother.node_remove_helper_setup_smoke.proof.v1",
        "node": NODE,
        "service_uuid": SERVICE_UUID,
        "controller_id": CONTROLLER_ID,
        "helper_name": HELPER_NAME,
        "besu_container": BESU_CONTAINER,
        "besu_network": BESU_NETWORK,
        "rpc_url": RPC,
        "chain_id": chain_id,
        "latest_block_number": block_number,
        "latest_block_hash": latest.get("hash"),
        "latest_block_parent_hash": latest.get("parentHash"),
        "latest_validator_set": validators,
        "pending_votes": pending_votes,
        "rpc_genesis_hash": genesis.get("hash"),
        "config_genesis_sha256": config_genesis_sha256,
        "mutating_chain_rpc_called": False,
        "qbft_propose_validator_vote_called": False,
        "service_deletion_called": False,
        "proof_server_port": PORT,
        "proved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_json(PROOF_PATH, payload)
    try:
        os.unlink(ERROR_PATH)
    except FileNotFoundError:
        pass
    with open(HEALTH_PATH, "w", encoding="ascii") as handle:
        handle.write(str(now))

while True:
    try:
        collect()
    except Exception as exc:
        try:
            os.unlink(HEALTH_PATH)
        except FileNotFoundError:
            pass
        write_json(
            ERROR_PATH,
            {
                "kind": "main_computer.mother.node_remove_helper_setup_smoke.error.v1",
                "error": str(exc),
                "type": type(exc).__name__,
                "traceback": traceback.format_exc(limit=6),
                "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
    time.sleep(5)
"""


def _single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _runner_script(
    *,
    node: str,
    service_uuid: str,
    helper_name: str,
    proof_host_port: int,
    controller_id: str,
) -> str:
    helper_b64 = base64.b64encode(_helper_python().encode("utf-8")).decode("ascii")
    node_q = _single_quote(node)
    uuid_q = _single_quote(service_uuid)
    helper_q = _single_quote(helper_name)
    controller_q = _single_quote(controller_id)
    port_q = _single_quote(str(proof_host_port))
    helper_b64_q = _single_quote(helper_b64)
    return f"""set -eu
echo mother-node-remove-helper-setup-runner-started
NODE={node_q}
SERVICE_UUID={uuid_q}
HELPER_NAME={helper_q}
CONTROLLER_ID={controller_q}
PROOF_HOST_PORT={port_q}
BESU_NAME="${{NODE}}-${{SERVICE_UUID}}"

BESU_ID="$(docker ps -q --filter "name=^/${{BESU_NAME}}$" | head -1 || true)"
if [ -z "$BESU_ID" ]; then
  echo "MOTHER_HELPER_SETUP_BESU_CONTAINER_NOT_FOUND name=${{BESU_NAME}}" >&2
  exit 20
fi

BESU_SERVICE="$(docker inspect -f '{{{{ index .Config.Labels "com.docker.compose.service" }}}}' "$BESU_ID" 2>/dev/null || true)"
if [ "$BESU_SERVICE" != "$NODE" ]; then
  echo "MOTHER_HELPER_SETUP_BESU_SERVICE_MISMATCH expected=$NODE actual=$BESU_SERVICE" >&2
  exit 21
fi

BESU_NETWORK="$SERVICE_UUID"
if ! docker network inspect "$BESU_NETWORK" >/dev/null 2>&1; then
  BESU_NETWORK="$(docker inspect -f '{{{{range $k,$v := .NetworkSettings.Networks}}}}{{{{println $k}}}}{{{{end}}}}' "$BESU_ID" | head -1)"
fi
if [ -z "$BESU_NETWORK" ]; then
  echo "MOTHER_HELPER_SETUP_BESU_NETWORK_NOT_FOUND besu=$BESU_ID" >&2
  exit 22
fi

echo "BESU_ID=$BESU_ID"
echo "BESU_NETWORK=$BESU_NETWORK"
echo "HELPER_NAME=$HELPER_NAME"
echo "PROOF_HOST_PORT=$PROOF_HOST_PORT"

docker rm -f "$HELPER_NAME" >/dev/null 2>&1 || true

docker run -d \\
  --name "$HELPER_NAME" \\
  --network "$BESU_NETWORK" \\
  --volumes-from "$BESU_ID":ro \\
  -p "${{PROOF_HOST_PORT}}:8798/tcp" \\
  --restart no \\
  --label main_computer.mother.component=node-remove-helper-setup-smoke \\
  --label main_computer.mother.cleanup_owner=mother-helper-cleanup2-yagni \\
  --label main_computer.mother.cleanup_required=true \\
  --label main_computer.mother.network_helper=true \\
  --label main_computer.mother.node="$NODE" \\
  --label main_computer.mother.service_uuid="$SERVICE_UUID" \\
  --label main_computer.mother.controller_id="$CONTROLLER_ID" \\
  --label main_computer.mother.besu_network="$BESU_NETWORK" \\
  --label main_computer.mother.not_a_chain_service=true \\
  --label main_computer.mother.no_service_deletion=true \\
  --label main_computer.mother.no_validator_vote=true \\
  -e MOTHER_NODE_NAME="$NODE" \\
  -e MOTHER_SERVICE_UUID="$SERVICE_UUID" \\
  -e MOTHER_CONTROLLER_ID="$CONTROLLER_ID" \\
  -e MOTHER_BESU_NETWORK="$BESU_NETWORK" \\
  -e MOTHER_BESU_CONTAINER="$BESU_ID" \\
  -e MOTHER_HELPER_NAME="$HELPER_NAME" \\
  -e MOTHER_RPC_URL="http://${{NODE}}:8545" \\
  -e MOTHER_PROOF_PORT=8798 \\
  python:3.12-alpine \\
  sh -lc "printf '%s' {helper_b64_q} | base64 -d > /tmp/mother-node-remove-helper-setup-smoke.py && exec python -u /tmp/mother-node-remove-helper-setup-smoke.py"

touch /tmp/mother-node-remove-helper-setup-runner-done
echo mother-node-remove-helper-setup-runner-complete
"""


def _runner_compose(service_name: str, runner_script: str) -> str:
    compose = {
        "services": {
            service_name: {
                "image": "docker:27-cli",
                "command": ["sh", "-lc", runner_script],
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
                "restart": "no",
                "labels": {
                    "main_computer.mother.component": "node-remove-helper-setup-runner",
                    "main_computer.mother.not_a_validator": "true",
                    "main_computer.mother.not_a_chain_service": "true",
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", "test -f /tmp/mother-node-remove-helper-setup-runner-done"],
                    "interval": "5s",
                    "timeout": "2s",
                    "retries": 3,
                    "start_period": "1s",
                },
            }
        }
    }
    return yaml.safe_dump(compose, sort_keys=False)


def _fetch_json_url(url: str, *, timeout: float, max_response_bytes: int) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read(max_response_bytes + 1)
    except Exception as exc:
        return None, {
            "url": url,
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc)[:400],
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    if len(raw) > max_response_bytes:
        return None, {"url": url, "ok": False, "status": status, "reason": "response-too-large"}
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except Exception as exc:
        payload = None
        error = str(exc)[:200]
    else:
        error = ""
    return (payload if isinstance(payload, Mapping) else None), {
        "url": url,
        "ok": 200 <= status < 300,
        "status": status,
        "byte_length": len(raw),
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "json_object": isinstance(payload, Mapping),
        "json_error": error,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _write_evidence(runtime_state_root: str | Path, network: str, node: str, evidence: Mapping[str, Any]) -> Path:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root))
    root = paths.evidence_root / EVIDENCE_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    name = f"{_stamp()}-{_safe_node_token(network)}-{_safe_node_token(node)}-{hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()[:16]}.json"
    path = root / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def setup_node_remove_helper_smoke(
    *,
    runtime_state_root: str | Path,
    network: str,
    controller_id: str,
    node: str,
    service_uuid: str,
    proof_host: str | None = None,
    base_host_port: int | None = None,
    proof_host_port: int | None = None,
    execute: bool,
    timeout: float,
    max_response_bytes: int,
    wait_seconds: float,
    poll_interval_seconds: float,
    keep_runner: bool,
) -> dict[str, Any]:
    network = _identifier(network, "network")
    controller_id = _identifier(controller_id, "controller_id")
    node = _identifier(node, "node")
    service_uuid = _uuid(service_uuid, "service_uuid")
    mode = "execute" if execute else "plan"

    private_state = _load_private_state(runtime_state_root, network=network, mode=mode)
    controller = resolve_coolify_controller(private_state, network, controller_id)
    controller_cfg = _controller_config(private_state, network=network, controller_id=controller_id)

    observations: list[dict[str, Any]] = []
    detail_endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
    detail = _http(
        controller,
        "GET",
        detail_endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=urllib.request.urlopen,
    )
    observations.append({
        "phase": "survivor-service-detail",
        "method": "GET",
        "endpoint": detail_endpoint,
        "status": detail["status"],
        "ok": detail["ok"],
        "response_sha256": detail["response_sha256"],
        "byte_length": detail["byte_length"],
        "elapsed_ms": detail["elapsed_ms"],
    })
    if not detail["ok"]:
        raise _fail("MOTHER_NODE_REMOVE_HELPER_SETUP_SERVICE_DETAIL_FAILED", f"service detail failed with HTTP {detail['status']}")

    compose_text = _compose_text(detail.get("payload") if isinstance(detail.get("payload"), Mapping) else {})
    base_port = int(base_host_port) if base_host_port is not None else _first_published_host_port(compose_text, service_name=node)
    if not 1 <= base_port <= 65535:
        raise _fail("MOTHER_NODE_REMOVE_HELPER_SETUP_INVALID_ARGUMENT", "base host port outside TCP range")
    proof_port = int(proof_host_port) if proof_host_port is not None else base_port + PROOF_PORT_OFFSET
    if not 1 <= proof_port <= 65535:
        raise _fail("MOTHER_NODE_REMOVE_HELPER_SETUP_INVALID_ARGUMENT", "proof host port outside TCP range")
    public_host = proof_host.strip() if isinstance(proof_host, str) and proof_host.strip() else _controller_public_host(controller)
    helper_name = f"mother-node-remove-helper-setup-{_safe_node_token(node)}-{service_uuid}"
    runner_name = f"mother-node-remove-helper-setup-runner-{_safe_node_token(controller_id)}-{_stamp().lower()}"
    proof_url = f"http://{public_host}:{proof_port}/proof"

    runner_script = _runner_script(
        node=node,
        service_uuid=service_uuid,
        helper_name=helper_name,
        proof_host_port=proof_port,
        controller_id=controller_id,
    )
    runner_compose = _runner_compose(runner_name, runner_script)
    result: dict[str, Any] = {
        "kind": KIND,
        "mode": mode,
        "network": network,
        "controller_id": controller_id,
        "node": node,
        "service_uuid": service_uuid,
        "helper_name": helper_name,
        "runner_name": runner_name,
        "proof_endpoint": {
            "kind": "main_computer.mother.node-remove-helper-setup-smoke-public-proof-endpoint.v1",
            "transport": "http-public-controller",
            "host": public_host,
            "base_host_port": base_port,
            "host_port": proof_port,
            "container_port": PROOF_CONTAINER_PORT,
            "url": proof_url,
        },
        "policy": {
            "chain_vote_performed": False,
            "service_deletion_performed": False,
            "topology_mutation_performed": False,
            "temporary_runner_service_created": bool(execute),
            "helper_container_replaced_by_exact_name": bool(execute),
            "cleanup_owner": "mother-helper-cleanup2-yagni",
        },
        "survivor_compose_sha256": hashlib.sha256(compose_text.encode("utf-8")).hexdigest(),
        "runner_compose_sha256": hashlib.sha256(runner_compose.encode("utf-8")).hexdigest(),
        "observations": observations,
        "created_at": _utc_now(),
    }

    runner_service_uuid: str | None = None
    if not execute:
        result["status"] = "planned"
        return result

    env_uuid = _resolve_environment_uuid(
        controller=controller,
        controller_id=controller_id,
        endpoint=f"/api/v1/projects/{urllib.parse.quote(str(controller_cfg['project_uuid']), safe='')}/environments",
        expected_name="mainnet",
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=urllib.request.urlopen,
        observations=observations,
    )
    body = _temporary_service_body(controller_cfg, runner_name, runner_compose)
    body["environment_uuid"] = env_uuid
    body["description"] = "Ephemeral Mother node-remove helper setup smoke runner"
    body["instant_deploy"] = False

    create = _http(
        controller,
        "POST",
        "/api/v1/services",
        body=body,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=urllib.request.urlopen,
    )
    result["runner_create"] = {
        "method": "POST",
        "endpoint": "/api/v1/services",
        "status": create["status"],
        "ok": create["ok"],
        "response_sha256": create["response_sha256"],
        "byte_length": create["byte_length"],
        "elapsed_ms": create["elapsed_ms"],
        "request_body_sha256": hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    if not create["ok"]:
        result["status"] = "failed"
        result["reason"] = "runner-create-failed"
        return result

    runner_service_uuid = _application_uuid(create.get("payload"))
    result["runner_service_uuid"] = runner_service_uuid
    start_endpoint = f"/api/v1/services/{urllib.parse.quote(runner_service_uuid, safe='')}/start"
    start = _http(
        controller,
        "POST",
        start_endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=urllib.request.urlopen,
    )
    result["runner_start"] = {
        "method": "POST",
        "endpoint": start_endpoint,
        "status": start["status"],
        "ok": start["ok"],
        "response_sha256": start["response_sha256"],
        "byte_length": start["byte_length"],
        "elapsed_ms": start["elapsed_ms"],
    }
    if not start["ok"]:
        result["status"] = "failed"
        result["reason"] = "runner-start-failed"
        return result

    proof_observations: list[dict[str, Any]] = []
    proof_payload: Mapping[str, Any] | None = None
    deadline = time.monotonic() + float(wait_seconds)
    while time.monotonic() <= deadline:
        payload, probe = _fetch_json_url(proof_url, timeout=min(timeout, 12.0), max_response_bytes=max_response_bytes)
        proof_observations.append(probe)
        if isinstance(payload, Mapping) and payload.get("kind") == "main_computer.mother.node_remove_helper_setup_smoke.proof.v1":
            proof_payload = payload
            break
        time.sleep(max(1.0, min(float(poll_interval_seconds), max(0.0, deadline - time.monotonic()))))

    result["proof_observations"] = proof_observations[-10:]
    result["helper_proof_payload"] = proof_payload
    result["helper_setup_verified"] = isinstance(proof_payload, Mapping)
    result["status"] = "pass" if result["helper_setup_verified"] else "failed"
    if not result["helper_setup_verified"]:
        result["reason"] = "proof-endpoint-not-verified"

    if runner_service_uuid is not None and not keep_runner:
        delete_endpoint = f"/api/v1/services/{urllib.parse.quote(runner_service_uuid, safe='')}"
        delete = _http(
            controller,
            "DELETE",
            delete_endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=urllib.request.urlopen,
        )
        result["runner_delete"] = {
            "method": "DELETE",
            "endpoint": delete_endpoint,
            "status": delete["status"],
            "ok": delete["ok"] or delete["status"] == 404,
            "response_sha256": delete["response_sha256"],
            "byte_length": delete["byte_length"],
            "elapsed_ms": delete["elapsed_ms"],
        }

    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Set up a non-voting node-remove helper smoke container.")
    parser.add_argument("command", choices=["plan", "setup"])
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--controller-id", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--service-uuid", required=True)
    parser.add_argument("--proof-host", default=None)
    parser.add_argument("--base-host-port", type=int, default=None)
    parser.add_argument("--proof-host-port", type=int, default=None)
    parser.add_argument("--execute", action="store_true", help="Required with setup to create/start the runner service.")
    parser.add_argument("--wait-seconds", type=float, default=90.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=_DEFAULT_MAX_RESPONSE_BYTES)
    parser.add_argument("--keep-runner", action="store_true", help="Do not delete the temporary Coolify runner service after setup.")
    parser.add_argument("--write-evidence", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.repo_root:
        global REPO_ROOT
        REPO_ROOT = _install_repo_import_path(args.repo_root)

    execute = args.command == "setup" and args.execute
    if args.command == "setup" and not args.execute:
        parser.error("setup requires --execute; use plan for a non-mutating plan")

    try:
        result = setup_node_remove_helper_smoke(
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            controller_id=args.controller_id,
            node=args.node,
            service_uuid=args.service_uuid,
            proof_host=args.proof_host,
            base_host_port=args.base_host_port,
            proof_host_port=args.proof_host_port,
            execute=execute,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            wait_seconds=args.wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            keep_runner=args.keep_runner,
        )
        if args.write_evidence:
            path = _write_evidence(args.runtime_state_root, args.network, args.node, result)
            result["evidence_path"] = str(path)
            result["evidence_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") in {"planned", "pass"} else 2
    except MotherNodeRemoveHelperSetupSmokeError as exc:
        print(json.dumps({"status": "failed", "code": exc.code, "message": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
