"""Disposable HTTP-boundary tests for the SLH application's lifecycle actions.

These validate the real viewport route and operation headers while replacing the
machine-local controller, so no service or persistent identity state is mutated.
"""
from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from main_computer.config import MainComputerConfig


def test_declared_lifecycle_actions_require_same_origin_and_dispatch_correctly():
    with patch('main_computer.game_web_loader.read_game_bundle', return_value='<div></div>'):
        from main_computer.viewport_server import ViewportServer

    class Controller:
        calls: list[tuple[str, dict, str]] = []

        def execute(self, action, arguments, *, token=''):
            self.calls.append((action, arguments, token))
            return {'outcome': {'configure-and-enable': 'RUNNING',
                                'toggle': 'STOPPED',
                                'toggle-enablement': 'DISABLED'}[action],
                    'message': 'test-only receipt'}

    config = MainComputerConfig(workspace=__import__('pathlib').Path(__file__).resolve().parents[1])
    server = ViewportServer(('127.0.0.1', 0), config, verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'

    def dispatch(action, arguments, *, header=None, origin=None):
        data = json.dumps({'action': action, 'arguments': arguments}).encode()
        req = Request(base + '/api/applications/slh/setup/action', method='POST', data=data,
                      headers={'Content-Type': 'application/json',
                               'Origin': base if origin is None else origin,
                               'Sec-Fetch-Site': 'same-origin',
                               'X-MCEL-SLH-Operation': action if header is None else header,
                               'Authorization': 'Bearer disposable-test-token'})
        try:
            with urlopen(req, timeout=8) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    args = {'adminDeviceId': 'test-admin', 'bindAddress': '127.0.0.1', 'port': 8899}
    try:
        with patch('main_computer.mcel_slh_first_run.SlhFirstRunController', Controller):
            for action in ('configure-and-enable', 'toggle', 'toggle-enablement'):
                before = len(Controller.calls)
                code, _ = dispatch(action, args, origin='https://attacker.example')
                assert code == 403
                code, _ = dispatch(action, args, header='other-operation')
                assert code != 200
                assert len(Controller.calls) == before
                code, body = dispatch(action, args)
                assert code == 200, body
                assert body['message'] == 'test-only receipt'
                assert Controller.calls[-1] == (action, args, 'disposable-test-token')
            assert len(Controller.calls) == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=8)
