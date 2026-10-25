"""Captain pending bridge audit never mutates ledger, chain, or recovery hints."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('captain_bridge_read_only_audit', ROOT / 'tools/worker_browser_acceptance/captain_bridge_audit.py')
assert SPEC and SPEC.loader
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)

WALLET = '0x' + 'a' * 40
DEPOSIT = '0x' + 'b' * 64
TXHASH = '0x' + 'c' * 64
CONTRACT = '0x' + 'e' * 40
AMOUNT = '1000000000000000000'


def record():
    return {'deposit_id': DEPOSIT, 'transaction_hash': TXHASH,
            'wallet_address': WALLET, 'amount_credit_wei': AMOUNT,
            'receipt': {'status': '0x1', 'blockNumber': '0x5a9'}}


def response_words(*, exists=1, completed=1, amount=AMOUNT, wallet=WALLET):
    values = [exists, completed, int(wallet, 16), int(wallet, 16), int(amount)]
    return '0x' + ''.join(f'{v:064x}' for v in values)


class FakeRpc:
    def __init__(self, *, has_receipt=True, wrong_chain=False, amount=AMOUNT):
        self.calls = []
        self.has_receipt = has_receipt
        self.wrong_chain = wrong_chain
        self.amount = amount

    def chain_id(self):
        self.calls.append('eth_chainId')
        return 1 if self.wrong_chain else 42424241

    def rpc(self, method, params=None):
        self.calls.append(method)
        if method == 'eth_getCode': return '0x600a'
        if method == 'eth_getTransactionByHash':
            return {'from': WALLET, 'to': CONTRACT, 'value': hex(int(AMOUNT))}
        raise AssertionError('Unexpected potentially mutating RPC method: ' + method)

    def transaction_receipt(self, tx):
        self.calls.append('eth_getTransactionReceipt')
        if not self.has_receipt: return None
        return {'transactionHash': TXHASH, 'status': '0x1', 'to': CONTRACT, 'blockNumber': '0x5a9', 'logs': []}

    def eth_call(self, *, to, data):
        self.calls.append('eth_call')
        assert to == CONTRACT
        assert data.startswith('0x' + app._DEPOSIT_RECORD_SELECTOR)
        assert data.endswith(DEPOSIT[2:])
        return response_words(amount=self.amount)


def fake_hub(url, route, *, timeout_s):
    assert url == 'https://testnet-hub.greatlibrary.io'
    if route == '/api/hub/v1/credits/wallet-funding/completion':
        return {'ok': True, 'deployment': {'chain_id': 42424241, 'contract_address': CONTRACT}}
    if route.startswith('/api/hub/v1/credits/balance?'):
        assert 'wallet_address=' in route
        return {'ok': True, 'account': {'wallet_address': WALLET,
                  'available_credit_wei': '0', 'bridge_completed_credit_wei': AMOUNT, 'spent_credit_wei': AMOUNT}}
    raise AssertionError('Unexpected, possibly mutating Hub path ' + route)


def setup_profile(monkeypatch):
    profile = SimpleNamespace(network_key='testnet', chain_id=42424241,
        chain_rpc_url='https://testnet-rpc.greatlibrary.io',
        hub_url='https://testnet-hub.greatlibrary.io', contracts={})
    monkeypatch.setattr(app, 'load_hub_network_registry', lambda: SimpleNamespace(get=lambda name: profile))


def setup_record(tmp_path):
    folder = tmp_path / 'runtime/state/captain/pending-bridge-deposits'
    folder.mkdir(parents=True)
    p = folder / ('1' * 64 + '.json')
    p.write_text(json.dumps(record()), encoding='utf-8')
    return p


def test_chain_receipt_and_escrow_proof_preserves_record_and_hub_attribution_uncertain(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    path = setup_record(tmp_path)
    original = path.read_bytes()
    monkeypatch.setattr(app, '_verify_receipt_event', lambda receipt, rec, contract: True)
    rpc = FakeRpc()
    report = app.audit_pending(root=tmp_path, rpc=rpc, hub_get=fake_hub)
    assert report['records'][0]['chainProof'] == 'verified-on-current-chain'
    assert report['records'][0]['escrowRecord']['completed'] is True
    assert report['records'][0]['hubDepositAttribution'] == 'not-verifiable-via-current-read-only-hub-api'
    assert report['hubAccountAggregates'][WALLET]['spent_credit_wei'] == AMOUNT
    assert report['authorizesRetireOrNewDeposit'] is False
    assert path.read_bytes() == original
    assert set(rpc.calls) == {'eth_chainId', 'eth_getCode', 'eth_getTransactionReceipt', 'eth_getTransactionByHash', 'eth_call'}


def test_absent_live_receipt_cannot_be_considered_resolved(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    p = setup_record(tmp_path)
    report = app.audit_pending(root=tmp_path, rpc=FakeRpc(has_receipt=False), hub_get=fake_hub)
    assert report['records'][0]['chainProof'] == 'not-verified'
    assert report['records'][0]['reason'] == 'transaction_receipt_absent_from_live_chain'
    assert p.exists()


def test_wrong_chain_cannot_consume_local_record(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    setup_record(tmp_path)
    rpc = FakeRpc(wrong_chain=True)
    report = app.audit_pending(root=tmp_path, rpc=rpc, hub_get=fake_hub)
    assert report['records'][0]['reason'] == 'live_rpc_chain_mismatch'
    assert rpc.calls == ['eth_chainId']
    assert report['authorizesRetireOrNewDeposit'] is False


def test_escrow_amount_mismatch_fails_closed(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    setup_record(tmp_path)
    report = app.audit_pending(root=tmp_path, rpc=FakeRpc(amount='500'), hub_get=fake_hub)
    assert report['records'][0]['chainProof'] == 'not-verified'
    assert report['records'][0]['reason'] == 'escrow_deposit_record_mismatch'


def test_hub_deployment_mismatch_fails_closed(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    setup_record(tmp_path)
    def mismatch(url, route, *, timeout_s):
        return {'ok': True, 'deployment': {'chain_id': 1, 'contract_address': CONTRACT}}
    with pytest.raises(ValueError, match='identity_mismatch'):
        app.audit_pending(root=tmp_path, rpc=FakeRpc(), hub_get=mismatch)


def test_unreadable_local_record_is_never_retired(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    p = setup_record(tmp_path)
    p.write_text('not json', encoding='utf-8')
    report = app.audit_pending(root=tmp_path, rpc=FakeRpc(), hub_get=fake_hub)
    assert report['records'][0]['chainProof'] == 'not-verified'
    assert 'read_only_evidence_unavailable' in report['records'][0]['reason']
    assert p.read_text() == 'not json'


def test_record_reader_accepts_only_valid_public_identifiers():
    assert app._record_data(record())['amount_credit_wei'] == AMOUNT
    with pytest.raises(ValueError): app._record_data({**record(), 'transaction_hash': 'invalid'})
    with pytest.raises(ValueError): app._record_data({**record(), 'amount_credit_wei': '0'})
