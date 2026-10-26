"""Read-only Stable Hub historical deposit attribution without chain writes."""
from __future__ import annotations
import json
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from main_computer.stable_hub import (
    create_stable_hub_server, read_stable_hub_bridge_deposit_provenance,
)
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import StableHubPayoutLedgerDirectory
from main_computer.hub_credit_indexer import wallet_account_id
from test_stable_hub_credit_bridge_patch2 import (
    MemoryStore, WALLET, DEPOSIT, SECOND_DEPOSIT, CONTRACT, UNIT, FUNDING_TX,
)
from pathlib import Path


def _query(base, *, deposit=DEPOSIT, wallet=WALLET, contract=CONTRACT, chain='42424240'):
    path = '/api/hub/v1/credits/bridge-deposit?' + urlencode({
        'deposit_id': deposit, 'wallet_address': wallet,
        'contract_address': contract, 'chain_id': chain,
    })
    request = Request(base + path, method='GET')
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _prepared_credit_data():
    return {
        'account_id': wallet_account_id(WALLET), 'wallet_address': WALLET.lower(),
        'deposit_id': DEPOSIT.lower(), 'chain_id': 42424240, 'contract_address': CONTRACT.lower(),
        'deposit_amount_units': str(UNIT), 'status': 'bridge_completed',
        'credits_granted_wei': str(UNIT), 'funding_tx_hash': FUNDING_TX,
        'completion_tx_hash': '0x' + '77'*32, 'created_at': '2026-10-24T12:00:00+00:00',
        'metadata': {'secret-signing-material-must-not-leak': 'confidential'},
    }


def test_deposit_lookup_validates_identity_and_is_read_only():
    topology = load_stable_hub_topology(Path('deploy/hub-topology/dev-topology.json'))
    store = MemoryStore()
    ledger = StableHubPayoutLedgerDirectory(topology=topology, hub_id='dev-hub1', store=store)
    key = f'42424240:{CONTRACT.lower()}:{DEPOSIT.lower()}'
    record = _prepared_credit_data()
    store.save({'payout_deposits': {key: record},
                'payout_deposit_intents': {key: {**record, 'status': 'finalized'}}})
    before = store.load()
    proof = read_stable_hub_bridge_deposit_provenance(
        ledger, chain_id='42424240', contract_address=CONTRACT,
        deposit_id=DEPOSIT, wallet_address=WALLET)
    assert proof['found'] is True and proof['completed'] is True
    assert proof['scope'] == 'historical-hub-fdb-not-current-chain-proof'
    assert proof['credit']['funding_tx_hash'] == FUNDING_TX
    assert proof['credit']['credits_granted_wei'] == str(UNIT)
    assert 'metadata' not in proof['credit']
    assert 'confidential' not in json.dumps(proof)
    assert store.load() == before
    assert read_stable_hub_bridge_deposit_provenance(
        ledger, chain_id='42424240', contract_address=CONTRACT,
        deposit_id=DEPOSIT, wallet_address='0x'+'ff'*20)['found'] is False
    assert read_stable_hub_bridge_deposit_provenance(
        ledger, chain_id='42424240', contract_address=CONTRACT,
        deposit_id=SECOND_DEPOSIT, wallet_address=WALLET)['found'] is False
    assert store.load() == before


def test_deposit_lookup_http_contract_and_no_mutation():
    topology = load_stable_hub_topology(Path('deploy/hub-topology/dev-topology.json'))
    store = MemoryStore()
    key = f'42424240:{CONTRACT.lower()}:{DEPOSIT.lower()}'
    store.save({'payout_deposits': {key: _prepared_credit_data()}})
    server = create_stable_hub_server(
        topology=topology, hub_id='dev-hub1', bind_host='127.0.0.1', bind_port=0,
        worker_session_store=store,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f'http://127.0.0.1:{server.server_port}'
        before = store.load()
        status, body = _query(base)
        assert status == 200
        assert body['readOnly'] is True and body['completed'] is True
        assert body['credit']['funding_tx_hash'] == FUNDING_TX
        assert _query(base, deposit=SECOND_DEPOSIT)[1]['found'] is False
        assert _query(base, deposit='not-a-deposit')[0] == 400
        assert _query(base, wallet='bad-wallet')[0] == 400
        assert store.load() == before
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
