#!/usr/bin/env python3
"""Fail-closed Tor route test for main_computer.tor_web_search.

This script exercises the actual ``main_computer`` Tor-only search code, not a
separate requests/ddgs prototype.

Default route:
  main_computer.tor_web_search.web_search
      -> socks5h://127.0.0.1:<9050 or 9150>
      -> https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/html/

Examples, from the repo root:

  python tools/test_tor_ddg_onion_web_search.py --proxy socks5h://127.0.0.1:9150

  python tools/test_tor_ddg_onion_web_search.py \
    --proxy socks5h://127.0.0.1:9150 \
    --query "example domain"

Security posture:
  - No direct fallback.
  - Only socks5h:// loopback Tor proxies are accepted.
  - Local DNS lookups for non-localhost names are blocked during the test.
  - Outbound sockets are blocked during the test except to the configured local
    Tor SOCKS port.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
from types import TracebackType
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from main_computer.tor_web_search import (  # noqa: E402
    DEFAULT_DDG_ONION_URL,
    TorOnlySearchError,
    parse_tor_proxy,
    web_search as main_computer_web_search,
)


DEFAULT_QUERY = "example domain"
DEFAULT_MAX_RESULTS = 5


class RouteTestFailure(RuntimeError):
    """Raised when the fail-closed route test fails."""


class DirectNetworkBlocked(RouteTestFailure):
    """Raised when code attempts to bypass the configured local Tor proxy."""


def is_loopback_host(host: str | None) -> bool:
    if host is None:
        return True

    clean = str(host).strip("[]").lower()
    if clean in {"localhost", "ip6-localhost"}:
        return True

    try:
        return ipaddress.ip_address(clean).is_loopback
    except ValueError:
        return False


def tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def auto_detect_tor_proxy() -> str:
    # Tor daemon usually uses 9050. Tor Browser usually uses 9150.
    for port in (9050, 9150):
        if tcp_open("127.0.0.1", port):
            return f"socks5h://127.0.0.1:{port}"

    raise RouteTestFailure(
        "Could not find Tor SOCKS on 127.0.0.1:9050 or 127.0.0.1:9150. "
        "Start Tor, or pass --proxy socks5h://127.0.0.1:<port>."
    )


class NetworkGuard:
    """Process-local guard against direct outbound sockets and local DNS leaks.

    This is a test harness, not a substitute for an OS firewall kill switch.
    It catches accidental direct requests during this Python process.
    """

    def __init__(self, allowed_loopback_ports: set[int]) -> None:
        self.allowed_loopback_ports = set(allowed_loopback_ports)
        self._orig_connect: Any = None
        self._orig_getaddrinfo: Any = None

    def __enter__(self) -> "NetworkGuard":
        self._orig_connect = socket.socket.connect
        self._orig_getaddrinfo = socket.getaddrinfo
        allowed_ports = self.allowed_loopback_ports
        orig_connect = self._orig_connect
        orig_getaddrinfo = self._orig_getaddrinfo

        def guarded_connect(sock: socket.socket, address: Any) -> Any:
            # AF_UNIX and other non-TCP local IPC are allowed.
            if not isinstance(address, tuple) or len(address) < 2:
                return orig_connect(sock, address)

            host = str(address[0]).strip("[]")
            try:
                port = int(address[1])
            except Exception as exc:
                raise DirectNetworkBlocked(f"Blocked socket with unparsable port: {address!r}") from exc

            if is_loopback_host(host) and port in allowed_ports:
                return orig_connect(sock, address)

            raise DirectNetworkBlocked(
                f"Blocked direct network connect to {host}:{port}. "
                f"Only local Tor SOCKS ports are allowed: {sorted(allowed_ports)}"
            )

        def guarded_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
            # socks5h should only resolve the local proxy. Non-local DNS means a
            # leak or direct path.
            if host is None or is_loopback_host(str(host)):
                return orig_getaddrinfo(host, port, *args, **kwargs)

            raise DirectNetworkBlocked(
                f"Blocked local DNS lookup for {host!r}. "
                "Use socks5h:// and route host resolution through Tor."
            )

        socket.socket.connect = guarded_connect  # type: ignore[method-assign]
        socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._orig_connect is not None:
            socket.socket.connect = self._orig_connect  # type: ignore[method-assign]
        if self._orig_getaddrinfo is not None:
            socket.getaddrinfo = self._orig_getaddrinfo  # type: ignore[assignment]


def import_callable(spec: str) -> Callable[..., Any]:
    if ":" not in spec:
        raise RouteTestFailure(f"Invalid import spec {spec!r}. Expected module.path:function_name")

    module_name, attr_path = spec.split(":", 1)
    module = importlib.import_module(module_name.strip())
    target: Any = module

    for part in attr_path.strip().split("."):
        target = getattr(target, part)

    if not callable(target):
        raise RouteTestFailure(f"Imported object is not callable: {spec}")

    return target


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def call_search_function(
    fn: Callable[..., Any],
    *,
    query: str,
    max_results: int,
    proxy: str,
    onion_url: str,
    timeout_s: float,
) -> Any:
    """Call either the built-in function or a compatible custom search function."""

    attempts = [
        lambda: fn(query, max_results=max_results, proxy=proxy, onion_url=onion_url, timeout_s=timeout_s),
        lambda: fn(query=query, max_results=max_results, proxy=proxy, onion_url=onion_url, timeout_s=timeout_s),
        lambda: fn(query, max_results=max_results),
        lambda: fn(query=query, max_results=max_results),
        lambda: fn(query),
    ]

    last_type_error: TypeError | None = None

    for attempt in attempts:
        try:
            return asyncio.run(maybe_await(attempt()))
        except TypeError as exc:
            last_type_error = exc

    raise RouteTestFailure(f"Could not call search function with common signatures: {last_type_error}")


def normalize_preview(value: Any, max_items: int = 3) -> Any:
    if isinstance(value, list):
        return value[:max_items]
    if isinstance(value, tuple):
        return list(value[:max_items])
    if isinstance(value, dict):
        preview = dict(value)
        for key, item in list(preview.items()):
            if isinstance(item, list):
                preview[key] = item[:max_items]
        return preview
    return str(value)[:1000]


def set_fail_closed_env(proxy: str, onion_url: str) -> None:
    os.environ["TOR_PROXY"] = proxy
    os.environ["MAIN_COMPUTER_FORCE_TOR"] = "1"
    os.environ["MAIN_COMPUTER_TOR_ONLY"] = "1"
    os.environ["MAIN_COMPUTER_DDG_ONION_URL"] = onion_url.rstrip("/")
    os.environ["MAIN_COMPUTER_DDG_BASE_URL"] = onion_url.rstrip("/")
    os.environ["DUCKDUCKGO_ONION_URL"] = onion_url.rstrip("/")
    os.environ["DDG_ONION_URL"] = onion_url.rstrip("/")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test main_computer's DuckDuckGo onion web_search route through Tor only."
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="Tor SOCKS proxy. Must be socks5h:// loopback. Default: auto-detect 9050 then 9150.",
    )
    parser.add_argument(
        "--onion-url",
        default=DEFAULT_DDG_ONION_URL,
        help="DuckDuckGo onion base URL.",
    )
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Search query to test.")
    parser.add_argument("--max-results", type=int, default=DEFAULT_MAX_RESULTS)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument(
        "--import",
        dest="import_spec",
        default=None,
        help=(
            "Optional extra callable to test as module.path:function_name. "
            "The built-in main_computer.tor_web_search:web_search is always tested."
        ),
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    try:
        proxy_raw = args.proxy or auto_detect_tor_proxy()
        proxy = parse_tor_proxy(proxy_raw)
        onion_url = args.onion_url.rstrip("/")
        set_fail_closed_env(proxy.raw, onion_url)

        print(f"[config] Tor proxy: {proxy.raw}", flush=True)
        print(f"[config] DuckDuckGo onion URL: {onion_url}", flush=True)
        print("[guard] Installing fail-closed socket/DNS guard.", flush=True)

        with NetworkGuard(allowed_loopback_ports={proxy.port}):
            print("[probe] Testing main_computer.tor_web_search.web_search...", flush=True)
            results = main_computer_web_search(
                args.query,
                max_results=args.max_results,
                proxy=proxy.raw,
                onion_url=onion_url,
                timeout_s=args.timeout,
            )
            print("[pass] main_computer.tor_web_search returned parseable results.", flush=True)
            print(json.dumps({"results_preview": normalize_preview(results)}, indent=2), flush=True)

            if args.import_spec:
                print(f"[probe] Testing extra import: {args.import_spec}", flush=True)
                custom_fn = import_callable(args.import_spec)
                custom_results = call_search_function(
                    custom_fn,
                    query=args.query,
                    max_results=args.max_results,
                    proxy=proxy.raw,
                    onion_url=onion_url,
                    timeout_s=args.timeout,
                )
                if custom_results is None:
                    raise RouteTestFailure(f"{args.import_spec} returned None")
                print(f"[pass] Extra callable ran without direct network breakout: {args.import_spec}", flush=True)
                print(
                    json.dumps({"extra_results_preview": normalize_preview(custom_results)}, indent=2, default=str),
                    flush=True,
                )

        print("[pass] Fail-closed Tor route test completed. No direct fallback was used.", flush=True)
        return 0

    except DirectNetworkBlocked as exc:
        print(f"[fail] Direct network/DNS breakout blocked: {exc}", file=sys.stderr)
        return 2
    except (RouteTestFailure, TorOnlySearchError) as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[fail] Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
