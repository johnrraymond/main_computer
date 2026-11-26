"""Disposable black-box SEH reference client. Executed inside the Python image.

No production identities, configuration, or network endpoints are used.
The real relay entry point is started as a child with a freshly generated key.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import inspect
import os
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

from main_computer.slh.authenticator import generate_identity, sign_object
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


def require(condition, detail):
    if not condition:
        raise RuntimeError(detail)


def http(port, path):
    try:
        response = build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}{path}', timeout=2)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read())


def eventually(fn, *, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if fn():
                return
        except OSError:
            pass
        time.sleep(.05)
    raise RuntimeError('reference readiness deadline exceeded')


@contextmanager
def relay(public_key=None):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    # Only the fresh public key is sent to the actual service process.
    env = {k: v for k, v in os.environ.items() if k in
           {'PATH', 'HOME', 'PYTHONPATH', 'PYTHONUNBUFFERED', 'PYTHONDONTWRITEBYTECODE', 'LANG', 'LC_ALL', 'SYSTEMROOT'}}
    env['PYTHONUNBUFFERED'] = '1'
    if public_key:
        env['SEH_SLH_PUBLIC_KEY_B64'] = base64.b64encode(public_key.encode('ascii')).decode('ascii')
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([sys.executable, '-m', 'main_computer.seh.relay',
                                    '--host', '127.0.0.1', '--port', str(port)],
                                   env=env, stdout=log, stderr=log)
        try:
            eventually(lambda: process.poll() is None and http(port, '/healthz')[0] == 200)
            yield port
        except Exception:
            log.seek(0)
            print(log.read()[-4000:].decode('utf-8', 'replace'), file=sys.stderr)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def ws(port, path):
    options = {'proxy': None} if 'proxy' in inspect.signature(connect).parameters else {}
    return connect(f'ws://127.0.0.1:{port}{path}', open_timeout=4, close_timeout=2, **options)


def closed(connection, code, reason):
    try:
        connection.recv(timeout=5)
    except ConnectionClosed as exc:
        require(exc.rcvd is not None and (exc.rcvd.code, exc.rcvd.reason) == (code, reason),
                f'wrong close: {exc}')
    else:
        raise RuntimeError('expected websocket close')


def run(launch=relay):
    """Run the same protocol contract against any isolated launch context."""
    frames = []
    passed = []

    def record(name, wire):
        require(type(wire) is str, f'{name}: expected text frame')
        payload = json.loads(wire)
        require(type(payload) is dict, f'{name}: expected object')
        frames.append({'name': name, 'wire': wire})
        return payload

    def receive(connection, name):
        return record(name, connection.recv(timeout=5))

    def send(connection, name, value):
        wire = json.dumps(value, sort_keys=True, separators=(',', ':'))
        record(name, wire)
        connection.send(wire)

    with launch() as port:
        status, body = http(port, '/healthz')
        require(status == 200 and body['live'] is True and body['bound'] is False, 'unbound health')
        require(http(port, '/readyz')[0] == 503, 'unbound readiness')
        for path in ('/v1/seh/slh', '/v1/seh/client', '/v1/seh/admin'):
            with ws(port, path) as conn:
                closed(conn, 1008, 'SEH_NOT_BOUND')
        passed.append('unbound_fail_closed')

    key, pub = generate_identity()
    foreign, _ = generate_identity()
    with launch(pub) as port:
        require(http(port, '/readyz')[0] == 503, 'bound initial readiness')
        with ws(port, '/unknown') as conn:
            closed(conn, 1008, 'UNKNOWN_ROUTE')
        with ws(port, '/v1/seh/client') as conn:
            closed(conn, 1013, 'SLH_UNAVAILABLE_OR_BUSY')
        passed.append('bound_route_and_unavailable_refusal')
        with ws(port, '/v1/seh/slh') as conn:
            challenge = receive(conn, 'wrong_signer_challenge')
            require(challenge['type'] == 'seh.auth.challenge', 'wrong signer challenge')
            send(conn, 'wrong_signer_proof', {'type': 'seh.auth.proof',
                                              'signature': sign_object(foreign, challenge['statement'])})
            closed(conn, 1008, 'SLH_IDENTITY_REJECTED')
        passed.append('wrong_signer_refused')
        require(http(port, '/readyz')[0] == 503, 'wrong signer changed readiness')
        with ws(port, '/v1/seh/slh') as tunnel:
            challenge = receive(tunnel, 'valid_challenge')
            require(challenge['type'] == 'seh.auth.challenge', 'valid challenge')
            send(tunnel, 'valid_proof', {'type': 'seh.auth.proof',
                                         'signature': sign_object(key, challenge['statement'])})
            require(receive(tunnel, 'auth_accepted')['type'] == 'seh.auth.accepted', 'auth accepted')
            eventually(lambda: http(port, '/readyz')[0] == 200)
            passed.append('valid_signer_ready')
            with ws(port, '/v1/seh/slh') as duplicate:
                challenge = receive(duplicate, 'duplicate_challenge')
                send(duplicate, 'duplicate_proof', {'type': 'seh.auth.proof',
                    'signature': sign_object(key, challenge['statement'])})
                closed(duplicate, 1008, 'SLH_ALREADY_CONNECTED')
            passed.append('duplicate_tunnel_refused')
            with ws(port, '/v1/seh/client') as client:
                opened = receive(tunnel, 'stream_open')
                require(opened['op'] == 'open' and opened['purpose'] == 'worker', 'stream open')
                sid = opened['id']
                send(tunnel, 'stream_authenticated', {'op': 'authenticated', 'id': sid})
                client.send(b'\x00\x01client\xff')
                data = receive(tunnel, 'client_to_slh_data')
                require(data == {'op': 'data', 'id': sid,
                                 'data': base64.b64encode(b'\x00\x01client\xff').decode('ascii')},
                        'client bytes changed')
                send(tunnel, 'slh_to_client_data', {'op': 'data', 'id': sid,
                     'data': base64.b64encode(b'\x02slh\x00').decode('ascii')})
                require(client.recv(timeout=5) == b'\x02slh\x00', 'SLH bytes changed')
            require(receive(tunnel, 'stream_close') == {'op': 'close', 'id': sid}, 'stream close')
            passed.append('bidirectional_stream_and_close')
        eventually(lambda: http(port, '/readyz')[0] == 503)
        passed.append('disconnect_not_ready')
    return {'cases': passed, 'frames': frames}


if __name__ == '__main__':
    print(json.dumps(run(), sort_keys=True))
