"""P5.4f network binding remains in force after authenticated P5.4h migration."""
from __future__ import annotations

from types import SimpleNamespace
import pytest
from main_computer.hub_interface import resolve_hub_destination
from main_computer.viewport_routes_energy import ViewportEnergyRoutesMixin


class _Probe(ViewportEnergyRoutesMixin):
    def __init__(self, network, body):
        self.network=network
        self.body=body
        self.server=SimpleNamespace(config=SimpleNamespace(hub_url='http://127.0.0.1:8871'), signal=lambda *a,**kw:None)
    def _load_worker_settings(self):
        return {'selectedNetwork': self.network, 'workerAutoConnectNetwork':self.network}


@pytest.mark.parametrize('network',['dev','test','testnet','mainnet'])
def test_offer_destination_uses_saved_worker_network(network):
    profile=resolve_hub_destination({'selectedNetwork': network})
    p=_Probe(network,{'hub_url':profile.hub_url})
    assert p._worker_offer_registration_destination(p.body)==profile


@pytest.mark.parametrize('network,foreign',[
    ('dev','https://mainnet-hub.greatlibrary.io'),
    ('test','http://127.0.0.1:8871'),
    ('testnet','https://mainnet-hub.greatlibrary.io'),
    ('mainnet','https://testnet-hub.greatlibrary.io'),
])
def test_foreign_hub_url_fails_before_registration(network,foreign):
    p=_Probe(network,{'hub_url':foreign})
    with pytest.raises(ValueError,match='does not match selected network'):
        p._worker_offer_registration_destination(p.body)


@pytest.mark.parametrize('network',['none','slh'])
def test_no_selection_and_managed_slh_never_fall_back(network):
    p=_Probe(network,{'hub_url':'http://127.0.0.1:8871'})
    with pytest.raises(ValueError):
        p._worker_offer_registration_destination(p.body)


def test_missing_request_url_resolves_only_from_saved_selection():
    p=_Probe('dev',{})
    assert p._worker_offer_registration_destination({}).network=='dev'


def test_untrusted_network_name_does_not_override_saved_network():
    p=_Probe('mainnet',{'hub_url':'https://mainnet-hub.greatlibrary.io','selectedNetwork':'dev'})
    assert p._worker_offer_registration_destination(p.body).network=='mainnet'


def test_readonly_verifier_is_safe(capsys):
    from tools.hub_worker_offer_route_verify import main,verify
    assert verify()['ok'] is True
    assert main([])==0
    assert '"obsolete_viewport_offer_route_removed": true' in capsys.readouterr().out


def test_deprecated_rest_viewport_route_is_absent_but_new_route_rejects_unselected(tmp_path,monkeypatch):
    import json,threading
    from urllib.error import HTTPError
    from urllib.request import Request,urlopen
    from main_computer.config import MainComputerConfig
    from main_computer.viewport_server import ViewportServer
    monkeypatch.chdir(tmp_path)
    viewport=ViewportServer(('127.0.0.1',0),MainComputerConfig(workspace=tmp_path/'work'),verbose=False)
    thread=threading.Thread(target=viewport.serve_forever,daemon=True)
    thread.start()
    try:
        for url,status in (('/api/applications/worker/register-offer',404),('/api/applications/worker/offer-sync',400)):
            req=Request(f'http://127.0.0.1:{viewport.server_port}'+url,data=json.dumps({'worker':{'model':'mock'}}).encode(),headers={'Content-Type':'application/json'},method='POST')
            with pytest.raises(HTTPError) as exc:
                urlopen(req,timeout=3)
            assert exc.value.code==status
    finally:
        viewport.shutdown();viewport.server_close();thread.join(timeout=3)
