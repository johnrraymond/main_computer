"""Headless Stable Hub live-session Worker transport, usable by installed CLI.

This module must never import viewport pages or game assets.
"""
from __future__ import annotations

import base64
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
import socket
import ssl
import threading
import time
from typing import Any
from urllib.parse import urlparse

from main_computer.credit_units import credit_decimal_text_to_wei

DEFAULT_WORKER_LIVE_SESSION_LOCAL_AI_TIMEOUT_SECONDS = 300.0


def _worker_runtime_public_redacted(value: Any) -> Any:
    private_keys = {
        "worker_id",
        "workerId",
        "node_id",
        "nodeId",
        "worker_node_id",
        "workerNodeId",
        "requested_worker_node_id",
        "requestedWorkerNodeId",
        "selected_worker_node_id",
        "selectedWorkerNodeId",
        "worker_instance_id",
        "workerInstanceId",
        "selected_worker_instance_id",
        "selectedWorkerInstanceId",
        "connection_id",
        "connectionId",
        "worker_connection_id",
        "workerConnectionId",
        "worker_wallet_address",
        "workerWalletAddress",
        "wallet_address",
        "walletAddress",
        "wallet",
        "worker_account_id",
        "workerAccountId",
        "account_id",
        "accountId",
        "worker_msk_id",
        "workerMskId",
        "multisession_key_id",
        "multisessionKeyId",
        "active_multisession_key",
        "activeMultisessionKey",
        "multisession_authorization",
        "multisessionAuthorization",
        "credit_wallet",
        "creditWallet",
        "payout_wallet_address",
        "payoutWalletAddress",
        "owner",
        "market",
        "selected_worker",
        "selectedWorker",
        "accepted_session",
        "acceptedSession",
        "signedWorkerConnection",
        "signed_worker_connection",
        "workerHubRegistration",
        "worker_hub_registration",
    }
    if isinstance(value, dict):
        return {
            str(key): _worker_runtime_public_redacted(item)
            for key, item in value.items()
            if str(key) not in private_keys
        }
    if isinstance(value, list):
        return [_worker_runtime_public_redacted(item) for item in value]
    return value


class _WorkerHubLiveSessionClient:
    """Minimal stdlib WebSocket client for the Hub worker live-session contract.

    Worker availability is no longer a REST heartbeat.  The Hub owns liveness by
    keeping a worker WebSocket open, sending JSON ``hub.ping`` messages, and
    expecting JSON ``worker.pong`` replies over the same connection.
    """

    endpoint_path = "/api/hub/v1/workers/live-session"

    def __init__(
        self,
        *,
        hub_url: str,
        worker_id: str,
        auth_message: dict[str, Any],
        timeout_s: float = 5.0,
        work_executor: Any | None = None,
        work_canceller: Any | None = None,
    ) -> None:
        self.hub_url = str(hub_url or "").rstrip("/")
        self.worker_id = str(worker_id or "").strip()
        self.auth_message = json.loads(json.dumps(auth_message, ensure_ascii=False))
        self.auth_message["execution_protocol"] = "durable-authorization-v1"
        self.timeout_s = max(1.0, float(timeout_s or 5.0))
        self.work_executor = work_executor
        self.work_canceller = work_canceller
        self.fingerprint = json.dumps(
            {
                "hub_url": self.hub_url,
                "worker_id": self.worker_id,
                "auth": self._stable_auth_fingerprint_payload(self.auth_message),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.connection_id = ""
        self.websocket_upgrade_status = ""
        self.accepted: dict[str, Any] = {}
        self.owner: dict[str, Any] = {}
        self.market: dict[str, Any] = {}
        self.last_pong: dict[str, Any] = {}
        self.last_offer: dict[str, Any] = {}
        self._pending_authorizations: dict[str, dict[str, Any]] = {}
        self._authorized_sessions: set[str] = set()
        self.last_result: dict[str, Any] = {}
        self._delta_seq_by_session: dict[str, int] = {}
        self._delta_content_by_session: dict[str, str] = {}
        self.active_work_count = 0
        self.last_error = ""
        self.opened_at = ""
        self.closed_at = ""
        self.close_reason = ""
        self._socket: socket.socket | None = None
        self._send_lock = threading.RLock()
        # A/B tested on Windows: concurrent recv()/sendall() on this SSL
        # socket loses paid Worker sessions. Serialize individual socket I/O
        # calls (not complete WebSocket frames or the idle receive loop).
        self._tls_io_lock = threading.RLock()
        self.tls_io_mode = "serialized"
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._keepalive_thread: threading.Thread | None = None
        self.keepalive_interval_s = 10.0

    @property
    def is_alive(self) -> bool:
        sock = self._socket
        thread = self._reader_thread
        return (
            sock is not None
            and not self._stop_event.is_set()
            and not self.closed_at
            and (thread is None or thread.is_alive())
        )

    @property
    def has_active_work(self) -> bool:
        with self._state_lock:
            return self.active_work_count > 0

    @staticmethod
    def _stable_auth_fingerprint_payload(auth_message: dict[str, Any]) -> dict[str, Any]:
        """Return only durable auth/market inputs used to decide socket replacement.

        The full ``worker.auth`` payload includes volatile availability snapshots
        such as last user activity and local AI capacity.  Reconnecting whenever
        those fields change can close the selected websocket while an accepted
        live-session job is still running.  The Hub learns current capacity from
        the live session itself; this fingerprint is only for deciding whether a
        viewport-side websocket must be replaced.
        """

        market = dict(auth_message.get("market") or {}) if isinstance(auth_message.get("market"), dict) else {}
        auth = (
            dict(auth_message.get("multisession_authorization") or {})
            if isinstance(auth_message.get("multisession_authorization"), dict)
            else {}
        )
        return {
            "type": str(auth_message.get("type") or ""),
            "chain_id": str(auth_message.get("chain_id") or auth.get("chain_id") or ""),
            "model": str(auth_message.get("model") or ""),
            "models": [str(item) for item in auth_message.get("models", [])] if isinstance(auth_message.get("models"), list) else [],
            "authorization": {
                "kind": str(auth.get("kind") or ""),
                "key_id": str(auth.get("key_id") or auth.get("multisession_key_id") or ""),
                "wallet_address": str(auth.get("wallet_address") or ""),
                "chain_id": str(auth.get("chain_id") or ""),
            },
            "market": {
                "rings": [str(item) for item in market.get("rings", [])] if isinstance(market.get("rings"), list) else [],
                "partitions": [str(item) for item in market.get("partitions", [])] if isinstance(market.get("partitions"), list) else [],
                "capabilities": [str(item) for item in market.get("capabilities", [])] if isinstance(market.get("capabilities"), list) else [],
                "models": [str(item) for item in market.get("models", [])] if isinstance(market.get("models"), list) else [],
                "max_concurrency": int(market.get("max_concurrency") or auth_message.get("max_concurrency") or 1),
                "price": dict(market.get("price") or {}) if isinstance(market.get("price"), dict) else {},
            },
        }

    def start(self) -> dict[str, Any]:
        self._socket = self._open_socket()
        self.opened_at = datetime.now(timezone.utc).isoformat()
        self._send_json(self.auth_message)

        deadline = time.monotonic() + self.timeout_s
        saw_accepted = False
        saw_pong = False
        received_types: list[str] = []
        while time.monotonic() < deadline:
            try:
                message = self._recv_json()
            except TimeoutError:
                continue
            message_type = str(message.get("type") or "")
            if len(received_types) < 12:
                received_types.append(message_type or "<missing-type>")
            if message_type == "hub.auth.rejected":
                details = []
                for field in ("reason_code", "user_message", "hub_id"):
                    value = str(message.get(field) or "").strip()
                    if value:
                        details.append(f"{field}={value}")
                suffix = f": {'; '.join(details)}" if details else "."
                raise RuntimeError(f"Hub rejected worker.auth{suffix}")
            if message_type == "hub.error":
                raise RuntimeError(str(message.get("error") or message))
            self._handle_hub_message(message)
            if message_type == "hub.auth.accepted":
                if (message.get("service") != "main_computer.exp_fdb_hub"
                        and message.get("execution_protocol") != "durable-authorization-v1"):
                    raise RuntimeError("Stable Hub does not support durable execution authorization")
                saw_accepted = True
            elif message_type == "hub.pong.accepted":
                saw_pong = True
            if saw_accepted and saw_pong:
                break

        if not saw_accepted:
            raise RuntimeError(
                "Hub live-session did not accept worker.auth "
                f"after {self.timeout_s:g}s; hub={self.hub_url}; "
                f"upgrade={self.websocket_upgrade_status or 'unknown'}; "
                f"frames_received={received_types}."
            )
        if not saw_pong:
            raise RuntimeError(
                "Hub live-session did not complete the initial ping/pong keepalive "
                f"after {self.timeout_s:g}s; hub={self.hub_url}; "
                f"frames_received={received_types}."
            )

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"main-computer-worker-live-session-{self.worker_id}",
            daemon=True,
        )
        self._reader_thread.start()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            name=f"main-computer-worker-live-session-keepalive-{self.worker_id}",
            daemon=True,
        )
        self._keepalive_thread.start()
        return self.snapshot()

    def _diagnostic_transport_event(self, event: str, *, exc=None, close_code=None) -> None:
        """Optional metadata hook. Diagnostics must never alter transport behavior."""
        hook = getattr(self, "_record_transport_event", None)
        if callable(hook):
            try:
                hook(event, exc=exc, close_code=close_code)
            except Exception:
                pass

    def close(self, reason: str = "closed_by_runtime") -> None:
        if self._stop_event.is_set():
            return
        self._diagnostic_transport_event("explicit_close_requested")
        self._stop_event.set()
        with self._state_lock:
            self._pending_authorizations.clear()
        self.close_reason = str(reason or "closed")
        try:
            self._send_json({"type": "worker.close", "reason": self.close_reason})
        except Exception:
            pass
        try:
            self._send_close_frame()
            self._diagnostic_transport_event("explicit_websocket_close_sent")
        except Exception as exc:
            self._diagnostic_transport_event("explicit_websocket_close_send_failed", exc=exc)
        sock = self._socket
        self._socket = None
        if sock is not None:
            self._diagnostic_transport_event("explicit_socket_close_invoked")
            try:
                with self._tls_io_guard():
                    sock.close()
            except OSError as exc:
                self._diagnostic_transport_event("explicit_socket_close_failed", exc=exc)
        if not self.closed_at:
            self.closed_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            # Only Hub-authenticated, public market constraints are surfaced.
            # No worker identifiers, wallet, MSK, connection IDs or raw offer.
            market = self.accepted.get("market") if isinstance(self.accepted.get("market"), dict) else {}
            market_price = market.get("price") if isinstance(market.get("price"), dict) else {}
            market_price_wei = ""
            if str(market_price.get("unit") or "") == "compute_credit":
                try:
                    market_price_wei = str(credit_decimal_text_to_wei(
                        market_price.get("amount"), default="0", round_up=False,
                    ))
                except (TypeError, ValueError):
                    pass
            market_proof = {
                "hub_acknowledged": self.accepted.get("type") == "hub.auth.accepted"
                    and self.accepted.get("ok") is True,
                "price_credit_wei": market_price_wei,
                "ring_three": "ring-3" in (market.get("rings") if isinstance(market.get("rings"), list) else []),
                "chat_completions": "chat.completions" in (
                    market.get("capabilities") if isinstance(market.get("capabilities"), list) else []
                ),
                "capacity_available": (
                    isinstance(market.get("max_concurrency"), int)
                    and isinstance(market.get("active_sessions"), int)
                    and market["active_sessions"] < market["max_concurrency"]
                ),
            }
            return {
                "ok": not bool(self.last_error),
                "market_proof": market_proof,
                "transport": "websocket",
                "endpoint": self.endpoint_path,
                "hub_url": self.hub_url,
                "worker_session": {
                    "connected": bool(self.accepted),
                    "privacy": "worker wallet and hub connection are internal",
                },
                "opened_at": self.opened_at,
                "closed_at": self.closed_at,
                "close_reason": self.close_reason,
                "last_error": self.last_error,
                "accepted": _worker_runtime_public_redacted(dict(self.accepted)),
                "last_pong": _worker_runtime_public_redacted(dict(self.last_pong)),
                "last_offer": _worker_runtime_public_redacted(dict(self.last_offer)),
                "last_result": _worker_runtime_public_redacted(dict(self.last_result)),
                "active_work_count": self.active_work_count,
                "has_active_work": self.active_work_count > 0,
                "alive": self.is_alive,
            }

    def _send_keepalive(self) -> None:
        self._send_json(
            {
                "type": "worker.pong",
                "keepalive": True,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    def _keepalive_loop(self) -> None:
        """Keep the live-session WebSocket active while local AI is pre-filling.

        The Hub sends an initial ``hub.ping`` during authentication, but the
        request thread then blocks reading worker messages.  Real local models can
        spend longer than common 30 second idle limits before producing a first
        token or terminal result, so the worker must keep the socket visible to
        the Hub while an offer is running.
        """

        while not self._stop_event.wait(max(2.0, float(self.keepalive_interval_s or 10.0))):
            if not self.is_alive:
                return
            try:
                self._send_keepalive()
            except Exception as exc:
                self._diagnostic_transport_event("keepalive_send_failed", exc=exc)
                with self._state_lock:
                    if not self._stop_event.is_set():
                        self.last_error = f"Worker live-session keepalive failed: {exc}"
                        self.close_reason = "keepalive_failed"
                    if not self.closed_at:
                        self.closed_at = datetime.now(timezone.utc).isoformat()
                self._stop_event.set()
                sock = self._socket
                self._socket = None
                if sock is not None:
                    self._diagnostic_transport_event("keepalive_socket_close_invoked")
                    try:
                        with self._tls_io_guard():
                            sock.close()
                    except OSError as close_exc:
                        self._diagnostic_transport_event("keepalive_socket_close_failed", exc=close_exc)
                return

    def _reader_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    message = self._recv_json()
                except TimeoutError:
                    continue
                self._handle_hub_message(message)
        except Exception as exc:
            self._diagnostic_transport_event("reader_exception", exc=exc)
            with self._state_lock:
                if not self._stop_event.is_set():
                    self.last_error = str(exc)
                    self.close_reason = "reader_error"
                if not self.closed_at:
                    self.closed_at = datetime.now(timezone.utc).isoformat()
            self._stop_event.set()
            sock = self._socket
            self._socket = None
            if sock is not None:
                self._diagnostic_transport_event("reader_socket_close_invoked")
                try:
                    with self._tls_io_guard():
                        sock.close()
                except OSError as close_exc:
                    self._diagnostic_transport_event("reader_socket_close_failed", exc=close_exc)

    def _handle_hub_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        if message_type == "hub.auth.accepted":
            with self._state_lock:
                self.accepted = dict(message)
                self.connection_id = ""
                self.owner = {}
                self.market = {}
            return
        if message_type == "hub.ping":
            ping_id = str(message.get("ping_id") or "")
            self._send_json(
                {
                    "type": "worker.pong",
                    "ping_id": ping_id,
                }
            )
            return
        if message_type == "hub.pong.accepted":
            with self._state_lock:
                self.last_pong = dict(message)
            return
        if message_type == "hub.work.offer":
            self._handle_work_offer(message)
            return
        if message_type == "hub.work.authorized":
            self._handle_work_authorized(message)
            return
        if message_type == "hub.work.denied":
            with self._state_lock:
                self._pending_authorizations.pop(str(message.get("session_id") or ""), None)
            return
        if message_type in {"hub.work.result.accepted", "hub.work.failed.accepted", "hub.work.terminal.accepted"}:
            with self._state_lock:
                self.last_result = dict(message)
            return
        if message_type == "hub.error":
            with self._state_lock:
                self.last_error = str(message.get("error") or message)
            return

    def _send_work_delta_from_activity(self, offer: dict[str, Any], event: dict[str, Any]) -> None:
        session_id = str(offer.get("session_id") or "")
        request_id = str(offer.get("request_id") or "")
        run_id = str(offer.get("run_id") or "")
        if not session_id or not request_id:
            return
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        latest_text = str(data.get("latest_text") or event.get("message") or "")
        delta = str(data.get("delta") or "")
        with self._state_lock:
            seq = self._delta_seq_by_session.get(session_id, 0) + 1
            self._delta_seq_by_session[session_id] = seq
            previous = self._delta_content_by_session.get(session_id, "")
            if not delta and latest_text:
                if latest_text.startswith(previous):
                    delta = latest_text[len(previous) :]
                elif latest_text != previous:
                    delta = latest_text
            if latest_text:
                self._delta_content_by_session[session_id] = latest_text
        if not delta and not latest_text:
            return
        message: dict[str, Any] = {
            "type": "worker.work.delta",
            "session_id": session_id,
            "request_id": request_id,
            "seq": seq,
            "delta": delta,
            "content_so_far": latest_text,
            "done": False,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if run_id:
            message["run_id"] = run_id
        if data.get("content_chars") is not None:
            message["content_chars"] = data.get("content_chars")
        try:
            self._send_json(message)
        except Exception as exc:
            with self._state_lock:
                self.last_error = f"Worker live-session failed to forward stream delta: {exc}"

    def _handle_work_offer(self, offer: dict[str, Any]) -> None:
        session_id = str(offer.get("session_id") or "")
        request_id = str(offer.get("request_id") or "")
        if not session_id or not request_id:
            raise RuntimeError(f"Hub work offer missing session_id/request_id: {offer}")
        accepted = {
            "type": "worker.work.accepted",
            "session_id": session_id,
            "request_id": request_id,
        }
        run_id = str(offer.get("run_id") or "")
        if run_id:
            accepted["run_id"] = run_id
        requires_authorization = offer.get("execution_authorization_required") is True
        if requires_authorization:
            from main_computer.model_execution_contract import market_supports
            if not market_supports(self.auth_message.get('market'), offer.get('work')):
                raise ValueError('Hub prepared an unsupported model or operation')
        with self._state_lock:
            if requires_authorization:
                if session_id in self._authorized_sessions or session_id in self._pending_authorizations:
                    raise RuntimeError("Duplicate work preparation session")
                self._pending_authorizations[session_id] = dict(offer)
            self.last_offer = dict(offer)
        try:
            self._send_json(accepted)
        except BaseException:
            with self._state_lock:
                self._pending_authorizations.pop(session_id, None)
            raise
        if requires_authorization:
            return  # CRITICAL: no computation, streaming or billable side effects.
        self._start_authorized_work(offer)

    def _handle_work_authorized(self, message: dict[str, Any]) -> None:
        session_id = str(message.get("session_id") or "")
        with self._state_lock:
            offer = self._pending_authorizations.get(session_id)
            if offer is None or self._stop_event.is_set():
                self.last_error = "Unexpected or duplicate Hub work authorization"
                return
            if (str(offer.get("request_id") or "") != str(message.get("request_id") or "")
                    or str(offer.get("run_id") or "") != str(message.get("run_id") or "")
                    or message.get("execution_protocol") != "durable-authorization-v1"
                    or not isinstance(message.get("work"), dict)):
                self.last_error = "Hub work authorization identity or payload mismatch"
                return
            from main_computer.model_execution_contract import request_contract
            prepared = offer.get('work')
            declared = request_contract(prepared)
            actual = request_contract(message['work'])
            if declared != actual:
                self.last_error = 'Hub authorized a different model or operation than offered'
                raise ValueError(self.last_error)
            self._pending_authorizations.pop(session_id, None)
            self._authorized_sessions.add(session_id)
            authorized_offer = dict(offer)
            authorized_offer["work"] = dict(message["work"])
            self.last_offer = dict(authorized_offer)
        self._start_authorized_work(authorized_offer)

    def _start_authorized_work(self, offer: dict[str, Any]) -> None:
        session_id = str(offer.get("session_id") or "")
        request_id = str(offer.get("request_id") or "")
        with self._state_lock:
            self.active_work_count += 1
        worker_thread = threading.Thread(
            target=self._complete_work_offer,
            args=(dict(offer),),
            name=f"worker-live-session-offer-{session_id[:12] or request_id[:12]}",
            daemon=True,
        )
        worker_thread.start()

    def _complete_work_offer(self, offer: dict[str, Any]) -> None:
        session_id = str(offer.get("session_id") or "")
        request_id = str(offer.get("request_id") or "")
        run_id = str(offer.get("run_id") or "")
        result_message: dict[str, Any] = {}
        try:
            execution_offer = dict(offer)
            execution_offer["_worker_live_session_delta_callback"] = (
                lambda event: self._send_work_delta_from_activity(offer, event)
            )
            from main_computer.model_execution_contract import (market_supports,
                verify_execution_result, ModelContractError)
            if not market_supports(self.auth_message.get('market'), execution_offer.get('work')):
                raise ModelContractError('Worker is not configured for requested model/operation')
            result = self._execute_work_offer(execution_offer)
            verify_execution_result(execution_offer.get('work'), result)
            result_message = {
                "type": "worker.work.result",
                "session_id": session_id,
                "request_id": request_id,
                "result": result,
            }
            if run_id:
                result_message["run_id"] = run_id
            self._send_json(result_message)
        except BaseException as exc:
            error_payload = self._worker_failure_payload_for_exception(exc, offer)
            result_message = {
                "type": "worker.work.failed",
                "session_id": session_id,
                "request_id": request_id,
                "error": error_payload,
            }
            if run_id:
                result_message["run_id"] = run_id
            try:
                self._send_json(result_message)
            except Exception as send_exc:
                with self._state_lock:
                    self.last_error = (
                        f"Worker live-session local executor failed with {exc.__class__.__name__}: {exc}; "
                        f"also failed to report terminal worker.work.failed: {send_exc}"
                    )
        finally:
            with self._state_lock:
                if result_message:
                    self.last_result = dict(result_message)
                self._delta_seq_by_session.pop(session_id, None)
                self._delta_content_by_session.pop(session_id, None)
                self.active_work_count = max(0, self.active_work_count - 1)

    def _worker_failure_payload_for_exception(self, exc: BaseException, offer: dict[str, Any]) -> dict[str, Any]:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        session_id = str(offer.get("session_id") or "")
        request_id = str(offer.get("request_id") or "")
        run_id = str(offer.get("run_id") or "")
        return {
            "error": str(exc) or exc.__class__.__name__,
            "error_type": exc.__class__.__name__,
            "status": "failed",
            "transport": "websocket-live-session",
            "session_id": session_id,
            "request_id": request_id,
            "run_id": run_id,
            "local_ai_timeout_seconds": self._work_offer_timeout_seconds(offer),
            "model": str(work.get("model") or ""),
            "capabilities": [str(item) for item in work.get("capabilities", [])] if isinstance(work.get("capabilities"), list) else [],
        }

    def _work_offer_timeout_seconds(self, offer: dict[str, Any]) -> float:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        input_payload = dict(work.get("input") or {}) if isinstance(work.get("input"), dict) else {}
        work_metadata = dict(work.get("metadata") or {}) if isinstance(work.get("metadata"), dict) else {}
        offer_metadata = dict(offer.get("metadata") or {}) if isinstance(offer.get("metadata"), dict) else {}
        execution_limits = (
            dict(work.get("execution_limits") or {})
            if isinstance(work.get("execution_limits"), dict)
            else dict(offer.get("execution_limits") or {})
            if isinstance(offer.get("execution_limits"), dict)
            else {}
        )
        candidates = [
            work.get("timeout_seconds"),
            work.get("worker_timeout_seconds"),
            work.get("work_timeout_seconds"),
            work.get("max_runtime_seconds"),
            work.get("local_ai_timeout_seconds"),
            work.get("worker_local_ai_timeout_seconds"),
            input_payload.get("timeout_seconds"),
            input_payload.get("worker_timeout_seconds"),
            input_payload.get("work_timeout_seconds"),
            input_payload.get("max_runtime_seconds"),
            input_payload.get("local_ai_timeout_seconds"),
            input_payload.get("worker_local_ai_timeout_seconds"),
            work_metadata.get("timeout_seconds"),
            work_metadata.get("worker_timeout_seconds"),
            work_metadata.get("work_timeout_seconds"),
            work_metadata.get("max_runtime_seconds"),
            work_metadata.get("local_ai_timeout_seconds"),
            work_metadata.get("worker_local_ai_timeout_seconds"),
            execution_limits.get("timeout_seconds"),
            execution_limits.get("worker_timeout_seconds"),
            execution_limits.get("work_timeout_seconds"),
            execution_limits.get("max_runtime_seconds"),
            execution_limits.get("local_ai_timeout_seconds"),
            execution_limits.get("worker_local_ai_timeout_seconds"),
            offer.get("timeout_seconds"),
            offer.get("worker_timeout_seconds"),
            offer.get("work_timeout_seconds"),
            offer.get("max_runtime_seconds"),
            offer.get("local_ai_timeout_seconds"),
            offer.get("worker_local_ai_timeout_seconds"),
            offer_metadata.get("timeout_seconds"),
            offer_metadata.get("worker_timeout_seconds"),
            offer_metadata.get("work_timeout_seconds"),
            offer_metadata.get("max_runtime_seconds"),
            offer_metadata.get("local_ai_timeout_seconds"),
            offer_metadata.get("worker_local_ai_timeout_seconds"),
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
        # Local LLMs can cold-start or prefill slowly.  The worker result
        # handoff is already terminal-safe, so keep this as a generous outer
        # executor deadline rather than a short request/accept timer.
        return DEFAULT_WORKER_LIVE_SESSION_LOCAL_AI_TIMEOUT_SECONDS

    def _cancel_work_executor_after_timeout(self, executor: Any, offer: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        """Best-effort cancellation for worker local-AI work after the outer offer timeout.

        The live-session client owns the Hub terminal failure timeout, but the actual
        local AI slot is owned by the route/server executor.  Without this hook the
        Hub can see a failed job while the local subprocess remains active and keeps
        the shared local-AI capacity slot busy.
        """

        canceller = self.work_canceller
        if not callable(canceller):
            owner = getattr(executor, "__self__", None)
            canceller = getattr(owner, "_cancel_worker_live_session_offer", None)
        if not callable(canceller):
            return {"ok": False, "cancelled": False, "reason": "no-work-canceller"}
        result = canceller(
            offer,
            reason="worker-live-session-timeout",
            timeout_s=timeout_s,
        )
        return result if isinstance(result, dict) else {"ok": True, "cancelled": True, "result": result}

    def _call_work_executor_with_timeout(self, executor: Any, offer: dict[str, Any]) -> Any:
        timeout_s = self._work_offer_timeout_seconds(offer)
        done = threading.Event()
        box: dict[str, Any] = {}

        def runner() -> None:
            try:
                box["result"] = executor(offer)
            except BaseException as exc:  # propagate through the parent offer thread
                box["exception"] = exc
            finally:
                done.set()

        thread = threading.Thread(
            target=runner,
            name=f"worker-live-session-local-ai-{str(offer.get('session_id') or offer.get('request_id') or '')[:12]}",
            daemon=True,
        )
        thread.start()
        if not done.wait(timeout_s):
            try:
                box["timeout_cancel"] = self._cancel_work_executor_after_timeout(executor, offer, timeout_s=timeout_s)
            except BaseException as cancel_exc:
                box["timeout_cancel_error"] = f"{cancel_exc.__class__.__name__}: {cancel_exc}"
            done.wait(0.25)
            raise TimeoutError(f"Worker live-session local AI executor timed out after {timeout_s:.1f}s.")
        if "exception" in box:
            raise box["exception"]
        return box.get("result")

    def _execute_work_offer(self, offer: dict[str, Any]) -> dict[str, Any]:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        input_payload = dict(work.get("input") or {}) if isinstance(work.get("input"), dict) else {}
        if str(input_payload.get("kind") or "").lower() == "echo":
            raise ValueError("Echo is a diagnostic bypass, not authenticated model execution")
        executor = self.work_executor
        if not callable(executor):
            raise RuntimeError("Worker live-session has no local work executor configured.")
        result = self._call_work_executor_with_timeout(executor, offer)
        if not isinstance(result, dict):
            result = {"response": {"content": str(result or "")}}
        result.setdefault("status", "success")
        response = result.get("response")
        if not isinstance(response, dict):
            result["response"] = {"content": str(response or "")}
        result.setdefault("transport", "websocket-live-session")
        return result

    def _debug_echo_result_payload_for_offer(self, offer: dict[str, Any]) -> dict[str, Any]:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        input_payload = dict(work.get("input") or {}) if isinstance(work.get("input"), dict) else {}
        content = str(input_payload.get("value") or input_payload.get("prompt") or "")
        return {
            "status": "success",
            "response": {
                "role": "assistant",
                "content": content,
            },
            "transport": "websocket-live-session",
            "debug_echo": True,
        }

    def _open_socket(self) -> socket.socket:
        parsed = urlparse(self.hub_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise RuntimeError(f"Hub URL must be http(s): {self.hub_url!r}")
        if isinstance(self.auth_message.get("multisession_authorization"), dict):
            # Final fail-closed guard before any MSK-bearing network session.
            from main_computer.slh.marketplace_auto import _check_url
            _check_url(self.hub_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        raw_sock = socket.create_connection((parsed.hostname, port), timeout=self.timeout_s)
        if parsed.scheme == "https":
            managed_pin = getattr(self, "managed_slh_fingerprint", None)
            context = ssl.create_default_context() if not managed_pin else ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            if managed_pin:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                context.minimum_version = ssl.TLSVersion.TLSv1_2
            sock: socket.socket = context.wrap_socket(
                raw_sock, server_hostname=parsed.hostname,
                # Probe-only strict EOF reporting: distinguish TLS close_notify
                # from a ragged TCP EOF. Production defaults are unchanged.
                suppress_ragged_eofs=not bool(getattr(self, "diagnostic_strict_tls_eof", False)))
            if managed_pin and hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest() != managed_pin:
                sock.close()
                raise RuntimeError("SLH_TLS_PIN_MISMATCH")
        else:
            sock = raw_sock
        sock.settimeout(self.timeout_s)
        request_path = self._websocket_request_path(parsed)
        host = parsed.hostname
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {request_path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: MainComputerWorker/0.1\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        # Read exactly the HTTP headers.  recv(4096) can consume an early
        # websocket frame along with the upgrade and silently discard it.
        response = bytearray()
        while not response.endswith(b"\r\n\r\n"):
            chunk = sock.recv(1)
            if not chunk:
                raise RuntimeError(f"Hub live-session websocket handshake closed early: {bytes(response)!r}")
            response.extend(chunk)
            if len(response) > 65536:
                raise RuntimeError("Hub live-session websocket handshake response was too large.")
        first_line = bytes(response).split(b"\r\n", 1)[0]
        self.websocket_upgrade_status = first_line.decode("latin-1", errors="replace")
        if not (first_line.startswith(b"HTTP/1.1 101 ") or first_line == b"HTTP/1.1 101"):
            detail = response.decode("utf-8", errors="replace")
            raise RuntimeError(
                "Hub live-session websocket handshake was not HTTP/1.1 101: "
                + detail[:1000]
            )
        header_lines = bytes(response).split(b"\r\n")[1:]
        headers: dict[str, str] = {}
        for line in header_lines:
            if b":" not in line:
                continue
            name, value = line.split(b":", 1)
            headers[name.strip().decode("ascii", errors="ignore").lower()] = value.strip().decode("latin-1")
        expected_accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        if (
            headers.get("upgrade", "").lower() != "websocket"
            or "upgrade" not in {token.strip().lower() for token in headers.get("connection", "").split(",")}
            or headers.get("sec-websocket-accept") != expected_accept
        ):
            raise RuntimeError(
                "Hub live-session websocket upgrade response was missing valid Upgrade, "
                "Connection, or Sec-WebSocket-Accept headers."
            )
        # Keep individual receives short enough that a writer waiting for the
        # shared TLS lock can submit a paid result promptly. The baseline
        # mode is only retained for controlled diagnosis/regression.
        sock.settimeout(0.1 if self.tls_io_mode == "serialized" else 1.0)
        return sock

    def _websocket_request_path(self, parsed: Any) -> str:
        prefix = str(parsed.path or "").rstrip("/")
        if not prefix:
            prefix = ""
        return prefix + self.endpoint_path

    def _send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        # Fixed labels only: no frame bodies, request IDs, tokens, or answers.
        safe_type = payload.get('type')
        if safe_type in {'worker.work.accepted', 'worker.work.result', 'worker.work.failed'}:
            self._diagnostic_transport_event({
                'worker.work.accepted': 'worker_paid_offer_ack_sending',
                'worker.work.result': 'worker_paid_result_sending',
                'worker.work.failed': 'worker_paid_failure_sending',
            }[safe_type])
        self._send_frame(opcode=0x1, payload=data)
        if safe_type in {'worker.work.accepted', 'worker.work.result', 'worker.work.failed'}:
            self._diagnostic_transport_event({
                'worker.work.accepted': 'worker_paid_offer_ack_sent',
                'worker.work.result': 'worker_paid_result_sent',
                'worker.work.failed': 'worker_paid_failure_sent',
            }[safe_type])

    def _send_close_frame(self) -> None:
        self._send_frame(opcode=0x8, payload=b"")

    def _tls_io_guard(self):
        """Serialize SSL socket calls to protect the paid Worker live session.

        Lock one recv/sendall/close operation at a time, not entire frames.
        The baseline mode is an explicit diagnostic-only A/B control.
        """
        lock = getattr(self, "_tls_io_lock", None)
        return lock if lock is not None and getattr(self, "tls_io_mode", "serialized") == "serialized" else nullcontext()

    def _send_frame(self, *, opcode: int, payload: bytes) -> None:
        sock = self._socket
        if sock is None:
            raise RuntimeError("worker live-session socket is not open.")
        header = bytearray([0x80 | (opcode & 0x0F)])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(length.to_bytes(2, "big"))
        else:
            header.append(0x80 | 127)
            header.extend(length.to_bytes(8, "big"))
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        with self._send_lock:
            try:
                with self._tls_io_guard():
                    sock.sendall(bytes(header) + mask + masked)
            except (OSError, ssl.SSLError) as exc:
                self._diagnostic_transport_event("websocket_send_failed", exc=exc)
                raise

    def _recv_json(self) -> dict[str, Any]:
        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x8:
                # WebSocket close code is safe diagnostic metadata; the close
                # reason string may contain untrusted/private data, so omit it.
                close_code = int.from_bytes(payload[:2], "big") if len(payload) >= 2 else None
                self._diagnostic_transport_event("websocket_close_frame_received", close_code=close_code)
                raise RuntimeError(f"Hub closed the worker live-session websocket (close_code={close_code}).")
            if opcode == 0x9:
                self._send_frame(opcode=0xA, payload=payload)
                continue
            if opcode != 0x1:
                continue
            try:
                decoded = json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Hub live-session returned non-JSON text: {exc}") from exc
            if not isinstance(decoded, dict):
                raise RuntimeError("Hub live-session returned non-object JSON.")
            return decoded

    # A timeout is normal when waiting for the first frame byte. Once any
    # bytes of a WebSocket frame arrive, however, restarting frame parsing on
    # the next byte corrupts the byte stream. Keep the in-progress frame
    # together for a bounded interval, including across its separate header,
    # extended-length, mask, and payload reads.
    _fragment_read_timeout_s = 15.0

    def _recv_frame(self) -> tuple[int, bytes]:
        sock = self._socket
        if sock is None:
            raise RuntimeError("worker live-session socket is not open.")
        header = self._read_exact(2)
        # The frame is now in progress. Subsequent reads must not abandon
        # it simply because the 1-second socket timeout fired without data.
        frame_deadline = time.monotonic() + self._fragment_read_timeout_s
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        masked = bool(header[1] & 0x80)
        if length == 126:
            length = int.from_bytes(self._read_exact(2, deadline=frame_deadline), "big")
        elif length == 127:
            length = int.from_bytes(self._read_exact(8, deadline=frame_deadline), "big")
        mask = self._read_exact(4, deadline=frame_deadline) if masked else b""
        payload = self._read_exact(length, deadline=frame_deadline) if length else b""
        if masked and mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _read_exact(self, length: int, *, deadline: float | None = None) -> bytes:
        sock = self._socket
        if sock is None:
            raise RuntimeError("worker live-session socket is not open.")
        chunks: list[bytes] = []
        remaining = int(length)
        while remaining:
            try:
                with self._tls_io_guard():
                    chunk = sock.recv(remaining)
            except socket.timeout as exc:
                if deadline is None and not chunks:
                    # Idle: no bytes of the next frame have arrived.
                    raise TimeoutError("Timed out waiting for Hub live-session websocket data.") from exc
                if deadline is not None and time.monotonic() >= deadline:
                    self._diagnostic_transport_event("websocket_frame_timeout", exc=exc)
                    raise RuntimeError("Hub live-session websocket frame incomplete after bounded wait.") from exc
                continue
            if not chunk:
                self._diagnostic_transport_event("socket_recv_eof")
                raise RuntimeError("Hub live-session websocket closed unexpectedly.")
            chunks.append(chunk)
            remaining -= len(chunk)
            if deadline is None and remaining:
                # Some bytes of the frame header are already consumed.
                deadline = time.monotonic() + self._fragment_read_timeout_s
        return b"".join(chunks)
