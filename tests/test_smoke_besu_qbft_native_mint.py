"""Regression tests for genesis-funded native supply on unmodified Besu."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(relative: str, module_name: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def reserve():
    return load('tools/genesis_native_reserve.py', 'genesis_native_reserve')


@pytest.fixture
def smoke(monkeypatch, reserve):
    harness = load('tools/smoke_besu_qbft_one_validator.py', 'smoke_besu_qbft_one_validator')
    monkeypatch.setitem(sys.modules, 'smoke_besu_qbft_one_validator', harness)
    monkeypatch.setitem(sys.modules, 'genesis_native_reserve', reserve)
    return load('tools/smoke_besu_qbft_native_mint.py', 'stock_besu_reserve_smoke')


def fake_artifact() -> dict:
    fields = [
        ('_offices', 0, 0),
        ('officeIndexPlusOne', 4, 0),
        ('maxPayoutWei', 8, 0),
        ('payoutDelayBlocks', 9, 0),
        ('resetDelayBlocks', 9, 8),
        ('nextProposalId', 10, 0),
    ]
    return {
        'storageLayout': {'storage': [
            {'label': name, 'slot': str(slot), 'offset': offset} for name,slot,offset in fields
        ]},
        'deployedBytecode': {'object': '0x600160005260206000f3'},
    }


OFFICES = [f'0x{i:040x}' for i in range(1, 5)]
DEPLOYER = '0x' + '99' * 20
RESERVE = '0x' + 'ab' * 20


def allocated(reserve, *, deployer=DEPLOYER, captain_wei=10_000*10**18,
              deployer_wei=10_000*10**18):
    return reserve.reserve_allocation(
        artifact=fake_artifact(),
        captain=OFFICES[0], first_officer=OFFICES[1],
        second_officer=OFFICES[2], third_officer=OFFICES[3],
        deployer=deployer, initial_captain_wei=captain_wei,
        initial_deployer_wei=deployer_wei,
        max_payout_wei=20*10**18,
        reserve_address=RESERVE,
    )


def test_genesis_funds_all_four_officers_equal_to_captain_and_separate_deployer(reserve):
    captain_wei = 10_000*10**18
    deployer_wei = 7_000*10**18
    alloc, profile = allocated(reserve, deployer_wei=deployer_wei)
    for officer in OFFICES:
        assert int(alloc[officer[2:]]['balance'], 16) == captain_wei
    assert int(alloc[DEPLOYER[2:]]['balance'], 16) == deployer_wei
    assert profile['initial_officer_wei_each'] == captain_wei
    assert profile['offices'] == OFFICES
    assert int(alloc[RESERVE[2:]]['balance'], 16) == reserve.MAX_UINT256 - 4*captain_wei - deployer_wei
    assert sum(int(entry['balance'], 16) for entry in alloc.values()) == reserve.MAX_UINT256
    assert len(alloc) == 6
    assert alloc[RESERVE[2:]]['code'].startswith('0x')


def test_genesis_reserve_constructor_state_matches_office_roles(reserve):
    alloc, _ = allocated(reserve)
    storage = alloc[RESERVE[2:]]['storage']
    for index, officer in enumerate(OFFICES):
        assert int(storage[reserve.word(index)], 16) == int(officer, 16)
        assert int(storage[reserve.word(reserve.mapping_slot(officer, 4))], 16) == index + 1
    assert int(storage[reserve.word(8)], 16) == 20*10**18
    assert int(storage[reserve.word(9)], 16) == 0
    assert int(storage[reserve.word(10)], 16) == 1


def test_alias_deployer_officer_not_double_funded(reserve):
    amount = 10_000*10**18
    alloc, profile = allocated(reserve, deployer=OFFICES[1])
    assert len(alloc) == 5
    assert profile['deployer'] == OFFICES[1]
    assert int(alloc[RESERVE[2:]]['balance'], 16) == reserve.MAX_UINT256 - 4*amount
    with pytest.raises(ValueError, match='same genesis funding'):
        allocated(reserve, deployer=OFFICES[1], deployer_wei=1)


def test_reserve_rejects_overlapping_reserve_and_duplicate_officers(reserve):
    with pytest.raises(ValueError, match='overlap'):
        reserve.reserve_allocation(
            artifact=fake_artifact(), captain=OFFICES[0], first_officer=OFFICES[1],
            second_officer=OFFICES[2], third_officer=OFFICES[3],
            deployer=DEPLOYER, reserve_address=OFFICES[0],
            initial_captain_wei=100, initial_deployer_wei=200, max_payout_wei=20,
        )
    with pytest.raises(ValueError, match='distinct'):
        reserve.reserve_allocation(
            artifact=fake_artifact(), captain=OFFICES[0], first_officer=OFFICES[0],
            second_officer=OFFICES[2], third_officer=OFFICES[3],
            deployer=DEPLOYER, initial_captain_wei=100, initial_deployer_wei=200,
            max_payout_wei=20,
        )


def test_reserve_fails_closed_when_contract_storage_layout_changes(reserve):
    artifact = fake_artifact()
    artifact['storageLayout']['storage'][0]['slot'] = '99'
    with pytest.raises(ValueError, match='storage layout changed'):
        reserve.reserve_allocation(
            artifact=artifact, captain=OFFICES[0], first_officer=OFFICES[1],
            second_officer=OFFICES[2], third_officer=OFFICES[3],
            deployer=DEPLOYER, initial_captain_wei=100, initial_deployer_wei=100,
            max_payout_wei=20,
        )


def test_ethereum_keccak_used_for_mapping_slots(reserve):
    assert reserve.keccak256(b'').hex() == 'c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470'
    assert reserve.keccak256(b'abc').hex() == '4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45'


def test_seed_preserves_generated_qbft_config_and_funds_every_officer(reserve, tmp_path):
    files = tmp_path / 'networkFiles'
    (files / 'keys').mkdir(parents=True)
    for officer in OFFICES:
        (files / 'keys' / officer[2:]).mkdir()
    genesis = {'config': {'chainId': 42424241, 'qbft': {}}, 'extraData': '0xdeadbeef', 'alloc': {'11'*20: {'balance': '0x11'}}}
    (files / 'genesis.json').write_text(json.dumps(genesis), encoding='utf-8')
    artifact = tmp_path / 'artifact.json'
    artifact.write_text(json.dumps(fake_artifact()), encoding='utf-8')
    profile = reserve.seed(
        files, artifact_path=artifact, deployer_address=DEPLOYER,
        reserve_address=RESERVE, captain_balance_wei=1000, deployer_balance_wei=500,
        max_payout_wei=20,
    )
    written = json.loads((files / 'genesis.json').read_text(encoding='utf-8'))
    assert written['config'] == genesis['config']
    assert written['extraData'] == genesis['extraData']
    assert len(written['alloc']) == 6
    assert profile['offices'] == OFFICES
    assert sum(int(e['balance'],16) for e in written['alloc'].values()) == reserve.MAX_UINT256


def test_smoke_defaults_to_stock_besu_and_exact_amounts(smoke):
    args = smoke.parse_args(['--amount-native', '20'])
    assert args.image.startswith('hyperledger/besu:')
    assert not args.skip_build
    assert smoke.native_to_wei('20') == 20 * 10**18
    assert smoke.native_to_wei('0.000000000000000001') == 1
    with pytest.raises(ValueError):
        smoke.native_to_wei('0.0000000000000000001')
    with pytest.raises(ValueError):
        smoke.native_to_wei('Infinity')


def test_sends_contract_calls_using_environment_secrets(monkeypatch, smoke):
    captured = {}
    def fake_run(cmd, **kwargs):
        captured['cmd'] = cmd
        captured['env'] = kwargs['env']
        return SimpleNamespace(returncode=0, stdout=json.dumps({'status': '0x1', 'blockNumber': '0xb'}))
    monkeypatch.setattr(smoke.subprocess, 'run', fake_run)
    key = '0x' + 'ef'*32
    receipt = smoke.signed_contract_call(
        foundry_image='forge:local', private_key=key, signer_index=3,
        contract=RESERVE, signature='secondPayout(uint256)', arguments=['1'],
    )
    assert receipt['status'] == '0x1'
    assert key not in str(captured['cmd'])
    assert captured['env']['RESERVE_PRIVATE_KEY'] == key
    assert captured['env']['RESERVE_ARG_0'] == '1'
    assert smoke.qbft.DOCKER_NETWORK in captured['cmd']


def test_harness_has_stock_reserve_opt_in(monkeypatch, smoke):
    args = smoke.qbft.parse_args(['restart', '--genesis-native-reserve'])
    assert args.genesis_native_reserve is True
    assert args.native_mint_genesis is False


def test_contract_payout_not_misrepresented_as_three_of_four_quorum(smoke):
    source = (ROOT / 'contracts/src/XLagBridgeReserve.sol').read_text(encoding='utf-8')
    assert 'msg.sender == _offices[CAPTAIN]' in source
    assert 'office == SECOND_OFFICER || office == THIRD_OFFICER' in source
    assert 'proposal.secondedBy == _offices[SECOND_OFFICER]' in source
    assert 'proposal.secondedBy == _offices[THIRD_OFFICER]' in source
    assert 'payable(proposal.recipient).call{value: proposal.amountWei}' in source


def make_block_zero_rpc_fixture(tmp_path, reserve):
    """Minimal genesis-backed RPC peer responses for the block-zero proof."""
    alloc, _ = allocated(reserve)
    root = tmp_path / 'smoke'
    root.mkdir()
    (root / 'genesis.json').write_text(json.dumps({'alloc': alloc, 'config': {'chainId': 42424241}}))
    artifact_path = tmp_path / 'artifact.json'
    artifact_path.write_text(json.dumps(fake_artifact()))
    contract_entry = alloc[RESERVE[2:]]
    selectors = {
        reserve.keccak256(signature.encode('ascii'))[:4].hex(): value
        for signature, value in (
            ('maxPayoutWei()', 20*10**18),
            ('nextProposalId()', 1),
            ('payoutDelayBlocks()', 0),
            ('resetDelayBlocks()', 0),
        )
    }
    office_selector = reserve.keccak256(b'getOffices()')[:4].hex()
    urls = [f'http://node-{index}' for index in range(5)]

    def rpc(url, method, params=None, **kwargs):
        assert url in urls
        params = params or []
        if method == 'eth_getBlockByNumber':
            assert params == ['0x0', False]
            return {'number': '0x0', 'hash': '0x' + 'aa'*32, 'stateRoot': '0x' + 'bb'*32, 'transactions': []}
        if method == 'eth_getCode':
            assert params[1] == '0x0'
            return contract_entry['code']
        if method == 'eth_getStorageAt':
            assert params[2] == '0x0'
            return contract_entry['storage'].get(reserve.word(int(params[1], 16)), reserve.word(0))
        if method == 'eth_getBalance':
            assert params[1] == '0x0'
            return alloc.get(params[0].lower()[2:], {'balance': '0x0'})['balance']
        if method == 'eth_call':
            assert params[1] == '0x0'
            selector = params[0]['data'][2:]
            if selector == office_selector:
                return '0x' + ''.join(f'{int(a,16):064x}' for a in OFFICES)
            return reserve.word(selectors[selector])
        raise AssertionError(method)

    options = dict(
        runtime_dir=root, artifact_path=artifact_path, urls=urls, contract=RESERVE,
        officers=OFFICES, deployer=DEPLOYER, captain_wei=10_000*10**18,
        deployer_wei=10_000*10**18,
        reserve_wei=int(contract_entry['balance'], 16), max_payout_wei=20*10**18,
        recipient='0x' + '88'*20,
    )
    return options, rpc


def test_genesis_escrow_verified_at_block_zero_on_five_nodes(monkeypatch, smoke, reserve, tmp_path):
    args, rpc = make_block_zero_rpc_fixture(tmp_path, reserve)
    monkeypatch.setattr(smoke.qbft, 'rpc_call', rpc)
    proof = smoke.verify_genesis_contract(**args)
    assert proof['contract_predeployed_at_block_zero'] is True
    assert proof['genesis_nodes_verified'] == 5
    assert proof['genesis_block_transactions'] == 0
    assert proof['genesis_supply_exactly_max_uint256'] is True
    assert proof['constructor_getters_verified'] is True
    assert proof['constructor_storage_slots_verified_per_node'] == 11
    assert proof['genesis_contract_code_keccak256'] == '0x' + reserve.keccak256(bytes.fromhex('600160005260206000f3')).hex()


def test_genesis_escrow_rejects_client_code_mismatch(monkeypatch, smoke, reserve, tmp_path):
    args, rpc = make_block_zero_rpc_fixture(tmp_path, reserve)
    def broken(url, method, params=None, **kwargs):
        if url.endswith('3') and method == 'eth_getCode':
            return '0x60006000'
        return rpc(url, method, params, **kwargs)
    monkeypatch.setattr(smoke.qbft, 'rpc_call', broken)
    with pytest.raises(smoke.NativeReserveSmokeError, match='block-0 escrow bytecode'):
        smoke.verify_genesis_contract(**args)


def test_genesis_escrow_rejects_missing_office_mapping(monkeypatch, smoke, reserve, tmp_path):
    args, rpc = make_block_zero_rpc_fixture(tmp_path, reserve)
    slot = reserve.word(reserve.mapping_slot(OFFICES[2], 4))
    def broken(url, method, params=None, **kwargs):
        if url.endswith('4') and method == 'eth_getStorageAt' and params[1] == hex(int(slot, 16)):
            return reserve.word(0)
        return rpc(url, method, params, **kwargs)
    monkeypatch.setattr(smoke.qbft, 'rpc_call', broken)
    with pytest.raises(smoke.NativeReserveSmokeError, match='block-0 storage slot'):
        smoke.verify_genesis_contract(**args)


def test_genesis_escrow_rejects_contract_created_after_block_zero(monkeypatch, smoke, reserve, tmp_path):
    args, rpc = make_block_zero_rpc_fixture(tmp_path, reserve)
    def broken(url, method, params=None, **kwargs):
        if method == 'eth_getCode' and params[1] == '0x0':
            return '0x'
        return rpc(url, method, params, **kwargs)
    monkeypatch.setattr(smoke.qbft, 'rpc_call', broken)
    with pytest.raises(smoke.NativeReserveSmokeError, match='block-0 escrow bytecode'):
        smoke.verify_genesis_contract(**args)


def test_genesis_escrow_rejects_nonempty_genesis_transactions(monkeypatch, smoke, reserve, tmp_path):
    args, rpc = make_block_zero_rpc_fixture(tmp_path, reserve)
    def broken(url, method, params=None, **kwargs):
        result = rpc(url, method, params, **kwargs)
        if method == 'eth_getBlockByNumber':
            return {**result, 'transactions': ['0x' + 'aa'*32]}
        return result
    monkeypatch.setattr(smoke.qbft, 'rpc_call', broken)
    with pytest.raises(smoke.NativeReserveSmokeError, match='genesis unexpectedly has deployment transactions'):
        smoke.verify_genesis_contract(**args)
