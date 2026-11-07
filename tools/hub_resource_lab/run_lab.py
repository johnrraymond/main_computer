"""Hub Resource Lab: R0-R4 socket-boundary and bounded admission probes.

Only binds to 127.0.0.1 and uses ephemeral ports and disposable state.
R5 mixed paid-work, R6 failover, and R7 SLH/SEH remain separate gates.

Examples:
    python -m tools.hub_resource_lab.run_lab baseline --output /tmp/baseline.json
    python -m tools.hub_resource_lab.run_lab boundaries --output /tmp/patched.json
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from typing import Any

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer
from main_computer.hub_resource_limits import DEFAULT_RESOURCE_POLICY, load_hub_resource_policy
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
from main_computer.stable_hub_topology import load_stable_hub_topology

ROOT = Path(__file__).resolve().parents[2]
WS_ENDPOINT = '/api/hub/v1/workers/live-session'
HTTP_ENDPOINT = '/api/hub/v1/credits/multisession-keys/validate'


def recv_status(sock: socket.socket, *, timeout: float = 1.5) -> tuple[int | None, bytes]:
    sock.settimeout(timeout)
    data = b''
    try:
        while b'\r\n\r\n' not in data and len(data) < 2048:
            chunk = sock.recv(2048)
            if not chunk: break
            data += chunk
    except (socket.timeout, OSError):
        return None, data
    try:
        return int(data.split(b'\r\n', 1)[0].split()[1]), data
    except (IndexError, ValueError):
        return None, data


def raw_http(port: int, headers: list[str], *, body: bytes = b'', timeout: float = 1.5,
             path: str = HTTP_ENDPOINT) -> int | None:
    request = (f'POST {path} HTTP/1.1\r\nHost: localhost\r\n' +
               '\r\n'.join(headers) + '\r\n\r\n').encode() + body
    with socket.create_connection(('127.0.0.1', port), timeout=2.0) as sock:
        sock.sendall(request)
        status, _ = recv_status(sock, timeout=timeout)
        return status


def ws_open(port: int) -> socket.socket:
    sock = socket.create_connection(('127.0.0.1', port), timeout=2.0)
    key = base64.b64encode(os.urandom(16)).decode('ascii')
    payload = (f'GET {WS_ENDPOINT} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
               f'Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Key: {key}\r\n'
               'Sec-WebSocket-Version: 13\r\n\r\n')
    sock.sendall(payload.encode('ascii'))
    code, raw = recv_status(sock, timeout=2.0)
    if code != 101:
        sock.close()
        raise RuntimeError(f'websocket upgrade failed: {code} {raw[:120]!r}')
    return sock


def send_ws(sock: socket.socket, payload: bytes, *, opcode: int = 1, fin: bool = True, masked: bool = True,
            declared_length: int | None = None, send_payload: bool = True) -> None:
    length = len(payload) if declared_length is None else declared_length
    start = (0x80 if fin else 0) | opcode
    frame = bytearray([start])
    flag = 0x80 if masked else 0
    if length < 126:
        frame.append(flag | length)
    elif length <= 65535:
        frame.append(flag | 126)
        frame.extend(length.to_bytes(2, 'big'))
    else:
        frame.append(flag | 127)
        frame.extend(length.to_bytes(8, 'big'))
    if masked:
        mask = b'\x11\x22\x33\x44'
        frame.extend(mask)
        if send_payload:
            frame.extend(bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))
    elif send_payload:
        frame.extend(payload)
    sock.sendall(frame)


def recv_ws(sock: socket.socket, *, timeout: float = 1.5) -> tuple[int | None, int | None]:
    sock.settimeout(timeout)
    try:
        header = sock.recv(2)
        if len(header) != 2:
            return None, None
        opcode = header[0] & 15
        size = header[1] & 0x7f
        if size == 126:
            size = int.from_bytes(_read_n(sock, 2), 'big')
        if size == 127:
            size = int.from_bytes(_read_n(sock, 8), 'big')
        if size > 1024:
            return opcode, None
        payload = _read_n(sock, size)
        return opcode, int.from_bytes(payload[:2], 'big') if opcode == 8 and len(payload) >= 2 else None
    except (socket.timeout, ConnectionError, OSError):
        return None, None


def recv_ws_json(sock: socket.socket, *, timeout: float = 1.5) -> dict[str, Any] | None:
    sock.settimeout(timeout)
    try:
        header = _read_n(sock, 2)
        length = header[1] & 127
        if length == 126: length = int.from_bytes(_read_n(sock, 2), 'big')
        elif length == 127: length = int.from_bytes(_read_n(sock, 8), 'big')
        if header[0] & 15 != 1 or length > 2048: return None
        return json.loads(_read_n(sock, length).decode('utf-8'))
    except (socket.timeout, ConnectionError, OSError, ValueError):
        return None


def _read_n(sock: socket.socket, n: int) -> bytes:
    result = b''
    while len(result) < n:
        chunk = sock.recv(n - len(result))
        if not chunk: raise ConnectionError('closed frame')
        result += chunk
    return result


def _server(kind: str, temp: Path):
    if kind == 'legacy':
        cfg = MainComputerConfig(workspace=temp, model='fake-model', hub_root=temp / 'hub-state',
                                 hub_bridge_backend='mock-chain', hub_require_multisession_auth=False)
        return HubHttpServer(('127.0.0.1', 0), cfg, verbose=False)
    topology = load_stable_hub_topology(ROOT / 'deploy/hub-topology/dev-topology.json')
    return create_stable_hub_server(topology=topology, hub_id='dev-hub1', bind_host='127.0.0.1',
                                    bind_port=0, multisession_key_store=InMemoryStableMultiSessionKeyStore(),
                                    worker_session_store=InMemoryStableWorkerSessionStore())


def _one(name: str, fn, expected) -> dict[str, Any]:
    try:
        observed = fn()
        return {'name': name, 'expected': expected, 'observed': observed, 'passed': observed == expected}
    except Exception as exc:
        return {'name': name, 'expected': expected, 'observed': type(exc).__name__,
                'error': str(exc)[:140], 'passed': False}


def lab_run(*, mode: str = 'boundaries') -> dict[str, Any]:
    if mode not in {'baseline', 'boundaries'}:
        raise ValueError('mode must be baseline or boundaries')
    before = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
              for p in ('main_computer/hub.py', 'main_computer/stable_hub.py')}
    # Smaller test ceilings prove the same rejection mechanism safely without
    # spending 2 MiB per probe. Defaults are separately asserted by tests.
    defaults = {
        'MAIN_COMPUTER_HUB_HTTP_BODY_MAX_BYTES': '256',
        'MAIN_COMPUTER_HUB_HTTP_HEADER_MAX_BYTES': '4096',
        'MAIN_COMPUTER_HUB_HTTP_BODY_DEADLINE_SECONDS': '1',
        'MAIN_COMPUTER_HUB_WS_FRAME_MAX_BYTES': '128',
        'MAIN_COMPUTER_HUB_WS_MESSAGE_MAX_BYTES': '256',
        'MAIN_COMPUTER_HUB_WS_PREAUTH_DEADLINE_SECONDS': '1',
    }
    old = {name: os.environ.get(name) for name in defaults}
    os.environ.update(defaults)
    try:
        policy = load_hub_resource_policy()
        entries: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix='main-computer-hub-resource-lab-') as directory:
            for kind in ('legacy', 'stable'):
                server = _server(kind, Path(directory) / kind)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                port = server.server_port
                try:
                    def add(label, fn, expected):
                        entries.append({'hub': kind, **_one(label, fn, expected)})
                    add('oversize_content_length_rejected_without_body',
                        lambda: raw_http(port, ['Content-Length: 257']), 413)
                    add('duplicate_content_length_rejected',
                        lambda: raw_http(port, ['Content-Length: 2', 'Content-Length: 2']), 400)
                    add('unsupported_transfer_encoding_rejected',
                        lambda: raw_http(port, ['Transfer-Encoding: chunked']), 400)
                    add('negative_content_length_rejected',
                        lambda: raw_http(port, ['Content-Length: -1']), 400)
                    add('headers_above_global_limit_rejected',
                        lambda: raw_http(port, ['Content-Length: 2'] +
                                         [f'X-Extra-{i}: ' + 'X' * 400 for i in range(12)], body=b'{}'), 431)
                    add('incomplete_body_deadline',
                        lambda: raw_http(port, ['Content-Length: 20'], body=b'{}', timeout=2), 408)
                    add('ordinary_json_reaches_application',
                        lambda: raw_http(port, ['Content-Length: 2'], body=b'{}') is not None, True)
                    if kind == 'stable':
                        def check_ws(send, expected_code):
                            with ws_open(port) as sock:
                                send(sock)
                                return recv_ws(sock, timeout=1.5)
                        add('ws_oversize_advertised_frame_length',
                            lambda: check_ws(lambda sock: send_ws(sock, b'', declared_length=129, send_payload=False), 1009),
                            (8, 1009))
                        add('ws_unmasked_frame',
                            lambda: check_ws(lambda sock: send_ws(sock, b'{}', masked=False), 1002), (8, 1002))
                        add('ws_partial_frame_read_deadline',
                            lambda: check_ws(lambda sock: sock.sendall(b'\x81'), 1002), (8, 1002))
                        add('ws_large_64bit_length',
                            lambda: check_ws(lambda sock: send_ws(sock, b'', declared_length=2**32, send_payload=False), 1009),
                            (8, 1009))
                        add('ws_oversize_fragmented_message',
                            lambda: check_ws(lambda sock: (
                                send_ws(sock, b'x' * 128, fin=False),
                                send_ws(sock, b'y' * 128, opcode=0, fin=False),
                                send_ws(sock, b'z', opcode=0)), 1009), (8, 1009))
                        def verify_fragmented():
                            with ws_open(port) as sock:
                                send_ws(sock, b'{"type":', fin=False)
                                send_ws(sock, b'"bogus"}', opcode=0)
                                response = recv_ws_json(sock)
                                return (response or {}).get('error', '').startswith(
                                    'first worker live-session message must be worker.auth')
                        add('ws_fragmented_valid_text_parses', verify_fragmented, True)
                finally:
                    server.shutdown(); server.server_close(); thread.join(timeout=3)
        result = {'mode': mode, 'source_sha256': before, 'policy_sha256': policy.fingerprint(),
                  'policy': vars(policy), 'tests': entries,
                  'passed': sum(t['passed'] for t in entries), 'total': len(entries),
                  'result': 'PASS' if all(t['passed'] for t in entries) else ('FAIL_BASELINE' if mode == 'baseline' else 'FAIL')}
        return result
    finally:
        for name, value in old.items():
            if value is None: os.environ.pop(name, None)
            else: os.environ[name] = value



def _wait_until(predicate, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def _header_hold(port: int) -> socket.socket:
    sock = socket.create_connection(('127.0.0.1', port), timeout=1)
    sock.sendall(b'GET /status HTTP/1.1\r\nHost: localhost\r\nX-Slow:')
    return sock


def _body_hold(port: int) -> socket.socket:
    sock = socket.create_connection(('127.0.0.1', port), timeout=1)
    sock.sendall((f'POST {HTTP_ENDPOINT} HTTP/1.1\r\nHost: localhost\r\n'
                  'Content-Length: 100\r\n\r\n').encode())
    return sock


def _ws_upgrade_status(port: int) -> int | None:
    key = base64.b64encode(os.urandom(16)).decode('ascii')
    request = (f'GET {WS_ENDPOINT} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
               f'Upgrade: websocket\r\nConnection: Upgrade\r\n'
               f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode()
    with socket.create_connection(('127.0.0.1', port), timeout=1) as sock:
        sock.sendall(request)
        return recv_status(sock, timeout=1)[0]


def admission_lab_run() -> dict[str, Any]:
    """R3/R4: bounded header workers, total threads, WS pending auth and recovery."""
    policies = {
        'MAIN_COMPUTER_HUB_MAX_CONNECTIONS': '4',
        'MAIN_COMPUTER_HUB_MAX_HEADER_CONNECTIONS': '2',
        'MAIN_COMPUTER_HUB_MAX_CONNECTIONS_PER_PEER': '4',
        'MAIN_COMPUTER_HUB_MAX_WEBSOCKET_CONNECTIONS': '2',
        'MAIN_COMPUTER_HUB_MAX_UNAUTH_WEBSOCKET_CONNECTIONS': '1',
        'MAIN_COMPUTER_HUB_HTTP_HEADER_DEADLINE_SECONDS': '2',
        'MAIN_COMPUTER_HUB_HTTP_BODY_DEADLINE_SECONDS': '8',
        'MAIN_COMPUTER_HUB_WS_PREAUTH_DEADLINE_SECONDS': '3',
    }
    original = {name: os.environ.get(name) for name in policies}
    os.environ.update(policies)
    entries = []
    try:
        with tempfile.TemporaryDirectory(prefix='hub-resource-admission-') as directory:
            for kind in ('legacy', 'stable'):
                server = _server(kind, Path(directory) / kind)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                port = server.server_port
                def snapshot():
                    # Baseline has no admission policy. Treat missing coverage as FAIL.
                    return server.resource_admission_snapshot()
                def check(label, fn, expected):
                    entries.append({'hub': kind, **_one(label, fn, expected)})
                try:
                    with _header_hold(port) as first, _header_hold(port) as second:
                        check('slow_header_slots_admitted',
                              lambda: _wait_until(lambda: snapshot()['header_connections'] == 2), True)
                        check('third_header_refused_before_thread',
                              lambda: raw_http(port, ['Content-Length: 2'], body=b'{}'), 503)
                        check('peak_connections_does_not_exceed_limit',
                              lambda: snapshot()['peak_connections'] <= 4, True)
                        check('absolute_header_deadline_expires',
                              lambda: _wait_until(lambda: snapshot()['expired_headers'] >= 2, timeout=4.0), True)
                    check('valid_http_recovers_after_header_deadline',
                          lambda: raw_http(port, ['Content-Length: 2'], body=b'{}') is not None, True)

                    with _body_hold(port) as body_one, _body_hold(port) as body_two:
                        check('partial_body_connections_enter_bounded_pool',
                              lambda: _wait_until(lambda: snapshot()['connections'] == 2, timeout=3.0), True)
                        with _header_hold(port) as header_one, _header_hold(port) as header_two:
                            check('total_capacity_confirmed_before_overload',
                                  lambda: _wait_until(lambda: snapshot()['connections'] == 4
                                      and snapshot()['header_connections'] == 2, timeout=1.0), True)
                            check('total_connection_admission_bounded',
                                  lambda: (raw_http(port, ['Content-Length: 2'], body=b'{}'),
                                           snapshot()['connections'] <= 4), (503, True))
                        check('body_disconnect_reclaims_connections',
                              lambda: _wait_until(lambda: snapshot()['connections'] <= 2, timeout=2.0), True)
                    check('all_connection_slots_eventually_released',
                          lambda: _wait_until(lambda: snapshot()['connections'] == 0, timeout=4.0), True)

                    if kind == 'stable':
                        with ws_open(port) as worker_socket:
                            check('pending_websocket_upgrade_counted',
                                  lambda: _wait_until(lambda: snapshot()['unauth_websocket_connections'] == 1), True)
                            check('excess_unauth_ws_upgrade_rejected',
                                  lambda: _ws_upgrade_status(port), 503)
                            check('ordinary_http_remains_available_beside_pending_ws',
                                  lambda: raw_http(port, ['Content-Length: 2'], body=b'{}') is not None, True)
                        check('pending_websocket_slot_released',
                              lambda: _wait_until(lambda: snapshot()['unauth_websocket_connections'] == 0), True)
                        check('websocket_upgrade_recovers', lambda: _ws_upgrade_status(port), 101)
                finally:
                    server.shutdown();server.server_close();thread.join(timeout=3)
        result = {'mode':'admission', 'policy_sha256':load_hub_resource_policy().fingerprint(),
                  'tests':entries,'passed':sum(t['passed'] for t in entries),'total':len(entries)}
        result['result'] = 'PASS' if result['passed'] == result['total'] else 'FAIL'
        return result
    finally:
        for name, value in original.items():
            if value is None: os.environ.pop(name, None)
            else: os.environ[name] = value

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Loopback-only Hub Resource Lab R0-R7')
    parser.add_argument('mode', choices=('baseline', 'boundaries', 'admission', 'relay'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.mode == 'relay':
        from .relay_lab import run_relay_lab
        result = run_relay_lab()
    else:
        result = admission_lab_run() if args.mode == 'admission' else lab_run(mode=args.mode)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    for t in result['tests']:
        label = f"{'PASS' if t['passed'] else 'FAIL'} {t.get('hub','relay')} {t['name']}"
        print(label if args.mode == 'relay' else f"{label}: {t['observed']!r} expected={t['expected']!r}")
    print(f"HUB_RESOURCE_LAB_{result['result']} {result['passed']}/{result['total']}")
    return 0 if (result['result'] == 'PASS' or (args.mode == 'baseline' and result['result'] == 'FAIL_BASELINE')) else 1


if __name__ == '__main__':
    raise SystemExit(main())
