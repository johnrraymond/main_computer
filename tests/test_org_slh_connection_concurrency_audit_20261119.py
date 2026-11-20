"""Isolated ORG-SLH concurrency/regression acceptance; no production state."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import socket
import ssl
import threading
import time

import pytest

from main_computer.slh.managed_gateway import transport_identity
from main_computer.slh.organization_service import (
    OrganizationHTTPHandler, OrganizationHTTPServer, make_seh_identity_listener,
)

_HOLD_STARTED = threading.Event()
_HOLD_RELEASE = threading.Event()


class _AuditHandler(OrganizationHTTPHandler):
    def _dispatch(self):
        if self.path == '/audit/hold':
            _HOLD_STARTED.set()
            _HOLD_RELEASE.wait(timeout=4)
            self._respond(200, {'released': True})
            return
        return super()._dispatch()


@contextmanager
def _service(tmp_path, *, restricted=False, max_connections=128):
    _HOLD_STARTED.clear()
    _HOLD_RELEASE.clear()
    tls, fingerprint = transport_identity(tmp_path)
    if restricted:
        server = make_seh_identity_listener(
            authority=object(), api=object(),
            tls_context=tls, fingerprint=fingerprint)
    else:
        server = OrganizationHTTPServer(('127.0.0.1', 0), _AuditHandler)
        server.max_concurrent_connections = max_connections
        server.configure_tls(tls)
        server.authority = object()
        server.fingerprint = fingerprint
        server.seh_identity_only = False
    server.tls_handshake_timeout = .5
    server.http_io_timeout = .75
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={'poll_interval': .025}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        _HOLD_RELEASE.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _get(port, path='/v1/org/identity', timeout=1.5):
    client = http.client.HTTPSConnection('127.0.0.1', port, timeout=timeout,
                                         context=ssl._create_unverified_context())
    try:
        client.request('GET', path)
        response = client.getresponse()
        response.read()
        return response.status
    finally:
        client.close()


def _stall_handshake(port):
    stalled = socket.create_connection(('127.0.0.1', port), timeout=2)
    # Raw TCP socket with no ClientHello intentionally occupies one handshake.
    time.sleep(.08)
    return stalled


def test_completed_tls_connections_run_in_parallel(tmp_path):
    with _service(tmp_path) as server:
        port = server.server_port
        with ThreadPoolExecutor(max_workers=10) as executor:
            held = executor.submit(_get, port, '/audit/hold', 5)
            assert _HOLD_STARTED.wait(timeout=2)
            assert _get(port, timeout=2) == 200
            assert not held.done()
            futures = [executor.submit(_get, port, timeout=3) for _ in range(8)]
            assert [future.result(timeout=4) for future in futures] == [200] * 8
            _HOLD_RELEASE.set()
            assert held.result(timeout=3) == 200


@pytest.mark.parametrize('restricted', [False, True])
def test_stalled_tls_handshake_does_not_block_healthy_clients(tmp_path, restricted):
    with _service(tmp_path, restricted=restricted) as server:
        port = server.server_port
        assert _get(port) == 200
        stalled = _stall_handshake(port)
        try:
            # The previous implementation blocked the accept loop here.
            assert _get(port, timeout=1.25) == 200
            stalled.settimeout(1.5)
            # Handshake must expire independently, without a client write.
            assert stalled.recv(1) == b''
            assert _get(port, timeout=1.25) == 200
            if restricted:
                assert _get(port, '/forbidden', timeout=1.25) == 403
                assert server.server_address[0] == '127.0.0.1'
        finally:
            stalled.close()


def test_stalled_handshake_is_bounded_without_stalling_accept_loop(tmp_path):
    with _service(tmp_path, max_connections=2) as server:
        port = server.server_port
        sockets = [socket.create_connection(('127.0.0.1', port), timeout=2)
                   for _ in range(2)]
        try:
            # The bounded pool must reject excess requests promptly instead
            # of blocking its accept loop or creating unbounded threads.
            deadline = time.monotonic() + 1
            while getattr(server._connection_slots, '_value') != 0:
                assert time.monotonic() < deadline
                time.sleep(.005)
            started = time.monotonic()
            with pytest.raises((ssl.SSLError, OSError, ConnectionError)):
                _get(port, timeout=.7)
            assert time.monotonic() - started < .7
            # After a stalled handshake expires, normal traffic resumes.
            time.sleep(.55)
            assert _get(port, timeout=1.5) == 200
        finally:
            for connection in sockets:
                connection.close()


def test_slow_partial_http_body_does_not_block_independent_tls_requests(tmp_path):
    with _service(tmp_path) as server:
        port = server.server_port
        raw = socket.create_connection(('127.0.0.1', port), timeout=2)
        client = ssl._create_unverified_context().wrap_socket(
            raw, server_hostname='localhost')
        try:
            client.sendall(b'POST /v1/auth/challenge HTTP/1.1\r\n'
                           b'Host: localhost\r\nContent-Length: 90\r\n'
                           b'Content-Type: application/json\r\n\r\n{')
            assert _get(port, timeout=1.25) == 200
            # The incomplete body will time out, not occupy a thread forever.
            client.settimeout(2)
            try:
                client.recv(1024)
            except (OSError, TimeoutError):
                pass
            assert _get(port, timeout=1.25) == 200
        finally:
            client.close()
