"""R02-D diagnostic acceptance; no deployed changes or financial transactions.

Live read-only checks require MC_R02D_LIVE_READONLY=1. Testnet MSK issuance
and revocation require MC_R02D_TESTNET_MSK=1 (disposable wallet only).
Passing the local case is not certification of the public Testnet.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import time

import pytest

from main_computer.hub_networks import load_hub_network_registry
from main_computer.multisession_key_signing import build_personal_sign_blob
from main_computer.slh.marketplace_auto import AutomaticMarketplace, load_authorization, read_settings, update_settings
from main_computer.slh.organization_signer import resolve_organization_signer, SIGNER_ENV
from main_computer.slh.organization_wallet import HubCreditReader, OrganizationWallet
from main_computer.slh.storage import secure_machine_state_dir
from tests.test_slh_marketplace_automatic import ControlledProxy
from tests.test_hub_network_client_smoke_script import load_smoke_client

# Accepted membership observed in the R02 deployment inspection on Nov 25.
# Deliberately explicit release baseline; update only after accepted membership changes.
MEMBERS = {"mainnet": {"mainneta-hub1", "mainnetc-hub1"}, "testnet": {"testnetb-hub1"}}
ROOT = Path(__file__).resolve().parents[1]


def _check_membership(report, expected, *, origin, responder=None):
    actual = set(report['advertised_hub_ids'])
    assert actual == expected, f'{origin}: topology missing {sorted(expected-actual)}; unexpected {sorted(actual-expected)}'
    for field in ('health_hub_id', 'identity_hub_id', 'topology_hub_id'):
        assert report[field] in expected, f'{origin}: invalid {field}: {report[field]}'
        if responder is not None:
            assert report[field] == responder, f'{origin}: wrong concrete responder for {field}'


def test_membership_gate_detects_missing_peer():
    row = {'advertised_hub_ids': ['mainneta-hub1'], 'health_hub_id': 'mainneta-hub1',
           'identity_hub_id': 'mainneta-hub1', 'topology_hub_id': 'mainneta-hub1'}
    with pytest.raises(AssertionError, match='mainnetc-hub1'):
        _check_membership(row, MEMBERS['mainnet'], origin='fixture')


@pytest.mark.parametrize('network,hub_id', [('mainnet', None), ('mainnet', 'mainneta-hub1'),
                                          ('mainnet', 'mainnetc-hub1'), ('testnet', None),
                                          ('testnet', 'testnetb-hub1')])
def test_live_hub_membership(network, hub_id):
    if os.environ.get('MC_R02D_LIVE_READONLY') != '1':
        pytest.skip('NOT_RUN: set MC_R02D_LIVE_READONLY=1 to query deployed Hubs')
    profile = load_hub_network_registry().get(network)
    url = f'https://{hub_id}.greatlibrary.io' if hub_id else profile.hub_url
    result = load_smoke_client().verify_stable_hub(profile, hub_url=url, timeout=10)
    _check_membership(result, MEMBERS[network], origin=url, responder=hub_id)
    assert result['cluster_id'] == f'main-computer-{network}-hubs'


@pytest.mark.parametrize('network', ['testnet', 'mainnet'])
def test_live_validator_set_and_block_progress(network):
    if os.environ.get('MC_R02D_LIVE_READONLY') != '1':
        pytest.skip('NOT_RUN: set MC_R02D_LIVE_READONLY=1; requires current Mother state')
    from tools import mother_preflight_paranoia_validator_set as validator
    from main_computer.hub_credit_bridge_completion import JsonRpcClient
    args = validator.build_parser().parse_args(['--network', network])
    evidence = validator.run(args)
    assert evidence['status'] == 'pass', f"{network}: {evidence.get('status')}"
    assert evidence['validator_set_matches'] is True
    assert evidence['summary']['live_mutation_performed'] is False
    profile = load_hub_network_registry().get(network)
    client = JsonRpcClient(profile.chain_rpc_url, timeout_s=10)
    assert int(client.rpc('eth_chainId', []), 16) == profile.chain_id
    first = int(client.rpc('eth_blockNumber', []), 16)
    end = time.monotonic() + 60
    while time.monotonic() < end:
        if int(client.rpc('eth_blockNumber', []), 16) > first:
            return
        time.sleep(2)
    pytest.fail(f'{network}: no observed block advance within 60 seconds; quorum liveness unverified')


def _testnet_wallet_roundtrip(state, monkeypatch, transport):
    """Use the actual financial controller and protected new state; capture issued
    keys for cleanup even if the controller fails before persisting authorization.
    No spending, advertising, funding, resident LMS or Mainnet operations.
    """
    profile = load_hub_network_registry().get('testnet')
    assert profile.chain_id == 42424241 and profile.hub_url == 'https://testnet-hub.greatlibrary.io'
    assert not state.exists()
    secure_machine_state_dir(state)
    monkeypatch.delenv(SIGNER_ENV, raising=False)
    monkeypatch.delenv('MAIN_COMPUTER_RESERVE_WALLET_ADDRESS', raising=False)
    issued = set()
    signer = None
    cluster = 'main-computer-testnet-hubs'
    allowed = {
        ('GET', '/api/hub/v1/hub-identity'),
        ('GET', '/api/hub/v1/credits/balance'),
        ('POST', '/api/hub/v1/credits/multisession-keys/request'),
        ('POST', '/api/hub/v1/credits/multisession-keys/validate'),
        ('POST', '/api/hub/v1/credits/multisession-keys/revoke'),
    }
    def observed(self, method, path, data=None, *, bearer_token=''):
        assert self.scheme == 'https' and self.host == 'testnet-hub.greatlibrary.io' and self.port == 443
        assert (method, path.split('?')[0]) in allowed, 'unexpected financial test request refused'
        if path.endswith('/request'):
            # Do not permit a second issuance during this restart test.
            assert not issued, 'unexpected duplicate MSK issuance'
        result = transport(self, method, path, data, bearer_token=bearer_token)
        if path.endswith('/request'):
            key = (result.get('key') or {}).get('id')
            if key:
                issued.add(key)
        return result
    with monkeypatch.context() as m:
        m.setattr(HubCreditReader, 'request', observed)
        wallet = OrganizationWallet(state)
        proxy = ControlledProxy()
        controller = AutomaticMarketplace(proxy, state, wallet_service=wallet,
                                          financial_startup=True, provision_wallet=True)
        update_settings(state, {'hub_url': profile.hub_url}, 0)
        try:
            # Provision inside the actual controller, not with the resident signer.
            controller.reconcile()
            status = controller.status()
            assert status['financial_readiness']['state'] == 'READY', status['financial_readiness'].get('reason')
            signer = resolve_organization_signer(wallet)
            binding = status['organization_wallet']['binding']
            assert status['financial_readiness']['network'] == 'testnet'
            assert status['organization_wallet']['state'] == 'CURRENT'
            assert binding['chain_id'] == str(profile.chain_id) and binding['hub_url'] == profile.hub_url
            assert binding['cluster_id'] == cluster and binding['wallet_address'] == signer[0]
            assert binding['trust_mode'] == 'SYSTEM_TLS'
            balance = status['organization_wallet']['balance']
            assert all(int(balance[k]) == 0 for k in ('available_credit_wei', 'held_credit_wei', 'spent_credit_wei', 'bridge_completed_credit_wei'))
            assert not read_settings(state)['enabled'] and not proxy.calls
            key = load_authorization(state)['multisession_key_id']
            assert bool(key in issued) and len(issued) == 1
            controller.stop()
            restarted = AutomaticMarketplace(ControlledProxy(), state, wallet_service=OrganizationWallet(state),
                                             financial_startup=True, provision_wallet=True)
            try:
                restarted.reconcile()
                second = restarted.status()
                assert second['financial_readiness']['state'] == 'READY', second['financial_readiness'].get('reason')
                assert second['organization_wallet']['binding'] == binding
                assert bool(load_authorization(state)['multisession_key_id'] == key)
                assert bool(resolve_organization_signer(restarted.wallet_service) == signer)
                assert len(issued) == 1 and not restarted.market.calls
                assert bool(key not in json.dumps(second) and signer[1] not in json.dumps(second)), "credential disclosed in status"
            finally:
                restarted.stop()
        finally:
            controller.stop()
            # Preserve protected disposable state if cleanup cannot be verified.
            if issued:
                signer = signer or resolve_organization_signer(wallet)
                reader = HubCreditReader(profile.hub_url)
                for key in issued:
                    now = datetime.now(timezone.utc)
                    message = {'purpose': 'request_multi_session_key', 'action': 'revoke_multisession_key',
                        'key_id': key, 'wallet_address': signer[0], 'cluster_id': cluster,
                        'chain_id': str(profile.chain_id), 'request_id': 'r02d-revoke-' + secrets.token_hex(16),
                        'issued_at': now.isoformat(), 'expires_at': (now + timedelta(minutes=5)).isoformat()}
                    signed = build_personal_sign_blob(message=message, private_key=signer[1],
                                                     wallet_address=signer[0], chain_id=str(profile.chain_id))
                    result = reader.request('POST', '/api/hub/v1/credits/multisession-keys/revoke', {'signed_request': signed})
                    assert result.get('revoked') is True, 'disposable MSK revocation failed; preserve isolated state'
                    result = reader.request('POST', '/api/hub/v1/credits/multisession-keys/validate',
                        {'multisession_authorization': {'kind': 'multisession_key', 'multisession_key_id': key}})
                    assert result.get('valid') is False, 'disposable MSK revocation unverified'
    shutil.rmtree(state)


def test_isolated_testnet_wallet_startup_and_reopen(tmp_path, monkeypatch):
    """Real local Hub, synthetic storage, simulated public HTTPS transport."""
    from main_computer.stable_hub import create_stable_hub_server
    from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
    from main_computer.stable_hub_topology import load_stable_hub_topology
    profile = load_hub_network_registry().get('testnet')
    doc = json.loads((ROOT / 'deploy/hub-topology/dev-topology.json').read_text())
    doc['cluster_id'] = 'main-computer-testnet-hubs'
    doc['network'].update(network_key='testnet', network_kind='testnet', chain_id=str(profile.chain_id),
                          chain_rpc_url=profile.chain_rpc_url)
    doc['entry_urls'] = [profile.hub_url]
    doc['hubs'] = [{'hub_id':'testnetb-hub1','hub_url':profile.hub_url,'public_url':profile.hub_url,
                    'roles':['entry','execution']}]
    p = tmp_path / 'topology.json'
    p.write_text(json.dumps(doc))
    server = create_stable_hub_server(topology=load_stable_hub_topology(p), hub_id='testnetb-hub1',
        bind_host='127.0.0.1', bind_port=0, multisession_key_store=InMemoryStableMultiSessionKeyStore(),
        worker_session_store=InMemoryStableWorkerSessionStore())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = HubCreditReader.request
    local = HubCreditReader(f'http://127.0.0.1:{server.server_port}')
    def local_transport(_reader, method, path, data=None, *, bearer_token=''):
        return original(local, method, path, data, bearer_token=bearer_token)
    try:
        _testnet_wallet_roundtrip(tmp_path / 'disposable-state', monkeypatch, local_transport)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_live_testnet_wallet_startup_and_reopen(tmp_path, monkeypatch):
    if os.environ.get('MC_R02D_TESTNET_MSK') != '1':
        pytest.skip('NOT_RUN: explicitly authorize disposable Testnet MSK issue/revoke with MC_R02D_TESTNET_MSK=1')
    _testnet_wallet_roundtrip(tmp_path / 'disposable-state', monkeypatch, HubCreditReader.request)
