"""Experimental SSL socket I/O serialization and forensic logging regressions."""
from __future__ import annotations
import threading
import time

from main_computer.viewport_routes_energy import _WorkerHubLiveSessionClient
from main_computer.seh.relay import SEHRelay


class _ConcurrentSocket:
    def __init__(self):
        self.in_recv = threading.Event()
        self.active = 0
        self.max_active = 0
        self.mu = threading.Lock()

    def _begin(self):
        with self.mu:
            self.active += 1
            self.max_active = max(self.active, self.max_active)

    def _end(self):
        with self.mu:
            self.active -= 1

    def recv(self, n):
        self._begin()
        try:
            self.in_recv.set()
            time.sleep(0.12)
            return b'A' * n
        finally:
            self._end()

    def sendall(self, frame):
        self._begin()
        try:
            time.sleep(0.01)
        finally:
            self._end()


def _trial(serialized):
    client = _WorkerHubLiveSessionClient.__new__(_WorkerHubLiveSessionClient)
    client._send_lock = threading.RLock()
    client._tls_io_lock = threading.RLock()
    client.tls_io_mode = "serialized" if serialized else "baseline"
    client._socket = sock = _ConcurrentSocket()
    errors = []
    def read():
        try:
            assert client._read_exact(2) == b'AA'
        except Exception as exc:
            errors.append(exc)
    def write():
        try:
            client._send_frame(opcode=1, payload=b'test')
        except Exception as exc:
            errors.append(exc)
    reader = threading.Thread(target=read)
    writer = threading.Thread(target=write)
    reader.start()
    assert sock.in_recv.wait(timeout=2)
    writer.start()
    reader.join(timeout=3)
    writer.join(timeout=3)
    assert not errors and not reader.is_alive() and not writer.is_alive()
    return sock.max_active


def test_serialized_mode_never_overlaps_socket_read_and_write():
    assert _trial(serialized=True) == 1


def test_explicit_baseline_mode_keeps_overlapping_io_for_control():
    assert _trial(serialized=False) == 2


def test_closed_websocket_remote_address_cannot_crash_relay_forensic_log():
    class ClosedWebsocket:
        @property
        def remote_address(self):
            raise OSError(10038, 'not a socket')
        protocol = None

    relay = SEHRelay.__new__(SEHRelay)
    relay._lock = threading.RLock()
    from collections import deque
    relay._stream_events = deque(maxlen=10)
    relay._stream_event('relay_client_handler_exiting', 'stream-x', ws=ClosedWebsocket())
    assert relay._stream_events[0]['event'] == 'relay_client_handler_exiting'


def test_production_defaults_to_serialized_tls_io():
    client = _WorkerHubLiveSessionClient(
        hub_url='https://localhost:443', worker_id='test',
        auth_message={'type': 'worker.auth'},
    )
    assert client.tls_io_mode == 'serialized'
    assert client._tls_io_guard() is client._tls_io_lock


def test_baseline_can_be_selected_explicitly_for_ab_regression():
    client = _WorkerHubLiveSessionClient(
        hub_url='https://localhost:443', worker_id='test',
        auth_message={'type': 'worker.auth'},
    )
    client.tls_io_mode = 'baseline'
    assert client._tls_io_guard() is not client._tls_io_lock
