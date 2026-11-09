"""P5.3e: extracted HTTP server/dispatcher preserve legacy and Dev-Test contracts."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer, HubServerHandler, HubDispatcher
from main_computer.hub_http_runtime import HubHttpServer as ExtractedServer, HubDispatcher as ExtractedDispatcher
from main_computer.hub_http_shared import _JsonHandler
from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubHttpServer, ExperimentalFoundationDbHubServerHandler
from tools.hub_http_runtime_verify import verify_contract


def _config(tmp_path: Path) -> MainComputerConfig:
    return MainComputerConfig(
        workspace=tmp_path, hub_root=tmp_path / 'hub',
        model='test-model', hub_bridge_backend='mock',
    )


def test_http_runtime_owner_identity_exact_dispatcher_and_audit_methods():
    result = verify_contract()
    assert result['ok'] is True, result
    assert HubDispatcher is ExtractedDispatcher
    assert HubHttpServer is ExtractedServer
    assert issubclass(ExperimentalFoundationDbHubHttpServer, HubHttpServer)


def test_legacy_server_defaults_to_unchanged_legacy_handler(tmp_path):
    server = HubHttpServer(('127.0.0.1', 0), _config(tmp_path), verbose=False)
    try:
        assert server.RequestHandlerClass is HubServerHandler
        assert server.dispatcher.__class__ is HubDispatcher
        assert server.worker_route_semaphore is not None
        assert server.multisession_key_store_path.parent == tmp_path / 'hub' / 'compute_credits'
    finally:
        server.server_close()


def test_dev_test_handler_can_be_selected_without_instantiating_fdb_or_rewriting_legacy_handler(tmp_path):
    server = HubHttpServer(('127.0.0.1', 0), _config(tmp_path),
                           verbose=False, handler_cls=ExperimentalFoundationDbHubServerHandler)
    try:
        assert server.RequestHandlerClass is ExperimentalFoundationDbHubServerHandler
        assert HubServerHandler is not ExperimentalFoundationDbHubServerHandler
        assert server.dispatcher.__class__ is HubDispatcher
    finally:
        server.server_close()


class _Inspect(_JsonHandler):
    def do_GET(self):
        self._send_json({'handler': 'injected', 'bounded': hasattr(self.server, 'resource_limits')})


def test_extracted_server_dispatches_an_explicit_handler_over_loopback(tmp_path):
    server = HubHttpServer(('127.0.0.1', 0), _config(tmp_path),
                           verbose=False, handler_cls=_Inspect)
    thread=threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/probe', timeout=3) as response:
            body = json.load(response)
        assert body == {'handler':'injected', 'bounded':True}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_extracted_server_legacy_status_over_loopback(tmp_path):
    server = HubHttpServer(('127.0.0.1', 0), _config(tmp_path), verbose=False)
    thread=threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/hub/status', timeout=3) as response:
            body=json.load(response)
        assert 'worker_count' in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
