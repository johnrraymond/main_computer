#!/usr/bin/env python3
"""Wait for block production through one Mother/Coolify node.

This is a standalone diagnostic Mother script.  It does not patch an existing
service, restart a parent service, redeploy a stack, vote, or edit topology.
When run from the operator machine, it creates one temporary Coolify service on
the requested controller.  That service exposes a tiny HTTP endpoint whose only
job is to report the target Besu container's current block as observed from the
controller host.  The Mother-side script polls that temporary endpoint until the
reported block number advances.

Examples:

    python tools/mother_wait_for_block_advance.py mainnet coolify-a coolify-a-1
    python tools/mother_wait_for_block_advance.py mainnet coolify-c coolify-c-2
    python tools/mother_wait_for_block_advance.py mainnet coolify-c mainnetc-super1
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping
import urllib.parse
import urllib.request

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import CoolifyController, resolve_coolify_controller  # noqa: E402
from tools.mother.common.deployment_completed_helper_cleanup import (  # noqa: E402
    MotherDeploymentCompletedHelperCleanupError,
    _application_uuid,
    _controller_config,
    _http,
    _resolve_environment_uuid,
    _temporary_service_body,
)
from tools.mother.common.models import OperationIdentity  # noqa: E402
from tools.mother.common.paths import MotherPaths  # noqa: E402
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state  # noqa: E402
from tools.mother_helper_cleanup2_yagni import (  # noqa: E402
    MotherHelperCleanup2YagniError,
    _load_topology,
)


KIND = "main_computer.mother.wait_for_block_advance.v1"
EVIDENCE_SUBDIR = "mother-wait-for-block-advance"
WATCH_PREFIX = "mother-block-advance-watch"
RUNTIME_LOG_PREFIX = "MOTHER_BLOCK_ADVANCE_WATCH"
BLOCK_ENDPOINT_CONTAINER_PORT = 8797
BLOCK_ENDPOINT_HOST_PORT_OFFSET = 9000
START_TERMINAL_GRACE_SECONDS = 90.0
DIAGNOSTIC_LOG_EXCERPT_CHARS = 4000

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTROLLER_SLOT_RE = re.compile(r"^(coolify-[A-Za-z0-9._-]+)-([1-9][0-9]*)$")
SHORT_SLOT_RE = re.compile(r"^([A-Za-z])([1-9][0-9]*)$")


class MotherWaitForBlockAdvanceError(RuntimeError):
    """The block-advance watcher could not produce a trustworthy result."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _debug(enabled: bool, phase: str, **fields: Any) -> None:
    if not enabled:
        return
    parts = [f"{_utc_now()} mother-wait-for-block-advance.debug", f"phase={phase}"]
    for key in sorted(fields):
        value = fields[key]
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple)):
            rendered = json.dumps(value, sort_keys=True, separators=(",", ":"))
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    print(" ".join(parts), file=sys.stderr, flush=True)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _operation(network: str, command: str) -> OperationIdentity:
    stamp = _stamp()
    return OperationIdentity(
        operation_id=f"mother-wait-for-block-advance-{command}-{stamp}",
        request_id=f"mother-wait-for-block-advance-{command}",
        network=network,
        operation_kind="MOTHER-OP-DIAGNOSE",
    )


def _identifier(value: object, name: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or text in {".", ".."}
        or "/" in text
        or "\\" in text
        or "\x00" in text
        or not IDENTIFIER_RE.fullmatch(text)
    ):
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_INVALID_ARGUMENT",
            f"{name} must be a simple identifier",
        )
    return text


def _uuid(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text or not UUID_RE.fullmatch(text):
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_INVALID_ARGUMENT",
            f"{name} must be a valid UUID-like identifier",
        )
    return text


def _sha256(value: object, name: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(text):
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_INVALID_ARGUMENT",
            f"{name} must be a SHA-256 hex digest",
        )
    return text


def _positive_float(value: float | int, name: str) -> float:
    number = float(value)
    if number <= 0:
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_INVALID_ARGUMENT",
            f"{name} must be positive",
        )
    return number


def _nonnegative_float(value: float | int, name: str) -> float:
    number = float(value)
    if number < 0:
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_INVALID_ARGUMENT",
            f"{name} must be non-negative",
        )
    return number


def _load_private_state(runtime_state_root: str | Path, *, network: str) -> PrivateStateReadResult:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root)).resolve_private_state_paths()
    return read_private_state(paths, operation=_operation(network, "read-private-state"))


def _controller(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_id: str,
) -> CoolifyController:
    return resolve_coolify_controller(
        private_state,
        _identifier(network, "network"),
        _identifier(controller_id, "controller_id"),
        require_enabled=True,
        require_token=True,
    )


def _service_sort_key(record: Mapping[str, Any]) -> tuple[int, str]:
    node = str(record.get("node") or "")
    match = re.search(r"-super([0-9]+)$", node)
    if match:
        return (int(match.group(1)), node)
    return (10**9, node)


def _topology_chain_id(topology_info: Mapping[str, Any]) -> int | None:
    candidates = [
        topology_info.get("topology"),
        topology_info.get("document"),
    ]
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            value = candidate.get("chain_id")
            if type(value) is int and value > 0:
                return value
            if isinstance(value, str):
                try:
                    parsed = int(value, 0)
                except ValueError:
                    continue
                if parsed > 0:
                    return parsed
    return None


def resolve_watch_target(
    *,
    topology_info: Mapping[str, Any],
    network: str,
    controller_id: str,
    target: str,
) -> dict[str, Any]:
    network_id = _identifier(network, "network")
    controller = _identifier(controller_id, "controller_id")
    requested = _identifier(target, "target")

    services_raw = topology_info.get("services")
    if not isinstance(services_raw, list):
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_TOPOLOGY_INVALID",
            "topology did not provide service records",
        )

    services: list[dict[str, str]] = []
    for item in services_raw:
        if not isinstance(item, Mapping):
            continue
        node = item.get("node")
        item_controller = item.get("controller_id")
        service_uuid = item.get("service_uuid")
        if isinstance(node, str) and isinstance(item_controller, str) and isinstance(service_uuid, str):
            record: dict[str, Any] = {
                "node": _identifier(node, "topology service node"),
                "controller_id": _identifier(item_controller, "topology service controller_id"),
                "service_uuid": _uuid(service_uuid, "topology service_uuid"),
            }
            for key in ("validator_route", "p2p_route"):
                value = item.get(key)
                if isinstance(value, Mapping):
                    record[key] = dict(value)
            for key in ("p2p_port", "p2p_endpoint", "advertised_host", "public_host", "host", "hostname"):
                value = item.get(key)
                if value is not None:
                    record[key] = value
            services.append(record)

    exact = [item for item in services if item["node"] == requested]
    if exact:
        if len(exact) != 1:
            raise MotherWaitForBlockAdvanceError(
                "MOTHER_WAIT_FOR_BLOCK_ADVANCE_TARGET_AMBIGUOUS",
                f"target node {requested!r} matched multiple service records",
            )
        record = exact[0]
        if record["controller_id"] != controller:
            raise MotherWaitForBlockAdvanceError(
                "MOTHER_WAIT_FOR_BLOCK_ADVANCE_CONTROLLER_MISMATCH",
                f"target node {requested!r} belongs to {record['controller_id']}, not {controller}",
            )
        return record

    slot: int | None = None
    match = CONTROLLER_SLOT_RE.fullmatch(requested)
    if match and match.group(1) == controller:
        slot = int(match.group(2))

    short = SHORT_SLOT_RE.fullmatch(requested)
    if slot is None and short and controller == f"coolify-{short.group(1)}":
        slot = int(short.group(2))

    if slot is None:
        suffix = controller.removeprefix("coolify-")
        fallback = f"{network_id}{suffix}-super"
        if requested.startswith(fallback):
            exact_fallback = [item for item in services if item["node"] == requested and item["controller_id"] == controller]
            if len(exact_fallback) == 1:
                return exact_fallback[0]
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_TARGET_NOT_FOUND",
            f"target {requested!r} did not match a topology node or a {controller}-# slot",
        )

    controller_services = sorted(
        [item for item in services if item["controller_id"] == controller],
        key=_service_sort_key,
    )
    if slot > len(controller_services):
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_TARGET_NOT_FOUND",
            f"target {requested!r} requested slot {slot}, but {controller} has {len(controller_services)} topology service(s)",
        )
    return controller_services[slot - 1]


def _single_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _watch_service_name_prefix(controller_id: str, target_node: str) -> str:
    controller = _identifier(controller_id, "controller_id").replace("_", "-")
    node = _identifier(target_node, "target_node").replace("_", "-")
    return f"{WATCH_PREFIX}-{controller}-{node}-"


def _watch_service_name(controller_id: str, target_node: str) -> str:
    return f"{_watch_service_name_prefix(controller_id, target_node)}{_stamp().lower()}"




def _watch_script(
    *,
    network: str,
    controller_id: str,
    target_node: str,
    service_uuid: str,
    expected_chain_id: int | None,
    endpoint_port: int,
) -> str:
    """Build a proof-guardian-style Python block endpoint.

    This intentionally follows the existing Mother /proof guardians:
    a Python HTTP server serves a JSON artifact path while the same long-lived
    process keeps producing that artifact.  Docker access is still required to
    observe the target Besu container from the controller host, so the helper
    installs docker-cli inside the Python Alpine image if the image does not
    already provide it.
    """

    config = {
        "network": network,
        "controller_id": controller_id,
        "target_node": target_node,
        "service_uuid": service_uuid,
        "expected_chain_id": expected_chain_id,
        "endpoint_port": int(endpoint_port),
        "runtime_log_prefix": RUNTIME_LOG_PREFIX,
        "www": "/www",
        "block_file": "/www/block",
    }
    config_json = json.dumps(config, sort_keys=True, separators=(",", ":"))

    template = """import http.server
import json
import os
import shutil
import subprocess
import threading
import time
import traceback
from datetime import datetime, timezone

CONFIG = __CONFIG_JSON__
NETWORK = CONFIG["network"]
CONTROLLER_ID = CONFIG["controller_id"]
TARGET_NODE = CONFIG["target_node"]
SERVICE_UUID = CONFIG["service_uuid"]
EXPECTED_CHAIN_ID = CONFIG["expected_chain_id"]
ENDPOINT_PORT = int(CONFIG["endpoint_port"])
PREFIX = CONFIG["runtime_log_prefix"]
WWW = CONFIG["www"]
BLOCK_FILE = CONFIG["block_file"]
TARGET_CONTAINER = TARGET_NODE + "-" + SERVICE_UUID
MAX_FILE_BYTES = 65536

os.makedirs(WWW, exist_ok=True)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_text(value, limit=500):
    text = str(value)
    return text.replace("\\r", " ").replace("\\n", " ").replace(chr(34), " ").replace(chr(92), " ")[:limit]


def log(phase, **fields):
    parts = [PREFIX, "phase=" + str(phase)]
    for key in sorted(fields):
        parts.append(str(key) + "=" + safe_text(fields[key]))
    print(" ".join(parts), flush=True)


def write_json_file(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
    os.replace(tmp, path)


def write_status(ok, reason, block_number=None, block_hex="", chain_id=None, peer_count_hex=""):
    payload = {
        "ok": bool(ok),
        "reason": safe_text(reason),
        "network": NETWORK,
        "controller_id": CONTROLLER_ID,
        "target_node": TARGET_NODE,
        "service_uuid": SERVICE_UUID,
        "target_container": TARGET_CONTAINER,
        "chain_id": chain_id,
        "expected_chain_id": "" if EXPECTED_CHAIN_ID is None else str(EXPECTED_CHAIN_ID),
        "block_number": block_number,
        "block_hex": safe_text(block_hex),
        "peer_count_hex": safe_text(peer_count_hex),
        "observed_at": utc_now(),
        "server": "python-http.server.ThreadingHTTPServer",
    }
    write_json_file(BLOCK_FILE, payload)


class BlockHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        if self.path not in ("/block", "/block.json"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            with open(BLOCK_FILE, "rb") as handle:
                raw = handle.read(MAX_FILE_BYTES)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def serve_block():
    try:
        http.server.ThreadingHTTPServer(("0.0.0.0", ENDPOINT_PORT), BlockHandler).serve_forever()
    except Exception as exc:
        write_status(False, "http-server-failed:" + safe_text(exc))
        log("http_server_failed", error=safe_text(exc), traceback=safe_text(traceback.format_exc(), 1000))


def run_command(argv, timeout=30):
    return subprocess.run(
        argv,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def ensure_docker_cli():
    existing = shutil.which("docker")
    if existing:
        return True
    if not shutil.which("apk"):
        write_status(False, "docker-cli-missing-and-apk-missing")
        log("docker_cli_missing", reason="apk-missing")
        return False

    log("docker_cli_install_begin", package="docker-cli")
    proc = run_command(["apk", "add", "--no-cache", "docker-cli"], timeout=180)
    excerpt = (proc.stdout + " " + proc.stderr).strip()[-1000:]
    if proc.returncode != 0:
        write_status(False, "docker-cli-install-failed:" + str(proc.returncode) + ":" + safe_text(excerpt))
        log("docker_cli_install_failed", exit_code=proc.returncode, excerpt=excerpt)
        return False

    existing = shutil.which("docker")
    if not existing:
        write_status(False, "docker-cli-install-did-not-provide-binary")
        log("docker_cli_install_missing_binary")
        return False

    log("docker_cli_install_done", binary=existing)
    return True


def json_rpc(container_id, method, params=None):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": [] if params is None else params}, separators=(",", ":"))
    proc = run_command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "container:" + container_id,
            "alpine:3.20",
            "wget",
            "-qO-",
            "--header=Content-Type: application/json",
            "--post-data=" + payload,
            "http://127.0.0.1:8545",
        ],
        timeout=45,
    )
    if proc.returncode != 0:
        raise RuntimeError(method + " rpc failed rc=" + str(proc.returncode) + " stderr=" + safe_text(proc.stderr))
    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(method + " rpc returned invalid json:" + safe_text(proc.stdout)) from exc
    if isinstance(response, dict) and response.get("error") is not None:
        raise RuntimeError(method + " rpc error:" + safe_text(response.get("error")))
    if not isinstance(response, dict) or "result" not in response:
        raise RuntimeError(method + " rpc result missing")
    return response["result"]


def hex_to_int(value):
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("not a hex quantity")
    return int(value, 16)


def sample_once():
    if not ensure_docker_cli():
        return

    proc = run_command(["docker", "ps", "-a", "--filter", "name=" + TARGET_CONTAINER, "--format", "{{.ID}} {{.Names}}"], timeout=15)
    if proc.returncode != 0:
        write_status(False, "docker-ps-failed:" + safe_text(proc.stderr))
        log("docker_ps_failed", exit_code=proc.returncode, stderr=safe_text(proc.stderr))
        return

    matches = []
    for line in proc.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[1] == TARGET_CONTAINER:
            matches.append(parts[0])

    if len(matches) != 1:
        label_proc = run_command(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "label=com.docker.compose.project=" + SERVICE_UUID,
                "--filter",
                "label=com.docker.compose.service=" + TARGET_NODE,
                "--format",
                "{{.ID}} {{.Names}}",
            ],
            timeout=15,
        )
        if label_proc.returncode != 0:
            write_status(False, "docker-label-ps-failed:" + safe_text(label_proc.stderr))
            log("docker_label_ps_failed", exit_code=label_proc.returncode, stderr=safe_text(label_proc.stderr))
            return

        label_matches = []
        for line in label_proc.stdout.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                label_matches.append(parts[0])

        if len(label_matches) == 1:
            log("target_container_selected_by_compose_labels", container_id=label_matches[0], target_node=TARGET_NODE, service_uuid=SERVICE_UUID)
            matches = label_matches

    if len(matches) != 1:
        write_status(False, "container-not-unique:" + str(len(matches)))
        log("target_container_not_unique", count=len(matches), target_container=TARGET_CONTAINER)
        return

    cid = matches[0]
    chain_hex = json_rpc(cid, "eth_chainId", [])
    chain_id = hex_to_int(chain_hex)
    if EXPECTED_CHAIN_ID is not None and chain_id != int(EXPECTED_CHAIN_ID):
        write_status(False, "chain-id-mismatch:" + str(chain_id), chain_id=chain_id)
        log("chain_id_mismatch", actual_chain_id=chain_id, expected_chain_id=EXPECTED_CHAIN_ID)
        return

    block_hex = json_rpc(cid, "eth_blockNumber", [])
    block_number = hex_to_int(block_hex)
    try:
        peer_count_hex = json_rpc(cid, "net_peerCount", [])
    except Exception as exc:
        peer_count_hex = ""
        log("peer_count_unavailable", error=safe_text(exc))

    write_status(True, "ok", block_number=block_number, block_hex=block_hex, chain_id=chain_id, peer_count_hex=peer_count_hex)
    log("sample_ok", block_number=block_number, block_hex=block_hex, chain_id=chain_id, peer_count_hex=peer_count_hex)


log(
    "endpoint_start",
    network=NETWORK,
    controller_id=CONTROLLER_ID,
    target_node=TARGET_NODE,
    service_uuid=SERVICE_UUID,
    target_container=TARGET_CONTAINER,
    endpoint_port=ENDPOINT_PORT,
    server="python-http.server.ThreadingHTTPServer",
)
write_status(False, "starting")

server_thread = threading.Thread(target=serve_block, daemon=True)
server_thread.start()
log("block_server_started", path="/block", alt_path="/block.json", port=ENDPOINT_PORT, thread_alive=server_thread.is_alive())

while True:
    try:
        sample_once()
    except Exception as exc:
        write_status(False, "sample-failed:" + safe_text(exc))
        log("sample_failed", error=safe_text(exc), traceback=safe_text(traceback.format_exc(), 1000))
    time.sleep(2)
"""
    return template.replace("__CONFIG_JSON__", config_json)


def _compose_command_escape(text: str) -> str:
    # Docker Compose interpolates shell-dollar syntax before the container is
    # created.  The Python proof-style watcher is generated without such syntax,
    # but keep the helper for future shell payloads.
    return text.replace("$", "$$")


def _watch_compose(service_name: str, watch_script: str, *, host_port: int, endpoint_port: int) -> str:
    document = {
        "services": {
            service_name: {
                "image": "python:3.12-alpine",
                "command": ["python", "-u", "-c", watch_script],
                "volumes": ["/var/run/docker.sock:/var/run/docker.sock"],
                "ports": [f"{int(host_port)}:{int(endpoint_port)}"],
                "restart": "unless-stopped",
                "labels": {
                    "main_computer.mother.block_advance_watch": "true",
                    "main_computer.mother.diagnostic_only": "true",
                },
            }
        }
    }
    return yaml.safe_dump(document, sort_keys=False)


def _endpoint_port(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _host_port_from_endpoint(value: object) -> tuple[str | None, int | None]:
    text = str(value or "").strip()
    if not text:
        return None, None
    parsed = urllib.parse.urlsplit(text if "://" in text else f"tcp://{text}")
    host = parsed.hostname
    port = parsed.port
    return (host.strip() if isinstance(host, str) and host.strip() else None), port


def _first_route_mapping(target_record: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("validator_route", "p2p_route"):
        value = target_record.get(key)
        if isinstance(value, Mapping):
            return value
    return target_record


def _controller_public_host(controller_base_url: str) -> str:
    parsed = urllib.parse.urlsplit(controller_base_url if "://" in controller_base_url else f"http://{controller_base_url}")
    host = parsed.hostname
    if not isinstance(host, str) or not host.strip() or host.strip() in {"0.0.0.0", "::"}:
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_CONTROLLER_INVALID",
            "Coolify controller base URL lacks a usable public host for block endpoint polling",
        )
    return host.strip()


def _watch_block_endpoint(
    target_record: Mapping[str, Any],
    *,
    controller_base_url: str,
) -> dict[str, Any]:
    """Copy the existing Mother public-proof endpoint allocation pattern.

    Validator admission explicitly passes the controller-public host into the
    proof endpoint builder, then uses the node P2P port plus a fixed offset for
    the published host port.  Do the same here.  The node route P2P endpoint can
    be an internal Docker/VPN address, so it must not be used as the URL host
    that the Mother-side process polls.
    """

    route = _first_route_mapping(target_record)
    public_host = _controller_public_host(controller_base_url)
    endpoint_host, endpoint_port = _host_port_from_endpoint(route.get("p2p_endpoint") or target_record.get("p2p_endpoint"))

    p2p_port = _endpoint_port(route.get("p2p_port"))
    if p2p_port is None:
        p2p_port = _endpoint_port(target_record.get("p2p_port"))
    if p2p_port is None:
        p2p_port = endpoint_port
    if p2p_port is None:
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_ROUTE_INVALID",
            "target route lacks a valid p2p_port/p2p_endpoint for block endpoint port allocation",
        )

    host_port = int(p2p_port) + BLOCK_ENDPOINT_HOST_PORT_OFFSET
    if not 1 <= host_port <= 65535:
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_ROUTE_INVALID",
            "derived block endpoint host port is outside the valid TCP range",
        )

    url_host = f"[{public_host}]" if ":" in public_host and not public_host.startswith("[") else public_host
    return {
        "kind": "mother-block-advance-public-endpoint.v1",
        "transport": "http-public-controller",
        "host": public_host,
        "route_host": endpoint_host,
        "host_port": host_port,
        "container_port": BLOCK_ENDPOINT_CONTAINER_PORT,
        "url": urllib.parse.urlunsplit(("http", f"{url_host}:{host_port}", "/block", "", "")),
        "port_source": "p2p_port_plus_offset",
        "p2p_port": int(p2p_port),
        "host_port_offset": BLOCK_ENDPOINT_HOST_PORT_OFFSET,
    }


def _open_plain_json(
    url: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    started = time.monotonic()
    try:
        if callable(opener):
            response = opener(request, timeout=timeout)
        elif hasattr(opener, "open"):
            response = opener.open(request, timeout=timeout)
        else:
            raise TypeError("opener must be callable or provide open(request, timeout=...)")
        try:
            body = response.read(max_response_bytes + 1)
            status = int(response.getcode() if hasattr(response, "getcode") else getattr(response, "status", 0) or 0)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": 0,
            "payload": None,
            "error": str(exc),
            "response_sha256": None,
            "byte_length": 0,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    if len(body) > max_response_bytes:
        return {
            "ok": False,
            "status": status,
            "payload": None,
            "error": "response-too-large",
            "response_sha256": hashlib.sha256(body).hexdigest(),
            "byte_length": len(body),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        payload = None
        error = f"invalid-json:{exc}"
    else:
        error = None
    return {
        "ok": 200 <= status < 300 and isinstance(payload, Mapping),
        "status": status,
        "payload": payload,
        "error": error,
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "byte_length": len(body),
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def _endpoint_block_number(payload: object) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("block_number")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _watch_service_status(payload: object) -> str:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return ""
    if isinstance(payload, Mapping):
        return str(payload.get("status") or payload.get("service_status") or "").strip()
    return ""


def _service_status_is_running(value: object) -> bool:
    status = str(value or "").strip().lower()
    return status == "running" or status.startswith("running:")


def _service_status_is_terminal(value: object) -> bool:
    status = str(value or "").strip().lower()
    return status.startswith(("exited", "dead", "failed", "error"))


def _service_status_is_endpoint_probe_eligible(value: object) -> bool:
    status = str(value or "").strip().lower()
    if not status or _service_status_is_terminal(status):
        return False
    return _service_status_is_running(status) or status.startswith(("starting", "restarting"))


def _service_record_summaries(payload: object, *, service_uuid: str | None = None, service_name: str | None = None) -> list[dict[str, Any]]:
    """Return compact service records found in a Coolify payload.

    This is deliberately permissive because Coolify has returned service detail
    as both root objects and nested resource/application objects across versions.
    The caller still requires an exact UUID or exact name match before trusting a
    row.
    """

    uuid_wanted = str(service_uuid or "").strip()
    name_wanted = str(service_name or "").strip()
    records: list[dict[str, Any]] = []

    def walk(item: object) -> None:
        if isinstance(item, Mapping):
            uuid_value = item.get("uuid") or item.get("id") or item.get("service_uuid")
            name_value = item.get("name") or item.get("service_name")
            status_value = item.get("status") or item.get("service_status")
            uuid_text = str(uuid_value).strip() if isinstance(uuid_value, str) else ""
            name_text = str(name_value).strip() if isinstance(name_value, str) else ""
            status_text = str(status_value).strip() if isinstance(status_value, str) else ""
            if uuid_text or name_text or status_text:
                matched = (
                    bool(uuid_wanted and uuid_text == uuid_wanted)
                    or bool(name_wanted and name_text == name_wanted)
                )
                if matched or not uuid_wanted and not name_wanted:
                    records.append(
                        {
                            "uuid": uuid_text or None,
                            "name": name_text or None,
                            "status": status_text or None,
                            "matched": matched,
                        }
                    )
            for value in item.values():
                if isinstance(value, (Mapping, list, tuple)):
                    walk(value)
        elif isinstance(item, (list, tuple)):
            for value in item:
                walk(value)

    walk(payload)
    return records


def _summarize_service_payload(payload: object, *, service_uuid: str, service_name: str) -> dict[str, Any]:
    records = _service_record_summaries(payload, service_uuid=service_uuid, service_name=service_name)
    matched = [record for record in records if record.get("matched")]
    statuses = [str(record["status"]) for record in matched if record.get("status")]
    status = ""
    for candidate in statuses:
        if _service_status_is_running(candidate):
            status = candidate
            break
    if not status and statuses:
        status = statuses[-1]
    return {
        "matched": bool(matched),
        "matched_count": len(matched),
        "service_status": status or None,
        "running": _service_status_is_running(status),
        "terminal": _service_status_is_terminal(status),
        "endpoint_probe_eligible": _service_status_is_endpoint_probe_eligible(status),
        "matches_sample": matched[:5],
    }


def _clip_text(value: object, max_chars: int = DIAGNOSTIC_LOG_EXCERPT_CHARS) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"...<truncated {len(text) - max_chars} chars>"


def _safe_diag_subset(record: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "uuid",
        "id",
        "name",
        "status",
        "service_status",
        "resource_uuid",
        "application_uuid",
        "application_name",
        "service_uuid",
        "service_name",
        "deployment_uuid",
        "deployment_id",
        "deployment_status",
        "status",
        "message",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "type",
        "image",
        "ports",
        "fqdn",
    )
    result: dict[str, Any] = {}
    for key in allowed:
        if key not in record:
            continue
        value = record.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = _clip_text(value, 500) if isinstance(value, str) else value
    return result


def _walk_mappings(value: object) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []

    def walk(item: object) -> None:
        if isinstance(item, Mapping):
            found.append(item)
            for child in item.values():
                if isinstance(child, (Mapping, list, tuple)):
                    walk(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child)

    walk(value)
    return found


def _watch_detail_runtime_identity(payload: object, *, service_uuid: str, service_name: str) -> dict[str, Any]:
    service = str(service_uuid or "").strip()
    name = str(service_name or "").strip()
    applications: list[dict[str, Any]] = []
    compose_text = ""
    parent_status = None
    config_hash = None
    connect_to_docker_network = None

    if isinstance(payload, Mapping):
        parent_status = payload.get("status") or payload.get("service_status")
        config_hash = payload.get("config_hash")
        connect_to_docker_network = payload.get("connect_to_docker_network")
        raw_compose = payload.get("docker_compose")
        if isinstance(raw_compose, str):
            compose_text = raw_compose
        raw_apps = payload.get("applications")
        if isinstance(raw_apps, list):
            for item in raw_apps:
                if not isinstance(item, Mapping):
                    continue
                safe = _safe_diag_subset(item)
                if item.get("name") == name or item.get("service_id") or item.get("uuid"):
                    applications.append(safe)

    selected = None
    for item in applications:
        if item.get("name") == name:
            selected = item
            break
    if selected is None and applications:
        selected = applications[0]

    container_name = None
    match = re.search(r"(?m)^\s*container_name:\s*['\"]?([^'\"\s]+)", compose_text)
    if match:
        container_name = match.group(1).strip()

    ports = None
    if selected is not None:
        ports = selected.get("ports")
    if ports is None and compose_text:
        match = re.search(r"(?m)^\s*-\s*['\"]?([0-9]+:[0-9]+)['\"]?\s*$", compose_text)
        if match:
            ports = match.group(1)

    return {
        "service_uuid": service,
        "service_name": name,
        "parent_status": parent_status,
        "config_hash": config_hash,
        "connect_to_docker_network": connect_to_docker_network,
        "application_count": len(applications),
        "selected_application": selected,
        "selected_application_uuid": selected.get("uuid") if isinstance(selected, Mapping) else None,
        "selected_application_status": selected.get("status") if isinstance(selected, Mapping) else None,
        "expected_container_name": container_name,
        "published_ports": ports,
        "compose_sha256": hashlib.sha256(compose_text.encode("utf-8")).hexdigest() if compose_text else None,
        "compose_has_docker_socket_mount": "/var/run/docker.sock" in compose_text if compose_text else None,
        "compose_has_published_port": bool(re.search(r"(?m)^\s*-\s*[\'\"]?[0-9]+:[0-9]+[\'\"]?\s*$", compose_text)) if compose_text else None,
        "applications_sample": applications[:5],
    }


def _diagnostic_matches(payload: object, *, service_uuid: str, service_name: str, application_uuid: str | None) -> list[dict[str, Any]]:
    wanted = {str(service_uuid or "").strip(), str(service_name or "").strip()}
    if application_uuid:
        wanted.add(str(application_uuid).strip())
    wanted.discard("")
    matches: list[dict[str, Any]] = []
    for record in _walk_mappings(payload):
        values = {
            str(record.get(key) or "").strip()
            for key in (
                "uuid",
                "resource_uuid",
                "application_uuid",
                "service_uuid",
                "name",
                "service_name",
                "application_name",
                "deployment_uuid",
            )
        }
        if values & wanted:
            safe = _safe_diag_subset(record)
            if safe:
                matches.append(safe)
    unique: dict[str, dict[str, Any]] = {}
    for item in matches:
        unique[json.dumps(item, sort_keys=True, separators=(",", ":"))] = item
    return list(unique.values())[:20]


def _payload_log_excerpts(payload: object) -> list[str]:
    excerpts: list[str] = []

    def walk(item: object) -> None:
        if isinstance(item, Mapping):
            for key, value in item.items():
                key_text = str(key).lower()
                if key_text in {"log", "logs", "stdout", "stderr", "output", "message", "error"} and isinstance(value, str):
                    if value.strip():
                        excerpts.append(_clip_text(value, DIAGNOSTIC_LOG_EXCERPT_CHARS))
                elif isinstance(value, (Mapping, list, tuple)):
                    walk(value)
        elif isinstance(item, (list, tuple)):
            for child in item:
                walk(child)

    walk(payload)
    return excerpts[:5]


def _read_watch_failure_diagnostics(
    *,
    controller: CoolifyController,
    controller_id: str,
    server_uuid: str | None,
    service_uuid: str,
    service_name: str,
    service_detail: Mapping[str, Any] | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
    debug: bool,
    reason: str,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "watch_service_uuid")
    detail_payload = service_detail.get("payload") if isinstance(service_detail, Mapping) else None
    identity = _watch_detail_runtime_identity(detail_payload, service_uuid=service, service_name=service_name)
    application_uuid = identity.get("selected_application_uuid")
    application_uuid_text = str(application_uuid).strip() if isinstance(application_uuid, str) and application_uuid.strip() else None

    endpoints: list[tuple[str, str]] = [
        ("deployment-list", "/api/v1/deployments"),
        ("deployment-list-uuid", f"/api/v1/deployments?uuid={urllib.parse.quote(service, safe='')}"),
        ("deployment-list-resource-uuid", f"/api/v1/deployments?resource_uuid={urllib.parse.quote(service, safe='')}"),
    ]
    if server_uuid:
        endpoints.append(("server-resources", f"/api/v1/servers/{urllib.parse.quote(str(server_uuid), safe='')}/resources"))
    if application_uuid_text:
        endpoints.extend(
            [
                (
                    "service-application-logs",
                    f"/api/v1/services/{urllib.parse.quote(service, safe='')}/applications/{urllib.parse.quote(application_uuid_text, safe='')}/logs?lines=200&show_timestamps=false",
                ),
                (
                    "application-logs",
                    f"/api/v1/applications/{urllib.parse.quote(application_uuid_text, safe='')}/logs?lines=200",
                ),
            ]
        )
    endpoints.append(
        (
            "parent-service-logs",
            f"/api/v1/services/{urllib.parse.quote(service, safe='')}/logs?sub_service_name={urllib.parse.quote(service_name, safe='')}&lines=200&show_timestamps=false",
        )
    )

    channels: list[dict[str, Any]] = []
    _debug(
        debug,
        "temporary-service-failure-diagnostics-begin",
        service_uuid=service,
        service_name=service_name,
        reason=reason,
        selected_application_uuid=application_uuid_text,
        expected_container_name=identity.get("expected_container_name"),
        endpoint_count=len(endpoints),
    )
    for channel, endpoint in endpoints:
        response = _http(
            controller,
            "GET",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        matches = _diagnostic_matches(
            response.get("payload"),
            service_uuid=service,
            service_name=service_name,
            application_uuid=application_uuid_text,
        )
        log_excerpts = _payload_log_excerpts(response.get("payload"))
        record = {
            "channel": channel,
            "method": "GET",
            "endpoint": endpoint,
            "http_status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "match_count": len(matches),
            "matches": matches[:10],
            "log_excerpt_count": len(log_excerpts),
            "log_excerpts": log_excerpts,
        }
        channels.append(record)
        observations.append(
            {
                "method": "GET",
                "endpoint": endpoint,
                "status": response["status"],
                "ok": response["ok"],
                "response_sha256": response["response_sha256"],
                "byte_length": response["byte_length"],
                "elapsed_ms": response["elapsed_ms"],
                "controller_id": controller_id,
                "phase": "temporary-service-failure-diagnostics",
                "service_uuid": service,
                "service_name": service_name,
                "channel": channel,
                "match_count": len(matches),
                "log_excerpt_count": len(log_excerpts),
            }
        )
        _debug(
            debug,
            "temporary-service-failure-diagnostics-channel",
            service_uuid=service,
            channel=channel,
            http_ok=response.get("ok"),
            http_status=response.get("status"),
            match_count=len(matches),
            log_excerpt_count=len(log_excerpts),
            elapsed_ms=response.get("elapsed_ms"),
        )

    return {
        "reason": reason,
        "service_uuid": service,
        "service_name": service_name,
        "runtime_identity": identity,
        "channels": channels,
    }


def _deploy_response_is_accepted(payload: object, *, service_uuid: str | None = None) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("deployment_uuid") or payload.get("deployment_id"):
        return True

    deployments = payload.get("deployments")
    if isinstance(deployments, list) and deployments:
        if service_uuid:
            for deployment in deployments:
                if not isinstance(deployment, dict):
                    continue
                if str(deployment.get("resource_uuid") or deployment.get("uuid") or "") == service_uuid:
                    return True
            return False
        return any(isinstance(deployment, dict) for deployment in deployments)

    message = str(payload.get("message") or "").lower()
    return "deploy" in message and ("queued" in message or "queue" in message or "request" in message)


def _trigger_watch_service_deploy(
    *,
    controller: CoolifyController,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
    debug: bool,
) -> dict[str, Any]:
    """Trigger the Coolify deploy path for a newly created composed service.

    Raw service compose records need the generic deploy queue endpoint before the
    child application/container is materialized.  Coolify 4.3 reports this
    endpoint as POST-only; the service /start endpoint can acknowledge a queued
    start while leaving only the parsed service/application rows behind.
    """

    service = _uuid(service_uuid, "watch_service_uuid")
    deploy_query = urllib.parse.urlencode({"uuid": service, "force": "true"})
    attempts_spec = [
        ("POST", f"/api/v1/deploy?{deploy_query}", "generic-deploy"),
    ]
    attempts: list[dict[str, Any]] = []

    _debug(
        debug,
        "temporary-service-deploy-trigger-begin",
        service_uuid=service,
        service_name=service_name,
        endpoint_count=len(attempts_spec),
    )
    for method, endpoint, operation in attempts_spec:
        response = _http(
            controller,
            method,
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        receipt = {
            "operation": operation,
            "method": method,
            "endpoint": endpoint,
            "status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "payload": response.get("payload"),
            "service_uuid": service,
            "service_name": service_name,
            "cleanup_scope": "block-advance-watch-temporary-service",
        }
        attempts.append(receipt)
        observations.append(
            {
                key: receipt[key]
                for key in ("operation", "method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
            }
        )
        _debug(
            debug,
            "temporary-service-deploy-trigger-attempt",
            operation=operation,
            method=method,
            endpoint=endpoint,
            ok=receipt.get("ok"),
            http_status=receipt.get("status"),
            elapsed_ms=receipt.get("elapsed_ms"),
            payload=response.get("payload"),
        )
        accepted = bool(response["ok"] and _deploy_response_is_accepted(response.get("payload"), service_uuid=service))
        receipt["accepted"] = accepted
        if response["ok"] and not accepted:
            receipt["reason"] = "deploy-response-not-accepted"
            _debug(
                debug,
                "temporary-service-deploy-trigger-unaccepted",
                operation=operation,
                method=method,
                endpoint=endpoint,
                http_status=receipt.get("status"),
                payload=response.get("payload"),
            )
        if accepted:
            result = dict(receipt)
            result["ok"] = True
            result["attempts"] = attempts
            result["selected_operation"] = operation
            result["selected_endpoint"] = endpoint
            _debug(
                debug,
                "temporary-service-deploy-trigger-done",
                operation=operation,
                method=method,
                endpoint=endpoint,
                ok=True,
                http_status=receipt.get("status"),
            )
            return result

    result = {
        "method": attempts[-1]["method"] if attempts else None,
        "endpoint": attempts[-1]["endpoint"] if attempts else None,
        "status": attempts[-1]["status"] if attempts else None,
        "ok": False,
        "service_uuid": service,
        "service_name": service_name,
        "attempts": attempts,
        "cleanup_scope": "block-advance-watch-temporary-service",
    }
    _debug(
        debug,
        "temporary-service-deploy-trigger-failed",
        service_uuid=service,
        attempt_count=len(attempts),
    )
    return result



def _watch_service_cleanup_candidate_score(record: Mapping[str, Any]) -> int:
    """Prefer parent Coolify service records over nested application rows.

    ``GET /api/v1/services`` can include a service object and its application
    object with the same generated name. Only the parent service UUID is
    deletable at ``/api/v1/services/{uuid}``; the child application UUID returns
    404 from that endpoint. The score keeps cleanup focused on parent services
    while remaining tolerant of Coolify shape changes.
    """

    score = 0
    if isinstance(record.get("applications"), list) or isinstance(record.get("databases"), list):
        score += 100
    if "compose_parsing_version" in record or "docker_compose" in record or "docker_compose_raw" in record:
        score += 60
    if "destination_id" in record or "environment_id" in record or "server_id" in record:
        score += 40
    if "service_type" in record or "destination_type" in record:
        score += 20
    if "service_id" in record and ("image" in record or "ports" in record):
        score -= 100
    if "image" in record and "ports" in record:
        score -= 40
    return score


def _watch_service_cleanup_candidates(payload: object, *, service_name_prefix: str) -> list[dict[str, Any]]:
    prefix = str(service_name_prefix or "").strip()
    if not prefix:
        return []

    by_name: dict[str, dict[str, Any]] = {}

    def consider(record: Mapping[str, Any]) -> None:
        uuid_text = str(record.get("uuid") or "").strip()
        name_text = str(record.get("name") or record.get("service_name") or "").strip()
        status_text = str(record.get("status") or record.get("service_status") or "").strip()
        if not uuid_text or not name_text.startswith(prefix):
            return

        score = _watch_service_cleanup_candidate_score(record)
        if score < 0:
            return
        candidate = {
            "uuid": uuid_text,
            "name": name_text,
            "status": status_text or None,
            "record_score": score,
        }
        previous = by_name.get(name_text)
        if previous is None or score > int(previous.get("record_score") or 0):
            by_name[name_text] = candidate

    def walk(item: object) -> None:
        if isinstance(item, Mapping):
            consider(item)
            for value in item.values():
                if isinstance(value, (Mapping, list, tuple)):
                    walk(value)
        elif isinstance(item, (list, tuple)):
            for value in item:
                walk(value)

    walk(payload)
    candidates = list(by_name.values())
    candidates.sort(key=lambda item: (str(item.get("name") or ""), str(item.get("uuid") or "")))
    return candidates


def _list_stale_watch_services(
    *,
    controller: CoolifyController,
    controller_id: str,
    service_name_prefix: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    response = _http(
        controller,
        "GET",
        "/api/v1/services",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    candidates = _watch_service_cleanup_candidates(
        response.get("payload"),
        service_name_prefix=service_name_prefix,
    )
    receipt = {
        "method": "GET",
        "endpoint": "/api/v1/services",
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "phase": phase,
        "service_name_prefix": service_name_prefix,
        "candidate_count": len(candidates),
        "candidates": candidates[:25],
    }
    observations.append(
        {
            "method": "GET",
            "endpoint": "/api/v1/services",
            "status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "controller_id": controller_id,
            "phase": phase,
            "service_name_prefix": service_name_prefix,
            "candidate_count": len(candidates),
        }
    )
    return receipt


def _delete_stale_watch_services(
    *,
    controller: CoolifyController,
    controller_id: str,
    service_name_prefix: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
    debug: bool,
) -> dict[str, Any]:
    """Remove old block-watch services for the same controller/target.

    The block watcher intentionally reuses the proof-style deterministic host
    port derived from the target node's P2P port. That is only safe when there is
    at most one watcher for the target. Previous failed diagnostics are left for
    inspection, so this preflight deletes only services with the exact generated
    block-watch prefix for the same controller and target before creating a new
    watcher.
    """

    started = time.monotonic()
    prefix = str(service_name_prefix or "").strip()
    list_receipt = _list_stale_watch_services(
        controller=controller,
        controller_id=controller_id,
        service_name_prefix=prefix,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
        observations=observations,
        phase="stale-watch-service-cleanup-list",
    )
    candidates = list(list_receipt.get("candidates") or [])
    _debug(
        debug,
        "stale-watch-service-cleanup-list",
        service_name_prefix=prefix,
        candidate_count=len(candidates),
        candidates=candidates[:10],
    )
    delete_receipts: list[dict[str, Any]] = []
    for candidate in candidates:
        service_uuid = _uuid(candidate.get("uuid"), "stale_watch_service_uuid")
        endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
        response = _http(
            controller,
            "DELETE",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        cleanup_ok = bool(response["ok"]) or int(response["status"]) == 404
        receipt = {
            "method": "DELETE",
            "endpoint": endpoint,
            "status": response["status"],
            "ok": response["ok"],
            "cleanup_ok": cleanup_ok,
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "service_uuid": service_uuid,
            "service_name": candidate.get("name"),
            "service_status": candidate.get("status"),
            "record_score": candidate.get("record_score"),
            "cleanup_scope": "block-advance-watch-stale-precreate",
        }
        delete_receipts.append(receipt)
        observations.append(
            {
                key: receipt[key]
                for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")
            }
        )
        _debug(
            debug,
            "stale-watch-service-delete-done",
            service_uuid=service_uuid,
            service_name=candidate.get("name"),
            service_status=candidate.get("status"),
            ok=receipt.get("cleanup_ok"),
            http_status=response.get("status"),
            elapsed_ms=response.get("elapsed_ms"),
            record_score=candidate.get("record_score"),
        )

    wait_attempts: list[dict[str, Any]] = []
    remaining = candidates
    blocking_delete_failures = [receipt for receipt in delete_receipts if receipt.get("cleanup_ok") is not True]
    if candidates and not blocking_delete_failures:
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= max_wait_seconds:
                break
            time.sleep(min(max(0.01, poll_interval_seconds), max(0.0, max_wait_seconds - elapsed)))
            wait_receipt = _list_stale_watch_services(
                controller=controller,
                controller_id=controller_id,
                service_name_prefix=prefix,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                observations=observations,
                phase="stale-watch-service-cleanup-wait",
            )
            remaining = list(wait_receipt.get("candidates") or [])
            wait_attempts.append(
                {
                    "candidate_count": len(remaining),
                    "http_status": wait_receipt.get("status"),
                    "http_ok": wait_receipt.get("ok"),
                    "elapsed_milliseconds": int((time.monotonic() - started) * 1000),
                }
            )
            _debug(
                debug,
                "stale-watch-service-cleanup-wait",
                service_name_prefix=prefix,
                remaining_count=len(remaining),
                elapsed_ms=wait_receipt.get("elapsed_ms"),
            )
            if not remaining:
                break

    return {
        "service_name_prefix": prefix,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "delete_attempts": delete_receipts,
        "wait_attempts": wait_attempts,
        "remaining_count": len(remaining),
        "remaining": remaining[:25],
        "ok": (not remaining and not blocking_delete_failures),
        "blocking_delete_failures": blocking_delete_failures[:10],
        "wait_milliseconds": int((time.monotonic() - started) * 1000),
        "cleanup_scope": "block-advance-watch-stale-precreate",
    }


def _read_watch_service_detail(
    *,
    controller: CoolifyController,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "watch_service_uuid")
    endpoint = f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
    response = _http(
        controller,
        "GET",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    summary = _summarize_service_payload(response.get("payload"), service_uuid=service, service_name=service_name)
    receipt = {
        "method": "GET",
        "endpoint": endpoint,
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "service_uuid": service,
        "service_name": service_name,
        "phase": phase,
        "payload": response.get("payload"),
        "summary": summary,
    }
    observations.append(
        {
            "method": "GET",
            "endpoint": endpoint,
            "status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "controller_id": controller_id,
            "phase": phase,
            "service_uuid": service,
            "service_name": service_name,
            "service_status": summary.get("service_status"),
            "service_matched": summary.get("matched"),
            "service_running": summary.get("running"),
        }
    )
    return receipt


def _inventory_watch_service_candidates(
    *,
    controller: CoolifyController,
    controller_id: str,
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    response = _http(
        controller,
        "GET",
        "/api/v1/services",
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    candidates = [
        record for record in _service_record_summaries(response.get("payload"), service_uuid=service_uuid, service_name=service_name)
        if record.get("matched")
    ]
    receipt = {
        "method": "GET",
        "endpoint": "/api/v1/services",
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "service_uuid": service_uuid,
        "service_name": service_name,
        "phase": phase,
        "candidate_count": len(candidates),
        "candidates": candidates[:10],
    }
    observations.append(
        {
            "method": "GET",
            "endpoint": "/api/v1/services",
            "status": response["status"],
            "ok": response["ok"],
            "response_sha256": response["response_sha256"],
            "byte_length": response["byte_length"],
            "elapsed_ms": response["elapsed_ms"],
            "controller_id": controller_id,
            "phase": phase,
            "service_uuid": service_uuid,
            "service_name": service_name,
            "candidate_count": len(candidates),
        }
    )
    return receipt


def _wait_for_created_watch_service_row(
    *,
    controller: CoolifyController,
    controller_id: str,
    proposed_service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
    debug: bool,
) -> dict[str, Any]:
    """Resolve and prove the durable Coolify service row before start.

    The create response can be misleading if it contains a nested application UUID
    or if the service row is not immediately readable. This function refuses to
    move on until a GET service-detail or exact-name inventory match proves the
    actual service row.
    """

    started = time.monotonic()
    proposed = _uuid(proposed_service_uuid, "watch_service_uuid")
    attempts: list[dict[str, Any]] = []
    _debug(
        debug,
        "temporary-service-readback-begin",
        proposed_service_uuid=proposed,
        service_name=service_name,
        max_wait_seconds=max_wait_seconds,
    )
    while True:
        detail = _read_watch_service_detail(
            controller=controller,
            controller_id=controller_id,
            service_uuid=proposed,
            service_name=service_name,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observations=observations,
            phase="temporary-service-readback-detail",
        )
        detail_summary = detail.get("summary") if isinstance(detail.get("summary"), Mapping) else {}
        attempts.append(
            {
                "kind": "detail",
                "service_uuid": proposed,
                "http_status": detail.get("status"),
                "http_ok": detail.get("ok"),
                "summary": detail_summary,
            }
        )
        _debug(
            debug,
            "temporary-service-readback-poll",
            service_uuid=proposed,
            http_ok=detail.get("ok"),
            http_status=detail.get("status"),
            service_matched=detail_summary.get("matched"),
            service_status=detail_summary.get("service_status"),
            elapsed_ms=detail.get("elapsed_ms"),
        )
        if detail.get("ok") is True and detail_summary.get("matched") is True:
            return {
                "resolved": True,
                "reason": "service-detail-readable",
                "service_uuid": proposed,
                "service_name": service_name,
                "detail": detail,
                "inventory": None,
                "attempts": attempts,
                "wait_milliseconds": int((time.monotonic() - started) * 1000),
            }

        inventory = _inventory_watch_service_candidates(
            controller=controller,
            controller_id=controller_id,
            service_uuid=proposed,
            service_name=service_name,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observations=observations,
            phase="temporary-service-readback-inventory",
        )
        candidates = inventory.get("candidates") if isinstance(inventory.get("candidates"), list) else []
        attempts.append(
            {
                "kind": "inventory",
                "http_status": inventory.get("status"),
                "http_ok": inventory.get("ok"),
                "candidate_count": len(candidates),
                "candidates": candidates[:5],
            }
        )
        _debug(
            debug,
            "temporary-service-readback-inventory",
            http_ok=inventory.get("ok"),
            http_status=inventory.get("status"),
            candidate_count=len(candidates),
        )
        candidate_uuids = sorted(
            {
                str(candidate.get("uuid")).strip()
                for candidate in candidates
                if isinstance(candidate, Mapping) and isinstance(candidate.get("uuid"), str) and str(candidate.get("uuid")).strip()
            }
        )
        if len(candidate_uuids) == 1:
            resolved = candidate_uuids[0]
            resolved_detail = _read_watch_service_detail(
                controller=controller,
                controller_id=controller_id,
                service_uuid=resolved,
                service_name=service_name,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                observations=observations,
                phase="temporary-service-readback-resolved-detail",
            )
            resolved_summary = resolved_detail.get("summary") if isinstance(resolved_detail.get("summary"), Mapping) else {}
            _debug(
                debug,
                "temporary-service-readback-resolved",
                proposed_service_uuid=proposed,
                resolved_service_uuid=resolved,
                http_ok=resolved_detail.get("ok"),
                http_status=resolved_detail.get("status"),
                service_matched=resolved_summary.get("matched"),
                service_status=resolved_summary.get("service_status"),
            )
            if resolved_detail.get("ok") is True and resolved_summary.get("matched") is True:
                return {
                    "resolved": True,
                    "reason": "inventory-exact-name-match",
                    "service_uuid": resolved,
                    "proposed_service_uuid": proposed,
                    "service_name": service_name,
                    "detail": resolved_detail,
                    "inventory": inventory,
                    "attempts": attempts,
                    "wait_milliseconds": int((time.monotonic() - started) * 1000),
                }

        elapsed = time.monotonic() - started
        if elapsed >= max_wait_seconds:
            _debug(
                debug,
                "temporary-service-readback-timeout",
                proposed_service_uuid=proposed,
                service_name=service_name,
                attempt_count=len(attempts),
                wait_milliseconds=int(elapsed * 1000),
            )
            return {
                "resolved": False,
                "reason": "created-service-row-not-readable",
                "service_uuid": proposed,
                "service_name": service_name,
                "detail": detail,
                "inventory": inventory,
                "attempts": attempts,
                "wait_milliseconds": int(elapsed * 1000),
            }
        time.sleep(min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed)))


def _wait_for_watch_service_running(
    *,
    controller: CoolifyController,
    controller_id: str,
    server_uuid: str | None,
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    terminal_grace_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
    debug: bool,
) -> dict[str, Any]:
    started = time.monotonic()
    service = _uuid(service_uuid, "watch_service_uuid")
    statuses: list[str] = []
    attempts: list[dict[str, Any]] = []
    _debug(
        debug,
        "temporary-service-deploy-wait-begin",
        service_uuid=service,
        service_name=service_name,
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        terminal_grace_seconds=terminal_grace_seconds,
    )
    while True:
        detail = _read_watch_service_detail(
            controller=controller,
            controller_id=controller_id,
            service_uuid=service,
            service_name=service_name,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
            observations=observations,
            phase="temporary-service-deploy-status",
        )
        summary = detail.get("summary") if isinstance(detail.get("summary"), Mapping) else {}
        status = str(summary.get("service_status") or "")
        if status:
            statuses.append(status)
        elapsed = time.monotonic() - started
        terminal_grace_remaining = max(0.0, terminal_grace_seconds - elapsed)
        attempts.append(
            {
                "http_status": detail.get("status"),
                "http_ok": detail.get("ok"),
                "service_status": status or None,
                "service_matched": summary.get("matched"),
                "running": summary.get("running"),
                "terminal": summary.get("terminal"),
                "endpoint_probe_eligible": summary.get("endpoint_probe_eligible"),
                "elapsed_milliseconds": int(elapsed * 1000),
                "terminal_grace_remaining_seconds": terminal_grace_remaining,
            }
        )
        _debug(
            debug,
            "temporary-service-status-poll",
            service_uuid=service,
            http_ok=detail.get("ok"),
            http_status=detail.get("status"),
            service_matched=summary.get("matched"),
            service_status=status or None,
            running=summary.get("running"),
            terminal=summary.get("terminal"),
            endpoint_probe_eligible=summary.get("endpoint_probe_eligible"),
            terminal_grace_remaining_seconds=terminal_grace_remaining,
            elapsed_ms=detail.get("elapsed_ms"),
        )
        if detail.get("ok") is True and summary.get("matched") is True and summary.get("running") is True:
            _debug(
                debug,
                "temporary-service-running",
                service_uuid=service,
                service_status=status,
                wait_milliseconds=int(elapsed * 1000),
            )
            return {
                "running": True,
                "endpoint_probe_eligible": True,
                "reason": "service-running",
                "service_uuid": service,
                "service_name": service_name,
                "service_status": status,
                "statuses": statuses,
                "attempts": attempts,
                "detail": detail,
                "wait_milliseconds": int(elapsed * 1000),
            }
        if detail.get("ok") is True and summary.get("matched") is True and summary.get("endpoint_probe_eligible") is True:
            _debug(
                debug,
                "temporary-service-endpoint-probe-eligible",
                service_uuid=service,
                service_status=status,
                running=summary.get("running"),
                wait_milliseconds=int(elapsed * 1000),
            )
            return {
                "running": bool(summary.get("running")),
                "endpoint_probe_eligible": True,
                "reason": "service-endpoint-probe-eligible",
                "service_uuid": service,
                "service_name": service_name,
                "service_status": status,
                "statuses": statuses,
                "attempts": attempts,
                "detail": detail,
                "wait_milliseconds": int(elapsed * 1000),
            }
        if detail.get("ok") is True and summary.get("matched") is True and summary.get("terminal") is True:
            if elapsed < terminal_grace_seconds:
                _debug(
                    debug,
                    "temporary-service-terminal-ignored",
                    service_uuid=service,
                    service_status=status,
                    terminal_grace_remaining_seconds=max(0.0, terminal_grace_seconds - elapsed),
                    wait_milliseconds=int(elapsed * 1000),
                    reason="queued-start-grace-period",
                )
            else:
                _debug(
                    debug,
                    "temporary-service-terminal",
                    service_uuid=service,
                    service_status=status,
                    wait_milliseconds=int(elapsed * 1000),
                    terminal_grace_seconds=terminal_grace_seconds,
                )
                diagnostics = _read_watch_failure_diagnostics(
                    controller=controller,
                    controller_id=controller_id,
                    server_uuid=server_uuid,
                    service_uuid=service,
                    service_name=service_name,
                    service_detail=detail,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                    observations=observations,
                    debug=debug,
                    reason="service-terminal-after-start-grace-before-endpoint",
                )
                return {
                    "running": False,
                    "endpoint_probe_eligible": False,
                    "reason": "service-terminal-after-start-grace-before-endpoint",
                    "service_uuid": service,
                    "service_name": service_name,
                    "service_status": status,
                    "statuses": statuses,
                    "attempts": attempts,
                    "detail": detail,
                    "diagnostics": diagnostics,
                    "terminal_grace_seconds": terminal_grace_seconds,
                    "wait_milliseconds": int(elapsed * 1000),
                }
        if elapsed >= max_wait_seconds:
            _debug(
                debug,
                "temporary-service-deploy-timeout",
                service_uuid=service,
                last_service_status=status or None,
                attempt_count=len(attempts),
                wait_milliseconds=int(elapsed * 1000),
            )
            diagnostics = _read_watch_failure_diagnostics(
                controller=controller,
                controller_id=controller_id,
                server_uuid=server_uuid,
                service_uuid=service,
                service_name=service_name,
                service_detail=detail,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
                observations=observations,
                debug=debug,
                reason="service-not-running-before-endpoint",
            )
            return {
                "running": False,
                "endpoint_probe_eligible": False,
                "reason": "service-not-running-before-endpoint",
                "service_uuid": service,
                "service_name": service_name,
                "service_status": status or None,
                "statuses": statuses,
                "attempts": attempts,
                "detail": detail,
                "diagnostics": diagnostics,
                "wait_milliseconds": int(elapsed * 1000),
            }
        time.sleep(min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed)))


def _iter_log_text_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, child in value.items():
            key_text = str(key).lower()
            if key_text in {"log", "logs", "message", "output", "stdout", "stderr", "content", "data"}:
                result.extend(_iter_log_text_values(child))
            elif isinstance(child, (Mapping, list, tuple)):
                result.extend(_iter_log_text_values(child))
        return result
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            result.extend(_iter_log_text_values(item))
        return result
    return []


def _parse_watch_line(line: str) -> dict[str, str] | None:
    if RUNTIME_LOG_PREFIX not in line:
        return None
    text = line.split(RUNTIME_LOG_PREFIX, 1)[1].strip()
    event: dict[str, str] = {}
    for token in text.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key:
            event[key] = value
    return event if event else None


def _watch_events_from_payload(payload: object) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for text_value in _iter_log_text_values(payload):
        for line in str(text_value).splitlines():
            event = _parse_watch_line(line)
            if event is not None:
                events.append(event)
    return events


def _collect_watch_logs(
    *,
    controller: CoolifyController,
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    service = _uuid(service_uuid, "watch_service_uuid")
    endpoints = [
        f"/api/v1/services/{urllib.parse.quote(service, safe='')}/logs?sub_service_name={urllib.parse.quote(service_name, safe='')}",
        f"/api/v1/services/{urllib.parse.quote(service, safe='')}/logs",
    ]
    sources: list[dict[str, Any]] = []
    all_events: list[dict[str, str]] = []
    for endpoint in endpoints:
        response = _http(
            controller,
            "GET",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        events = _watch_events_from_payload(response.get("payload"))
        observations.append(
            {
                "method": "GET",
                "endpoint": endpoint,
                "status": response["status"],
                "ok": response["ok"],
                "response_sha256": response["response_sha256"],
                "byte_length": response["byte_length"],
                "elapsed_ms": response["elapsed_ms"],
                "phase": "watch-runtime-logs",
                "event_count": len(events),
            }
        )
        sources.append(
            {
                "source": f"coolify-get:{endpoint}",
                "endpoint": endpoint,
                "http_ok": response["ok"],
                "http_status": response["status"],
                "observed": bool(events),
                "event_count": len(events),
            }
        )
        all_events.extend(events)
    return {
        "observed": bool(all_events),
        "event_count": len(all_events),
        "events": all_events,
        "events_sample": all_events[:20],
        "sources": sources,
        "completed": any(event.get("phase") == "block_advanced" for event in all_events),
        "failed": any(event.get("phase") in {"failed", "timeout"} for event in all_events),
        "timeout": any(event.get("phase") == "timeout" for event in all_events),
    }


def _wait_for_block_endpoint(
    *,
    endpoint_url: str,
    expected_chain_id: int | None,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    observations: list[dict[str, Any]],
    debug: bool,
) -> dict[str, Any]:
    started = time.monotonic()
    endpoint_observations: list[dict[str, Any]] = []
    baseline_block: int | None = None
    latest_block: int | None = None
    baseline_payload: Mapping[str, Any] | None = None
    latest_payload: Mapping[str, Any] | None = None

    _debug(
        debug,
        "block-endpoint-wait-begin",
        endpoint_url=endpoint_url,
        expected_chain_id=expected_chain_id,
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    while True:
        response = _open_plain_json(
            endpoint_url,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        payload = response.get("payload")
        block_number = _endpoint_block_number(payload)
        endpoint_ok = response.get("ok") is True and isinstance(payload, Mapping) and payload.get("ok") is True
        chain_id = payload.get("chain_id") if isinstance(payload, Mapping) else None
        reason = str(payload.get("reason") or "") if isinstance(payload, Mapping) else str(response.get("error") or "")

        if endpoint_ok and expected_chain_id is not None:
            try:
                endpoint_ok = int(chain_id) == int(expected_chain_id)
            except (TypeError, ValueError):
                endpoint_ok = False
                reason = "chain-id-missing-or-invalid"

        observation = {
            "method": "GET",
            "endpoint": endpoint_url,
            "status": response.get("status"),
            "ok": response.get("ok"),
            "endpoint_ok": endpoint_ok,
            "response_sha256": response.get("response_sha256"),
            "byte_length": response.get("byte_length"),
            "elapsed_ms": response.get("elapsed_ms"),
            "phase": "block-endpoint-poll",
            "block_number": block_number,
            "chain_id": chain_id,
            "reason": reason,
        }
        observations.append(observation)
        endpoint_observations.append(observation)
        _debug(
            debug,
            "block-endpoint-poll",
            endpoint_ok=endpoint_ok,
            http_ok=response.get("ok"),
            http_status=response.get("status"),
            chain_id=chain_id,
            block_number=block_number,
            baseline_block_number=baseline_block,
            latest_block_number=latest_block,
            reason=reason,
            elapsed_ms=response.get("elapsed_ms"),
        )

        if endpoint_ok and block_number is not None:
            latest_block = block_number
            latest_payload = payload  # type: ignore[assignment]
            if baseline_block is None:
                baseline_block = block_number
                baseline_payload = payload  # type: ignore[assignment]
                _debug(
                    debug,
                    "block-endpoint-baseline",
                    baseline_block_number=baseline_block,
                    chain_id=chain_id,
                )
            elif block_number > baseline_block:
                elapsed = time.monotonic() - started
                _debug(
                    debug,
                    "block-endpoint-advanced",
                    baseline_block_number=baseline_block,
                    latest_block_number=latest_block,
                    block_advance=latest_block - baseline_block,
                    wait_milliseconds=int(elapsed * 1000),
                )
                return {
                    "completed": True,
                    "reason": "block-advanced",
                    "endpoint_url": endpoint_url,
                    "baseline_block_number": baseline_block,
                    "latest_block_number": latest_block,
                    "block_advance": latest_block - baseline_block,
                    "baseline_payload": dict(baseline_payload or {}),
                    "latest_payload": dict(latest_payload or {}),
                    "observations": endpoint_observations,
                    "observation_count": len(endpoint_observations),
                    "wait_milliseconds": int(elapsed * 1000),
                }

        elapsed = time.monotonic() - started
        if elapsed >= max_wait_seconds:
            _debug(
                debug,
                "block-endpoint-timeout",
                baseline_block_number=baseline_block,
                latest_block_number=latest_block,
                observation_count=len(endpoint_observations),
                wait_milliseconds=int(elapsed * 1000),
            )
            return {
                "completed": False,
                "reason": "block-endpoint-timeout",
                "endpoint_url": endpoint_url,
                "baseline_block_number": baseline_block,
                "latest_block_number": latest_block,
                "baseline_payload": dict(baseline_payload or {}),
                "latest_payload": dict(latest_payload or {}),
                "observations": endpoint_observations,
                "observation_count": len(endpoint_observations),
                "wait_milliseconds": int(elapsed * 1000),
            }

        time.sleep(min(poll_interval_seconds, max(0.0, max_wait_seconds - elapsed)))


def _delete_watch_service(
    *,
    controller: CoolifyController,
    service_uuid: str,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "watch_service_uuid")
    endpoint = f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
    response = _http(
        controller,
        "DELETE",
        endpoint,
        body=None,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    return {
        "method": "DELETE",
        "endpoint": endpoint,
        "status": response["status"],
        "ok": response["ok"],
        "response_sha256": response["response_sha256"],
        "byte_length": response["byte_length"],
        "elapsed_ms": response["elapsed_ms"],
        "cleanup_scope": "block-advance-watch-temporary-service",
    }


def run_block_advance_watch(
    private_state: PrivateStateReadResult,
    *,
    runtime_state_root: str | Path,
    network: str,
    controller_id: str,
    target: str,
    topology_evidence: str | Path | None = None,
    acknowledged_topology_evidence_sha256: str | None = None,
    expected_chain_id: int | None = None,
    timeout: float = 30.0,
    max_response_bytes: int = 4 * 1024 * 1024,
    max_wait_seconds: float = 600.0,
    poll_interval_seconds: float = 10.0,
    start_terminal_grace_seconds: float = START_TERMINAL_GRACE_SECONDS,
    leave_service: bool = False,
    delete_on_failure: bool = False,
    opener: Any = urllib.request.urlopen,
    debug: bool = True,
) -> dict[str, Any]:
    network_id = _identifier(network, "network")
    controller_name = _identifier(controller_id, "controller_id")
    timeout_s = _positive_float(timeout, "timeout")
    max_bytes = int(max_response_bytes)
    if max_bytes <= 0:
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_INVALID_ARGUMENT",
            "max_response_bytes must be positive",
        )
    max_wait = _positive_float(max_wait_seconds, "max_wait_seconds")
    poll_interval = _positive_float(poll_interval_seconds, "poll_interval_seconds")
    start_terminal_grace = _nonnegative_float(start_terminal_grace_seconds, "start_terminal_grace_seconds")
    if poll_interval > max_wait:
        raise MotherWaitForBlockAdvanceError(
            "MOTHER_WAIT_FOR_BLOCK_ADVANCE_INVALID_ARGUMENT",
            "poll_interval_seconds must be less than or equal to max_wait_seconds",
        )

    _debug(
        debug,
        "start",
        network=network_id,
        controller_id=controller_name,
        target=target,
        runtime_state_root=runtime_state_root,
        max_wait_seconds=max_wait,
        poll_interval_seconds=poll_interval,
        start_terminal_grace_seconds=start_terminal_grace,
    )

    topology = _load_topology(
        runtime_state_root,
        network=network_id,
        topology_evidence=topology_evidence,
        acknowledged_sha256=acknowledged_topology_evidence_sha256,
    )
    target_record = resolve_watch_target(
        topology_info=topology,
        network=network_id,
        controller_id=controller_name,
        target=target,
    )
    chain_id = expected_chain_id if expected_chain_id is not None else _topology_chain_id(topology)
    _debug(
        debug,
        "target-resolved",
        topology_path=topology.get("path"),
        topology_sha256=topology.get("sha256"),
        target_node=target_record["node"],
        target_service_uuid=target_record["service_uuid"],
        expected_chain_id=chain_id,
    )

    controller = _controller(private_state, network=network_id, controller_id=controller_name)
    controller_config = _controller_config(private_state, network=network_id, controller_id=controller_name)
    observations: list[dict[str, Any]] = []

    service_name_prefix = _watch_service_name_prefix(controller_name, target_record["node"])
    service_name = _watch_service_name(controller_name, target_record["node"])
    block_endpoint = _watch_block_endpoint(
        target_record,
        controller_base_url=controller.base_url,
    )
    endpoint_host_port = int(block_endpoint["host_port"])
    endpoint_url = str(block_endpoint["url"])
    _debug(
        debug,
        "endpoint-selected",
        service_name=service_name,
        endpoint_url=endpoint_url,
        host=block_endpoint.get("host"),
        route_host=block_endpoint.get("route_host"),
        p2p_port=block_endpoint.get("p2p_port"),
        host_port=endpoint_host_port,
        host_port_offset=block_endpoint.get("host_port_offset"),
        container_port=BLOCK_ENDPOINT_CONTAINER_PORT,
        port_source=block_endpoint.get("port_source"),
    )
    watch_script = _watch_script(
        network=network_id,
        controller_id=controller_name,
        target_node=target_record["node"],
        service_uuid=target_record["service_uuid"],
        expected_chain_id=chain_id,
        endpoint_port=BLOCK_ENDPOINT_CONTAINER_PORT,
    )
    compose = _watch_compose(
        service_name,
        watch_script,
        host_port=endpoint_host_port,
        endpoint_port=BLOCK_ENDPOINT_CONTAINER_PORT,
    )

    _debug(
        debug,
        "environment-resolve-begin",
        project_uuid=controller_config.get("project_uuid"),
        expected_environment_name=network_id,
    )
    environment_uuid = _resolve_environment_uuid(
        controller=controller,
        controller_id=controller_name,
        endpoint=f"/api/v1/projects/{urllib.parse.quote(str(controller_config['project_uuid']), safe='')}/environments",
        expected_name=network_id,
        timeout=timeout_s,
        max_response_bytes=max_bytes,
        opener=opener,
        observations=observations,
    )
    _debug(debug, "environment-resolved", environment_uuid=environment_uuid)

    body = _temporary_service_body(controller_config, service_name, compose)
    body["environment_uuid"] = environment_uuid
    body["environment_name"] = network_id
    body["description"] = "Ephemeral Mother block endpoint. Diagnostic only; does not touch target services."
    body["instant_deploy"] = False

    watch_service_uuid: str | None = None
    create_receipt: dict[str, Any] | None = None
    create_readback: dict[str, Any] | None = None
    start_receipt: dict[str, Any] | None = None
    deployment_readiness: dict[str, Any] | None = None
    completion: dict[str, Any] | None = None
    endpoint_failure_readback: dict[str, Any] | None = None
    endpoint_failure_diagnostics: dict[str, Any] | None = None
    delete_receipt: dict[str, Any] | None = None
    stale_cleanup: dict[str, Any] | None = None
    status = "failed"
    reason = "not-started"

    try:
        stale_cleanup = _delete_stale_watch_services(
            controller=controller,
            controller_id=controller_name,
            service_name_prefix=service_name_prefix,
            timeout=timeout_s,
            max_response_bytes=max_bytes,
            max_wait_seconds=min(max_wait, 30.0),
            poll_interval_seconds=poll_interval,
            opener=opener,
            observations=observations,
            debug=debug,
        )
        if stale_cleanup.get("ok") is not True:
            reason = "stale-watch-service-cleanup-failed"
            _debug(
                debug,
                "stale-watch-service-cleanup-failed",
                service_name_prefix=service_name_prefix,
                candidate_count=stale_cleanup.get("candidate_count"),
                remaining_count=stale_cleanup.get("remaining_count"),
                wait_milliseconds=stale_cleanup.get("wait_milliseconds"),
            )
        else:
            _debug(
                debug,
                "stale-watch-service-cleanup-done",
                service_name_prefix=service_name_prefix,
                candidate_count=stale_cleanup.get("candidate_count"),
                remaining_count=stale_cleanup.get("remaining_count"),
                wait_milliseconds=stale_cleanup.get("wait_milliseconds"),
            )
            _debug(
                debug,
                "temporary-service-create-begin",
                service_name=service_name,
                compose_sha256=hashlib.sha256(compose.encode("utf-8")).hexdigest(),
                watch_script_sha256=hashlib.sha256(watch_script.encode("utf-8")).hexdigest(),
            )
            create_response = _http(
                controller,
                "POST",
                "/api/v1/services",
                body=body,
                timeout=timeout_s,
                max_response_bytes=max_bytes,
                opener=opener,
            )
            create_receipt = {
                "method": "POST",
                "endpoint": "/api/v1/services",
                "status": create_response["status"],
                "ok": create_response["ok"],
                "response_sha256": create_response["response_sha256"],
                "byte_length": create_response["byte_length"],
                "elapsed_ms": create_response["elapsed_ms"],
                "service_name": service_name,
                "request_body_sha256": hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                "compose_sha256": hashlib.sha256(compose.encode("utf-8")).hexdigest(),
                "watch_script_sha256": hashlib.sha256(watch_script.encode("utf-8")).hexdigest(),
                "payload": create_response.get("payload"),
                "cleanup_scope": "block-advance-watch-temporary-service",
            }
            observations.append({key: create_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
            _debug(
                debug,
                "temporary-service-create-done",
                ok=create_receipt.get("ok"),
                http_status=create_receipt.get("status"),
                elapsed_ms=create_receipt.get("elapsed_ms"),
                payload=create_response.get("payload"),
            )
            if not create_response["ok"]:
                reason = "create-failed"
                completion = None
            else:
                proposed_service_uuid = _application_uuid(create_response.get("payload"))
                create_receipt["service_uuid"] = proposed_service_uuid
                _debug(debug, "temporary-service-created", proposed_service_uuid=proposed_service_uuid)

                create_readback = _wait_for_created_watch_service_row(
                    controller=controller,
                    controller_id=controller_name,
                    proposed_service_uuid=proposed_service_uuid,
                    service_name=service_name,
                    timeout=timeout_s,
                    max_response_bytes=max_bytes,
                    max_wait_seconds=min(max_wait, 60.0),
                    poll_interval_seconds=poll_interval,
                    opener=opener,
                    observations=observations,
                    debug=debug,
                )
                if create_readback.get("resolved") is not True:
                    watch_service_uuid = proposed_service_uuid
                    reason = str(create_readback.get("reason") or "created-service-row-not-readable")
                    _debug(
                        debug,
                        "temporary-service-readback-failed",
                        proposed_service_uuid=proposed_service_uuid,
                        reason=reason,
                    )
                else:
                    watch_service_uuid = _uuid(create_readback.get("service_uuid"), "watch_service_uuid")
                    if watch_service_uuid != proposed_service_uuid:
                        create_receipt["resolved_service_uuid"] = watch_service_uuid
                    _debug(
                        debug,
                        "temporary-service-readback-done",
                        proposed_service_uuid=proposed_service_uuid,
                        watch_service_uuid=watch_service_uuid,
                        reason=create_readback.get("reason"),
                        wait_milliseconds=create_readback.get("wait_milliseconds"),
                    )

                    start_receipt = _trigger_watch_service_deploy(
                        controller=controller,
                        controller_id=controller_name,
                        service_uuid=watch_service_uuid,
                        service_name=service_name,
                        timeout=timeout_s,
                        max_response_bytes=max_bytes,
                        opener=opener,
                        observations=observations,
                        debug=debug,
                    )
                    if start_receipt.get("ok") is not True:
                        reason = "deploy-trigger-failed"
                    else:
                        deployment_readiness = _wait_for_watch_service_running(
                            controller=controller,
                            controller_id=controller_name,
                            server_uuid=str(controller_config.get("server_uuid") or "") or None,
                            service_uuid=watch_service_uuid,
                            service_name=service_name,
                            timeout=timeout_s,
                            max_response_bytes=max_bytes,
                            max_wait_seconds=min(max_wait, 120.0),
                            poll_interval_seconds=poll_interval,
                            terminal_grace_seconds=min(start_terminal_grace, min(max_wait, 120.0)),
                            opener=opener,
                            observations=observations,
                            debug=debug,
                        )
                        if (
                            deployment_readiness.get("running") is not True
                            and deployment_readiness.get("endpoint_probe_eligible") is not True
                        ):
                            reason = str(deployment_readiness.get("reason") or "service-not-running-before-endpoint")
                            _debug(
                                debug,
                                "temporary-service-not-running",
                                watch_service_uuid=watch_service_uuid,
                                reason=reason,
                                service_status=deployment_readiness.get("service_status"),
                            )
                        else:
                            completion = _wait_for_block_endpoint(
                                endpoint_url=endpoint_url,
                                expected_chain_id=chain_id,
                                timeout=timeout_s,
                                max_response_bytes=max_bytes,
                                max_wait_seconds=max_wait,
                                poll_interval_seconds=poll_interval,
                                opener=opener,
                                observations=observations,
                                debug=debug,
                            )
                            if completion.get("completed") is True:
                                status = "pass"
                                reason = "block-advanced"
                            else:
                                reason = str(completion.get("reason") or "block-advance-not-proven")
                                endpoint_failure_readback = _read_watch_service_detail(
                                    controller=controller,
                                    controller_id=controller_name,
                                    service_uuid=watch_service_uuid,
                                    service_name=service_name,
                                    timeout=timeout_s,
                                    max_response_bytes=max_bytes,
                                    opener=opener,
                                    observations=observations,
                                    phase="temporary-service-post-endpoint-failure-readback",
                                )
                                endpoint_failure_diagnostics = _read_watch_failure_diagnostics(
                                    controller=controller,
                                    controller_id=controller_name,
                                    server_uuid=str(controller_config.get("server_uuid") or "") or None,
                                    service_uuid=watch_service_uuid,
                                    service_name=service_name,
                                    service_detail=endpoint_failure_readback,
                                    timeout=timeout_s,
                                    max_response_bytes=max_bytes,
                                    opener=opener,
                                    observations=observations,
                                    debug=debug,
                                    reason=reason,
                                )
    finally:
        should_delete = (
            watch_service_uuid is not None
            and not leave_service
            and (status == "pass" or delete_on_failure)
        )
        if should_delete:
            try:
                _debug(debug, "temporary-service-delete-begin", watch_service_uuid=watch_service_uuid)
                delete_receipt = _delete_watch_service(
                    controller=controller,
                    service_uuid=watch_service_uuid,
                    timeout=timeout_s,
                    max_response_bytes=max_bytes,
                    opener=opener,
                )
                observations.append({key: delete_receipt[key] for key in ("method", "endpoint", "status", "ok", "response_sha256", "byte_length", "elapsed_ms")})
                _debug(
                    debug,
                    "temporary-service-delete-done",
                    ok=delete_receipt.get("ok"),
                    http_status=delete_receipt.get("status"),
                    elapsed_ms=delete_receipt.get("elapsed_ms"),
                )
            except Exception as exc:  # noqa: BLE001
                delete_receipt = {
                    "method": "DELETE",
                    "endpoint": f"/api/v1/services/{watch_service_uuid}",
                    "ok": False,
                    "error": str(exc),
                    "cleanup_scope": "block-advance-watch-temporary-service",
                }
                _debug(debug, "temporary-service-delete-error", error=str(exc))
        elif watch_service_uuid is not None:
            _debug(
                debug,
                "temporary-service-left-for-inspection",
                watch_service_uuid=watch_service_uuid,
                status=status,
                reason=reason,
                delete_on_failure=delete_on_failure,
            )

    _debug(debug, "finish", status=status, reason=reason)
    endpoint_observations = completion.get("observations") if isinstance(completion, Mapping) else []

    return {
        "kind": KIND,
        "schema_version": 1,
        "observed_at": _utc_now(),
        "status": status,
        "reason": reason,
        "network": network_id,
        "controller_id": controller_name,
        "target": target,
        "target_node": target_record["node"],
        "service_uuid": target_record["service_uuid"],
        "expected_chain_id": chain_id,
        "topology_evidence": {
            "path": str(topology.get("path")),
            "sha256": topology.get("sha256"),
            "discovered_from_disk": topology.get("discovered"),
        },
        "watch_service": {
            "service_name": service_name,
            "service_uuid": watch_service_uuid,
            "left_for_inspection": bool(watch_service_uuid and delete_receipt is None),
            "delete_on_failure": bool(delete_on_failure),
        },
        "block_endpoint": dict(block_endpoint),
        "stale_cleanup": stale_cleanup,
        "create": create_receipt,
        "create_readback": create_readback,
        "start": start_receipt,
        "deployment_readiness": deployment_readiness,
        "completion": completion,
        "endpoint_failure_readback": endpoint_failure_readback,
        "endpoint_failure_diagnostics": endpoint_failure_diagnostics,
        "delete": delete_receipt,
        "observations": observations,
        "summary": {
            "block_advance_completed": status == "pass",
            "block_advance_wait_performed": (
                start_receipt is not None
                and start_receipt.get("ok") is True
                and deployment_readiness is not None
                and (
                    deployment_readiness.get("running") is True
                    or deployment_readiness.get("endpoint_probe_eligible") is True
                )
            ),
            "block_advance_max_wait_seconds": max_wait,
            "block_advance_poll_interval_seconds": poll_interval,
            "start_terminal_grace_seconds": start_terminal_grace,
            "baseline_block_number": (
                completion.get("baseline_block_number") if isinstance(completion, Mapping) else None
            ),
            "latest_block_number": (
                completion.get("latest_block_number") if isinstance(completion, Mapping) else None
            ),
            "block_endpoint_observed": bool(endpoint_observations),
            "block_endpoint_observation_count": len(endpoint_observations) if isinstance(endpoint_observations, list) else 0,
            "stale_watch_service_cleanup_ok": stale_cleanup is not None and stale_cleanup.get("ok") is True,
            "stale_watch_service_cleanup_candidate_count": (
                stale_cleanup.get("candidate_count") if isinstance(stale_cleanup, Mapping) else None
            ),
            "stale_watch_service_cleanup_remaining_count": (
                stale_cleanup.get("remaining_count") if isinstance(stale_cleanup, Mapping) else None
            ),
            "temporary_service_created": create_receipt is not None and create_receipt.get("ok") is True,
            "temporary_service_readback_resolved": create_readback is not None and create_readback.get("resolved") is True,
            "temporary_service_running_before_endpoint": deployment_readiness is not None and deployment_readiness.get("running") is True,
            "temporary_service_endpoint_probe_eligible_before_endpoint": (
                deployment_readiness is not None
                and (
                    deployment_readiness.get("running") is True
                    or deployment_readiness.get("endpoint_probe_eligible") is True
                )
            ),
            "temporary_service_status_after_endpoint_failure": (
                endpoint_failure_readback.get("summary", {}).get("service_status")
                if isinstance(endpoint_failure_readback, Mapping)
                and isinstance(endpoint_failure_readback.get("summary"), Mapping)
                else None
            ),
            "endpoint_failure_diagnostics_captured": endpoint_failure_diagnostics is not None,
            "endpoint_failure_diagnostics_channel_count": (
                len(endpoint_failure_diagnostics.get("channels", []))
                if isinstance(endpoint_failure_diagnostics, Mapping)
                and isinstance(endpoint_failure_diagnostics.get("channels"), list)
                else 0
            ),
            "temporary_service_left_for_inspection": bool(watch_service_uuid and delete_receipt is None),
            "temporary_service_deleted": delete_receipt is not None and delete_receipt.get("ok") is True,
        },
    }


def _write_evidence(runtime_state_root: str | Path, result: Mapping[str, Any]) -> Path:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root))
    root = paths.evidence_root / EVIDENCE_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    target_node = _identifier(str(result.get("target_node") or "unknown"), "target_node")
    path = root / f"{_stamp()}-{result.get('network')}-{target_node}.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create a temporary diagnostic Coolify service and wait until the requested "
            "Mother node's Besu eth_blockNumber advances."
        )
    )
    parser.add_argument("network", help="Mother network id, for example mainnet")
    parser.add_argument("controller_id", help="Coolify controller id, for example coolify-a or coolify-c")
    parser.add_argument(
        "target",
        help=(
            "Topology node name or controller slot alias. Examples: mainneta-super1, "
            "mainnetc-super2, coolify-a-1, coolify-c-2, a1, c2"
        ),
    )
    parser.add_argument("--runtime-state-root", default=Path("runtime/state"), type=Path)
    parser.add_argument("--topology-evidence", type=Path)
    parser.add_argument("--acknowledge-topology-evidence-sha256")
    parser.add_argument("--expected-chain-id", type=int)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--max-wait-seconds", type=float, default=600.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=10.0)
    parser.add_argument(
        "--start-terminal-grace-seconds",
        type=float,
        default=START_TERMINAL_GRACE_SECONDS,
        help=(
            "After a Coolify deploy trigger queues a job, ignore exited/dead service status "
            "for this many seconds before treating it as terminal."
        ),
    )
    parser.add_argument("--leave-service", action="store_true", help="Leave the temporary diagnostic service for manual log inspection")
    parser.add_argument("--delete-on-failure", action="store_true", help="Delete the temporary service even when the diagnostic fails")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress/debug lines on stderr")
    parser.add_argument("--write-evidence", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        private_state = _load_private_state(args.runtime_state_root, network=args.network)
        result = run_block_advance_watch(
            private_state,
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            controller_id=args.controller_id,
            target=args.target,
            topology_evidence=args.topology_evidence,
            acknowledged_topology_evidence_sha256=args.acknowledge_topology_evidence_sha256,
            expected_chain_id=args.expected_chain_id,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            start_terminal_grace_seconds=args.start_terminal_grace_seconds,
            leave_service=args.leave_service,
            delete_on_failure=args.delete_on_failure,
            debug=not args.quiet,
        )
        if args.write_evidence:
            result = dict(result)
            result["evidence_path"] = str(_write_evidence(args.runtime_state_root, result))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("status") == "pass" else 2
    except (
        MotherWaitForBlockAdvanceError,
        MotherHelperCleanup2YagniError,
        MotherDeploymentCompletedHelperCleanupError,
    ) as exc:
        print(
            json.dumps(
                {
                    "kind": KIND,
                    "observed_at": _utc_now(),
                    "status": "failed",
                    "error_code": getattr(exc, "code", type(exc).__name__),
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
