"""Actual private TLS dialing and Mother's deployment contract."""
from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request

import pytest

from main_computer.hub_private_https import (
    REQUIRE_ENV, ROUTES_ENV, open_internal_https,
)
from tools.hub_control.common.deployment import private_https_routes
from tools.hub_control.common.errors import HubControlError


def test_deployment_routes_use_mother_vpn_addresses():
    private = {"networks": {"mainnet": {"coolify": {"controllers": {
        "coolify-a": {"vpn_ip": "10.116.0.3"},
        "coolify-c": {"vpn_ip": "10.116.0.2"},
    }}}}}
    routes = private_https_routes(private, "mainnet", [
        {"controller_id": "coolify-a", "public_url": "https://mainneta-hub1.greatlibrary.io"},
        {"controller_id": "coolify-c", "public_url": "https://mainnetc-hub1.greatlibrary.io"},
    ], "https://mainnet-rpc.greatlibrary.io")
    assert routes == {
        "mainnet-rpc.greatlibrary.io": "10.116.0.3",
        "mainneta-hub1.greatlibrary.io": "10.116.0.3",
        "mainnetc-hub1.greatlibrary.io": "10.116.0.2",
    }


def test_deployment_fails_for_missing_or_untrusted_vpn(monkeypatch):
    private = {"networks": {"mainnet": {"coolify": {"controllers": {
        "coolify-a": {"vpn_ip": "104.21.13.200"},
        "coolify-c": {"vpn_ip": "10.116.0.2"},
    }}}}}
    with pytest.raises(HubControlError, match="private IPv4"):
        private_https_routes(private, "mainnet", [], "https://mainnet-rpc.greatlibrary.io")


def test_required_route_missing_cannot_use_public_dns(monkeypatch):
    monkeypatch.setenv(ROUTES_ENV, json.dumps({"mainnet-rpc.greatlibrary.io": "10.116.0.3"}))
    monkeypatch.setenv(REQUIRE_ENV, "true")
    called = []
    with pytest.raises(URLError, match="No private HTTPS route"):
        open_internal_https(Request("https://mainnetc-hub1.greatlibrary.io/health"), timeout=1,
                            default_open=lambda *a, **k: called.append(True))
    assert called == []


def test_invalid_mapping_fails_closed(monkeypatch):
    monkeypatch.setenv(ROUTES_ENV, '{bad')
    monkeypatch.setenv(REQUIRE_ENV, "true")
    with pytest.raises(URLError, match="valid JSON"):
        open_internal_https(Request("https://mainnet-rpc.greatlibrary.io"), timeout=1)


def test_no_route_in_unconfigured_dev_uses_existing_urlopen(monkeypatch):
    monkeypatch.delenv(ROUTES_ENV, raising=False)
    monkeypatch.delenv(REQUIRE_ENV, raising=False)
    marker = object()
    req = Request("https://example.org/")
    assert open_internal_https(req, timeout=2, default_open=lambda r, timeout: marker) is marker


def test_private_https_reaches_vpn_ip_with_original_sni_host_and_tls_verification(tmp_path, monkeypatch):
    openssl = __import__("shutil").which("openssl")
    if not openssl:
        pytest.skip("openssl executable not installed for TLS integration test")
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run([
        openssl, "req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "1",
        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=mainnet-rpc.greatlibrary.io",
        "-addext", "subjectAltName=DNS:mainnet-rpc.greatlibrary.io",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["host"] = self.headers.get("Host")
            payload = b'{"result":"0x28757b0"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(cert), str(key))
    def capture_sni(_socket, server_name, _context):
        seen["sni"] = server_name
    server_context.set_servername_callback(capture_sni)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    actual_connect = socket.create_connection
    def route_to_localhost(address, *args, **kwargs):
        seen["tcp_dest"] = address
        assert address[0] == "10.116.0.3", "private route must not connect to DNS hostname"
        return actual_connect(("127.0.0.1", address[1]), *args, **kwargs)
    monkeypatch.setattr("main_computer.hub_private_https.socket.create_connection", route_to_localhost)
    monkeypatch.setenv(ROUTES_ENV, json.dumps({"mainnet-rpc.greatlibrary.io": "10.116.0.3"}))
    monkeypatch.setenv(REQUIRE_ENV, "true")
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))
    req = Request(f"https://mainnet-rpc.greatlibrary.io:{server.server_port}/", data=b"{}", method="POST")
    try:
        with open_internal_https(req, timeout=3) as response:
            assert response.status == 200
            assert json.loads(response.read())["result"] == "0x28757b0"
        assert seen["tcp_dest"] == ("10.116.0.3", server.server_port)
        assert seen["sni"] == "mainnet-rpc.greatlibrary.io"
        assert seen["host"] == f"mainnet-rpc.greatlibrary.io:{server.server_port}"
    finally:
        thread.join(timeout=1)
        server.server_close()


def test_private_connection_failure_never_falls_back_to_default_open(monkeypatch):
    monkeypatch.setenv(ROUTES_ENV, json.dumps({"mainnet-rpc.greatlibrary.io": "10.116.0.3"}))
    monkeypatch.setenv(REQUIRE_ENV, "true")
    monkeypatch.setattr("main_computer.hub_private_https.socket.create_connection", lambda *a, **kw: (_ for _ in ()).throw(OSError("VPN down")))
    fallback = []
    with pytest.raises(URLError, match="VPN down"):
        open_internal_https(Request("https://mainnet-rpc.greatlibrary.io/"), timeout=1,
            default_open=lambda *args, **kwargs: fallback.append(True))
    assert fallback == []
