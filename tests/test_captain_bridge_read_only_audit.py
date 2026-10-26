"""Captain pending bridge audit never mutates ledger, chain, or recovery hints."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs

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
            'receipt': {'status': '0x1', 'blockNumber': '0x5a9', 'blockHash': '0x'+'f'*64}}


def response_words(*, exists=1, completed=1, amount=AMOUNT, wallet=WALLET):
    values = [exists, completed, int(wallet, 16), int(wallet, 16), int(amount)]
    return '0x' + ''.join(f'{v:064x}' for v in values)


class FakeRpc:
    def __init__(self, *, has_receipt=True, wrong_chain=False, amount=AMOUNT, tip=5000, canonical='0x'+'f'*64, transaction_known=True, escrow_exists=True):
        self.calls = []
        self.has_receipt = has_receipt
        self.wrong_chain = wrong_chain
        self.amount = amount
        self.tip = tip
        self.canonical = canonical
        self.transaction_known = transaction_known
        self.escrow_exists = escrow_exists

    def chain_id(self):
        self.calls.append('eth_chainId')
        return 1 if self.wrong_chain else 42424241

    def rpc(self, method, params=None):
        self.calls.append(method)
        if method == 'eth_getCode': return '0x600a'
        if method == 'eth_blockNumber': return hex(self.tip)
        if method == 'eth_getBlockByNumber': return {'hash': self.canonical}
        if method == 'eth_getTransactionByHash':
            if not self.transaction_known: return None
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
        return response_words(amount=self.amount, exists=int(self.escrow_exists))


def fake_hub(url, route, *, timeout_s):
    assert url == 'https://testnet-hub.greatlibrary.io'
    if route == '/api/hub/v1/credits/wallet-funding/completion':
        return {'ok': True, 'deployment': {'chain_id': 42424241, 'contract_address': CONTRACT}}
    if route.startswith('/api/hub/v1/credits/bridge-deposit?'):
        query = {key: value[0] for key,value in parse_qs(urlparse(route).query).items()}
        return {'ok': True, 'readOnly': True, 'found': False, 'completed': False,
                'deposit_id': query['deposit_id'], 'wallet_address': query['wallet_address'],
                'chain_id': int(query['chain_id']), 'contract_address': query['contract_address']}
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
    assert report['records'][0]['hubDepositAttribution'] == 'not_recorded_in_hub_fdb'
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


def test_missing_receipt_with_changed_canonical_block_preserves_hint(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    path = setup_record(tmp_path)
    before = path.read_bytes()
    rpc = FakeRpc(has_receipt=False, transaction_known=False, escrow_exists=False,
                  canonical='0x'+'d'*64)
    report = app.audit_pending(root=tmp_path, rpc=rpc, hub_get=fake_hub)
    row = report['records'][0]
    e = row['missingReceiptEvidence']
    assert row['reason'] == 'transaction_receipt_absent_from_live_chain'
    assert e['recordedBlockCanonical'] == 'different_canonical_block'
    assert e['recordedReceiptBlockHash'] == '0x'+'f'*64
    assert e['currentBlockHashAtReceiptHeight'] == '0x'+'d'*64
    assert e['transactionLookup'] == 'absent_from_current_rpc'
    assert e['escrowDepositState']['exists'] is False
    assert e['permitsNewDeposit'] is False
    assert report['authorizesRetireOrNewDeposit'] is False
    assert path.read_bytes() == before


def test_missing_receipt_without_reorg_evidence_is_still_unresolved(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    setup_record(tmp_path)
    rpc = FakeRpc(has_receipt=False, canonical='0x'+'f'*64, transaction_known=False,
                  escrow_exists=False)
    row = app.audit_pending(root=tmp_path, rpc=rpc, hub_get=fake_hub)['records'][0]
    e = row['missingReceiptEvidence']
    assert e['recordedBlockCanonical'] == 'matches'
    assert e['transactionLookup'] == 'absent_from_current_rpc'
    assert e['permitsNewDeposit'] is False


def test_missing_receipt_when_rpc_head_is_behind_saved_receipt_is_unresolved(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    setup_record(tmp_path)
    rpc = FakeRpc(has_receipt=False, tip=100, transaction_known=False, escrow_exists=False)
    e = app.audit_pending(root=tmp_path, rpc=rpc, hub_get=fake_hub)['records'][0]['missingReceiptEvidence']
    assert e['recordedBlockCanonical'] == 'current_head_below_recorded_receipt'
    assert 'eth_getBlockByNumber' not in rpc.calls
    assert e['permitsNewDeposit'] is False


def test_missing_receipt_with_escrow_present_is_not_redeposited(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    setup_record(tmp_path)
    rpc = FakeRpc(has_receipt=False, escrow_exists=True)
    e = app.audit_pending(root=tmp_path, rpc=rpc, hub_get=fake_hub)['records'][0]['missingReceiptEvidence']
    assert e['escrowDepositState']['exists'] is True
    assert e['permitsNewDeposit'] is False


def test_missing_receipt_history_lookup_failure_preserves_pending_record(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    path = setup_record(tmp_path)
    class FailingRpc(FakeRpc):
        def rpc(self, method, params=None):
            if method == 'eth_blockNumber': raise RuntimeError('rpc unreachable')
            if method == 'eth_getTransactionByHash': raise RuntimeError('tx rpc unreachable')
            return super().rpc(method, params)
        def eth_call(self, *, to, data): raise RuntimeError('escrow query denied')
    e = app.audit_pending(root=tmp_path, rpc=FailingRpc(has_receipt=False), hub_get=fake_hub)['records'][0]['missingReceiptEvidence']
    assert e['recordedBlockCanonical'] == 'read_only_block_lookup_unavailable'
    assert e['transactionLookup'] == 'read_only_transaction_lookup_unavailable'
    assert e['escrowDepositState'] == 'read_only_escrow_lookup_unavailable'
    assert path.exists()


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


def test_deposit_attribution_correlates_exact_hub_credit_and_tx_hash(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    path = setup_record(tmp_path)
    before = path.read_bytes()
    def hub(url, route, *, timeout_s):
        if '/credits/bridge-deposit?' in route:
            q = {k:v[0] for k,v in parse_qs(urlparse(route).query).items()}
            return {'ok': True, 'readOnly': True, 'found': True, 'completed': True,
                    'deposit_id': q['deposit_id'], 'wallet_address': q['wallet_address'],
                    'contract_address': q['contract_address'], 'chain_id': int(q['chain_id']),
                    'credit': {'status': 'bridge_completed', 'amount_credit_wei': AMOUNT,
                               'credits_granted_wei': AMOUNT, 'funding_tx_hash': TXHASH,
                               'created_at': '2026-10-24T12:00:00+00:00', 'completion_tx_hash': '0x'+'e'*64}}
        return fake_hub(url, route, timeout_s=timeout_s)
    report = app.audit_pending(root=tmp_path,
                               rpc=FakeRpc(has_receipt=False, transaction_known=False, escrow_exists=False,
                                           canonical='0x'+'d'*64), hub_get=hub)
    row = report['records'][0]
    assert row['missingReceiptEvidence']['recordedBlockCanonical'] == 'different_canonical_block'
    assert row['hubDepositAttribution'] == 'historical_fdb_credit_verified'
    assert row['hubDepositRecord']['fundingTransactionMatches'] is True
    assert report['authorizesRetireOrNewDeposit'] is False
    assert path.read_bytes() == before


def test_deposit_attribution_never_certifies_an_unmatched_tx(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    setup_record(tmp_path)
    def hub(url, route, *, timeout_s):
        if '/credits/bridge-deposit?' in route:
            q = {k:v[0] for k,v in parse_qs(urlparse(route).query).items()}
            return {'ok': True, 'readOnly': True, 'found': True, 'completed': True,
                    'deposit_id': q['deposit_id'], 'wallet_address': q['wallet_address'],
                    'contract_address': q['contract_address'], 'chain_id': int(q['chain_id']),
                    'credit': {'status': 'bridge_completed', 'amount_credit_wei': AMOUNT,
                               'credits_granted_wei': AMOUNT, 'funding_tx_hash': '0x'+'d'*64}}
        return fake_hub(url, route, timeout_s=timeout_s)
    row = app.audit_pending(root=tmp_path, rpc=FakeRpc(has_receipt=False), hub_get=hub)['records'][0]
    assert row['hubDepositAttribution'] == 'hub_deposit_credit_conflict'
    assert row['hubDepositRecord']['fundingTransactionMatches'] is False


def test_deposit_lookup_missing_on_old_hub_remains_unverified(tmp_path, monkeypatch):
    setup_profile(monkeypatch)
    setup_record(tmp_path)
    def old_hub(url, route, *, timeout_s):
        if '/credits/bridge-deposit?' in route: raise RuntimeError('404 not found')
        return fake_hub(url, route, timeout_s=timeout_s)
    row=app.audit_pending(root=tmp_path, rpc=FakeRpc(has_receipt=False), hub_get=old_hub)['records'][0]
    assert row['hubDepositAttribution'] == 'exact_lookup_unavailable'
    assert row['missingReceiptEvidence']['permitsNewDeposit'] is False
