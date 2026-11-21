"""Hub Choice persistence must never be a Worker connection/session operation."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def test_hub_choice_write_does_not_modify_worker_session_or_wake_supervisor(tmp_path, monkeypatch):
    from main_computer import game_web_loader
    monkeypatch.setattr(game_web_loader, "read_game_bundle", lambda *_args, **_kwargs: "")
    from main_computer.viewport_routes_energy import WorkerRuntimeService

    kicks=[]
    server=SimpleNamespace(debug_root=tmp_path,
        config=SimpleNamespace(hub_url='https://hub.example.invalid'),
        signal=lambda *args, **kwargs: None,
        worker_runtime_supervisor=SimpleNamespace(kick=lambda reason:kicks.append(reason)))

    class Handler(WorkerRuntimeService):
        def _worker_ui_client_is_local(self):
            return True
        def _read_json(self):
            return {'mcelHubChoice':'slh','changed_fields':['mcelHubChoice']}
        def _send_json(self, body, status=None):
            self.response=body

    handler=Handler(server)
    original=handler._save_worker_settings({
        'selectedNetwork':'testnet','workerAutoConnectNetwork':'testnet',
        'workerRequestedRing':'2',
        'signedWorkerConnection':{'signature':'DO_NOT_ERASE','worker_id':'w123'},
        'sellerEnabled':True,
    })
    handler._handle_worker_settings_save()
    assert handler.response['ok'] is True
    saved=handler._load_worker_settings()
    assert saved['mcelHubChoice']=='slh'
    for field in ('selectedNetwork','workerAutoConnectNetwork','workerRequestedRing',
                  'signedWorkerConnection','sellerEnabled'):
        assert saved[field]==original[field],field
    assert not kicks, 'Hub selection must not activate the Worker supervisor'
    assert (Path(tmp_path)/'worker_settings.json').exists()


def test_legacy_session_refresh_cannot_restore_obsolete_hub_choice(tmp_path, monkeypatch):
    """A stale network-session GET must never undo a newer Hub radio save."""
    from main_computer import game_web_loader
    monkeypatch.setattr(game_web_loader, "read_game_bundle", lambda *_args, **_kwargs: "")
    from main_computer.viewport_routes_energy import WorkerRuntimeService

    server = SimpleNamespace(debug_root=tmp_path,
        config=SimpleNamespace(hub_url='https://hub.example.invalid'),
        signal=lambda *args, **kwargs: None)

    class Handler(WorkerRuntimeService):
        def _worker_ui_client_is_local(self):
            return True

        def _worker_network_session_payload(self, settings, *, check_hub=False):
            assert settings['mcelHubChoice'] == 'none'
            # Simulate a Hub Choice save completing while the legacy GET is
            # still using an older settings snapshot.
            self._save_worker_settings({'mcelHubChoice': 'mainnet'},
                                       changed_fields=['mcelHubChoice'])
            return {'ok': True, 'session': {'selected_network': 'testnet',
                    'connection_status': 'stale', 'connected_hub_url': '',
                    'connection_error': 'not connected'}, 'networks': []}

        def _send_json(self, payload, status=None):
            self.response = payload

    handler = Handler(server)
    handler._save_worker_settings({'selectedNetwork': 'testnet', 'mcelHubChoice': 'none'})
    handler._handle_worker_network_session_load()
    assert handler.response['ok'] is True
    saved = handler._load_worker_settings()
    assert saved['mcelHubChoice'] == 'mainnet'
    assert saved['selectedNetwork'] == 'testnet'
    assert saved['workerConnectionStatus'] == 'stale'
