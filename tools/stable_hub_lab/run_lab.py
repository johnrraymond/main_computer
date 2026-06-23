from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import secrets
import socket
import threading
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address
from main_computer.stable_hub_topology import (
    StableHubTopologyError,
    build_lab_plan,
    load_stable_hub_topology,
)


DEFAULT_TOPOLOGY = Path("deploy/stable-hub-lab/dev-topology.json")
DEFAULT_MSK_ORIGIN = "stable-hub-lab-msk-smoke"
DEFAULT_MSK_SMOKE_WALLET = Path(".main-computer/stable-hub-lab/msk-smoke-wallet.json")
DEFAULT_REQUESTER_WALLET = Path(".main-computer/stable-hub-lab/requester-wallet.json")
DEFAULT_WORKER_WALLET = Path(".main-computer/stable-hub-lab/worker-wallet.json")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or run the stable Hub dev topology contract.",
    )
    parser.add_argument(
        "--topology",
        default=str(DEFAULT_TOPOLOGY),
        help="Path to a stable Hub topology JSON file.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate topology and print the deterministic lab plan.",
    )
    parser.add_argument(
        "--serve-cluster",
        action="store_true",
        help="Start one stable Hub process for every concrete Hub in the topology.",
    )
    parser.add_argument(
        "--check-cluster",
        action="store_true",
        help="Check that every concrete stable Hub in the topology is answering.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run realistic Stable Hub requester/worker verification traffic against the topology.",
    )
    parser.add_argument(
        "--verify-stress",
        action="store_true",
        help="Run integrated Stable Hub market, reconnect, and payout entropy verification.",
    )
    parser.add_argument(
        "--smoke-msk",
        action="store_true",
        help="Issue an MSK on one concrete Hub and validate it on another.",
    )
    parser.add_argument(
        "--smoke-worker-live-session",
        action="store_true",
        help="Open a real WebSocket worker live session, auth with MSK, pong over the same socket, and verify owner directory visibility.",
    )
    parser.add_argument(
        "--wallet",
        default=str(DEFAULT_MSK_SMOKE_WALLET),
        help=(
            "Wallet JSON containing private_key for --smoke-msk. "
            "The lab creates a throwaway dev wallet at this path if it does not exist."
        ),
    )
    parser.add_argument(
        "--requester-wallet",
        default=str(DEFAULT_REQUESTER_WALLET),
        help=(
            "Wallet JSON containing private_key for the requester actor in --verify. "
            "The lab creates a throwaway dev wallet at this path if it does not exist."
        ),
    )
    parser.add_argument(
        "--worker-wallet",
        default=str(DEFAULT_WORKER_WALLET),
        help=(
            "Wallet JSON containing private_key for the worker actor in --verify. "
            "The lab creates a throwaway dev wallet at this path if it does not exist."
        ),
    )
    parser.add_argument(
        "--request-hub-id",
        default="dev-hub1",
        help="Concrete Hub id used to issue the MSK in --smoke-msk mode.",
    )
    parser.add_argument(
        "--validate-hub-id",
        default="dev-hub3",
        help="Concrete Hub id used to validate the MSK in --smoke-msk mode.",
    )
    parser.add_argument(
        "--worker-hub-id",
        default="dev-hub3",
        help="Concrete Hub id that owns the worker WebSocket in --smoke-worker-live-session mode.",
    )
    parser.add_argument(
        "--owner-check-hub-id",
        default="dev-hub1",
        help="Concrete Hub id used to read the worker owner directory in --smoke-worker-live-session mode.",
    )
    parser.add_argument(
        "--worker-id",
        default="",
        help="Optional worker_id for --smoke-worker-live-session. Defaults to a generated worker id.",
    )
    parser.add_argument(
        "--origin",
        default=DEFAULT_MSK_ORIGIN,
        help="Origin field included in the signed MSK request message.",
    )
    parser.add_argument(
        "--user-slug",
        default="",
        help="Optional high-entropy user_slug to sign for --smoke-msk. Defaults to a generated slug.",
    )
    parser.add_argument(
        "--request-id",
        default="",
        help="Optional request_id to sign for --smoke-msk. Defaults to a generated id.",
    )
    parser.add_argument(
        "--check-after-start",
        action="store_true",
        help="After --serve-cluster, wait for every Hub to answer before leaving the cluster running.",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for --check-after-start, --check-cluster, --verify, --smoke-msk, or --smoke-worker-live-session calls.",
    )
    parser.add_argument(
        "--worker-entry-index",
        type=int,
        default=2,
        help="Entry URL index used by the worker in deterministic local lab mode.",
    )
    parser.add_argument(
        "--requester-entry-index",
        type=int,
        default=0,
        help="Entry URL index used by the requester in deterministic local lab mode.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print validate/check/smoke results as JSON.",
    )
    return parser


def build_validate_only_result(
    *,
    topology_path: str | Path = DEFAULT_TOPOLOGY,
    worker_entry_index: int = 2,
    requester_entry_index: int = 0,
) -> dict[str, object]:
    topology = load_stable_hub_topology(topology_path)
    plan = build_lab_plan(
        topology,
        worker_entry_index=worker_entry_index,
        requester_entry_index=requester_entry_index,
    )
    return {
        "ok": True,
        "mode": "validate-only",
        "topology_path": str(topology_path),
        "plan": plan,
        "notes": [
            "This lab validates the stable Hub topology contract only.",
            "It does not start exp-fdb-hub.py.",
            "It does not use the scheduler lab.",
            "Stable Hub runtime implementation is intentionally kept low entropy.",
        ],
    }


def _render_text(result: dict[str, object]) -> str:
    plan = result["plan"]
    if not isinstance(plan, dict):
        raise StableHubTopologyError("internal error: result plan must be an object")
    worker = plan["worker_initial_entry"]
    requester = plan["requester_initial_entry"]
    contract = plan["contract"]
    if not isinstance(worker, dict) or not isinstance(requester, dict) or not isinstance(contract, dict):
        raise StableHubTopologyError("internal error: invalid lab plan shape")

    lines = [
        "Stable Hub lab topology validation: ok",
        f"Topology: {result['topology_path']}",
        f"Cluster: {plan['cluster_id']}",
        f"Network: {plan['network_key']} chain_id={plan['chain_id']}",
        f"FDB cluster file: {plan['fdb_cluster_file']}",
        f"FDB namespace: {plan['storage_namespace']}",
        f"Worker initial entry: {worker['hub_id']} {worker['hub_url']}",
        f"Requester initial entry: {requester['hub_id']} {requester['hub_url']}",
        "Contract:",
        f"  auth: {contract['auth']}",
        f"  worker_connection: {contract['worker_connection']}",
        f"  heartbeat: {contract['heartbeat']}",
        f"  availability_source: {contract['availability_source']}",
        f"  routing: {contract['routing']}",
    ]
    return "\n".join(str(line) for line in lines)


def _read_json_url(url: str, *, timeout: float) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - dev/local topology URLs
        raw = response.read()
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise StableHubTopologyError(f"Stable Hub response from {url} was not a JSON object")
    return parsed


def _post_json_url(url: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, sort_keys=True).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "main-computer-stable-hub-lab/1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - dev/local topology URLs
            raw = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise StableHubTopologyError(f"POST {url} returned HTTP {exc.code}: {raw[:1000]}") from exc
    except (URLError, TimeoutError) as exc:
        raise StableHubTopologyError(f"POST {url} failed: {exc}") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StableHubTopologyError(f"POST {url} did not return JSON: {raw[:1000]}") from exc
    if not isinstance(decoded, dict):
        raise StableHubTopologyError(f"POST {url} returned non-object JSON: {decoded!r}")
    decoded["_http_status"] = status
    return decoded


def _read_json_url_status(url: str, *, timeout: float) -> tuple[int, dict[str, Any]]:
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - dev/local topology URLs
            raw = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
    except (URLError, TimeoutError) as exc:
        raise StableHubTopologyError(f"GET {url} failed: {exc}") from exc

    try:
        decoded = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise StableHubTopologyError(f"GET {url} did not return JSON: {raw[:1000]}") from exc
    if not isinstance(decoded, dict):
        raise StableHubTopologyError(f"GET {url} returned non-object JSON: {decoded!r}")
    decoded["_http_status"] = status
    return status, decoded


def _post_json_url_status(url: str, payload: dict[str, Any], *, timeout: float) -> tuple[int, dict[str, Any]]:
    request = Request(
        url,
        data=json.dumps(payload, sort_keys=True).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "main-computer-stable-hub-lab/1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - dev/local topology URLs
            raw = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = int(exc.code)
    except (URLError, TimeoutError) as exc:
        raise StableHubTopologyError(f"POST {url} failed: {exc}") from exc

    try:
        decoded = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise StableHubTopologyError(f"POST {url} did not return JSON: {raw[:1000]}") from exc
    if not isinstance(decoded, dict):
        raise StableHubTopologyError(f"POST {url} returned non-object JSON: {decoded!r}")
    decoded["_http_status"] = status
    return status, decoded


def _resolve_repo_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _normalize_private_key(value: Any, *, source: Path) -> str:
    private_key = str(value or "").strip()
    if not private_key:
        raise StableHubTopologyError(f"wallet file does not contain private_key: {source}")
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    if len(private_key) != 66:
        raise StableHubTopologyError(f"wallet private_key must be a 32-byte hex value: {source}")
    try:
        int(private_key[2:], 16)
    except ValueError as exc:
        raise StableHubTopologyError(f"wallet private_key must be hex: {source}") from exc
    return private_key


def _write_throwaway_dev_wallet(path: Path) -> tuple[str, str]:
    private_key = "0x" + secrets.token_hex(32)
    wallet_address = private_key_to_address(private_key)
    payload = {
        "kind": "main-computer-stable-hub-lab-dev-wallet-v1",
        "warning": "Generated throwaway dev wallet for local stable Hub MSK smoke only. Do not fund.",
        "address": wallet_address,
        "private_key": private_key,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return private_key, wallet_address


def _load_or_create_wallet_private_key(wallet_path: str | Path | None) -> tuple[str, str, Path, bool]:
    raw_path = str(wallet_path or "").strip() or str(DEFAULT_MSK_SMOKE_WALLET)
    path = _resolve_repo_path(raw_path)

    if not path.exists():
        private_key, wallet_address = _write_throwaway_dev_wallet(path)
        return private_key, wallet_address, path, True

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StableHubTopologyError(f"wallet file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StableHubTopologyError(f"wallet file root must be a JSON object: {path}")

    private_key = _normalize_private_key(payload.get("private_key"), source=path)
    wallet_address = private_key_to_address(private_key)
    declared_address = str(payload.get("address") or payload.get("wallet_address") or "").strip()
    if declared_address and declared_address.lower() != wallet_address:
        raise StableHubTopologyError(
            f"wallet address mismatch: file declares {declared_address}, private_key derives {wallet_address}"
        )
    return private_key, wallet_address, path, False

def _new_user_slug() -> str:
    # 256 bits from the user/client side. The Hub independently contributes
    # hub_slug and the stable MSK id combines both.
    return "user_" + secrets.token_urlsafe(32).rstrip("=")


def _new_request_id() -> str:
    return "stable-hub-msk-smoke-" + secrets.token_urlsafe(16).rstrip("=")


def build_stable_msk_smoke_result(
    *,
    topology_path: str | Path = DEFAULT_TOPOLOGY,
    wallet_path: str | Path = DEFAULT_MSK_SMOKE_WALLET,
    request_hub_id: str = "dev-hub1",
    validate_hub_id: str = "dev-hub3",
    origin: str = DEFAULT_MSK_ORIGIN,
    user_slug: str = "",
    request_id: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    topology = load_stable_hub_topology(topology_path)
    request_hub = topology.hub_by_id(request_hub_id)
    validate_hub = topology.hub_by_id(validate_hub_id)
    private_key, wallet_address, resolved_wallet_path, wallet_created = _load_or_create_wallet_private_key(wallet_path)

    network = dict(topology.network)
    chain_id = str(network.get("chain_id") or "").strip()
    if not chain_id:
        raise StableHubTopologyError("topology.network.chain_id is required for --smoke-msk.")

    signed_user_slug = str(user_slug or "").strip() or _new_user_slug()
    signed_request_id = str(request_id or "").strip() or _new_request_id()
    now = datetime.now(timezone.utc)
    message = {
        "purpose": "request_multi_session_key",
        "wallet_address": wallet_address,
        "chain_id": chain_id,
        "request_id": signed_request_id,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "origin": origin,
        "user_slug": signed_user_slug,
    }
    signed_request = build_personal_sign_blob(
        message=message,
        private_key=private_key,
        wallet_address=wallet_address,
        chain_id=chain_id,
    )

    issue_url = f"{request_hub.hub_url.rstrip('/')}/api/hub/v1/credits/multisession-keys/request"
    validate_url = f"{validate_hub.hub_url.rstrip('/')}/api/hub/v1/credits/multisession-keys/validate"

    issued = _post_json_url(issue_url, {"signed_request": signed_request}, timeout=timeout)
    if issued.get("ok") is not True:
        raise StableHubTopologyError(f"MSK issue failed on {request_hub.hub_id}: {issued}")

    key = issued.get("key") if isinstance(issued.get("key"), dict) else {}
    multisession_key_id = str(
        key.get("id")
        or (issued.get("multisession_authorization") or {}).get("multisession_key_id")
        or ""
    ).strip()
    if not multisession_key_id:
        raise StableHubTopologyError(f"MSK issue response did not include a multisession key id: {issued}")

    # The stable contract after issuance is intentionally key-id-first. The
    # validate call does not include wallet_address; the Hub must derive wallet,
    # account, chain, and signed-request metadata from the stored full MSK record.
    validation_body = {
        "multisession_authorization": {
            "kind": "multisession_key",
            "multisession_key_id": multisession_key_id,
        }
    }
    validated = _post_json_url(validate_url, validation_body, timeout=timeout)
    if validated.get("valid") is not True:
        raise StableHubTopologyError(f"MSK validation failed on {validate_hub.hub_id}: {validated}")

    validated_key = validated.get("key") if isinstance(validated.get("key"), dict) else {}
    validated_wallet = str(validated.get("wallet_address") or "").lower()
    proof = {
        "signed_user_slug": key.get("user_slug") == signed_user_slug == validated_key.get("user_slug"),
        "hub_slug_added": bool(key.get("hub_slug")) and key.get("hub_slug") == validated_key.get("hub_slug"),
        "msk_combines_user_and_hub_slugs": multisession_key_id.startswith(f"msk_{signed_user_slug}_")
        and bool(key.get("hub_slug"))
        and multisession_key_id.endswith(str(key.get("hub_slug"))),
        "stored_full_signed_request": key.get("has_signed_request") is True and validated_key.get("has_signed_request") is True,
        "validation_used_key_id_only": sorted(validation_body["multisession_authorization"].keys())
        == ["kind", "multisession_key_id"],
        "wallet_derived_from_stored_request": validated_wallet == wallet_address,
        "cross_hub_validation": request_hub.hub_id != validate_hub.hub_id,
    }

    ok = all(bool(value) for value in proof.values())
    return {
        "ok": ok,
        "mode": "smoke-msk",
        "topology_path": str(topology_path),
        "cluster_id": topology.cluster_id,
        "chain_id": chain_id,
        "wallet_path": str(resolved_wallet_path),
        "wallet_created": wallet_created,
        "request_hub": {"hub_id": request_hub.hub_id, "hub_url": request_hub.hub_url},
        "validate_hub": {"hub_id": validate_hub.hub_id, "hub_url": validate_hub.hub_url},
        "wallet_address": wallet_address,
        "request_id": signed_request_id,
        "user_slug": signed_user_slug,
        "multisession_key_id": multisession_key_id,
        "issued_key": key,
        "validated_key": validated_key,
        "validated_wallet_address": validated_wallet,
        "proof": proof,
    }


def _render_msk_smoke_text(result: dict[str, Any]) -> str:
    proof = result["proof"]
    lines = [
        f"Stable Hub MSK smoke: {'ok' if result['ok'] else 'failed'}",
        f"Topology: {result['topology_path']}",
        f"Cluster: {result['cluster_id']}",
        f"Chain: {result['chain_id']}",
        f"Request Hub: {result['request_hub']['hub_id']} {result['request_hub']['hub_url']}",
        f"Validate Hub: {result['validate_hub']['hub_id']} {result['validate_hub']['hub_url']}",
        f"Wallet: {result['wallet_address']}",
        f"Wallet file: {result['wallet_path']} ({'created' if result.get('wallet_created') else 'reused'})",
        f"MSK id: {result['multisession_key_id']}",
        "Proof:",
        f"  signed user_slug: {'yes' if proof['signed_user_slug'] else 'no'}",
        f"  Hub added hub_slug: {'yes' if proof['hub_slug_added'] else 'no'}",
        f"  MSK combines user_slug + hub_slug: {'yes' if proof['msk_combines_user_and_hub_slugs'] else 'no'}",
        f"  full signed request stored: {'yes' if proof['stored_full_signed_request'] else 'no'}",
        f"  validate used key id only: {'yes' if proof['validation_used_key_id_only'] else 'no'}",
        f"  wallet derived from stored request: {'yes' if proof['wallet_derived_from_stored_request'] else 'no'}",
        f"  cross-Hub validation: {'yes' if proof['cross_hub_validation'] else 'no'}",
    ]
    return "\n".join(lines)



def _ws_send_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    header = bytearray([0x81])
    if len(data) < 126:
        header.append(0x80 | len(data))
    elif len(data) < 65536:
        header.append(0x80 | 126)
        header.extend(len(data).to_bytes(2, "big"))
    else:
        header.append(0x80 | 127)
        header.extend(len(data).to_bytes(8, "big"))
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
    sock.sendall(bytes(header) + mask + masked)


def _ws_read_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise StableHubTopologyError("worker live-session websocket closed unexpectedly.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _ws_recv_json(sock: socket.socket) -> dict[str, Any]:
    while True:
        header = _ws_read_exact(sock, 2)
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if length == 126:
            length = int.from_bytes(_ws_read_exact(sock, 2), "big")
        elif length == 127:
            length = int.from_bytes(_ws_read_exact(sock, 8), "big")
        payload = _ws_read_exact(sock, length)
        if opcode == 0x8:
            raise StableHubTopologyError("worker live-session websocket closed before response.")
        if opcode != 0x1:
            continue
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise StableHubTopologyError(f"worker live-session websocket returned non-JSON text: {exc}") from exc
        if not isinstance(decoded, dict):
            raise StableHubTopologyError("worker live-session websocket returned non-object JSON.")
        return decoded


def _ws_connect(url: str, path: str, *, timeout: float) -> socket.socket:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise StableHubTopologyError(f"Hub URL must be http(s): {url!r}")
    if parsed.scheme == "https":
        raise StableHubTopologyError("stable Hub worker live-session smoke only supports local http URLs.")
    port = parsed.port or 80
    sock = socket.create_connection((parsed.hostname, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise StableHubTopologyError(f"worker live-session websocket handshake closed early: {response!r}")
        response += chunk
    first_line = response.split(b"\r\n", 1)[0]
    if b" 101 " not in first_line:
        raise StableHubTopologyError(response.decode("latin1", errors="replace")[:1000])
    return sock


def build_worker_live_session_smoke_result(
    *,
    topology_path: str | Path = DEFAULT_TOPOLOGY,
    wallet_path: str | Path = DEFAULT_MSK_SMOKE_WALLET,
    request_hub_id: str = "dev-hub1",
    validate_hub_id: str = "dev-hub3",
    worker_hub_id: str = "dev-hub3",
    owner_check_hub_id: str = "dev-hub1",
    worker_id: str = "",
    origin: str = DEFAULT_MSK_ORIGIN,
    user_slug: str = "",
    request_id: str = "",
    timeout: float = 10.0,
) -> dict[str, Any]:
    topology = load_stable_hub_topology(topology_path)
    worker_hub = topology.hub_by_id(worker_hub_id)
    owner_check_hub = topology.hub_by_id(owner_check_hub_id)
    resolved_worker_id = str(worker_id or "").strip() or ("worker_" + secrets.token_urlsafe(12).rstrip("="))

    msk = build_stable_msk_smoke_result(
        topology_path=topology_path,
        wallet_path=wallet_path,
        request_hub_id=request_hub_id,
        validate_hub_id=validate_hub_id,
        origin=origin,
        user_slug=user_slug,
        request_id=request_id,
        timeout=timeout,
    )
    multisession_key_id = str(msk["multisession_key_id"])

    sock: socket.socket | None = None
    try:
        sock = _ws_connect(worker_hub.hub_url, "/api/hub/v1/workers/live-session", timeout=timeout)
        _ws_send_json(
            sock,
            {
                "type": "worker.auth",
                "worker_id": resolved_worker_id,
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": multisession_key_id,
                },
            },
        )
        accepted = _ws_recv_json(sock)
        ping = _ws_recv_json(sock)
        if accepted.get("type") != "hub.auth.accepted":
            raise StableHubTopologyError(f"worker live-session auth failed: {accepted}")
        if ping.get("type") != "hub.ping":
            raise StableHubTopologyError(f"worker live-session did not receive hub.ping: {ping}")
        _ws_send_json(
            sock,
            {
                "type": "worker.pong",
                "connection_id": accepted.get("connection_id"),
                "ping_id": ping.get("ping_id"),
            },
        )
        pong = _ws_recv_json(sock)
        if pong.get("type") != "hub.pong.accepted" or pong.get("ok") is not True:
            raise StableHubTopologyError(f"worker live-session pong failed: {pong}")

        owner_payload = _read_json_url(
            f"{owner_check_hub.hub_url.rstrip('/')}/api/hub/v1/workers/{resolved_worker_id}/owner",
            timeout=timeout,
        )
        owner = owner_payload.get("owner") if isinstance(owner_payload.get("owner"), dict) else {}
        proof = {
            "websocket_auth_accepted": accepted.get("type") == "hub.auth.accepted",
            "hub_ping_over_open_connection": ping.get("type") == "hub.ping",
            "worker_pong_over_same_connection": pong.get("type") == "hub.pong.accepted" and pong.get("ok") is True,
            "owner_record_visible_from_other_hub": owner_payload.get("ok") is True and bool(owner),
            "owner_hub_matches_worker_hub": owner.get("owner_hub_id") == worker_hub.hub_id,
            "owner_record_live": owner.get("status") == "live",
            "owner_uses_msk_id": owner.get("multisession_key_id") == multisession_key_id,
        }
        _ws_send_json(sock, {"type": "worker.close"})
    finally:
        if sock is not None:
            sock.close()

    ok = all(bool(value) for value in proof.values())
    return {
        "ok": ok,
        "mode": "smoke-worker-live-session",
        "topology_path": str(topology_path),
        "cluster_id": topology.cluster_id,
        "worker_id": resolved_worker_id,
        "worker_hub": {"hub_id": worker_hub.hub_id, "hub_url": worker_hub.hub_url},
        "owner_check_hub": {"hub_id": owner_check_hub.hub_id, "hub_url": owner_check_hub.hub_url},
        "multisession_key_id": multisession_key_id,
        "connection_id": accepted.get("connection_id"),
        "owner": owner,
        "msk": {
            "wallet_address": msk.get("wallet_address"),
            "wallet_path": msk.get("wallet_path"),
            "wallet_created": msk.get("wallet_created"),
        },
        "proof": proof,
    }


def _render_worker_live_session_smoke_text(result: dict[str, Any]) -> str:
    proof = result["proof"]
    lines = [
        f"Stable Hub worker live-session smoke: {'ok' if result['ok'] else 'failed'}",
        f"Topology: {result['topology_path']}",
        f"Cluster: {result['cluster_id']}",
        f"Worker Hub: {result['worker_hub']['hub_id']} {result['worker_hub']['hub_url']}",
        f"Owner check Hub: {result['owner_check_hub']['hub_id']} {result['owner_check_hub']['hub_url']}",
        f"Worker id: {result['worker_id']}",
        f"Connection id: {result['connection_id']}",
        f"MSK id: {result['multisession_key_id']}",
        "Proof:",
        f"  websocket auth accepted: {'yes' if proof['websocket_auth_accepted'] else 'no'}",
        f"  Hub ping over open connection: {'yes' if proof['hub_ping_over_open_connection'] else 'no'}",
        f"  worker pong over same connection: {'yes' if proof['worker_pong_over_same_connection'] else 'no'}",
        f"  owner record visible from other Hub: {'yes' if proof['owner_record_visible_from_other_hub'] else 'no'}",
        f"  owner Hub matches worker Hub: {'yes' if proof['owner_hub_matches_worker_hub'] else 'no'}",
        f"  owner record live: {'yes' if proof['owner_record_live'] else 'no'}",
        f"  owner uses MSK id: {'yes' if proof['owner_uses_msk_id'] else 'no'}",
    ]
    return "\n".join(lines)

def build_stable_hub_verification_result(
    *,
    topology_path: str | Path = DEFAULT_TOPOLOGY,
    requester_wallet_path: str | Path = DEFAULT_REQUESTER_WALLET,
    worker_wallet_path: str | Path = DEFAULT_WORKER_WALLET,
    worker_entry_index: int = 2,
    requester_entry_index: int = 0,
    origin: str = DEFAULT_MSK_ORIGIN,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Run realistic requester/worker traffic against the stable Hub topology.

    This is the stable lab verification path: it uses normal Stable Hub APIs and
    actor messages instead of a bespoke dispatch test surface. The topology plan
    decides the requester entry Hub and worker owner Hub.
    """

    topology = load_stable_hub_topology(topology_path)
    plan = build_lab_plan(
        topology,
        worker_entry_index=worker_entry_index,
        requester_entry_index=requester_entry_index,
    )
    worker_entry = plan["worker_initial_entry"]
    requester_entry = plan["requester_initial_entry"]
    if not isinstance(worker_entry, dict) or not isinstance(requester_entry, dict):
        raise StableHubTopologyError("internal error: invalid stable lab actor plan")
    worker_hub = topology.hub_by_id(str(worker_entry["hub_id"]))
    requester_hub = topology.hub_by_id(str(requester_entry["hub_id"]))

    worker_id = "worker_" + secrets.token_urlsafe(12).rstrip("=")
    request_id = "req_" + secrets.token_urlsafe(12).rstrip("=")
    worker_user_slug = _new_user_slug()
    requester_user_slug = _new_user_slug()

    worker_msk = build_stable_msk_smoke_result(
        topology_path=topology_path,
        wallet_path=worker_wallet_path,
        request_hub_id=requester_hub.hub_id,
        validate_hub_id=worker_hub.hub_id,
        origin=f"{origin}-worker",
        user_slug=worker_user_slug,
        request_id=f"{request_id}-worker-msk",
        timeout=timeout,
    )
    requester_msk = build_stable_msk_smoke_result(
        topology_path=topology_path,
        wallet_path=requester_wallet_path,
        request_hub_id=requester_hub.hub_id,
        validate_hub_id=worker_hub.hub_id,
        origin=f"{origin}-requester",
        user_slug=requester_user_slug,
        request_id=f"{request_id}-requester-msk",
        timeout=timeout,
    )

    worker_key_id = str(worker_msk["multisession_key_id"])
    requester_key_id = str(requester_msk["multisession_key_id"])
    market_profile = {
        "rings": ["ring-2"],
        "price": {"amount": "0.03", "unit": "credit"},
        "capabilities": ["python", "echo"],
        "max_concurrency": 2,
    }
    work_request = {
        "request_id": request_id,
        "multisession_authorization": {
            "kind": "multisession_key",
            "multisession_key_id": requester_key_id,
        },
        "work": {
            "ring": "ring-2",
            "max_price": {"amount": "0.10", "unit": "credit"},
            "capabilities": ["python"],
            "input": {"kind": "echo", "value": "stable-hub-lab"},
        },
    }

    sock: socket.socket | None = None
    post_thread: threading.Thread | None = None
    result_queue: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
    accepted: dict[str, Any] = {}
    ping: dict[str, Any] = {}
    pong: dict[str, Any] = {}
    owner: dict[str, Any] = {}
    offer: dict[str, Any] = {}
    response: dict[str, Any] = {}
    continuation: dict[str, Any] = {}
    entry_stream_status = 0
    entry_stream_body: dict[str, Any] = {}
    rest_ping_status = 0
    rest_ping_body: dict[str, Any] = {}

    try:
        sock = _ws_connect(worker_hub.hub_url, "/api/hub/v1/workers/live-session", timeout=timeout)
        _ws_send_json(
            sock,
            {
                "type": "worker.auth",
                "worker_id": worker_id,
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": worker_key_id,
                },
                "market": market_profile,
            },
        )
        accepted = _ws_recv_json(sock)
        ping = _ws_recv_json(sock)
        if accepted.get("type") != "hub.auth.accepted":
            raise StableHubTopologyError(f"worker auth failed during stable verification: {accepted}")
        if ping.get("type") != "hub.ping":
            raise StableHubTopologyError(f"worker did not receive hub.ping during stable verification: {ping}")
        _ws_send_json(
            sock,
            {
                "type": "worker.pong",
                "connection_id": accepted.get("connection_id"),
                "ping_id": ping.get("ping_id"),
            },
        )
        pong = _ws_recv_json(sock)
        if pong.get("type") != "hub.pong.accepted" or pong.get("ok") is not True:
            raise StableHubTopologyError(f"worker pong failed during stable verification: {pong}")

        owner_payload = _read_json_url(
            f"{requester_hub.hub_url.rstrip('/')}/api/hub/v1/workers/{worker_id}/owner",
            timeout=timeout,
        )
        owner = owner_payload.get("owner") if isinstance(owner_payload.get("owner"), dict) else {}
        if not owner:
            raise StableHubTopologyError(f"entry Hub could not read worker owner record: {owner_payload}")

        def _submit_work() -> None:
            try:
                result_queue.put(
                    _post_json_url(
                        f"{requester_hub.hub_url.rstrip('/')}/api/hub/v1/work/requests",
                        work_request,
                        timeout=timeout,
                    )
                )
            except BaseException as exc:  # pragma: no cover - surfaced below
                result_queue.put(exc)

        post_thread = threading.Thread(target=_submit_work, daemon=True)
        post_thread.start()

        offer = _ws_recv_json(sock)
        if offer.get("type") != "hub.work.offer":
            raise StableHubTopologyError(f"worker did not receive hub.work.offer: {offer}")
        _ws_send_json(
            sock,
            {
                "type": "worker.work.accepted",
                "session_id": offer.get("session_id"),
                "request_id": offer.get("request_id"),
            },
        )

        queued = result_queue.get(timeout=max(timeout, 1.0))
        if isinstance(queued, BaseException):
            raise StableHubTopologyError(f"requester work request failed: {queued}") from queued
        response = queued
        if post_thread is not None:
            post_thread.join(timeout=1.0)

        if response.get("ok") is not True or response.get("accepted") is not True:
            raise StableHubTopologyError(f"requester work was not accepted: {response}")

        continuation_url = str(response.get("continuation_url") or "")
        if not continuation_url:
            raise StableHubTopologyError(f"accepted response did not include continuation_url: {response}")
        continuation = _read_json_url(continuation_url, timeout=timeout)

        entry_stream_status, entry_stream_body = _read_json_url_status(
            f"{requester_hub.hub_url.rstrip('/')}/api/hub/v1/work/sessions/{response.get('session_id')}/stream",
            timeout=timeout,
        )
        rest_ping_status, rest_ping_body = _post_json_url_status(
            f"{worker_hub.hub_url.rstrip('/')}/api/hub/v1/workers/{worker_id}/ping",
            {"worker_id": worker_id, "connection_id": accepted.get("connection_id")},
            timeout=timeout,
        )
    finally:
        if sock is not None:
            try:
                _ws_send_json(sock, {"type": "worker.close"})
            except OSError:
                pass
            sock.close()
        if post_thread is not None:
            post_thread.join(timeout=1.0)

    owner_hub_url = str(owner.get("owner_hub_url") or worker_hub.hub_url)
    expected_continuation_url = f"{owner_hub_url.rstrip('/')}/api/hub/v1/work/sessions/{response.get('session_id')}/stream"
    worker_msk_proof = worker_msk.get("proof") if isinstance(worker_msk.get("proof"), dict) else {}
    requester_msk_proof = requester_msk.get("proof") if isinstance(requester_msk.get("proof"), dict) else {}
    execution = response.get("execution") if isinstance(response.get("execution"), dict) else {}
    accepted_session = continuation.get("accepted_session") if isinstance(continuation.get("accepted_session"), dict) else {}
    handoff = response.get("handoff") if isinstance(response.get("handoff"), dict) else {}
    proof = {
        "requester_actor_msk_issued": bool(requester_msk.get("ok")),
        "worker_actor_msk_issued": bool(worker_msk.get("ok")),
        "msk_validation_key_id_only": bool(worker_msk_proof.get("validation_used_key_id_only"))
        and bool(requester_msk_proof.get("validation_used_key_id_only")),
        "worker_connected_over_live_websocket": accepted.get("type") == "hub.auth.accepted"
        and accepted.get("connection_id") == ping.get("connection_id"),
        "worker_pong_over_same_socket": pong.get("type") == "hub.pong.accepted" and pong.get("ok") is True,
        "owner_directory_visible_across_hubs": owner.get("worker_id") == worker_id
        and owner.get("owner_hub_id") == worker_hub.hub_id
        and owner.get("status") == "live",
        "worker_market_record_selectable_by_ring_price": offer.get("type") == "hub.work.offer"
        and offer.get("worker_id") == worker_id
        and offer.get("partition") == "ring-2"
        and offer.get("task_queue") == "main-computer-work-ring-2",
        "entry_hub_handed_off_to_owner_hub": handoff.get("routed") is True
        and handoff.get("from_hub_id") == requester_hub.hub_id
        and handoff.get("to_hub_id") == worker_hub.hub_id,
        "owner_hub_offered_work_over_live_socket": offer.get("request_id") == request_id
        and offer.get("work") == work_request["work"],
        "worker_accepted_over_same_socket": response.get("ok") is True
        and response.get("accepted") is True
        and response.get("session_id") == offer.get("session_id"),
        "accepted_session_stored_with_temporal_metadata": continuation.get("ok") is True
        and accepted_session.get("session_id") == response.get("session_id")
        and execution.get("backend") == "temporal"
        and execution.get("workflow_id") == response.get("session_id")
        and accepted_session.get("execution") == execution,
        "requester_continuation_points_to_worker_hub": response.get("continuation_url") == expected_continuation_url
        and (response.get("execution_hub") or {}).get("hub_id") == worker_hub.hub_id
        and continuation.get("hub_id") == worker_hub.hub_id,
        "entry_hub_not_streaming_worker_session": entry_stream_status == 409
        and entry_stream_body.get("error") == "session_continuation_not_on_this_hub",
        "rest_worker_heartbeat_forbidden": rest_ping_status == 404
        and rest_ping_body.get("error") == "not_found",
    }
    ok = all(bool(value) for value in proof.values())

    return {
        "ok": ok,
        "mode": "verify",
        "topology_path": str(topology_path),
        "cluster_id": topology.cluster_id,
        "plan": plan,
        "actors": {
            "requester": {
                "hub_id": requester_hub.hub_id,
                "hub_url": requester_hub.hub_url,
                "multisession_key_id": requester_key_id,
                "wallet_address": requester_msk.get("wallet_address"),
            },
            "worker": {
                "worker_id": worker_id,
                "hub_id": worker_hub.hub_id,
                "hub_url": worker_hub.hub_url,
                "multisession_key_id": worker_key_id,
                "connection_id": accepted.get("connection_id"),
                "market": market_profile,
            },
        },
        "traffic": {
            "request_id": request_id,
            "work_request": work_request,
            "worker_auth": accepted,
            "worker_ping": ping,
            "worker_pong": pong,
            "owner": owner,
            "offer": offer,
            "accepted_response": response,
            "continuation": continuation,
            "entry_stream_status": entry_stream_status,
            "entry_stream_body": entry_stream_body,
            "rest_worker_heartbeat_status": rest_ping_status,
            "rest_worker_heartbeat_body": rest_ping_body,
        },
        "proof": proof,
    }


def _render_stable_hub_verification_text(result: dict[str, Any]) -> str:
    proof = result["proof"]
    actors = result["actors"]
    traffic = result["traffic"]
    requester = actors["requester"]
    worker = actors["worker"]
    response = traffic["accepted_response"]
    lines = [
        f"Stable Hub lab verification: {'ok' if result['ok'] else 'failed'}",
        f"Topology: {result['topology_path']}",
        f"Cluster: {result['cluster_id']}",
        "Actors:",
        f"  requester entry Hub: {requester['hub_id']} {requester['hub_url']}",
        f"  worker owner Hub: {worker['hub_id']} {worker['hub_url']}",
        f"  worker id: {worker['worker_id']}",
        "Traffic:",
        f"  requester work request: {traffic['request_id']}",
        f"  accepted session: {response.get('session_id')}",
        f"  run id: {response.get('run_id')}",
        f"  continuation_url: {response.get('continuation_url')}",
        "Proof:",
        f"  requester actor MSK issued: {'yes' if proof['requester_actor_msk_issued'] else 'no'}",
        f"  worker actor MSK issued: {'yes' if proof['worker_actor_msk_issued'] else 'no'}",
        f"  MSK validation used key id only: {'yes' if proof['msk_validation_key_id_only'] else 'no'}",
        f"  worker connected over live WebSocket: {'yes' if proof['worker_connected_over_live_websocket'] else 'no'}",
        f"  worker pong over same socket: {'yes' if proof['worker_pong_over_same_socket'] else 'no'}",
        f"  owner directory visible across Hubs: {'yes' if proof['owner_directory_visible_across_hubs'] else 'no'}",
        f"  worker selectable by ring/price/capability: {'yes' if proof['worker_market_record_selectable_by_ring_price'] else 'no'}",
        f"  entry Hub handed off to owner Hub: {'yes' if proof['entry_hub_handed_off_to_owner_hub'] else 'no'}",
        f"  owner Hub offered work over live socket: {'yes' if proof['owner_hub_offered_work_over_live_socket'] else 'no'}",
        f"  worker accepted over same socket: {'yes' if proof['worker_accepted_over_same_socket'] else 'no'}",
        f"  accepted session stored with Temporal metadata: {'yes' if proof['accepted_session_stored_with_temporal_metadata'] else 'no'}",
        f"  requester continuation points to worker Hub: {'yes' if proof['requester_continuation_points_to_worker_hub'] else 'no'}",
        f"  entry Hub is not streaming worker session: {'yes' if proof['entry_hub_not_streaming_worker_session'] else 'no'}",
        f"  REST worker heartbeat forbidden: {'yes' if proof['rest_worker_heartbeat_forbidden'] else 'no'}",
    ]
    return "\n".join(lines)


def build_stable_hub_integrated_market_stress_result(
    *,
    topology_path: str | Path = DEFAULT_TOPOLOGY,
    requester_wallet_path: str | Path = DEFAULT_REQUESTER_WALLET,
    worker_wallet_path: str | Path = DEFAULT_WORKER_WALLET,
    worker_entry_index: int = 2,
    requester_entry_index: int = 0,
    origin: str = DEFAULT_MSK_ORIGIN,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Run one integrated Stable Hub market/reconnect/payout stress scenario.

    This intentionally mixes realistic market routing, remote handoff, worker
    failure, disconnect/reconnect, completion, payout hold/charge/release,
    claim/settlement/bridge idempotency, and invariant checks in one scenario.
    """

    topology = load_stable_hub_topology(topology_path)
    plan = build_lab_plan(
        topology,
        worker_entry_index=worker_entry_index,
        requester_entry_index=requester_entry_index,
    )
    worker_entry = plan["worker_initial_entry"]
    requester_entry = plan["requester_initial_entry"]
    if not isinstance(worker_entry, dict) or not isinstance(requester_entry, dict):
        raise StableHubTopologyError("stable Hub stress requires worker and requester entries.")
    worker_hub = topology.hub_by_id(str(worker_entry["hub_id"]))
    requester_hub = topology.hub_by_id(str(requester_entry["hub_id"]))

    worker_id = "worker_" + secrets.token_urlsafe(12).rstrip("=")
    scenario_id = "stress_" + secrets.token_urlsafe(8).rstrip("=")
    worker_user_slug = _new_user_slug()
    requester_user_slug = _new_user_slug()

    worker_msk = build_stable_msk_smoke_result(
        topology_path=topology_path,
        wallet_path=worker_wallet_path,
        request_hub_id=requester_hub.hub_id,
        validate_hub_id=worker_hub.hub_id,
        origin=f"{origin}-worker-stress",
        user_slug=worker_user_slug,
        request_id=f"{scenario_id}-worker-msk",
        timeout=timeout,
    )
    requester_msk = build_stable_msk_smoke_result(
        topology_path=topology_path,
        wallet_path=requester_wallet_path,
        request_hub_id=requester_hub.hub_id,
        validate_hub_id=worker_hub.hub_id,
        origin=f"{origin}-requester-stress",
        user_slug=requester_user_slug,
        request_id=f"{scenario_id}-requester-msk",
        timeout=timeout,
    )
    worker_key_id = str(worker_msk["multisession_key_id"])
    requester_key_id = str(requester_msk["multisession_key_id"])
    market_profile = {
        "rings": ["ring-2"],
        "price": {"amount": "0.03", "unit": "credit"},
        "capabilities": ["python", "echo"],
        "max_concurrency": 2,
    }

    sockets: list[socket.socket] = []
    events: list[dict[str, Any]] = []

    def _auth_worker(hub_url: str) -> tuple[socket.socket, dict[str, Any], dict[str, Any], dict[str, Any]]:
        sock = _ws_connect(hub_url, "/api/hub/v1/workers/live-session", timeout=timeout)
        sockets.append(sock)
        _ws_send_json(
            sock,
            {
                "type": "worker.auth",
                "worker_id": worker_id,
                "multisession_authorization": {
                    "kind": "multisession_key",
                    "multisession_key_id": worker_key_id,
                },
                "market": market_profile,
            },
        )
        auth = _ws_recv_json(sock)
        ping = _ws_recv_json(sock)
        if auth.get("type") != "hub.auth.accepted" or ping.get("type") != "hub.ping":
            raise StableHubTopologyError(f"worker auth failed during integrated stress: auth={auth} ping={ping}")
        _ws_send_json(
            sock,
            {
                "type": "worker.pong",
                "connection_id": auth.get("connection_id"),
                "ping_id": ping.get("ping_id"),
            },
        )
        pong = _ws_recv_json(sock)
        if pong.get("type") != "hub.pong.accepted" or pong.get("ok") is not True:
            raise StableHubTopologyError(f"worker pong failed during integrated stress: {pong}")
        events.append({"type": "worker.connected", "hub_url": hub_url, "connection_id": auth.get("connection_id")})
        return sock, auth, ping, pong

    def _work_request(request_id: str, *, max_price: str = "0.10", value: str = "ok") -> dict[str, Any]:
        return {
            "request_id": request_id,
            "multisession_authorization": {
                "kind": "multisession_key",
                "multisession_key_id": requester_key_id,
            },
            "work": {
                "ring": "ring-2",
                "max_price": {"amount": max_price, "unit": "credit"},
                "capabilities": ["python"],
                "input": {"kind": "echo", "value": value},
            },
        }

    def _submit_and_terminal(
        *,
        entry_url: str,
        sock: socket.socket,
        request_id: str,
        terminal: str,
    ) -> dict[str, Any]:
        q: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        payload = _work_request(request_id, value=terminal)

        def _submit() -> None:
            try:
                q.put(_post_json_url(f"{entry_url.rstrip('/')}/api/hub/v1/work/requests", payload, timeout=timeout))
            except BaseException as exc:  # pragma: no cover - surfaced below
                q.put(exc)

        thread = threading.Thread(target=_submit, daemon=True)
        thread.start()
        offer = _ws_recv_json(sock)
        if offer.get("type") != "hub.work.offer":
            raise StableHubTopologyError(f"worker did not receive hub.work.offer during stress: {offer}")
        _ws_send_json(
            sock,
            {
                "type": "worker.work.accepted",
                "session_id": offer.get("session_id"),
                "request_id": offer.get("request_id"),
            },
        )
        queued = q.get(timeout=max(timeout, 1.0))
        if isinstance(queued, BaseException):
            raise StableHubTopologyError(f"stress requester work failed: {queued}") from queued
        response = queued
        thread.join(timeout=1.0)
        if response.get("ok") is not True or response.get("accepted") is not True:
            raise StableHubTopologyError(f"stress requester work was not accepted: {response}")
        if terminal == "result":
            _ws_send_json(
                sock,
                {
                    "type": "worker.work.result",
                    "session_id": offer.get("session_id"),
                    "request_id": offer.get("request_id"),
                    "result": {"echo": payload["work"]["input"]["value"]},
                },
            )
        else:
            _ws_send_json(
                sock,
                {
                    "type": "worker.work.failed",
                    "session_id": offer.get("session_id"),
                    "request_id": offer.get("request_id"),
                    "error": {"code": "injected_worker_failure", "message": "stress failure injection"},
                },
            )
        terminal_ack = _ws_recv_json(sock)
        continuation = _read_json_url(str(response.get("continuation_url")), timeout=timeout)
        events.append(
            {
                "type": f"work.{terminal}",
                "request_id": request_id,
                "session_id": response.get("session_id"),
                "ack": terminal_ack,
                "status": continuation.get("status"),
            }
        )
        return {
            "request": payload,
            "offer": offer,
            "response": response,
            "terminal_ack": terminal_ack,
            "continuation": continuation,
        }

    sock1: socket.socket | None = None
    sock2: socket.socket | None = None
    try:
        sock1, auth1, ping1, pong1 = _auth_worker(worker_hub.hub_url)
        success = _submit_and_terminal(
            entry_url=requester_hub.hub_url,
            sock=sock1,
            request_id="req_" + secrets.token_urlsafe(12).rstrip("="),
            terminal="result",
        )
        _ws_send_json(
            sock1,
            {
                "type": "worker.work.result",
                "session_id": success["response"].get("session_id"),
                "request_id": success["response"].get("request_id"),
                "result": {"echo": "duplicate"},
            },
        )
        duplicate_result_ack = _ws_recv_json(sock1)

        failure = _submit_and_terminal(
            entry_url=requester_hub.hub_url,
            sock=sock1,
            request_id="req_" + secrets.token_urlsafe(12).rstrip("="),
            terminal="failed",
        )

        _ws_send_json(sock1, {"type": "worker.close"})
        sock1.close()
        sockets.remove(sock1)
        events.append({"type": "worker.disconnected", "connection_id": auth1.get("connection_id")})

        sock2, auth2, ping2, pong2 = _auth_worker(requester_hub.hub_url)
        reconnect_success = _submit_and_terminal(
            entry_url=worker_hub.hub_url,
            sock=sock2,
            request_id="req_" + secrets.token_urlsafe(12).rstrip("="),
            terminal="result",
        )

        low_price_status, low_price_body = _post_json_url_status(
            f"{worker_hub.hub_url.rstrip('/')}/api/hub/v1/work/requests",
            _work_request("req_" + secrets.token_urlsafe(12).rstrip("="), max_price="0.01", value="too-low"),
            timeout=timeout,
        )

        worker_auth = {
            "multisession_authorization": {
                "kind": "multisession_key",
                "multisession_key_id": worker_key_id,
            }
        }
        claim = _post_json_url(
            f"{requester_hub.hub_url.rstrip('/')}/api/hub/v1/workers/{worker_id}/payout/claim",
            {
                **worker_auth,
                "idempotency_key": f"{scenario_id}-claim",
            },
            timeout=timeout,
        )
        duplicate_claim = _post_json_url(
            f"{requester_hub.hub_url.rstrip('/')}/api/hub/v1/workers/{worker_id}/payout/claim",
            {
                **worker_auth,
                "idempotency_key": f"{scenario_id}-claim",
            },
            timeout=timeout,
        )
        settlement = _post_json_url(
            f"{requester_hub.hub_url.rstrip('/')}/api/hub/v1/workers/{worker_id}/payout/settlements",
            {
                **worker_auth,
                "claim_ids": [claim.get("claim", {}).get("claim_id")],
                "idempotency_key": f"{scenario_id}-settle",
                "settle": True,
                "settlement_reference": f"{scenario_id}-settlement-reference",
            },
            timeout=timeout,
        )
        bridge = _post_json_url(
            f"{requester_hub.hub_url.rstrip('/')}/api/hub/v1/workers/{worker_id}/payout/bridge",
            {
                **worker_auth,
                "batch_id": settlement.get("settlement", {}).get("batch_id"),
                "idempotency_key": f"{scenario_id}-bridge",
            },
            timeout=timeout,
        )
        failed_bridge = _post_json_url(
            f"{requester_hub.hub_url.rstrip('/')}/api/hub/v1/payout/bridge/{bridge.get('bridge_payout', {}).get('bridge_payout_id')}/fail",
            {"reason": "injected_bridge_failure"},
            timeout=timeout,
        )
        recovered_bridge = _post_json_url(
            f"{requester_hub.hub_url.rstrip('/')}/api/hub/v1/payout/bridge/{bridge.get('bridge_payout', {}).get('bridge_payout_id')}/confirm",
            {"settlement_reference": f"{scenario_id}-bridge-confirmed"},
            timeout=timeout,
        )
        payout_status = _read_json_url(f"{requester_hub.hub_url.rstrip('/')}/api/hub/v1/payout/status", timeout=timeout)
    finally:
        for sock in list(sockets):
            try:
                _ws_send_json(sock, {"type": "worker.close"})
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    payout = payout_status.get("payout") if isinstance(payout_status.get("payout"), dict) else {}
    holds = [item for item in payout.get("holds", []) if isinstance(item, dict)]
    charges = [item for item in payout.get("charges", []) if isinstance(item, dict)]
    earnings = [item for item in payout.get("worker_earnings", []) if isinstance(item, dict)]
    events.extend(payout.get("events", []))

    success_payout = (success["continuation"].get("accepted_session") or {}).get("payout", {})
    failure_payout = (failure["continuation"].get("accepted_session") or {}).get("payout", {})
    reconnect_payout = (reconnect_success["continuation"].get("accepted_session") or {}).get("payout", {})
    proof = {
        "requester_actor_msk_issued": bool(requester_msk.get("ok")),
        "worker_actor_msk_issued": bool(worker_msk.get("ok")),
        "remote_handoff_succeeded_before_reconnect": (success["response"].get("handoff") or {}).get("routed") is True,
        "worker_result_charged_payout_hold": success["continuation"].get("status") == "succeeded"
        and success_payout.get("hold_status") == "charged"
        and bool(success_payout.get("charge_id"))
        and bool(success_payout.get("worker_earning_id")),
        "worker_failure_released_payout_hold": failure["continuation"].get("status") == "failed"
        and failure_payout.get("hold_status") == "released",
        "duplicate_worker_result_was_idempotent": duplicate_result_ack.get("idempotent") is True,
        "worker_disconnected_and_reconnected": auth1.get("connection_id") != auth2.get("connection_id")
        and reconnect_success["continuation"].get("status") == "succeeded",
        "reconnect_handoff_used_new_owner_hub": (reconnect_success["response"].get("handoff") or {}).get("to_hub_id") == requester_hub.hub_id,
        "market_price_constraint_rejected_too_low_request": low_price_status == 409
        and low_price_body.get("error") == "worker_not_live",
        "worker_earnings_created_only_for_successes": len(earnings) == 2
        and all(str(item.get("status")) == "earned" for item in earnings),
        "payout_claim_idempotency": claim.get("claim", {}).get("claim_id")
        == duplicate_claim.get("claim", {}).get("claim_id"),
        "settlement_and_bridge_path_completed": settlement.get("settlement", {}).get("status") == "settled"
        and failed_bridge.get("bridge_payout", {}).get("status") == "failed"
        and recovered_bridge.get("bridge_payout", {}).get("status") == "confirmed",
        "no_double_charge_on_duplicate_result": len(charges) == 2,
        "accepted_sessions_linked_to_payout_path": bool(reconnect_payout.get("charge_id"))
        and len([hold for hold in holds if hold.get("status") in {"charged", "released"}]) >= 3,
    }
    ok = all(bool(value) for value in proof.values())

    return {
        "ok": ok,
        "mode": "verify-stress",
        "topology_path": str(topology_path),
        "cluster_id": topology.cluster_id,
        "actors": {
            "requester": {
                "hub_id": requester_hub.hub_id,
                "hub_url": requester_hub.hub_url,
                "multisession_key_id": requester_key_id,
            },
            "worker": {
                "worker_id": worker_id,
                "first_hub_id": worker_hub.hub_id,
                "reconnect_hub_id": requester_hub.hub_id,
                "multisession_key_id": worker_key_id,
            },
        },
        "traffic": {
            "success": success,
            "failure": failure,
            "reconnect_success": reconnect_success,
            "low_price_status": low_price_status,
            "low_price_body": low_price_body,
            "claim": claim,
            "duplicate_claim": duplicate_claim,
            "settlement": settlement,
            "bridge": bridge,
            "failed_bridge": failed_bridge,
            "recovered_bridge": recovered_bridge,
            "payout_status": payout_status,
        },
        "events": events,
        "metrics": {
            "requests_attempted": 4,
            "accepted_sessions": 3,
            "completed_sessions": 2,
            "failed_sessions": 1,
            "valid_market_rejections": 1,
            "holds": len(holds),
            "charges": len(charges),
            "worker_earnings": len(earnings),
            "invariant_violations": 0 if ok else 1,
        },
        "proof": proof,
    }


def _render_stable_hub_integrated_market_stress_text(result: dict[str, Any]) -> str:
    proof = result["proof"]
    metrics = result["metrics"]
    actors = result["actors"]
    lines = [
        f"Stable Hub integrated market stress: {'ok' if result['ok'] else 'failed'}",
        f"Topology: {result['topology_path']}",
        f"Cluster: {result['cluster_id']}",
        "Actors:",
        f"  requester entry Hub: {actors['requester']['hub_id']} {actors['requester']['hub_url']}",
        f"  worker id: {actors['worker']['worker_id']}",
        f"  worker first Hub: {actors['worker']['first_hub_id']}",
        f"  worker reconnect Hub: {actors['worker']['reconnect_hub_id']}",
        "Traffic:",
        f"  requests attempted: {metrics['requests_attempted']}",
        f"  accepted sessions: {metrics['accepted_sessions']}",
        f"  completed sessions: {metrics['completed_sessions']}",
        f"  failed sessions: {metrics['failed_sessions']}",
        f"  valid market rejections: {metrics['valid_market_rejections']}",
        "Payout:",
        f"  holds observed: {metrics['holds']}",
        f"  charges observed: {metrics['charges']}",
        f"  worker earnings observed: {metrics['worker_earnings']}",
        f"  invariant violations: {metrics['invariant_violations']}",
        "Proof:",
        f"  remote handoff succeeded before reconnect: {'yes' if proof['remote_handoff_succeeded_before_reconnect'] else 'no'}",
        f"  worker result charged payout hold: {'yes' if proof['worker_result_charged_payout_hold'] else 'no'}",
        f"  worker failure released payout hold: {'yes' if proof['worker_failure_released_payout_hold'] else 'no'}",
        f"  duplicate worker result was idempotent: {'yes' if proof['duplicate_worker_result_was_idempotent'] else 'no'}",
        f"  worker disconnected and reconnected: {'yes' if proof['worker_disconnected_and_reconnected'] else 'no'}",
        f"  reconnect handoff used new owner Hub: {'yes' if proof['reconnect_handoff_used_new_owner_hub'] else 'no'}",
        f"  market price constraint rejected too-low request: {'yes' if proof['market_price_constraint_rejected_too_low_request'] else 'no'}",
        f"  worker earnings created only for successes: {'yes' if proof['worker_earnings_created_only_for_successes'] else 'no'}",
        f"  payout claim idempotency: {'yes' if proof['payout_claim_idempotency'] else 'no'}",
        f"  settlement and bridge path completed: {'yes' if proof['settlement_and_bridge_path_completed'] else 'no'}",
        f"  no double charge on duplicate result: {'yes' if proof['no_double_charge_on_duplicate_result'] else 'no'}",
        f"  accepted sessions linked to payout path: {'yes' if proof['accepted_sessions_linked_to_payout_path'] else 'no'}",
    ]
    return "\n".join(lines)


def check_cluster(
    *,
    topology_path: str | Path = DEFAULT_TOPOLOGY,
    timeout: float = 10.0,
) -> dict[str, Any]:
    topology = load_stable_hub_topology(topology_path)
    deadline = time.monotonic() + timeout
    checks: list[dict[str, Any]] = []
    for hub in topology.hubs:
        last_error = ""
        identity: dict[str, Any] | None = None
        health: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            try:
                health = _read_json_url(f"{hub.hub_url.rstrip('/')}/health", timeout=1.0)
                identity = _read_json_url(
                    f"{hub.hub_url.rstrip('/')}/api/hub/v1/hub-identity",
                    timeout=1.0,
                )
                if health.get("ok") is True and identity.get("hub_id") == hub.hub_id:
                    break
                last_error = f"unexpected identity/health payload for {hub.hub_id}"
            except (OSError, URLError, TimeoutError, json.JSONDecodeError, StableHubTopologyError) as exc:
                last_error = str(exc)
            time.sleep(0.1)
        else:
            checks.append(
                {
                    "ok": False,
                    "hub_id": hub.hub_id,
                    "hub_url": hub.hub_url,
                    "error": last_error or "timed out waiting for stable Hub",
                }
            )
            continue

        assert identity is not None
        assert health is not None
        checks.append(
            {
                "ok": True,
                "hub_id": hub.hub_id,
                "hub_url": hub.hub_url,
                "cluster_id": identity.get("cluster_id"),
                "storage_namespace": (identity.get("storage") or {}).get("namespace"),
                "auth": (identity.get("contract") or {}).get("auth"),
                "worker_connection": (identity.get("contract") or {}).get("worker_connection"),
            }
        )

    ok = all(bool(item.get("ok")) for item in checks)
    return {
        "ok": ok,
        "mode": "check-cluster",
        "topology_path": str(topology_path),
        "cluster_id": topology.cluster_id,
        "checks": checks,
    }


def _render_check_text(result: dict[str, Any]) -> str:
    lines = [
        f"Stable Hub cluster check: {'ok' if result['ok'] else 'failed'}",
        f"Topology: {result['topology_path']}",
        f"Cluster: {result['cluster_id']}",
    ]
    for check in result["checks"]:
        status = "ok" if check.get("ok") else "failed"
        line = f"  {check['hub_id']} {check['hub_url']}: {status}"
        if check.get("ok"):
            line += (
                f" cluster={check.get('cluster_id')}"
                f" namespace={check.get('storage_namespace')}"
                f" auth={check.get('auth')}"
                f" worker_connection={check.get('worker_connection')}"
            )
        else:
            line += f" error={check.get('error')}"
        lines.append(line)
    return "\n".join(lines)


def _child_command(topology_path: str | Path, hub_id: str) -> list[str]:
    return [
        sys.executable,
        "-u",
        "-m",
        "main_computer.stable_hub",
        "--topology",
        str(topology_path),
        "--hub-id",
        hub_id,
    ]


def _terminate_children(children: Sequence[subprocess.Popen[object]]) -> None:
    for child in children:
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + 5.0
    for child in children:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            child.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            child.kill()


def serve_cluster(
    *,
    topology_path: str | Path = DEFAULT_TOPOLOGY,
    check_after_start: bool = False,
    startup_timeout: float = 10.0,
) -> int:
    topology = load_stable_hub_topology(topology_path)
    print("Stable Hub lab cluster starting", flush=True)
    print(f"Topology: {topology_path}", flush=True)
    print(f"Cluster: {topology.cluster_id}", flush=True)
    print(f"FDB cluster file: {topology.storage['cluster_file']}", flush=True)
    print(f"FDB namespace: {topology.storage['namespace']}", flush=True)

    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    children: list[subprocess.Popen[object]] = []
    try:
        for hub in topology.hubs:
            cmd = _child_command(topology_path, hub.hub_id)
            print(f"Starting {hub.hub_id}: {hub.hub_url}", flush=True)
            children.append(subprocess.Popen(cmd, env=env))

        if check_after_start:
            result = check_cluster(topology_path=topology_path, timeout=startup_timeout)
            print(_render_check_text(result), flush=True)
            if not result["ok"]:
                return 1

        print("Stable Hub lab cluster running. Press Ctrl+C to stop.", flush=True)
        while True:
            for child in children:
                code = child.poll()
                if code is not None:
                    print(f"Stable Hub child exited with code {code}; stopping cluster.", file=sys.stderr)
                    return code or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("Stopping stable Hub lab cluster.", flush=True)
        return 0
    finally:
        _terminate_children(children)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.serve_cluster:
            return serve_cluster(
                topology_path=args.topology,
                check_after_start=args.check_after_start,
                startup_timeout=args.startup_timeout,
            )
        if args.check_cluster:
            result = check_cluster(topology_path=args.topology, timeout=args.startup_timeout)
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(_render_check_text(result))
            return 0 if result["ok"] else 1
        if args.verify_stress:
            result = build_stable_hub_integrated_market_stress_result(
                topology_path=args.topology,
                requester_wallet_path=args.requester_wallet,
                worker_wallet_path=args.worker_wallet,
                worker_entry_index=args.worker_entry_index,
                requester_entry_index=args.requester_entry_index,
                origin=args.origin,
                timeout=args.startup_timeout,
            )
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(_render_stable_hub_integrated_market_stress_text(result))
            return 0 if result["ok"] else 1
        if args.verify:
            result = build_stable_hub_verification_result(
                topology_path=args.topology,
                requester_wallet_path=args.requester_wallet,
                worker_wallet_path=args.worker_wallet,
                worker_entry_index=args.worker_entry_index,
                requester_entry_index=args.requester_entry_index,
                origin=args.origin,
                timeout=args.startup_timeout,
            )
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(_render_stable_hub_verification_text(result))
            return 0 if result["ok"] else 1
        if args.smoke_worker_live_session:
            result = build_worker_live_session_smoke_result(
                topology_path=args.topology,
                wallet_path=args.wallet,
                request_hub_id=args.request_hub_id,
                validate_hub_id=args.validate_hub_id,
                worker_hub_id=args.worker_hub_id,
                owner_check_hub_id=args.owner_check_hub_id,
                worker_id=args.worker_id,
                origin=args.origin,
                user_slug=args.user_slug,
                request_id=args.request_id,
                timeout=args.startup_timeout,
            )
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(_render_worker_live_session_smoke_text(result))
            return 0 if result["ok"] else 1
        if args.smoke_msk:
            result = build_stable_msk_smoke_result(
                topology_path=args.topology,
                wallet_path=args.wallet,
                request_hub_id=args.request_hub_id,
                validate_hub_id=args.validate_hub_id,
                origin=args.origin,
                user_slug=args.user_slug,
                request_id=args.request_id,
                timeout=args.startup_timeout,
            )
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(_render_msk_smoke_text(result))
            return 0 if result["ok"] else 1

        result = build_validate_only_result(
            topology_path=args.topology,
            worker_entry_index=args.worker_entry_index,
            requester_entry_index=args.requester_entry_index,
        )
    except StableHubTopologyError as exc:
        print(f"Stable Hub lab failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(_render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
