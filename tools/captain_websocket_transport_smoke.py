"""Prove that a WebSocket 101 upgrade actually transports frames to/from the Hub.

Unlike a handshake-only test, this sends a correctly masked INVALID first
worker message and requires the Stable Hub to answer with hub.error.  It needs
no wallet, FoundationDB writes, Ollama model, or paid work session.

Examples (from repository root):
  python -m tools.captain_websocket_transport_smoke --all-hubs
  python -m tools.captain_websocket_transport_smoke --url http://127.0.0.1:8790
  python tools/captain_websocket_transport_smoke.py --all-hubs
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in (None, ""):
    root = str(Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)

HUB_URLS = (
    "https://mainnet-hub.greatlibrary.io",
    "https://mainneta-hub1.greatlibrary.io",
    "https://mainnetc-hub1.greatlibrary.io",
)
ENDPOINT = "/api/hub/v1/workers/live-session"
INVALID_MESSAGE = {"type": "diagnostic.invalid"}
EXPECTED_ERROR = "first worker live-session message must be worker.auth"


def _read_exact(sock: socket.socket, size: int) -> bytes:
    result = bytearray()
    while len(result) < size:
        chunk = sock.recv(size - len(result))
        if not chunk:
            raise ConnectionError(f"socket closed while reading {size} bytes (received {len(result)})")
        result.extend(chunk)
    return bytes(result)


def _read_upgrade(sock: socket.socket) -> tuple[str, dict[str, str]]:
    # Consume only headers so an adjacent websocket frame is never lost.
    header = bytearray()
    while not header.endswith(b"\r\n\r\n"):
        c = _read_exact(sock, 1)
        header.extend(c)
        if len(header) > 65536:
            raise ValueError("HTTP upgrade response exceeded 65536 bytes")
    lines = bytes(header).split(b"\r\n")
    status = lines[0].decode("latin1")
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            name, value = line.split(b":", 1)
            headers[name.strip().decode("ascii", errors="ignore").lower()] = value.strip().decode("latin1")
    return status, headers


def _send_masked_text(sock: socket.socket, message: dict) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    header = bytearray([0x81])
    if len(payload) < 126:
        header.append(0x80 | len(payload))
    elif len(payload) < 65536:
        header.append(0x80 | 126)
        header.extend(len(payload).to_bytes(2, "big"))
    else:
        header.append(0x80 | 127)
        header.extend(len(payload).to_bytes(8, "big"))
    mask = os.urandom(4)
    sock.sendall(header + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))


def _read_frame(sock: socket.socket) -> tuple[int, bytes]:
    first, second = _read_exact(sock, 2)
    opcode = first & 15
    length = second & 127
    if length == 126:
        length = int.from_bytes(_read_exact(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(_read_exact(sock, 8), "big")
    # Server-to-client frames must not be masked (RFC 6455).
    if second & 128:
        raise ValueError("server incorrectly masked its websocket frame")
    if length > 1024 * 1024:
        raise ValueError("unexpected oversized websocket reply")
    return opcode, _read_exact(sock, length)


def probe(url: str, timeout_s: float = 8.0) -> bool:
    start = time.monotonic()

    def emit(*parts: object) -> None:
        print(f"  [{time.monotonic() - start:6.2f}s]", *parts, flush=True)

    print("TARGET", url, flush=True)
    sock = None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("target must be an http(s) base URL")
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        sock = socket.create_connection((host, port), timeout=timeout_s)
        if parsed.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        sock.settimeout(timeout_s)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = (parsed.path.rstrip("/") or "") + ENDPOINT
        host_header = host + (f":{port}" if parsed.port is not None else "")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        status, headers = _read_upgrade(sock)
        emit("HANDSHAKE", status)
        if not status.startswith("HTTP/1.1 101 "):
            raise RuntimeError("FAIL_UPGRADE_PROTOCOL: expected HTTP/1.1 101")
        expected = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest()).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise RuntimeError("FAIL_UPGRADE_ACCEPT: invalid Sec-WebSocket-Accept")
        if headers.get("upgrade", "").lower() != "websocket" or "upgrade" not in {
            x.strip().lower() for x in headers.get("connection", "").split(",")
        }:
            raise RuntimeError("FAIL_UPGRADE_HEADERS: missing Upgrade / Connection")

        _send_masked_text(sock, INVALID_MESSAGE)
        emit("INVALID_FIRST_FRAME_SENT")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                opcode, body = _read_frame(sock)
            except socket.timeout:
                raise RuntimeError("FAIL_NO_FRAME: HTTP 101 succeeded but no WebSocket frame arrived") from None
            if opcode == 0x9:
                # Reply to a server ping, if the server sends one.
                data = body
                mask = os.urandom(4)
                sock.sendall(bytes([0x8A, 0x80 | len(data)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
                continue
            if opcode != 0x1:
                raise RuntimeError(f"FAIL_UNEXPECTED_OPCODE: {opcode}")
            reply = json.loads(body.decode("utf-8"))
            emit("FRAME", reply.get("type"), "error=", reply.get("error"))
            if reply.get("type") != "hub.error" or reply.get("error") != EXPECTED_ERROR:
                raise RuntimeError("FAIL_UNEXPECTED_REPLY: expected invalid-first-message hub.error")
            emit("PASS: upgrade and bidirectional WebSocket frames verified")
            return True
        raise RuntimeError("FAIL_NO_FRAME: frame deadline expired")
    except Exception as exc:
        emit("FAIL", type(exc).__name__, str(exc))
        return False
    finally:
        if sock is not None:
            sock.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test 101 response AND returned WebSocket frame on Stable Hubs")
    parser.add_argument("--url", action="append", help="Hub base URL, repeatable")
    parser.add_argument("--all-hubs", action="store_true", help="Run public ingress plus direct Hub A and C")
    parser.add_argument("--timeout-s", type=float, default=8.0)
    args = parser.parse_args(argv)
    if args.timeout_s <= 0:
        parser.error("--timeout-s must be positive")
    if args.url and args.all_hubs:
        parser.error("select --url or --all-hubs")
    urls = HUB_URLS if args.all_hubs else (args.url or HUB_URLS[:1])
    results = [probe(url.rstrip("/"), timeout_s=args.timeout_s) for url in urls]
    print("SUMMARY", sum(results), "/", len(results), "PASS", flush=True)
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
