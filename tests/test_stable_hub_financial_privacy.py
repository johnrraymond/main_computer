"""Financial receipts must not duplicate plaintext semantic content."""
import json
from urllib.error import HTTPError
import threading
from pathlib import Path
from urllib.request import urlopen
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore, StableHubPayoutLedgerDirectory

TOPOLOGY = Path('deploy/hub-topology/dev-topology.json')


def test_earning_stores_canonical_result_digest_not_semantic_payload():
    store=InMemoryStableWorkerSessionStore()
    topo=load_stable_hub_topology(TOPOLOGY)
    ledger=StableHubPayoutLedgerDirectory(topology=topo,hub_id='dev-hub1',store=store)
    wallet='0x'+'15'*20
    account=wallet_account_id(wallet)
    ledger.fund_account(account_id=account,wallet_address=wallet,credits='2',replace=True)
    original={'status':'success','response':{'model':'test-model','content':'PRIVATE USER PROMPT AND RESULT'}}
    result=ledger.spend_request_credit(
        account_id=account,wallet_address=wallet,
        request_id='privacy-test-1',session_id='privacy-session',run_id='privacy-run',
        worker_id='worker_privacy',selected_price={'amount':'1','unit':'credit'},
        requester_max_price={'amount':'2','unit':'credit'},partition='ring-1',result=original)
    rows=store.load()['worker_earnings']
    entry=rows[result['worker_earning']['earning_id']]
    assert entry['result_sha256'] and len(entry['result_sha256'])==64
    assert 'result' not in entry
    assert 'PRIVATE USER PROMPT AND RESULT' not in json.dumps(rows)


def test_public_discovery_omits_storage_network_and_internal_peers():
    topo=load_stable_hub_topology(TOPOLOGY)
    hub=create_stable_hub_server(topology=topo,hub_id='dev-hub1',bind_host='127.0.0.1',bind_port=0,
                                 multisession_key_store=None,worker_session_store=InMemoryStableWorkerSessionStore())
    t=threading.Thread(target=hub.serve_forever,daemon=True);t.start()
    try:
        base=f'http://127.0.0.1:{hub.server_port}'
        with urlopen(base+'/api/hub/v1/hub-identity',timeout=3) as r:
            identity=json.loads(r.read())
        with urlopen(base+'/api/hub/v1/topology',timeout=3) as r:
            topology=json.loads(r.read())['topology']
        assert identity['hub_id']=='dev-hub1'
        assert identity['contract']['auth']=='multisession-wallet'
        assert 'storage' not in identity
        assert set(identity['network']) == {'chain_id','network_key','network_kind'}
        assert 'peer_hubs' not in identity
        assert 'storage' not in topology and 'network' not in topology
        assert '.foundationdb' not in json.dumps([identity,topology])
    finally:
        hub.shutdown();hub.server_close();t.join(timeout=2)


def test_runtime_attestation_proves_loaded_config_without_disclosing_it(monkeypatch):
    from main_computer.stable_hub_topology import (
        RUNTIME_ATTESTATION_ENV, RUNTIME_ATTESTATION_SCHEMA, stable_hub_runtime_attestation,
    )
    secret = "56" * 32
    monkeypatch.setenv(RUNTIME_ATTESTATION_ENV, secret)
    topo = load_stable_hub_topology(TOPOLOGY)
    hub = create_stable_hub_server(
        topology=topo, hub_id="dev-hub1", bind_host="127.0.0.1", bind_port=0,
        worker_session_store=InMemoryStableWorkerSessionStore(),
    )
    t = threading.Thread(target=hub.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{hub.server_port}"
        nonce = "cd" * 16
        with urlopen(base + "/api/hub/v1/runtime-attestation?nonce=" + nonce, timeout=3) as r:
            payload = json.loads(r.read())
        assert payload == {
            "ok": True, "schema": RUNTIME_ATTESTATION_SCHEMA,
            "hub_id": "dev-hub1", "nonce": nonce,
            "hmac_sha256": stable_hub_runtime_attestation(
                key_hex=secret, nonce=nonce, hub_id="dev-hub1",
                network=topo.network, storage=topo.storage,
            ),
        }
        assert "namespace" not in json.dumps(payload)
        assert "cluster_file" not in json.dumps(payload)
        assert "chain_rpc_url" not in json.dumps(payload)
        assert secret not in json.dumps(payload)
        for bad_nonce in ("xyz", "bb" * 40):
            try:
                urlopen(base + "/api/hub/v1/runtime-attestation?nonce=" + bad_nonce, timeout=3)
            except HTTPError as exc:
                assert exc.code == 400
            else:
                raise AssertionError("invalid nonce accepted")
    finally:
        hub.shutdown()
        hub.server_close()
        t.join(timeout=2)
