"""P2-A diagnostic regression: surface Temporal admission failures immediately."""
from __future__ import annotations

import queue
import socket
import threading
import time

import pytest

from tools.exp_hub_lab import run_lab
from tools.exp_hub_lab.experimental_stress import render_stress


def test_http_rejection_does_not_wait_on_missing_worker_offer():
    ws_client, ws_peer = socket.socketpair()
    try:
        responses = queue.Queue()
        responses.put(run_lab.ExpHubLabError("request failed HTTP 503: {'error': 'temporal_unavailable'}"))
        start = time.monotonic()
        with pytest.raises(run_lab.ExpHubLabError, match="temporal_unavailable"):
            run_lab._recv_offer_or_rejection(ws_client, responses, timeout=10)
        assert time.monotonic() - start < 2.0
    finally:
        ws_client.close()
        ws_peer.close()


def test_cluster_preflight_surfaces_temporal_not_ready(monkeypatch):
    urls = ("http://127.0.0.1:8970", "http://127.0.0.1:8971", "http://127.0.0.1:8972")
    def fake_json(url, **_kwargs):
        if url.endswith('/hub-identity'):
            return {'ok': True, 'service': 'main_computer.exp_fdb_hub',
                    'hub_id': 'some-hub', 'cluster_id': 'cluster',
                    'peer_hubs': [{}, {}], 'entry_urls': list(urls),
                    'multi_session_auth_required': True}
        return {'ok': True, 'service': 'main_computer.exp_fdb_hub',
                'topology': {'hubs': [{}, {}, {}], 'entry_urls': list(urls), 'cluster_id': 'cluster'}}
    monkeypatch.setattr(run_lab, '_read_json_url', fake_json)
    monkeypatch.setattr(run_lab, '_read_json_url_status',
                        lambda *_a, **_kw: (503, {'ok': False, 'temporal': {'configured': True, 'ready': False}}))
    result = run_lab.check_cluster(hub_urls=urls, require_multisession_auth=True)
    assert not result['ok']
    assert all(c['error'] == 'temporal_not_ready' for c in result['checks'])
    output = render_stress({'ok': False, 'error': 'cluster_check_failed', 'cluster': result})
    assert 'temporal_not_ready' in output
    assert 'temporal_ready=False' in output


def test_cluster_preflight_accepts_ready_temporal(monkeypatch):
    urls = ("http://127.0.0.1:8970", "http://127.0.0.1:8971", "http://127.0.0.1:8972")
    def fake_json(url, **_kwargs):
        if url.endswith('/hub-identity'):
            return {'ok': True, 'service': 'main_computer.exp_fdb_hub',
                    'hub_id': 'some-hub', 'cluster_id': 'cluster',
                    'peer_hubs': [{}, {}], 'entry_urls': list(urls),
                    'multi_session_auth_required': True}
        return {'ok': True, 'service': 'main_computer.exp_fdb_hub',
                'topology': {'hubs': [{}, {}, {}], 'entry_urls': list(urls), 'cluster_id': 'cluster'}}
    monkeypatch.setattr(run_lab, '_read_json_url', fake_json)
    monkeypatch.setattr(run_lab, '_read_json_url_status',
                        lambda *_a, **_kw: (200, {'ok': True, 'temporal': {'configured': True, 'ready': True}}))
    result = run_lab.check_cluster(hub_urls=urls, require_multisession_auth=True)
    assert result['ok']
    assert all(c['temporal_ready'] for c in result['checks'])
