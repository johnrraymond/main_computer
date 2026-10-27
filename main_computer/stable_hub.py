from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.credit_units import credit_decimal_text_to_wei, credit_wei_to_decimal_text, credit_wei_to_whole_credits_floor
from main_computer.hub_credit_bridge_completion import (
    HubCreditBridgeCompletionService,
    load_bridge_deployment,
    normalize_evm_address,
)
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.hub_private_https import open_internal_https
from main_computer.stable_hub_msk import (
    StableHubMultiSessionKeyError,
    StableHubMultiSessionKeyService,
    StableHubMultiSessionKeyStore,
    stable_multisession_store_from_topology,
)
from main_computer.stable_hub_temporal import StableHubTemporalRuntime, TemporalLifecycleUnavailable, temporal_address
from main_computer.stable_hub_topology import (
    StableHubNode,
    StableHubTopology,
    StableHubTopologyError,
    load_stable_hub_topology,
    stable_hub_node_to_dict,
    stable_hub_topology_to_dict,
)
from main_computer.stable_hub_worker_sessions import (
    StableHubAcceptedWorkSessionDirectory,
    StableHubPayoutLedgerDirectory,
    StableHubWorkerMarketDirectory,
    StableHubWorkerSessionDirectory,
    StableHubWorkerSessionError,
    StableHubWorkerSessionStore,
    new_connection_id,
    new_run_id,
    new_session_id,
    normalize_request_id,
    normalize_session_id,
    normalize_worker_id,
    normalize_worker_market_profile,
    stable_partition_key_for_work,
    stable_task_queue_for_partition,
    stable_worker_session_store_from_topology,
    worker_market_identity,
)



def settle_disconnected_worker_sessions(
    server: Any, *, worker_id: str, connection_id: str,
) -> int:
    """Fail still-open sessions tied to an irrevocably CLOSED Worker socket.

    A WebSocket-connection-scoped worker cannot deliver a terminal result
    after the connection is closed. Release each unearned payout hold in the
    same FDB atomic transaction as the corresponding terminal session state.
    Never release a successful/charged session or one on a replacement socket.
    This function is called only after the durable owner record was closed.
    """
    sessions = server.accepted_work_session_directory
    payouts = server.payout_ledger_directory
    owner_dir = server.worker_session_directory
    store = payouts.store
    closed_count = 0
    for old in sessions.list_open_sessions_for_worker_connection(
        worker_id=worker_id, connection_id=connection_id,
    ):
        session_id = str(old.get("session_id") or "")

        def _fail_unreachable_session() -> bool:
            owner = owner_dir.get_owner(worker_id)
            if (not isinstance(owner, dict)
                    or str(owner.get("connection_id") or "") != connection_id
                    or str(owner.get("status") or "") != "closed"):
                # A reconnect/replacement must never be mutated by an old
                # socket's close handler.
                return False
            current = sessions.get_session(session_id)
            if (not isinstance(current, dict)
                    or str(current.get("worker_id") or "") != worker_id
                    or str(current.get("worker_connection_id") or "") != connection_id
                    or str(current.get("status") or "") not in {"accepted", "running"}):
                return False
            payout = dict(current.get("payout") or {})
            hold_id = str(payout.get("hold_id") or "")
            if hold_id:
                hold = payouts.store.load().get("payout_holds", {}).get(hold_id)
                if (not isinstance(hold, dict) or hold.get("status") != "held"
                        or str(hold.get("session_id") or "") != session_id):
                    # Missing/charged/ambiguous credit evidence is NOT a
                    # license to synthesize a refund or terminal status.
                    raise StableHubWorkerSessionError("disconnected_session_hold_not_proven_held")
                released = payouts.release_hold(hold_id=hold_id, session_id=session_id)
                payout.update({
                    "hold_status": "released", "payment_status": "released",
                    "released_credits": str(released.get("amount") or "0"),
                    "settlement_status": "not_earned", "direct_spend": False,
                    "charge_id": "", "worker_earning_id": "",
                    "charged_credits": "0",
                })
            sessions.record_failed(
                session_id=session_id, worker_connection_id=connection_id,
                worker_failure={
                    "type": "worker.work.failed",
                    "error": {"reason_code": "worker_connection_closed_before_result"},
                },
                payout=payout,
            )
            return True

        atomic = getattr(store, "atomic", None)
        settled = atomic(_fail_unreachable_session) if callable(atomic) else _fail_unreachable_session()
        if settled:
            closed_count += 1
    return closed_count



def close_worker_connection_and_settle(
    server: Any, *, worker_id: str, connection_id: str,
) -> tuple[dict[str, Any], int]:
    """Close an owner and its unearned sessions in ONE durable transaction.

    A separate owner-close commit followed by a settlement commit permits a
    process crash to strand credit indefinitely. Nested Stable Hub directory
    operations join the outer FoundationDB transaction. A failure aborts all
    changes rather than committing a closed owner with an outstanding hold.
    """
    store = server.worker_session_directory.store
    if (store is not server.worker_market_directory.store
            or store is not server.accepted_work_session_directory.store
            or store is not server.payout_ledger_directory.store):
        raise StableHubWorkerSessionError("worker_disconnect_directories_do_not_share_store")

    def _close_and_settle() -> tuple[dict[str, Any], int]:
        owner = server.worker_session_directory.record_closed(
            worker_id=worker_id, connection_id=connection_id, reason="socket_closed",
        ) or {}
        server.worker_market_directory.record_worker_closed(
            worker_id=worker_id, connection_id=connection_id, reason="socket_closed",
        )
        if (str(owner.get("status") or "") == "closed"
                and str(owner.get("connection_id") or "") == connection_id):
            return owner, settle_disconnected_worker_sessions(
                server, worker_id=worker_id, connection_id=connection_id,
            )
        return owner, 0

    atomic = getattr(store, "atomic", None)
    return atomic(_close_and_settle) if callable(atomic) else _close_and_settle()



def read_stable_hub_bridge_deposit_provenance(
    payout_ledger: StableHubPayoutLedgerDirectory,
    *, chain_id: str, contract_address: str, deposit_id: str, wallet_address: str,
) -> dict[str, Any]:
    """Read exact historical FDB credit evidence; never touch the live chain.

    The Hub ledger persists both verified completion intents and finalized
    credit records in the same atomic store. The key is chain + escrow + deposit;
    a wallet constraint prevents revealing a different account's record. A
    historical FDB record is NOT proof that the deposit exists on today's chain.
    """
    if not re.fullmatch(r"[0-9]{1,19}", str(chain_id or "")) or int(chain_id) <= 0:
        raise ValueError("chain_id must be a positive decimal integer")
    chain = int(chain_id)
    contract = normalize_evm_address(contract_address, field_name="contract_address")
    wallet = normalize_evm_address(wallet_address, field_name="wallet_address")
    deposit = str(deposit_id or "").strip().lower()
    if not re.fullmatch(r"0x[0-9a-f]{64}", deposit):
        raise ValueError("deposit_id must be a 32-byte hex identifier")
    key = f"{chain}:{contract}:{deposit}"
    data = payout_ledger.store.load()
    completed = (data.get("payout_deposits") or {}).get(key)
    intent = (data.get("payout_deposit_intents") or {}).get(key)
    expected_account = wallet_account_id(wallet)
    def owned(record: Any) -> bool:
        return (isinstance(record, dict)
                and str(record.get("account_id") or "") == expected_account
                and str(record.get("wallet_address") or "").lower() == wallet
                and str(record.get("deposit_id") or "").lower() == deposit
                and str(record.get("contract_address") or "").lower() == contract
                and str(record.get("chain_id") or "") == str(chain))
    if not owned(completed):
        completed = None
    if not owned(intent):
        intent = None
    response: dict[str, Any] = {
        "ok": True, "readOnly": True,
        "scope": "historical-hub-fdb-not-current-chain-proof",
        "found": completed is not None or intent is not None,
        "completed": completed is not None,
        "deposit_id": deposit, "chain_id": chain,
        "contract_address": contract, "wallet_address": wallet,
    }
    if completed is not None:
        response["credit"] = {
            "status": str(completed.get("status") or ""),
            "account_id": expected_account,
            "amount_credit_wei": str(completed.get("deposit_amount_units") or ""),
            "credits_granted_wei": str(completed.get("credits_granted_wei") or ""),
            "funding_tx_hash": str(completed.get("funding_tx_hash") or ""),
            "completion_tx_hash": str(completed.get("completion_tx_hash") or ""),
            "created_at": str(completed.get("created_at") or ""),
        }
    if intent is not None:
        response["intent"] = {
            "status": str(intent.get("status") or ""),
            "created_at": str(intent.get("created_at") or ""),
        }
    return response


def stable_hub_contract() -> dict[str, str]:
    """Return the stable Hub contract advertised by this low-entropy runtime."""

    return {
        "auth": "multisession-wallet",
        "worker_identity": "validated-wallet-account-address",
        "worker_slot": "hub-generated-connection-id",
        "worker_connection": "long-lived-msk-session",
        "heartbeat": "connection-ping-pong",
        "availability_source": "live-worker-session-owner",
        "routing": "entry-hub-reserves-with-worker-home-hub-then-returns-concrete-execution-hub-url",
        "worker_live_session_transport": "websocket",
        "worker_heartbeat": "live-connection-ping-pong",
        "rest_worker_heartbeat": "forbidden",
        "wallet_funding": "verified-bridge-completion-to-shared-fdb",
        "refund_reconciliation": "disabled_pending_accounting_proof",
    }


def _url_host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise StableHubTopologyError(f"Hub URL must be an http(s) URL with a host: {url!r}")
    if parsed.scheme == "https":
        default_port = 443
    else:
        default_port = 80
    return parsed.hostname, parsed.port or default_port


def build_hub_identity(topology: StableHubTopology, hub_id: str) -> dict[str, Any]:
    """Build the stable identity document for one concrete Hub in a topology."""

    current = topology.hub_by_id(hub_id)
    peers = [hub for hub in topology.hubs if hub.hub_id != hub_id]
    return {
        "ok": True,
        "service": "main_computer.stable_hub",
        "hub": stable_hub_node_to_dict(current),
        "hub_id": current.hub_id,
        "hub_url": current.hub_url,
        "cluster_id": topology.cluster_id,
        "network": dict(topology.network),
        "storage": dict(topology.storage),
        "entry_urls": list(topology.entry_urls),
        "peer_hubs": [stable_hub_node_to_dict(hub) for hub in peers],
        "contract": stable_hub_contract(),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_work_session_stream_path(session_id: str) -> str:
    """Return the stable requester continuation route for an accepted work session."""

    return f"/api/hub/v1/work/sessions/{normalize_session_id(session_id)}/stream"


def stable_work_session_continuation_url(owner_hub_url: str, session_id: str) -> str:
    """Build the direct requester continuation URL on the worker/owner Hub."""

    base_url = str(owner_hub_url or "").strip().rstrip("/")
    if not base_url:
        raise StableHubWorkerSessionError("owner_hub_url is required to build continuation_url.")
    return f"{base_url}{stable_work_session_stream_path(session_id)}"



class LiveWorkerSession:
    """Local owner-Hub state for one authenticated worker live connection.

    Shared storage records that a worker is live and which Hub owns it. This
    object is intentionally local-only: it owns the actual WebSocket writer so a
    later requester/owner-Hub thread can push stable Hub messages over the
    already-open worker connection without introducing REST polling or FDB-carried
    live messages.
    """

    def __init__(
        self,
        *,
        worker_id: str,
        connection_id: str,
        handler: "StableHubRequestHandler",
        opened_at: str | None = None,
        multisession_key_id: str = "",
        market_profile: dict[str, Any] | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.connection_id = connection_id
        self.multisession_key_id = multisession_key_id
        self.market_profile = dict(market_profile or {})
        self.opened_at = opened_at or _utc_now()
        self.last_pong_at: str | None = None
        self.closed_at: str | None = None
        self.close_reason: str | None = None
        self._handler = handler
        self._send_lock = threading.RLock()
        self._state_condition = threading.Condition(threading.RLock())
        self._pending_acceptances: dict[str, dict[str, Any] | None] = {}

    @property
    def is_live(self) -> bool:
        return self.closed_at is None

    def send_json(self, payload: dict[str, Any]) -> None:
        """Send one JSON message over the worker WebSocket.

        All server-originated writes for this worker connection must pass through
        this method after authentication, so concurrent requester/owner-Hub work
        can safely share the same socket writer with the worker read loop.
        """

        with self._send_lock:
            if self.closed_at is not None:
                raise ConnectionError("worker live session is closed")
            self._handler._ws_send_json(payload)

    def record_pong(self, owner: dict[str, Any]) -> None:
        last_pong_at = str(owner.get("last_pong_at") or _utc_now())
        with self._state_condition:
            self.last_pong_at = last_pong_at
            self._state_condition.notify_all()

    def offer_work_and_wait_for_acceptance(
        self,
        offer: dict[str, Any],
        *,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        session_id = str(offer.get("session_id") or "").strip()
        request_id = str(offer.get("request_id") or "").strip()
        if not session_id:
            raise StableHubWorkerSessionError("hub.work.offer session_id is required.")
        if not request_id:
            raise StableHubWorkerSessionError("hub.work.offer request_id is required.")
        with self._state_condition:
            if session_id in self._pending_acceptances:
                raise StableHubWorkerSessionError("hub.work.offer session_id is already pending.")
            self._pending_acceptances[session_id] = None
        try:
            self.send_json(offer)
            return self.wait_for_work_accepted(
                session_id=session_id,
                request_id=request_id,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            with self._state_condition:
                if self._pending_acceptances.get(session_id) is None:
                    self._pending_acceptances.pop(session_id, None)
            raise

    def record_work_accepted(self, message: dict[str, Any]) -> None:
        session_id = str(message.get("session_id") or "").strip()
        request_id = str(message.get("request_id") or "").strip()
        if not session_id:
            raise StableHubWorkerSessionError("worker.work.accepted session_id is required.")
        if not request_id:
            raise StableHubWorkerSessionError("worker.work.accepted request_id is required.")
        with self._state_condition:
            if session_id not in self._pending_acceptances:
                raise StableHubWorkerSessionError("worker.work.accepted does not match a pending offer.")
            self._pending_acceptances[session_id] = dict(message)
            self._state_condition.notify_all()

    def wait_for_work_accepted(
        self,
        *,
        session_id: str,
        request_id: str,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        with self._state_condition:
            while self.closed_at is None:
                accepted = self._pending_acceptances.get(session_id)
                if accepted is not None:
                    self._pending_acceptances.pop(session_id, None)
                    if str(accepted.get("request_id") or "") != str(request_id):
                        raise StableHubWorkerSessionError("worker.work.accepted request_id mismatch.")
                    return dict(accepted)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._pending_acceptances.pop(session_id, None)
                    raise TimeoutError("worker did not accept hub.work.offer before timeout.")
                self._state_condition.wait(timeout=remaining)
            self._pending_acceptances.pop(session_id, None)
            raise ConnectionError("worker live session closed before accepting work.")

    def mark_closed(self, *, reason: str = "socket_closed", closed_at: str | None = None) -> None:
        with self._state_condition:
            if self.closed_at is None:
                self.closed_at = closed_at or _utc_now()
                self.close_reason = reason
            self._state_condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self._state_condition:
            return {
                "worker_id": self.worker_id,
                "connection_id": self.connection_id,
                "multisession_key_id": self.multisession_key_id,
                "market_profile": dict(self.market_profile),
                "opened_at": self.opened_at,
                "last_pong_at": self.last_pong_at,
                "closed_at": self.closed_at,
                "close_reason": self.close_reason,
                "pending_offer_count": sum(1 for value in self._pending_acceptances.values() if value is None),
                "status": "live" if self.closed_at is None else "closed",
            }


class _StableHubBridgeLedgerAdapter:
    """Narrow interface from existing escrow verification to Stable's atomic ledger.

    The completion service reads escrow state and signs only authorized deposit
    completion. This adapter has no chain-signing authority and cannot create
    credits without the service's verified cumulative escrow result.
    """

    def __init__(self, ledger: StableHubPayoutLedgerDirectory) -> None:
        self.ledger = ledger

    def prepare_bridge_deposit_completion(
        self,
        *,
        account_id: str,
        owner_address: str,
        deposit_id: str,
        chain_id: int,
        contract_address: str,
        deposit_amount_units: int,
        chain_completed_credit_wei: int,
        already_completed: bool,
    ) -> dict[str, Any]:
        if wallet_account_id(owner_address) != account_id:
            raise ValueError("bridge account does not match deposit wallet")
        return self.ledger.prepare_bridge_deposit_completion(
            account_id=account_id,
            wallet_address=owner_address,
            deposit_id=deposit_id,
            chain_id=chain_id,
            contract_address=contract_address,
            deposit_amount_units=deposit_amount_units,
            chain_completed_credit_wei=chain_completed_credit_wei,
            already_completed=already_completed,
        )

    def record_completed_bridge_deposit(
        self,
        *,
        account_id: str,
        owner_address: str,
        chain_completed_credit_wei: int,
        deposit_id: str,
        completion_tx_hash: str = "",
        chain_id: int = 0,
        contract_address: str = "",
        completed_units: int = 0,
        deposit_amount_units: int = 0,
        memo: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del memo
        if int(completed_units) != int(chain_completed_credit_wei):
            raise ValueError("bridge completed units disagree with completed credit wei")
        if wallet_account_id(owner_address) != account_id:
            raise ValueError("bridge account does not match deposit wallet")
        result = self.ledger.record_completed_bridge_deposit(
            account_id=account_id,
            wallet_address=owner_address,
            chain_completed_credit_wei=chain_completed_credit_wei,
            deposit_amount_units=deposit_amount_units,
            deposit_id=deposit_id,
            completion_tx_hash=completion_tx_hash,
            chain_id=chain_id,
            contract_address=contract_address,
            metadata=metadata,
        )
        return {
            **result,
            "chain_completed_credits_display": credit_wei_to_decimal_text(int(chain_completed_credit_wei)),
            "delta_credits_display": credit_wei_to_decimal_text(int(result["delta_credit_wei"])),
        }


class StableHubHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        topology: StableHubTopology,
        hub: StableHubNode,
        *,
        multisession_key_store: StableHubMultiSessionKeyStore | None = None,
        worker_session_store: StableHubWorkerSessionStore | None = None,
        work_offer_timeout_seconds: float = 10.0,
        bridge_completion_service: Any | None = None,
    ) -> None:
        super().__init__(server_address, StableHubRequestHandler)
        self.topology = topology
        self.hub = hub
        self.identity = build_hub_identity(topology, hub.hub_id)
        self.multisession_key_store = multisession_key_store or stable_multisession_store_from_topology(topology)
        self.multisession_key_service = StableHubMultiSessionKeyService(
            topology=topology,
            hub_id=hub.hub_id,
            store=self.multisession_key_store,
        )
        self.worker_session_store = worker_session_store or stable_worker_session_store_from_topology(topology)
        self.worker_session_directory = StableHubWorkerSessionDirectory(
            topology=topology,
            hub_id=hub.hub_id,
            store=self.worker_session_store,
        )
        self.worker_market_directory = StableHubWorkerMarketDirectory(
            topology=topology,
            hub_id=hub.hub_id,
            store=self.worker_session_store,
        )
        self.accepted_work_session_directory = StableHubAcceptedWorkSessionDirectory(
            topology=topology,
            hub_id=hub.hub_id,
            store=self.worker_session_store,
        )
        self.payout_ledger_directory = StableHubPayoutLedgerDirectory(
            topology=topology,
            hub_id=hub.hub_id,
            store=self.worker_session_store,
        )
        # A missing or invalid private signer bundle fails closed when the
        # endpoint is used; a public Hub must never fall back to a mock credit
        # importer or mint unverified requester balances.
        bridge_config = replace(
            MainComputerConfig.from_env(),
            hub_network=str(topology.network.get("network_key") or "").strip(),
        )
        self.bridge_configuration = bridge_config
        self.bridge_completion_override = bridge_completion_service is not None
        self.credit_bridge_completion = bridge_completion_service or HubCreditBridgeCompletionService(
            _StableHubBridgeLedgerAdapter(self.payout_ledger_directory), bridge_config
        )
        self.temporal_runtime = None
        if temporal_address():
            from main_computer.stable_hub_worker_sessions import stable_temporal_namespace_for_topology
            self.temporal_runtime = StableHubTemporalRuntime(
                address=temporal_address(), namespace=stable_temporal_namespace_for_topology(topology),
                store=self.worker_session_store,
            )
        self.work_offer_timeout_seconds = max(0.001, float(work_offer_timeout_seconds))
        self._live_worker_sessions_lock = threading.RLock()
        self.live_worker_sessions: dict[str, LiveWorkerSession] = {}

    def register_live_worker_session(self, session: LiveWorkerSession) -> None:
        with self._live_worker_sessions_lock:
            self.live_worker_sessions[session.connection_id] = session

    def get_live_worker_session(self, connection_id: str) -> LiveWorkerSession | None:
        with self._live_worker_sessions_lock:
            return self.live_worker_sessions.get(connection_id)

    def remove_live_worker_session(self, connection_id: str) -> LiveWorkerSession | None:
        with self._live_worker_sessions_lock:
            return self.live_worker_sessions.pop(connection_id, None)


    def server_close(self) -> None:
        if self.temporal_runtime is not None:
            self.temporal_runtime.close()
        super().server_close()

class StableHubRequestHandler(BaseHTTPRequestHandler):
    # RFC 6455 upgrades require an HTTP/1.1 handshake.  BaseHTTPRequestHandler
    # otherwise emits HTTP/1.0 even when the request is HTTP/1.1, which some
    # reverse proxies accept as a 101 response without tunnelling frames.
    # Every ordinary HTTP response below includes Content-Length.
    protocol_version = "HTTP/1.1"
    server: StableHubHTTPServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        sys.stderr.write(
            "stable-hub %s - %s\n"
            % (self.server.hub.hub_id, format % args)
        )

    def _log_event(self, message: str) -> None:
        sys.stderr.write(f"stable-hub {self.server.hub.hub_id} - {message}\n")
        sys.stderr.flush()

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError as exc:
            raise StableHubMultiSessionKeyError("bad Content-Length") from exc
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise StableHubMultiSessionKeyError(f"request body is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise StableHubMultiSessionKeyError("request JSON body must be an object")
        return payload


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
                426,
                {
                    "ok": False,
                    "error": "websocket_upgrade_required",
                    "hub_id": self.server.hub.hub_id,
                },
            )
            return False
        key = self.headers.get("Sec-WebSocket-Key", "").strip()
        if not key:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": "missing_sec_websocket_key",
                    "hub_id": self.server.hub.hub_id,
                },
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

    def _handle_worker_terminal_message(
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
        accepted_session = self.server.accepted_work_session_directory.get_session(session_id)
        if accepted_session is None:
            raise StableHubWorkerSessionError("accepted work session does not exist.")
        # Ignore client worker labels. The socket's connection_id and durable
        # accepted session determine authorization for this terminal message.
        message_run_id = str(message.get("run_id") or "")
        if message_run_id and message_run_id != str(accepted_session.get("run_id") or ""):
            raise StableHubWorkerSessionError("worker terminal message run_id mismatch.")
        if str(accepted_session.get("request_id") or "") != request_id:
            raise StableHubWorkerSessionError("worker terminal message request_id mismatch.")
        if str(accepted_session.get("worker_id") or "") != worker_id:
            raise StableHubWorkerSessionError("worker terminal message worker_id mismatch.")
        if str(accepted_session.get("worker_connection_id") or "") != connection_id:
            raise StableHubWorkerSessionError("worker terminal message connection_id mismatch.")
        previous_status = str(accepted_session.get("status") or "")
        if previous_status in {"succeeded", "failed", "cancelled"}:
            session.send_json(
                {
                    "type": "hub.work.terminal.accepted",
                    "ok": True,
                    "idempotent": True,
                    "session_id": session_id,
                    "request_id": request_id,
                    "status": previous_status,
                    "accepted_session": accepted_session,
                }
            )
            return

        payout = dict(accepted_session.get("payout") or {})
        hold_id = str(payout.get("hold_id") or "")
        store = self.server.payout_ledger_directory.store

        def _atomic_terminal(action: Any) -> Any:
            # The funds transition and accepted-session state change must
            # commit (or conflict/retry) as one FoundationDB transaction.
            atomic = getattr(store, "atomic", None)
            return atomic(action) if callable(atomic) else action()

        if message_type == "worker.work.result":
            result_payload = message.get("result")
            if not isinstance(result_payload, dict):
                result_payload = {"value": result_payload}

            def _record_success() -> dict[str, Any]:
                final_payout = dict(payout)
                if hold_id:
                    payment = self.server.payout_ledger_directory.spend_request_credit(
                        account_id=str(accepted_session.get("requester_account_id") or ""),
                        wallet_address=str(accepted_session.get("requester_wallet_address") or ""),
                        request_id=request_id, session_id=session_id,
                        run_id=str(accepted_session.get("run_id") or ""),
                        worker_id=worker_id,
                        worker_account_id=str(payout.get("worker_account_id") or ""),
                        selected_price=dict(payout.get("selected_price") or {}),
                        requester_max_price=(accepted_session.get("work") or {}).get("max_price"),
                        partition=str(accepted_session.get("partition") or ""),
                        hold_id=hold_id, result=result_payload,
                        metadata={"pricing_mode": "stable_market_price", "hub_id": self.server.hub.hub_id},
                    )
                    charge = dict(payment.get("charge") or {})
                    earning = dict(payment.get("worker_earning") or {})
                    final_payout.update({
                        "hold_status": "charged", "charge_id": charge.get("charge_id", ""),
                        "worker_earning_id": earning.get("earning_id", ""),
                        "charged_credits": str(charge.get("amount") or "0"),
                        "payment_status": "charged", "settlement_status": "earned",
                        "direct_spend": False, "released_credits": "0",
                    })
                return self.server.accepted_work_session_directory.record_succeeded(
                    session_id=session_id, worker_connection_id=connection_id,
                    worker_result={"type": message_type, "result": result_payload}, payout=final_payout,
                )

            updated = _atomic_terminal(_record_success)
            self.server.worker_market_directory.record_session_finished(
                worker_id=worker_id, connection_id=connection_id,
            )
            session.send_json({"type": "hub.work.result.accepted", "ok": True,
                               "session_id": session_id, "request_id": request_id,
                               "status": updated.get("status"), "payout": updated.get("payout", {})})
            return

        if message_type == "worker.work.failed":
            failure_payload = message.get("error")
            if not isinstance(failure_payload, dict):
                failure_payload = {"error": str(failure_payload or message.get("message") or "worker_failed")}

            def _record_failure() -> dict[str, Any]:
                final_payout = dict(payout)
                if hold_id:
                    self.server.payout_ledger_directory.release_hold(
                        hold_id=hold_id, session_id=session_id,
                    )
                    final_payout.update({
                        "hold_status": "released", "payment_status": "released",
                        "released_credits": str(payout.get("reserved_credits") or "0"),
                        "settlement_status": "not_earned", "direct_spend": False,
                        "charge_id": "", "worker_earning_id": "", "charged_credits": "0",
                    })
                else:
                    # Preserve legacy pay-on-acceptance records as historical
                    # accounting facts; never synthesize a second refund.
                    final_payout["failure_reason"] = "worker_failed"
                return self.server.accepted_work_session_directory.record_failed(
                    session_id=session_id, worker_connection_id=connection_id,
                    worker_failure={"type": message_type, "error": failure_payload}, payout=final_payout,
                )

            updated = _atomic_terminal(_record_failure)
            self.server.worker_market_directory.record_session_finished(
                worker_id=worker_id, connection_id=connection_id,
            )
            session.send_json({"type": "hub.work.failed.accepted", "ok": True,
                               "session_id": session_id, "request_id": request_id,
                               "status": updated.get("status"), "payout": updated.get("payout", {})})
            return

        raise StableHubWorkerSessionError("unsupported worker terminal message.")

    def _handle_worker_live_session_websocket(self) -> None:
        if not self._accept_websocket():
            return

        worker_id = ""
        connection_id = ""
        session: LiveWorkerSession | None = None
        session_registered = False
        diagnostic_attempt_id = "none"
        termination_reason = "handler_exit"
        last_message_type = ""
        last_worker_pong_at = ""
        try:
            auth_message = self._ws_read_json_message()
            raw_attempt_id = str(auth_message.get("diagnostic_attempt_id") or "")
            diagnostic_attempt_id = "".join(
                character for character in raw_attempt_id
                if character.isalnum() or character in "_-"
            )[:48] or "none"
            last_message_type = str(auth_message.get("type") or "<missing-type>")
            if auth_message.get("type") != "worker.auth":
                raise StableHubWorkerSessionError("first worker live-session message must be worker.auth")
            # A caller-supplied worker_id is never an identity or routing key.
            # The signed authorization defines the account; the Hub owns sockets.
            authorization = auth_message.get("multisession_authorization")
            if not isinstance(authorization, dict):
                key_id = str(auth_message.get("multisession_key_id") or "").strip()
                authorization = {"kind": "multisession_key", "multisession_key_id": key_id} if key_id else {}
            validation = self.server.multisession_key_service.validate_key(
                {"multisession_authorization": authorization}
            )
            if validation.get("valid") is not True:
                termination_reason = f"auth_rejected:{str(validation.get('reason_code') or 'unknown')[:120]}"
                self._ws_send_json(
                    {
                        "type": "hub.auth.rejected",
                        "ok": False,
                        "reason_code": validation.get("reason_code"),
                        "user_message": validation.get("user_message"),
                        "hub_id": self.server.hub.hub_id,
                    }
                )
                return

            account_id = wallet_account_id(validation.get("wallet_address"))
            if str(validation.get("account_id") or "") != account_id:
                raise StableHubWorkerSessionError("worker_authorization_account_mismatch")
            connection_id = new_connection_id()
            worker_id = connection_id  # Hub-generated, connection-scoped routing slot.
            key_id = str(validation.get("multisession_key_id") or authorization.get("multisession_key_id") or "")
            raw_market_profile = auth_message.get("market")
            if not isinstance(raw_market_profile, dict):
                raw_market_profile = auth_message.get("worker_market")
            market_profile = normalize_worker_market_profile(
                raw_market_profile if isinstance(raw_market_profile, dict) else None
            )
            # An owner must never be observable separately from market publication.
            # The transaction callback performs durable operations only.
            def _register_worker_records():
                owner = self.server.worker_session_directory.record_connected(
                    worker_id=worker_id,
                    connection_id=connection_id,
                    multisession_key_id=key_id,
                    wallet_address=str(validation.get("wallet_address") or ""),
                    account_id=str(validation.get("account_id") or ""),
                )
                market_record = self.server.worker_market_directory.record_worker_live(
                    worker_id=worker_id,
                    owner=owner,
                    market_profile=market_profile,
                    worker_msk_id=key_id,
                    worker_wallet_address=str(validation.get("wallet_address") or ""),
                    worker_account_id=str(validation.get("account_id") or ""),
                )
                return owner, market_record

            store = self.server.worker_session_directory.store
            atomic = getattr(store, "atomic", None)
            owner, market_record = (
                atomic(_register_worker_records) if callable(atomic) else _register_worker_records()
            )
            session = LiveWorkerSession(
                worker_id=worker_id,
                connection_id=connection_id,
                handler=self,
                opened_at=str(owner.get("connected_at") or ""),
                multisession_key_id=key_id,
                market_profile=market_profile,
            )
            self.server.register_live_worker_session(session)
            session_registered = True
            termination_reason = "authenticated_socket_loop"
            session.send_json(
                {
                    "type": "hub.auth.accepted",
                    "ok": True,
                    "hub_id": self.server.hub.hub_id,
                    "hub_url": self.server.hub.hub_url,
                    "cluster_id": self.server.topology.cluster_id,
                    "account_id": account_id,
                    "connection_id": connection_id,
                    "owner": owner,
                    "market": market_record,
                    "worker_hub": {
                        "hub_id": self.server.hub.hub_id,
                        "hub_url": self.server.hub.hub_url,
                        "local_owner": True,
                        "handoff": False,
                    },
                    "heartbeat": {
                        "transport": "websocket",
                        "mode": "hub-ping-worker-pong",
                    },
                }
            )

            ping_id = "ping_" + secrets.token_urlsafe(12).rstrip("=")
            session.send_json({"type": "hub.ping", "ping_id": ping_id, "connection_id": connection_id})
            while True:
                message = self._ws_read_json_message()
                message_type = str(message.get("type") or "")
                last_message_type = message_type or "<missing-type>"
                if message_type == "worker.pong":
                    last_worker_pong_at = datetime.now(timezone.utc).isoformat()
                    if str(message.get("connection_id") or connection_id) != connection_id:
                        session.send_json(
                            {
                                "type": "hub.error",
                                "ok": False,
                                "error": "connection_id_mismatch",
                                "connection_id": connection_id,
                            }
                        )
                        continue
                    owner = self.server.worker_session_directory.record_pong(
                        worker_id=worker_id,
                        connection_id=connection_id,
                    )
                    session.record_pong(owner)
                    session.send_json(
                        {
                            "type": "hub.pong.accepted",
                            "ok": True,
                            "worker_id": worker_id,
                            "connection_id": connection_id,
                            "owner": owner,
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
                if message_type in {"worker.work.result", "worker.work.failed"}:
                    try:
                        self._handle_worker_terminal_message(
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
                    termination_reason = "worker.close"
                    break
                session.send_json(
                    {
                        "type": "hub.error",
                        "ok": False,
                        "error": "unsupported_worker_message",
                        "received_type": message_type,
                    }
                )
        except (ConnectionError, OSError) as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            termination_reason = f"{exc.__class__.__name__}: {detail[:240]}"
        except (StableHubWorkerSessionError, RuntimeError, ValueError) as exc:
            termination_reason = f"{exc.__class__.__name__}: {str(exc)[:240]}"
            try:
                if session is not None:
                    session.send_json(
                        {
                            "type": "hub.error",
                            "ok": False,
                            "error": str(exc),
                            "hub_id": self.server.hub.hub_id,
                        }
                    )
                else:
                    self._ws_send_json(
                        {
                            "type": "hub.error",
                            "ok": False,
                            "error": str(exc),
                            "hub_id": self.server.hub.hub_id,
                        }
                    )
            except Exception:
                pass
        except Exception as exc:
            if getattr(exc, "code", None) != 1020:
                termination_reason = f"unhandled_{exc.__class__.__name__}: {str(exc)[:240]}"
                raise
            termination_reason = "foundationdb_transaction_conflict"
            # FoundationDB aborted the transaction; do not claim connection
            # or payment acceptance. The worker can reconnect and retry.
            try:
                sender = session.send_json if session is not None else self._ws_send_json
                sender({"type": "hub.error", "ok": False,
                        "error": "foundationdb_transaction_conflict",
                        "retryable": True, "hub_id": self.server.hub.hub_id})
            except OSError:
                pass
        finally:
            self._log_event(
                "worker live-session ended "
                f"attempt_id={diagnostic_attempt_id} "
                f"connection_id={connection_id or 'none'} "
                f"session_registered={session_registered} "
                f"last_message_type={last_message_type!r} "
                f"last_worker_pong_at={last_worker_pong_at or 'none'} "
                f"reason={termination_reason!r}"
            )
            if worker_id and connection_id:
                abandoned_sessions = 0
                # Owner close, market close, and unearned-hold settlement must
                # commit together. Otherwise a Hub crash between commits can
                # leave a closed Worker with credit held indefinitely.
                try:
                    closed_owner, abandoned_sessions = close_worker_connection_and_settle(
                        self.server, worker_id=worker_id, connection_id=connection_id,
                    )
                except Exception as exc:
                    if getattr(exc, "code", None) != 1020:
                        raise
                    self._log_event("worker close/settlement deferred after FDB conflict")
                finally:
                    removed = self.server.remove_live_worker_session(connection_id)
                    if removed is not None:
                        removed.mark_closed(reason="socket_closed")
                if abandoned_sessions:
                    self._log_event(
                        f"settled {abandoned_sessions} abandoned accepted sessions "
                        f"for closed connection_id={connection_id}"
                    )




    def _send_existing_accepted_work_session_response(
        self,
        *,
        accepted_session: dict[str, Any],
        request_id: str,
        idempotent: bool = True,
    ) -> None:
        session_id = normalize_session_id(accepted_session.get("session_id"))
        owner_hub_id = str(accepted_session.get("owner_hub_id") or "")
        owner_hub_url = str(accepted_session.get("owner_hub_url") or "")
        continuation_url = stable_work_session_continuation_url(owner_hub_url, session_id)
        self._send_json(
            200,
            {
                "ok": True,
                "accepted": True,
                "idempotent": idempotent,
                "duplicate_request_id": True,
                "session_id": session_id,
                "run_id": accepted_session.get("run_id"),
                "request_id": request_id,
                "worker_id": accepted_session.get("worker_id"),
                "owner_hub_id": owner_hub_id,
                "owner_hub_url": owner_hub_url,
                "partition": accepted_session.get("partition"),
                "task_queue": accepted_session.get("task_queue"),
                "execution_hub": {
                    "hub_id": owner_hub_id,
                    "hub_url": owner_hub_url,
                },
                "continuation_url": continuation_url,
                "continuation": {
                    "direct": True,
                    "stream_path": stable_work_session_stream_path(session_id),
                    "hub_id": owner_hub_id,
                    "hub_url": owner_hub_url,
                },
                "execution": accepted_session.get("execution", {}),
                "payout": accepted_session.get("payout", {}),
                "accepted_session": accepted_session,
                "hub_id": self.server.hub.hub_id,
                "cluster_id": self.server.topology.cluster_id,
            },
        )

    def _work_payloads_match(self, left: Any, right: Any) -> bool:
        try:
            return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
                right,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return False


    def _handle_work_session_stream(self, path: str) -> None:
        prefix = "/api/hub/v1/work/sessions/"
        suffix = "/stream"
        raw_session_id = path[len(prefix) : -len(suffix)]
        try:
            session_id = normalize_session_id(raw_session_id)
        except StableHubWorkerSessionError as exc:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": str(exc),
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

        accepted_session = self.server.accepted_work_session_directory.get_session(session_id)
        if accepted_session is None:
            self._send_json(
                404,
                {
                    "ok": False,
                    "error": "work_session_not_found",
                    "message": "No accepted Stable Hub work session was found for that session_id.",
                    "session_id": session_id,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

        owner_hub_id = str(accepted_session.get("owner_hub_id") or "")
        owner_hub_url = str(accepted_session.get("owner_hub_url") or "")
        try:
            continuation_url = stable_work_session_continuation_url(owner_hub_url, session_id)
        except StableHubWorkerSessionError as exc:
            self._send_json(
                500,
                {
                    "ok": False,
                    "error": "work_session_continuation_unavailable",
                    "message": str(exc),
                    "session_id": session_id,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

        execution_hub = {
            "hub_id": owner_hub_id,
            "hub_url": owner_hub_url,
        }
        if owner_hub_id != self.server.hub.hub_id:
            self._send_json(
                409,
                {
                    "ok": False,
                    "error": "session_continuation_not_on_this_hub",
                    "message": "Requester continuation must connect directly to the execution Hub.",
                    "session_id": session_id,
                    "run_id": accepted_session.get("run_id"),
                    "status": accepted_session.get("status"),
                    "execution_hub": execution_hub,
                    "continuation_url": continuation_url,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

        self._send_json(
            200,
            {
                "ok": True,
                "session_id": session_id,
                "run_id": accepted_session.get("run_id"),
                "request_id": accepted_session.get("request_id"),
                "status": accepted_session.get("status"),
                "execution_hub": execution_hub,
                "continuation_url": continuation_url,
                "execution": accepted_session.get("execution", {}),
                "payout": accepted_session.get("payout", {}),
                "accepted_session": accepted_session,
                "stream": {
                    "transport": "stable-hub-session-stream",
                    "mode": "accepted-session-state",
                    "status": accepted_session.get("status"),
                    "source": "accepted-session-record",
                },
                "hub_id": self.server.hub.hub_id,
                "cluster_id": self.server.topology.cluster_id,
            },
        )


    def _post_same_request_to_owner_hub(
        self,
        *,
        owner_hub_id: str,
        owner_hub_url: str,
        body: dict[str, Any],
        timeout_seconds: float = 15.0,
    ) -> tuple[int, dict[str, Any]]:
        """Forward the normal requester work request to the concrete owner Hub.

        This is the stable Hub-to-Hub handoff boundary: the owner Hub receives
        the same requester-shaped JSON request and validates the requester MSK
        itself. The entry Hub does not send a worker-internal command and does
        not stream work/token messages through shared storage.
        """

        base_url = str(owner_hub_url or "").rstrip("/")
        if not base_url:
            raise StableHubWorkerSessionError("owner_hub_url is required for remote handoff.")
        if owner_hub_id == self.server.hub.hub_id:
            raise StableHubWorkerSessionError("remote handoff target must be a different Hub.")
        handoff_url = f"{base_url}/api/hub/v1/work/requests"
        payload = json.dumps(body, sort_keys=True).encode("utf-8")
        request = Request(
            handoff_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                # Cloudflare blocks urllib's default Python-urllib User-Agent (1010).
                # This is a transport signature, not Hub-to-Hub authentication.
                "User-Agent": "main-computer-hub-handoff/1.0",
                "X-Main-Computer-Stable-Hub-Handoff-From": self.server.hub.hub_id,
            },
            method="POST",
        )
        try:
            with open_internal_https(request, timeout=timeout_seconds, default_open=urlopen) as response:
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
            raise ConnectionError(f"owner Hub handoff failed: {exc}") from exc


    def _validate_worker_payout_authorization(self, worker_id: str, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        worker_id = normalize_worker_id(worker_id)
        validation = self.server.multisession_key_service.validate_key(
            {"multisession_authorization": body.get("multisession_authorization")}
        )
        if validation.get("valid") is not True:
            raise StableHubWorkerSessionError("worker_payout_msk_invalid")
        account_id = wallet_account_id(validation.get("wallet_address"))
        if account_id != worker_id or str(validation.get("account_id") or "") != account_id:
            raise StableHubWorkerSessionError("worker_payout_account_mismatch")
        # No market/connection lookup: claims outlive sockets and are wallet-owned.
        return validation, {"worker_account_id": account_id}

    def _handle_worker_payout_claim(self, worker_id: str) -> None:
        try:
            body = self._read_json()
            self._validate_worker_payout_authorization(worker_id, body)
            claim = self.server.payout_ledger_directory.record_worker_claim(
                worker_id=worker_id,
                earning_ids=body.get("earning_ids") if isinstance(body.get("earning_ids"), list) else None,
                idempotency_key=str(body.get("idempotency_key") or ""),
            )
        except StableHubWorkerSessionError as exc:
            self._send_json(403 if ("msk" in str(exc) or "account_mismatch" in str(exc)) else 400, {"ok": False, "error": str(exc), "hub_id": self.server.hub.hub_id})
            return
        self._send_json(200, {"ok": True, "claim": claim, "hub_id": self.server.hub.hub_id, "cluster_id": self.server.topology.cluster_id})

    def _handle_worker_payout_settlement(self, worker_id: str) -> None:
        try:
            body = self._read_json()
            self._validate_worker_payout_authorization(worker_id, body)
            batch = self.server.payout_ledger_directory.create_worker_settlement_batch(
                worker_id=worker_id,
                claim_ids=body.get("claim_ids") if isinstance(body.get("claim_ids"), list) else None,
                idempotency_key=str(body.get("idempotency_key") or ""),
            )
            if body.get("settle") is True:
                batch = self.server.payout_ledger_directory.settle_worker_settlement_batch(
                    batch_id=str(batch.get("batch_id") or ""),
                    settlement_reference=str(body.get("settlement_reference") or ""),
                    idempotency_key=str(body.get("idempotency_key") or ""),
                )
        except StableHubWorkerSessionError as exc:
            self._send_json(403 if ("msk" in str(exc) or "account_mismatch" in str(exc)) else 400, {"ok": False, "error": str(exc), "hub_id": self.server.hub.hub_id})
            return
        self._send_json(200, {"ok": True, "settlement": batch, "hub_id": self.server.hub.hub_id, "cluster_id": self.server.topology.cluster_id})

    def _handle_worker_bridge_payout(self, worker_id: str) -> None:
        try:
            body = self._read_json()
            self._validate_worker_payout_authorization(worker_id, body)
            payout = self.server.payout_ledger_directory.request_bridge_payout(
                worker_id=worker_id,
                batch_id=str(body.get("batch_id") or ""),
                idempotency_key=str(body.get("idempotency_key") or ""),
            )
        except StableHubWorkerSessionError as exc:
            self._send_json(403 if ("msk" in str(exc) or "account_mismatch" in str(exc)) else 400, {"ok": False, "error": str(exc), "hub_id": self.server.hub.hub_id})
            return
        self._send_json(200, {"ok": True, "bridge_payout": payout, "hub_id": self.server.hub.hub_id, "cluster_id": self.server.topology.cluster_id})

    def _handle_bridge_payout_confirm(self, bridge_payout_id: str) -> None:
        try:
            body = self._read_json()
            payout = self.server.payout_ledger_directory.confirm_bridge_payout(
                bridge_payout_id=bridge_payout_id,
                settlement_reference=str(body.get("settlement_reference") or ""),
            )
        except StableHubWorkerSessionError as exc:
            self._send_json(400, {"ok": False, "error": str(exc), "hub_id": self.server.hub.hub_id})
            return
        self._send_json(200, {"ok": True, "bridge_payout": payout, "hub_id": self.server.hub.hub_id, "cluster_id": self.server.topology.cluster_id})

    def _handle_bridge_payout_fail(self, bridge_payout_id: str) -> None:
        try:
            body = self._read_json()
            payout = self.server.payout_ledger_directory.fail_bridge_payout(
                bridge_payout_id=bridge_payout_id,
                reason=str(body.get("reason") or ""),
            )
        except StableHubWorkerSessionError as exc:
            self._send_json(400, {"ok": False, "error": str(exc), "hub_id": self.server.hub.hub_id})
            return
        self._send_json(200, {"ok": True, "bridge_payout": payout, "hub_id": self.server.hub.hub_id, "cluster_id": self.server.topology.cluster_id})


    def _handle_work_request(self) -> None:
        try:
            body = self._read_json()
            request_id = normalize_request_id(body.get("request_id"))
            work = body.get("work")
            if not isinstance(work, dict):
                raise StableHubWorkerSessionError("work object is required.")
        except (StableHubMultiSessionKeyError, StableHubWorkerSessionError, ValueError) as exc:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": str(exc),
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

        validation = self.server.multisession_key_service.validate_key(
            {"multisession_authorization": body.get("multisession_authorization")}
        )
        if validation.get("valid") is not True:
            self._send_json(
                401,
                {
                    "ok": False,
                    "error": "requester_msk_invalid",
                    "reason_code": validation.get("reason_code"),
                    "message": validation.get("user_message"),
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

        requester_account_id = str(validation.get("account_id") or "")
        partition = stable_partition_key_for_work(work)
        existing_session = self.server.accepted_work_session_directory.get_session_for_request(
            requester_account_id=requester_account_id,
            request_id=request_id,
        )
        if existing_session is not None:
            if not self._work_payloads_match(existing_session.get("work", {}), work):
                self._send_json(
                    409,
                    {
                        "ok": False,
                        "error": "duplicate_request_id_work_mismatch",
                        "message": "A stable Hub work session already exists for this requester request_id with different work.",
                        "request_id": request_id,
                        "existing_session_id": existing_session.get("session_id"),
                        "existing_status": existing_session.get("status"),
                        "hub_id": self.server.hub.hub_id,
                        "cluster_id": self.server.topology.cluster_id,
                    },
                )
                return
            self._send_existing_accepted_work_session_response(
                accepted_session=existing_session,
                request_id=request_id,
                idempotent=True,
            )
            return

        # Legacy payout holds are no longer authoritative.  Idempotency is handled by
        # accepted-session and direct charge records instead of pre-reserved credit.

        # Market selection is advisory. Another transaction may close the
        # selected socket before we read its owner, so exclude rejected leases
        # and retry BEFORE sending any offer or accepting money.
        retired_missing_socket: tuple[str, dict[str, Any]] | None = None
        rejected_selection: dict[str, Any] | None = None
        excluded_connections: set[tuple[str, str, int]] = set()
        max_selection_attempts = 64
        market_record = None

        for selection_attempt in range(1, max_selection_attempts + 1):
            selected_worker = self.server.worker_market_directory.select_worker_for_work(
                work, excluded_connections=excluded_connections,
            )
            if selected_worker is None:
                break
            worker_id = normalize_worker_id(selected_worker.get("worker_id"))
            identity = worker_market_identity(selected_worker)
            owner = self.server.worker_session_directory.get_owner(worker_id)

            def _reject_candidate(code: str, message: str) -> None:
                nonlocal rejected_selection
                excluded_connections.add(identity)
                rejected_selection = {
                    "ok": False, "error": code, "message": message,
                    "request_id": request_id, "worker_id": worker_id,
                    "partition": partition, "owner": owner,
                    "selected_worker": selected_worker,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                }
                self._log_event(
                    "worker_selection_retry "
                    f"request_id={request_id} worker_id={worker_id} "
                    f"connection_id={identity[1]} lease_epoch={identity[2]} "
                    f"reason={code} attempt={selection_attempt}"
                )

            if not owner or owner.get("status") != "live":
                _reject_candidate("worker_not_live", "Selected worker has no live owner record.")
                continue
            if (
                str(owner.get("owner_hub_id") or "") != str(selected_worker.get("owner_hub_id") or "")
                or str(owner.get("owner_hub_url") or "") != str(selected_worker.get("owner_hub_url") or "")
                or str(owner.get("connection_id") or "") != identity[1]
                or int(owner.get("lease_epoch") or 0) != identity[2]
            ):
                _reject_candidate("worker_owner_changed", "Selected worker owner record changed before work could be offered.")
                continue
            if str(owner.get("owner_hub_id") or "") != self.server.hub.hub_id:
                break  # Remote owner validates its own socket and reserves capacity.

            local_socket = self.server.get_live_worker_session(identity[1])
            if local_socket is None or not local_socket.is_live:
                # Retire exactly the stale local lease atomically. Never retire
                # a newer reconnect or a socket that reappeared meanwhile.
                def _retire_missing_socket() -> bool:
                    current = self.server.worker_session_directory.get_owner(worker_id)
                    if (
                        not current or current.get("status") != "live"
                        or str(current.get("owner_hub_id") or "") != self.server.hub.hub_id
                        or str(current.get("connection_id") or "") != identity[1]
                        or int(current.get("lease_epoch") or 0) != identity[2]
                    ):
                        return False
                    active = self.server.get_live_worker_session(identity[1])
                    if active is not None and active.is_live:
                        return False
                    self.server.worker_session_directory.record_closed(
                        worker_id=worker_id, connection_id=identity[1],
                        reason="local_socket_missing",
                    )
                    self.server.worker_market_directory.record_worker_closed(
                        worker_id=worker_id, connection_id=identity[1],
                        reason="local_socket_missing",
                    )
                    return True

                store = self.server.worker_session_directory.store
                atomic = getattr(store, "atomic", None)
                retired = atomic(_retire_missing_socket) if callable(atomic) else _retire_missing_socket()
                if retired:
                    retired_missing_socket = (worker_id, dict(owner))
                    self._log_event(f"retired missing local worker socket: {worker_id}")
                else:
                    # The connection may have reappeared while inspecting the
                    # store; selection is re-evaluated on the next request.
                    pass
                _reject_candidate(
                    "worker_owner_not_local",
                    "Selected worker has no local live socket. Worker must reconnect.",
                )
                continue

            # A configured Temporal runtime is required before reserving the
            # first capacity slot. Never run readiness checks inside FDB atomic.
            if self.server.temporal_runtime is not None:
                try:
                    self.server.temporal_runtime.require_ready()
                except TemporalLifecycleUnavailable as exc:
                    self._send_json(503, {"ok": False, "error": "temporal_unavailable", "message": str(exc)})
                    return

            try:
                market_record = self.server.worker_market_directory.reserve_worker_capacity(
                    worker_id=worker_id,
                    connection_id=identity[1],
                    lease_epoch=identity[2],
                )
            except StableHubWorkerSessionError as exc:
                if str(exc) not in {
                    "worker_not_live", "worker_owner_changed", "worker_capacity_unavailable"
                }:
                    raise
                _reject_candidate(str(exc), "Selected worker no longer has an available live capacity slot.")
                continue
            break
        else:
            # Even a rapidly changing market must not create an unbounded loop.
            self._send_json(409, {
                "ok": False, "error": "worker_selection_unstable",
                "message": "Worker market changed repeatedly during selection; retry the request.",
                "request_id": request_id, "partition": partition,
                "hub_id": self.server.hub.hub_id,
                "cluster_id": self.server.topology.cluster_id,
            })
            return

        if selected_worker is None:
            if rejected_selection is not None:
                self._send_json(409, rejected_selection)
            elif retired_missing_socket is not None:
                stale_worker_id, stale_owner = retired_missing_socket
                self._send_json(409, {
                    "ok": False, "error": "worker_owner_not_local",
                    "message": "Selected worker has no local live socket. Worker must reconnect.",
                    "request_id": request_id, "worker_id": stale_worker_id,
                    "partition": partition, "owner": stale_owner,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                })
            else:
                self._send_json(409, {
                    "ok": False, "error": "worker_not_live",
                    "message": "No live worker matched the request market constraints.",
                    "request_id": request_id, "partition": partition,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                })
            return
        self._log_event(
            "worker_selection_succeeded "
            f"request_id={request_id} worker_id={worker_id} "
            f"connection_id={identity[1]} lease_epoch={identity[2]} "
            f"excluded={len(excluded_connections)}"
        )

        owner_hub_id = str(owner.get("owner_hub_id") or "")
        owner_hub_url = str(owner.get("owner_hub_url") or "")
        if owner_hub_id != self.server.hub.hub_id:
            try:
                owner_status, owner_response = self._post_same_request_to_owner_hub(
                    owner_hub_id=owner_hub_id,
                    owner_hub_url=owner_hub_url,
                    body=body,
                )
            except (ConnectionError, StableHubWorkerSessionError) as exc:
                self._send_json(
                    502,
                    {
                        "ok": False,
                        "error": "owner_hub_handoff_failed",
                        "message": str(exc),
                        "request_id": request_id,
                        "worker_id": worker_id,
                        "partition": partition,
                        "owner_hub": {
                            "hub_id": owner_hub_id,
                            "hub_url": owner_hub_url,
                        },
                        "selected_worker": selected_worker,
                        "hub_id": self.server.hub.hub_id,
                        "cluster_id": self.server.topology.cluster_id,
                    },
                )
                return

            if owner_response.get("ok") is True and owner_response.get("accepted") is True:
                entry_response = dict(owner_response)
                entry_response["hub_id"] = self.server.hub.hub_id
                entry_response["entry_hub_id"] = self.server.hub.hub_id
                entry_response["accepted_by_hub_id"] = str(owner_response.get("hub_id") or owner_hub_id)
                entry_response["execution_hub"] = {
                    "hub_id": owner_hub_id,
                    "hub_url": owner_hub_url,
                }
                accepted_session_id = normalize_session_id(entry_response.get("session_id"))
                entry_response["continuation_url"] = stable_work_session_continuation_url(
                    owner_hub_url,
                    accepted_session_id,
                )
                entry_response["continuation"] = {
                    "direct": True,
                    "stream_path": stable_work_session_stream_path(accepted_session_id),
                    "hub_id": owner_hub_id,
                    "hub_url": owner_hub_url,
                }
                entry_response["handoff"] = {
                    "routed": True,
                    "from_hub_id": self.server.hub.hub_id,
                    "to_hub_id": owner_hub_id,
                    "to_hub_url": owner_hub_url,
                    "request_shape": "stable-requester-work",
                }
                self._send_json(200, entry_response)
                return

            handoff_error = dict(owner_response)
            handoff_error.setdefault("ok", False)
            handoff_error["entry_hub_id"] = self.server.hub.hub_id
            handoff_error["hub_id"] = self.server.hub.hub_id
            handoff_error["owner_hub"] = {
                "hub_id": owner_hub_id,
                "hub_url": owner_hub_url,
            }
            handoff_error["handoff"] = {
                "routed": True,
                "from_hub_id": self.server.hub.hub_id,
                "to_hub_id": owner_hub_id,
                "to_hub_url": owner_hub_url,
                "request_shape": "stable-requester-work",
            }
            self._send_json(owner_status if 400 <= owner_status <= 599 else 502, handoff_error)
            return

        # For the local owner, the selection loop above validated the socket
        # and atomically reserved a capacity slot against the live owner lease.
        owner_connection_id = identity[1]
        live_session = local_socket
        assert market_record is not None

        session_id = new_session_id()
        run_id = new_run_id()
        task_queue = stable_task_queue_for_partition(partition)

        offer = {
            "type": "hub.work.offer",
            "session_id": session_id,
            "run_id": run_id,
            "request_id": request_id,
            "worker_id": worker_id,
            "partition": partition,
            "task_queue": task_queue,
            "work": json.loads(json.dumps(work)),
        }
        try:
            worker_acceptance = live_session.offer_work_and_wait_for_acceptance(
                offer,
                timeout_seconds=self.server.work_offer_timeout_seconds,
            )
        except TimeoutError as exc:
            self.server.worker_market_directory.record_session_finished(
                worker_id=worker_id,
                connection_id=owner_connection_id,
            )
            self._send_json(
                504,
                {
                    "ok": False,
                    "error": "worker_offer_timeout",
                    "message": str(exc),
                    "request_id": request_id,
                    "worker_id": worker_id,
                    "partition": partition,
                    "owner": owner,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return
        except (ConnectionError, OSError) as exc:
            self.server.worker_market_directory.record_session_finished(
                worker_id=worker_id,
                connection_id=owner_connection_id,
            )
            self._send_json(
                409,
                {
                    "ok": False,
                    "error": "worker_owner_not_local",
                    "message": str(exc),
                    "request_id": request_id,
                    "worker_id": worker_id,
                    "partition": partition,
                    "owner": owner,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return
        except StableHubWorkerSessionError as exc:
            self.server.worker_market_directory.record_session_finished(
                worker_id=worker_id,
                connection_id=owner_connection_id,
            )
            self._send_json(
                409,
                {
                    "ok": False,
                    "error": "worker_acceptance_rejected",
                    "message": str(exc),
                    "request_id": request_id,
                    "worker_id": worker_id,
                    "partition": partition,
                    "owner": owner,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

        # The worker has accepted the offer, but payment is not earned until
        # an authenticated successful result. Reserve the maximum agreed price
        # atomically with accepted-session creation; failures release the hold.
        def _record_acceptance() -> dict[str, Any]:
            reservation = self.server.payout_ledger_directory.create_hold(
                account_id=str(validation.get("account_id") or ""),
                wallet_address=str(validation.get("wallet_address") or ""),
                request_id=request_id, session_id=session_id, worker_id=worker_id,
                run_id=run_id, partition=partition,
                selected_price=dict(selected_worker.get("price") or {}),
                requester_max_price=(work.get("max_price") if isinstance(work, dict) else None),
            )
            price = dict(selected_worker.get("price") or {})
            price["credit_wei"] = str(reservation["credit_wei"])
            return self.server.accepted_work_session_directory.record_accepted(
                session_id=session_id, run_id=run_id, request_id=request_id,
                requester_msk_id=str(validation.get("multisession_key_id") or ""),
                requester_account_id=str(validation.get("account_id") or ""),
                requester_wallet_address=str(validation.get("wallet_address") or ""),
                worker_id=worker_id, worker_connection_id=owner_connection_id,
                owner_hub_id=owner_hub_id, owner_hub_url=owner_hub_url,
                partition=partition, task_queue=task_queue, work=work,
                worker_acceptance=worker_acceptance,
                temporal_intent=self.server.temporal_runtime is not None,
                payout={
                    "unit": str(price.get("unit") or "credit"),
                    "pricing_mode": "stable_market_price",
                    "worker_account_id": wallet_account_id(selected_worker.get("worker_wallet_address")),
                    "selected_price": price,
                    "requester_max_price": dict(work.get("max_price") or {}),
                    "hold_id": str(reservation["hold_id"]), "hold_status": "held",
                    "reserved_credits": str(reservation.get("amount") or "0"),
                    "charge_id": "", "worker_earning_id": "",
                    "charged_credits": "0", "released_credits": "0",
                    "settlement_status": "not_earned", "payment_status": "held",
                    "direct_spend": False,
                },
            )

        try:
            store = self.server.payout_ledger_directory.store
            atomic = getattr(store, "atomic", None)
            accepted_session = atomic(_record_acceptance) if callable(atomic) else _record_acceptance()
        except StableHubWorkerSessionError as exc:
            self.server.worker_market_directory.record_session_finished(
                worker_id=worker_id, connection_id=owner_connection_id,
            )
            self._send_json(402 if "funds" in str(exc) else 409, {
                "ok": False, "error": str(exc),
                "message": "Requester credit reservation or session acceptance failed.",
                "request_id": request_id, "worker_id": worker_id,
                "partition": partition, "hub_id": self.server.hub.hub_id,
                "cluster_id": self.server.topology.cluster_id,
            })
            return

        # Capacity was reserved before the offer was sent, so acceptance only
        # confirms the session record.  Terminal success/failure releases the slot.
        market_record = self.server.worker_market_directory.get_worker(worker_id) or market_record

        continuation_url = stable_work_session_continuation_url(owner_hub_url, session_id)
        self._send_json(
            200,
            {
                "ok": True,
                "accepted": True,
                "session_id": session_id,
                "run_id": run_id,
                "request_id": request_id,
                "worker_id": worker_id,
                "owner_hub_id": owner_hub_id,
                "owner_hub_url": owner_hub_url,
                "partition": partition,
                "task_queue": task_queue,
                "execution_hub": {
                    "hub_id": owner_hub_id,
                    "hub_url": owner_hub_url,
                },
                "continuation_url": continuation_url,
                "continuation": {
                    "direct": True,
                    "stream_path": stable_work_session_stream_path(session_id),
                    "hub_id": owner_hub_id,
                    "hub_url": owner_hub_url,
                },
                "execution": accepted_session.get("execution", {}),
                "payout": accepted_session.get("payout", {}),
                "accepted_session": accepted_session,
                "worker_market": market_record,
                "hub_id": self.server.hub.hub_id,
                "cluster_id": self.server.topology.cluster_id,
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler method
        path = urlparse(self.path).path
        self._log_event(f"POST {path} started")
        try:
            if path == "/api/hub/v1/credits/multisession-keys/request":
                self._send_json(200, self.server.multisession_key_service.request_key(self._read_json()))
                return
            if path == "/api/hub/v1/credits/multisession-keys/validate":
                self._send_json(200, self.server.multisession_key_service.validate_key(self._read_json()))
                return
            if path == "/api/hub/v1/credits/wallet-funding/complete":
                body = self._read_json()
                if not self.server.bridge_completion_override:
                    deployment = load_bridge_deployment(self.server.bridge_configuration)
                    topology_chain_id = str(self.server.topology.network.get("chain_id") or "").strip()
                    if not topology_chain_id or str(deployment.chain_id) != topology_chain_id:
                        raise RuntimeError("bridge deployment chain does not match Stable Hub topology")
                    if body.get("chain_id") not in (None, "") and str(body["chain_id"]) != str(deployment.chain_id):
                        raise ValueError("bridge completion chain_id does not match deployment")
                    if body.get("contract_address") not in (None, "") and normalize_evm_address(
                        body["contract_address"], field_name="contract_address"
                    ) != deployment.contract_address:
                        raise ValueError("bridge completion contract_address does not match deployment")
                result = self.server.credit_bridge_completion.complete_wallet_funding_deposit(body)
                self._send_json(200, result)
                return
            if path in {
                "/api/hub/v1/credits/wallet-funding/import",
                "/api/hub/v1/credits/bridge-reconciliation/execute",
                "/api/hub/v1/credits/bridge-reconciliation/record",
            }:
                # Consume the request body before replying on an HTTP/1.1
                # keep-alive connection, otherwise its bytes become the next
                # (malformed) request and can abort the client's 501 response.
                self._read_json()
                self._send_json(501, {
                    "ok": False,
                    "error": "stable_hub_unverified_financial_operation_disabled",
                    "path": path,
                    "hub_id": self.server.hub.hub_id,
                })
                return
            if path == "/api/hub/v1/work/requests":
                self._handle_work_request()
                return
            if path.startswith("/api/hub/v1/workers/") and path.endswith("/payout/claim"):
                worker_id = path[len("/api/hub/v1/workers/") : -len("/payout/claim")]
                self._handle_worker_payout_claim(worker_id)
                return
            if path.startswith("/api/hub/v1/workers/") and path.endswith("/payout/settlements"):
                worker_id = path[len("/api/hub/v1/workers/") : -len("/payout/settlements")]
                self._handle_worker_payout_settlement(worker_id)
                return
            if path.startswith("/api/hub/v1/workers/") and path.endswith("/payout/bridge"):
                worker_id = path[len("/api/hub/v1/workers/") : -len("/payout/bridge")]
                self._handle_worker_bridge_payout(worker_id)
                return
            if path.startswith("/api/hub/v1/payout/bridge/") and path.endswith("/confirm"):
                bridge_payout_id = path[len("/api/hub/v1/payout/bridge/") : -len("/confirm")]
                self._handle_bridge_payout_confirm(bridge_payout_id)
                return
            if path.startswith("/api/hub/v1/payout/bridge/") and path.endswith("/fail"):
                bridge_payout_id = path[len("/api/hub/v1/payout/bridge/") : -len("/fail")]
                self._handle_bridge_payout_fail(bridge_payout_id)
                return
        except StableHubMultiSessionKeyError as exc:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": str(exc),
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return
        except ValueError as exc:
            self._send_json(
                400,
                {
                    "ok": False,
                    "error": str(exc),
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return
        except (FileNotFoundError, OSError) as exc:
            self._send_json(503, {
                "ok": False,
                "error": "bridge_completion_unavailable",
                "message": type(exc).__name__,
                "hub_id": self.server.hub.hub_id,
            })
            return
        except RuntimeError as exc:
            self._send_json(
                503,
                {
                    "ok": False,
                    "error": str(exc),
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

        except Exception as exc:
            if getattr(exc, "code", None) != 1020:
                raise
            self._send_json(503, {
                "ok": False, "error": "foundationdb_transaction_conflict",
                "retryable": True, "hub_id": self.server.hub.hub_id,
            })
            return
        self._send_json(
            404,
            {
                "ok": False,
                "error": "not_found",
                "path": path,
                "hub_id": self.server.hub.hub_id,
            },
        )

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler method
        path = urlparse(self.path).path
        if path == "/api/hub/v1/workers/live-session":
            self._log_event(f"GET {path} websocket start")
            self._handle_worker_live_session_websocket()
            return
        if path.startswith("/api/hub/v1/work/sessions/") and path.endswith("/stream"):
            self._handle_work_session_stream(path)
            return
        if path == "/api/hub/v1/credits/wallet-funding/completion":
            self._send_json(200, self.server.credit_bridge_completion.status())
            return
        if path == "/api/hub/v1/credits/balance":
            query = parse_qs(urlparse(self.path).query)
            wallet = str(query.get("wallet_address", [""])[0] or "").strip()
            if not wallet:
                self._send_json(400, {"ok": False, "error": "wallet_address is required"})
                return
            try:
                wallet = normalize_evm_address(wallet, field_name="wallet_address")
                account_id = wallet_account_id(wallet)
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            stored = self.server.payout_ledger_directory.get_account(account_id)
            if isinstance(stored, dict):
                account = dict(stored)
                if "available_credit_wei" not in account:
                    account["available_credit_wei"] = str(credit_decimal_text_to_wei(str(account.get("available_credits", "0"))))
                account.setdefault("wallet_address", wallet)
            else:
                account = {
                    "account_id": account_id, "wallet_address": wallet,
                    "owner_address": wallet, "available_credit_wei": "0",
                    "held_credit_wei": "0", "spent_credit_wei": "0",
                    "bridge_completed_credit_wei": "0",
                }
            available = int(account["available_credit_wei"])
            account["available_credits"] = credit_wei_to_whole_credits_floor(available)
            account["available_credits_display"] = credit_wei_to_decimal_text(available)
            self._send_json(200, {
                "ok": True, "wallet_address": wallet, "account_id": account_id,
                "account": account, "unit": "compute_credit",
                "funding_model": "hub_credit_bridge_escrow_wallet_v2",
            })
            return
        if path == "/api/hub/v1/credits/bridge-deposit":
            query = parse_qs(urlparse(self.path).query)
            fields = {name: str(query.get(name, [""])[0] or "").strip()
                      for name in ("chain_id", "contract_address", "deposit_id", "wallet_address")}
            try:
                evidence = read_stable_hub_bridge_deposit_provenance(
                    self.server.payout_ledger_directory, **fields,
                )
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            self._send_json(200, evidence)
            return
        if path == "/api/hub/v1/credits/charges":
            query = parse_qs(urlparse(self.path).query)
            request_id = str(query.get("request_id", [""])[0] or "").strip()
            account_id = str(query.get("account_id", [""])[0] or "").strip()
            try:
                limit = min(500, max(1, int(query.get("limit", ["100"])[0])))
            except (TypeError, ValueError):
                self._send_json(400, {"ok": False, "error": "limit must be an integer"})
                return
            raw = self.server.payout_ledger_directory.store.load().get("payout_charges", {})
            charges = [dict(item) for item in raw.values() if isinstance(item, dict)] if isinstance(raw, dict) else []
            if request_id:
                charges = [item for item in charges if item.get("request_id") == request_id]
            if account_id:
                charges = [item for item in charges if item.get("account_id") == account_id]
            charges.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
            charges = charges[:limit]
            self._send_json(200, {"ok": True, "charges": charges, "charge_count": len(charges)})
            return
        if path == "/api/hub/v1/payout/status":
            self._send_json(
                200,
                {
                    "ok": True,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                    "payout": self.server.payout_ledger_directory.status(),
                },
            )
            return
        if path == "/health":
            runtime = self.server.temporal_runtime
            ready = runtime is None or runtime.is_ready
            self._send_json(
                200 if ready else 503,
                {
                    "ok": ready,
                    "service": "main_computer.stable_hub",
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                    "temporal": {
                        "configured": runtime is not None,
                        "ready": runtime.is_ready if runtime is not None else False,
                        "mode": "durable_lifecycle_observation_no_worker_reexecution",
                    },
                },
            )
            return
        if path == "/api/hub/v1/hub-identity":
            self._send_json(200, dict(self.server.identity))
            return
        if path == "/api/hub/v1/topology":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "main_computer.stable_hub",
                    "hub_id": self.server.hub.hub_id,
                    "topology": stable_hub_topology_to_dict(self.server.topology),
                    "contract": stable_hub_contract(),
                },
            )
            return
        self._send_json(
            404,
            {
                "ok": False,
                "error": "not_found",
                "path": path,
                "hub_id": self.server.hub.hub_id,
            },
        )


def create_stable_hub_server(
    *,
    topology: StableHubTopology,
    hub_id: str,
    bind_host: str | None = None,
    bind_port: int | None = None,
    multisession_key_store: StableHubMultiSessionKeyStore | None = None,
    worker_session_store: StableHubWorkerSessionStore | None = None,
    work_offer_timeout_seconds: float = 10.0,
    bridge_completion_service: Any | None = None,
) -> StableHubHTTPServer:
    hub = topology.hub_by_id(hub_id)
    default_host, default_port = _url_host_port(hub.hub_url)
    address = (bind_host or default_host, default_port if bind_port is None else bind_port)
    return StableHubHTTPServer(
        address,
        topology,
        hub,
        multisession_key_store=multisession_key_store,
        worker_session_store=worker_session_store,
        work_offer_timeout_seconds=work_offer_timeout_seconds,
        bridge_completion_service=bridge_completion_service,
    )


def serve_stable_hub(
    *,
    topology_path: str | Path,
    hub_id: str,
    bind_host: str | None = None,
    bind_port: int | None = None,
) -> None:
    topology = load_stable_hub_topology(topology_path)
    server = create_stable_hub_server(
        topology=topology,
        hub_id=hub_id,
        bind_host=bind_host,
        bind_port=bind_port,
    )
    actual_host, actual_port = server.server_address
    identity = server.identity
    print(
        f"Stable Hub server: {identity['hub_id']} "
        f"{identity['hub_url']} listening on {actual_host}:{actual_port}",
        flush=True,
    )
    print(f"Stable Hub cluster: {identity['cluster_id']}", flush=True)
    print(
        "Stable Hub storage: "
        f"{identity['storage']['backend']} "
        f"{identity['storage']['cluster_file']} "
        f"namespace={identity['storage']['namespace']}",
        flush=True,
    )
    print("Stable Hub contract: multisession-wallet long-lived-msk-session", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one concrete stable Hub from a topology file.")
    parser.add_argument(
        "--topology",
        required=True,
        help="Path to a stable Hub topology JSON file.",
    )
    parser.add_argument(
        "--hub-id",
        required=True,
        help="Concrete hub_id from the topology to serve.",
    )
    parser.add_argument(
        "--bind-host",
        default=None,
        help="Override bind host for tests/dev. Defaults to the host in the concrete hub_url.",
    )
    parser.add_argument(
        "--bind-port",
        type=int,
        default=None,
        help="Override bind port for tests/dev. Defaults to the port in the concrete hub_url.",
    )
    parser.add_argument(
        "--print-identity",
        action="store_true",
        help="Print this Hub's resolved identity as JSON and exit.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.print_identity:
            topology = load_stable_hub_topology(args.topology)
            identity = build_hub_identity(topology, args.hub_id)
            print(json.dumps(identity, indent=2, sort_keys=True))
            return 0
        serve_stable_hub(
            topology_path=args.topology,
            hub_id=args.hub_id,
            bind_host=args.bind_host,
            bind_port=args.bind_port,
        )
        return 0
    except StableHubTopologyError as exc:
        print(f"stable Hub startup failed: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"stable Hub startup failed: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
