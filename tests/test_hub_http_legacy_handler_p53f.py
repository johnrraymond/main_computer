"""P5.3f route extraction: identity, authorization source, and loopback behavior."""
from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from main_computer import hub, hub_http_legacy_handler, hub_http_legacy_support
from main_computer.config import MainComputerConfig
from main_computer.exp_fdb_hub import ExperimentalFoundationDbHubServerHandler
from main_computer.hub_http_runtime import HubHttpServer
from main_computer.hub_http_shared import _JsonHandler
from tools.hub_http_legacy_handler_verify import verify_contract


def test_exact_handler_source_and_owner():
    result = verify_contract()
    assert result['ok'] is True, result
    assert hub.HubServerHandler is hub_http_legacy_handler.HubServerHandler
    assert hub.HubServerHandler.__bases__ == (_JsonHandler,)
    assert ExperimentalFoundationDbHubServerHandler.__bases__ == (hub_http_legacy_handler.HubServerHandler,)
    assert hub.HubCreditAuthorizationError is hub_http_legacy_support.HubCreditAuthorizationError


def test_wallet_authorization_globals_resolve_to_shared_contract():
    cls=hub_http_legacy_handler.HubServerHandler
    check=cls._handle_multisession_key_validate
    assert check.__globals__['HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID'] == hub.HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID
    assert check.__globals__['HubCreditAuthorizationError'] is hub.HubCreditAuthorizationError


def test_legacy_server_default_handler_injection_does_not_import_facade(tmp_path):
    config = MainComputerConfig(workspace=tmp_path, hub_root=tmp_path/'hub',model='test-model',hub_bridge_backend='mock')
    server=HubHttpServer(('127.0.0.1', 0),config,verbose=False)
    try:
        assert server.RequestHandlerClass is hub_http_legacy_handler.HubServerHandler
    finally:
        server.server_close()


def test_legacy_handler_status_over_loopback_and_dev_test_inheritance():
    server=ThreadingHTTPServer(('127.0.0.1', 0), hub_http_legacy_handler.HubServerHandler)
    server.verbose=False
    server.resource_begin_headers=lambda connection: True
    server.resource_headers_complete=lambda connection: True
    server.registry=SimpleNamespace(status=lambda: {'worker_count': 2, 'available_worker_count': 1, 'stale_worker_count': 0})
    server.config=SimpleNamespace(hub_network='dev',chain_id=42424242, hub_id='dev-hub1')
    server.stable_hub_node=SimpleNamespace(hub_id='dev-hub1')
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{server.server_port}/api/hub/status',timeout=3) as response:
            body=json.load(response)
        assert body['worker_count']==2
        assert body['network_key']=='dev'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
