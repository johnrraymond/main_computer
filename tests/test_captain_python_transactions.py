from types import SimpleNamespace
import subprocess

import pytest
from eth_account import Account
from eth_account._utils.legacy_transactions import Transaction
from eth_abi import decode
from eth_utils import keccak
import rlp

from main_computer import captain_cli as captain

KEY = '0x' + '11' * 32
CONTRACT = '0x000000000000000000000000000000000000e5c0'
DEPOSIT = '0x' + '22' * 32


class RpcFixture:
    def __init__(self):
        self.raw = []
        self.failure = ''
        self.receipt = {'status': '0x1', 'blockNumber': '0x10', 'gasUsed': '0x100', 'effectiveGasPrice': '0x2'}

    def chain_id(self): return 42424240
    def get_transaction_count(self, address): return 7
    def gas_price(self): return 2
    def estimate_gas(self, tx):
        if self.failure == 'estimate': raise RuntimeError('execution reverted')
        return 100000
    def send_raw_transaction(self, raw):
        self.raw.append(raw)
        if self.failure == 'send': raise TimeoutError('RPC response lost')
        return '0x' + keccak(raw).hex()
    def transaction_receipt(self, tx_hash): return self.receipt


@pytest.fixture
def setup(monkeypatch):
    rpc = RpcFixture()
    monkeypatch.setattr(captain, 'JsonRpcClient', lambda *a, **kw: rpc)
    def forbidden(*args, **kwargs): raise AssertionError('container or subprocess invoked')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(captain, 'resolve_container_runtime', forbidden, raising=False)
    runtime = SimpleNamespace(
        wallet=SimpleNamespace(address=Account.from_key(KEY).address, private_key=KEY),
        chain_id=42424240, rpc_url='https://rpc.invalid', bridge_escrow_address=CONTRACT,
    )
    return runtime, rpc


def send(runtime):
    return captain.send_captain_contract_transaction(
        runtime, contract_address=CONTRACT, private_key=KEY,
        function_signature='depositFor(address,uint256,bytes32,string)',
        function_args=[runtime.wallet.address, str(10**18), DEPOSIT, 'captain deposit'],
        value_wei=10**18, timeout_s=1,
    )


def test_signed_deposit_preserves_abi_value_signer_and_receipt(setup):
    runtime, rpc = setup
    result = send(runtime)
    raw = rpc.raw[0]
    assert Account.recover_transaction(raw) == runtime.wallet.address
    tx = rlp.decode(raw, Transaction)
    assert tx.nonce == 7 and tx.value == 10**18
    assert tx.to == bytes.fromhex(CONTRACT[2:])
    assert (tx.v - 35) // 2 == runtime.chain_id
    assert tx.data[:4] == keccak(text='depositFor(address,uint256,bytes32,string)')[:4]
    wallet, amount, deposit, memo = decode(['address', 'uint256', 'bytes32', 'string'], tx.data[4:])
    assert wallet == runtime.wallet.address.lower()
    assert amount == 10**18 and deposit.hex() == DEPOSIT[2:] and memo == 'captain deposit'
    assert result['transaction_hash'] == '0x' + keccak(raw).hex()
    assert result['gas_cost_wei'] == 512
    assert result['transport'] == 'python-signed-json-rpc' and result['command'] == []


@pytest.mark.parametrize('failure', ['wallet', 'chain', 'estimate'])
def test_failure_before_submission(setup, failure):
    runtime, rpc = setup
    if failure == 'wallet': runtime.wallet.address = '0x' + '33' * 20
    if failure == 'chain': runtime.chain_id = 1
    if failure == 'estimate': rpc.failure = 'estimate'
    with pytest.raises(captain.CaptainCliError): send(runtime)
    assert rpc.raw == []


def test_uncertain_submission_reports_hash_without_retry(setup):
    runtime, rpc = setup
    rpc.failure = 'send'
    with pytest.raises(captain.CaptainCliError, match='submission') as caught: send(runtime)
    assert len(rpc.raw) == 1
    assert '0x' + keccak(rpc.raw[0]).hex() in str(caught.value)
    assert KEY not in str(caught.value)


def test_revert_is_not_success(setup):
    runtime, rpc = setup
    rpc.receipt['status'] = '0x0'
    with pytest.raises(captain.CaptainCliError, match='receipt confirmation'): send(runtime)
    assert len(rpc.raw) == 1


def test_receipt_timeout_does_not_resubmit(setup, monkeypatch):
    runtime, rpc = setup
    def timeout(*args, **kw): raise captain.CaptainCliError('Timed out waiting for receipt')
    monkeypatch.setattr(captain, '_wait_for_receipt', timeout)
    with pytest.raises(captain.CaptainCliError, match='receipt confirmation') as caught: send(runtime)
    assert len(rpc.raw) == 1 and KEY not in str(caught.value)


def test_deposit_wrapper_preserves_hub_import_fields(setup):
    runtime, rpc = setup
    result = captain.send_captain_bridge_deposit(runtime, deposit_credit_wei=10**18, smoke_id=DEPOSIT, timeout_s=1)
    assert result['wallet_address'] == runtime.wallet.address
    assert result['amount_credit_wei'] == str(10**18)
    assert result['deposit_id'].startswith('0x')
    assert len(rpc.raw) == 1


@pytest.mark.parametrize('is_office', [True, False])
def test_final_smoke_uses_python_signer(setup, is_office):
    runtime, rpc = setup
    runtime.wallet.is_office = is_office
    runtime.xlag_address = CONTRACT
    result = captain.send_captain_smoke_transaction(runtime, smoke_id=DEPOSIT, memo='ready', timeout_s=1)
    tx = rlp.decode(rpc.raw[0], Transaction)
    signature = 'finalizeWalletSmokeTest(bytes32,string)' if is_office else 'frobByAnyUser(bytes32,string)'
    assert tx.data[:4] == keccak(text=signature)[:4]
    assert decode(['bytes32', 'string'], tx.data[4:]) == (bytes.fromhex(DEPOSIT[2:]), 'ready')
    assert tx.value == 0 and result['transport'] == 'python-signed-json-rpc'
