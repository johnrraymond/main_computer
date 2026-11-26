"""Independent black-box probe; run in the reference image, not the candidate.
The same checks and observations are made against both processes. No
production config, credentials, or host network are available.
"""
from __future__ import annotations

import http.client as http_client
import inspect
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect


def http(port, path, headers=None):
    conn = http_client.HTTPConnection('127.0.0.1', port, timeout=4)
    try:
        conn.request('GET', path, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read().decode('utf-8')
    finally:
        conn.close()


def ready(port, process=None):
    end = time.monotonic() + 15
    while time.monotonic() < end:
        if process is not None and process.poll() is not None:
            raise RuntimeError('reference service exited before listening')
        try:
            if http(port, '/healthz')[0] == 200:
                return
        except (OSError, http_client.HTTPException):
            pass
        time.sleep(.1)
    raise RuntimeError('listener did not become healthy')


def fragment(port):
    with socket.create_connection(('127.0.0.1', port), timeout=4) as sock:
        sock.sendall(b'GET /hea')
        time.sleep(.05)
        sock.sendall(b'lthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
        response = http_client.HTTPResponse(sock)
        response.begin()
        return response.status, response.read().decode('utf-8')


def close_observation(port, path):
    options = {'proxy': None} if 'proxy' in inspect.signature(connect).parameters else {}
    with connect(f'ws://127.0.0.1:{port}{path}', open_timeout=5, close_timeout=2, **options) as conn:
        try:
            conn.recv(timeout=4)
        except ConnectionClosed as exc:
            if exc.rcvd is None:
                raise RuntimeError(f'no peer close frame on {path}') from exc
            return [exc.rcvd.code, exc.rcvd.reason]
        raise RuntimeError(f'accepted application message on unbound route {path}')


def observe(port):
    rows = {}
    for path in ('/healthz','/readyz'):
        status, body = http(port, path)
        rows[path] = [status, body]
    status, body = fragment(port)
    rows['fragmented_health'] = [status, body]
    rows['unknown_http_status'] = http(port, '/unknown')[0]
    rows['invalid_upgrade_status'] = http(port, '/unknown', {'Upgrade':'websocket','Connection':'Upgrade','Sec-WebSocket-Version':'13','Sec-WebSocket-Key':'bad!'})[0]
    for path in ('/v1/seh/slh','/v1/seh/client','/v1/seh/admin','/unknown'):
        rows['ws:'+path] = close_observation(port, path)
    # A separate connection after the refusals must still be served.
    status, body = http(port, '/healthz')
    rows['health_after_refusals'] = [status, body]
    health = json.loads(rows['/healthz'][1])
    if rows['/healthz'][0] != 200 or health.get('live') is not True or health.get('bound') is not False or health.get('ready') is not False:
        raise RuntimeError('unbound health contract violated')
    if rows['/readyz'][0] != 503 or rows['fragmented_health'] != rows['/healthz'] or rows['health_after_refusals'] != rows['/healthz']:
        raise RuntimeError('readiness or listener continuity contract violated')
    if any(v != [1008,'SEH_NOT_BOUND'] for k,v in rows.items() if k.startswith('ws:')):
        raise RuntimeError('unbound refusal contract violated')
    return rows


@contextmanager
def original(port):
    env = dict(os.environ)
    env.pop('SEH_SLH_PUBLIC_KEY_B64',None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    process = subprocess.Popen([sys.executable,'-m','main_computer.seh.relay','--host','127.0.0.1','--port',str(port)],env=env,stdout=sys.stderr,stderr=sys.stderr)
    try:
        ready(port,process)
        yield
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=4)


def main():
    mode, port = sys.argv[1], int(sys.argv[2])
    if mode == 'reference':
        with original(port):
            result = observe(port)
    elif mode == 'candidate':
        ready(port)
        result = observe(port)
    else:
        raise ValueError('unknown probe mode')
    print(json.dumps(result,sort_keys=True))


if __name__ == '__main__':
    main()
