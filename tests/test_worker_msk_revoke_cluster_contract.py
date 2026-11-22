"""Exercise the real viewport cache functions without importing the absent game bundle."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'main_computer/viewport_routes_energy.py'


def _route_methods():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    route = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == 'ViewportEnergyRoutesMixin')
    names = {'_normalize_worker_multisession_key_record', '_public_worker_multisession_key_record',
             '_store_worker_multisession_key_from_hub_result'}
    funcs = [n for n in route.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(funcs) == len(names)
    cls = ast.ClassDef(name='Cache', bases=[], keywords=[], body=funcs, decorator_list=[])
    namespace = {'Any': Any, 'datetime': datetime, 'timezone': timezone}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),str(SOURCE),'exec'),namespace)
    instance=namespace['Cache']()
    instance._clean_hub_url=lambda url,allow_empty=False: url.rstrip('/')
    instance._normalize_worker_wallet_address=lambda addr: str(addr).lower()
    data={'keys':{}}
    instance._load_worker_multisession_key_cache=lambda: data
    instance._save_worker_multisession_key_cache=lambda new_data: data.update(new_data)
    return instance,data


def test_issued_cluster_survives_cache_and_redacted_load():
    route, data = _route_methods()
    key={'id':'msk_test', 'wallet_address':'0x'+'ab'*20, 'status':'active',
         'chain_id':'42424241','created_at':'2026-11-21T00:00:00Z'}
    stored=route._store_worker_multisession_key_from_hub_result(
        hub_url='https://testnet-hub.example/',result={'key':key,'cluster_id':'testnet-cluster'})
    assert stored['cluster_id']=='testnet-cluster'
    assert data['keys']['msk_test']['cluster_id']=='testnet-cluster'
    public=route._public_worker_multisession_key_record(stored,reveal_key=False)
    assert public['cluster_id']=='testnet-cluster'
    assert 'id' not in public


def test_public_loaded_key_exposes_cluster_but_not_bearer_id():
    route,_=_route_methods()
    key={'id':'msk_test', 'wallet_address':'0x'+'ab'*20, 'status':'active',
         'cluster_id':'testnet-cluster'}
    stored=route._normalize_worker_multisession_key_record(key,hub_url='https://testnet-hub.example')
    public=route._public_worker_multisession_key_record(stored)
    assert public['cluster_id']=='testnet-cluster'
    assert 'id' not in public


def test_real_hub_service_issue_cache_revoke_roundtrip():
    """Real Testnet-chain Hub contract; only wallet/Hub transport are simulated."""
    from datetime import timedelta
    from types import SimpleNamespace
    from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address
    from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore, StableHubMultiSessionKeyService

    private='0x'+'11'*32
    wallet=private_key_to_address(private)
    cluster='contract-test-cluster'
    hub=StableHubMultiSessionKeyService(
        topology=SimpleNamespace(network={'chain_id':42424241},cluster_id=cluster),
        hub_id='testnet-hub1',store=InMemoryStableMultiSessionKeyStore())
    now=datetime.now(timezone.utc)
    msg={'purpose':'request_multi_session_key','request_id':'new-key-1',
         'wallet_address':wallet,'chain_id':'0x28757b1',
         'user_slug':'a'*64,'issued_at':now.isoformat(),
         'expires_at':(now+timedelta(minutes=5)).isoformat()}
    issued=hub.request_key({'signed_request':build_personal_sign_blob(
        message=msg,private_key=private,wallet_address=wallet,chain_id='42424241')})
    route,_=_route_methods()
    saved=route._store_worker_multisession_key_from_hub_result(hub_url='https://testnet-hub.example',result=issued)
    assert saved['cluster_id']==cluster
    loaded=route._public_worker_multisession_key_record(saved)
    assert loaded['cluster_id']==cluster and 'id' not in loaded
    revocation={**msg,'action':'revoke_multisession_key',
                'key_id':issued['key']['id'],'cluster_id':loaded['cluster_id'],
                'request_id':'revoke:'+issued['key']['id']}
    signed=build_personal_sign_blob(message=revocation,private_key=private,
                                    wallet_address=wallet,chain_id='42424241')
    result=hub.revoke_key({'signed_request':signed})
    assert result['ok'] is True and result['revoked'] is True
