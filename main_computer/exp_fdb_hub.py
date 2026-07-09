from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from main_computer.container_runtime import command_display as _container_command_display, legacy_docker_command_override, resolve_container_runtime
from main_computer.config import DEFAULT_HUB_BRIDGE_BACKEND, MainComputerConfig
from main_computer.credit_units import (
    CREDIT_WEI_PER_CREDIT,
    credit_decimal_text_to_wei,
    credit_wei_to_decimal_text,
    credit_wei_to_whole_credits_floor,
)
from main_computer.contract_config import contract_config_path
from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbConfig, ExperimentalFoundationDbCreditLedger
from main_computer.exp_fdb_hub_state import (
    ExperimentalFoundationDbEnergyCreditLedger,
    ExperimentalFoundationDbFeedbackStateStore,
    ExperimentalFoundationDbHubState,
    ExperimentalFoundationDbMultiSessionKeyStore,
    ExperimentalFoundationDbQuoteStateStore,
    ExperimentalFoundationDbRegistry,
    ExperimentalFoundationDbRequestStateStore,
    ExperimentalFoundationDbSecureSessionStore,
)
from main_computer.hub import (
    DEFAULT_HUB_PORT,
    HUB_SECURITY_PROFILE,
    HubCreditAuthorizationError,
    HubDispatcher,
    HubHttpServer,
    HubServerHandler,
)
from main_computer.stable_hub import LiveWorkerSession, stable_hub_contract
from main_computer.hub_plex_models import HubAIRequest, HubRequestStatus, chat_response_from_payload
from main_computer.hub_plex_service import idempotent_request_id, stable_request_id
from main_computer.runtime_env_file import apply_runtime_env_file
from main_computer.stable_hub_topology import (
    StableHubNode,
    StableHubTopology,
    load_stable_hub_topology,
    stable_hub_node_to_dict,
    stable_hub_topology_to_dict,
)
from main_computer.stable_hub_worker_sessions import (
    FoundationDbStableWorkerSessionStore,
    StableHubAcceptedWorkSessionDirectory,
    StableHubWorkerMarketDirectory,
    StableHubWorkerSessionDirectory,
    StableHubWorkerSessionError,
    new_connection_id,
    new_run_id,
    new_session_id,
    normalize_request_id,
    normalize_request_market_constraints,
    normalize_session_id,
    normalize_worker_market_profile,
    stable_partition_key_for_work,
    stable_task_queue_for_partition,
)
from main_computer.hub_credit_bridge_completion import HubCreditBridgeCompletionService
from main_computer.hub_credit_indexer import HubCreditIndexer


DEFAULT_EXP_FDB_HUB_PORT = DEFAULT_HUB_PORT + 100
DEFAULT_EXP_LIVE_SESSION_LOCAL_AI_TIMEOUT_SECONDS = 300.0
DEFAULT_EXP_FDB_NAMESPACE = "main-computer-exp-fdb"
DEFAULT_EXP_FDB_CLUSTER_FILE = Path(".foundationdb") / "docker.cluster"
DEFAULT_EXP_FDB_HUB_ROOT = Path("runtime") / "exp-fdb-hub"
DEFAULT_EXP_FDB_DOCKER_OUTPUT_DIR = Path("runtime") / "scheduler-lab" / "exp-fdb"
DEFAULT_EXP_FDB_DOCKER_COMPOSE_FILE = Path("deploy") / "scheduler-lab" / "docker-compose.worker-lab.yml"
DEFAULT_EXP_FDB_DOCKER_HUB_HOST = "host.docker.internal"
DEFAULT_EXP_FDB_DOCKER_CONTAINER_OUTPUT_DIR = "/lab-output"
DEFAULT_EXP_FDB_SMOKE_SCRIPT = Path("scripts") / "smoke_foundationdb_credit_ledger_primitives.py"
DEFAULT_EXP_FDB_SMOKE_CONTAINER_NAME = "main-computer-foundationdb-smoke"
DEFAULT_EXP_FDB_SMOKE_DOCKER_IMAGE = "foundationdb/foundationdb:7.4.6"
DEFAULT_EXP_FDB_SMOKE_PORT = 4550
DEFAULT_EXP_FDB_SMOKE_NAMESPACE = "main-computer-exp-fdb-autostart-smoke"
DEFAULT_EXP_FDB_SMOKE_START_TIMEOUT_SECONDS = 45.0


def _exp_short_response_summary(response: Any) -> str:
    text = " ".join(str(getattr(response, "content", "") or "").split())
    if len(text) > 240:
        return text[:237] + "..."
    return text



def experimental_stable_hub_contract() -> dict[str, str]:
    """Return the stable live-worker connection contract exposed by the exp Hub."""

    contract = dict(stable_hub_contract())
    contract.update(
        {
            "service": "main_computer.exp_fdb_hub",
            "worker_live_session_transport": "websocket",
            "worker_liveness": "hub-ping-worker-pong",
            "hub_to_hub_handoff": "owner-hub-forwarding-v1",
            "owner_hub_scope": "topology-owner-hub",
            "requester_connection": "stable-live-session-work-requests-with-continuation-bounce",
            "routing": "entry-hub-forwards-remote-worker-requests-to-owner-hub",
        }
    )
    return contract


def _exp_fdb_stable_hub_id(port: int) -> str:
    return f"exp-fdb-hub-{int(port)}"


def _exp_fdb_stable_namespace(namespace: object) -> str:
    base = str(namespace or DEFAULT_EXP_FDB_NAMESPACE).strip() or DEFAULT_EXP_FDB_NAMESPACE
    return f"{base}-stable-live-sessions"


def _exp_fdb_cluster_id(namespace: object) -> str:
    base = str(namespace or DEFAULT_EXP_FDB_NAMESPACE).strip() or DEFAULT_EXP_FDB_NAMESPACE
    clean = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in base).strip("-_.")
    return f"{clean or 'exp-fdb'}-stable-cluster"


def _exp_fdb_url_for_port(args: argparse.Namespace, port: int) -> str:
    explicit = str(getattr(args, "hub_url", "") or "").strip().rstrip("/")
    if explicit:
        parsed = urlparse(explicit if "://" in explicit else f"http://{explicit}")
        if parsed.scheme and parsed.hostname:
            return f"{parsed.scheme}://{parsed.hostname}:{int(port)}"
    return f"http://{getattr(args, 'host', '127.0.0.1')}:{int(port)}"


def _exp_fdb_topology_ports_from_args(args: argparse.Namespace, *, current_port: int) -> list[int]:
    """Return the concrete exp Hub ports that should share one topology.

    ``exp-fdb-hub.py -ports 8870,8871,8872`` starts all three Hub servers in one
    process.  Every server must advertise the same topology so remote worker
    owner records are routable instead of looking like single-local-Hub state.
    Direct unit tests and ad-hoc server construction still fall back to a
    single-Hub topology.
    """

    raw_ports = getattr(args, "ports", None)
    if raw_ports is None or raw_ports == "":
        raw_ports = getattr(args, "port", None)
    ports = parse_ports(raw_ports, default=int(current_port))
    current = int(current_port)
    if current not in ports:
        ports.append(current)
    return ports


def _exp_fdb_network_key_from_args(args: argparse.Namespace, *, bridge_backend: str) -> str:
    explicit = str(getattr(args, "network_key", "") or "").strip()
    if explicit:
        return explicit
    if bridge_backend not in {"mock", "mock-chain", "mock-chain-lite"}:
        # The manual exp Hub may use an isolated FDB namespace, but the local
        # dev-chain deployment and contract config are published under the dev
        # network key.  Defaulting contract-backed startup to "dev" makes:
        #
        #   python exp-fdb-hub.py --namespace my-lab --bridge-backend dev-chain -ports 8870,8871,8872
        #
        # use runtime/deployments/dev/latest.json instead of incorrectly looking
        # for runtime/deployments/exp-fdb/latest.json.
        return "dev"
    return DEFAULT_EXP_FDB_NAMESPACE


def _is_exp_dev_chain_backend(bridge_backend: str) -> bool:
    return str(bridge_backend or "").strip().lower() in {
        "dev",
        "dev-chain",
        "devchain",
        "contract",
        "contract-chain",
        "credit-bridge-contract",
        "evm-contract",
        "real-chain",
    }


def _deployment_manifest_is_smoke_bridge(path: Path | None) -> bool:
    if path is None or not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    return isinstance(payload.get("smoke_client"), dict)


def _exp_fdb_topology_path_from_args(args: argparse.Namespace) -> Path | None:
    raw = getattr(args, "topology", None)
    if raw is None or str(raw).strip() == "":
        return None
    return Path(raw)


def _exp_fdb_hub_id_for_topology(
    args: argparse.Namespace,
    topology: StableHubTopology,
    *,
    port: int,
) -> str:
    explicit = str(getattr(args, "hub_id", "") or "").strip()
    if explicit:
        topology.hub_by_id(explicit)
        return explicit
    current_port = int(port)
    for hub in topology.hubs:
        try:
            parsed = urlparse(hub.hub_url)
            if int(parsed.port or 0) == current_port:
                return hub.hub_id
        except (TypeError, ValueError):
            continue
    return topology.hubs[0].hub_id


def _exp_fdb_topology_with_current_hub_url(
    args: argparse.Namespace,
    topology: StableHubTopology,
    *,
    hub_id: str,
    port: int,
) -> StableHubTopology:
    """Return a topology whose selected Hub advertises the actual bind URL.

    This lets the start.bat path run a single exp/FDB Hub against the dev
    topology contract even when MAIN_COMPUTER_HUB_PORT overrides the concrete
    port from the checked-in topology file.
    """

    current_url = _exp_fdb_url_for_port(args, int(port))
    old_url = ""
    hubs: list[StableHubNode] = []
    for hub in topology.hubs:
        if hub.hub_id == hub_id:
            old_url = hub.hub_url
            hubs.append(
                StableHubNode(
                    hub_id=hub.hub_id,
                    hub_url=current_url,
                    public_url=current_url,
                    roles=hub.roles,
                )
            )
        else:
            hubs.append(hub)

    entry_urls = tuple(current_url if url == old_url else url for url in topology.entry_urls)
    return replace(topology, entry_urls=entry_urls, hubs=tuple(hubs))


def build_experimental_stable_topology(
    args: argparse.Namespace,
    *,
    config: MainComputerConfig,
    fdb_config: ExperimentalFoundationDbConfig,
    port: int,
) -> StableHubTopology:
    """Build the stable-Hub-compatible topology advertised by exp Hubs.

    A single-port manual startup remains a one-Hub topology.  A multi-port manual
    startup such as ``-ports 8870,8871,8872`` now advertises all three concrete
    exp Hubs from every server, which is required for owner-Hub forwarding and
    the dev-topology stress lab.
    """

    topology_path = _exp_fdb_topology_path_from_args(args)
    if topology_path is not None:
        topology = load_stable_hub_topology(topology_path)
        hub_id = _exp_fdb_hub_id_for_topology(args, topology, port=int(port))
        return _exp_fdb_topology_with_current_hub_url(args, topology, hub_id=hub_id, port=int(port))

    ports = _exp_fdb_topology_ports_from_args(args, current_port=int(port))
    hubs = tuple(
        StableHubNode(
            hub_id=_exp_fdb_stable_hub_id(int(item)),
            hub_url=_exp_fdb_url_for_port(args, int(item)),
            public_url=_exp_fdb_url_for_port(args, int(item)),
            roles=("entry", "worker-owner", "requester", "execution"),
        )
        for item in ports
    )
    return StableHubTopology(
        kind="main_computer.stable_hub_topology.v1",
        cluster_id=_exp_fdb_cluster_id(fdb_config.namespace),
        network={
            "network_key": config.hub_network,
            "display_name": config.hub_network_display_name,
            "kind": config.hub_network_kind,
            "chain_id": config.chain_id,
            "chain_rpc_url": config.chain_rpc_url,
        },
        storage={
            "backend": "foundationdb",
            "cluster_file": str(fdb_config.cluster_file),
            "namespace": _exp_fdb_stable_namespace(fdb_config.namespace),
            "api_version": int(fdb_config.api_version),
        },
        entry_urls=tuple(hub.hub_url for hub in hubs),
        hubs=hubs,
    )


def build_experimental_hub_identity(topology: StableHubTopology, hub_id: str) -> dict[str, Any]:
    current = topology.hub_by_id(hub_id)
    return {
        "ok": True,
        "service": "main_computer.exp_fdb_hub",
        "hub": stable_hub_node_to_dict(current),
        "hub_id": current.hub_id,
        "hub_url": current.hub_url,
        "cluster_id": topology.cluster_id,
        "network": dict(topology.network),
        "storage": dict(topology.storage),
        "entry_urls": list(topology.entry_urls),
        "peer_hubs": [
            stable_hub_node_to_dict(hub)
            for hub in topology.hubs
            if hub.hub_id != current.hub_id
        ],
        "contract": experimental_stable_hub_contract(),
    }


def _optional_int_arg(value: object, *, flag: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(text, 0)
    except ValueError:
        raise SystemExit(f"{flag} must be an integer, got {text!r}") from None
    if parsed < 0:
        raise SystemExit(f"{flag} must be non-negative")
    return parsed


def exp_work_session_stream_path(session_id: str) -> str:
    return f"/api/hub/v1/work/sessions/{normalize_session_id(session_id)}/stream"


def exp_work_session_continuation_url(hub_url: str, session_id: str) -> str:
    base = str(hub_url or "").rstrip("/")
    if not base:
        raise StableHubWorkerSessionError("hub_url is required for exp work session continuation.")
    return base + exp_work_session_stream_path(session_id)


_EXP_PUBLIC_PRIVATE_FIELD_NAMES = {
    "worker_id",
    "worker_node_id",
    "requested_worker_node_id",
    "selected_worker_node_id",
    "worker_instance_id",
    "selected_worker_instance_id",
    "connection_id",
    "worker_connection_id",
    "worker_wallet_address",
    "worker_account_id",
    "worker_msk_id",
    "account_id",
    "wallet_address",
    "requester_account_id",
    "requester_wallet_address",
    "multisession_key_id",
    "msk_id",
    "credit_wallet",
    "payout_wallet_address",
    "selected_worker",
    "accepted_session",
    "log_file",
    "raw_stream_events",
}


def _exp_public_redacted(value: Any) -> Any:
    """Return requester/worker safe payload data with worker privacy fields removed.

    The exp live-session golden path routes by Hub-owned live sockets.  Wallet
    addresses and connection ids are internal authorization/settlement/runtime
    facts, not public worker identities.
    """

    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in _EXP_PUBLIC_PRIVATE_FIELD_NAMES:
                continue
            if key_text == "receipt" and isinstance(item, dict):
                clean[key_text] = _exp_public_redacted(item)
                continue
            if key_text == "selected_offer" and isinstance(item, dict):
                # Keep price/settlement information but remove worker/socket handles.
                clean[key_text] = _exp_public_redacted(item)
                continue
            clean[key_text] = _exp_public_redacted(item)
        return clean
    if isinstance(value, list):
        return [_exp_public_redacted(item) for item in value]
    return value


def _exp_public_stream_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return a requester-visible session stream event.

    Session stream events are allowed to expose session/request/run ids and token
    deltas.  They must not expose worker wallets, socket ids, local log paths, or
    multisession keys.
    """

    clean = _exp_public_redacted(event)
    if not isinstance(clean, dict):
        return {"type": "event", "data": clean}
    clean.setdefault("type", "event")
    return clean


def _exp_internal_settlement_route_key(authorization: dict[str, Any], *, connection_id: str = "") -> str:
    """Return an internal-only settlement key for the live connection.

    Request routing is no longer keyed by a caller supplied worker id.  The only
    real runtime handle is the Hub-owned websocket connection.  Credit settlement
    still needs a stable internal bucket, so derive an opaque key from the
    authenticated wallet when present and fall back to this connection generation.
    This value must never be emitted by requester endpoints.
    """

    material = str(authorization.get("wallet_address") or authorization.get("account_id") or connection_id or "").strip().lower()
    if not material:
        raise StableHubWorkerSessionError("worker wallet authorization or Hub connection id is required for live-session settlement.")
    return "live_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _exp_legacy_worker_route(path: str) -> bool:
    legacy_paths = {
        "/api/hub/workers/register",
        "/api/hub/v1/workers/register",
        "/api/hub/workers/heartbeat",
        "/api/hub/v1/workers/heartbeat",
        "/api/hub/workers/poll",
        "/api/hub/v1/workers/poll",
        "/api/hub/workers/results",
        "/api/hub/v1/workers/results",
    }
    if path in legacy_paths:
        return True
    return path.startswith("/api/hub/v1/workers/") and path.endswith("/heartbeat")


class ExperimentalFoundationDbHubServerHandler(HubServerHandler):
    """Hub request handler with opt-in exp multi-hub access logging.

    Scheduler-lab Docker runs can generate thousands of expected 503 worker
    route responses while probing overload behavior.  Keep those request access
    logs off by default so stderr remains readable; set EXP_FDB_HUB_ACCESS_LOGS=1
    when raw per-request HTTP logs are needed.
    """

    def _exp_fdb_access_log_enabled(self) -> bool:
        raw = str(os.environ.get("EXP_FDB_HUB_ACCESS_LOGS", "0")).strip().lower()
        return raw in {"1", "true", "yes", "on", "all"}

    def log_message(self, format: str, *args: object) -> None:
        if not getattr(self.server, "verbose", False):
            return
        if not self._exp_fdb_access_log_enabled():
            return
        try:
            message = format % args
        except Exception:
            message = f"{format} {args!r}"
        port = getattr(self.server, "server_port", "?")
        client = self.client_address[0] if self.client_address else "-"
        print(
            f"[exp-fdb-hub:{port}] {client} - - [{self.log_date_time_string()}] {message}",
            file=sys.stderr,
            flush=True,
        )

    def _ws_send_frame(self, opcode: int, payload: bytes = b"") -> None:
        length = len(payload)
        if length < 126:
            header = bytes([0x80 | opcode, length])
        elif length < 65536:
            header = bytes([0x80 | opcode, 126]) + length.to_bytes(2, "big")
        else:
            header = bytes([0x80 | opcode, 127]) + length.to_bytes(8, "big")
        self.wfile.write(header + payload)
        self.wfile.flush()

    def _ws_send_json(self, payload: dict[str, Any]) -> None:
        self._ws_send_frame(0x1, json.dumps(payload, sort_keys=True).encode("utf-8"))

    def _ws_read_frame(self) -> tuple[int, bytes]:
        header = self.rfile.read(2)
        if len(header) < 2:
            raise ConnectionError("websocket closed")
        first, second = header[0], header[1]
        opcode = first & 0x0F
        length = second & 0x7F
        masked = bool(second & 0x80)
        if length == 126:
            raw = self.rfile.read(2)
            if len(raw) < 2:
                raise ConnectionError("websocket closed during frame length")
            length = int.from_bytes(raw, "big")
        elif length == 127:
            raw = self.rfile.read(8)
            if len(raw) < 8:
                raise ConnectionError("websocket closed during frame length")
            length = int.from_bytes(raw, "big")
        mask = self.rfile.read(4) if masked else b""
        if masked and len(mask) < 4:
            raise ConnectionError("websocket closed during frame mask")
        payload = self.rfile.read(length) if length else b""
        if len(payload) < length:
            raise ConnectionError("websocket closed during frame payload")
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _ws_read_json_message(self) -> dict[str, Any]:
        while True:
            opcode, payload = self._ws_read_frame()
            if opcode == 0x8:
                raise ConnectionError("websocket close frame received")
            if opcode == 0x9:
                self._ws_send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode != 0x1:
                raise StableHubWorkerSessionError(f"unsupported websocket opcode: {opcode}")
            try:
                decoded = json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise StableHubWorkerSessionError(f"websocket text frame is not JSON: {exc}") from exc
            if not isinstance(decoded, dict):
                raise StableHubWorkerSessionError("websocket JSON message must be an object")
            return decoded

    def _accept_websocket(self) -> bool:
        if self.headers.get("Upgrade", "").lower() != "websocket":
            self._send_json(
                {"ok": False, "error": "websocket_upgrade_required", "hub_id": self.server.stable_hub_node.hub_id},
                status=HTTPStatus.UPGRADE_REQUIRED,
            )
            return False
        key = self.headers.get("Sec-WebSocket-Key", "").strip()
        if not key:
            self._send_json(
                {"ok": False, "error": "missing_sec_websocket_key", "hub_id": self.server.stable_hub_node.hub_id},
                status=HTTPStatus.BAD_REQUEST,
            )
            return False
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True
        return True

    def _market_profile_from_auth(self, message: dict[str, Any]) -> dict[str, Any]:
        raw_market = message.get("market")
        if not isinstance(raw_market, dict):
            raw_market = message.get("worker_market")
        market_source = dict(raw_market) if isinstance(raw_market, dict) else {}
        if "models" not in market_source and isinstance(message.get("models"), list):
            market_source["models"] = list(message.get("models") or [])
        if "model" not in market_source and message.get("model"):
            market_source["model"] = str(message.get("model") or "")
        market = normalize_worker_market_profile(market_source)
        return market


    def _exp_request_store(self) -> Any:
        store = getattr(self.server, "request_store", None)
        if store is not None:
            return store
        return self.server.dispatcher.plex_service.request_store

    def _exp_worker_instance_id_for_connection(self, worker_id: str, connection_id: str) -> str:
        # The live-session connection is the concrete worker execution slot.
        # Keeping the exp worker_instance_id equal to connection_id avoids a
        # second identity namespace and makes worker.work.result bindings match
        # the accepted live socket.
        return str(connection_id or worker_id)

    def _exp_request_body_for_live_session(
        self,
        body: dict[str, Any],
        *,
        selected_worker: dict[str, Any],
    ) -> dict[str, Any]:
        payload = dict(body)
        metadata = dict(payload.get("metadata", {})) if isinstance(payload.get("metadata"), dict) else {}
        metadata.setdefault("execution_mode", "exp-live-session-direct-v1")
        metadata.setdefault("worker_connection_mode", "stable-websocket-live-session")
        selected_price = dict(selected_worker.get("price") or {})
        selected_credit_wei = credit_decimal_text_to_wei(str(selected_price.get("amount") or "1"), default="1", minimum_wei=1)
        metadata.setdefault("selected_offer", {
            "credits_per_request": max(1, (selected_credit_wei + CREDIT_WEI_PER_CREDIT - 1) // CREDIT_WEI_PER_CREDIT),
            "credits_per_request_wei": str(selected_credit_wei),
            "credits_per_request_display": credit_wei_to_decimal_text(selected_credit_wei),
            "unit": str(selected_price.get("unit") or "compute_credit"),
            "execution_mode": "exp-live-session-direct-v1",
            "price_source": "exp_live_worker_market",
            "legacy_worker_pull_lease": False,
        })
        if payload.get("request_id") and not payload.get("idempotency_key"):
            payload["idempotency_key"] = str(payload.get("request_id"))
        if not payload.get("messages"):
            input_payload = payload.get("input") if isinstance(payload.get("input"), dict) else {}
            prompt = (
                payload.get("prompt")
                or input_payload.get("prompt")
                or input_payload.get("text")
                or input_payload.get("message")
                or "exp live-session work request"
            )
            payload["messages"] = [{"role": "user", "content": str(prompt)}]
        requested_model = str(payload.get("model") or "").strip()
        if requested_model:
            metadata.setdefault("requested_model", requested_model)
        else:
            payload["model"] = "live-session-worker"
        max_credits = payload.get("max_credits", payload.get("max_price_credits"))
        if max_credits is None:
            max_price = payload.get("max_price") if isinstance(payload.get("max_price"), dict) else {}
            max_credits = max_price.get("amount", 1) if isinstance(max_price, dict) else 1
        try:
            payload["max_credits"] = max(1, int(float(max_credits or 1)))
        except (TypeError, ValueError):
            payload["max_credits"] = 1
        payload["metadata"] = metadata
        payload, metadata = self._apply_request_multisession_authorization(
            body=payload,
            metadata=metadata,
            required=bool(getattr(self.server.config, "hub_require_multisession_auth", False)),
        )
        return payload

    def _exp_live_session_selected_credit_wei(self, selected_worker: dict[str, Any]) -> int:
        price = dict(selected_worker.get("price") or {})
        return credit_decimal_text_to_wei(str(price.get("amount") or "1"), default="1", minimum_wei=1)

    def _exp_live_session_selected_credit_units(self, selected_worker: dict[str, Any]) -> int:
        credit_wei = self._exp_live_session_selected_credit_wei(selected_worker)
        return max(1, (credit_wei + CREDIT_WEI_PER_CREDIT - 1) // CREDIT_WEI_PER_CREDIT)

    def _exp_live_session_local_ai_timeout_seconds(self, *, body: dict[str, Any], metadata: dict[str, Any]) -> float:
        input_payload = dict(body.get("input") or {}) if isinstance(body.get("input"), dict) else {}
        execution_limits = dict(body.get("execution_limits") or {}) if isinstance(body.get("execution_limits"), dict) else {}
        candidates = [
            body.get("local_ai_timeout_seconds"),
            body.get("worker_local_ai_timeout_seconds"),
            body.get("worker_timeout_seconds"),
            body.get("work_timeout_seconds"),
            body.get("timeout_seconds"),
            body.get("timeout"),
            body.get("max_runtime_seconds"),
            input_payload.get("local_ai_timeout_seconds"),
            input_payload.get("worker_local_ai_timeout_seconds"),
            input_payload.get("worker_timeout_seconds"),
            input_payload.get("work_timeout_seconds"),
            input_payload.get("timeout_seconds"),
            input_payload.get("timeout"),
            input_payload.get("max_runtime_seconds"),
            execution_limits.get("local_ai_timeout_seconds"),
            execution_limits.get("worker_local_ai_timeout_seconds"),
            execution_limits.get("worker_timeout_seconds"),
            execution_limits.get("work_timeout_seconds"),
            execution_limits.get("timeout_seconds"),
            execution_limits.get("timeout"),
            execution_limits.get("max_runtime_seconds"),
            metadata.get("local_ai_timeout_seconds"),
            metadata.get("worker_local_ai_timeout_seconds"),
            metadata.get("worker_timeout_seconds"),
            metadata.get("work_timeout_seconds"),
            metadata.get("timeout_seconds"),
            metadata.get("timeout"),
            metadata.get("max_runtime_seconds"),
            os.environ.get("MAIN_COMPUTER_WORKER_LIVE_SESSION_LOCAL_AI_TIMEOUT_SECONDS"),
        ]
        for value in candidates:
            raw = str(value or "").strip()
            if not raw:
                continue
            try:
                parsed = float(raw)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return max(1.0, min(parsed, 3600.0))
        return DEFAULT_EXP_LIVE_SESSION_LOCAL_AI_TIMEOUT_SECONDS

    def _exp_create_live_session_request_record(
        self,
        *,
        request: HubAIRequest,
        request_payload: dict[str, Any],
        worker_id: str,
        worker_instance_id: str,
        selected_worker: dict[str, Any],
    ) -> Any:
        existing = self.server.dispatcher.plex_service._idempotent_record(request)
        if existing is not None:
            return existing
        normalized = request.as_payload()
        request_id = (
            idempotent_request_id(request.client_node_id, request.idempotency_key)
            if request.idempotency_key
            else stable_request_id(normalized)
        )
        record = self.server.dispatcher.plex_service._create_record(
            request_id=request_id,
            request=request,
            security_mode="exp-live-session-direct-v1",
            hub_blind=False,
            initial_state="dispatching",
            initial_event_type="exp_live_session.selected",
        )
        self.server.dispatcher.plex_service._prepare_paid_request_spend(record, request)
        return self._exp_request_store().update(
            request_id,
            state="dispatching",
            selected_worker_node_id=worker_id,
            selected_worker_instance_id=worker_instance_id,
            credits_queued=self._exp_live_session_selected_credit_units(dict(selected_worker)),
            event_type="exp_live_session.worker_selected",
            event={
                "worker_node_id": worker_id,
                "worker_instance_id": worker_instance_id,
                "direct_live_session": True,
                "legacy_worker_pull_lease": False,
            },
        )

    def _exp_spend_live_session_credit(
        self,
        *,
        request_id: str,
        worker_id: str,
        selected_worker: dict[str, Any],
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self._exp_request_store().get(request_id)
        if record is None:
            raise StableHubWorkerSessionError("live-session request record does not exist.")
        if getattr(record, "charge_id", ""):
            return dict(getattr(record, "receipt", {}) or {})
        credit_wei = self._exp_live_session_selected_credit_wei(selected_worker)
        credit_units = self._exp_live_session_selected_credit_units(selected_worker)
        receipt = self.server.dispatcher.plex_service._spend_paid_request_credit(
            record=record,
            worker_node_id=worker_id,
            worker_credits=credit_units,
            worker_credit_wei=credit_wei,
        )
        return dict(receipt or {})

    def _exp_mark_live_session_request_running(
        self,
        *,
        request_id: str,
        session_id: str,
        run_id: str,
        worker_id: str,
        worker_instance_id: str,
        worker_acceptance: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self._exp_request_store().get(request_id)
        if existing is not None and str(existing.state or "") in {"completed", "failed", "cancelled", "expired"}:
            return HubRequestStatus.from_record(
                existing,
                polling_url=f"/api/hub/v1/requests/{request_id}",
            ).as_dict()
        record = self._exp_request_store().update(
            request_id,
            state="running",
            session_id=session_id,
            selected_worker_node_id=worker_id,
            selected_worker_instance_id=worker_instance_id,
            event_type="exp_live_session.accepted",
            event={
                "session_id": session_id,
                "run_id": run_id,
                "worker_id": worker_id,
                "connection_id": worker_instance_id,
                "direct_live_session": True,
                "legacy_worker_pull_lease": False,
                "worker_acceptance": dict(worker_acceptance),
            },
        )
        return HubRequestStatus.from_record(record, polling_url=f"/api/hub/v1/requests/{request_id}").as_dict()

    def _exp_complete_live_session_request(
        self,
        *,
        request_id: str,
        worker_id: str,
        worker_instance_id: str,
        result_payload: dict[str, Any],
        receipt: dict[str, Any],
        selected_worker: dict[str, Any],
    ) -> dict[str, Any]:
        record = self._exp_request_store().get(request_id)
        if record is None:
            raise StableHubWorkerSessionError("live-session request record does not exist.")
        response_payload = result_payload.get("response") if isinstance(result_payload.get("response"), dict) else result_payload
        if not isinstance(response_payload, dict):
            response_payload = {"content": str(response_payload or "")}
        response = chat_response_from_payload(
            response_payload,
            default_provider="exp-live-worker",
            default_model=str(record.model or "live-session-worker"),
        )
        credit_wei = self._exp_live_session_selected_credit_wei(selected_worker)
        credit_units = self._exp_live_session_selected_credit_units(selected_worker)
        metadata = dict(response.metadata)
        metadata["hub"] = {
            "request_id": request_id,
            "credits_queued": credit_units,
            "credit_wei": str(credit_wei),
            "credits_display": credit_wei_to_decimal_text(credit_wei),
            "settlement": "batched-worker-claim",
            "security_mode": "exp-live-session-direct-v1",
            "hub_blind": False,
            "exp_live_session": True,
            "direct_live_session": True,
            "legacy_worker_pull_lease": False,
            "payment": _exp_public_redacted(dict(receipt or {})),
        }
        selected_offer = (
            dict(record.request_payload.get("metadata", {}).get("selected_offer", {}))
            if isinstance(record.request_payload, dict)
            and isinstance(record.request_payload.get("metadata", {}), dict)
            and isinstance(record.request_payload.get("metadata", {}).get("selected_offer"), dict)
            else {}
        )
        if selected_offer:
            metadata["hub"]["selected_offer"] = selected_offer
        completed_record = self._exp_request_store().update(
            request_id,
            state="completed",
            selected_worker_node_id=worker_id,
            selected_worker_instance_id=worker_instance_id,
            response={
                "content": response.content,
                "provider": response.provider,
                "model": response.model,
                "metadata": metadata,
            },
            response_summary=_exp_short_response_summary(response),
            credits_queued=credit_units,
            charge_id=str(receipt.get("charge_id", "")) if receipt else "",
            charged_credits=max(0, int(receipt.get("charged_credits", 0) or 0)) if receipt else credit_wei_to_whole_credits_floor(credit_wei),
            released_credits=0,
            worker_earning_id=str(receipt.get("worker_earning_id", "")) if receipt else "",
            receipt=dict(receipt or {}),
            error="",
            terminal_reason="completed",
            event_type="request.completed",
            event={
                "worker_node_id": worker_id,
                "worker_instance_id": worker_instance_id,
                "exp_live_session": True,
                "direct_live_session": True,
                "legacy_worker_pull_lease": False,
            },
        )
        return HubRequestStatus.from_record(
            completed_record,
            polling_url=f"/api/hub/v1/requests/{request_id}",
        ).as_dict()

    def _exp_fail_live_session_request(
        self,
        *,
        request_id: str,
        worker_id: str,
        worker_instance_id: str,
        error: str,
        terminal_reason: str,
    ) -> dict[str, Any]:
        record = self._exp_request_store().update(
            request_id,
            state="failed",
            selected_worker_node_id=worker_id,
            selected_worker_instance_id=worker_instance_id,
            error=str(error or terminal_reason),
            terminal_reason=str(terminal_reason or "worker_failed"),
            event_type="request.failed",
            event={
                "worker_node_id": worker_id,
                "worker_instance_id": worker_instance_id,
                "error": str(error or terminal_reason),
                "exp_live_session": True,
                "direct_live_session": True,
                "legacy_worker_pull_lease": False,
            },
        )
        return HubRequestStatus.from_record(record, polling_url=f"/api/hub/v1/requests/{request_id}").as_dict()

    def _exp_fail_open_live_sessions_for_connection(
        self,
        *,
        worker_id: str,
        connection_id: str | None = None,
        exclude_connection_id: str | None = None,
        error: str,
        terminal_reason: str,
    ) -> list[dict[str, Any]]:
        """Make broken live-session handoffs terminal and release capacity.

        Direct live-session work is bound to one websocket generation.  If that
        socket closes or the worker reconnects before a terminal result, the
        requester must see a failed session instead of polling an accepted session
        forever.
        """

        failed: list[dict[str, Any]] = []
        try:
            open_sessions = self.server.exp_accepted_work_session_directory.list_open_sessions_for_worker_connection(
                worker_id=worker_id,
                connection_id=connection_id,
                exclude_connection_id=exclude_connection_id,
            )
        except Exception:
            return failed
        for accepted in open_sessions:
            session_id = normalize_session_id(accepted.get("session_id"))
            request_id = normalize_request_id(accepted.get("request_id"))
            accepted_connection_id = str(accepted.get("worker_connection_id") or connection_id or "")
            work = dict(accepted.get("work") or {})
            worker_instance_id = str(work.get("worker_instance_id") or accepted_connection_id)
            payout = dict(accepted.get("payout") or {})
            payout.update(
                {
                    "status": "failed",
                    "direct_spend": True,
                    "legacy_worker_pull_lease": False,
                    "release_reason": str(terminal_reason or "worker_connection_lost"),
                }
            )
            try:
                request_status = self._exp_fail_live_session_request(
                    request_id=request_id,
                    worker_id=worker_id,
                    worker_instance_id=worker_instance_id,
                    error=error,
                    terminal_reason=terminal_reason,
                )
            except Exception:
                request_status = {}
            try:
                updated = self.server.exp_accepted_work_session_directory.record_failed(
                    session_id=session_id,
                    worker_connection_id=accepted_connection_id,
                    worker_failure={
                        "type": "worker.connection.closed",
                        "error": error,
                        "terminal_reason": terminal_reason,
                        "request": request_status,
                    },
                    payout=payout,
                )
                failed.append(updated)
            except Exception:
                continue
        return failed

    def _exp_reconcile_open_live_session(self, accepted: dict[str, Any]) -> dict[str, Any]:
        """Fail accepted/running direct live sessions whose websocket is no longer current."""

        if str(accepted.get("status") or "") not in {"accepted", "running"}:
            return accepted
        work = dict(accepted.get("work") or {})
        if work.get("direct_live_session") is not True:
            return accepted
        worker_id = str(accepted.get("worker_id") or "")
        connection_id = str(accepted.get("worker_connection_id") or "")
        if not worker_id or not connection_id:
            return accepted
        live_session = self.server.get_live_worker_session(connection_id)
        if live_session is not None and live_session.is_live:
            return accepted
        reason = "worker_connection_lost"
        failed = self._exp_fail_open_live_sessions_for_connection(
            worker_id=worker_id,
            connection_id=connection_id,
            error=f"worker live-session connection {connection_id} is no longer available",
            terminal_reason=reason,
        )
        for item in failed:
            if str(item.get("session_id") or "") == str(accepted.get("session_id") or ""):
                return item
        return self.server.exp_accepted_work_session_directory.get_session(str(accepted.get("session_id") or "")) or accepted

    def _exp_live_session_response(
        self,
        *,
        accepted_session: dict[str, Any],
        request_status: dict[str, Any] | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        session_id = normalize_session_id(accepted_session.get("session_id"))
        continuation_url = exp_work_session_continuation_url(self.server.stable_hub_node.hub_url, session_id)
        execution_hub = {
            "hub_id": self.server.stable_hub_node.hub_id,
            "hub_url": self.server.stable_hub_node.hub_url,
            "local_owner": True,
            "handoff": False,
        }
        return {
            "ok": True,
            "accepted": True,
            "idempotent": bool(idempotent),
            "service": "main_computer.exp_fdb_hub",
            "session_id": session_id,
            "run_id": accepted_session.get("run_id"),
            "request_id": accepted_session.get("request_id"),
            "execution_hub": execution_hub,
            "worker_hub": dict(execution_hub),
            "continuation_url": continuation_url,
            "continuation": {
                "direct": True,
                "bounce_required": True,
                "reason": "continue_on_execution_hub",
                "stream_path": exp_work_session_stream_path(session_id),
                "hub_id": self.server.stable_hub_node.hub_id,
                "hub_url": self.server.stable_hub_node.hub_url,
            },
            "bounce": {
                "required": True,
                "reason": "continue_on_execution_hub",
                "same_hub": True,
            },
            "execution": accepted_session.get("execution", {}),
            "payout": _exp_public_redacted(accepted_session.get("payout", {})),
            "request": _exp_public_redacted(request_status or {}),
            "hub_to_hub_handoff": False,
            "hub_id": self.server.stable_hub_node.hub_id,
            "cluster_id": self.server.stable_topology.cluster_id,
        }

    def _post_same_request_to_owner_hub(
        self,
        *,
        owner_hub_id: str,
        owner_hub_url: str,
        body: dict[str, Any],
        timeout_seconds: float = 15.0,
    ) -> tuple[int, dict[str, Any]]:
        """Forward a requester-shaped exp work request to the worker owner Hub.

        The entry exp Hub remains a router only. The owner Hub receives the same
        public work-request payload, revalidates the current worker owner locally,
        and owns the exp request hold, lease, result, earning, and continuation.
        """

        base_url = str(owner_hub_url or "").rstrip("/")
        if not base_url:
            raise StableHubWorkerSessionError("owner_hub_url is required for remote exp handoff.")
        if str(owner_hub_id or "") == self.server.stable_hub_node.hub_id:
            raise StableHubWorkerSessionError("remote exp handoff target must be a different Hub.")

        handoff_url = f"{base_url}/api/hub/v1/work/requests"
        payload = json.dumps(body, sort_keys=True).encode("utf-8")
        request = Request(
            handoff_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Main-Computer-Exp-Hub-Handoff-From": self.server.stable_hub_node.hub_id,
                "X-Main-Computer-Exp-Hub-Handoff-To": str(owner_hub_id),
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - topology-owned Hub URL
                response_body = response.read().decode("utf-8") or "{}"
                decoded = json.loads(response_body)
                if not isinstance(decoded, dict):
                    decoded = {"ok": False, "error": "owner_hub_response_not_object"}
                return int(response.status), decoded
        except HTTPError as exc:
            response_body = exc.read().decode("utf-8") or "{}"
            try:
                decoded = json.loads(response_body)
                if not isinstance(decoded, dict):
                    decoded = {"ok": False, "error": "owner_hub_response_not_object"}
            except json.JSONDecodeError:
                decoded = {"ok": False, "error": "owner_hub_response_not_json", "body": response_body}
            return int(exc.code), decoded
        except (URLError, TimeoutError, OSError) as exc:
            raise ConnectionError(f"exp owner Hub handoff failed: {exc}") from exc


    def _exp_live_session_open_work_count(self, session: LiveWorkerSession) -> int:
        snapshot = session.snapshot() if hasattr(session, "snapshot") else {}
        pending = int(snapshot.get("pending_offer_count") or 0) if isinstance(snapshot, dict) else 0
        try:
            open_sessions = self.server.exp_accepted_work_session_directory.list_open_sessions_for_worker_connection(
                worker_id="",
                connection_id=str(session.connection_id or ""),
            )
            accepted = len(open_sessions)
        except Exception:
            accepted = 0
        return max(0, pending + accepted)


    def _exp_live_market_record_matches_work(self, record: dict[str, Any], work: dict[str, Any]) -> bool:
        try:
            constraints = normalize_request_market_constraints(work)
        except Exception:
            return False
        required_ring = str(constraints.get("ring") or "")
        required_capabilities = set(str(value) for value in constraints.get("capabilities", []))
        if str(record.get("status") or "") != "live":
            return False
        if required_ring and required_ring not in [str(value) for value in record.get("rings", [])]:
            return False
        worker_capabilities = set(str(value) for value in record.get("capabilities", []))
        if not required_capabilities.issubset(worker_capabilities):
            return False
        try:
            if int(record.get("active_sessions") or 0) >= max(1, int(record.get("max_concurrency") or 1)):
                return False
        except (TypeError, ValueError):
            return False
        max_price = constraints.get("max_price")
        if isinstance(max_price, dict):
            worker_price = dict(record.get("price") or {})
            if str(worker_price.get("unit") or "compute_credit") != str(max_price.get("unit") or "compute_credit"):
                return False
            worker_wei = credit_decimal_text_to_wei(str(worker_price.get("amount") or "0"), default="0")
            max_wei = credit_decimal_text_to_wei(str(max_price.get("amount") or "0"), default="0")
            if worker_wei > max_wei:
                return False
        return True

    def _exp_select_local_live_worker_for_work(self, work: dict[str, Any]) -> dict[str, Any] | None:
        """Select directly from this Hub's live websocket sessions.

        The live-session route no longer consults durable worker-id market rows for
        local selection.  A worker page refresh must not be able to create a hidden
        selectable identity.  The socket's authenticated market profile and current
        connection state are the only routeable facts.
        """

        with self.server.live_worker_sessions_lock:
            sessions = [session for session in self.server.live_worker_sessions.values()]
        candidates: list[dict[str, Any]] = []
        for session in sessions:
            if session is None or not session.is_live:
                continue
            connection_id = str(session.connection_id or "")
            if not connection_id:
                continue
            try:
                market = normalize_worker_market_profile(dict(getattr(session, "market_profile", {}) or {}))
            except Exception:
                continue
            candidate = dict(market)
            candidate.update(
                {
                    "status": "live",
                    "owner_hub_id": self.server.stable_hub_node.hub_id,
                    "owner_hub_url": self.server.stable_hub_node.hub_url,
                    "connection_id": connection_id,
                    "active_sessions": self._exp_live_session_open_work_count(session),
                    "selection": {
                        "mode": "live-websocket-connection",
                        "source": "live-websocket-connection",
                    },
                }
            )
            if not self._exp_live_market_record_matches_work(candidate, work):
                continue
            try:
                constraints = normalize_request_market_constraints(work)
                candidate["partition"] = str(constraints.get("partition") or constraints.get("ring") or "")
                candidate["selection"] = {
                    "mode": "live-websocket-connection",
                    "partition": candidate["partition"],
                    "required_capabilities": sorted(str(value) for value in constraints.get("capabilities", [])),
                    "source": "live-websocket-connection",
                }
            except Exception:
                pass
            candidates.append(candidate)
        if not candidates:
            return None
        candidates.sort(
            key=lambda record: (
                str((record.get("price") or {}).get("unit") or "compute_credit"),
                credit_decimal_text_to_wei(str((record.get("price") or {}).get("amount") or "0"), default="0"),
                str(record.get("connection_id") or ""),
            )
        )
        return candidates[0]

    def _exp_select_worker_for_work(self, body: dict[str, Any]) -> dict[str, Any] | None:
        # Requester routing is intentionally socket-only.  Durable/stale worker id
        # rows are not a fallback and cannot make a worker selectable.
        return self._exp_select_local_live_worker_for_work(body)


    def _handle_exp_live_work_request(self) -> None:
        body = self._read_json()
        selected_worker = self._exp_select_worker_for_work(body)
        if selected_worker is None:
            self._send_json(
                {
                    "ok": False,
                    "error": "no_live_worker_available",
                    "message": "No currently connected live worker matched the request.",
                    "hub_id": self.server.stable_hub_node.hub_id,
                    "cluster_id": self.server.stable_topology.cluster_id,
                },
                status=HTTPStatus.CONFLICT,
            )
            return
        owner_hub_id = str(selected_worker.get("owner_hub_id") or "")
        owner_hub_url = str(selected_worker.get("owner_hub_url") or "")
        if owner_hub_id != self.server.stable_hub_node.hub_id:
            if self.headers.get("X-Main-Computer-Exp-Hub-Handoff-From"):
                self._send_json(
                    {
                        "ok": False,
                        "error": "remote_handoff_target_not_local",
                        "message": "Forwarded exp handoff reached a Hub that no longer owns a matching live worker.",
                        "owner_hub": {
                            "hub_id": owner_hub_id,
                            "hub_url": owner_hub_url,
                        },
                        "hub_id": self.server.stable_hub_node.hub_id,
                        "cluster_id": self.server.stable_topology.cluster_id,
                    },
                    status=HTTPStatus.CONFLICT,
                )
                return
            try:
                owner_status, owner_response = self._post_same_request_to_owner_hub(
                    owner_hub_id=owner_hub_id,
                    owner_hub_url=owner_hub_url,
                    body=body,
                )
            except (ConnectionError, StableHubWorkerSessionError) as exc:
                self._send_json(
                    {
                        "ok": False,
                        "error": "owner_hub_handoff_failed",
                        "message": str(exc),
                        "owner_hub": {
                            "hub_id": owner_hub_id,
                            "hub_url": owner_hub_url,
                        },
                        "hub_id": self.server.stable_hub_node.hub_id,
                        "cluster_id": self.server.stable_topology.cluster_id,
                    },
                    status=HTTPStatus.BAD_GATEWAY,
                )
                return

            if owner_response.get("ok") is True and owner_response.get("accepted") is True:
                entry_response = dict(owner_response)
                accepted_session_id = normalize_session_id(entry_response.get("session_id"))
                entry_response["hub_id"] = self.server.stable_hub_node.hub_id
                entry_response["entry_hub_id"] = self.server.stable_hub_node.hub_id
                entry_response["accepted_by_hub_id"] = str(owner_response.get("hub_id") or owner_hub_id)
                entry_response["execution_hub"] = {
                    "hub_id": owner_hub_id,
                    "hub_url": owner_hub_url,
                    "local_owner": False,
                    "handoff": True,
                }
                entry_response["worker_hub"] = dict(entry_response["execution_hub"])
                entry_response["continuation_url"] = exp_work_session_continuation_url(
                    owner_hub_url,
                    accepted_session_id,
                )
                entry_response["continuation"] = {
                    "direct": True,
                    "bounce_required": True,
                    "reason": "continue_on_execution_hub",
                    "stream_path": exp_work_session_stream_path(accepted_session_id),
                    "hub_id": owner_hub_id,
                    "hub_url": owner_hub_url,
                }
                entry_response["bounce"] = {
                    "required": True,
                    "reason": "continue_on_execution_hub",
                    "same_hub": False,
                }
                entry_response["hub_to_hub_handoff"] = True
                entry_response["handoff"] = {
                    "routed": True,
                    "from_hub_id": self.server.stable_hub_node.hub_id,
                    "to_hub_id": owner_hub_id,
                    "to_hub_url": owner_hub_url,
                    "request_shape": "exp-live-session-work",
                }
                self._send_json(entry_response)
                return

            handoff_error = dict(owner_response)
            handoff_error.setdefault("ok", False)
            handoff_error["entry_hub_id"] = self.server.stable_hub_node.hub_id
            handoff_error["hub_id"] = self.server.stable_hub_node.hub_id
            handoff_error["owner_hub"] = {
                "hub_id": owner_hub_id,
                "hub_url": owner_hub_url,
            }
            handoff_error["handoff"] = {
                "routed": True,
                "from_hub_id": self.server.stable_hub_node.hub_id,
                "to_hub_id": owner_hub_id,
                "to_hub_url": owner_hub_url,
                "request_shape": "exp-live-session-work",
            }
            self._send_json(
                handoff_error,
                status=HTTPStatus(owner_status) if 400 <= owner_status <= 599 else HTTPStatus.BAD_GATEWAY,
            )
            return
        connection_id = str(selected_worker.get("connection_id") or "")
        live_session = self.server.get_live_worker_session(connection_id)
        if live_session is None or not live_session.is_live:
            try:
                self.server.stable_worker_market_directory.record_worker_closed(
                    worker_id=str(selected_worker.get("worker_id") or ""),
                    connection_id=connection_id,
                    reason="socket_missing_during_dispatch",
                )
            except Exception:
                pass
            self._send_json(
                {
                    "ok": False,
                    "error": "no_live_worker_available",
                    "message": "No currently connected live worker matched the request.",
                    "hub_id": self.server.stable_hub_node.hub_id,
                },
                status=HTTPStatus.CONFLICT,
            )
            return

        request_payload = self._exp_request_body_for_live_session(body, selected_worker=selected_worker)
        request = HubAIRequest.from_payload(
            request_payload,
            default_model=str(request_payload.get("model") or "live-session-worker"),
            default_client_node_id=str(request_payload.get("client_node_id") or "exp-live-requester"),
        )
        worker_id = str(getattr(live_session, "worker_id", "") or "")
        if not worker_id:
            worker_id = _exp_internal_settlement_route_key({}, connection_id=connection_id)
        worker_instance_id = self._exp_worker_instance_id_for_connection(worker_id, connection_id)

        try:
            record = self._exp_create_live_session_request_record(
                request=request,
                request_payload=request_payload,
                worker_id=worker_id,
                worker_instance_id=worker_instance_id,
                selected_worker=dict(selected_worker),
            )
        except Exception as exc:
            self._send_json(
                {
                    "ok": False,
                    "error": "credit_spend_prepare_failed",
                    "message": str(exc),
                    "hub_id": self.server.stable_hub_node.hub_id,
                },
                status=HTTPStatus.PAYMENT_REQUIRED,
            )
            return

        request_id = str(record.request_id or "")

        session_id = new_session_id()
        run_id = new_run_id()
        partition = stable_partition_key_for_work(body)
        task_queue = stable_task_queue_for_partition(partition)
        metadata = dict(request_payload.get("metadata") or {}) if isinstance(request_payload.get("metadata"), dict) else {}
        selected_offer = dict(metadata.get("selected_offer") or {}) if isinstance(metadata.get("selected_offer"), dict) else {}
        selected_credit_wei = self._exp_live_session_selected_credit_wei(selected_worker)
        selected_credit_units = self._exp_live_session_selected_credit_units(selected_worker)
        local_ai_timeout_seconds = self._exp_live_session_local_ai_timeout_seconds(body=body, metadata=metadata)
        input_payload = body.get("input") if isinstance(body.get("input"), dict) else {}
        execution_limits = body.get("execution_limits") if isinstance(body.get("execution_limits"), dict) else {}

        def positive_int(value: Any) -> int | None:
            try:
                parsed = int(float(str(value).strip()))
            except (TypeError, ValueError):
                return None
            return max(1, min(parsed, 128_000)) if parsed > 0 else None

        target_tokens: int | None = None
        for value in (
            body.get("target_tokens"),
            body.get("max_output_tokens"),
            input_payload.get("target_tokens"),
            input_payload.get("max_output_tokens"),
            execution_limits.get("target_tokens"),
            execution_limits.get("max_output_tokens"),
            metadata.get("target_tokens"),
            metadata.get("max_output_tokens"),
            metadata.get("worker_target_tokens"),
        ):
            target_tokens = positive_int(value)
            if target_tokens is not None:
                break

        def first_present(*values: Any) -> Any:
            for value in values:
                if value is not None and str(value).strip() != "":
                    return value
            return None

        def first_string_list(*values: Any) -> list[str]:
            for value in values:
                if not isinstance(value, list):
                    continue
                items = [str(item or "").strip() for item in value]
                items = [item for item in items if item]
                if items:
                    return items
            return []

        required_headings = first_string_list(
            body.get("required_headings"),
            body.get("early_result_required_headings"),
            input_payload.get("required_headings"),
            input_payload.get("early_result_required_headings"),
            execution_limits.get("required_headings"),
            execution_limits.get("early_result_required_headings"),
            metadata.get("required_headings"),
            metadata.get("early_result_required_headings"),
        )

        think_value = first_present(
            body.get("ollama_think"),
            body.get("think"),
            input_payload.get("ollama_think"),
            input_payload.get("think"),
            execution_limits.get("ollama_think"),
            execution_limits.get("think"),
            metadata.get("ollama_think"),
            metadata.get("think"),
        )

        completion_sentinel = first_present(
            body.get("stream_result_sentinel"),
            body.get("early_result_sentinel"),
            body.get("completion_sentinel"),
            input_payload.get("stream_result_sentinel"),
            input_payload.get("early_result_sentinel"),
            input_payload.get("completion_sentinel"),
            execution_limits.get("stream_result_sentinel"),
            execution_limits.get("early_result_sentinel"),
            execution_limits.get("completion_sentinel"),
            metadata.get("stream_result_sentinel"),
            metadata.get("early_result_sentinel"),
            metadata.get("completion_sentinel"),
        )

        def merge_options(*values: Any) -> dict[str, Any]:
            merged: dict[str, Any] = {}
            for value in values:
                if isinstance(value, dict):
                    merged.update(value)
            if target_tokens is not None:
                existing = positive_int(merged.get("num_predict"))
                merged["num_predict"] = min(existing, target_tokens) if existing is not None else target_tokens
            return merged

        provider_options = merge_options(
            body.get("provider_options"),
            body.get("ollama_options"),
            input_payload.get("provider_options"),
            input_payload.get("ollama_options"),
            execution_limits.get("provider_options"),
            execution_limits.get("ollama_options"),
            metadata.get("provider_options"),
            metadata.get("ollama_options"),
        )

        work_execution_limits: dict[str, Any] = {
            "timeout_seconds": local_ai_timeout_seconds,
            "worker_timeout_seconds": local_ai_timeout_seconds,
            "work_timeout_seconds": local_ai_timeout_seconds,
            "max_runtime_seconds": local_ai_timeout_seconds,
            "local_ai_timeout_seconds": local_ai_timeout_seconds,
            "worker_local_ai_timeout_seconds": local_ai_timeout_seconds,
        }
        if target_tokens is not None:
            work_execution_limits["target_tokens"] = target_tokens
            work_execution_limits["max_output_tokens"] = target_tokens
        if think_value is not None:
            work_execution_limits["think"] = think_value
            work_execution_limits["ollama_think"] = think_value
        if completion_sentinel is not None:
            work_execution_limits["completion_sentinel"] = str(completion_sentinel)
            work_execution_limits["early_result_sentinel"] = str(completion_sentinel)
            work_execution_limits["stream_result_sentinel"] = str(completion_sentinel)
        if required_headings:
            work_execution_limits["required_headings"] = list(required_headings)
            work_execution_limits["early_result_required_headings"] = list(required_headings)
        if provider_options:
            work_execution_limits["provider_options"] = dict(provider_options)
            work_execution_limits["ollama_options"] = dict(provider_options)

        work_input = dict(body.get("input", {})) if isinstance(body.get("input"), dict) else {}
        if target_tokens is not None:
            work_input.setdefault("target_tokens", target_tokens)
            work_input.setdefault("max_output_tokens", target_tokens)
        if think_value is not None:
            work_input.setdefault("think", think_value)
            work_input.setdefault("ollama_think", think_value)
        if completion_sentinel is not None:
            work_input.setdefault("completion_sentinel", str(completion_sentinel))
            work_input.setdefault("early_result_sentinel", str(completion_sentinel))
            work_input.setdefault("stream_result_sentinel", str(completion_sentinel))
        if required_headings:
            work_input.setdefault("required_headings", list(required_headings))
            work_input.setdefault("early_result_required_headings", list(required_headings))
        if provider_options:
            work_input.setdefault("provider_options", dict(provider_options))
            work_input.setdefault("ollama_options", dict(provider_options))

        offer = {
            "type": "hub.work.offer",
            "service": "main_computer.exp_fdb_hub",
            "session_id": session_id,
            "run_id": run_id,
            "request_id": request_id,
            "work": {
                "messages": request.messages,
                "input": work_input,
                "model": str(metadata.get("requested_model") or request.model or "live-session-worker"),
                "ring": body.get("ring", body.get("partition", partition)),
                "capabilities": body.get("capabilities", body.get("required_capabilities", [])),
                "timeout_seconds": local_ai_timeout_seconds,
                "worker_timeout_seconds": local_ai_timeout_seconds,
                "work_timeout_seconds": local_ai_timeout_seconds,
                "max_runtime_seconds": local_ai_timeout_seconds,
                "local_ai_timeout_seconds": local_ai_timeout_seconds,
                "worker_local_ai_timeout_seconds": local_ai_timeout_seconds,
                "execution_limits": work_execution_limits,
            },
            "pricing": {
                "quoted_credits": selected_credit_units,
                "quoted_credits_wei": str(selected_credit_wei),
                "quoted_credits_display": credit_wei_to_decimal_text(selected_credit_wei),
                "worker_earning_credits": selected_credit_units,
                "worker_earning_credit_wei": str(selected_credit_wei),
                "worker_earning_display": credit_wei_to_decimal_text(selected_credit_wei),
                "unit": str((selected_worker.get("price") or {}).get("unit") or "compute_credit"),
                "pricing_mode": "exp_live_session_direct",
                "execution_mode": "exp-live-session-direct-v1",
                "legacy_worker_pull_lease": False,
            },
            "selected_offer": selected_offer,
            "execution_hub": {
                "hub_id": self.server.stable_hub_node.hub_id,
                "hub_url": self.server.stable_hub_node.hub_url,
                "local_owner": True,
                "handoff": False,
            },
        }
        if target_tokens is not None:
            offer["work"]["target_tokens"] = target_tokens
            offer["work"]["max_output_tokens"] = target_tokens
        if think_value is not None:
            offer["work"]["think"] = think_value
            offer["work"]["ollama_think"] = think_value
        if completion_sentinel is not None:
            offer["work"]["completion_sentinel"] = str(completion_sentinel)
            offer["work"]["early_result_sentinel"] = str(completion_sentinel)
            offer["work"]["stream_result_sentinel"] = str(completion_sentinel)
        if required_headings:
            offer["work"]["required_headings"] = list(required_headings)
            offer["work"]["early_result_required_headings"] = list(required_headings)
        if provider_options:
            offer["work"]["provider_options"] = dict(provider_options)
            offer["work"]["ollama_options"] = dict(provider_options)

        payout = {
            "backend": "exp_fdb_credit_ledger",
            "charge_id": "",
            "worker_earning_id": "",
            "unit": "compute_credit",
            "status": "pending_worker_acceptance",
            "direct_spend": True,
            "legacy_worker_pull_lease": False,
        }
        accepted = self.server.exp_accepted_work_session_directory.record_accepted(
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            requester_msk_id=str(metadata.get("multisession_key_id") or ""),
            requester_account_id=str(request.account_id or ""),
            requester_wallet_address=str(metadata.get("wallet_address") or ""),
            worker_id=worker_id,
            worker_connection_id=connection_id,
            owner_hub_id=self.server.stable_hub_node.hub_id,
            owner_hub_url=self.server.stable_hub_node.hub_url,
            partition=partition,
            task_queue=task_queue,
            work={
                "request": body,
                "exp_request_payload": request_payload,
                "worker_instance_id": worker_instance_id,
                "selected_worker": dict(selected_worker),
                "direct_live_session": True,
                "legacy_worker_pull_lease": False,
            },
            worker_acceptance={"status": "pending", "reason": "offer_sent"},
            payout=payout,
        )

        try:
            acceptance = live_session.offer_work_and_wait_for_acceptance(
                offer,
                timeout_seconds=float(body.get("accept_timeout_seconds") or 10.0),
            )
        except Exception as exc:
            try:
                failed_payout = dict(payout)
                failed_payout.update({"status": "failed", "release_reason": "worker_accept_timeout"})
                self._exp_fail_live_session_request(
                    request_id=request_id,
                    worker_id=worker_id,
                    worker_instance_id=worker_instance_id,
                    error=f"worker did not accept offer: {exc}",
                    terminal_reason="worker_accept_timeout",
                )
                self.server.exp_accepted_work_session_directory.record_failed(
                    session_id=session_id,
                    worker_connection_id=connection_id,
                    worker_failure={
                        "type": "worker.work.accept_timeout",
                        "error": f"worker did not accept offer: {exc}",
                    },
                    payout=failed_payout,
                )
            except Exception:
                pass
            self._send_json(
                {
                    "ok": False,
                    "error": "worker_accept_timeout",
                    "message": str(exc),
                    "request_id": request_id,
                    "session_id": session_id,
                    "hub_id": self.server.stable_hub_node.hub_id,
                },
                status=HTTPStatus.GATEWAY_TIMEOUT,
            )
            return

        receipt = self._exp_spend_live_session_credit(
            request_id=request_id,
            worker_id=worker_id,
            selected_worker=dict(selected_worker),
        )
        request_status = self._exp_mark_live_session_request_running(
            request_id=request_id,
            session_id=session_id,
            run_id=run_id,
            worker_id=worker_id,
            worker_instance_id=worker_instance_id,
            worker_acceptance=dict(acceptance),
        )
        accepted = self.server.exp_accepted_work_session_directory.get_session(session_id) or accepted
        if str(accepted.get("status") or "") not in {"succeeded", "failed", "cancelled"}:
            payout = dict(accepted.get("payout") or payout)
            payout.update(
                {
                    "status": "charged",
                    "charge_id": str(receipt.get("charge_id") or ""),
                    "worker_earning_id": str(receipt.get("worker_earning_id") or ""),
                    "charged_credits": int(receipt.get("charged_credits") or 0),
                    "charged_credit_wei": str(receipt.get("charged_credit_wei") or selected_credit_wei),
                    "direct_spend": True,
                    "legacy_worker_pull_lease": False,
                }
            )
        self._send_json(self._exp_live_session_response(accepted_session=accepted, request_status=request_status))

    def _handle_exp_worker_delta_message(
        self,
        *,
        session: LiveWorkerSession,
        worker_id: str,
        connection_id: str,
        message: dict[str, Any],
    ) -> None:
        session_id = normalize_session_id(message.get("session_id"))
        request_id = normalize_request_id(message.get("request_id"))
        accepted = self.server.exp_accepted_work_session_directory.get_session(session_id)
        if accepted is None:
            raise StableHubWorkerSessionError("accepted exp work session does not exist.")
        if str(accepted.get("request_id") or "") != request_id:
            raise StableHubWorkerSessionError("worker delta message request_id mismatch.")
        if str(accepted.get("worker_id") or "") != worker_id:
            raise StableHubWorkerSessionError("worker delta message worker_id mismatch.")
        if str(accepted.get("worker_connection_id") or "") != connection_id:
            raise StableHubWorkerSessionError("worker delta message connection_id mismatch.")
        expected_run_id = str(accepted.get("run_id") or "")
        if message.get("run_id") and str(message.get("run_id")) != expected_run_id:
            raise StableHubWorkerSessionError("worker delta message run_id mismatch.")
        if str(accepted.get("status") or "") in {"succeeded", "failed", "cancelled"}:
            return

        worker_seq: int | None = None
        try:
            worker_seq = int(message.get("seq")) if message.get("seq") is not None else None
        except (TypeError, ValueError):
            worker_seq = None
        delta = str(message.get("delta") or message.get("content_delta") or "")
        content_so_far = str(message.get("content_so_far") or message.get("content") or "")
        if not delta and not content_so_far:
            return
        stream_event: dict[str, Any] = {
            "type": "delta",
            "status": "running",
            "session_id": session_id,
            "request_id": request_id,
            "run_id": expected_run_id,
            "delta": delta,
            "content_so_far": content_so_far,
            "done": bool(message.get("done", False)),
            "worker_created_at": str(message.get("created_at") or message.get("worker_created_at") or ""),
        }
        if worker_seq is not None:
            stream_event["worker_seq"] = worker_seq
        if message.get("content_chars") is not None:
            stream_event["content_chars"] = message.get("content_chars")
        recorded = self.server.exp_accepted_work_session_directory.append_stream_event(session_id, stream_event)
        try:
            session.send_json(
                {
                    "type": "hub.work.delta.accepted",
                    "ok": True,
                    "session_id": session_id,
                    "request_id": request_id,
                    "seq": recorded.get("seq"),
                    "worker_seq": worker_seq,
                }
            )
        except Exception:
            # Delta acknowledgement is best-effort; terminal result handling remains
            # authoritative for job completion.
            pass

    def _handle_exp_worker_terminal_message(
        self,
        *,
        session: LiveWorkerSession,
        worker_id: str,
        connection_id: str,
        message: dict[str, Any],
    ) -> None:
        message_type = str(message.get("type") or "")
        session_id = normalize_session_id(message.get("session_id"))
        request_id = normalize_request_id(message.get("request_id"))
        accepted = self.server.exp_accepted_work_session_directory.get_session(session_id)
        if accepted is None:
            raise StableHubWorkerSessionError("accepted exp work session does not exist.")
        if str(accepted.get("request_id") or "") != request_id:
            raise StableHubWorkerSessionError("worker terminal message request_id mismatch.")
        if str(accepted.get("worker_id") or "") != worker_id:
            raise StableHubWorkerSessionError("worker terminal message worker_id mismatch.")
        if str(accepted.get("worker_connection_id") or "") != connection_id:
            raise StableHubWorkerSessionError("worker terminal message connection_id mismatch.")
        expected_run_id = str(accepted.get("run_id") or "")
        if message.get("run_id") and str(message.get("run_id")) != expected_run_id:
            raise StableHubWorkerSessionError("worker terminal message run_id mismatch.")
        if str(accepted.get("status") or "") in {"succeeded", "failed", "cancelled"}:
            session.send_json(
                {
                    "type": "hub.work.terminal.accepted",
                    "ok": True,
                    "idempotent": True,
                    "session_id": session_id,
                    "request_id": request_id,
                    "status": accepted.get("status"),
                    "session": {
                        "terminal": True,
                        "privacy": "worker wallet and hub connection are internal",
                    },
                }
            )
            return
        work = dict(accepted.get("work") or {})
        selected_worker = dict(work.get("selected_worker") or {})
        worker_instance_id = str(work.get("worker_instance_id") or connection_id)
        if message_type == "worker.work.result":
            result_payload = message.get("result")
            if not isinstance(result_payload, dict):
                result_payload = {"response": {"content": str(result_payload or "")}}
            result_payload.setdefault("status", "success")
            receipt = self._exp_spend_live_session_credit(
                request_id=request_id,
                worker_id=worker_id,
                selected_worker=selected_worker,
                result=result_payload,
            )
            request_status = self._exp_complete_live_session_request(
                request_id=request_id,
                worker_id=worker_id,
                worker_instance_id=worker_instance_id,
                result_payload=result_payload,
                receipt=receipt,
                selected_worker=selected_worker,
            )
            payout = dict(accepted.get("payout") or {})
            payout.update(
                {
                    "status": "charged",
                    "charge_id": str(receipt.get("charge_id") or ""),
                    "worker_earning_id": str(receipt.get("worker_earning_id") or ""),
                    "charged_credits": int(receipt.get("charged_credits") or 0),
                    "charged_credit_wei": str(receipt.get("charged_credit_wei") or ""),
                    "released_credits": 0,
                    "released_credit_wei": "0",
                    "direct_spend": True,
                    "legacy_worker_pull_lease": False,
                }
            )
            updated = self.server.exp_accepted_work_session_directory.record_succeeded(
                session_id=session_id,
                worker_connection_id=connection_id,
                worker_result={"type": message_type, "result": result_payload, "request": request_status},
                payout=payout,
            )
            session.send_json(
                {
                    "type": "hub.work.result.accepted",
                    "ok": True,
                    "session_id": session_id,
                    "request_id": request_id,
                    "status": updated.get("status"),
                    "payout": _exp_public_redacted(updated.get("payout", {})),
                    "request": _exp_public_redacted(request_status),
                }
            )
            return
        if message_type == "worker.work.failed":
            error = message.get("error")
            error_text = str(error.get("error") if isinstance(error, dict) else error or message.get("message") or "worker_failed")
            receipt = self._exp_spend_live_session_credit(
                request_id=request_id,
                worker_id=worker_id,
                selected_worker=selected_worker,
                result={"status": "failed", "error": error_text},
            )
            request_status = self._exp_fail_live_session_request(
                request_id=request_id,
                worker_id=worker_id,
                worker_instance_id=worker_instance_id,
                error=error_text,
                terminal_reason="worker_result_failed",
            )
            payout = dict(accepted.get("payout") or {})
            payout.update(
                {
                    "status": "failed",
                    "charge_id": str(receipt.get("charge_id") or ""),
                    "worker_earning_id": str(receipt.get("worker_earning_id") or ""),
                    "charged_credits": int(receipt.get("charged_credits") or 0),
                    "charged_credit_wei": str(receipt.get("charged_credit_wei") or ""),
                    "released_credits": 0,
                    "released_credit_wei": "0",
                    "direct_spend": True,
                    "legacy_worker_pull_lease": False,
                    "release_reason": "worker_failed",
                }
            )
            updated = self.server.exp_accepted_work_session_directory.record_failed(
                session_id=session_id,
                worker_connection_id=connection_id,
                worker_failure={"type": message_type, "error": error_text, "request": request_status},
                payout=payout,
            )
            session.send_json(
                {
                    "type": "hub.work.failed.accepted",
                    "ok": True,
                    "session_id": session_id,
                    "request_id": request_id,
                    "status": updated.get("status"),
                    "payout": _exp_public_redacted(updated.get("payout", {})),
                    "request": _exp_public_redacted(request_status),
                }
            )
            return
        raise StableHubWorkerSessionError("unsupported worker terminal message.")

    def _exp_stream_events_for_response(self, session_id: str) -> list[dict[str, Any]]:
        try:
            events = self.server.exp_accepted_work_session_directory.list_stream_events(session_id, after_seq=-1)
        except Exception:
            return []
        return [_exp_public_stream_event(event) for event in events]

    def _write_sse_event(self, event_type: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.wfile.write(f"event: {event_type}\n".encode("utf-8"))
        for line in data.splitlines() or ["{}"]:
            self.wfile.write(f"data: {line}\n".encode("utf-8"))
        self.wfile.write(b"\n")
        self.wfile.flush()

    def _handle_exp_work_session_sse_stream(self, session_id: str) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            after_seq = int((query.get("after_seq") or query.get("since_seq") or ["-1"])[0])
        except (TypeError, ValueError):
            after_seq = -1
        try:
            timeout_s = float((query.get("timeout") or query.get("timeout_seconds") or ["300"])[0])
        except (TypeError, ValueError):
            timeout_s = 300.0
        timeout_s = max(1.0, min(timeout_s, 3600.0))

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        deadline = time.monotonic() + timeout_s
        last_seq = after_seq
        terminal_sent = False
        while time.monotonic() < deadline and not terminal_sent:
            accepted = self.server.exp_accepted_work_session_directory.get_session(session_id)
            if accepted is None:
                self._write_sse_event(
                    "error",
                    {"ok": False, "type": "error", "error": "work_session_not_found", "session_id": session_id},
                )
                return
            accepted = self._exp_reconcile_open_live_session(accepted)
            try:
                events = self.server.exp_accepted_work_session_directory.list_stream_events(session_id, after_seq=last_seq)
            except Exception as exc:
                self._write_sse_event(
                    "error",
                    {"ok": False, "type": "error", "error": str(exc), "session_id": session_id},
                )
                return
            for event in events:
                public_event = _exp_public_stream_event(event)
                try:
                    last_seq = max(last_seq, int(public_event.get("seq") or last_seq))
                except (TypeError, ValueError):
                    pass
                event_type = str(public_event.get("type") or "event")
                self._write_sse_event(event_type, public_event)
                if event_type in {"result", "failed", "cancelled"} or str(public_event.get("status") or "") in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }:
                    terminal_sent = True
            if terminal_sent:
                break
            time.sleep(0.1)
        if not terminal_sent:
            self._write_sse_event(
                "keepalive",
                {
                    "ok": True,
                    "type": "keepalive",
                    "session_id": session_id,
                    "after_seq": last_seq,
                    "timeout": True,
                    "hub_received_at": time.time(),
                },
            )

    def _handle_exp_work_session_stream(self, path: str) -> None:
        prefix = "/api/hub/v1/work/sessions/"
        suffix = "/stream"
        raw_session_id = path[len(prefix) : -len(suffix)]
        try:
            session_id = normalize_session_id(raw_session_id)
        except StableHubWorkerSessionError as exc:
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        accepted = self.server.exp_accepted_work_session_directory.get_session(session_id)
        if accepted is None:
            self._send_json({"ok": False, "error": "work_session_not_found", "session_id": session_id}, status=HTTPStatus.NOT_FOUND)
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        wants_sse = (
            "text/event-stream" in str(self.headers.get("Accept") or "").lower()
            or str((query.get("format") or query.get("transport") or [""])[0]).lower() == "sse"
            or str((query.get("stream") or [""])[0]).lower() in {"1", "true", "yes", "sse"}
        )
        if wants_sse:
            self._handle_exp_work_session_sse_stream(session_id)
            return
        accepted = self._exp_reconcile_open_live_session(accepted)
        request_record = None
        try:
            request_record = self._exp_request_store().get(str(accepted.get("request_id") or ""))
        except Exception:
            request_record = None
        request_status = request_record.as_dict() if request_record is not None and hasattr(request_record, "as_dict") else {}
        continuation_url = exp_work_session_continuation_url(self.server.stable_hub_node.hub_url, session_id)
        self._send_json(
            {
                "ok": True,
                "service": "main_computer.exp_fdb_hub",
                "session_id": session_id,
                "run_id": accepted.get("run_id"),
                "request_id": accepted.get("request_id"),
                "status": accepted.get("status"),
                "execution_hub": {
                    "hub_id": self.server.stable_hub_node.hub_id,
                    "hub_url": self.server.stable_hub_node.hub_url,
                    "local_owner": True,
                    "handoff": False,
                },
                "continuation_url": continuation_url,
                "bounce": {
                    "required": True,
                    "reason": "continue_on_execution_hub",
                    "same_hub": True,
                },
                "payout": _exp_public_redacted(accepted.get("payout", {})),
                "execution": accepted.get("execution", {}),
                "request": _exp_public_redacted(request_status),
                "stream": {
                    "transport": "exp-hub-session-stream",
                    "mode": "accepted-session-state",
                    "source": "exp-accepted-session-record",
                    "realtime": {
                        "transport": "sse",
                        "url": continuation_url + "?format=sse",
                    },
                    "events": self._exp_stream_events_for_response(session_id),
                },
                "hub_to_hub_handoff": False,
                "hub_id": self.server.stable_hub_node.hub_id,
                "cluster_id": self.server.stable_topology.cluster_id,
            }
        )

    def _handle_worker_live_session_websocket(self) -> None:
        if not self._accept_websocket():
            return

        worker_id = ""
        connection_id = ""
        session: LiveWorkerSession | None = None
        try:
            auth_message = self._ws_read_json_message()
            if auth_message.get("type") != "worker.auth":
                raise StableHubWorkerSessionError("first worker live-session message must be worker.auth")
            authorization = self._authorize_worker_route(
                body=auth_message,
                worker_id="",
                registration=True,
            )
            connection_id = new_connection_id()
            worker_id = _exp_internal_settlement_route_key(authorization, connection_id=connection_id)
            market = self._market_profile_from_auth(auth_message)
            owner = {
                "status": "live",
                "owner_hub_id": self.server.stable_hub_node.hub_id,
                "owner_hub_url": self.server.stable_hub_node.hub_url,
                "connection_id": connection_id,
                "connected_at": datetime.now(timezone.utc).isoformat(),
                "lease_epoch": 0,
            }
            session = LiveWorkerSession(
                worker_id=worker_id,
                connection_id=connection_id,
                handler=self,
                opened_at=str(owner.get("connected_at") or ""),
                multisession_key_id=str(authorization.get("multisession_key_id") or ""),
                market_profile=market,
            )
            self.server.register_live_worker_session(session)
            self._exp_fail_open_live_sessions_for_connection(
                worker_id=worker_id,
                exclude_connection_id=connection_id,
                error=f"worker reconnected as {connection_id} before previous live-session work finished",
                terminal_reason="worker_connection_replaced",
            )
            session.send_json(
                {
                    "type": "hub.auth.accepted",
                    "ok": True,
                    "service": "main_computer.exp_fdb_hub",
                    "hub_id": self.server.stable_hub_node.hub_id,
                    "hub_url": self.server.stable_hub_node.hub_url,
                    "cluster_id": self.server.stable_topology.cluster_id,
                    "worker_session": {
                        "connected": True,
                        "privacy": "worker wallet and hub connection are internal",
                    },
                    "worker_hub": {
                        "hub_id": self.server.stable_hub_node.hub_id,
                        "hub_url": self.server.stable_hub_node.hub_url,
                        "local_owner": True,
                        "handoff": False,
                    },
                    "execution_hub": {
                        "hub_id": self.server.stable_hub_node.hub_id,
                        "hub_url": self.server.stable_hub_node.hub_url,
                    },
                    "heartbeat": {
                        "transport": "websocket",
                        "mode": "hub-ping-worker-pong",
                    },
                    "contract": experimental_stable_hub_contract(),
                }
            )

            ping_id = "ping_" + secrets.token_urlsafe(12).rstrip("=")
            session.send_json({"type": "hub.ping", "ping_id": ping_id})
            while True:
                message = self._ws_read_json_message()
                message_type = str(message.get("type") or "")
                if message_type == "worker.pong":
                    if message.get("connection_id") and str(message.get("connection_id")) != connection_id:
                        session.send_json(
                            {
                                "type": "hub.error",
                                "ok": False,
                                "error": "connection_mismatch",
                            }
                        )
                        continue
                    session.record_pong({"last_pong_at": datetime.now(timezone.utc).isoformat()})
                    session.send_json(
                        {
                            "type": "hub.pong.accepted",
                            "ok": True,
                            "worker_session": {
                                "connected": True,
                                "privacy": "worker wallet and hub connection are internal",
                            },
                        }
                    )
                    continue
                if message_type == "worker.work.accepted":
                    try:
                        session.record_work_accepted(message)
                    except StableHubWorkerSessionError as exc:
                        session.send_json(
                            {
                                "type": "hub.error",
                                "ok": False,
                                "error": str(exc),
                                "received_type": message_type,
                            }
                        )
                    continue
                if message_type == "worker.work.delta":
                    try:
                        self._handle_exp_worker_delta_message(
                            session=session,
                            worker_id=worker_id,
                            connection_id=connection_id,
                            message=message,
                        )
                    except StableHubWorkerSessionError as exc:
                        session.send_json(
                            {
                                "type": "hub.error",
                                "ok": False,
                                "error": str(exc),
                                "received_type": message_type,
                                "session_id": message.get("session_id"),
                                "request_id": message.get("request_id"),
                            }
                        )
                    continue
                if message_type in {"worker.work.result", "worker.work.failed"}:
                    try:
                        self._handle_exp_worker_terminal_message(
                            session=session,
                            worker_id=worker_id,
                            connection_id=connection_id,
                            message=message,
                        )
                    except StableHubWorkerSessionError as exc:
                        session.send_json(
                            {
                                "type": "hub.error",
                                "ok": False,
                                "error": str(exc),
                                "received_type": message_type,
                                "session_id": message.get("session_id"),
                                "request_id": message.get("request_id"),
                            }
                        )
                    continue
                if message_type == "worker.close":
                    break
                session.send_json(
                    {
                        "type": "hub.error",
                        "ok": False,
                        "error": "unsupported_worker_message",
                        "received_type": message_type,
                    }
                )
        except (ConnectionError, OSError):
            pass
        except (StableHubWorkerSessionError, RuntimeError, ValueError) as exc:
            try:
                if session is not None:
                    session.send_json(
                        {
                            "type": "hub.error",
                            "ok": False,
                            "error": str(exc),
                            "hub_id": self.server.stable_hub_node.hub_id,
                        }
                    )
                else:
                    self._ws_send_json(
                        {
                            "type": "hub.error",
                            "ok": False,
                            "error": str(exc),
                            "hub_id": self.server.stable_hub_node.hub_id,
                        }
                    )
            except Exception:
                pass
        finally:
            if worker_id and connection_id:
                self._exp_fail_open_live_sessions_for_connection(
                    worker_id=worker_id,
                    connection_id=connection_id,
                    error=f"worker live-session connection {connection_id} closed before returning a terminal result",
                    terminal_reason="worker_connection_closed",
                )
                removed = self.server.remove_live_worker_session(connection_id)
                if removed is not None:
                    removed.mark_closed(
                        reason="socket_closed",
                        closed_at=datetime.now(timezone.utc).isoformat(),
                    )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/hub/v1/workers/live-session":
            self._handle_worker_live_session_websocket()
            return
        if path.startswith("/api/hub/v1/work/sessions/") and path.endswith("/stream"):
            self._handle_exp_work_session_stream(path)
            return
        if path == "/api/hub/v1/hub-identity":
            identity = dict(self.server.identity)
            multisession_required = bool(getattr(self.server.config, "hub_require_multisession_auth", False))
            identity["multi_session_auth_required"] = multisession_required
            identity["auth"] = {
                "multi_session_auth_required": multisession_required,
                "multisession_auth_required": multisession_required,
                "worker_routes": "required" if multisession_required else "optional",
                "requester_routes": "required" if multisession_required else "optional",
                "scheme": "multisession-wallet",
            }
            return self._send_json(identity)
        if path == "/api/hub/v1/topology":
            self._send_json(
                {
                    "ok": True,
                    "service": "main_computer.exp_fdb_hub",
                    "hub_id": self.server.stable_hub_node.hub_id,
                    "cluster_id": self.server.stable_topology.cluster_id,
                    "topology": stable_hub_topology_to_dict(self.server.stable_topology),
                }
            )
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if _exp_legacy_worker_route(path):
            self._send_json(
                {
                    "ok": False,
                    "error": "not_found",
                    "message": "Deprecated exp worker REST endpoint was removed; use /api/hub/v1/workers/live-session.",
                    "path": path,
                    "hub_id": self.server.stable_hub_node.hub_id,
                },
                status=HTTPStatus.NOT_FOUND,
            )
            return
        if path == "/api/hub/v1/work/requests":
            try:
                self._handle_exp_live_work_request()
            except HubCreditAuthorizationError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.FORBIDDEN)
            except StableHubWorkerSessionError as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        super().do_POST()


class ExperimentalFoundationDbHubHttpServer(HubHttpServer):
    """Manual-only Hub clone that keeps shared hub state in FoundationDB."""

    def __init__(
        self,
        server_address: tuple[str, int],
        config: MainComputerConfig,
        *,
        fdb_config: ExperimentalFoundationDbConfig,
        stable_topology: StableHubTopology | None = None,
        stable_hub_id: str = "",
        verbose: bool = True,
    ) -> None:
        super().__init__(server_address, config, verbose=verbose)
        diagnostics_value = str(os.environ.get("HUB_WORKER_ROUTE_DIAGNOSTICS", "0")).strip().lower()
        self.worker_route_diagnostics = verbose and diagnostics_value in {"1", "true", "yes", "on"}
        self.RequestHandlerClass = ExperimentalFoundationDbHubServerHandler
        self.stable_topology = stable_topology or build_experimental_stable_topology(
            argparse.Namespace(host=server_address[0], hub_url=""),
            config=config,
            fdb_config=fdb_config,
            port=int(server_address[1]),
        )
        self.stable_hub_node = self.stable_topology.hub_by_id(
            stable_hub_id or self.stable_topology.hubs[0].hub_id
        )
        self.identity = build_experimental_hub_identity(self.stable_topology, self.stable_hub_node.hub_id)
        self.live_worker_sessions: dict[str, LiveWorkerSession] = {}
        self.live_worker_sessions_lock = threading.Lock()
        self.fdb_state = ExperimentalFoundationDbHubState(fdb_config)
        self.stable_worker_session_store = FoundationDbStableWorkerSessionStore(
            cluster_file=fdb_config.cluster_file,
            namespace=_exp_fdb_stable_namespace(fdb_config.namespace),
            api_version=fdb_config.api_version,
            repo_root=fdb_config.repo_root,
        )
        self.stable_worker_session_directory = StableHubWorkerSessionDirectory(
            topology=self.stable_topology,
            hub_id=self.stable_hub_node.hub_id,
            store=self.stable_worker_session_store,
        )
        self.stable_worker_market_directory = StableHubWorkerMarketDirectory(
            topology=self.stable_topology,
            hub_id=self.stable_hub_node.hub_id,
            store=self.stable_worker_session_store,
        )
        self.exp_accepted_work_session_directory = StableHubAcceptedWorkSessionDirectory(
            topology=self.stable_topology,
            hub_id=self.stable_hub_node.hub_id,
            store=self.stable_worker_session_store,
        )
        self.registry = ExperimentalFoundationDbRegistry(
            self.fdb_state,
            root=self.hub_root,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
        )
        self.energy_ledger = ExperimentalFoundationDbEnergyCreditLedger(
            self.fdb_state,
            root=self.hub_root / "energy_credits",
        )
        self.credit_ledger = ExperimentalFoundationDbCreditLedger(fdb_config)
        self.credit_indexer = HubCreditIndexer(self.credit_ledger)
        self.credit_bridge_completion = HubCreditBridgeCompletionService(self.credit_ledger, config)
        self.request_store = ExperimentalFoundationDbRequestStateStore(self.fdb_state)
        self.quote_store = ExperimentalFoundationDbQuoteStateStore(self.fdb_state)
        self.feedback_store = ExperimentalFoundationDbFeedbackStateStore(self.fdb_state)
        self.secure_session_store = ExperimentalFoundationDbSecureSessionStore(self.fdb_state)
        self.multisession_key_store = ExperimentalFoundationDbMultiSessionKeyStore(self.fdb_state)
        self.dispatcher = HubDispatcher(
            self.registry,
            self.energy_ledger,
            timeout_s=config.hub_timeout_s,
            allow_insecure_dev_network=config.hub_allow_insecure_dev_network,
            credit_ledger=self.credit_ledger,
            default_credits_per_request=config.hub_credits_per_request,
            request_store=self.request_store,
            quote_store=self.quote_store,
            secure_session_store=self.secure_session_store,
            feedback_store=self.feedback_store,
        )

    def register_live_worker_session(self, session: LiveWorkerSession) -> None:
        with self.live_worker_sessions_lock:
            self.live_worker_sessions[session.connection_id] = session

    def get_live_worker_session(self, connection_id: str) -> LiveWorkerSession | None:
        with self.live_worker_sessions_lock:
            return self.live_worker_sessions.get(str(connection_id))

    def remove_live_worker_session(self, connection_id: str) -> LiveWorkerSession | None:
        with self.live_worker_sessions_lock:
            return self.live_worker_sessions.pop(str(connection_id), None)




def _default_dev_chain_deployment_path(*, repo_root: Path, network_key: str) -> Path:
    clean_network = str(network_key or "dev").strip() or "dev"
    return repo_root / "runtime" / "deployments" / clean_network / "latest.json"


def _default_contracts_path(*, repo_root: Path, network_key: str) -> Path:
    return contract_config_path(str(network_key or "dev").strip() or "dev", repo_root=repo_root)


def _hub_bridge_backend_from_args(args: argparse.Namespace, base: MainComputerConfig) -> str:
    return str(args.bridge_backend or base.hub_bridge_backend or DEFAULT_HUB_BRIDGE_BACKEND).strip().lower() or DEFAULT_HUB_BRIDGE_BACKEND

def build_experimental_config(args: argparse.Namespace, *, port: int) -> tuple[MainComputerConfig, ExperimentalFoundationDbConfig]:
    base = MainComputerConfig.from_env()
    repo_root = _repo_root_from_args(args)
    hub_root = Path(args.hub_root) if args.hub_root else DEFAULT_EXP_FDB_HUB_ROOT
    if not hub_root.is_absolute():
        hub_root = repo_root / hub_root

    hub_url = args.hub_url or f"http://{args.host}:{port}"
    bridge_backend = _hub_bridge_backend_from_args(args, base)
    network_key = _exp_fdb_network_key_from_args(args, bridge_backend=bridge_backend)
    allow_missing_bridge_signer = bool(getattr(args, "allow_missing_bridge_signer", False)) or base.hub_allow_missing_bridge_signer
    dev_chain_deployment_path = Path(args.dev_chain_deployment_path) if args.dev_chain_deployment_path else base.hub_dev_chain_deployment_path
    if dev_chain_deployment_path is not None and not dev_chain_deployment_path.is_absolute():
        dev_chain_deployment_path = repo_root / dev_chain_deployment_path
    contracts_path = Path(args.contracts_path) if getattr(args, "contracts_path", None) else base.hub_contracts_path
    if contracts_path is None and bridge_backend not in {"mock", "mock-chain", "mock-chain-lite"}:
        contracts_path = _default_contracts_path(repo_root=repo_root, network_key=network_key)
    if contracts_path is not None and not contracts_path.is_absolute():
        contracts_path = repo_root / contracts_path
    if (
        not allow_missing_bridge_signer
        and bridge_backend not in {"mock", "mock-chain", "mock-chain-lite"}
        and contracts_path is not None
        and contracts_path.exists()
        and (dev_chain_deployment_path is None or not dev_chain_deployment_path.exists())
    ):
        # Remote contract-aware Hub images carry public contract-address config
        # but normally do not mount a private bridge signer bundle.  Infer the
        # safe read/status-only mode here as a runtime fallback so stale Coolify
        # start commands that still mention the default missing signer path do
        # not crash before the deployer can repair the command.
        allow_missing_bridge_signer = True
    if (
        dev_chain_deployment_path is None
        and bridge_backend not in {"mock", "mock-chain", "mock-chain-lite"}
        and not allow_missing_bridge_signer
    ):
        dev_chain_deployment_path = _default_dev_chain_deployment_path(repo_root=repo_root, network_key=network_key)
    enable_smoke_bridge = bool(getattr(args, "enable_smoke_bridge", False)) or base.hub_enable_smoke_bridge
    if (
        not enable_smoke_bridge
        and not bool(getattr(args, "strict_bridge_signer", False))
        and _is_exp_dev_chain_backend(bridge_backend)
        and _deployment_manifest_is_smoke_bridge(dev_chain_deployment_path)
    ):
        enable_smoke_bridge = True
    ring_config_path = Path(args.ring_config_path) if getattr(args, "ring_config_path", None) else base.hub_ring_config_path
    if ring_config_path is not None and not ring_config_path.is_absolute():
        ring_config_path = repo_root / ring_config_path

    network_display_name = (
        str(getattr(args, "network_display_name", "Experimental FDB Hub") or "Experimental FDB Hub").strip()
        or "Experimental FDB Hub"
    )
    network_kind = str(getattr(args, "network_kind", "experimental") or "experimental").strip() or "experimental"
    chain_id_arg = _optional_int_arg(getattr(args, "chain_id", ""), flag="--chain-id")
    chain_id = chain_id_arg if chain_id_arg is not None else base.chain_id
    chain_rpc_url_arg = str(getattr(args, "chain_rpc_url", "") or "").strip()
    chain_rpc_url = chain_rpc_url_arg or base.chain_rpc_url

    config = replace(
        base,
        hub_root=hub_root,
        hub_bind_host=args.host,
        hub_bind_port=port,
        hub_url=hub_url,
        hub_network=network_key,
        hub_network_display_name=network_display_name,
        hub_network_kind=network_kind,
        hub_allow_insecure_dev_network=True,
        hub_bridge_backend=bridge_backend,
        hub_dev_chain_deployment_path=dev_chain_deployment_path,
        hub_contracts_path=contracts_path,
        hub_allow_missing_bridge_signer=allow_missing_bridge_signer,
        hub_enable_smoke_bridge=enable_smoke_bridge,
        hub_require_multisession_auth=bool(getattr(args, "require_multisession_auth", False)),
        hub_ring_config_path=ring_config_path,
        chain_id=chain_id,
        chain_id_source="arg" if chain_id_arg is not None else base.chain_id_source,
        chain_rpc_url=chain_rpc_url,
        chain_rpc_url_source="arg" if chain_rpc_url_arg else base.chain_rpc_url_source,
    )

    cluster_file = _cluster_file_from_args(args, repo_root=repo_root)

    fdb_config = ExperimentalFoundationDbConfig(
        cluster_file=cluster_file,
        namespace=args.namespace,
        api_version=args.api_version,
        repo_root=repo_root,
        activate_native_client=not args.no_activate_cached_native_client,
    )
    return config, fdb_config


def parse_ports(value: str | int | Sequence[int] | None, *, default: int = DEFAULT_EXP_FDB_HUB_PORT) -> list[int]:
    if value is None or value == "":
        return [int(default)]
    if isinstance(value, int):
        raw_parts = [str(value)]
    elif isinstance(value, str):
        raw_parts = [part.strip() for part in value.split(",")]
    else:
        raw_parts = [str(part).strip() for part in value]
    ports: list[int] = []
    for raw in raw_parts:
        if not raw:
            continue
        try:
            port = int(raw)
        except Exception as exc:
            raise SystemExit(f"invalid port value {raw!r}") from exc
        if port <= 0 or port > 65535:
            raise SystemExit(f"invalid port value {port}; expected 1..65535")
        if port not in ports:
            ports.append(port)
    if not ports:
        ports.append(int(default))
    return ports


def docker_hub_base_urls(args: argparse.Namespace, live_ports: Sequence[int]) -> list[str]:
    ports = parse_ports(args.docker_ports, default=live_ports[0]) if args.docker_ports else list(live_ports)
    host = str(args.docker_hub_host or DEFAULT_EXP_FDB_DOCKER_HUB_HOST).strip()
    if not host:
        host = DEFAULT_EXP_FDB_DOCKER_HUB_HOST
    return [f"http://{host}:{port}" for port in ports]



def _repo_root_from_args(args: argparse.Namespace) -> Path:
    return Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()


def _cluster_file_from_args(args: argparse.Namespace, *, repo_root: Path) -> Path:
    cluster_file = Path(args.cluster_file)
    if not cluster_file.is_absolute():
        cluster_file = repo_root / cluster_file
    return cluster_file.resolve()


def _docker_container_running(docker_command: str, container_name: str) -> bool | None:
    runtime = resolve_container_runtime(
        container_command=legacy_docker_command_override(docker_command),
        probe=False,
    )
    try:
        result = subprocess.run(
            runtime.container_args("inspect", "--format={{.State.Running}}", container_name),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None

    if result.returncode != 0:
        return False
    return result.stdout.strip().lower() == "true"


def _should_autostart_foundationdb_smoke(args: argparse.Namespace, *, repo_root: Path, cluster_file: Path) -> bool:
    if args.no_fdb_autostart:
        return False
    if not cluster_file.exists():
        return True

    default_cluster_file = (repo_root / DEFAULT_EXP_FDB_CLUSTER_FILE).resolve()
    if cluster_file != default_cluster_file:
        return False

    running = _docker_container_running(args.fdb_docker_command, args.fdb_container_name)
    return running is False


def ensure_foundationdb_smoke_loaded(args: argparse.Namespace) -> None:
    """Start/reuse the local smoke FoundationDB Docker cluster before hub startup.

    This keeps the manual exp-FDB command self-contained for the default local
    Docker cluster path.  The smoke uses its own namespace and clears that
    namespace afterwards; it does not clear the experiment namespace.
    """

    repo_root = _repo_root_from_args(args)
    cluster_file = _cluster_file_from_args(args, repo_root=repo_root)
    if not _should_autostart_foundationdb_smoke(args, repo_root=repo_root, cluster_file=cluster_file):
        return

    smoke_script = repo_root / DEFAULT_EXP_FDB_SMOKE_SCRIPT
    if not smoke_script.exists():
        raise SystemExit(f"FoundationDB smoke script not found: {smoke_script}")

    command = [
        sys.executable,
        str(smoke_script),
        "--cluster-file",
        str(cluster_file),
        "--api-version",
        str(args.api_version),
        "--namespace",
        DEFAULT_EXP_FDB_SMOKE_NAMESPACE,
        "--concurrent-holds",
        "11",
        "--workers",
        "2",
        "--fdb-container-name",
        str(args.fdb_container_name),
        "--fdb-port",
        str(int(args.fdb_port)),
        "--fdb-docker-image",
        str(args.fdb_docker_image),
        "--docker-command",
        _container_command_display(
            resolve_container_runtime(
                container_command=legacy_docker_command_override(args.fdb_docker_command),
                probe=False,
            ).container_command
        ),
        "--docker-start-timeout",
        str(float(args.fdb_docker_start_timeout)),
        "--keep-container",
        "--reuse-container",
    ]
    if args.fdb_docker_platform:
        command.extend(["--docker-platform", str(args.fdb_docker_platform)])

    print("FoundationDB Docker cluster is not loaded; starting/reusing the local smoke container.")
    print(f"FDB smoke container: {args.fdb_container_name}")
    print(f"FDB cluster file: {cluster_file}")
    print("FDB bootstrap command:")
    print("  " + " ".join(command))
    result = subprocess.run(command, cwd=str(repo_root))
    if result.returncode != 0:
        raise SystemExit(result.returncode)



def create_exp_fdb_hub_server(args: argparse.Namespace, *, port: int) -> ExperimentalFoundationDbHubHttpServer:
    config, fdb_config = build_experimental_config(args, port=port)
    if not fdb_config.cluster_file.exists():
        raise SystemExit(
            f"FoundationDB cluster file not found: {fdb_config.cluster_file}\n"
            "Start the local FDB container first, for example:\n"
            "  python scripts/smoke_foundationdb_credit_ledger_primitives.py --keep-container"
        )

    stable_topology = build_experimental_stable_topology(
        args,
        config=config,
        fdb_config=fdb_config,
        port=port,
    )
    server = ExperimentalFoundationDbHubHttpServer(
        (args.host, port),
        config,
        fdb_config=fdb_config,
        stable_topology=stable_topology,
        stable_hub_id=_exp_fdb_hub_id_for_topology(args, stable_topology, port=int(port)),
        verbose=not args.noverbose,
    )
    fdb_health = server.credit_ledger.health_check()
    state_health = server.fdb_state.health_check()

    print(f"Experimental FDB hub server: http://{args.host}:{server.server_port}")
    print("Manual-only: this entry point is not part of normal Main Computer startup.")
    print(f"Hub runtime: {server.hub_root}")
    print(f"Hub admin/control site: http://{args.host}:{server.server_port}/admin")
    print(f"Hub security: high-security={config.hub_high_security} profile={HUB_SECURITY_PROFILE}; local experimental mode allows insecure dev network")
    print(f"Worker route diagnostics: {'on' if server.worker_route_diagnostics else 'off'} (set HUB_WORKER_ROUTE_DIAGNOSTICS=1 to enable per-stage logging)")
    print(f"FDB cluster file: {fdb_config.cluster_file}")
    print(f"FDB namespace: {fdb_config.namespace}")
    print(f"Stable live-session identity: {server.stable_hub_node.hub_id} {server.stable_hub_node.hub_url}")
    print("Stable live-session topology: owner-hub forwarding enabled when topology exposes peer Hubs")
    ring_status = server.ring_admission_config.public_status()
    print(
        "Ring admission config: "
        f"path={ring_status.get('ring_config_path')} "
        f"default_min_ring={ring_status.get('ring_config_default_min_ring')} "
        f"allowlisted_wallet_count={ring_status.get('ring_config_allowlisted_wallet_count')} "
        f"hash={ring_status.get('ring_config_hash')}"
    )
    print(f"FDB credit ledger health: {fdb_health}")
    print(f"FDB hub state health: {state_health}")
    bridge_backend_status = getattr(server.bridge_backend, "status", lambda: {"backend": config.hub_bridge_backend})()
    print(f"Hub bridge backend: {bridge_backend_status}")
    return server



def _ensure_scheduler_lab_run_id(args: argparse.Namespace) -> str:
    existing = str(getattr(args, "scheduler_lab_run_id", "") or "").strip()
    if existing:
        return existing
    run_id = f"scheduler-e2e-{uuid.uuid4().hex[:12]}"
    setattr(args, "scheduler_lab_run_id", run_id)
    return run_id


def _effective_scheduler_lab_ring(args: argparse.Namespace) -> int | None:
    explicit = getattr(args, "scheduler_lab_ring", None)
    if explicit is not None:
        return int(explicit)
    if bool(getattr(args, "payout_lab", False)) and str(getattr(args, "payout_lab_source", "seeded") or "seeded") == "hub-earned-credits":
        return 3
    return None


def launch_scheduler_lab_docker(args: argparse.Namespace, *, hub_base_urls: Sequence[str]) -> subprocess.Popen[bytes]:
    repo_root = _repo_root_from_args(args)
    compose_file = Path(args.docker_compose_file or DEFAULT_EXP_FDB_DOCKER_COMPOSE_FILE)
    if not compose_file.is_absolute():
        compose_file = repo_root / compose_file
    compose_file = compose_file.resolve()
    if not compose_file.exists():
        raise SystemExit(f"scheduler-lab docker compose file not found: {compose_file}")

    output_dir = Path(args.docker_output_dir or DEFAULT_EXP_FDB_DOCKER_OUTPUT_DIR)
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    hub_urls_arg = ",".join(str(url).rstrip("/") for url in hub_base_urls)
    container_output = DEFAULT_EXP_FDB_DOCKER_CONTAINER_OUTPUT_DIR
    total_nodes = int(args.nodes if args.nodes is not None else args.docker_total)
    env = os.environ.copy()
    env.update(
        {
            "HUB_BASE_URLS": hub_urls_arg,
            "HUB_BASE_URL": str(hub_base_urls[0]).rstrip("/"),
            "LAB_OUTPUT_DIR": container_output,
            "LAB_NODE_LIST": f"{container_output}/{total_nodes}-exp-fdb-nodes.jsonl",
            "LAB_RUN_ID": _ensure_scheduler_lab_run_id(args),
            "LAB_OUTPUT_DIR_HOST": str(output_dir),
            "GENERATE_NODE_LIST": "1",
            "LAB_ROLE": str(args.docker_role),
            "LAB_TOTAL": str(total_nodes),
            "LAB_NODES": str(total_nodes),
            "LAB_WORKTIME": str(args.worktime or ""),
            "LAB_FUNDED": str(args.funded),
            "LAB_REQUEST_STARTUP_MODE": str(args.request_startup_mode),
            "LAB_REQUEST_STARTUP_SPREAD_SECONDS": str(float(args.request_startup_spread_seconds)),
            "LAB_EXECUTION_MODE": str(args.lab_execution),
            "LAB_WARM": str(args.warm or ""),
            "B2B_FAILURES": str(int(args.b2bfailures)),
            "HTTP_TIMEOUT_SECONDS": str(float(args.http_timeout_seconds)),
            "LEASE_SECONDS": str(float(args.lease_seconds)),
        }
    )
    if args.docker_duration_seconds is not None:
        env["LAB_DURATION_SECONDS"] = str(float(args.docker_duration_seconds))
    else:
        # Compose files may use their own ${LAB_DURATION_SECONDS:-300} fallback.
        # Pass an explicit nonnumeric sentinel so the worker-lab process can
        # bypass compose defaults and derive from LAB_WORKTIME/default minimum.
        env["LAB_DURATION_SECONDS"] = "auto"
    if args.forced_alive is not None:
        env["FORCED_ALIVE_SECONDS"] = str(float(args.forced_alive))
    else:
        # Same pattern for compose defaults: the worker-lab treats this as
        # "derive from the resolved observation window".
        env["FORCED_ALIVE_SECONDS"] = "duration"

    scheduler_ring = _effective_scheduler_lab_ring(args)
    if scheduler_ring is not None:
        env["LAB_RING"] = str(int(scheduler_ring))

    if args.docker_workers is not None:
        env["LAB_WORKERS"] = str(args.docker_workers)
    else:
        env.pop("LAB_WORKERS", None)
    if args.docker_requesters is not None:
        env["LAB_REQUESTERS"] = str(args.docker_requesters)
    else:
        env.pop("LAB_REQUESTERS", None)

    command = resolve_container_runtime(cwd=repo_root, probe=False).compose_args(
        "-f",
        str(compose_file),
        "--profile",
        "worker-lab",
        "up",
        "--abort-on-container-exit",
        "--exit-code-from",
        "worker-lab",
    )
    if not args.no_docker_build:
        command.append("--build")
    command.append("worker-lab")

    print("Starting scheduler lab Docker side with the dedicated lightweight worker-lab compose stack.")
    print(f"Compose file: {compose_file}")
    print(f"Host output dir: {output_dir}")
    print("Non-sticky hub URLs advertised to Docker:")
    for url in hub_base_urls:
        print(f"  {url}")
    print(f"Scheduler lab nodes: {total_nodes}")
    print(f"Scheduler lab run id: {_ensure_scheduler_lab_run_id(args)}")
    if _effective_scheduler_lab_ring(args) is not None:
        print(f"Scheduler lab ring: {int(_effective_scheduler_lab_ring(args) or 0)}")
    print(f"Scheduler lab assumed already-funded accounts: {args.funded:g}%")
    print(f"Scheduler lab execution mode: {args.lab_execution}")
    print(f"Scheduler lab request startup: {args.request_startup_mode} over {float(args.request_startup_spread_seconds):g}s")
    print(f"Scheduler lab warm-up delay: {args.warm or 'none'}")
    print(f"Scheduler lab b2b transport failure limit: {int(args.b2bfailures)}")
    if args.docker_duration_seconds is None:
        print("Scheduler lab duration seconds: derived by worker-lab from worktime/default minimum")
    else:
        print(f"Scheduler lab duration seconds: {float(args.docker_duration_seconds):g}")
    if args.forced_alive is None:
        print("Scheduler lab forced-alive grace seconds: derived from resolved worker-lab observation duration")
    else:
        print(f"Scheduler lab forced-alive grace seconds: {float(args.forced_alive):g}")
    print(f"Scheduler lab HTTP timeout seconds: {float(args.http_timeout_seconds):g}")
    if args.worktime:
        print(f"Scheduler lab worker result runtime: {args.worktime} (seconds; sigma is standard deviation)")
    print(f"Scheduler lab lease seconds: {float(args.lease_seconds):g}")
    print("Container command:")
    print("  " + " ".join(command))
    return subprocess.Popen(command, cwd=str(repo_root), env=env)



def _payout_lab_namespace_from_args(args: argparse.Namespace, *, run_id: str) -> str:
    explicit_namespace = str(getattr(args, "payout_lab_namespace", "") or "").strip()
    if explicit_namespace:
        return explicit_namespace
    base_namespace = str(getattr(args, "namespace", "") or DEFAULT_EXP_FDB_NAMESPACE).strip() or DEFAULT_EXP_FDB_NAMESPACE
    if str(getattr(args, "payout_lab_source", "seeded") or "seeded") == "hub-earned-credits":
        return base_namespace
    suffix = run_id
    if suffix.startswith("payout-lab-"):
        suffix = suffix[len("payout-lab-") :]
    return f"{base_namespace}-payout-lab-{suffix}"


def _payout_lab_output_dir_from_args(args: argparse.Namespace, *, repo_root: Path) -> Path:
    output_dir = Path(getattr(args, "payout_lab_output_dir", None) or Path(args.docker_output_dir or DEFAULT_EXP_FDB_DOCKER_OUTPUT_DIR) / "payout-lab")
    if not output_dir.is_absolute():
        output_dir = repo_root / output_dir
    return output_dir.resolve()



def _record_scheduler_lab_run_id(record: object) -> str:
    payload = getattr(record, "request_payload", {})
    metadata = dict(payload.get("metadata", {}) if isinstance(payload, dict) and isinstance(payload.get("metadata"), dict) else {})
    return str(metadata.get("scheduler_lab_run_id", "") or "").strip()


def _wait_for_scheduler_lab_activity(args: argparse.Namespace, *, hub_server: object | None) -> bool:
    run_id = str(getattr(args, "scheduler_lab_run_id", "") or "").strip()
    if not run_id or hub_server is None:
        return False
    request_store = getattr(hub_server, "request_store", None)
    if request_store is None or not hasattr(request_store, "list"):
        return False

    deadline = time.time() + max(0.0, float(getattr(args, "payout_lab_source_wait_seconds", 0.0) or 0.0))
    poll_seconds = max(0.05, float(getattr(args, "payout_lab_source_poll_seconds", 0.25) or 0.25))
    while True:
        try:
            records = request_store.list(limit=500)
        except Exception:
            records = []
        for record in records:
            if _record_scheduler_lab_run_id(record) == run_id:
                return True
        if time.time() >= deadline:
            return False
        time.sleep(poll_seconds)


def _require_probe_response(label: str, response: object) -> dict[str, object]:
    status = int(getattr(response, "status", 0) or 0)
    payload = getattr(response, "payload", {})
    ok = bool(getattr(response, "ok", False))
    if not ok:
        raise RuntimeError(f"payout e2e probe {label} failed: status={status} payload={payload!r}")
    return dict(payload or {}) if isinstance(payload, dict) else {}


def _run_payout_worker_earning_e2e_probe(args: argparse.Namespace) -> None:
    """Create one current-run worker earning through normal Hub HTTP routes.

    The scheduler lab is intentionally stochastic, so this deterministic probe is
    the end-to-end proof point: paid requester account -> worker-pull request ->
    worker lease -> worker result -> FDB WorkerEarning tagged with the current
    scheduler run id.  The payout lab then drains that WorkerEarning through the
    backend payout/settlement path.
    """

    from tools.scheduler_lab.hub_client import HubClient

    run_id = str(getattr(args, "scheduler_lab_run_id", "") or "").strip()
    if not run_id:
        raise RuntimeError("payout e2e probe requires scheduler_lab_run_id")
    hub_base_url = str(getattr(args, "payout_lab_hub_base_url", "") or "").strip().rstrip("/")
    if not hub_base_url:
        raise RuntimeError("payout e2e probe requires payout_lab_hub_base_url")

    suffix = "".join(ch if ch.isalnum() else "-" for ch in run_id)[-24:] or uuid.uuid4().hex[:12]
    worker_node_id = f"payout-e2e-worker-{suffix}"
    requester_node_id = f"payout-e2e-requester-{suffix}"
    account_id = f"payout-e2e-account-{suffix}"

    worker_node = {
        "node_id": worker_node_id,
        "kind": "worker",
        "model": "mock-ai-model-phase9",
        "models_json": json.dumps(["mock-ai-model-phase9"]),
        "min_accepted_credits": 1,
        "offered_credits": 2,
        "max_concurrency": 1,
        "network": str(getattr(args, "network_key", "dev") or "dev"),
        "ring": 3,
        "cohort": "payout-e2e",
        "tags": "payout-e2e,end-to-end",
    }
    requester_node = {
        "node_id": requester_node_id,
        "kind": "requester",
        "model": "mock-ai-model-phase9",
        "models_json": json.dumps(["mock-ai-model-phase9"]),
        "offered_credits": 2,
        "account_id": account_id,
        "network": str(getattr(args, "network_key", "dev") or "dev"),
        "ring": 3,
        "cohort": "payout-e2e",
        "tags": "payout-e2e,end-to-end",
    }

    client = HubClient(
        hub_base_url,
        timeout_seconds=max(1.0, float(getattr(args, "http_timeout_seconds", 5.0) or 5.0)),
        retries=1,
    )
    try:
        print("Payout e2e probe: issuing requester credits through Hub HTTP.")
        _require_probe_response(
            "issue credits",
            client.issue_credits(
                account_id=account_id,
                credits=5,
                memo=f"payout e2e probe funding {run_id}",
                metadata={
                    "payout_e2e_probe": True,
                    "scheduler_lab": True,
                    "scheduler_lab_run_id": run_id,
                },
            ),
        )

        print("Payout e2e probe: registering deterministic worker through Hub HTTP.")
        _require_probe_response("register worker", client.register_worker(worker_node))

        print("Payout e2e probe: submitting worker-pull request through Hub HTTP.")
        request_payload = _require_probe_response(
            "submit request",
            client.submit_request(
                requester_node,
                request_index=1,
                request_mode="worker_pull_v0",
                account_id_prefix="payout-e2e-account",
                prompt=f"payout e2e probe request for {run_id}",
                scheduler_lab_run_id=run_id,
            ),
        )
        request = dict(request_payload.get("request", {}) if isinstance(request_payload.get("request"), dict) else {})
        request_id = str(request.get("request_id", "") or "")
        if not request_id:
            raise RuntimeError(f"payout e2e probe submit request did not return request_id: {request_payload!r}")

        lease: dict[str, object] | None = None
        deadline = time.time() + min(30.0, max(5.0, float(getattr(args, "payout_lab_source_wait_seconds", 30.0) or 30.0)))
        while time.time() < deadline:
            poll_payload = _require_probe_response(
                "poll worker",
                client.poll_worker(worker_node, lease_seconds=float(getattr(args, "lease_seconds", 180.0) or 180.0)),
            )
            candidate = poll_payload.get("lease")
            if isinstance(candidate, dict):
                lease = dict(candidate)
                break
            time.sleep(max(0.05, float(getattr(args, "payout_lab_source_poll_seconds", 0.25) or 0.25)))
        if lease is None:
            raise RuntimeError(f"payout e2e probe worker did not receive a lease for request {request_id}")

        print("Payout e2e probe: submitting deterministic worker result through Hub HTTP.")
        _require_probe_response(
            "submit worker result",
            client.submit_worker_result(
                worker_node,
                lease,
                {
                    "status": "success",
                    "content": f"payout e2e probe result for {request_id}",
                    "provider": "scheduler-lab",
                    "model": "mock-ai-model-phase9",
                    "metadata": {
                        "payout_e2e_probe": True,
                        "scheduler_lab": True,
                        "scheduler_lab_run_id": run_id,
                        "worker_node_id": worker_node_id,
                        "lease_id": lease.get("lease_id"),
                    },
                },
            ),
        )
        print(f"Payout e2e probe: worker earning created for run {run_id}.")
    finally:
        try:
            client.close()
        except Exception:
            pass


def _prepare_hub_earned_payout_source(args: argparse.Namespace) -> None:
    if str(getattr(args, "payout_lab_source", "seeded") or "seeded") != "hub-earned-credits":
        return

    hub_server = getattr(args, "payout_lab_hub_server", None)
    if hub_server is not None:
        print("Payout e2e probe: waiting for current scheduler-lab activity.")
        if not _wait_for_scheduler_lab_activity(args, hub_server=hub_server):
            raise RuntimeError(
                "payout e2e probe did not observe current scheduler-lab request activity "
                f"for run {getattr(args, 'scheduler_lab_run_id', '')!r}"
            )

    _run_payout_worker_earning_e2e_probe(args)



def run_payout_lab_phase(args: argparse.Namespace, *, runner: object | None = None) -> int:
    """Run the optional mock payout settlement smoke lab after the Hub is live.

    This phase is intentionally opt-in.  It reuses the configured FoundationDB
    cluster and credit ledger implementation, but defaults to an isolated
    payout-lab namespace so scheduler-lab and normal Hub state are not polluted.
    The chain/relayer side remains mocked here; real bridge settlement is a
    later, explicit integration step.
    """

    from tools.payout_lab.run_payout_lab import PayoutLabConfig, run_payout_lab

    repo_root = _repo_root_from_args(args)
    cluster_file = _cluster_file_from_args(args, repo_root=repo_root)
    run_id = str(getattr(args, "payout_lab_run_id", "") or f"payout-lab-{uuid.uuid4().hex[:12]}")
    namespace = _payout_lab_namespace_from_args(args, run_id=run_id)
    output_dir = _payout_lab_output_dir_from_args(args, repo_root=repo_root)
    summary_dir = output_dir / run_id
    summary_dir.mkdir(parents=True, exist_ok=True)

    config = PayoutLabConfig(
        backend=str(args.payout_lab_backend),
        source=str(args.payout_lab_source),
        wallets=int(args.payout_lab_wallets),
        starting_credits=int(args.payout_lab_starting_credits),
        requests=int(args.payout_lab_requests),
        concurrency=int(args.payout_lab_concurrency),
        settlement_workers=int(args.payout_lab_settlement_workers),
        max_payout_credits=int(args.payout_lab_max_payout_credits),
        duplicate_rate=float(args.payout_lab_duplicate_rate),
        failure_rate=float(args.payout_lab_failure_rate),
        after_broadcast_crash_rate=float(args.payout_lab_after_broadcast_crash_rate),
        lease_seconds=float(args.payout_lab_lease_seconds),
        settle_timeout_seconds=float(args.payout_lab_settle_timeout_seconds),
        seed=int(args.payout_lab_seed),
        run_id=run_id,
        cluster_file=cluster_file,
        namespace=namespace,
        repo_root=repo_root,
        fdb_api_version=int(args.api_version),
        source_wait_seconds=float(args.payout_lab_source_wait_seconds),
        source_poll_seconds=float(args.payout_lab_source_poll_seconds),
        source_min_accounts=int(args.payout_lab_source_min_accounts),
        source_scheduler_run_id=str(getattr(args, "scheduler_lab_run_id", "") or ""),
    )

    print("Starting optional payout settlement smoke lab.")
    print(f"Payout lab backend: {config.backend}")
    print(f"Payout lab source: {config.source}")
    print(f"Payout lab run id: {config.run_id}")
    print(f"Payout lab FDB namespace: {config.namespace}")
    print(f"Payout lab wallets: {config.wallets}")
    print(f"Payout lab requests: {config.requests}")
    print(f"Payout lab concurrency: {config.concurrency}")
    print(f"Payout lab settlement workers: {config.settlement_workers}")
    if config.source == "hub-earned-credits":
        print(f"Payout lab source wait seconds: {float(config.source_wait_seconds):g}")
        print(f"Payout lab source minimum accounts: {int(config.source_min_accounts)}")
        print(f"Payout lab source scheduler run id: {config.source_scheduler_run_id or '(not set)'}")
    print(f"Payout lab output dir: {summary_dir}")

    if config.source == "hub-earned-credits" and runner is None:
        _prepare_hub_earned_payout_source(args)

    active_runner = runner if runner is not None else run_payout_lab
    summary = active_runner(config)  # type: ignore[misc]
    payload = summary.as_dict()
    print("Payout lab summary:")
    print(json.dumps(payload, indent=2, sort_keys=True))
    summary_path = summary_dir / "summary.json"
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Payout lab summary written: {summary_path}")
    return 0 if bool(getattr(summary, "ok", False)) else 1



def _run_payout_lab_phase_in_thread(
    args: argparse.Namespace,
) -> tuple[threading.Thread, dict[str, object]]:
    """Start the optional payout lab in a host thread while the scheduler lab runs.

    The payout lab is intentionally host-side: it reuses the same FDB cluster
    file and real ledger implementation, while the settlement/chain side remains
    mocked.  Running it concurrently with the scheduler Docker lab makes the
    combined smoke exercise shared FDB pressure at the same time instead of
    merely proving two independent sequential phases.
    """

    result: dict[str, object] = {"return_code": None, "exception": None}

    def _target() -> None:
        try:
            result["return_code"] = int(run_payout_lab_phase(args))
        except Exception as exc:  # pragma: no cover - defensive, exercised by real lab failures.
            result["return_code"] = 1
            result["exception"] = exc
            print(
                f"Optional payout settlement smoke lab failed before producing a clean summary: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )

    thread = threading.Thread(target=_target, name="exp-fdb-payout-lab", daemon=False)
    thread.start()
    return thread, result


def serve_exp_fdb_hubs(args: argparse.Namespace) -> int:
    live_ports = parse_ports(args.ports if args.ports else args.port, default=DEFAULT_EXP_FDB_HUB_PORT)
    ensure_foundationdb_smoke_loaded(args)
    servers = [create_exp_fdb_hub_server(args, port=port) for port in live_ports]
    threads: list[threading.Thread] = []
    docker_process: subprocess.Popen[bytes] | None = None
    payout_thread: threading.Thread | None = None
    payout_result: dict[str, object] | None = None
    try:
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, name=f"exp-fdb-hub-{server.server_port}", daemon=True)
            thread.start()
            threads.append(thread)
        print(f"Experimental FDB hub ports listening: {', '.join(str(port) for port in live_ports)}")
        print(f"Multi-session auth required: {'yes' if getattr(args, 'require_multisession_auth', False) else 'no'}")
        if args.docker:
            _ensure_scheduler_lab_run_id(args)
            hub_urls = docker_hub_base_urls(args, live_ports)
            docker_process = launch_scheduler_lab_docker(args, hub_base_urls=hub_urls)
            if args.payout_lab:
                setattr(args, "payout_lab_hub_base_url", f"http://127.0.0.1:{live_ports[0]}")
                setattr(args, "payout_lab_hub_server", servers[0])
                print("Starting optional payout settlement smoke lab concurrently with scheduler lab.")
                payout_thread, payout_result = _run_payout_lab_phase_in_thread(args)
            return_code = docker_process.wait()
            if payout_thread is not None:
                payout_thread.join()
            if int(return_code) != 0:
                return int(return_code)
            if payout_result is not None:
                return int(payout_result.get("return_code") or 0)
            return 0
        if args.payout_lab:
            return run_payout_lab_phase(args)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            print("\nExperimental FDB hub stopped.")
            return 0
    finally:
        if docker_process is not None and docker_process.poll() is None:
            docker_process.terminate()
            try:
                docker_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                docker_process.kill()
        if payout_thread is not None and payout_thread.is_alive():
            payout_thread.join(timeout=5)
        for server in servers:
            server.shutdown()
        for thread in threads:
            thread.join(timeout=5)
        for server in servers:
            server.server_close()


def serve_exp_fdb_hub(args: argparse.Namespace) -> None:
    raise SystemExit(serve_exp_fdb_hubs(args))

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exp-fdb-hub.py",
        description=(
            "Start a manual-only clone of the Main Computer hub that uses the local "
            "FoundationDB Docker cluster for the compute-credit ledger, worker registry, request queue, quote store, sessions, and payout state."
        ),
    )
    parser.add_argument(
        "--runtime-env-file",
        default="",
        help=(
            "Optional strict KEY=VALUE runtime env file. Loaded before runtime config is built; "
            "explicit CLI flags still override env-derived defaults."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host/interface to bind.")
    parser.add_argument("-ports", "--ports", default=None, help="Comma-separated experimental hub ports to bind, for example 8870,8871,8872. Defaults to 8870.")
    parser.add_argument("--port", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--hub-url", help="Public URL advertised for this experimental hub. Defaults per port when omitted.")
    parser.add_argument("--topology", type=Path, default=None, help="Optional stable-Hub-compatible topology JSON to advertise.")
    parser.add_argument("--hub-id", default="", help="Concrete Hub id from --topology for this process/port. Defaults to the topology Hub whose URL port matches --port.")
    parser.add_argument("--network-key", default="", help="Hub network key advertised by /api/hub/status. Defaults to dev for dev-chain and exp-fdb for mock-chain.")
    parser.add_argument("--network-display-name", default="Experimental FDB Hub", help="Hub network display name advertised by /api/hub/status.")
    parser.add_argument("--network-kind", default="experimental", help="Hub network kind advertised by /api/hub/status.")
    parser.add_argument("--chain-id", default="", help="Chain ID advertised by /api/hub/status. Defaults to MAIN_COMPUTER_CHAIN_ID/base config.")
    parser.add_argument("--chain-rpc-url", default="", help="Chain RPC URL advertised by /api/hub/status. Defaults to MAIN_COMPUTER_CHAIN_RPC_URL/base config.")
    parser.add_argument("--hub-root", type=Path, default=DEFAULT_EXP_FDB_HUB_ROOT, help="Separate runtime root for the experimental hub.")
    parser.add_argument("--cluster-file", type=Path, default=DEFAULT_EXP_FDB_CLUSTER_FILE, help="FoundationDB cluster file written by the FDB smoke.")
    parser.add_argument("--bridge-backend", choices=["mock-chain", "dev-chain", "credit-bridge-contract"], default=None, help="Hub bridge backend for bridge confirm endpoints. Defaults to dev-chain/contract-backed mode; use mock-chain only for explicit labs.")
    parser.add_argument("--dev-chain-deployment-path", type=Path, default=None, help="Private deployment metadata JSON used for bridge signing wallet paths. Defaults to runtime/deployments/<network-key>/latest.json in contract mode unless signer-disabled startup is allowed.")
    parser.add_argument("--contracts-path", type=Path, default=None, help="Public contract discovery JSON. Defaults to main_computer/config/<network-key>_contracts.json when contract mode is selected.")
    parser.add_argument(
        "--allow-missing-bridge-signer",
        action="store_true",
        help="Allow contract-aware Hub startup from public contract config when private bridge signer metadata is not mounted.",
    )
    parser.add_argument(
        "--enable-smoke-bridge",
        action="store_true",
        help="Enable explicit admin-only smoke bridge mode that may load smoke_client wallet metadata from a private deployment manifest.",
    )
    parser.add_argument(
        "--strict-bridge-signer",
        action="store_true",
        help=(
            "Do not auto-enable the local dev-chain smoke bridge for exp-fdb-hub.py. "
            "Use this when the dev-chain backend must require a non-smoke bridge signer bundle."
        ),
    )
    parser.add_argument("--ring-config-path", type=Path, default=None, help="JSON ring admission config path. Bad explicit configs fail startup.")
    parser.add_argument(
        "--require-multisession-auth",
        action="store_true",
        help=(
            "Require multi-session key authorization on wallet-backed requester and worker Hub routes. "
            "Use this for local golden-path MSK tests against the dev chain."
        ),
    )
    parser.add_argument("--namespace", default=DEFAULT_EXP_FDB_NAMESPACE, help="FDB tuple namespace for this experiment.")
    parser.add_argument("--api-version", type=int, default=740, help="FoundationDB API version to request.")
    parser.add_argument("--repo-root", type=Path, help="Repository root. Defaults to the current working directory.")
    parser.add_argument("-docker", "--docker", action="store_true", help="After starting the requested exp hub ports, run the lightweight scheduler-lab Docker stack with the same advertised hub-port list.")
    parser.add_argument("--docker-compose-file", type=Path, default=DEFAULT_EXP_FDB_DOCKER_COMPOSE_FILE, help="Scheduler-lab Docker Compose file to run when --docker is set.")
    parser.add_argument("--docker-ports", default="", help="Comma-separated ports advertised to Docker workers. May include intentionally dead ports not present in --ports.")
    parser.add_argument("--docker-hub-host", default=DEFAULT_EXP_FDB_DOCKER_HUB_HOST, help="Host name Docker containers use to reach the host exp hubs.")
    parser.add_argument("--docker-role", choices=["all", "workers", "requesters"], default="all", help="Scheduler lab role to run in Docker.")
    parser.add_argument("--scheduler-lab-ring", type=int, default=None, help="Optional ring value for generated scheduler-lab nodes. Defaults to 3 for hub-earned payout end-to-end mode; otherwise worker-lab default applies.")
    parser.add_argument("--scheduler-lab-run-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--docker-total", type=int, default=120, help="Total generated scheduler lab nodes. Kept for compatibility; --nodes is preferred.")
    parser.add_argument("--nodes", type=int, default=None, help="Total generated scheduler lab nodes for Docker experiments, for example --nodes 1000.")
    parser.add_argument("--docker-workers", type=int, default=None, help="Optional worker-capable generated node count.")
    parser.add_argument("--docker-requesters", type=int, default=None, help="Optional requester-capable generated node count.")
    parser.add_argument(
        "--docker-duration-seconds",
        type=float,
        default=None,
        help="Explicit scheduler lab Docker observation duration. If omitted, the worker-lab derives max(900, 3*worktime_mu + worktime_sigma).",
    )
    parser.add_argument(
        "--worktime",
        default="",
        help="Optional Docker worker result-runtime distribution in seconds, e.g. --worktime 100mu,30sigma where sigma is standard deviation.",
    )
    parser.add_argument(
        "--funded",
        type=float,
        default=0.0,
        help="Percent of generated Docker lab node accounts to assume are already funded in FDB; accepts 90 or 0.9.",
    )
    parser.add_argument(
        "--request-startup-mode",
        choices=["auto", "natural", "surge"],
        default="auto",
        help="How Docker lab request-capable nodes begin traffic. auto surges when --funded is nonzero.",
    )
    parser.add_argument(
        "--request-startup-spread-seconds",
        type=float,
        default=0.0,
        help="Legacy async-lab request-surge spread. Process mode uses --warm for node readiness; use 0 for an immediate wall.",
    )
    parser.add_argument("--lab-execution", choices=["process", "async"], default="process", help="Docker lab execution model. process starts one OS child process per node.")
    parser.add_argument("--warm", default="", help="Docker node warm-up delay distribution in seconds before first hub contact, e.g. --warm 2mu,1sigma.")
    parser.add_argument("--b2bfailures", type=int, default=10, help="Consecutive transport failures before each node process self-terminates after --forced-alive has elapsed. 0 disables.")
    parser.add_argument(
        "--forced-alive",
        type=float,
        default=None,
        help="Optional seconds each Docker node must stay alive before --b2bfailures can self-terminate it. If omitted, the worker-lab keeps nodes alive for the resolved observation duration.",
    )
    parser.add_argument("--http-timeout-seconds", type=float, default=1.0, help="Short per-attempt hub HTTP timeout used by Docker node processes.")
    parser.add_argument("--lease-seconds", type=float, default=180.0, help="Lease duration advertised by Docker workers when polling. Increase this above expected --worktime.")
    parser.add_argument("--docker-output-dir", type=Path, default=DEFAULT_EXP_FDB_DOCKER_OUTPUT_DIR, help="Repository-relative output directory for scheduler lab events.")
    parser.add_argument("--no-docker-build", action="store_true", help="Do not pass --build to docker compose up.")
    parser.add_argument(
        "--payout-lab",
        action="store_true",
        help="After the experimental Hub starts, run the optional mock payout settlement smoke lab.",
    )
    parser.add_argument("--payout-lab-backend", choices=["memory", "fdb"], default="fdb", help="Payout lab backend. fdb reuses the configured local FDB cluster.")
    parser.add_argument(
        "--payout-lab-source",
        choices=["seeded", "hub-earned-credits"],
        default="seeded",
        help="Payout lab credit source. seeded keeps the old isolated synthetic namespace; hub-earned-credits consumes scheduler-created account balances from the Hub namespace.",
    )
    parser.add_argument("--payout-lab-wallets", type=int, default=8, help="Number of synthetic payout lab wallets/accounts to seed.")
    parser.add_argument("--payout-lab-starting-credits", type=int, default=100, help="Initial whole-credit balance per payout lab account.")
    parser.add_argument("--payout-lab-requests", type=int, default=200, help="Concurrent payout request attempts for the payout lab.")
    parser.add_argument("--payout-lab-concurrency", type=int, default=32, help="Concurrent client request workers for the payout lab.")
    parser.add_argument("--payout-lab-settlement-workers", type=int, default=4, help="Mock backend settlement worker count for the payout lab.")
    parser.add_argument("--payout-lab-max-payout-credits", type=int, default=10, help="Maximum whole-credit amount per generated payout request.")
    parser.add_argument("--payout-lab-duplicate-rate", type=float, default=0.10, help="Fraction of generated payout requests that reuse a prior idempotency key.")
    parser.add_argument("--payout-lab-failure-rate", type=float, default=0.15, help="Deterministic mock failure rate before broadcast.")
    parser.add_argument("--payout-lab-after-broadcast-crash-rate", type=float, default=0.10, help="Deterministic mock crash rate after broadcast but before local settlement.")
    parser.add_argument("--payout-lab-lease-seconds", type=float, default=0.10, help="Short per-payout mock settlement claim lease.")
    parser.add_argument("--payout-lab-settle-timeout-seconds", type=float, default=60.0, help="Seconds to keep mock settlement workers draining accepted payouts.")
    parser.add_argument("--payout-lab-seed", type=int, default=1337, help="Deterministic payout lab request generation seed.")
    parser.add_argument("--payout-lab-run-id", default="", help="Optional explicit payout lab run id. Defaults to a generated payout-lab-* id.")
    parser.add_argument("--payout-lab-namespace", default="", help="Optional explicit payout lab FDB namespace. Defaults to an isolated namespace derived from --namespace and run id.")
    parser.add_argument("--payout-lab-output-dir", type=Path, default=None, help="Optional payout lab summary output directory. Defaults under --docker-output-dir/payout-lab.")
    parser.add_argument("--payout-lab-source-wait-seconds", type=float, default=30.0, help="Seconds to wait for eligible Hub scheduler-created balances when --payout-lab-source=hub-earned-credits.")
    parser.add_argument("--payout-lab-source-poll-seconds", type=float, default=0.50, help="Polling interval while waiting for Hub scheduler-created payout source balances.")
    parser.add_argument("--payout-lab-source-min-accounts", type=int, default=1, help="Minimum eligible Hub scheduler-created accounts required for --payout-lab-source=hub-earned-credits.")
    parser.add_argument(
        "--no-fdb-autostart",
        action="store_true",
        help=(
            "Do not automatically start/reuse the local FoundationDB smoke Docker container "
            "when the default .foundationdb/docker.cluster path is missing or not loaded."
        ),
    )
    parser.add_argument("--fdb-container-name", default=DEFAULT_EXP_FDB_SMOKE_CONTAINER_NAME, help="Local FoundationDB smoke container name to start/reuse before hub startup.")
    parser.add_argument("--fdb-port", type=int, default=DEFAULT_EXP_FDB_SMOKE_PORT, help="Host/container TCP port for the local FoundationDB smoke database.")
    parser.add_argument("--fdb-docker-image", default=DEFAULT_EXP_FDB_SMOKE_DOCKER_IMAGE, help="FoundationDB Docker image used when auto-starting the local smoke database.")
    parser.add_argument("--fdb-docker-command", default="docker", help="Docker CLI executable used when auto-starting the local FoundationDB smoke database.")
    parser.add_argument("--fdb-docker-platform", default=None, help="Optional Docker platform for the auto-started FoundationDB smoke container, for example linux/amd64.")
    parser.add_argument("--fdb-docker-start-timeout", type=float, default=DEFAULT_EXP_FDB_SMOKE_START_TIMEOUT_SECONDS, help="Seconds to wait for the auto-started FoundationDB smoke database to become configurable.")
    parser.add_argument(
        "--no-activate-cached-native-client",
        action="store_true",
        help="Do not add .foundationdb/native-client to PATH/DLL search path before importing FDB.",
    )
    parser.add_argument(
        "-noverbose",
        "--noverbose",
        action="store_true",
        help="Suppress hub request logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runtime_env_file = str(
        getattr(args, "runtime_env_file", "")
        or os.environ.get("MAIN_COMPUTER_HUB_RUNTIME_ENV_FILE")
        or os.environ.get("MAIN_COMPUTER_RUNTIME_ENV_FILE")
        or ""
    ).strip()
    if runtime_env_file:
        apply_runtime_env_file(runtime_env_file)
    if args.funded < 0:
        raise SystemExit("--funded must be >= 0")
    if args.funded <= 1:
        args.funded *= 100.0
    if args.funded > 100:
        raise SystemExit("--funded must be <= 100")
    if args.request_startup_spread_seconds < 0:
        raise SystemExit("--request-startup-spread-seconds must be >= 0")
    if args.b2bfailures < 0:
        raise SystemExit("--b2bfailures must be >= 0")
    if args.docker_duration_seconds is not None and args.docker_duration_seconds <= 0:
        raise SystemExit("--docker-duration-seconds must be > 0")
    if args.forced_alive is not None and args.forced_alive < 0:
        raise SystemExit("--forced-alive must be >= 0")
    if args.http_timeout_seconds <= 0:
        raise SystemExit("--http-timeout-seconds must be > 0")
    if args.fdb_port <= 0 or args.fdb_port > 65535:
        raise SystemExit("--fdb-port must be 1..65535")
    if args.fdb_docker_start_timeout <= 0:
        raise SystemExit("--fdb-docker-start-timeout must be > 0")
    if args.payout_lab:
        if args.payout_lab_source == "hub-earned-credits" and args.payout_lab_backend != "fdb":
            raise SystemExit("--payout-lab-source=hub-earned-credits requires --payout-lab-backend=fdb")
        if args.payout_lab_wallets <= 0:
            raise SystemExit("--payout-lab-wallets must be > 0")
        if args.payout_lab_starting_credits < 0:
            raise SystemExit("--payout-lab-starting-credits must be >= 0")
        if args.payout_lab_requests < 0:
            raise SystemExit("--payout-lab-requests must be >= 0")
        if args.payout_lab_concurrency <= 0:
            raise SystemExit("--payout-lab-concurrency must be > 0")
        if args.payout_lab_settlement_workers <= 0:
            raise SystemExit("--payout-lab-settlement-workers must be > 0")
        if args.payout_lab_max_payout_credits <= 0:
            raise SystemExit("--payout-lab-max-payout-credits must be > 0")
        for flag, value in [
            ("--payout-lab-duplicate-rate", args.payout_lab_duplicate_rate),
            ("--payout-lab-failure-rate", args.payout_lab_failure_rate),
            ("--payout-lab-after-broadcast-crash-rate", args.payout_lab_after_broadcast_crash_rate),
        ]:
            if value < 0 or value > 1:
                raise SystemExit(f"{flag} must be between 0 and 1")
        if args.payout_lab_lease_seconds <= 0:
            raise SystemExit("--payout-lab-lease-seconds must be > 0")
        if args.payout_lab_settle_timeout_seconds <= 0:
            raise SystemExit("--payout-lab-settle-timeout-seconds must be > 0")
        if args.payout_lab_source_wait_seconds < 0:
            raise SystemExit("--payout-lab-source-wait-seconds must be >= 0")
        if args.payout_lab_source_poll_seconds <= 0:
            raise SystemExit("--payout-lab-source-poll-seconds must be > 0")
        if args.payout_lab_source_min_accounts <= 0:
            raise SystemExit("--payout-lab-source-min-accounts must be > 0")
    if args.request_startup_mode == "auto":
        args.request_startup_mode = "surge" if args.funded > 0 else "natural"
    return serve_exp_fdb_hubs(args)


if __name__ == "__main__":
    raise SystemExit(main())
