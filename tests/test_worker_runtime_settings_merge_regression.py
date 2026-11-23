"""A stale backend runtime reconcile may not replace newer seller settings."""
from __future__ import annotations

import threading
from types import SimpleNamespace

from main_computer.config import MainComputerConfig
from main_computer.viewport_routes_energy import WorkerRuntimeService


class _Server:
    def __init__(self, root):
        self.debug_root = root
        self.config = MainComputerConfig(workspace=root, hub_url='http://127.0.0.1:8780')
        self.worker_runtime_lock = threading.RLock()
        self.chat_ai_processes = SimpleNamespace(
            local_ai_capacity_snapshot=lambda **_: {
                'ok': True, 'available_now': True, 'active_run_count': 0,
            }
        )
        self.signals = []
    def signal(self, name, **fields):
        self.signals.append((name, fields))


def test_runtime_reconcile_does_not_roll_back_concurrent_seller_price(tmp_path, monkeypatch):
    service = WorkerRuntimeService(_Server(tmp_path))
    original = service._save_worker_settings({
        'sellerCreditsPerToken': '0.001',
        'sellerTargetTokens': 512,
        'sellerAvailabilityMode': 'totally_idle',
        'sellerEnabled': False,
        'rentalEnabled': False,
    })
    def concurrent_seller_save(**_kwargs):
        latest = service._save_worker_settings({
            'settings': {'sellerCreditsPerToken': '0.00001', 'sellerTargetTokens': 512},
        }, changed_fields=['sellerCreditsPerToken', 'sellerTargetTokens'])
        assert latest['sellerCreditsPerToken'] == '0.00001'
        return 0
    monkeypatch.setattr(service, '_worker_live_session_active_work_count', concurrent_seller_save)
    updated, _status = service._worker_runtime_transition(original, action='sync', send_heartbeat=False)
    assert updated['sellerCreditsPerToken'] == '0.00001'
    assert service._load_worker_settings()['sellerCreditsPerToken'] == '0.00001'
    assert updated['workerRuntimePhase'] == 'not_accepting'


def test_runtime_explicit_deactivation_still_updates_seller_policy(tmp_path, monkeypatch):
    service = WorkerRuntimeService(_Server(tmp_path))
    previous = service._save_worker_settings({
        'sellerEnabled': True, 'rentalEnabled': True,
        'sellerCreditsPerToken': '0.001',
        'selectedNetwork': 'none', 'workerAutoConnectNetwork': 'none',
    })
    monkeypatch.setattr(service, '_worker_live_session_active_work_count', lambda **_: 0)
    after, _ = service._worker_runtime_transition(previous, action='deactivate', send_heartbeat=False)
    assert after['sellerEnabled'] is False
    assert after['rentalEnabled'] is False
    assert service._load_worker_settings()['sellerEnabled'] is False


def test_runtime_reconcile_does_not_roll_back_concurrent_policy_and_tokens(tmp_path, monkeypatch):
    service = WorkerRuntimeService(_Server(tmp_path))
    previous = service._save_worker_settings({
        'sellerCreditsPerToken': '0.001', 'sellerTargetTokens': 1024,
        'sellerAvailabilityMode': 'totally_idle',
        'sellerEnabled': False, 'rentalEnabled': False,
    })
    def concurrent_seller_save(**_kwargs):
        service._save_worker_settings({
            'settings': {'sellerTargetTokens': 512, 'sellerAvailabilityMode': 'ai_idle'},
        }, changed_fields=['sellerTargetTokens', 'sellerAvailabilityMode'])
        return 0
    monkeypatch.setattr(service, '_worker_live_session_active_work_count', concurrent_seller_save)
    updated, _ = service._worker_runtime_transition(previous, action='sync', send_heartbeat=False)
    assert updated['sellerTargetTokens'] == 512
    assert updated['sellerAvailabilityMode'] == 'ai_idle'
    assert service._load_worker_settings()['sellerTargetTokens'] == 512
