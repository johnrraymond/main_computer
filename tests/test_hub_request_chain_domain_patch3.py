"""Requester Stable Hub authorization must use Hub-advertised signing chain ID."""
from __future__ import annotations
from types import SimpleNamespace
import pytest
from main_computer import hub_cli_live_session as live
from main_computer.multisession_key_signing import private_key_to_address, verify_personal_sign_blob

KEY = '0x' + '1'.zfill(64)
WALLET = private_key_to_address(KEY)


def _wallet(monkeypatch):
    monkeypatch.setattr(live, '_officer_wallet', lambda selector: SimpleNamespace(address=WALLET, private_key=KEY))


def test_stable_requester_signs_mainnet_42424240(monkeypatch):
    _wallet(monkeypatch)
    monkeypatch.setattr(live, 'identify_hub_protocol', lambda url: {
        'service': 'main_computer.stable_hub',
        'cluster_id': 'main-computer-mainnet-hubs',
        'network': {'chain_id': 42424240},
    })
    signed=[]
    def post(url, path, body, *, timeout_s):
        v=verify_personal_sign_blob(body['signed_request'], expected_chain_id='42424240', max_age_minutes=15)
        assert v['wallet_address']==WALLET
        assert v['chain_id']=='42424240'
        assert v['message']['user_slug'].startswith('user_slug_')
        signed.append(v)
        return {'ok': True, 'key': {'id': 'msk-requester', 'status': 'active', 'wallet_address': WALLET}}
    monkeypatch.setattr(live,'_hub_post',post)
    auth=live.cli_request_authorization('https://mainnet-hub.greatlibrary.io',officer='o3')
    assert auth['chain_id']=='42424240'
    assert auth['cluster_id']=='main-computer-mainnet-hubs'
    assert auth['key_id']=='msk-requester'
    assert len(signed)==1


def test_experimental_requester_keeps_existing_signing_domain(monkeypatch):
    _wallet(monkeypatch)
    monkeypatch.setattr(live,'identify_hub_protocol', lambda url: {'service':'main_computer.exp_fdb_hub'})
    def post(url,path,body,*,timeout_s):
        v=verify_personal_sign_blob(body['signed_request'],expected_chain_id=live.HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID,max_age_minutes=15)
        assert 'user_slug' not in v['message']
        return {'ok': True,'key':{'id':'msk-exp','status':'active','wallet_address':WALLET}}
    monkeypatch.setattr(live,'_hub_post',post)
    auth=live.cli_request_authorization('http://127.0.0.1:8770',officer='o3')
    assert auth['chain_id']==live.HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID


@pytest.mark.parametrize('identity',[{'service':'main_computer.stable_hub','network':{}},
                                     {'service':'main_computer.stable_hub','network':{'chain_id':'not-a-chain'}},
                                     {'service':'unrecognized','network':{'chain_id':42424240}}])
def test_requester_refuses_unknown_signing_domain_before_post(monkeypatch,identity):
    _wallet(monkeypatch)
    monkeypatch.setattr(live,'identify_hub_protocol',lambda url:identity)
    monkeypatch.setattr(live,'_hub_post',lambda *a,**kw:pytest.fail('unexpected POST'))
    with pytest.raises(RuntimeError, match='(valid signing chain ID|unsupported request authorization service)'):
        live.cli_request_authorization('https://invalid.example',officer='o3')
