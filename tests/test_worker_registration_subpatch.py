"""Registration-only Worker subpatch verification; no Hub network or money calls."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'main_computer/web/applications'


def _routes(tmp_path, monkeypatch):
    # The exported project snapshot omits its independent game bundle.
    # Stub only this unrelated HTML asset while importing viewport services.
    from main_computer import game_web_loader
    monkeypatch.setattr(game_web_loader, 'read_game_bundle', lambda *args, **kwargs: '')
    from main_computer.config import MainComputerConfig
    from main_computer.viewport_routes_energy import WorkerRuntimeService
    cfg = MainComputerConfig(workspace=tmp_path)
    return WorkerRuntimeService(SimpleNamespace(debug_root=tmp_path, config=cfg, signal=lambda *a, **k: None))


def test_removed_worker_fields_do_not_block_form():
    html = (APP/'apps/worker.html').read_text(encoding='utf-8')
    js = (APP/'scripts/worker.js').read_text(encoding='utf-8')
    segment = js.split('function buildWorkerOfferRegistrationPayload()', 1)[1].split('async function workerGetJson(', 1)[0]
    assert 'id="worker-node-id"' not in html
    assert 'id="worker-endpoint"' not in html
    assert 'Worker node id is required.' not in segment
    assert 'Worker callback endpoint is required.' not in segment
    assert 'node_id: settings.nodeId' not in segment
    assert 'settings.executionMode === "worker_pull_v0"' in segment


def test_finish_does_not_require_registration_form():
    js = (APP/'scripts/worker.js').read_text(encoding='utf-8')
    segment = js.split('function buildWorkerNetworkWorkNowPayload(', 1)[1].split('async function workerSubmitNetworkWorkNow(', 1)[0]
    assert 'action === "finish"' in segment
    assert 'buildWorkerNetworkRegistrationPayload' in segment


def test_backend_provides_worker_identity_for_pull(tmp_path, monkeypatch):
    service = _routes(tmp_path, monkeypatch)
    worker = service._worker_registration_payload_from_ui({
        'models': ['gemma4:26b-a4b-it-q4_K_M'],
        'availability': {'accept_paid_jobs': True, 'availability_mode': 'ai_idle'},
        'execution': {'mode':'worker_pull_v0','max_concurrency':1},
    })
    assert worker['node_id'] == service.server.config.hub_worker_node_id
    assert worker['endpoint'] == ''
    assert worker['capabilities']['execution']['mode'] == 'worker_pull_v0'


@pytest.mark.parametrize('mode', ['http_callback', 'callback'])
def test_backend_rejects_callback_without_endpoint(tmp_path, mode, monkeypatch):
    service = _routes(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='Callback execution requires'):
        service._worker_registration_payload_from_ui({
            'models': ['example-model'],
            'availability': {'accept_paid_jobs': True, 'availability_mode':'ai_idle'},
            'execution': {'mode': mode},
        })


def test_hub_roundtrip_and_callback_guard(tmp_path):
    from main_computer.hub import HubRegistry
    registry = HubRegistry(tmp_path, allow_insecure_dev_network=True)
    worker = registry.register_worker(node_id='reg-worker', endpoint='', model='example-model',
        capabilities={'execution': {'mode': 'worker_pull_v0'}})
    assert worker.endpoint == ''
    assert any(w['node_id'] == 'reg-worker' for w in HubRegistry(tmp_path,allow_insecure_dev_network=True).status()['workers'])
    with pytest.raises(ValueError, match='endpoint is required'):
        registry.register_worker(node_id='invalid-callback', endpoint='', model='example-model',
            capabilities={'execution': {'mode': 'callback'}})
    with pytest.raises(ValueError):
        registry.register_worker(node_id='invalid-url', endpoint='http://evil.invalid', model='example-model',
            capabilities={'execution': {'mode': 'worker_pull_v0'}})


def test_chromium_real_page_payload_and_finish_action():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as manager:
        browser_path = next((path for name in ('chromium', 'chromium-browser', 'chrome', 'msedge')
                             if (path := shutil.which(name))), None)
        try:
            browser = manager.chromium.launch(headless=True, **({'executable_path': browser_path} if browser_path else {}))
        except Exception as exc:
            pytest.skip(f'Chromium not installed: {exc}')
        try:
            page = browser.new_page()
            page.set_content((APP/'apps/worker.html').read_text(encoding='utf-8'))
            page.add_script_tag(content=(APP/'scripts/dom-bindings/worker.js').read_text(encoding='utf-8'))
            page.add_script_tag(content=(APP/'scripts/worker.js').read_text(encoding='utf-8'))
            result = page.evaluate('''() => {
                workerNetworkSession.selected_network = 'testnet';
                workerNetworkSession.profile = {hub_url: 'https://testnet-hub.greatlibrary.io', chain_id: '42424241'};
                workerNetworkSession.connected_hub_url = 'https://testnet-hub.greatlibrary.io';
                document.querySelector('#worker-auto-hub-testnet').checked = true;
                return {offer: buildWorkerOfferRegistrationPayload(), finish: buildWorkerNetworkWorkNowPayload({action:'finish'})};
            }''')
            assert result['offer']['hub_url'] == 'https://testnet-hub.greatlibrary.io'
            assert 'node_id' not in result['offer']['worker']
            assert 'endpoint' not in result['offer']['worker']
            assert result['offer']['worker']['execution']['mode'] == 'worker_pull_v0'
            assert result['finish']['action'] == 'finish'
            assert result['finish']['network'] == 'testnet'
            assert 'worker' not in result['finish']
        finally:
            browser.close()
