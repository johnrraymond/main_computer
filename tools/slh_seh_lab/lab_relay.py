"""Loopback-only opaque TCP relay for testing the SEH transport boundary.

This is a lab fixture, not the production Secure External Hub. The SLH TLS
handshake passes THROUGH it: the relay owns no SLH signing key and never
terminates TLS. No deployment or remote authentication is claimed.
"""
from __future__ import annotations

import select
import socket
import socketserver
import threading


class _RelayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        relay = self.server
        try:
            upstream = socket.create_connection(relay.upstream, timeout=3)
        except OSError:
            return
        with upstream:
            sockets = (self.request, upstream)
            while True:
                try:
                    readable, _, _ = select.select(sockets, [], [], 1)
                    if not readable:
                        continue
                    for endpoint in readable:
                        chunk = endpoint.recv(65536)
                        if not chunk:
                            return
                        destination = upstream if endpoint is self.request else self.request
                        destination.sendall(chunk)
                        with relay.stats_lock:
                            relay.bytes_forwarded += len(chunk)
                except OSError:
                    return


class SEHTestRelay(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, host: str, port: int, upstream_host: str, upstream_port: int):
        if host != '127.0.0.1' or upstream_host != '127.0.0.1':
            raise ValueError('SEH lab relay must bind and forward only on loopback')
        self.upstream = (upstream_host, upstream_port)
        self.bytes_forwarded = 0
        self.stats_lock = threading.Lock()
        super().__init__((host, port), _RelayHandler)
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self.serve_forever, daemon=True, name='seh-test-relay')
        self._thread.start()
        return self

    def stop(self):
        self.shutdown()
        self.server_close()
        if self._thread:
            self._thread.join(timeout=3)
