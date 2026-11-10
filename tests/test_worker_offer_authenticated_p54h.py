"""P5.4h: authenticated Worker offer update, no legacy REST or Work now side effects."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.hub_interface import resolve_hub_destination
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin, WorkerRuntimeService


WALLET = '0x' + 'a'*40
KEY = 'test-msk-authorized'


class _Viewport(ViewportEnergyRoutesMixin):
    def __init__(self, tmp_path: Path, network='dev'):
        self.server = SimpleNamespace(debug_root=tmp_path, signal=lambda *args, **kwargs: None,
                                      config=SimpleNamespace(hub_url='http://127.0.0.1:8871'))
        self.body = {}
        self.responses = []
        self.outbound = []
        self.closes = []
        self.normal = True
        self.active = 0
        self.auth_valid = True
        self.reject_registration = False
        self.after_send = None
        destination = resolve_hub_destination({'selectedNetwork': network})
        self._save_worker_settings({'selectedNetwork': network, 'workerAutoConnectNetwork': network,
                                    'workerRequestedRing': '3', 'sellerEnabled': True,
                                    'sellerAvailabilityMode': 'totally-idle',
                                    'workerWorkNowOverrideExpiresAt': '2099-01-01T00:00:00+00:00',
                                    'workerWorkNowOverrideStartedAt': '2026-01-01T00:00:00+00:00',
                                    'workerWorkNowOverrideDurationSeconds': 0})
        self.body = {'hub_url': destination.hub_url, 'network': network, 'chain_id': '',
                     'requested_ring': '3', 'wallet_address': WALLET,
                     'active_multisession_key_id': KEY,
                     'worker': {'model': 'test-model', 'models': ['test-model']}}

    def _worker_ui_client_is_local(self):
        return True

    def _read_json(self):
        return self.body

    def _send_json(self, data, **kwargs):
        self.responses.append((data, kwargs))

    def _worker_network_session_payload(self, settings, *, check_hub=False):
        network = settings.get('workerAutoConnectNetwork')
        chain = '42424240' if network=='mainnet' else '42424241' if network=='testnet' else '42424242'
        return {'session': {'profile': {'chain_id': chain, 'hub_url': resolve_hub_destination(settings).hub_url}}}

    def _worker_registration_payload_from_ui(self, worker, *, work_now_override=False, validate_offer_only=False):
        assert validate_offer_only and not work_now_override
        return {'node_id': 'local-worker', **worker}

    def _worker_runtime_local_policy(self, settings, **kwargs):
        return {'normal_allowed': self.normal, 'normal_reason': 'busy' if not self.normal else 'ready',
                'allowed': True, 'work_now_override': {'active': True}}

    def _worker_multisession_authorization_for_wallet(self, *, hub_url, wallet_address, chain_id='', requested_key_id=''):
        assert hub_url == resolve_hub_destination(self._load_worker_settings()).hub_url
        if not self.auth_valid or wallet_address.lower() != WALLET or requested_key_id != KEY:
            raise ValueError('MSK mismatch')
        return {'key_id': KEY, 'multisession_key_id': KEY, 'chain_id': chain_id,
                'wallet_address': wallet_address, 'kind': 'multisession_key'}

    def _worker_live_session_active_work_count(self, *, hub_url=None, worker_id=None):
        return self.active

    def _worker_runtime_hub_url(self, settings):
        return resolve_hub_destination(settings).hub_url

    def _worker_runtime_worker_id(self, settings):
        signed = settings.get('signedWorkerConnection', {})
        return signed.get('wallet_address') or ''

    def _close_worker_live_session(self, *, hub_url, worker_id, reason):
        self.closes.append((hub_url, worker_id, reason))
        return {'ok': True}

    def _post_worker_start_to_hub(self, *, hub_url, payload):
        self.outbound.append((hub_url, payload))
        if self.after_send:
            self.after_send()
        if self.reject_registration:
            raise RuntimeError('Hub declined worker.auth')
        return {'ok': True, 'transport': 'websocket-live-session' if payload['network'] in ('mainnet','testnet') else 'dev-test-connect',
                'worker_id': 'server-issued-worker-id', 'assigned_ring': '3',
                'worker': dict(payload['worker'], worker_id='server-issued-worker-id', assigned_ring='3'),
                'pool': {}}


def _sync(view):
    view._handle_worker_offer_sync()
    assert view.responses
    return view.responses[-1]


@pytest.mark.parametrize('network', ['dev','test','testnet','mainnet'])
def test_sync_uses_canonical_authenticated_hub_only_when_normal_available(tmp_path, network):
    v=_Viewport(tmp_path,network=network)
    result,kw=_sync(v)
    assert kw == {} and result['ok'] is True and result['status'] == 'accepted'
    assert len(v.outbound)==1
    assert v.outbound[0][0]==resolve_hub_destination({'selectedNetwork':network}).hub_url
    assert v.outbound[0][1]['multisession_authorization']['key_id']==KEY
    settings=v._load_worker_settings()
    assert settings['workerOfferPending'] is False
    assert settings['workerOfferStatus']['status']=='accepted'
    assert settings['workerRegisteredId']=='server-issued-worker-id'
    assert settings['workerHubRegistration']['ok'] is True
    assert settings['workerWorkNowOverrideExpiresAt']=='2099-01-01T00:00:00+00:00'


def test_offer_saved_but_not_advertised_when_normal_policy_blocks_even_with_work_now_override(tmp_path):
    v=_Viewport(tmp_path)
    v.normal=False
    response,kwargs=_sync(v)
    assert response['status']=='deferred' and not kwargs
    assert v.outbound==[] and v.closes==[]
    saved=v._load_worker_settings()
    assert saved['workerOfferPending'] is True
    assert saved['signedWorkerConnection']['hub_registration_status']=='not_submitted'
    assert saved['workerWorkNowOverrideExpiresAt']=='2099-01-01T00:00:00+00:00'
    v.normal=True
    outcome=v._worker_offer_sync_pending()
    assert outcome['status']=='accepted' and len(v.outbound)==1
    assert v._load_worker_settings()['workerOfferPending'] is False


def test_backend_supervisor_reconciles_deferred_offer_without_work_now(tmp_path):
    v=_Viewport(tmp_path)
    v.normal=False
    assert _sync(v)[0]['status']=='deferred'
    v.normal=True
    from main_computer.viewport_routes_energy import WorkerRuntimeService
    service=WorkerRuntimeService(v.server)
    service._worker_offer_sync_pending=v._worker_offer_sync_pending
    service._load_worker_settings=v._load_worker_settings
    service._worker_runtime_transition=lambda settings, **kw:(settings,{'runtime':{'phase':'accepting'}})
    status=service.reconcile_worker_runtime(reason='test')
    assert status['runtime']['phase']=='accepting'
    assert len(v.outbound)==1


def test_off_network_and_foreign_url_fail_without_hub_calls(tmp_path):
    v=_Viewport(tmp_path)
    v.body['hub_url']='https://mainnet-hub.greatlibrary.io'
    response,kw=_sync(v)
    assert response['ok'] is False and kw['status']==400 and v.outbound==[]
    v.body['hub_url']=resolve_hub_destination(v._load_worker_settings()).hub_url
    v._save_worker_settings({'selectedNetwork':'none','workerAutoConnectNetwork':'none'})
    response,kw=_sync(v)
    assert response['ok'] is False and v.outbound==[]


def test_invalid_msk_denied_before_saving_offer_or_contacting_hub(tmp_path):
    v=_Viewport(tmp_path);v.auth_valid=False
    response,kw=_sync(v)
    assert response['ok'] is False and 'MSK mismatch' in response['error']
    assert not v._load_worker_settings()['workerOfferPending'] and not v.outbound


def test_active_work_must_finish_before_changing_market(tmp_path):
    v=_Viewport(tmp_path);v.active=1
    response,kw=_sync(v)
    assert response['ok'] is False and 'Active Worker jobs' in response['error']
    assert not v.outbound and not v.closes and not v._load_worker_settings()['workerOfferPending']


def test_failed_hub_registration_cannot_claim_success(tmp_path):
    v=_Viewport(tmp_path);v.reject_registration=True
    response,kw=_sync(v)
    assert response['ok'] is False and kw['status']==400
    assert len(v.outbound)==1 and any('failed' in x[2] for x in v.closes)
    settings=v._load_worker_settings()
    assert settings['workerOfferStatus']['status']=='failed'
    assert settings['workerRegisteredId']==''
    assert settings['signedWorkerConnection']['hub_registration_status']=='failed'


def test_settings_network_changed_during_registration_fails_closed(tmp_path):
    v=_Viewport(tmp_path)
    def mutate():
        s=v._load_worker_settings()
        s['selectedNetwork']='test'
        s['workerAutoConnectNetwork']='test'
        v._save_worker_settings(s)
    v.after_send=mutate
    response,kw=_sync(v)
    assert response['ok'] is False
    assert v._load_worker_settings()['workerRegisteredId']==''
    assert any(x[2]=='offer_authentication_failed' for x in v.closes)


def test_server_owned_intent_survives_ordinary_worker_settings_save(tmp_path):
    v=_Viewport(tmp_path);v.normal=False
    assert _sync(v)[0]['status']=='deferred'
    before=v._load_worker_settings()['workerOfferIntent']
    v._save_worker_settings({'selectedNetwork':'dev','workerAutoConnectNetwork':'dev','sellerEnabled':True,'remoteEnabled':True})
    assert v._load_worker_settings()['workerOfferIntent']==before
    assert v._load_worker_settings()['workerOfferPending'] is True


def test_old_worker_page_rest_endpoint_and_transport_are_deleted():
    from main_computer import viewport_route_dispatch
    from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin
    source=Path(viewport_route_dispatch.__file__).read_text(encoding='utf-8')
    js=Path(__file__).parents[1]/'main_computer/web/applications/scripts/worker.js'
    assert '/api/applications/worker/register-offer' not in source
    assert '/api/applications/worker/register-offer' not in js.read_text(encoding='utf-8')
    assert '/api/applications/worker/offer-sync' in source
    assert '/api/applications/worker/offer-sync' in js.read_text(encoding='utf-8')
    assert not hasattr(ViewportEnergyRoutesMixin,'_post_worker_registration_to_hub')
    assert not hasattr(ViewportEnergyRoutesMixin,'_handle_worker_offer_register')


def test_wrong_posted_network_is_rejected_before_authentication(tmp_path):
    v = _Viewport(tmp_path, network='dev')
    v.body['network'] = 'mainnet'
    response, kwargs = _sync(v)
    assert response['ok'] is False and kwargs['status'] == 400
    assert not v.outbound
    assert not v._load_worker_settings()['workerOfferPending']


def test_deferred_offer_reports_disconnected_not_live_session(tmp_path):
    v = _Viewport(tmp_path)
    v.normal = False
    result, _ = _sync(v)
    assert result['status'] == 'deferred'
    state = v._load_worker_settings()
    assert state['workerConnectionStatus'] == 'disconnected'
    assert state['workerOfferPending'] is True
    assert v.outbound == []


def test_offer_update_refuses_session_close_deferred_by_racing_active_work(tmp_path):
    v = _Viewport(tmp_path)
    settings = v._load_worker_settings()
    settings['signedWorkerConnection'] = {'wallet_address': WALLET, 'hub_url': v.body['hub_url']}
    v._save_worker_settings(settings)
    v._close_worker_live_session = lambda **kw: {'close_deferred': True}
    result, kwargs = _sync(v)
    assert result['ok'] is False and kwargs['status'] == 400
    assert 'Worker job began' in result['error']
    assert not v.outbound
    assert not v._load_worker_settings()['workerOfferPending']
