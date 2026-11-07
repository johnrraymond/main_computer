"""Shared resource ceilings and bounded socket admission for all HTTP Hubs.

All tunables can only tighten hard ceilings. The admission gate runs before
ThreadingHTTPServer can create an unbounded connection-handling thread.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
import queue
import socket
import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any, Mapping


@dataclass(frozen=True)
class HubResourcePolicy:
    version: str = "hub-resource-v2"
    http_body_max_bytes: int = 2 * 1024 * 1024
    http_header_max_bytes: int = 64 * 1024
    http_body_deadline_seconds: int = 15
    ws_frame_max_bytes: int = 2 * 1024 * 1024
    ws_message_max_bytes: int = 2 * 1024 * 1024
    ws_preauth_deadline_seconds: int = 8
    ws_frame_deadline_seconds: int = 60
    http_header_deadline_seconds: int = 8
    http_keepalive_idle_seconds: int = 20
    max_connections: int = 320
    max_header_connections: int = 32
    max_connections_per_peer: int = 320  # reverse proxies may share an IP
    max_websocket_connections: int = 256
    max_unauth_websocket_connections: int = 16

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


DEFAULT_RESOURCE_POLICY = HubResourcePolicy()
ENV_POLICY_FIELDS = {
    "MAIN_COMPUTER_HUB_HTTP_BODY_MAX_BYTES": "http_body_max_bytes",
    "MAIN_COMPUTER_HUB_HTTP_HEADER_MAX_BYTES": "http_header_max_bytes",
    "MAIN_COMPUTER_HUB_HTTP_BODY_DEADLINE_SECONDS": "http_body_deadline_seconds",
    "MAIN_COMPUTER_HUB_WS_FRAME_MAX_BYTES": "ws_frame_max_bytes",
    "MAIN_COMPUTER_HUB_WS_MESSAGE_MAX_BYTES": "ws_message_max_bytes",
    "MAIN_COMPUTER_HUB_WS_PREAUTH_DEADLINE_SECONDS": "ws_preauth_deadline_seconds",
    "MAIN_COMPUTER_HUB_WS_FRAME_DEADLINE_SECONDS": "ws_frame_deadline_seconds",
    "MAIN_COMPUTER_HUB_HTTP_HEADER_DEADLINE_SECONDS": "http_header_deadline_seconds",
    "MAIN_COMPUTER_HUB_HTTP_KEEPALIVE_IDLE_SECONDS": "http_keepalive_idle_seconds",
    "MAIN_COMPUTER_HUB_MAX_CONNECTIONS": "max_connections",
    "MAIN_COMPUTER_HUB_MAX_HEADER_CONNECTIONS": "max_header_connections",
    "MAIN_COMPUTER_HUB_MAX_CONNECTIONS_PER_PEER": "max_connections_per_peer",
    "MAIN_COMPUTER_HUB_MAX_WEBSOCKET_CONNECTIONS": "max_websocket_connections",
    "MAIN_COMPUTER_HUB_MAX_UNAUTH_WEBSOCKET_CONNECTIONS": "max_unauth_websocket_connections",
}


def load_hub_resource_policy(environ: Mapping[str, str] | None = None) -> HubResourcePolicy:
    env = os.environ if environ is None else environ
    fields = asdict(DEFAULT_RESOURCE_POLICY)
    for key, field in ENV_POLICY_FIELDS.items():
        if key not in env:
            continue
        try:
            value = int(env[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid resource policy {key}") from exc
        upper = getattr(DEFAULT_RESOURCE_POLICY, field)
        if not 1 <= value <= upper:
            raise ValueError(f"resource policy {key} must be in [1, {upper}]")
        fields[field] = value
    # Independent tightening is valid: the smaller applicable limit wins at
    # admission. Do not require every dependent ceiling to be overridden.
    return HubResourcePolicy(**fields)


class HubHTTPResourceError(ValueError):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


class HubWebSocketResourceError(ConnectionError):
    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code


def validate_http_headers(headers: Any, policy: HubResourcePolicy) -> None:
    # http.server limits individual header lines to 64 KiB and the number of
    # header lines; this adds a global aggregate bound after stdlib parsing.
    used = sum(len(str(k).encode("latin1", "replace")) + len(str(v).encode("latin1", "replace")) + 4
               for k, v in headers.items())
    if used > policy.http_header_max_bytes:
        raise HubHTTPResourceError(431, "http_headers_too_large")


def http_content_length(headers: Any, policy: HubResourcePolicy) -> int:
    if headers.get_all("Transfer-Encoding"):
        raise HubHTTPResourceError(400, "unsupported_transfer_encoding")
    lengths = headers.get_all("Content-Length", [])
    if len(lengths) > 1:
        raise HubHTTPResourceError(400, "ambiguous_content_length")
    if not lengths:
        return 0
    value = lengths[0].strip()
    if not value or not value.isascii() or not value.isdecimal():
        raise HubHTTPResourceError(400, "invalid_content_length")
    if len(value) > 20:
        raise HubHTTPResourceError(413, "http_body_too_large")
    length = int(value)
    if length > policy.http_body_max_bytes:
        raise HubHTTPResourceError(413, "http_body_too_large")
    return length


def exact_read_with_deadline(handler: Any, size: int, deadline: float) -> bytes:
    if size == 0:
        return b""
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("network_read_deadline")
        handler.connection.settimeout(remaining)
        try:
            chunk = handler.rfile.read(min(size - len(result), 64 * 1024))
        except (socket.timeout, TimeoutError) as exc:
            raise TimeoutError("network_read_deadline") from exc
        if not chunk:
            raise ConnectionError("connection_closed_during_read")
        result.extend(chunk)
    return bytes(result)


def read_bounded_json(handler: Any, policy: HubResourcePolicy) -> dict[str, Any]:
    validate_http_headers(handler.headers, policy)
    length = http_content_length(handler.headers, policy)
    old_timeout = handler.connection.gettimeout()
    try:
        raw = exact_read_with_deadline(handler, length, time.monotonic() + policy.http_body_deadline_seconds) if length else b"{}"
    except TimeoutError as exc:
        raise HubHTTPResourceError(408, "http_body_read_timeout") from exc
    except ConnectionError as exc:
        raise HubHTTPResourceError(400, "incomplete_http_body") from exc
    finally:
        handler.connection.settimeout(old_timeout)
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HubHTTPResourceError(400, "invalid_json_body") from exc
    if not isinstance(payload, dict):
        raise HubHTTPResourceError(400, "json_object_required")
    return payload


def close_ws_for_error(handler: Any, exc: HubWebSocketResourceError) -> None:
    """Send RFC 6455 close status, never an application-level accounting error."""
    try:
        handler._ws_send_frame(0x8, exc.code.to_bytes(2, "big"))
    except (OSError, ConnectionError):
        pass
    handler.close_connection = True


@dataclass
class _HubConnectionState:
    sock: socket.socket
    peer: str
    phase: str
    header_deadline: float
    websocket: bool = False
    websocket_pending_auth: bool = False


class BoundedHubHTTPServer(ThreadingHTTPServer):
    """Gate sockets before spawning worker threads, regardless of Hub backend.

    A single daemon watchdog enforces a *wall-clock* deadline on header reads,
    including trickle uploads and persistent HTTP connections. Socket shutdown
    interrupts blocked stdlib readline(), not merely the idle socket timeout.
    The gate never reads untrusted request data and never queries the ledger.
    """

    daemon_threads = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._resource_admission_lock = threading.Lock()
        self._resource_connections: dict[int, _HubConnectionState] = {}
        self._resource_peer_counts: dict[str, int] = {}
        self._resource_header_count = 0
        self._resource_websocket_count = 0
        self._resource_pending_websocket_count = 0
        self._resource_rejections = 0
        self._resource_header_timeouts = 0
        self._resource_peak_connections = 0
        self._resource_watchdog_stop = threading.Event()
        self._resource_watchdog_thread: threading.Thread | None = None
        # A bounded, separately serviced rejection lane is needed on Windows:
        # closing a TCP socket with unread request bytes can reset the connection
        # before the 503 response is delivered. Rejections must never create
        # unbounded handler threads or block the main accept loop.
        self._resource_reject_queue: queue.Queue[socket.socket] = queue.Queue(maxsize=32)
        self._resource_reject_lock = threading.Lock()
        self._resource_reject_threads: list[threading.Thread] = []
        self._resource_reject_stop = threading.Event()
        self._resource_reject_dropped = 0

    def resource_admission_snapshot(self) -> dict[str, int]:
        with self._resource_admission_lock:
            return {
                'connections': len(self._resource_connections),
                'header_connections': self._resource_header_count,
                'websocket_connections': self._resource_websocket_count,
                'unauth_websocket_connections': self._resource_pending_websocket_count,
                'rejections': self._resource_rejections,
                'expired_headers': self._resource_header_timeouts,
                'peak_connections': self._resource_peak_connections,
                'active_connections': sum(state.phase == 'active'
                                          for state in self._resource_connections.values()),
                'rejection_queue_depth': self._resource_reject_queue.qsize(),
                'rejection_queue_limit': self._resource_reject_queue.maxsize,
                'rejection_workers': len(self._resource_reject_threads),
                'rejection_dropped': self._resource_reject_dropped,
            }

    def _resource_watchdog_loop(self) -> None:
        while not self._resource_watchdog_stop.wait(0.05):
            expired: list[socket.socket] = []
            now = time.monotonic()
            with self._resource_admission_lock:
                for state in self._resource_connections.values():
                    if state.phase == 'headers' and now >= state.header_deadline:
                        state.phase = 'expired'
                        self._resource_header_count -= 1
                        self._resource_header_timeouts += 1
                        expired.append(state.sock)
            for sock in expired:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _resource_send_rejection(self, sock: socket.socket) -> None:
        """Best-effort 503 without an abortive close on Windows.

        HTTP rejection happens before parsing, so the client may already have
        transmitted its entire request. A close with those bytes unread can
        produce TCP RST and erase the response. Drain only a bounded amount,
        send a short reply, half-close the write side, and allow a short grace
        for the peer to consume it. Never run this on the accept-loop thread.
        """
        max_discard = 64 * 1024
        try:
            sock.setblocking(False)
            remaining = max_discard
            while remaining > 0:
                try:
                    chunk = sock.recv(min(8192, remaining))
                except (BlockingIOError, InterruptedError):
                    break
                if not chunk:
                    break
                remaining -= len(chunk)

            sock.settimeout(0.25)
            sock.sendall(b'HTTP/1.1 503 Service Unavailable\r\n'
                         b'Content-Length: 0\r\nConnection: close\r\n'
                         b'Retry-After: 1\r\n\r\n')
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            # Preserve the response across Windows' unread-data/reset rule.
            # Limit both time and bytes to avoid hostile request-body uploads.
            sock.settimeout(0.25)
            remaining = max_discard
            while remaining > 0:
                try:
                    chunk = sock.recv(min(8192, remaining))
                except (socket.timeout, BlockingIOError, InterruptedError):
                    break
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            self.shutdown_request(sock)

    def _resource_reject_worker(self) -> None:
        while True:
            try:
                sock = self._resource_reject_queue.get(timeout=0.1)
            except queue.Empty:
                if self._resource_reject_stop.is_set():
                    return
                continue
            try:
                self._resource_send_rejection(sock)
            finally:
                self._resource_reject_queue.task_done()

    def _resource_reject(self, sock: socket.socket) -> None:
        # A fixed worker pool plus bounded queue prevents the rejection path
        # from becoming a thread/socket exhaustion vector in its own right.
        if self._resource_reject_stop.is_set():
            self.shutdown_request(sock)
            return
        with self._resource_reject_lock:
            if not self._resource_reject_threads:
                for index in range(4):
                    thread = threading.Thread(
                        target=self._resource_reject_worker,
                        name=f'hub-reject-{index}', daemon=True)
                    self._resource_reject_threads.append(thread)
                    thread.start()
        try:
            self._resource_reject_queue.put_nowait(sock)
        except queue.Full:
            # Under extreme overload, fail closed rather than retain sockets.
            # A readable 503 is not guaranteed once this separate budget fills.
            with self._resource_admission_lock:
                self._resource_reject_dropped += 1
            self.shutdown_request(sock)

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        policy = getattr(self, 'resource_limits', DEFAULT_RESOURCE_POLICY)
        peer = str(client_address[0])
        accepted = False
        with self._resource_admission_lock:
            if (len(self._resource_connections) >= policy.max_connections
                    or self._resource_header_count >= policy.max_header_connections
                    or self._resource_peer_counts.get(peer, 0) >= policy.max_connections_per_peer):
                self._resource_rejections += 1
            else:
                if self._resource_watchdog_thread is None:
                    self._resource_watchdog_thread = threading.Thread(
                        target=self._resource_watchdog_loop, name='hub-resource-header-watchdog', daemon=True)
                    self._resource_watchdog_thread.start()
                self._resource_connections[id(request)] = _HubConnectionState(
                    sock=request, peer=peer, phase='headers',
                    header_deadline=time.monotonic() + policy.http_header_deadline_seconds)
                self._resource_header_count += 1
                self._resource_peer_counts[peer] = self._resource_peer_counts.get(peer, 0) + 1
                self._resource_peak_connections = max(self._resource_peak_connections,
                                                     len(self._resource_connections))
                accepted = True
        if not accepted:
            self._resource_reject(request)
            return
        try:
            request.settimeout(policy.http_header_deadline_seconds + 1)
            super().process_request(request, client_address)
        except BaseException:
            self._resource_release(request)
            self.shutdown_request(request)
            raise

    def _resource_release(self, sock: socket.socket) -> None:
        with self._resource_admission_lock:
            state = self._resource_connections.pop(id(sock), None)
            if state is None:
                return
            if state.phase == 'headers':
                self._resource_header_count -= 1
            if state.websocket:
                self._resource_websocket_count -= 1
            if state.websocket_pending_auth:
                self._resource_pending_websocket_count -= 1
            remaining = self._resource_peer_counts.get(state.peer, 0) - 1
            if remaining > 0:
                self._resource_peer_counts[state.peer] = remaining
            else:
                self._resource_peer_counts.pop(state.peer, None)

    def process_request_thread(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._resource_release(request)

    def resource_begin_headers(self, sock: socket.socket) -> bool:
        policy = getattr(self, 'resource_limits', DEFAULT_RESOURCE_POLICY)
        with self._resource_admission_lock:
            state = self._resource_connections.get(id(sock))
            if state is None or state.phase == 'expired':
                return False
            now = time.monotonic()
            if state.phase == 'headers':
                # Preserve the deadline assigned at accept(). A slow client
                # cannot refresh it by triggering another parsing pass.
                if now >= state.header_deadline:
                    return False
            else:
                if self._resource_header_count >= policy.max_header_connections:
                    self._resource_rejections += 1
                    return False
                self._resource_header_count += 1
                state.phase = 'headers'
                state.header_deadline = now + policy.http_header_deadline_seconds
        sock.settimeout(policy.http_header_deadline_seconds + 1)
        return True

    def resource_headers_complete(self, sock: socket.socket) -> bool:
        policy = getattr(self, 'resource_limits', DEFAULT_RESOURCE_POLICY)
        with self._resource_admission_lock:
            state = self._resource_connections.get(id(sock))
            if state is None or state.phase != 'headers':
                return False
            # Check time here too: a watchdog shutdown racing with completed
            # parsing must never allow an expired request into application code.
            if time.monotonic() >= state.header_deadline:
                state.phase = 'expired'
                self._resource_header_count -= 1
                self._resource_header_timeouts += 1
                return False
            state.phase = 'active'
            self._resource_header_count -= 1
        sock.settimeout(policy.http_keepalive_idle_seconds)
        return True

    def resource_claim_websocket(self, sock: socket.socket) -> bool:
        policy = getattr(self, 'resource_limits', DEFAULT_RESOURCE_POLICY)
        with self._resource_admission_lock:
            state = self._resource_connections.get(id(sock))
            if state is None or state.websocket or state.phase != 'active':
                return False
            if (self._resource_websocket_count >= policy.max_websocket_connections
                    or self._resource_pending_websocket_count >= policy.max_unauth_websocket_connections):
                self._resource_rejections += 1
                return False
            state.websocket = True
            state.websocket_pending_auth = True
            self._resource_websocket_count += 1
            self._resource_pending_websocket_count += 1
            return True

    def resource_websocket_authenticated(self, sock: socket.socket) -> None:
        with self._resource_admission_lock:
            state = self._resource_connections.get(id(sock))
            if state is not None and state.websocket_pending_auth:
                state.websocket_pending_auth = False
                self._resource_pending_websocket_count -= 1

    def server_close(self) -> None:
        self._resource_reject_stop.set()
        while True:
            try:
                pending = self._resource_reject_queue.get_nowait()
            except queue.Empty:
                break
            self.shutdown_request(pending)
            self._resource_reject_queue.task_done()
        for thread in self._resource_reject_threads:
            thread.join(timeout=0.5)
        self._resource_watchdog_stop.set()
        with self._resource_admission_lock:
            sockets = [state.sock for state in self._resource_connections.values()]
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._resource_watchdog_thread is not None:
            self._resource_watchdog_thread.join(timeout=1)
        super().server_close()
