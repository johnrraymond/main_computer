"""A timed-out partial WebSocket frame must not corrupt Worker traffic."""
import json
import socket
import pytest

from main_computer.viewport_routes_energy import _WorkerHubLiveSessionClient


class SegmentedSocket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    def recv(self, requested):
        data = next(self.chunks)
        if data is None:
            raise socket.timeout('interleaved transport delay')
        assert len(data) <= requested
        return data


def reader(*chunks):
    client = object.__new__(_WorkerHubLiveSessionClient)
    client._socket = SegmentedSocket(chunks)
    client._fragment_read_timeout_s = 15
    return client


def test_idle_socket_timeout_is_not_a_partial_frame():
    client = reader(None, b'\x81\x02', b'{}')
    with pytest.raises(TimeoutError):
        client._recv_json()
    assert client._recv_json() == {}


def test_header_and_payload_survive_multiple_transport_timeouts():
    message = json.dumps({'type': 'hub.ping', 'ping_id': 'fragment'}).encode()
    assert len(message) < 126
    client = reader(b'\x81', None, b'\x00' if not message else bytes([len(message)]),
                    None, message[:4], None, message[4:])
    assert client._recv_json() == {'type': 'hub.ping', 'ping_id': 'fragment'}


def test_delay_between_complete_header_and_first_payload_byte():
    client = reader(b'\x81\x02', None, None, b'{}')
    assert client._recv_json() == {}


def test_extended_length_and_payload_fragmentation():
    payload = json.dumps({'payload': 'a' * 130}).encode()
    client = reader(b'\x81\x7e', None, len(payload).to_bytes(2, 'big'),
                    None, payload[:17], None, payload[17:])
    assert client._recv_json() == {'payload': 'a' * 130}


def test_close_frame_reports_only_the_close_code():
    client = reader(b'\x88\x04', b'\x03\xe9NO')
    with pytest.raises(RuntimeError, match='close_code=1001') as failure:
        client._recv_json()
    assert 'NO' not in str(failure.value)


def test_frame_timeout_is_bounded_after_fragment(monkeypatch):
    import main_computer.viewport_routes_energy as mod
    ticks = iter([0, 0, 20])
    monkeypatch.setattr(mod.time, 'monotonic', lambda: next(ticks))
    client = reader(b'\x81', None, None)
    with pytest.raises(RuntimeError, match='frame incomplete'):
        client._recv_json()
