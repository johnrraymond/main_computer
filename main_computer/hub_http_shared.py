"""Shared bounded HTTP/JSON request-handler mechanics for Hub implementations.

Contains only the pre-existing, unchanged legacy JSON handler implementation.
Importing this module does not initialize a Hub, contact FoundationDB or run
credit accounting.  The legacy HTTP handler and the Dev-Test handler inherit
these exact request limits, error handling and stream semantics.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any

from main_computer.hub_resource_limits import (
    DEFAULT_RESOURCE_POLICY, HubHTTPResourceError,
    validate_http_headers, http_content_length, read_bounded_json,
)

DEFAULT_HUB_STATUS_SLOW_LOG_SECONDS = 2.0

class _JsonHandler(BaseHTTPRequestHandler):
    server_version = "MainComputerHub/0.2"
    protocol_version = "HTTP/1.1"

    def _resource_policy(self):
        return getattr(self.server, "resource_limits", DEFAULT_RESOURCE_POLICY)

    def handle_one_request(self) -> None:
        if not self.server.resource_begin_headers(self.connection):
            self.close_connection = True
            return
        return super().handle_one_request()

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        try:
            validate_http_headers(self.headers, self._resource_policy())
        except HubHTTPResourceError as exc:
            self.close_connection = True
            self.send_error(exc.status, exc.code)
            return False
        if not self.server.resource_headers_complete(self.connection):
            self.close_connection = True
            return False
        return True

    def handle_expect_100(self) -> bool:
        try:
            validate_http_headers(self.headers, self._resource_policy())
            http_content_length(self.headers, self._resource_policy())
        except HubHTTPResourceError as exc:
            self.close_connection = True
            self.send_error(exc.status, exc.code)
            return False
        return super().handle_expect_100()

    def _preflight_post(self) -> bool:
        try:
            self._resource_json_payload = read_bounded_json(self, self._resource_policy())
        except HubHTTPResourceError as exc:
            self.close_connection = True
            self._send_json({"ok": False, "error": exc.code}, status=HTTPStatus(exc.status))
            return False
        return True

    def log_message(self, format: str, *args: Any) -> None:
        if getattr(self.server, "verbose", False):
            super().log_message(format, *args)

    def _worker_route_diagnostics_enabled(self) -> bool:
        raw = str(os.environ.get("HUB_WORKER_ROUTE_DIAGNOSTICS", "")).strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False
        if raw in {"1", "true", "yes", "on"}:
            return True
        return bool(getattr(self.server, "worker_route_diagnostics", False))

    def _next_worker_route_diag_id(self) -> int:
        lock = getattr(self.server, "worker_route_diag_lock", None)
        if lock is None:
            self.server.worker_route_diag_lock = threading.Lock()
            self.server.worker_route_diag_sequence = 0
            lock = self.server.worker_route_diag_lock
        with lock:
            value = int(getattr(self.server, "worker_route_diag_sequence", 0) or 0) + 1
            self.server.worker_route_diag_sequence = value
            return value

    def _worker_route_diag_start(self, route: str, path: str) -> tuple[int, float]:
        diag_id = self._next_worker_route_diag_id()
        started_at = time.perf_counter()
        self._worker_route_diag_step(diag_id, route, "start", started_at, path=path)
        return diag_id, started_at

    def _worker_route_diag_step(self, diag_id: int, route: str, stage: str, started_at: float, **fields: Any) -> None:
        if not self._worker_route_diagnostics_enabled():
            return
        port = getattr(self.server, "server_port", "")
        payload: dict[str, Any] = {
            "event": "hub.worker_route.diagnostic",
            "route": route,
            "stage": stage,
            "diag_id": int(diag_id),
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000.0, 3),
            "port": port,
            "client": self.client_address[0] if self.client_address else "",
            "thread": threading.current_thread().name,
        }
        for key, value in fields.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                payload[key] = value
            elif isinstance(value, (list, tuple, set)):
                payload[key] = [str(item) for item in list(value)[:20]]
            elif isinstance(value, dict):
                payload[key] = {str(k): str(v) for k, v in list(value.items())[:20]}
            else:
                payload[key] = str(value)
        try:
            print(f"[hub-worker-route:{port}] {json.dumps(payload, sort_keys=True)}", file=sys.stderr, flush=True)
        except Exception:
            pass

    def _worker_route_enter_or_reject(self, diag_id: int, route: str, started_at: float) -> bool:
        semaphore = getattr(self.server, "worker_route_semaphore", None)
        max_in_flight = int(getattr(self.server, "worker_route_max_in_flight", 0) or 0)
        if semaphore is None or max_in_flight <= 0:
            self._worker_route_diag_step(diag_id, route, "route_gate.disabled", started_at)
            return True

        if semaphore.acquire(blocking=False):
            in_flight = 0
            lock = getattr(self.server, "worker_route_in_flight_lock", None)
            if lock is not None:
                with lock:
                    self.server.worker_route_in_flight = int(getattr(self.server, "worker_route_in_flight", 0) or 0) + 1
                    in_flight = int(self.server.worker_route_in_flight)
            self._worker_route_diag_step(
                diag_id,
                route,
                "route_gate.acquired",
                started_at,
                worker_route_in_flight=in_flight,
                worker_route_max_in_flight=max_in_flight,
            )
            return True

        retry_after = float(getattr(self.server, "worker_route_retry_after_seconds", 1.0) or 1.0)
        self._worker_route_diag_step(
            diag_id,
            route,
            "route_gate.rejected",
            started_at,
            status=503,
            worker_route_max_in_flight=max_in_flight,
            retry_after_seconds=retry_after,
        )
        self._worker_route_diag_step(diag_id, route, "request_body.discard.start", started_at)
        self._discard_request_body()
        self._worker_route_diag_step(
            diag_id,
            route,
            "request_body.discard.done",
            started_at,
            close_connection=bool(getattr(self, "close_connection", False)),
        )
        self._worker_route_diag_step(diag_id, route, "route_gate.reject_sent", started_at, status=503)
        self._send_json(
            {
                "ok": False,
                "error": "Hub worker route overloaded; retry later.",
                "error_type": "hub_worker_route_overloaded",
                "retry_after_seconds": retry_after,
                "worker_route_max_in_flight": max_in_flight,
            },
            status=HTTPStatus.SERVICE_UNAVAILABLE,
        )
        return False

    def _worker_route_exit(self, diag_id: int, route: str, started_at: float) -> None:
        semaphore = getattr(self.server, "worker_route_semaphore", None)
        if semaphore is None:
            return
        in_flight = 0
        lock = getattr(self.server, "worker_route_in_flight_lock", None)
        if lock is not None:
            with lock:
                self.server.worker_route_in_flight = max(0, int(getattr(self.server, "worker_route_in_flight", 0) or 0) - 1)
                in_flight = int(self.server.worker_route_in_flight)
        try:
            semaphore.release()
        except ValueError:
            pass
        self._worker_route_diag_step(diag_id, route, "route_gate.released", started_at, worker_route_in_flight=in_flight)

    def _worker_route_success_payload(self, worker: Any) -> dict[str, Any]:
        worker_data = worker.as_dict()
        capabilities = worker_data.get("capabilities", {}) if isinstance(worker_data.get("capabilities"), dict) else {}
        payload = {
            "ok": True,
            "worker": worker_data,
            "hub": {"status_omitted": True},
            "hub_status_omitted": True,
        }
        for key in (
            "requested_ring",
            "effective_ring",
            "minimum_allowed_ring",
            "allowed_min_ring",
            "ring_admission_status",
            "ring_admission_message",
            "fallback_ring",
        ):
            if key in capabilities:
                payload[key] = capabilities.get(key)
        return payload

    def _discard_request_body(self) -> None:
        # An overloaded request must not consume an unbounded or stalled body.
        # Close the socket rather than attempting to drain attacker bytes.
        self.close_connection = True

    def _read_json(self) -> dict[str, Any]:
        cached = getattr(self, "_resource_json_payload", None)
        if cached is not None:
            return dict(cached)
        return read_bounded_json(self, self._resource_policy())

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> bool:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Health/status observers and reverse proxies are allowed to abandon a
            # response after their own timeout. Treat that as a transport outcome,
            # not as an unhandled Hub request failure that dumps a traceback.
            self.close_connection = True
            return False

    def _send_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        cache_control: str = "no-store",
    ) -> bool:
        try:
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            self.wfile.write(payload)
            return True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
            return False

    def _hub_status_slow_log_seconds(self) -> float:
        raw = str(os.environ.get("HUB_STATUS_SLOW_LOG_SECONDS", DEFAULT_HUB_STATUS_SLOW_LOG_SECONDS)).strip()
        try:
            return max(0.0, float(raw))
        except ValueError:
            return DEFAULT_HUB_STATUS_SLOW_LOG_SECONDS

    def _log_hub_status_timing(
        self,
        *,
        path: str,
        started_at: float,
        exact_payouts: bool,
        response_written: bool,
    ) -> None:
        elapsed_ms = (time.perf_counter() - started_at) * 1000.0
        threshold_ms = self._hub_status_slow_log_seconds() * 1000.0
        slow = elapsed_ms >= threshold_ms
        disconnected = not response_written
        if not slow and not disconnected:
            return
        payload = {
            "event": "hub.status.request",
            "path": path,
            "elapsed_ms": round(elapsed_ms, 3),
            "slow_threshold_ms": round(threshold_ms, 3),
            "slow": slow,
            "client_disconnected": disconnected,
            "exact_payouts": bool(exact_payouts),
            "client": self.client_address[0] if self.client_address else "",
        }
        port = getattr(self.server, "server_port", "")
        try:
            print(f"[hub-status:{port}] {json.dumps(payload, sort_keys=True)}", file=sys.stderr, flush=True)
        except Exception:
            pass

    def _send_jsonl_payload(self, payload: dict[str, Any]) -> None:
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        self.wfile.flush()

    def _request_stream_payload(
        self,
        *,
        request_id: str,
        event_index: int,
        event: dict[str, Any],
        record: Any,
    ) -> dict[str, Any]:
        event_type = str(event.get("type") or event.get("event") or "message").strip() or "message"
        stream_event = str(event.get("stream_event") or event.get("stream_event_type") or "").strip()
        event_name = stream_event if event_type == "worker.stream.event" and stream_event else event_type
        payload: dict[str, Any] = {
            "event": event_name,
            "request_event_type": event_type,
            "event_index": int(event_index),
            "request_id": request_id,
            "state": str(event.get("state") or getattr(record, "state", "") or ""),
            "created_at": str(event.get("created_at", "") or ""),
            "payload": dict(event),
        }
        for key in (
            "stream_event",
            "text",
            "delta",
            "content_delta",
            "thinking_delta",
            "message",
            "progress",
            "token_count",
            "worker_node_id",
            "worker_instance_id",
            "lease_id",
        ):
            if key in event:
                payload[key] = event[key]
        return payload

    def _stream_request_events(self, request_id: str, query: dict[str, list[str]]) -> None:
        try:
            next_index = max(0, int(str(query.get("after", ["0"])[0] or "0")))
        except (TypeError, ValueError):
            next_index = 0
        try:
            max_seconds = max(0.1, min(600.0, float(str(query.get("timeout_seconds", ["30"])[0] or "30"))))
        except (TypeError, ValueError):
            max_seconds = 30.0
        try:
            heartbeat_seconds = max(0.25, min(30.0, float(str(query.get("heartbeat_seconds", ["5"])[0] or "5"))))
        except (TypeError, ValueError):
            heartbeat_seconds = 5.0

        self.send_response(int(HTTPStatus.OK))
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        started = time.monotonic()
        terminal_states = {"completed", "failed", "cancelled", "expired"}
        try:
            self._send_jsonl_payload(
                {
                    "event": "open",
                    "request_id": request_id,
                    "transport": "hub-request-jsonl",
                    "after": next_index,
                }
            )
            while True:
                remaining = max_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    self._send_jsonl_payload(
                        {
                            "event": "timeout",
                            "request_id": request_id,
                            "next_event_index": next_index,
                        }
                    )
                    return
                events, record = self.server.dispatcher.wait_for_request_events(
                    request_id,
                    after=next_index,
                    timeout_s=min(heartbeat_seconds, remaining),
                )
                for event in events:
                    self._send_jsonl_payload(
                        self._request_stream_payload(
                            request_id=request_id,
                            event_index=next_index,
                            event=event,
                            record=record,
                        )
                    )
                    next_index += 1
                if str(getattr(record, "state", "")) in terminal_states:
                    self._send_jsonl_payload(
                        {
                            "event": "done",
                            "request_id": request_id,
                            "state": str(getattr(record, "state", "")),
                            "next_event_index": next_index,
                        }
                    )
                    return
                if not events:
                    self._send_jsonl_payload(
                        {
                            "event": "heartbeat",
                            "request_id": request_id,
                            "next_event_index": next_index,
                            "state": str(getattr(record, "state", "")),
                        }
                    )
        except (BrokenPipeError, ConnectionResetError, OSError):
            return



# Public name for newly written code; _JsonHandler remains a compatibility alias.
HubJSONRequestHandler = _JsonHandler
