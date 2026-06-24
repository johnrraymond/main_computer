from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import queue
import secrets
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address


DEFAULT_HUB_URLS = "http://127.0.0.1:8870,http://127.0.0.1:8871,http://127.0.0.1:8872"
DEFAULT_REQUESTER_ACCOUNT = "exp-handoff-lab-requester"
DEFAULT_DEV_CHAIN_STATE_FILE = Path("runtime/deployments/dev/latest.json")
DEFAULT_REPORT_PATH = Path("runtime/exp-hub-lab/full_e2e_report.json")
DEFAULT_DEV_CHAIN_ID_HEX = "0x28757b2"
CREDIT_WEI_PER_CREDIT = 10**18


class ExpHubLabError(RuntimeError):
    pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and stress the experimental FDB Hub owner-handoff topology.",
    )
    parser.add_argument(
        "--hub-urls",
        default=DEFAULT_HUB_URLS,
        help="Comma-separated exp Hub base URLs. Defaults to the three-port dev topology.",
    )
    parser.add_argument("--check-cluster", action="store_true", help="Check that every exp Hub is answering and advertises peers.")
    parser.add_argument(
        "--verify-handoff",
        "--verify",
        dest="verify_handoff",
        action="store_true",
        help="Run live-session owner-Hub handoff traffic through the exp Hub topology.",
    )
    parser.add_argument(
        "--stress-requests",
        type=int,
        default=3,
        help="Number of sequential handoff work requests for --verify-handoff/--verify-full-e2e. Defaults to one per Hub URL.",
    )
    parser.add_argument(
        "--verify-full-e2e",
        action="store_true",
        help=(
            "Run the full exp owner-handoff + payout + dev-chain settlement lab: "
            "live-session handoff traffic, worker claim, settlement batch, dev-chain payout, and Hub receipt recording. "
            "This mode requires the target Hubs to run with --require-multisession-auth unless --allow-optional-multisession-auth is set."
        ),
    )
    parser.add_argument(
        "--require-multisession-auth",
        action="store_true",
        help=(
            "Require target Hubs to advertise multi-session auth and attach real dev-chain MSK authorizations to worker and requester traffic. "
            "This is implied by --verify-full-e2e."
        ),
    )
    parser.add_argument(
        "--allow-optional-multisession-auth",
        action="store_true",
        help="Compatibility escape hatch: allow --verify-full-e2e against Hubs that do not require multi-session auth.",
    )
    parser.add_argument(
        "--worker-credits",
        type=int,
        default=5_500_123,
        help="Per-request live worker price for --verify-full-e2e, in Hub credit units. Defaults to a high-precision amount.",
    )
    parser.add_argument(
        "--precision-places",
        type=int,
        default=3,
        help="Public worker settlement precision for full E2E settlement. Defaults to 3 decimal places.",
    )
    parser.add_argument("--dev-chain-state", type=Path, default=DEFAULT_DEV_CHAIN_STATE_FILE, help="Deployment state for dev-chain settlement.")
    parser.add_argument("--rpc-url", default=None, help="Dev-chain RPC URL. Defaults to deployment state or the local dev-chain default.")
    parser.add_argument("--chain-id", type=int, default=None, help="Expected dev-chain id. Defaults to deployment state or 31337.")
    parser.add_argument("--contract-address", default="", help="XLagBridgeReserve address override.")
    parser.add_argument("--worker-payout-address", default="", help="Recipient address override. Defaults to the dev-chain worker payout address.")
    parser.add_argument("--captain-address", default="", help="Unlocked dev-chain captain/officer address override.")
    parser.add_argument("--beta-second-address", default="", help="Unlocked dev-chain second officer address override.")
    parser.add_argument("--fund-units", type=int, default=None, help="Native dev-chain units to fund the reserve before payout. Defaults to rounded payout units.")
    parser.add_argument("--expires-blocks", type=int, default=100, help="Dev-chain payout proposal expiry window in blocks.")
    parser.add_argument("--mine-extra-blocks", type=int, default=1, help="Extra blocks to mine after payout delay.")
    parser.add_argument("--poll-s", type=float, default=0.25, help="Dev-chain receipt polling interval.")
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH, help="Full E2E JSON report path.")
    parser.add_argument(
        "--requester-account",
        default=DEFAULT_REQUESTER_ACCOUNT,
        help="Requester account_id funded through /api/hub/v1/credits/admin/issue before verification.",
    )
    parser.add_argument(
        "--funding-credits",
        type=int,
        default=100,
        help="Hub credits to issue to the requester account before verification.",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP/WebSocket timeout in seconds.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser



def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_dev_wallet_private_key() -> tuple[str, str]:
    """Return an ephemeral dev/test secp256k1 private key and wallet address.

    The key is process-local lab material.  It is never written to the report and
    should not be reused outside the current local E2E run.
    """

    while True:
        private_key = "0x" + secrets.token_hex(32)
        try:
            wallet_address = private_key_to_address(private_key)
        except ValueError:
            continue
        return private_key, wallet_address


def _issue_lab_multisession_key(
    *,
    hub_url: str,
    private_key: str,
    scope: str,
    actor: str,
    timeout: float,
) -> dict[str, Any]:
    wallet_address = private_key_to_address(private_key)
    issued_at = datetime.now(timezone.utc)
    message = {
        "purpose": "request_multi_session_key",
        "wallet_address": wallet_address,
        "chain_id": DEFAULT_DEV_CHAIN_ID_HEX,
        "request_id": f"{scope}-{actor}-{secrets.token_urlsafe(8).rstrip('=')}",
        "issued_at": issued_at.isoformat(),
        "expires_at": (issued_at + timedelta(minutes=15)).isoformat(),
        "origin": "tools.exp_hub_lab",
    }
    response = _post_json_url(
        f"{hub_url}/api/hub/v1/credits/multisession-keys/request",
        {"signed_request": build_personal_sign_blob(message=message, private_key=private_key, chain_id=DEFAULT_DEV_CHAIN_ID_HEX)},
        timeout=timeout,
    )
    _require_ok(response, label=f"{actor} multi-session key request")
    key = response.get("key") if isinstance(response.get("key"), dict) else {}
    key_id = str(key.get("id") or "")
    if not key_id:
        raise ExpHubLabError(f"{actor} multi-session key request did not return a key id: {response}")
    return {
        "ok": True,
        "actor": actor,
        "wallet_address": wallet_address,
        "account_id": wallet_account_id(wallet_address),
        "key_id": key_id,
        "key": key,
        "response": response,
    }


def _lab_multisession_authorization(key_info: dict[str, Any], *, max_authorized_credits: int) -> dict[str, Any]:
    key = key_info.get("key") if isinstance(key_info.get("key"), dict) else {}
    wallet_address = str(key_info.get("wallet_address") or key.get("wallet_address") or "")
    key_id = str(key_info.get("key_id") or key.get("id") or "")
    chain_id = str(key.get("chain_id") or DEFAULT_DEV_CHAIN_ID_HEX)
    max_credits = max(1, int(max_authorized_credits or 1))
    return {
        "kind": "multisession_key",
        "wallet_address": wallet_address,
        "multisession_key_id": key_id,
        "key_id": key_id,
        "chain_id": chain_id,
        "max_authorized_credits": max_credits,
        "max_authorized_credit_wei": str(max_credits * CREDIT_WEI_PER_CREDIT),
    }


def _hub_identity_requires_multisession_auth(identity: dict[str, Any]) -> bool:
    auth = identity.get("auth") if isinstance(identity.get("auth"), dict) else {}
    return bool(
        identity.get("multi_session_auth_required")
        or identity.get("multisession_auth_required")
        or auth.get("multi_session_auth_required")
        or auth.get("multisession_auth_required")
        or auth.get("required")
    )



def _hub_urls(value: str | Sequence[str]) -> list[str]:
    if isinstance(value, str):
        urls = [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
    else:
        urls = [str(item).strip().rstrip("/") for item in value if str(item).strip()]
    if len(urls) < 2:
        raise ExpHubLabError("exp Hub handoff lab requires at least two Hub URLs.")
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ExpHubLabError(f"invalid Hub URL: {url!r}")
    return urls


def _read_json_url_status(url: str, *, timeout: float) -> tuple[int, dict[str, Any]]:
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - local/topology-owned lab URL
            body = response.read().decode("utf-8") or "{}"
            decoded = json.loads(body)
            return int(response.status), decoded if isinstance(decoded, dict) else {"ok": False, "body": decoded}
    except HTTPError as exc:
        body = exc.read().decode("utf-8") or "{}"
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError:
            decoded = {"ok": False, "body": body}
        return int(exc.code), decoded if isinstance(decoded, dict) else {"ok": False, "body": decoded}


def _read_json_url(url: str, *, timeout: float) -> dict[str, Any]:
    status, payload = _read_json_url_status(url, timeout=timeout)
    if not 200 <= status < 300:
        raise ExpHubLabError(f"GET {url} failed with HTTP {status}: {payload}")
    return payload


def _post_json_url_status(url: str, payload: dict[str, Any], *, timeout: float) -> tuple[int, dict[str, Any]]:
    request = Request(
        url,
        data=json.dumps(payload, sort_keys=True).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - local/topology-owned lab URL
            body = response.read().decode("utf-8") or "{}"
            decoded = json.loads(body)
            return int(response.status), decoded if isinstance(decoded, dict) else {"ok": False, "body": decoded}
    except HTTPError as exc:
        body = exc.read().decode("utf-8") or "{}"
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError:
            decoded = {"ok": False, "body": body}
        return int(exc.code), decoded if isinstance(decoded, dict) else {"ok": False, "body": decoded}


def _post_json_url(url: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    status, body = _post_json_url_status(url, payload, timeout=timeout)
    if not 200 <= status < 300:
        raise ExpHubLabError(f"POST {url} failed with HTTP {status}: {body}")
    return body


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (candidate / "exp-fdb-hub.py").exists() or (candidate / "main_computer").is_dir():
            return candidate
    return current.parents[2]


def _load_chain_settlement_helpers() -> Any:
    """Load the existing local-chain settlement smoke helpers without making scripts/ a package."""

    path = _repo_root() / "scripts" / "run_worker_local_chain_settlement_execution_smoke.py"
    if not path.exists():
        raise ExpHubLabError(f"missing local-chain settlement helper script: {path}")
    spec = importlib.util.spec_from_file_location("_exp_hub_lab_chain_settlement_smoke", path)
    if spec is None or spec.loader is None:
        raise ExpHubLabError(f"could not load local-chain settlement helper script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _positive_int(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(0, parsed)


def _clean_scope(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(value or "").strip().lower()).strip("-")
    return text or f"exp-full-e2e-{int(time.time())}"


def _extract_worker_earning_ids(handoff: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for item in handoff.get("requests") or []:
        if not isinstance(item, dict):
            continue
        payout = item.get("terminal_ack", {}).get("payout", {}) if isinstance(item.get("terminal_ack"), dict) else {}
        if isinstance(payout, dict):
            earning_id = str(payout.get("worker_earning_id") or "").strip()
            if earning_id and earning_id not in ids:
                ids.append(earning_id)
    return ids


def _require_ok(payload: dict[str, Any], *, label: str) -> dict[str, Any]:
    if payload.get("ok") is not True:
        raise ExpHubLabError(f"{label} failed: {payload}")
    return payload


def _recv_until_headers(sock: socket.socket) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _ws_connect(base_url: str, path: str, *, timeout: float) -> socket.socket:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ExpHubLabError("exp Hub lab WebSocket helper supports local http URLs only.")
    port = parsed.port or 80
    sock = socket.create_connection((parsed.hostname, port), timeout=timeout)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = _recv_until_headers(sock).decode("iso-8859-1", errors="replace")
    if not response.startswith("HTTP/1.1 101"):
        sock.close()
        raise ExpHubLabError(f"WebSocket upgrade failed for {base_url}{path}: {response}")
    return sock


def _ws_send_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    first = 0x81
    if len(data) < 126:
        header = struct.pack("!BB", first, 0x80 | len(data))
    elif len(data) <= 0xFFFF:
        header = struct.pack("!BBH", first, 0x80 | 126, len(data))
    else:
        header = struct.pack("!BBQ", first, 0x80 | 127, len(data))
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
    sock.sendall(header + mask + masked)


def _ws_recv_json(sock: socket.socket) -> dict[str, Any]:
    while True:
        header = sock.recv(2)
        if len(header) != 2:
            raise ExpHubLabError("WebSocket closed while reading frame header.")
        first, second = header[0], header[1]
        opcode = first & 0x0F
        length = second & 0x7F
        masked = bool(second & 0x80)
        if length == 126:
            length = struct.unpack("!H", sock.recv(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", sock.recv(8))[0]
        mask = sock.recv(4) if masked else b""
        payload = b""
        while len(payload) < length:
            chunk = sock.recv(length - len(payload))
            if not chunk:
                raise ExpHubLabError("WebSocket closed while reading frame payload.")
            payload += chunk
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if opcode == 0x9:
            sock.sendall(bytes([0x8A, len(payload)]) + payload)
            continue
        if opcode == 0xA:
            continue
        if opcode == 0x8:
            raise ExpHubLabError("WebSocket closed by peer.")
        if opcode != 0x1:
            raise ExpHubLabError(f"unexpected WebSocket opcode {opcode}")
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ExpHubLabError("WebSocket JSON payload was not an object.")
        return decoded


def check_cluster(
    *,
    hub_urls: str | Sequence[str] = DEFAULT_HUB_URLS,
    timeout: float = 10.0,
    require_multisession_auth: bool = False,
) -> dict[str, Any]:
    urls = _hub_urls(hub_urls)
    checks: list[dict[str, Any]] = []
    for url in urls:
        try:
            # Exp Hubs intentionally do not expose a generic /health route.  The
            # lab should exercise the actual Hub API surface it needs: identity
            # and topology.  This also gives a stronger cluster check than a
            # health probe because it proves each Hub knows about its peers.
            identity = _read_json_url(f"{url}/api/hub/v1/hub-identity", timeout=timeout)
            topology_response = _read_json_url(f"{url}/api/hub/v1/topology", timeout=timeout)
            peers = identity.get("peer_hubs") if isinstance(identity.get("peer_hubs"), list) else []
            topology = topology_response.get("topology") if isinstance(topology_response.get("topology"), dict) else {}
            topology_hubs = topology.get("hubs") if isinstance(topology.get("hubs"), list) else []
            topology_entry_urls = topology.get("entry_urls") if isinstance(topology.get("entry_urls"), list) else []
            identity_entry_urls = identity.get("entry_urls") if isinstance(identity.get("entry_urls"), list) else []
            multisession_required = _hub_identity_requires_multisession_auth(identity)
            base_ok = (
                identity.get("ok") is True
                and identity.get("service") == "main_computer.exp_fdb_hub"
                and topology_response.get("ok") is True
                and topology_response.get("service") == "main_computer.exp_fdb_hub"
            )
            ok = base_ok and (multisession_required or not require_multisession_auth)
            checks.append(
                {
                    "ok": ok,
                    "hub_url": url,
                    "hub_id": identity.get("hub_id"),
                    "cluster_id": identity.get("cluster_id"),
                    "topology_cluster_id": topology.get("cluster_id") or topology_response.get("cluster_id"),
                    "peer_count": len(peers),
                    "topology_hub_count": len(topology_hubs),
                    "entry_urls": identity_entry_urls,
                    "topology_entry_urls": topology_entry_urls,
                    "handoff_contract": (identity.get("contract") or {}).get("hub_to_hub_handoff"),
                    "bridge_backend": ((identity.get("bridge") or {}).get("backend") if isinstance(identity.get("bridge"), dict) else None),
                    "multi_session_auth_required": multisession_required,
                    "error": "" if ok else (
                        "multi_session_auth_not_required"
                        if base_ok and require_multisession_auth and not multisession_required
                        else "identity_or_topology_check_failed"
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001 - surfaced in lab output
            checks.append({"ok": False, "hub_url": url, "error": str(exc), "multi_session_auth_required": False})

    expected_peer_count = len(urls) - 1
    expected_hub_count = len(urls)
    expected_urls = set(urls)
    ok = all(
        item.get("ok")
        and int(item.get("peer_count", -1) or -1) == expected_peer_count
        and int(item.get("topology_hub_count", -1) or -1) == expected_hub_count
        and set(str(value).rstrip("/") for value in (item.get("entry_urls") or [])) == expected_urls
        and set(str(value).rstrip("/") for value in (item.get("topology_entry_urls") or [])) == expected_urls
        and (bool(item.get("multi_session_auth_required")) or not require_multisession_auth)
        for item in checks
    )
    return {
        "ok": ok,
        "mode": "check-cluster",
        "hub_urls": urls,
        "expected_peer_count": expected_peer_count,
        "expected_hub_count": expected_hub_count,
        "require_multisession_auth": bool(require_multisession_auth),
        "checks": checks,
    }


def _render_check_text(result: dict[str, Any]) -> str:
    lines = [
        f"Exp Hub handoff lab cluster check: {'ok' if result['ok'] else 'failed'}",
        f"Hub URLs: {', '.join(result['hub_urls'])}",
        f"Expected peer count per Hub: {result['expected_peer_count']}",
        f"Expected topology Hub count: {result.get('expected_hub_count', len(result['hub_urls']))}",
        f"Multi-session auth required by lab: {'yes' if result.get('require_multisession_auth') else 'no'}",
    ]
    for check in result["checks"]:
        status = "ok" if check.get("ok") else "failed"
        line = f"  {check.get('hub_id') or '?'} {check['hub_url']}: {status}"
        if check.get("ok"):
            line += (
                f" cluster={check.get('cluster_id')}"
                f" peers={check.get('peer_count')}"
                f" topology_hubs={check.get('topology_hub_count')}"
                f" handoff={check.get('handoff_contract')}"
                f" msk_auth={'required' if check.get('multi_session_auth_required') else 'optional'}"
            )
        else:
            line += f" error={check.get('error')}"
        lines.append(line)
    return "\n".join(lines)




def _continuation_points_to_hub(response: dict[str, Any], continuation: dict[str, Any], owner_hub_url: str) -> bool:
    owner = str(owner_hub_url or "").rstrip("/")
    if not owner:
        return False
    session_path_prefix = owner + "/api/hub/v1/work/sessions/"
    response_url = str(response.get("continuation_url") or "")
    continuation_url = str(continuation.get("continuation_url") or "")
    execution_hub = continuation.get("execution_hub") if isinstance(continuation.get("execution_hub"), dict) else {}
    continuation_hub_url = str(continuation.get("hub_url") or execution_hub.get("hub_url") or "").rstrip("/")
    return (
        response_url.startswith(session_path_prefix)
        and continuation_url.startswith(session_path_prefix)
        and continuation_hub_url == owner
    )


def verify_handoff(
    *,
    hub_urls: str | Sequence[str] = DEFAULT_HUB_URLS,
    requester_account: str = DEFAULT_REQUESTER_ACCOUNT,
    funding_credits: int = 100,
    stress_requests: int = 3,
    timeout: float = 10.0,
    worker_credits: int = 1,
    require_multisession_auth: bool = False,
    requester_private_key: str = "",
    worker_private_key: str = "",
) -> dict[str, Any]:
    urls = _hub_urls(hub_urls)
    cluster = check_cluster(hub_urls=urls, timeout=timeout, require_multisession_auth=require_multisession_auth)
    if not cluster.get("ok"):
        return {
            "ok": False,
            "mode": "verify-handoff",
            "hub_urls": urls,
            "cluster": cluster,
            "error": "cluster_check_failed",
        }

    entry_url = urls[0]
    worker_url = urls[-1]
    scenario_id = "exp_handoff_lab_" + secrets.token_urlsafe(8).rstrip("=")
    worker_id = "worker_" + scenario_id
    request_count = max(1, int(stress_requests or 1))
    live_worker_credits = max(1, int(worker_credits or 1))
    request_max_credits = max(3, live_worker_credits + 1)

    requester_key_info: dict[str, Any] | None = None
    worker_key_info: dict[str, Any] | None = None
    requester_auth: dict[str, Any] | None = None
    worker_auth: dict[str, Any] | None = None
    effective_requester_account = requester_account
    if require_multisession_auth:
        if not requester_private_key:
            requester_private_key, _requester_wallet = _new_dev_wallet_private_key()
        if not worker_private_key:
            worker_private_key, _worker_wallet = _new_dev_wallet_private_key()
        requester_key_info = _issue_lab_multisession_key(
            hub_url=entry_url,
            private_key=requester_private_key,
            scope=scenario_id,
            actor="requester",
            timeout=timeout,
        )
        worker_key_info = _issue_lab_multisession_key(
            hub_url=entry_url,
            private_key=worker_private_key,
            scope=scenario_id,
            actor="worker",
            timeout=timeout,
        )
        requester_auth = _lab_multisession_authorization(
            requester_key_info,
            max_authorized_credits=max(request_max_credits, int(funding_credits or 1), request_count * request_max_credits + 10),
        )
        worker_auth = _lab_multisession_authorization(worker_key_info, max_authorized_credits=max(1, live_worker_credits))
        effective_requester_account = str(requester_key_info["account_id"])

    funding = _post_json_url(
        f"{entry_url}/api/hub/v1/credits/admin/issue",
        {
            "account_id": effective_requester_account,
            "credits": max(1, int(funding_credits or 1)),
            "memo": f"exp handoff lab funding {scenario_id}",
            "metadata": {
                "scenario_id": scenario_id,
                "lab": "tools.exp_hub_lab",
                "auth_mode": "multisession-wallet" if require_multisession_auth else "optional",
                "wallet_address": (requester_key_info or {}).get("wallet_address", ""),
                "multisession_key_id": (requester_key_info or {}).get("key_id", ""),
            },
        },
        timeout=timeout,
    )

    sock: socket.socket | None = None
    submissions: list[dict[str, Any]] = []
    try:
        sock = _ws_connect(worker_url, "/api/hub/v1/workers/live-session", timeout=timeout)
        worker_auth_message: dict[str, Any] = {
            "type": "worker.auth",
            "worker_id": worker_id,
            "market": {
                "rings": ["ring-3"],
                "price": {"amount": str(live_worker_credits), "unit": "compute_credit"},
                "capabilities": ["text", "mock-ai", "echo"],
                "max_concurrency": 1,
                "settlement_precision_places": 3,
            },
        }
        if worker_auth:
            worker_auth_message["chain_id"] = worker_auth.get("chain_id", DEFAULT_DEV_CHAIN_ID_HEX)
            worker_auth_message["wallet_address"] = worker_auth.get("wallet_address", "")
            worker_auth_message["multisession_authorization"] = worker_auth
            worker_auth_message["payment_authorization"] = worker_auth
        _ws_send_json(sock, worker_auth_message)
        accepted = _ws_recv_json(sock)
        ping = _ws_recv_json(sock)
        if accepted.get("type") != "hub.auth.accepted" or ping.get("type") != "hub.ping":
            raise ExpHubLabError(f"worker auth failed: auth={accepted} ping={ping}")
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
            raise ExpHubLabError(f"worker pong failed: {pong}")

        for index in range(request_count):
            submit_url = urls[index % len(urls)]
            request_id = f"{scenario_id}_req_{index + 1}"
            result_queue: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()

            def _submit() -> None:
                try:
                    status, body = _post_json_url_status(
                        f"{submit_url}/api/hub/v1/work/requests",
                        {
                            "request_id": request_id,
                            "client_node_id": requester_account,
                            "account_id": effective_requester_account,
                            "max_credits": request_max_credits,
                            "ring": "ring-3",
                            "capabilities": ["text"],
                            "input": {"prompt": f"exp handoff lab request {index + 1}"},
                            "messages": [{"role": "user", "content": f"exp handoff lab request {index + 1}"}],
                            **(
                                {
                                    "chain_id": requester_auth.get("chain_id", DEFAULT_DEV_CHAIN_ID_HEX),
                                    "wallet_address": requester_auth.get("wallet_address", ""),
                                    "multisession_authorization": requester_auth,
                                    "payment_authorization": requester_auth,
                                    "metadata": {
                                        "auth_mode": "multisession-wallet",
                                        "wallet_address": requester_auth.get("wallet_address", ""),
                                        "multisession_key_id": requester_auth.get("multisession_key_id", ""),
                                        "scenario_id": scenario_id,
                                    },
                                }
                                if requester_auth
                                else {}
                            ),
                        },
                        timeout=max(timeout, 15.0),
                    )
                    if status != 200:
                        result_queue.put(ExpHubLabError(f"request {request_id} failed HTTP {status}: {body}"))
                    else:
                        result_queue.put(body)
                except BaseException as exc:  # noqa: BLE001 - surfaced below
                    result_queue.put(exc)

            thread = threading.Thread(target=_submit, daemon=True)
            thread.start()
            offer = _ws_recv_json(sock)
            if offer.get("type") != "hub.work.offer":
                raise ExpHubLabError(f"worker did not receive work offer: {offer}")
            _ws_send_json(
                sock,
                {
                    "type": "worker.work.accepted",
                    "session_id": offer.get("session_id"),
                    "run_id": offer.get("run_id"),
                    "request_id": offer.get("request_id"),
                    "worker_id": worker_id,
                },
            )
            queued = result_queue.get(timeout=max(timeout, 15.0))
            thread.join(timeout=1.0)
            if isinstance(queued, BaseException):
                raise queued
            response = queued
            _ws_send_json(
                sock,
                {
                    "type": "worker.work.result",
                    "session_id": offer.get("session_id"),
                    "run_id": offer.get("run_id"),
                    "request_id": offer.get("request_id"),
                    "worker_id": worker_id,
                    "lease_id": offer.get("lease_id"),
                    "result": {
                        "status": "success",
                        "response": {
                            "content": f"exp handoff lab result {index + 1}",
                            "provider": "exp-live-worker",
                            "model": "live-session-worker",
                            "metadata": {"scenario_id": scenario_id},
                        },
                    },
                },
            )
            ack = _ws_recv_json(sock)
            continuation = _read_json_url(str(response.get("continuation_url")), timeout=timeout)
            submissions.append(
                {
                    "request_id": request_id,
                    "entry_url": submit_url,
                    "offer": offer,
                    "response": response,
                    "terminal_ack": ack,
                    "continuation": continuation,
                }
            )
    finally:
        if sock is not None:
            try:
                _ws_send_json(sock, {"type": "worker.close"})
            except OSError:
                pass
            sock.close()

    proof = {
        "cluster_has_three_hubs": len(urls) >= 3 and cluster.get("ok") is True,
        "multi_session_auth_required": (
            not require_multisession_auth
            or all(bool(item.get("multi_session_auth_required")) for item in cluster.get("checks", []))
        ),
        "requester_msk_issued": (not require_multisession_auth) or bool(requester_key_info and requester_key_info.get("key_id")),
        "worker_msk_issued": (not require_multisession_auth) or bool(worker_key_info and worker_key_info.get("key_id")),
        "worker_connected_with_msk": (
            not require_multisession_auth
            or (
                bool(accepted.get("owner", {}).get("multisession_key_id") if isinstance(accepted.get("owner"), dict) else "")
                and accepted.get("owner", {}).get("multisession_key_id") == (worker_key_info or {}).get("key_id")
            )
        ),
        "requests_signed_with_msk": (
            not require_multisession_auth
            or all(
                (item["response"].get("request") or {}).get("metadata", {}).get("multisession_key_authorized") is True
                or (item["response"].get("accepted_session") or {}).get("requester_msk_id") == (requester_key_info or {}).get("key_id")
                or (item["continuation"].get("accepted_session") or {}).get("requester_msk_id") == (requester_key_info or {}).get("key_id")
                or (item["terminal_ack"].get("accepted_session") or {}).get("requester_msk_id") == (requester_key_info or {}).get("key_id")
                for item in submissions
            )
        ),
        "worker_connected_to_owner_hub": bool(submissions)
        and all((item["offer"].get("execution_hub") or {}).get("hub_url") == worker_url for item in submissions),
        "all_requests_accepted": len(submissions) == request_count
        and all((item["response"].get("ok") is True and item["response"].get("accepted") is True) for item in submissions),
        "remote_entries_handoff_to_owner": all(
            (
                item["entry_url"] == worker_url
                or (
                    item["response"].get("hub_to_hub_handoff") is True
                    and (item["response"].get("execution_hub") or {}).get("hub_url") == worker_url
                )
            )
            for item in submissions
        ),
        "continuations_point_to_owner_hub": all(
            _continuation_points_to_hub(item["response"], item["continuation"], worker_url)
            for item in submissions
        ),
        "owner_hub_charged_exp_ledger": all(
            item["terminal_ack"].get("ok") is True
            and (item["terminal_ack"].get("payout") or {}).get("status") == "charged"
            and bool((item["terminal_ack"].get("payout") or {}).get("charge_id"))
            and bool((item["terminal_ack"].get("payout") or {}).get("worker_earning_id"))
            for item in submissions
        ),
    }
    ok = all(bool(value) for value in proof.values())
    return {
        "ok": ok,
        "mode": "verify-handoff",
        "hub_urls": urls,
        "scenario_id": scenario_id,
        "requester_account": effective_requester_account,
        "requested_requester_account": requester_account,
        "worker_id": worker_id,
        "multi_session_auth": {
            "required": bool(require_multisession_auth),
            "requester_wallet_address": (requester_key_info or {}).get("wallet_address", ""),
            "requester_account_id": (requester_key_info or {}).get("account_id", ""),
            "requester_key_id": (requester_key_info or {}).get("key_id", ""),
            "worker_wallet_address": (worker_key_info or {}).get("wallet_address", ""),
            "worker_key_id": (worker_key_info or {}).get("key_id", ""),
        },
        "worker_credits": live_worker_credits,
        "funding": funding,
        "cluster": cluster,
        "requests": submissions,
        "metrics": {
            "requests_attempted": request_count,
            "requests_accepted": len(submissions),
            "remote_handoffs": sum(
                1
                for item in submissions
                if item["entry_url"] != worker_url and item["response"].get("hub_to_hub_handoff") is True
            ),
            "charged_results": sum(
                1
                for item in submissions
                if (item["terminal_ack"].get("payout") or {}).get("status") == "charged"
            ),
            "msk_authorized_requests": len(submissions) if proof.get("requests_signed_with_msk") else 0,
            "invariant_violations": 0 if ok else 1,
        },
        "proof": proof,
    }



def verify_full_e2e(
    *,
    hub_urls: str | Sequence[str] = DEFAULT_HUB_URLS,
    requester_account: str = DEFAULT_REQUESTER_ACCOUNT,
    funding_credits: int = 100,
    stress_requests: int = 3,
    timeout: float = 10.0,
    worker_credits: int = 5_500_123,
    precision_places: int = 3,
    dev_chain_state: Path = DEFAULT_DEV_CHAIN_STATE_FILE,
    rpc_url: str | None = None,
    chain_id: int | None = None,
    contract_address: str = "",
    worker_payout_address: str = "",
    captain_address: str = "",
    beta_second_address: str = "",
    fund_units: int | None = None,
    expires_blocks: int = 100,
    mine_extra_blocks: int = 1,
    poll_s: float = 0.25,
    report_path: Path = DEFAULT_REPORT_PATH,
    require_multisession_auth: bool = True,
) -> dict[str, Any]:
    """Run exp owner-handoff traffic and settle the resulting live-session earnings on dev-chain."""

    urls = _hub_urls(hub_urls)
    owner_url = urls[-1]
    scope = _clean_scope("exp_full_e2e_" + secrets.token_urlsafe(8).rstrip("="))
    worker_price = max(1, int(worker_credits or 1))
    request_count = max(1, int(stress_requests or 1))
    funded_credits = max(int(funding_credits or 0), request_count * (worker_price + 1) + 10)

    chain = _load_chain_settlement_helpers()
    repo_root = chain.find_repo_root(Path.cwd())
    state_path, state, env, state_candidates = chain.select_state(repo_root, dev_chain_state, contract_address)
    chain_args = SimpleNamespace(
        contract_address=contract_address,
        worker_payout_address=worker_payout_address,
    )
    offices = chain.offices_from_state(state, env)
    clean_contract = chain.resolve_contract_address(state, env, contract_address)
    clean_rpc_url = chain.resolve_rpc_url(state, env, rpc_url)
    clean_chain_id = chain.resolve_chain_id(state, env, chain_id)
    captain = chain.normalize_address(captain_address) if captain_address else offices[0]
    beta_second = chain.normalize_address(beta_second_address) if beta_second_address else offices[2]
    recipient = chain.resolve_worker_payout_address(chain_args, offices)

    steps: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "ok": False,
        "mode": "verify-full-e2e",
        "hub_urls": urls,
        "owner_hub_url": owner_url,
        "scope": scope,
        "state_file": str(state_path) if state_path else None,
        "state_candidates": state_candidates,
        "rpc_url": clean_rpc_url,
        "chain_id": clean_chain_id,
        "contract_address": clean_contract,
        "worker_payout_address": recipient,
        "worker_credits": worker_price,
        "precision_places": int(precision_places),
        "multi_session_auth_required": bool(require_multisession_auth),
        "steps": steps,
    }

    def add_step(name: str, payload: dict[str, Any]) -> dict[str, Any]:
        step = {"name": name, "ok": bool(payload.get("ok", True)), "payload": payload}
        steps.append(step)
        return payload

    try:
        actual_chain_id = int(str(chain.rpc(clean_rpc_url, "eth_chainId", [], timeout=timeout)), 16)
        add_step(
            "dev-chain rpc reachable",
            {
                "ok": actual_chain_id == int(clean_chain_id),
                "rpc_url": clean_rpc_url,
                "chain_id": actual_chain_id,
                "expected_chain_id": clean_chain_id,
            },
        )
        if actual_chain_id != int(clean_chain_id):
            raise ExpHubLabError(f"dev-chain id mismatch: expected {clean_chain_id}, got {actual_chain_id}")

        code = chain.get_code(clean_rpc_url, clean_contract, timeout=timeout)
        if not code or code == "0x":
            raise ExpHubLabError(f"XLagBridgeReserve contract address has no code: {clean_contract}")
        add_step("dev-chain reserve contract has code", {"ok": True, "contract_code_bytes": max(0, (len(code) - 2) // 2)})

        handoff = add_step(
            "run exp owner-handoff live-session traffic",
            verify_handoff(
                hub_urls=urls,
                requester_account=f"{requester_account}-{scope}",
                funding_credits=funded_credits,
                stress_requests=request_count,
                timeout=timeout,
                worker_credits=worker_price,
                require_multisession_auth=require_multisession_auth,
            ),
        )
        _require_ok(handoff, label="owner-handoff traffic")

        worker_id = str(handoff.get("worker_id") or "")
        earning_ids = _extract_worker_earning_ids(handoff)
        if not worker_id:
            raise ExpHubLabError("handoff lab did not return a worker_id")
        if len(earning_ids) != request_count:
            raise ExpHubLabError(f"expected {request_count} worker earning ids, got {len(earning_ids)}: {earning_ids}")

        earnings_view = add_step(
            "query owner Hub worker earnings",
            _read_json_url(
                f"{owner_url}/api/hub/v1/credits/worker-earnings?{urlencode({'worker_node_id': worker_id, 'limit': str(max(100, request_count * 2))})}",
                timeout=timeout,
            ),
        )
        _require_ok(earnings_view, label="worker earnings query")
        visible_earning_ids = {
            str(item.get("earning_id") or "")
            for item in earnings_view.get("worker_earnings", [])
            if isinstance(item, dict)
        }
        missing_earnings = [earning_id for earning_id in earning_ids if earning_id not in visible_earning_ids]
        if missing_earnings:
            raise ExpHubLabError(f"owner Hub worker earnings query missed live-session earnings: {missing_earnings}")

        claim = add_step(
            "record worker claim for live-session earnings",
            _post_json_url(
                f"{owner_url}/api/hub/v1/workers/claims",
                {
                    "worker_node_id": worker_id,
                    "earning_ids": earning_ids,
                    "idempotency_key": f"{scope}-claim",
                    "memo": "exp full E2E live-session handoff claim",
                    "metadata": {"exp_full_e2e": True, "scope": scope, "source": "tools.exp_hub_lab"},
                },
                timeout=timeout,
            ),
        )
        _require_ok(claim, label="worker claim")
        claim_payload = claim.get("claim") if isinstance(claim.get("claim"), dict) else {}
        claim_id = str(claim_payload.get("claim_id") or "")
        if not claim_id:
            raise ExpHubLabError(f"worker claim did not return a claim_id: {claim}")
        claimed_exact = _positive_int(claim_payload.get("claimed_credits"), default=0)

        settlement_before = add_step(
            "query owner Hub worker settlement before batch",
            _read_json_url(
                f"{owner_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker_id, 'audit': '1', 'precision_places': str(int(precision_places))})}",
                timeout=timeout,
            ),
        )
        _require_ok(settlement_before, label="worker settlement preflight")

        batch = add_step(
            "create worker settlement batch from live-session claim",
            _post_json_url(
                f"{owner_url}/api/hub/v1/workers/settlements/batches",
                {
                    "worker_node_id": worker_id,
                    "claim_ids": [claim_id],
                    "precision_places": int(precision_places),
                    "idempotency_key": f"{scope}-batch",
                    "bridge_account_id": "bridge-worker-payout-dust",
                    "metadata": {"exp_full_e2e": True, "scope": scope, "source": "tools.exp_hub_lab"},
                },
                timeout=timeout,
            ),
        )
        _require_ok(batch, label="settlement batch")
        batch_payload = batch.get("batch") if isinstance(batch.get("batch"), dict) else {}
        batch_id = str(batch_payload.get("batch_id") or "")
        if not batch_id:
            raise ExpHubLabError(f"settlement batch did not return a batch_id: {batch}")
        exact_units = _positive_int(batch_payload.get("total_credits_exact"), default=claimed_exact)
        published_units = _positive_int(batch_payload.get("total_credits_published"), default=0)
        dust_units = _positive_int(batch_payload.get("dust_credits"), default=0)
        if published_units <= 0:
            raise ExpHubLabError(
                "settlement batch rounded to zero; rerun with a larger --worker-credits or more --stress-requests."
            )

        exact_receipt_rejected = None
        if exact_units != published_units:
            status, exact_reject = _post_json_url_status(
                f"{owner_url}/api/hub/v1/workers/settlements/chain-executions",
                {
                    "batch_id": batch_id,
                    "chain_id": clean_chain_id,
                    "contract_address": clean_contract,
                    "recipient_address": recipient,
                    "payout_units_executed": exact_units,
                    "settlement_tx_hash": "0x" + "8" * 64,
                    "proposal_id": f"{scope}-bad-exact",
                    "block_number": 1,
                    "payout_rail": "xlag-bridge-reserve-local",
                    "operator_id": f"exp-full-e2e-operator-{scope}",
                    "idempotency_key": f"{scope}-exact-reject",
                },
                timeout=timeout,
            )
            exact_receipt_rejected = status >= 400
            add_step("reject exact high-precision chain receipt", {"ok": exact_receipt_rejected, "status": status, "response": exact_reject})

        chain_execution = add_step(
            "execute rounded payout on dev-chain",
            chain.execute_local_chain_payout(
                rpc_url=clean_rpc_url,
                expected_chain_id=clean_chain_id,
                contract_address=clean_contract,
                captain=captain,
                beta_second=beta_second,
                recipient_address=recipient,
                payout_units=published_units,
                fund_units=int(fund_units if fund_units is not None else published_units),
                memo=f"exp full E2E {batch_id}",
                expires_blocks=expires_blocks,
                mine_extra_blocks=mine_extra_blocks,
                timeout=timeout,
                poll_s=poll_s,
            ),
        )
        chain_tx_hash = chain.normalize_tx_hash(str(chain_execution.get("settlement_tx_hash", "")))
        proposal_id = str(chain_execution.get("proposal_id", ""))
        block_number = int(chain_execution.get("block_number", 0) or 0)
        if not chain_tx_hash:
            raise ExpHubLabError(f"dev-chain payout execution did not return a tx hash: {chain_execution}")

        proof_payload = {
            "exp_full_e2e": True,
            "scope": scope,
            "bridge": "xlag-bridge-reserve-local",
            "chain_id": clean_chain_id,
            "contract_address": clean_contract,
            "recipient_address": recipient,
            "proposal_id": proposal_id,
            "settlement_tx_hash": chain_tx_hash,
            "block_number": block_number,
            "exact_credits": exact_units,
            "executed_credits": published_units,
            "bridge_retained_credits": dust_units,
            "precision_places": int(precision_places),
            "event": chain_execution.get("payout_executed_event", {}),
        }

        settled = add_step(
            "record dev-chain payout receipt with owner Hub",
            _post_json_url(
                f"{owner_url}/api/hub/v1/workers/settlements/chain-executions",
                {
                    "batch_id": batch_id,
                    "chain_id": clean_chain_id,
                    "contract_address": clean_contract,
                    "recipient_address": recipient,
                    "payout_units_executed": published_units,
                    "settlement_tx_hash": chain_tx_hash,
                    "proposal_id": proposal_id,
                    "block_number": block_number,
                    "payout_rail": "xlag-bridge-reserve-local",
                    "operator_id": f"exp-full-e2e-operator-{scope}",
                    "settlement_reference": f"{scope}-settlement",
                    "settlement_proof": proof_payload,
                    "idempotency_key": f"{scope}-receipt",
                    "metadata": {"exp_full_e2e": True, "scope": scope, "source": "tools.exp_hub_lab"},
                },
                timeout=timeout,
            ),
        )
        _require_ok(settled, label="record chain receipt")
        settled_batch = settled.get("batch") if isinstance(settled.get("batch"), dict) else {}
        hub_execution = settled.get("chain_payout_execution") if isinstance(settled.get("chain_payout_execution"), dict) else {}

        duplicate_receipt = add_step(
            "duplicate dev-chain receipt is idempotent",
            _post_json_url(
                f"{owner_url}/api/hub/v1/workers/settlements/chain-executions",
                {
                    "batch_id": batch_id,
                    "chain_id": clean_chain_id,
                    "contract_address": clean_contract,
                    "recipient_address": recipient,
                    "payout_units_executed": published_units,
                    "settlement_tx_hash": chain_tx_hash,
                    "proposal_id": proposal_id,
                    "block_number": block_number,
                    "payout_rail": "xlag-bridge-reserve-local",
                    "operator_id": f"exp-full-e2e-operator-{scope}",
                    "settlement_reference": f"{scope}-settlement",
                    "settlement_proof": proof_payload,
                    "idempotency_key": f"{scope}-receipt",
                },
                timeout=timeout,
            ),
        )
        _require_ok(duplicate_receipt, label="duplicate chain receipt")

        settlement_after = add_step(
            "query owner Hub worker settlement after receipt",
            _read_json_url(
                f"{owner_url}/api/hub/v1/workers/settlements?{urlencode({'worker_node_id': worker_id, 'audit': '1', 'precision_places': str(int(precision_places))})}",
                timeout=timeout,
            ),
        )
        _require_ok(settlement_after, label="worker settlement after receipt")

        proof = {
            "cluster_has_three_hubs": bool((handoff.get("proof") or {}).get("cluster_has_three_hubs")),
            "multi_session_auth_required": bool((handoff.get("proof") or {}).get("multi_session_auth_required")),
            "requester_msk_issued": bool((handoff.get("proof") or {}).get("requester_msk_issued")),
            "worker_msk_issued": bool((handoff.get("proof") or {}).get("worker_msk_issued")),
            "worker_connected_with_msk": bool((handoff.get("proof") or {}).get("worker_connected_with_msk")),
            "requests_signed_with_msk": bool((handoff.get("proof") or {}).get("requests_signed_with_msk")),
            "remote_entries_handoff_to_owner": bool((handoff.get("proof") or {}).get("remote_entries_handoff_to_owner")),
            "continuations_point_to_owner_hub": bool((handoff.get("proof") or {}).get("continuations_point_to_owner_hub")),
            "owner_hub_charged_exp_ledger": bool((handoff.get("proof") or {}).get("owner_hub_charged_exp_ledger")),
            "worker_earnings_created": len(earning_ids) == request_count and not missing_earnings,
            "worker_claim_created": bool(claim_id),
            "settlement_batch_created": bool(batch_id) and published_units > 0,
            "dev_chain_payout_executed": bool(chain_tx_hash) and int(chain_execution.get("payout_units", 0) or 0) == published_units,
            "hub_recorded_chain_receipt": settled.get("ok") is True and str(hub_execution.get("settlement_tx_hash", "")).lower() == chain_tx_hash.lower(),
            "duplicate_receipt_idempotent": duplicate_receipt.get("ok") is True and int(duplicate_receipt.get("additional_settled_credits", 0) or 0) == 0,
            "exact_high_precision_receipt_rejected": True if exact_receipt_rejected is None else bool(exact_receipt_rejected),
        }
        ok = all(bool(value) for value in proof.values())
        metrics = {
            "requests_attempted": request_count,
            "requests_accepted": int((handoff.get("metrics") or {}).get("requests_accepted", 0) or 0),
            "remote_handoffs": int((handoff.get("metrics") or {}).get("remote_handoffs", 0) or 0),
            "charged_results": int((handoff.get("metrics") or {}).get("charged_results", 0) or 0),
            "worker_earnings_created": len(earning_ids),
            "worker_claims_created": 1 if claim_id else 0,
            "settlement_batches_created": 1 if batch_id else 0,
            "chain_payouts_executed": 1 if chain_tx_hash else 0,
            "exact_credits": exact_units,
            "rounded_payout_units": published_units,
            "bridge_retained_units": dust_units,
            "duplicate_receipt_additional_units": int(duplicate_receipt.get("additional_settled_credits", 0) or 0),
            "msk_authorized_requests": request_count if proof.get("requests_signed_with_msk") else 0,
            "invariant_violations": 0 if ok else 1,
        }

        report.update(
            {
                "ok": ok,
                "scenario_id": scope,
                "requester_account": str(handoff.get("requester_account") or f"{requester_account}-{scope}"),
                "requested_requester_account": f"{requester_account}-{scope}",
                "worker_id": worker_id,
                "multi_session_auth": handoff.get("multi_session_auth", {}),
                "worker_earning_ids": earning_ids,
                "claim_id": claim_id,
                "settlement_batch_id": batch_id,
                "settlement_proof_id": str(settled_batch.get("settlement_proof_id", "")),
                "settlement_proof_hash": str(settled_batch.get("settlement_proof_hash", "")),
                "proposal_id": proposal_id,
                "chain_tx_hash": chain_tx_hash,
                "handoff": handoff,
                "claim": claim,
                "batch": batch,
                "chain_execution": chain_execution,
                "recorded_receipt": settled,
                "duplicate_receipt": duplicate_receipt,
                "settlement_after": settlement_after,
                "metrics": metrics,
                "proof": proof,
            }
        )
        output_path = report_path if report_path.is_absolute() else repo_root / report_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        report["report_path"] = str(output_path)
        return report
    except Exception as exc:
        report["ok"] = False
        report["error"] = str(exc)
        try:
            output_path = report_path if report_path.is_absolute() else repo_root / report_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            report["report_path"] = str(output_path)
        except Exception:
            pass
        return report


def _render_verify_text(result: dict[str, Any]) -> str:
    proof = result.get("proof") or {}
    metrics = result.get("metrics") or {}
    lines = [
        f"Exp Hub owner-handoff lab: {'ok' if result.get('ok') else 'failed'}",
        f"Hub URLs: {', '.join(result.get('hub_urls') or [])}",
    ]
    if result.get("error"):
        lines.append(f"Error: {result['error']}")
    if result.get("scenario_id"):
        lines.append(f"Scenario: {result['scenario_id']}")
    if result.get("worker_id"):
        lines.append(f"Worker: {result['worker_id']}")
    if metrics:
        lines.extend(
            [
                "Traffic:",
                f"  requests attempted: {metrics.get('requests_attempted')}",
                f"  requests accepted: {metrics.get('requests_accepted')}",
                f"  remote handoffs: {metrics.get('remote_handoffs')}",
                f"  charged results: {metrics.get('charged_results')}",
                f"  MSK-authorized requests: {metrics.get('msk_authorized_requests')}",
                f"  invariant violations: {metrics.get('invariant_violations')}",
            ]
        )
    if proof:
        lines.extend(
            [
                "Proof:",
                f"  cluster has three Hubs: {'yes' if proof.get('cluster_has_three_hubs') else 'no'}",
                f"  multi-session auth required: {'yes' if proof.get('multi_session_auth_required') else 'no'}",
                f"  requester MSK issued: {'yes' if proof.get('requester_msk_issued') else 'no'}",
                f"  worker MSK issued: {'yes' if proof.get('worker_msk_issued') else 'no'}",
                f"  worker connected with MSK: {'yes' if proof.get('worker_connected_with_msk') else 'no'}",
                f"  requests signed with MSK: {'yes' if proof.get('requests_signed_with_msk') else 'no'}",
                f"  worker connected to owner Hub: {'yes' if proof.get('worker_connected_to_owner_hub') else 'no'}",
                f"  all requests accepted: {'yes' if proof.get('all_requests_accepted') else 'no'}",
                f"  remote entries handoff to owner: {'yes' if proof.get('remote_entries_handoff_to_owner') else 'no'}",
                f"  continuations point to owner Hub: {'yes' if proof.get('continuations_point_to_owner_hub') else 'no'}",
                f"  owner Hub charged exp ledger: {'yes' if proof.get('owner_hub_charged_exp_ledger') else 'no'}",
            ]
        )
    return "\n".join(lines)



def _render_full_e2e_text(result: dict[str, Any]) -> str:
    proof = result.get("proof") or {}
    metrics = result.get("metrics") or {}
    lines = [
        f"Exp Hub full E2E handoff + payout lab: {'ok' if result.get('ok') else 'failed'}",
        f"Hub URLs: {', '.join(result.get('hub_urls') or [])}",
    ]
    if result.get("error"):
        lines.append(f"Error: {result['error']}")
    if result.get("scenario_id"):
        lines.append(f"Scenario: {result['scenario_id']}")
    if result.get("worker_id"):
        lines.append(f"Worker: {result['worker_id']}")
    if result.get("chain_tx_hash"):
        lines.append(f"Dev-chain tx: {result['chain_tx_hash']}")
    if result.get("report_path"):
        lines.append(f"Report: {result['report_path']}")
    if metrics:
        lines.extend(
            [
                "Traffic:",
                f"  requests attempted: {metrics.get('requests_attempted')}",
                f"  requests accepted: {metrics.get('requests_accepted')}",
                f"  remote handoffs: {metrics.get('remote_handoffs')}",
                f"  charged results: {metrics.get('charged_results')}",
                f"  MSK-authorized requests: {metrics.get('msk_authorized_requests')}",
                "Ledger:",
                f"  worker earnings created: {metrics.get('worker_earnings_created')}",
                f"  worker claims created: {metrics.get('worker_claims_created')}",
                f"  settlement batches created: {metrics.get('settlement_batches_created')}",
                "Dev-chain:",
                f"  chain payouts executed: {metrics.get('chain_payouts_executed')}",
                f"  rounded payout units: {metrics.get('rounded_payout_units')}",
                f"  bridge retained units: {metrics.get('bridge_retained_units')}",
                f"  duplicate receipt additional units: {metrics.get('duplicate_receipt_additional_units')}",
                f"  invariant violations: {metrics.get('invariant_violations')}",
            ]
        )
    if proof:
        lines.extend(
            [
                "Proof:",
                f"  cluster has three Hubs: {'yes' if proof.get('cluster_has_three_hubs') else 'no'}",
                f"  multi-session auth required: {'yes' if proof.get('multi_session_auth_required') else 'no'}",
                f"  requester MSK issued: {'yes' if proof.get('requester_msk_issued') else 'no'}",
                f"  worker MSK issued: {'yes' if proof.get('worker_msk_issued') else 'no'}",
                f"  worker connected with MSK: {'yes' if proof.get('worker_connected_with_msk') else 'no'}",
                f"  requests signed with MSK: {'yes' if proof.get('requests_signed_with_msk') else 'no'}",
                f"  remote entries handoff to owner: {'yes' if proof.get('remote_entries_handoff_to_owner') else 'no'}",
                f"  continuations point to owner Hub: {'yes' if proof.get('continuations_point_to_owner_hub') else 'no'}",
                f"  owner Hub charged exp ledger: {'yes' if proof.get('owner_hub_charged_exp_ledger') else 'no'}",
                f"  worker earnings created: {'yes' if proof.get('worker_earnings_created') else 'no'}",
                f"  worker payout claim created: {'yes' if proof.get('worker_claim_created') else 'no'}",
                f"  settlement batch created: {'yes' if proof.get('settlement_batch_created') else 'no'}",
                f"  dev-chain payout executed: {'yes' if proof.get('dev_chain_payout_executed') else 'no'}",
                f"  Hub recorded chain receipt: {'yes' if proof.get('hub_recorded_chain_receipt') else 'no'}",
                f"  duplicate receipt idempotent: {'yes' if proof.get('duplicate_receipt_idempotent') else 'no'}",
                f"  exact high-precision receipt rejected: {'yes' if proof.get('exact_high_precision_receipt_rejected') else 'no'}",
            ]
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.verify_full_e2e:
            result = verify_full_e2e(
                hub_urls=args.hub_urls,
                requester_account=args.requester_account,
                funding_credits=args.funding_credits,
                stress_requests=args.stress_requests,
                timeout=args.timeout,
                worker_credits=args.worker_credits,
                precision_places=args.precision_places,
                dev_chain_state=args.dev_chain_state,
                rpc_url=args.rpc_url,
                chain_id=args.chain_id,
                contract_address=args.contract_address,
                worker_payout_address=args.worker_payout_address,
                captain_address=args.captain_address,
                beta_second_address=args.beta_second_address,
                fund_units=args.fund_units,
                expires_blocks=args.expires_blocks,
                mine_extra_blocks=args.mine_extra_blocks,
                poll_s=args.poll_s,
                report_path=args.report_path,
                require_multisession_auth=(False if args.allow_optional_multisession_auth else True),
            )
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(_render_full_e2e_text(result))
            return 0 if result.get("ok") else 1

        if args.verify_handoff:
            result = verify_handoff(
                hub_urls=args.hub_urls,
                requester_account=args.requester_account,
                funding_credits=args.funding_credits,
                stress_requests=args.stress_requests,
                timeout=args.timeout,
                require_multisession_auth=args.require_multisession_auth,
            )
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(_render_verify_text(result))
            return 0 if result.get("ok") else 1

        result = check_cluster(hub_urls=args.hub_urls, timeout=args.timeout, require_multisession_auth=args.require_multisession_auth)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(_render_check_text(result))
        return 0 if result.get("ok") else 1
    except (ExpHubLabError, OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Exp Hub lab failed: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
