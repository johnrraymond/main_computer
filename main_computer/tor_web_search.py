from __future__ import annotations

from dataclasses import asdict, dataclass
import html
import ipaddress
import os
import re
import socket
import ssl
from typing import Mapping
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse


DEFAULT_DDG_ONION_URL = "https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"
DEFAULT_TIMEOUT_S = 45.0
DEFAULT_MAX_RESULTS = 5

_PROXY_ENV_NAMES = (
    "TOR_PROXY",
    "MAIN_COMPUTER_TOR_PROXY",
)

_ONION_URL_ENV_NAMES = (
    "MAIN_COMPUTER_DDG_ONION_URL",
    "MAIN_COMPUTER_DDG_BASE_URL",
    "DUCKDUCKGO_ONION_URL",
    "DUCKDUCKGO_BASE_URL",
    "DDG_ONION_URL",
    "DDG_BASE_URL",
)


class TorOnlySearchError(RuntimeError):
    """Raised when Tor-only search cannot complete without leaving Tor."""


@dataclass(frozen=True)
class TorProxy:
    """Validated local Tor SOCKS proxy settings."""

    raw: str
    host: str
    port: int


@dataclass(frozen=True)
class SearchResult:
    """A DuckDuckGo HTML search result."""

    title: str
    url: str
    content: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False

    clean = str(host).strip("[]").lower()
    if clean in {"localhost", "ip6-localhost"}:
        return True

    try:
        return ipaddress.ip_address(clean).is_loopback
    except ValueError:
        return False


def parse_tor_proxy(proxy: str) -> TorProxy:
    """Validate a Tor SOCKS proxy URL.

    Only ``socks5h://`` loopback proxies are accepted. The ``h`` matters:
    hostnames, including ``.onion`` names, stay inside Tor instead of being
    resolved by the local operating system.
    """

    parsed = urlparse(str(proxy or "").strip())

    if parsed.scheme != "socks5h":
        raise TorOnlySearchError(
            "Unsafe Tor proxy rejected. Use socks5h://127.0.0.1:9050 "
            "or socks5h://127.0.0.1:9150."
        )

    if not parsed.hostname or not _is_loopback_host(parsed.hostname):
        raise TorOnlySearchError("Unsafe Tor proxy rejected. Proxy host must be loopback.")

    if parsed.port is None:
        raise TorOnlySearchError("Unsafe Tor proxy rejected. Proxy port is required.")

    return TorProxy(raw=parsed.geturl(), host=parsed.hostname, port=int(parsed.port))


def resolve_tor_proxy(proxy: str | None = None, env: Mapping[str, str] | None = None) -> TorProxy:
    """Resolve and validate the configured Tor proxy.

    Search intentionally fails closed when no explicit proxy is configured.
    """

    values = env if env is not None else os.environ
    raw = str(proxy or "").strip()
    if not raw:
        for name in _PROXY_ENV_NAMES:
            raw = str(values.get(name, "")).strip()
            if raw:
                break

    if not raw:
        raise TorOnlySearchError(
            "Tor proxy is not configured. Set TOR_PROXY=socks5h://127.0.0.1:9050 "
            "or TOR_PROXY=socks5h://127.0.0.1:9150."
        )

    return parse_tor_proxy(raw)


def resolve_duckduckgo_onion_url(onion_url: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """Resolve and validate the DuckDuckGo onion base URL."""

    values = env if env is not None else os.environ
    raw = str(onion_url or "").strip()
    if not raw:
        for name in _ONION_URL_ENV_NAMES:
            raw = str(values.get(name, "")).strip()
            if raw:
                break

    if not raw:
        raw = DEFAULT_DDG_ONION_URL

    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise TorOnlySearchError("DuckDuckGo onion URL must use https.")

    host = parsed.hostname or ""
    if not host.endswith(".onion"):
        raise TorOnlySearchError("DuckDuckGo search URL must be an .onion host for Tor-only mode.")

    if parsed.username or parsed.password:
        raise TorOnlySearchError("DuckDuckGo onion URL must not include credentials.")

    base = f"https://{host}"
    if parsed.port:
        base += f":{parsed.port}"

    prefix = parsed.path.rstrip("/")
    if prefix and prefix != "/":
        base += prefix

    return base


def _read_exact(sock_file, size: int) -> bytes:
    data = sock_file.read(size)
    if len(data) != size:
        raise TorOnlySearchError("Unexpected end of stream while reading from Tor connection.")
    return data


def _open_socks5h_tcp_stream(proxy: TorProxy, host: str, port: int, timeout_s: float) -> socket.socket:
    """Open a TCP stream through Tor using SOCKS5 remote hostname mode."""

    if not host.endswith(".onion"):
        raise TorOnlySearchError("Refusing to connect to a non-onion host in Tor-only search.")

    host_bytes = host.encode("idna")
    if len(host_bytes) > 255:
        raise TorOnlySearchError("Target onion hostname is too long for SOCKS5.")

    try:
        sock = socket.create_connection((proxy.host, proxy.port), timeout=timeout_s)
    except OSError as exc:
        raise TorOnlySearchError(f"Could not connect to local Tor SOCKS proxy at {proxy.host}:{proxy.port}.") from exc

    sock.settimeout(timeout_s)

    try:
        sock.sendall(b"\x05\x01\x00")
        greeting = _read_exact(sock.makefile("rb", buffering=0), 2)
        if greeting != b"\x05\x00":
            raise TorOnlySearchError("Tor SOCKS proxy did not accept no-auth SOCKS5.")

        request = (
            b"\x05\x01\x00\x03"
            + bytes([len(host_bytes)])
            + host_bytes
            + int(port).to_bytes(2, "big")
        )
        sock.sendall(request)

        sock_file = sock.makefile("rb", buffering=0)
        header = _read_exact(sock_file, 4)
        if header[0] != 0x05:
            raise TorOnlySearchError("Invalid SOCKS5 response from Tor proxy.")
        if header[1] != 0x00:
            raise TorOnlySearchError(f"Tor SOCKS CONNECT failed with code 0x{header[1]:02x}.")
        if header[2] != 0x00:
            raise TorOnlySearchError("Invalid reserved byte in SOCKS5 response.")

        atyp = header[3]
        if atyp == 0x01:
            _read_exact(sock_file, 4)
        elif atyp == 0x03:
            length = _read_exact(sock_file, 1)[0]
            _read_exact(sock_file, length)
        elif atyp == 0x04:
            _read_exact(sock_file, 16)
        else:
            raise TorOnlySearchError("Invalid address type in SOCKS5 response.")

        _read_exact(sock_file, 2)
        return sock
    except Exception:
        sock.close()
        raise


def _read_http_response(tls_sock: ssl.SSLSocket) -> tuple[int, dict[str, str], bytes]:
    stream = tls_sock.makefile("rb")
    status_line = stream.readline(65_536)
    if not status_line:
        raise TorOnlySearchError("No HTTP response received from DuckDuckGo onion endpoint.")

    try:
        parts = status_line.decode("iso-8859-1").strip().split(" ", 2)
        status = int(parts[1])
    except Exception as exc:
        raise TorOnlySearchError(f"Malformed HTTP status line: {status_line!r}") from exc

    headers: dict[str, str] = {}
    while True:
        line = stream.readline(65_536)
        if line in (b"\r\n", b"\n", b""):
            break

        text = line.decode("iso-8859-1").rstrip("\r\n")
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    if headers.get("transfer-encoding", "").lower() == "chunked":
        body_parts: list[bytes] = []
        while True:
            size_line = stream.readline(65_536)
            if not size_line:
                raise TorOnlySearchError("Unexpected end of chunked HTTP response.")
            size_text = size_line.split(b";", 1)[0].strip()
            try:
                size = int(size_text, 16)
            except ValueError as exc:
                raise TorOnlySearchError(f"Malformed HTTP chunk size: {size_line!r}") from exc

            if size == 0:
                # Consume trailer lines.
                while True:
                    trailer = stream.readline(65_536)
                    if trailer in (b"\r\n", b"\n", b""):
                        break
                break

            body_parts.append(_read_exact(stream, size))
            _read_exact(stream, 2)

        return status, headers, b"".join(body_parts)

    if "content-length" in headers:
        try:
            length = int(headers["content-length"])
        except ValueError as exc:
            raise TorOnlySearchError("Malformed content-length header.") from exc
        return status, headers, _read_exact(stream, length)

    return status, headers, stream.read()


def _http_get_through_tor(
    *,
    proxy: TorProxy,
    url: str,
    timeout_s: float,
    user_agent: str = "main_computer_tor_web_search/1.0",
) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not host.endswith(".onion"):
        raise TorOnlySearchError("Refusing to fetch a non-HTTPS onion URL in Tor-only search.")

    port = parsed.port or 443
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    raw_sock = _open_socks5h_tcp_stream(proxy, host, port, timeout_s)
    try:
        context = ssl.create_default_context()
        with context.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
            tls_sock.settimeout(timeout_s)
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"User-Agent: {user_agent}\r\n"
                "Accept: text/html,application/xhtml+xml\r\n"
                "Accept-Encoding: identity\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            tls_sock.sendall(request)

            status, headers, body = _read_http_response(tls_sock)
            if status != 200:
                raise TorOnlySearchError(f"DuckDuckGo onion endpoint returned HTTP {status}.")

            content_type = headers.get("content-type", "")
            match = re.search(r"charset=([A-Za-z0-9_.-]+)", content_type)
            charset = match.group(1) if match else "utf-8"
            return body.decode(charset, errors="replace")
    finally:
        raw_sock.close()


def _strip_tags(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", value)
    return html.unescape(without_tags).strip()


def _normalize_duckduckgo_href(href: str, onion_url: str) -> str:
    normalized = html.unescape(href)

    if normalized.startswith("//"):
        normalized = "https:" + normalized
    elif normalized.startswith("/"):
        normalized = urljoin(onion_url.rstrip("/") + "/", normalized)

    parsed = urlparse(normalized)
    query = parse_qs(parsed.query)
    if query.get("uddg"):
        return unquote(query["uddg"][0])

    return normalized


def parse_duckduckgo_html_results(body: str, onion_url: str, max_results: int = DEFAULT_MAX_RESULTS) -> list[SearchResult]:
    """Parse title/link/snippet search results from DuckDuckGo's HTML endpoint."""

    if max_results < 1:
        return []

    results: list[SearchResult] = []
    seen: set[str] = set()

    link_re = re.compile(
        r'<a\b(?=[^>]*\bclass=["\'][^"\']*result__a[^"\']*["\'])'
        r'(?=[^>]*\bhref=["\']([^"\']+)["\'])[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    for match in link_re.finditer(body):
        href, title_html = match.groups()
        title = _strip_tags(title_html)
        url = _normalize_duckduckgo_href(href, onion_url)

        if not title or not url or url in seen:
            continue

        nearby = body[match.end() : match.end() + 2500]
        snippet_match = re.search(
            r'<a\b(?=[^>]*\bclass=["\'][^"\']*result__snippet[^"\']*["\'])[^>]*>(.*?)</a>',
            nearby,
            re.IGNORECASE | re.DOTALL,
        )
        if not snippet_match:
            snippet_match = re.search(
                r'<div\b(?=[^>]*\bclass=["\'][^"\']*result__snippet[^"\']*["\'])[^>]*>(.*?)</div>',
                nearby,
                re.IGNORECASE | re.DOTALL,
            )

        content = _strip_tags(snippet_match.group(1)) if snippet_match else ""
        seen.add(url)
        results.append(SearchResult(title=title, url=url, content=content))

        if len(results) >= max_results:
            break

    return results


def web_search(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    *,
    proxy: str | None = None,
    onion_url: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[dict[str, str]]:
    """Search DuckDuckGo's onion HTML endpoint through Tor only.

    This function has no direct-web fallback. If the Tor proxy is missing,
    blocked, rate-limited, or otherwise unavailable, it raises
    :class:`TorOnlySearchError`.
    """

    clean_query = str(query or "").strip()
    if not clean_query:
        return []

    if max_results < 1:
        return []

    tor_proxy = resolve_tor_proxy(proxy)
    base_url = resolve_duckduckgo_onion_url(onion_url)
    search_url = f"{base_url.rstrip('/')}/html/?q={quote_plus(clean_query)}"

    body = _http_get_through_tor(proxy=tor_proxy, url=search_url, timeout_s=float(timeout_s))
    results = parse_duckduckgo_html_results(body, base_url, max_results=max_results)

    if not results:
        sample = _strip_tags(body[:1000]).replace("\n", " ")[:500]
        raise TorOnlySearchError(
            "DuckDuckGo onion route returned no parseable results. "
            "This may be rate limiting, a challenge page, or an HTML layout change. "
            f"Response sample: {sample!r}"
        )

    return [result.as_dict() for result in results]
