from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator
from urllib.parse import urlsplit


@dataclass
class HubHttpResponse:
    ok: bool
    status: int
    payload: dict[str, Any]
    elapsed_ms: float
    base_url: str = ""


@dataclass
class HubStreamEvent:
    event: str
    payload: dict[str, Any]
    elapsed_ms: float = 0.0
    status: int = 0
    base_url: str = ""
    transport: dict[str, Any] = field(default_factory=dict)


class HubTransport:
    def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float,
        headers: dict[str, str] | None = None,
    ) -> HubHttpResponse:
        raise NotImplementedError

    def stream_jsonl(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float,
        headers: dict[str, str] | None = None,
    ) -> Iterator[HubStreamEvent]:
        raise NotImplementedError

    def close(self) -> None:
        pass


@dataclass(frozen=True)
class _OriginKey:
    scheme: str
    host: str
    port: int

    @property
    def display(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


@dataclass
class _PooledConnection:
    id: int
    origin: _OriginKey
    connection: http.client.HTTPConnection
    request_count: int = 0
    last_used: float = field(default_factory=time.monotonic)


def _origin_and_target(url: str) -> tuple[_OriginKey, str]:
    parsed = urlsplit(str(url))
    scheme = (parsed.scheme or "http").lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"unsupported hub URL scheme: {scheme!r}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"hub URL is missing a host: {url!r}")
    port = int(parsed.port or (443 if scheme == "https" else 80))
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return _OriginKey(scheme=scheme, host=host, port=port), path


def _transport_error_kind(exc: BaseException | None) -> str:
    text = str(exc or "").lower()
    class_name = type(exc).__name__.lower() if exc is not None else ""
    if "timed out" in text or "timeout" in text or "timeout" in class_name:
        return "timeout"
    if "connection refused" in text or "winerror 10061" in text or "errno 111" in text:
        return "connection_refused"
    if "temporary failure in name resolution" in text or "name or service not known" in text or "nodename nor servname" in text:
        return "dns_error"
    if "connection reset" in text or "forcibly closed" in text or "broken pipe" in text:
        return "connection_reset"
    if "network is unreachable" in text or "no route to host" in text:
        return "network_unreachable"
    if "pool exhausted" in text:
        return "connection_pool_exhausted"
    return "transport_error"


def _diagnostic_payload(
    *,
    error: str,
    error_type: str,
    method: str,
    path: str,
    url: str,
    base_url: str,
    error_kind: str = "",
    exception_class: str = "",
    status: int | None = None,
    response_text: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": str(error),
        "error_type": str(error_type),
        "method": str(method).upper(),
        "path": str(path),
        "url": str(url),
        "base_url": str(base_url).rstrip("/"),
    }
    if error_kind:
        payload["error_kind"] = str(error_kind)
    if exception_class:
        payload["exception_class"] = str(exception_class)
    if status is not None:
        payload["http_status"] = int(status)
    if response_text:
        payload["response_text"] = str(response_text)[:500]
    return payload


def _base_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
        "Connection": "keep-alive",
    }
    for key, value in dict(extra or {}).items():
        headers[str(key)] = str(value)
    return headers


def _request_body(method: str, payload: dict[str, Any] | None) -> bytes | None:
    if method.upper() in {"GET", "HEAD"}:
        return None
    return json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")


def _connection_should_close(response: http.client.HTTPResponse) -> bool:
    connection_header = str(response.getheader("Connection", "") or "").lower()
    return "close" in connection_header or bool(getattr(response, "will_close", False))


def _attach_transport_diagnostics(payload: dict[str, Any], diagnostics: dict[str, Any]) -> dict[str, Any]:
    payload.update(diagnostics)
    return payload


class KeepAliveConnectionPool:
    def __init__(
        self,
        *,
        max_connections_per_origin: int = 2,
        idle_timeout_seconds: float = 30.0,
        max_requests_per_connection: int = 1000,
    ) -> None:
        self.max_connections_per_origin = max(1, int(max_connections_per_origin))
        self.idle_timeout_seconds = max(0.1, float(idle_timeout_seconds))
        self.max_requests_per_connection = max(1, int(max_requests_per_connection))
        self._idle: dict[_OriginKey, list[_PooledConnection]] = {}
        self._active_counts: dict[_OriginKey, int] = {}
        self._all_connections: dict[int, _PooledConnection] = {}
        self._next_connection_id = 1
        self._lock = threading.Lock()

    def _new_http_connection(self, origin: _OriginKey, timeout_seconds: float) -> http.client.HTTPConnection:
        connection_cls = http.client.HTTPSConnection if origin.scheme == "https" else http.client.HTTPConnection
        return connection_cls(origin.host, origin.port, timeout=float(timeout_seconds))

    def _close_connection(self, pooled: _PooledConnection) -> None:
        try:
            pooled.connection.close()
        except Exception:
            pass

    def _is_idle_usable(self, pooled: _PooledConnection, now: float) -> bool:
        if pooled.request_count >= self.max_requests_per_connection:
            return False
        if now - pooled.last_used > self.idle_timeout_seconds:
            return False
        return True

    def _prune_idle_locked(self, origin: _OriginKey, now: float) -> None:
        bucket = self._idle.get(origin, [])
        kept: list[_PooledConnection] = []
        for pooled in bucket:
            if self._is_idle_usable(pooled, now):
                kept.append(pooled)
            else:
                self._all_connections.pop(pooled.id, None)
                self._close_connection(pooled)
        if kept:
            self._idle[origin] = kept
        else:
            self._idle.pop(origin, None)

    def borrow(self, origin: _OriginKey, *, timeout_seconds: float) -> tuple[_PooledConnection, bool]:
        now = time.monotonic()
        with self._lock:
            self._prune_idle_locked(origin, now)
            bucket = self._idle.get(origin, [])
            if bucket:
                pooled = bucket.pop()
                if not bucket:
                    self._idle.pop(origin, None)
                self._active_counts[origin] = self._active_counts.get(origin, 0) + 1
                pooled.connection.timeout = float(timeout_seconds)
                if getattr(pooled.connection, "sock", None) is not None:
                    try:
                        pooled.connection.sock.settimeout(float(timeout_seconds))
                    except Exception:
                        pass
                return pooled, True

            active = self._active_counts.get(origin, 0)
            if active >= self.max_connections_per_origin:
                raise TimeoutError(f"connection pool exhausted for {origin.display}")

            connection_id = self._next_connection_id
            self._next_connection_id += 1
            pooled = _PooledConnection(
                id=connection_id,
                origin=origin,
                connection=self._new_http_connection(origin, timeout_seconds),
            )
            self._all_connections[connection_id] = pooled
            self._active_counts[origin] = active + 1
            return pooled, False

    def release(self, pooled: _PooledConnection, *, reusable: bool) -> None:
        with self._lock:
            active = max(0, self._active_counts.get(pooled.origin, 0) - 1)
            if active:
                self._active_counts[pooled.origin] = active
            else:
                self._active_counts.pop(pooled.origin, None)

            pooled.last_used = time.monotonic()
            if reusable and pooled.request_count < self.max_requests_per_connection:
                self._idle.setdefault(pooled.origin, []).append(pooled)
                return

            self._all_connections.pop(pooled.id, None)
            self._close_connection(pooled)

    def close(self) -> None:
        with self._lock:
            connections = list(self._all_connections.values())
            self._idle.clear()
            self._active_counts.clear()
            self._all_connections.clear()
        for pooled in connections:
            self._close_connection(pooled)


class KeepAliveHubTransport(HubTransport):
    def __init__(
        self,
        *,
        pool: KeepAliveConnectionPool | None = None,
        max_connections_per_origin: int = 2,
        idle_timeout_seconds: float = 30.0,
        max_requests_per_connection: int = 1000,
    ) -> None:
        self.pool = pool or KeepAliveConnectionPool(
            max_connections_per_origin=max_connections_per_origin,
            idle_timeout_seconds=idle_timeout_seconds,
            max_requests_per_connection=max_requests_per_connection,
        )

    def _diagnostics(
        self,
        *,
        origin: _OriginKey,
        pooled: _PooledConnection | None,
        connection_reused: bool,
        connection_error: str = "",
    ) -> dict[str, Any]:
        return {
            "transport_mode": "keepalive",
            "connection_reused": bool(connection_reused),
            "connection_id": int(pooled.id) if pooled is not None else 0,
            "origin": origin.display,
            "connection_error": str(connection_error or ""),
        }

    def request_json(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float,
        headers: dict[str, str] | None = None,
    ) -> HubHttpResponse:
        method_upper = str(method).upper()
        started = time.perf_counter()
        pooled: _PooledConnection | None = None
        connection_reused = False
        origin: _OriginKey | None = None
        target = ""
        reusable = False

        try:
            origin, target = _origin_and_target(url)
            pooled, connection_reused = self.pool.borrow(origin, timeout_seconds=timeout_seconds)
            body = _request_body(method_upper, payload)
            request_headers = _base_headers(headers)
            pooled.connection.request(method_upper, target, body=body, headers=request_headers)
            response = pooled.connection.getresponse()
            try:
                raw = response.read()
            except Exception:
                reusable = False
                raise
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            pooled.request_count += 1
            reusable = not _connection_should_close(response)

            diagnostics = self._diagnostics(origin=origin, pooled=pooled, connection_reused=connection_reused)
            try:
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception as exc:
                diagnostic = _diagnostic_payload(
                    error=f"JSON parse failed: {exc}",
                    error_type="parse_error",
                    error_kind="json_parse_error",
                    exception_class=type(exc).__name__,
                    method=method_upper,
                    path=target,
                    url=url,
                    base_url=origin.base_url,
                    status=response.status,
                    response_text=raw.decode("utf-8", errors="replace") if raw else "",
                )
                return HubHttpResponse(
                    ok=False,
                    status=int(response.status),
                    payload=_attach_transport_diagnostics(diagnostic, diagnostics),
                    elapsed_ms=elapsed_ms,
                    base_url=origin.base_url,
                )

            status = int(response.status)
            if not isinstance(parsed, dict):
                parsed = {"value": parsed}
            if not (200 <= status < 300):
                parsed.setdefault("error_type", "http_error")
                parsed.setdefault("error_kind", f"http_{status}")
                parsed.setdefault("http_status", status)
            parsed.setdefault("method", method_upper)
            parsed.setdefault("path", target)
            return HubHttpResponse(
                ok=200 <= status < 300,
                status=status,
                payload=_attach_transport_diagnostics(parsed, diagnostics),
                elapsed_ms=elapsed_ms,
                base_url=origin.base_url,
            )
        except (http.client.HTTPException, OSError, TimeoutError, socket.timeout, ValueError) as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if origin is None:
                try:
                    origin, target = _origin_and_target(url)
                except Exception:
                    origin = _OriginKey(scheme="http", host="", port=0)
                    target = url
            error_kind = _transport_error_kind(exc)
            diagnostics = self._diagnostics(
                origin=origin,
                pooled=pooled,
                connection_reused=connection_reused,
                connection_error=error_kind,
            )
            return HubHttpResponse(
                ok=False,
                status=0,
                payload=_attach_transport_diagnostics(
                    _diagnostic_payload(
                        error=str(exc),
                        error_type="transport",
                        error_kind=error_kind,
                        exception_class=type(exc).__name__,
                        method=method_upper,
                        path=target,
                        url=url,
                        base_url=origin.base_url,
                    ),
                    diagnostics,
                ),
                elapsed_ms=elapsed_ms,
                base_url=origin.base_url,
            )
        finally:
            if pooled is not None:
                self.pool.release(pooled, reusable=reusable)

    def stream_jsonl(
        self,
        method: str,
        url: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout_seconds: float,
        headers: dict[str, str] | None = None,
    ) -> Iterator[HubStreamEvent]:
        method_upper = str(method).upper()
        started = time.perf_counter()
        pooled: _PooledConnection | None = None
        connection_reused = False
        origin: _OriginKey | None = None
        target = ""
        reusable = False
        response: http.client.HTTPResponse | None = None

        try:
            origin, target = _origin_and_target(url)
            pooled, connection_reused = self.pool.borrow(origin, timeout_seconds=timeout_seconds)
            body = _request_body(method_upper, payload)
            request_headers = _base_headers({"Accept": "application/jsonl, application/x-ndjson, application/json", **dict(headers or {})})
            pooled.connection.request(method_upper, target, body=body, headers=request_headers)
            response = pooled.connection.getresponse()
            pooled.request_count += 1
            diagnostics = self._diagnostics(origin=origin, pooled=pooled, connection_reused=connection_reused)
            status = int(response.status)

            if status < 200 or status >= 300:
                raw = response.read()
                reusable = not _connection_should_close(response)
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                try:
                    parsed = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    parsed = {
                        "event": "error",
                        "error": raw.decode("utf-8", errors="replace")[:500],
                        "error_type": "http_error",
                        "error_kind": f"http_{status}",
                    }
                if not isinstance(parsed, dict):
                    parsed = {"value": parsed}
                parsed.setdefault("event", "error")
                parsed.setdefault("error_type", "http_error")
                parsed.setdefault("error_kind", f"http_{status}")
                parsed.setdefault("method", method_upper)
                parsed.setdefault("path", target)
                parsed.setdefault("http_status", status)
                _attach_transport_diagnostics(parsed, diagnostics)
                yield HubStreamEvent(
                    event=str(parsed.get("event") or "error"),
                    payload=parsed,
                    elapsed_ms=elapsed_ms,
                    status=status,
                    base_url=origin.base_url,
                    transport=diagnostics,
                )
                return

            while True:
                line = response.readline()
                if not line:
                    reusable = not _connection_should_close(response)
                    return
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed = json.loads(stripped.decode("utf-8"))
                    if not isinstance(parsed, dict):
                        parsed = {"event": "message", "value": parsed}
                    event_name = str(parsed.get("event") or "message")
                    parsed.setdefault("method", method_upper)
                    parsed.setdefault("path", target)
                    _attach_transport_diagnostics(parsed, diagnostics)
                    yield HubStreamEvent(
                        event=event_name,
                        payload=parsed,
                        elapsed_ms=elapsed_ms,
                        status=status,
                        base_url=origin.base_url,
                        transport=diagnostics,
                    )
                    if event_name == "done":
                        reusable = False
                        return
                except Exception as exc:
                    reusable = False
                    error_kind = "jsonl_parse_error"
                    error_payload = _diagnostic_payload(
                        error=f"JSONL parse failed: {exc}",
                        error_type="stream_protocol_error",
                        error_kind=error_kind,
                        exception_class=type(exc).__name__,
                        method=method_upper,
                        path=target,
                        url=url,
                        base_url=origin.base_url,
                        status=status,
                        response_text=stripped.decode("utf-8", errors="replace"),
                    )
                    _attach_transport_diagnostics(error_payload, {**diagnostics, "connection_error": error_kind})
                    yield HubStreamEvent(
                        event="error",
                        payload=error_payload,
                        elapsed_ms=elapsed_ms,
                        status=status,
                        base_url=origin.base_url,
                        transport=diagnostics,
                    )
                    return
        except (http.client.HTTPException, OSError, TimeoutError, socket.timeout, ValueError) as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if origin is None:
                try:
                    origin, target = _origin_and_target(url)
                except Exception:
                    origin = _OriginKey(scheme="http", host="", port=0)
                    target = url
            error_kind = _transport_error_kind(exc)
            diagnostics = self._diagnostics(
                origin=origin,
                pooled=pooled,
                connection_reused=connection_reused,
                connection_error=error_kind,
            )
            payload_dict = _diagnostic_payload(
                error=str(exc),
                error_type="transport",
                error_kind=error_kind,
                exception_class=type(exc).__name__,
                method=method_upper,
                path=target,
                url=url,
                base_url=origin.base_url,
            )
            _attach_transport_diagnostics(payload_dict, diagnostics)
            yield HubStreamEvent(
                event="error",
                payload=payload_dict,
                elapsed_ms=elapsed_ms,
                status=0,
                base_url=origin.base_url,
                transport=diagnostics,
            )
        finally:
            if pooled is not None:
                self.pool.release(pooled, reusable=reusable)

    def close(self) -> None:
        self.pool.close()
