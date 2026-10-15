"""HTTPS over Mother-owned VPN addresses without changing Host, SNI or TLS identity.

MAIN_COMPUTER_HUB_PRIVATE_HTTPS_ROUTES is a hostname -> IPv4 address JSON map
installed by Hub Control. This is a *connection* override, not a public URL,
certificate, hostname, proxy, or blockchain identity override.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import socket
from typing import Callable
from urllib.parse import urlsplit
from urllib.error import URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener, urlopen

ROUTES_ENV = "MAIN_COMPUTER_HUB_PRIVATE_HTTPS_ROUTES"
REQUIRE_ENV = "MAIN_COMPUTER_HUB_REQUIRE_PRIVATE_HTTPS_ROUTES"


def _private_address(request: Request) -> str:
    raw = os.environ.get(ROUTES_ENV, "").strip()
    required = os.environ.get(REQUIRE_ENV, "").strip().lower() in {"1", "true", "yes"}
    if not raw:
        if required:
            raise ValueError(f"{ROUTES_ENV} is required for Hub internal HTTPS traffic")
        return ""
    try:
        routes = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{ROUTES_ENV} is not valid JSON") from exc
    if not isinstance(routes, dict):
        raise ValueError(f"{ROUTES_ENV} must be an object")
    parsed = urlsplit(request.full_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Hub internal routing requires an HTTPS URL with hostname")
    value = routes.get(parsed.hostname.lower())
    if value is None:
        if required:
            raise ValueError(f"No private HTTPS route for {parsed.hostname}")
        return ""
    try:
        ip = ipaddress.ip_address(str(value))
    except ValueError as exc:
        raise ValueError(f"Invalid VPN address for {parsed.hostname}") from exc
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ValueError(f"Private HTTPS route for {parsed.hostname} must use a private IPv4 address")
    return str(ip)


class _PinnedHTTPSHandler(HTTPSHandler):
    """Dial the assigned VPN IP; let HTTPSConnection verify the original host."""

    def __init__(self, private_ip: str) -> None:
        super().__init__()
        self.private_ip = private_ip

    def https_open(self, req: Request):
        def connection_factory(host: str, **kwargs):
            connection = http.client.HTTPSConnection(host, **kwargs)

            def private_connect(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
                # http.client's connect() still sends TLS SNI for `host` and
                # verifies the certificate against it. Only TCP routing changes.
                return socket.create_connection(
                    (self.private_ip, address[1]), timeout, source_address
                )

            connection._create_connection = private_connect
            return connection

        return self.do_open(connection_factory, req, context=self._context)


def open_internal_https(request: Request, *, timeout: float, default_open: Callable = urlopen):
    """Use private TLS when deployed with a route; no public fallback on errors."""
    try:
        ip = _private_address(request)
    except ValueError as exc:
        raise URLError(str(exc)) from exc
    if not ip:
        return default_open(request, timeout=timeout)
    # Never honor ambient HTTPS_PROXY for explicitly private infrastructure.
    opener = build_opener(ProxyHandler({}), _PinnedHTTPSHandler(ip))
    return opener.open(request, timeout=timeout)
