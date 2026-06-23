from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from main_computer.stable_hub_msk import (
    StableHubMultiSessionKeyError,
    StableHubMultiSessionKeyService,
    StableHubMultiSessionKeyStore,
    stable_multisession_store_from_topology,
)
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
)


def stable_hub_contract() -> dict[str, str]:
    """Return the stable Hub contract advertised by this low-entropy runtime."""

    return {
        "auth": "multisession-wallet",
        "worker_connection": "long-lived-msk-session",
        "heartbeat": "connection-ping-pong",
        "availability_source": "live-worker-session-owner",
        "routing": "entry-hub-reserves-with-worker-home-hub-then-returns-concrete-execution-hub-url",
        "worker_live_session_transport": "websocket",
        "worker_heartbeat": "live-connection-ping-pong",
        "rest_worker_heartbeat": "forbidden",
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


class StableHubRequestHandler(BaseHTTPRequestHandler):
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
        if not hold_id:
            raise StableHubWorkerSessionError("accepted work session does not have a payout hold.")
        if message_type == "worker.work.result":
            result_payload = message.get("result")
            if not isinstance(result_payload, dict):
                result_payload = {"value": result_payload}
            charge = self.server.payout_ledger_directory.charge_hold(
                hold_id=hold_id,
                session_id=session_id,
                request_id=request_id,
                worker_id=worker_id,
                result=result_payload,
                metadata={"message_type": message_type, "hub_id": self.server.hub.hub_id},
            )
            hold = dict(charge.get("hold") or {})
            charge_record = dict(charge.get("charge") or {})
            earning_record = dict(charge.get("worker_earning") or {})
            payout.update(
                {
                    "hold_status": str(hold.get("status") or "charged"),
                    "charge_id": str(charge_record.get("charge_id") or ""),
                    "worker_earning_id": str(earning_record.get("earning_id") or ""),
                    "charged_credits": str(charge_record.get("amount") or hold.get("amount") or "0"),
                    "released_credits": "0",
                    "settlement_status": str(earning_record.get("settlement_status") or "earned"),
                }
            )
            updated = self.server.accepted_work_session_directory.record_succeeded(
                session_id=session_id,
                worker_connection_id=connection_id,
                worker_result={"type": message_type, "result": result_payload},
                payout=payout,
            )
            self.server.worker_market_directory.record_session_finished(
                worker_id=worker_id,
                connection_id=connection_id,
            )
            session.send_json(
                {
                    "type": "hub.work.result.accepted",
                    "ok": True,
                    "session_id": session_id,
                    "request_id": request_id,
                    "status": updated.get("status"),
                    "payout": updated.get("payout", {}),
                }
            )
            return

        if message_type == "worker.work.failed":
            failure_payload = message.get("error")
            if not isinstance(failure_payload, dict):
                failure_payload = {
                    "error": str(failure_payload or message.get("message") or "worker_failed")
                }
            released = self.server.payout_ledger_directory.release_hold(
                hold_id=hold_id,
                reason="worker_failed",
                metadata={"message_type": message_type, "failure": failure_payload},
            )
            payout.update(
                {
                    "hold_status": str(released.get("status") or "released"),
                    "release_reason": str(released.get("release_reason") or "worker_failed"),
                    "charged_credits": "0",
                    "released_credits": str(released.get("amount") or "0"),
                    "settlement_status": "not_settled",
                }
            )
            updated = self.server.accepted_work_session_directory.record_failed(
                session_id=session_id,
                worker_connection_id=connection_id,
                worker_failure={"type": message_type, "error": failure_payload},
                payout=payout,
            )
            self.server.worker_market_directory.record_session_finished(
                worker_id=worker_id,
                connection_id=connection_id,
            )
            session.send_json(
                {
                    "type": "hub.work.failed.accepted",
                    "ok": True,
                    "session_id": session_id,
                    "request_id": request_id,
                    "status": updated.get("status"),
                    "payout": updated.get("payout", {}),
                }
            )
            return

        raise StableHubWorkerSessionError("unsupported worker terminal message.")

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
            worker_id = normalize_worker_id(auth_message.get("worker_id"))
            authorization = auth_message.get("multisession_authorization")
            if not isinstance(authorization, dict):
                key_id = str(auth_message.get("multisession_key_id") or "").strip()
                authorization = {"kind": "multisession_key", "multisession_key_id": key_id} if key_id else {}
            validation = self.server.multisession_key_service.validate_key(
                {"multisession_authorization": authorization}
            )
            if validation.get("valid") is not True:
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

            connection_id = new_connection_id()
            key_id = str(validation.get("multisession_key_id") or authorization.get("multisession_key_id") or "")
            owner = self.server.worker_session_directory.record_connected(
                worker_id=worker_id,
                connection_id=connection_id,
                multisession_key_id=key_id,
                wallet_address=str(validation.get("wallet_address") or ""),
                account_id=str(validation.get("account_id") or ""),
            )
            raw_market_profile = auth_message.get("market")
            if not isinstance(raw_market_profile, dict):
                raw_market_profile = auth_message.get("worker_market")
            market_profile = normalize_worker_market_profile(
                raw_market_profile if isinstance(raw_market_profile, dict) else None
            )
            market_record = self.server.worker_market_directory.record_worker_live(
                worker_id=worker_id,
                owner=owner,
                market_profile=market_profile,
                worker_msk_id=key_id,
                worker_wallet_address=str(validation.get("wallet_address") or ""),
                worker_account_id=str(validation.get("account_id") or ""),
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
            session.send_json(
                {
                    "type": "hub.auth.accepted",
                    "ok": True,
                    "hub_id": self.server.hub.hub_id,
                    "hub_url": self.server.hub.hub_url,
                    "cluster_id": self.server.topology.cluster_id,
                    "worker_id": worker_id,
                    "connection_id": connection_id,
                    "owner": owner,
                    "market": market_record,
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
                if message_type == "worker.pong":
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
        finally:
            if worker_id and connection_id:
                closed_owner = self.server.worker_session_directory.record_closed(
                    worker_id=worker_id,
                    connection_id=connection_id,
                    reason="socket_closed",
                ) or {}
                self.server.worker_market_directory.record_worker_closed(
                    worker_id=worker_id,
                    connection_id=connection_id,
                    reason="socket_closed",
                )
                removed = self.server.remove_live_worker_session(connection_id)
                if removed is not None:
                    removed.mark_closed(
                        reason="socket_closed",
                        closed_at=str(closed_owner.get("closed_at") or ""),
                    )



    def _handle_work_session_stream(self, path: str) -> None:
        prefix = "/api/hub/v1/work/sessions/"
        suffix = "/stream"
        raw_session_id = path[len(prefix) : -len(suffix)]
        try:
            session_id = normalize_session_id(raw_session_id)
        except StableHubWorkerSessionError as exc:
            if payout_hold:
                self.server.payout_ledger_directory.release_hold(
                    hold_id=str(payout_hold.get("hold_id") or ""),
                    reason="worker_acceptance_rejected",
                    metadata={"request_id": request_id, "session_id": session_id},
                )
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
                "X-Main-Computer-Stable-Hub-Handoff-From": self.server.hub.hub_id,
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
            raise ConnectionError(f"owner Hub handoff failed: {exc}") from exc


    def _validate_worker_payout_authorization(self, worker_id: str, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        worker_id = normalize_worker_id(worker_id)
        validation = self.server.multisession_key_service.validate_key(
            {"multisession_authorization": body.get("multisession_authorization")}
        )
        if validation.get("valid") is not True:
            raise StableHubWorkerSessionError("worker_payout_msk_invalid")
        market_worker = self.server.worker_market_directory.get_worker(worker_id)
        if not isinstance(market_worker, dict):
            raise StableHubWorkerSessionError("worker_not_known_for_payout")
        expected_key_id = str(market_worker.get("worker_msk_id") or "")
        if expected_key_id and expected_key_id != str(validation.get("multisession_key_id") or ""):
            raise StableHubWorkerSessionError("worker_payout_msk_does_not_match_worker")
        return validation, market_worker

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
            self._send_json(403 if "msk" in str(exc) else 400, {"ok": False, "error": str(exc), "hub_id": self.server.hub.hub_id})
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
            self._send_json(403 if "msk" in str(exc) else 400, {"ok": False, "error": str(exc), "hub_id": self.server.hub.hub_id})
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
            self._send_json(403 if "msk" in str(exc) else 400, {"ok": False, "error": str(exc), "hub_id": self.server.hub.hub_id})
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

        partition = stable_partition_key_for_work(work)
        selected_worker = self.server.worker_market_directory.select_worker_for_work(work)
        if selected_worker is None:
            self._send_json(
                409,
                {
                    "ok": False,
                    "error": "worker_not_live",
                    "message": "No live worker matched the request market constraints.",
                    "request_id": request_id,
                    "partition": partition,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

        worker_id = normalize_worker_id(selected_worker.get("worker_id"))
        owner = self.server.worker_session_directory.get_owner(worker_id)
        if not owner or owner.get("status") != "live":
            self._send_json(
                409,
                {
                    "ok": False,
                    "error": "worker_not_live",
                    "message": "Selected worker has no live owner record.",
                    "request_id": request_id,
                    "worker_id": worker_id,
                    "partition": partition,
                    "owner": owner,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

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

        owner_connection_id = str(owner.get("connection_id") or "")
        selected_connection_id = str(selected_worker.get("connection_id") or "")
        owner_lease_epoch = int(owner.get("lease_epoch") or 0)
        selected_lease_epoch = int(selected_worker.get("lease_epoch") or 0)
        if (
            not owner_connection_id
            or owner_connection_id != selected_connection_id
            or owner_lease_epoch != selected_lease_epoch
        ):
            self._send_json(
                409,
                {
                    "ok": False,
                    "error": "worker_owner_changed",
                    "message": "Selected worker owner record changed before work could be offered.",
                    "request_id": request_id,
                    "worker_id": worker_id,
                    "partition": partition,
                    "owner": owner,
                    "selected_worker": selected_worker,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

        live_session = self.server.get_live_worker_session(owner_connection_id)
        if live_session is None or not live_session.is_live:
            self._send_json(
                409,
                {
                    "ok": False,
                    "error": "worker_owner_not_local",
                    "message": "Owner record points at this Hub, but no local live socket is present. Worker must reconnect.",
                    "request_id": request_id,
                    "worker_id": worker_id,
                    "partition": partition,
                    "owner": owner,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

        session_id = new_session_id()
        run_id = new_run_id()
        task_queue = stable_task_queue_for_partition(partition)
        payout_hold: dict[str, Any] = {}
        try:
            payout_hold = self.server.payout_ledger_directory.create_hold(
                account_id=str(validation.get("account_id") or ""),
                wallet_address=str(validation.get("wallet_address") or ""),
                request_id=request_id,
                session_id=session_id,
                run_id=run_id,
                worker_id=worker_id,
                selected_price=dict(selected_worker.get("price") or {}),
                requester_max_price=(work.get("max_price") if isinstance(work, dict) else None),
                partition=partition,
                metadata={
                    "pricing_mode": "stable_market_price",
                    "selected_worker": selected_worker,
                    "hub_id": self.server.hub.hub_id,
                },
            )
        except StableHubWorkerSessionError as exc:
            self._send_json(
                402,
                {
                    "ok": False,
                    "error": str(exc),
                    "message": "Requester credits could not be held for the selected worker.",
                    "request_id": request_id,
                    "worker_id": worker_id,
                    "partition": partition,
                    "selected_worker": selected_worker,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                },
            )
            return

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
            if payout_hold:
                self.server.payout_ledger_directory.release_hold(
                    hold_id=str(payout_hold.get("hold_id") or ""),
                    reason="worker_offer_timeout",
                    metadata={"request_id": request_id, "session_id": session_id},
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
            if payout_hold:
                self.server.payout_ledger_directory.release_hold(
                    hold_id=str(payout_hold.get("hold_id") or ""),
                    reason="worker_owner_not_local",
                    metadata={"request_id": request_id, "session_id": session_id},
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
            if payout_hold:
                self.server.payout_ledger_directory.release_hold(
                    hold_id=str(payout_hold.get("hold_id") or ""),
                    reason="worker_acceptance_rejected",
                    metadata={"request_id": request_id, "session_id": session_id},
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

        accepted_session = self.server.accepted_work_session_directory.record_accepted(
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            requester_msk_id=str(validation.get("multisession_key_id") or ""),
            requester_account_id=str(validation.get("account_id") or ""),
            requester_wallet_address=str(validation.get("wallet_address") or ""),
            worker_id=worker_id,
            worker_connection_id=owner_connection_id,
            owner_hub_id=owner_hub_id,
            owner_hub_url=owner_hub_url,
            partition=partition,
            task_queue=task_queue,
            work=work,
            worker_acceptance=worker_acceptance,
            payout={
                "unit": str(payout_hold.get("unit") or "credit"),
                "pricing_mode": "stable_market_price",
                "selected_price": dict(payout_hold.get("selected_price") or {}),
                "requester_max_price": dict(payout_hold.get("requester_max_price") or {}),
                "hold_id": str(payout_hold.get("hold_id") or ""),
                "hold_status": str(payout_hold.get("status") or "held"),
                "charge_id": "",
                "worker_earning_id": "",
                "settlement_status": "not_settled",
            },
        )
        market_record = self.server.worker_market_directory.record_session_accepted(
            worker_id=worker_id,
            connection_id=owner_connection_id,
        )

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
        if path.startswith("/api/hub/v1/workers/") and path.endswith("/owner"):
            worker_id = path[len("/api/hub/v1/workers/") : -len("/owner")]
            try:
                owner = self.server.worker_session_directory.get_owner(worker_id)
            except StableHubWorkerSessionError as exc:
                self._send_json(400, {"ok": False, "error": str(exc), "hub_id": self.server.hub.hub_id})
                return
            self._send_json(
                200,
                {
                    "ok": True,
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
                    "worker_id": worker_id,
                    "owner": owner,
                },
            )
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
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "main_computer.stable_hub",
                    "hub_id": self.server.hub.hub_id,
                    "cluster_id": self.server.topology.cluster_id,
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
