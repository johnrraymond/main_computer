"""Worker destination page split: SLH is not a blockchain marketplace network.

P1 deliberately does not perform enrollment. The SLH Worker Bank remains the
source of truth for enrolled and connected Workers.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import re
import shutil
import subprocess

import pytest

from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'main_computer/web/applications/apps/worker.html').read_text(encoding='utf-8')
JS = (ROOT / 'main_computer/web/applications/scripts/worker.js').read_text(encoding='utf-8')
CSS = (ROOT / 'main_computer/web/applications/styles/worker.css').read_text(encoding='utf-8')


class WorkerViewport(ViewportEnergyRoutesMixin):
    def __init__(self, root: Path):
        self.server = SimpleNamespace(
            debug_root=root,
            config=SimpleNamespace(hub_url='http://127.0.0.1:8871', hub_network='dev', model='sample', hub_timeout_s=1),
            signal=lambda *a, **kw: None,
        )
        self.response = None
        self.request = {}
        self.reasons = []

    def _worker_ui_client_is_local(self):
        return True

    def _read_json(self):
        return dict(self.request)

    def _send_json(self, response, **_kwargs):
        self.response = response

    def _kick_worker_runtime_supervisor(self, reason):
        self.reasons.append(reason)


def test_backend_accepts_slh_as_destination_without_marketplace_profile(tmp_path, monkeypatch):
    view = WorkerViewport(tmp_path)
    # No Stable Hub probe, external HTTP request, wallet operation or enrollment
    # is allowed merely to display the unconfigured SLH destination.
    monkeypatch.setattr(view, '_fetch_hub_status', lambda *_a, **_kw: pytest.fail('SLH is not a Stable Hub'))
    view.request = {'network': 'slh', 'requested_ring': '3'}
    view._handle_worker_network_session_select()
    assert view.response['ok'] is True
    session = view.response['session']
    assert session['selected_network'] == 'slh'
    assert session['profile']['kind'] == 'managed-slh'
    assert session['connection_status'] == 'setup'
    assert 'SLH Worker device setup is incomplete' in session['connection_error']
    assert session['profile']['hub_url'] == ''
    saved = view._load_worker_settings()
    assert saved['selectedNetwork'] == saved['workerAutoConnectNetwork'] == 'slh'
    assert 'worker-network-select' in view.reasons


def test_slh_mode_preserves_inactive_marketplace_preferences(tmp_path, monkeypatch):
    view = WorkerViewport(tmp_path)
    original = view._save_worker_settings({'settings': {
        'selectedNetwork': 'dev', 'workerAutoConnectNetwork': 'dev',
        'sellerCreditsPerToken': '0.003', 'sellerTargetTokens': 512,
        'remoteMode': 'never', 'remoteEnabled': False,
        'models': 'gemma4:26b', 'workerRequestedRing': '2',
    }})
    monkeypatch.setattr(view, '_fetch_hub_status', lambda *_a, **_kw: pytest.fail('unexpected Hub probe'))
    view.request = {'network': 'slh', 'requested_ring': '2'}
    view._handle_worker_network_session_select()
    assert view.response['ok'] is True
    saved = view._load_worker_settings()
    for key in ('sellerCreditsPerToken', 'sellerTargetTokens', 'remoteMode', 'remoteEnabled', 'models'):
        assert saved[key] == original[key], key
    assert saved['selectedNetwork'] == 'slh'


def test_configured_slh_does_not_claim_enrollment_from_saved_url(tmp_path, monkeypatch):
    view = WorkerViewport(tmp_path)
    monkeypatch.setattr(view, '_fetch_hub_status', lambda *_a, **_kw: pytest.fail('SLH is not a Stable Hub'))
    saved = view._save_worker_settings({'settings': {
        'selectedNetwork': 'slh', 'workerAutoConnectNetwork': 'slh',
        'managedSLHUrl': 'https://localhost:8897',
        'managedSLHDeviceId': 'known-device',
        'managedSLHFingerprint': 'a'*64,
        'managedSLHIdentityDir': str(tmp_path / 'device'),
        'sellerEnabled': True,
    }})
    session = view._worker_network_session_payload(saved, check_hub=True)['session']
    assert session['selected_network'] == 'slh'
    assert session['connection_status'] != 'connected'
    assert 'Verify Worker enrollment' in session['connection_error']
    assert session['profile']['hub_url'] == 'https://localhost:8897'


def test_existing_marketplace_profiles_and_wallet_selection_remain_unchanged(tmp_path):
    view = WorkerViewport(tmp_path)
    assert view._worker_network_order() == ['mainnet', 'testnet', 'test', 'dev']
    assert view._normalize_worker_network_key('slh') == 'slh'
    assert view._normalize_worker_network_key('none') == 'none'
    assert view._worker_network_profile_hub_url('dev') == 'http://127.0.0.1:8871'
    assert view._worker_network_profile_hub_url('mainnet') == 'https://mainnet-hub.greatlibrary.io'
    assert view._worker_network_profile_hub_url('slh') == ''


def test_markup_has_one_radio_group_and_a_real_slh_conditional_surface():
    assert 'id="worker-auto-hub-slh" value="slh" data-worker-network="slh"' in HTML
    for network in ['mainnet', 'testnet', 'test', 'dev', 'slh']:
        assert re.search(r'<input[^>]+name="worker-auto-connect-network"[^>]+value="' + network + r'"', HTML)
    assert 'id="worker-slh-panel"' in HTML
    assert 'id="worker-marketplace-workspace"' in HTML
    assert 'data-worker-marketplace' in HTML
    assert 'id="worker-slh-enrollment-status">Check SLH Worker Bank' in HTML
    assert 'worker-app [hidden]' in CSS and 'display: none !important' in CSS
    assert 'id="worker-network-work-now"' in HTML  # kept for marketplace
    assert 'id="worker-register-offer" data-worker-marketplace' in HTML
    assert 'id="worker-save-settings"' in HTML  # remains available in either mode
    assert 'id="worker-remote-enabled"' in HTML  # preferences not deleted


def test_slh_selection_never_launches_metamask():
    fn_start = JS.index('async function workerSelectNetworkAndConnectWallet(')
    fn_end = JS.index('async function workerDisconnectSelectedNetworkAndWallet(', fn_start)
    fn = JS[fn_start:fn_end]
    assert 'if (selected === "slh") return;' in fn
    assert fn.index('if (selected === "slh") return;') < fn.index('connectWorkerPrimaryWallet(event)')
    assert 'const WORKER_DESTINATIONS = [...WORKER_NETWORK_ORDER, "slh"];' in JS
    assert 'workerRenderDestinationMode(selected);' in JS


def test_conditional_rendering_runs_for_slh_and_marketplace_without_deleting_form_data():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable')
    start = JS.index('    function workerRenderDestinationMode(selected) {')
    end = JS.index('    function renderWorkerNetworkSurface() {', start)
    function_source = JS[start:end]
    script = '''
const vm = require('node:vm');
const source = %s;
const assert = require('node:assert/strict');
const hiddenElements = Array.from({length: 9}, () => ({hidden: false, value: 'preserved'}));
const elements = new Map();
for (const key of ['worker-slh-panel','worker-connection-title','worker-slh-selection','worker-slh-runtime-status','worker-slh-models','worker-slh-active-jobs']) elements.set('#'+key,{hidden:true,textContent:''});
const ctx = {
  workerApp:{dataset:{},querySelectorAll:()=>hiddenElements},
  document:{querySelector:(q)=>elements.get(q)},
  workerNetworkSetText:(el,txt)=>{if(el)el.textContent=String(txt);},
  workerRuntimeStatus:{runtime:{state:'SETUP'},phase:'not_accepting',active_jobs:0},
  workerOfferModels:{value:'gemma4:26b'}
};
const run = vm.runInNewContext('('+source.trim()+')',ctx);
run('slh');
assert.equal(ctx.workerApp.dataset.workerDestination,'slh');
assert.equal(elements.get('#worker-slh-panel').hidden,false);
assert.equal(elements.get('#worker-slh-runtime-status').textContent,'SETUP');
assert.equal(elements.get('#worker-slh-models').textContent,'gemma4:26b');
assert.ok(hiddenElements.every(x=>x.hidden && x.value==='preserved'));
run('mainnet');
assert.equal(ctx.workerApp.dataset.workerDestination,'marketplace');
assert.equal(elements.get('#worker-slh-panel').hidden,true);
assert.ok(hiddenElements.every(x=>!x.hidden && x.value==='preserved'));
''' % json.dumps(function_source)
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
