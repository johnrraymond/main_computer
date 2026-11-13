"""R7 relay resource lab, localhost-only; no production identity or financial state.

Real SEH websocket / SLH outbound tunnel / worker adapter round trips are
exercised alongside deterministic shared writer budget and stream isolation.
"""
from __future__ import annotations

import hashlib
import json
import socket
import socketserver
import threading
import time
from pathlib import Path

from main_computer.seh.relay import SEHRelay, MAX_AGGREGATE_PENDING_BYTES, MAX_FRAME
from main_computer.seh.stream_writer import SharedWriteBudget, StreamWriter
from main_computer.slh.authenticator import generate_identity
from main_computer.slh.seh_transport import SLHSEHConnector, SEHLocalClientAdapter
from websockets.sync.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed


def wait_for(fn, timeout=3):
    stop = time.monotonic() + timeout
    while time.monotonic() < stop:
        if fn():
            return True
        time.sleep(.02)
    return bool(fn())


class _Echo(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        try:
            while True:
                data = self.request.recv(65536)
                if not data: return
                self.request.sendall(data)
        except (ConnectionError, OSError):
            pass


class _EchoService(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _recv_exact(sock, count):
    result = bytearray()
    while len(result) < count:
        value = sock.recv(count - len(result))
        if not value:
            raise ConnectionError('relay echo closed before expected response')
        result.extend(value)
    return bytes(result)


def run_relay_lab() -> dict:
    checks=[]
    source = ('main_computer/seh/relay.py','main_computer/seh/stream_writer.py',
              'main_computer/slh/seh_transport.py','main_computer/slh/managed_gateway.py')
    root = Path(__file__).resolve().parents[2]
    def verify(name, fn):
        try:
            value = bool(fn())
            checks.append({'name':name, 'passed':value})
        except Exception as exc:
            checks.append({'name':name, 'passed':False, 'error_class':type(exc).__name__})
    # A deterministic memory-budget campaign: saturated victim cannot consume
    # another stream's reserved bytes or exceed a global aggregate ceiling.
    budget=SharedWriteBudget(24)
    release=threading.Event()
    blocked=threading.Event()
    healthy_done=threading.Event()
    def stall(data):
        blocked.set()
        release.wait(3)
    slow=StreamWriter(stall,lambda:None,shared_budget=budget,max_pending_bytes=64)
    healthy=StreamWriter(lambda data:healthy_done.set(),lambda:None,shared_budget=budget)
    try:
        verify('global_budget_limited_to_configured_bytes',lambda:budget.snapshot()['limit_bytes']==24)
        verify('blocked_stream_writes_one_packet',lambda:slow.offer(b'x'*16) and blocked.wait(1))
        verify('healthy_stream_completes_while_victim_is_blocked',lambda:healthy.offer(b'y'*8) and healthy_done.wait(1))
        verify('victim_exceeds_global_budget_and_fails_closed',lambda:not slow.offer(b'z'*9) and slow.failure_reason=='write_aggregate_budget')
        verify('budget_peak_bounded',lambda:budget.snapshot()['peak_bytes']<=24)
        slow.close()
        verify('closed_stream_releases_queued_but_not_inflight',lambda:budget.snapshot()['used_bytes']==16)
    finally:
        release.set()
        slow.close();healthy.close()
    verify('memory_reservations_released_after_stall_finishes',lambda:wait_for(lambda:budget.snapshot()['used_bytes']==0))
    verify('shared_relay_hard_cap_8mib',lambda:MAX_AGGREGATE_PENDING_BYTES==8*1024*1024)
    # True, independent TCP clients multiplex over the authenticated WebSocket
    # tunnel. Tests use ephemeral loopback ports and transient keys exclusively.
    echo = _EchoService(('127.0.0.1',0),_Echo)
    echo_thread=threading.Thread(target=echo.serve_forever,daemon=True)
    echo_thread.start()
    private,public=generate_identity()
    relay=connector=adapter=None
    healthy_socket=second_socket=None
    try:
        relay=SEHRelay(slh_public_key=public,port=0).start()
        url=f'ws://127.0.0.1:{relay.port}'
        connector=SLHSEHConnector(seh_url=url,identity_private_key=private,
                                 gateway_port=echo.server_address[1],allow_lab=True).connect()
        adapter=SEHLocalClientAdapter(url,allow_lab=True)
        verify('authenticated_control_tunnel_ready',lambda:relay.tunnel_ready)
        healthy_socket=socket.create_connection(adapter.server_address,timeout=3)
        healthy_socket.settimeout(4)
        second_socket=socket.create_connection(adapter.server_address,timeout=3)
        second_socket.settimeout(4)
        verify('two_clients_reach_isolated_streams',lambda:wait_for(lambda:len(relay._clients)==2,timeout=4))
        def roundtrip(sock,payload):
            sock.sendall(payload)
            return _recv_exact(sock,len(payload))==payload
        verify('healthy_worker_roundtrip_before_pressure',lambda:roundtrip(healthy_socket,b'legitimate-worker-ping'))
        verify('second_worker_roundtrip_before_pressure',lambda:roundtrip(second_socket,b'independent-worker-ping'))
        # Closing one stream must not destroy the authenticated SLH tunnel.
        second_socket.close();second_socket=None
        verify('closed_worker_released_without_tunnel_loss',lambda:wait_for(lambda:len(relay._clients)==1,timeout=4) and relay.tunnel_ready)
        verify('surviving_worker_roundtrip_after_peer_departure',lambda:roundtrip(healthy_socket,b'still-working'))
        # A malicious Client websocket is a separate stream, not an SLH tunnel
        # protocol error. The healthy stream must still deliver bytes afterward.
        def oversized_stream_refused():
            path=f'{url}/v1/seh/client'
            with ws_connect(path,open_timeout=4,max_size=MAX_FRAME*2+8192) as attacker:
                attacker.send(b'X'*(MAX_FRAME+1))
                try:
                    attacker.recv(timeout=3)
                except (ConnectionClosed,TimeoutError):
                    return True
            return False
        verify('oversized_worker_stream_refused_without_tunnel_failure',oversized_stream_refused)
        verify('honest_stream_survives_oversized_peer',lambda:relay.tunnel_ready and roundtrip(healthy_socket,b'after-malicious-frame'))
        # A real tunnel loss must fail closed for the old client, while the
        # original signed SLH can reconnect and serve new streams.
        connector.stop()
        connector=None
        verify('relay_detects_authenticated_tunnel_loss',lambda:wait_for(lambda:not relay.tunnel_ready,timeout=4))
        verify('old_clients_released_after_tunnel_loss',lambda:wait_for(lambda:len(relay._clients)==0,timeout=4))
        connector=SLHSEHConnector(seh_url=url,identity_private_key=private,
                                 gateway_port=echo.server_address[1],allow_lab=True).connect()
        verify('signed_control_tunnel_reconnects',lambda:wait_for(lambda:relay.tunnel_ready,timeout=4))
        new_socket=socket.create_connection(adapter.server_address,timeout=3)
        new_socket.settimeout(4)
        try:
            verify('worker_recovers_after_relay_reconnect',lambda:roundtrip(new_socket,b'after-reconnect'))
        finally:
            new_socket.close()
        verify('relay_pending_writer_memory_bounded',lambda:relay._client_write_budget.snapshot()['used_bytes']<=MAX_AGGREGATE_PENDING_BYTES)
        verify('slh_pending_writer_memory_bounded',lambda:connector._gateway_write_budget.snapshot()['used_bytes']<=MAX_AGGREGATE_PENDING_BYTES)
        verify('relay_aggregate_budget_configured',lambda:relay._client_write_budget.snapshot()['limit_bytes']==MAX_AGGREGATE_PENDING_BYTES)
        verify('slh_aggregate_budget_configured',lambda:connector._gateway_write_budget.snapshot()['limit_bytes']==MAX_AGGREGATE_PENDING_BYTES)
    finally:
        for sock in (healthy_socket,second_socket):
            if sock:
                try: sock.close()
                except OSError: pass
        if adapter:adapter.stop()
        if connector:connector.stop()
        if relay:relay.stop()
        echo.shutdown();echo.server_close();echo_thread.join(timeout=3)
    return {'schema':'main-computer.hub-resource-r7.v1','mode':'relay',
            'source_sha256':{path:hashlib.sha256((root/path).read_bytes()).hexdigest() for path in source},
            'tests':checks,'passed':sum(row['passed'] for row in checks),'total':len(checks),
            'result':'PASS' if all(row['passed'] for row in checks) else 'FAIL',
            'scope':'disposable-loopback-no-production','financial_settlement':'NOT_TESTED'}
