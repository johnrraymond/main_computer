"""Focused safety checks for Mother officers + published contract synchronization."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import wallet_address_rectifier as wr
from tools.mother.common.ethereum_identity import private_key_to_address


KEYS = [f"0x{i:064x}" for i in range(1, 5)]
ADDRESSES = [private_key_to_address(key) for key in KEYS]
OLD_ESCROW = "0x" + "a" * 40
NEW_ESCROW = "0x" + "e" * 40
NEW_RESERVE = "0x" + "c" * 40
PUBLIC_CONTRACTS = {
    "hub_credit_bridge_escrow": NEW_ESCROW,
    "xlag-bridge-reserve": NEW_RESERVE,
}


def mother_doc(network="mainnet", *, addresses=None, keys=None):
    addresses = addresses or ADDRESSES
    keys = keys or KEYS
    return {"networks": {network: {"chain_id": 42424240, "wallets": {
        role: {"address": addresses[i], "private_key": keys[i]}
        for i, role in enumerate(wr.ROLES)
    }}}}


def stub_verified(monkeypatch, document):
    monkeypatch.setattr(wr, "read_private_state", lambda paths, *, operation: SimpleNamespace(
        canonical_object_bytes=json.dumps(document).encode(),
        binding=SimpleNamespace(generation=11),
    ))


def create_contract_config(root: Path, network: str, entries=None):
    path = root / "main_computer" / "config" / f"{network}_contracts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(PUBLIC_CONTRACTS if entries is None else entries, indent=2), encoding="utf-8")
    return path


def create_manifest(root: Path, network: str, offices=None, **extra):
    target = root / 'runtime' / 'deployments' / network / 'latest.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    old_escrow_record = {
        'address': OLD_ESCROW, 'transaction_hash': '0x' + '1'*64,
        'constructor_args': ['old-controller'], 'bridge_controller_address': '0x' + 'd'*40,
        'chain_id': 42424240, 'payment_asset': 'native', 'approval_required': False,
    }
    manifest = {
        'environment': network,
        'chain': {'chain_id': 42424240, 'rpc_url': 'https://example.invalid'},
        'contracts': {
            'hub_credit_bridge_escrow': old_escrow_record.copy(),
            'xlag-bridge-reserve': {'address': NEW_RESERVE, 'target': 'existing-reserve'},
            'alpha-beta-lockout': {'address': '0x' + 'f'*40, 'target': 'untouched'},
        },
        'deployments': {
            'hub_credit_bridge_escrow': old_escrow_record.copy(),
            'xlag-bridge-reserve': {'address': NEW_RESERVE, 'target': 'existing-reserve'},
            'alpha-beta-lockout': {'address': '0x' + 'f'*40, 'target': 'untouched'},
        },
        'source': {'kind': 'mainnet-operator'},
        'run_id': 'preserve-me',
        **extra,
    }
    if offices is not None:
        manifest['offices'] = offices
    target.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return target


def mock_formatter_root(root, source_root):
    (root / 'tools').mkdir(parents=True, exist_ok=True)
    (root / 'tools' / 'dev-chain-reset.py').write_bytes((source_root / 'tools' / 'dev-chain-reset.py').read_bytes())


def setup_rectifier(monkeypatch, tmp_path, network='mainnet', *, offices=None, entries=None):
    stub_verified(monkeypatch, mother_doc(network))
    mock_formatter_root(tmp_path, wr.REPO_ROOT)
    create_contract_config(tmp_path, network, entries)
    return create_manifest(tmp_path, network, offices)


def test_existing_formatter_used_and_secret_not_published():
    generated = wr._office_records_from_dev_chain_reset(wr.REPO_ROOT, ADDRESSES)
    assert len(generated) == 4
    assert [record['office'] for record in generated] == ['O0', 'O1', 'O2', 'O3']
    assert [record['address'] for record in generated] == ADDRESSES
    assert all(set(record) == {'office', 'title', 'address'} for record in generated)
    assert all('private_key' not in record for record in generated)


def test_apply_mainnet_synchronizes_both_sections_and_preserves_unrelated(monkeypatch, tmp_path):
    legacy = [{'office': f'O{i}', 'title': 'old', 'address': '0x' + str(i+1) * 40} for i in range(4)]
    target = setup_rectifier(monkeypatch, tmp_path, offices=legacy)
    original = json.loads(target.read_text())
    report = wr.rectify(tmp_path, 'mainnet', apply=True)
    result = json.loads(target.read_text())
    assert report['status'] == 'UPDATED' and report['write_performed']
    assert result['offices'] == wr._office_records_from_dev_chain_reset(wr.REPO_ROOT, ADDRESSES)
    assert report['contract_source'].endswith('mainnet_contracts.json')
    for section in ('contracts', 'deployments'):
        # Old transaction metadata must not describe a different, genesis-installed contract.
        assert result[section]['hub_credit_bridge_escrow'] == {'address': NEW_ESCROW}
        assert result[section]['xlag-bridge-reserve'] == original[section]['xlag-bridge-reserve']
        assert result[section]['alpha-beta-lockout'] == original[section]['alpha-beta-lockout']
    assert {k: v for k, v in original.items() if k not in ('offices', 'contracts', 'deployments')} == {
        k: v for k, v in result.items() if k not in ('offices', 'contracts', 'deployments')
    }
    assert 'private_key' not in target.read_text()
    before = target.read_bytes()
    again = wr.rectify(tmp_path, 'mainnet', apply=True)
    assert again['status'] == 'PASS' and not again['write_performed']
    assert target.read_bytes() == before


def test_dry_run_does_not_touch_file(monkeypatch, tmp_path):
    target = setup_rectifier(monkeypatch, tmp_path, offices=[{'address': '0x' + '0'*40}])
    before = target.read_bytes()
    result = wr.rectify(tmp_path, 'mainnet', apply=False)
    assert result['status'] == 'DRIFT' and not result['write_performed']
    assert target.read_bytes() == before
    assert 'private_key' not in json.dumps(result)


def test_contract_only_drift_updates_even_if_offices_match(monkeypatch, tmp_path):
    target = setup_rectifier(monkeypatch, tmp_path)
    offices = wr._office_records_from_dev_chain_reset(wr.REPO_ROOT, ADDRESSES)
    manifest = json.loads(target.read_text())
    manifest['offices'] = offices
    target.write_text(json.dumps(manifest), encoding='utf-8')
    report = wr.rectify(tmp_path, 'mainnet', apply=True)
    assert report['status'] == 'UPDATED'
    updated = json.loads(target.read_text())
    assert updated['offices'] == offices
    assert updated['contracts']['hub_credit_bridge_escrow'] == {'address': NEW_ESCROW}


def test_testnet_and_absent_offices(monkeypatch, tmp_path):
    target = setup_rectifier(monkeypatch, tmp_path, 'testnet')
    result = wr.rectify(tmp_path, 'testnet', apply=True)
    assert result['status'] == 'UPDATED'
    document = json.loads(target.read_text())
    assert [row['address'] for row in document['offices']] == ADDRESSES
    assert document['contracts']['hub_credit_bridge_escrow'] == {'address': NEW_ESCROW}
    assert document['deployments']['hub_credit_bridge_escrow'] == {'address': NEW_ESCROW}


def test_rejects_missing_config_without_manifest_mutation(monkeypatch, tmp_path):
    target = setup_rectifier(monkeypatch, tmp_path)
    source = tmp_path/'main_computer'/'config'/'mainnet_contracts.json'
    source.unlink()
    before = target.read_bytes()
    with pytest.raises(Exception, match='contract config not found'):
        wr.rectify(tmp_path, 'mainnet', apply=True)
    assert target.read_bytes() == before


@pytest.mark.parametrize('bad', [
    {'hub_credit_bridge_escrow': 'not-an-address'},
    {'hub_credit_bridge_escrow': '0x' + '0'*40},
    {},
    {'schema': 'wrong-schema', 'hub_credit_bridge_escrow': NEW_ESCROW},
    {'network': 'testnet', 'hub_credit_bridge_escrow': NEW_ESCROW},
])
def test_rejects_invalid_public_config_without_mutation(monkeypatch, tmp_path, bad):
    target = setup_rectifier(monkeypatch, tmp_path, entries=bad)
    before = target.read_bytes()
    with pytest.raises(Exception):
        wr.rectify(tmp_path, 'mainnet', apply=True)
    assert target.read_bytes() == before


def test_refuses_malformed_contract_section_without_partial_write(monkeypatch, tmp_path):
    target = setup_rectifier(monkeypatch, tmp_path)
    manifest = json.loads(target.read_text())
    manifest['deployments'] = 'invalid'
    target.write_text(json.dumps(manifest), encoding='utf-8')
    before = target.read_bytes()
    with pytest.raises(wr.RectifierError, match='malformed deployments'):
        wr.rectify(tmp_path, 'mainnet', apply=True)
    assert target.read_bytes() == before


def test_refuses_missing_manifest(monkeypatch, tmp_path):
    stub_verified(monkeypatch, mother_doc())
    mock_formatter_root(tmp_path, wr.REPO_ROOT)
    with pytest.raises(wr.RectifierError, match='missing'):
        wr.rectify(tmp_path, 'mainnet', apply=True)
    assert not (tmp_path/'runtime'/'deployments'/'mainnet'/'latest.json').exists()


def test_refuses_wrong_chain_and_environment(monkeypatch, tmp_path):
    target = setup_rectifier(monkeypatch, tmp_path)
    manifest = json.loads(target.read_text())
    manifest['chain']['chain_id'] = 42424241
    target.write_text(json.dumps(manifest))
    before = target.read_bytes()
    with pytest.raises(wr.RectifierError, match='chain ID'):
        wr.rectify(tmp_path, 'mainnet', apply=True)
    assert target.read_bytes() == before
    manifest['chain']['chain_id'] = 42424240
    manifest['environment'] = 'testnet'
    target.write_text(json.dumps(manifest))
    before = target.read_bytes()
    with pytest.raises(wr.RectifierError, match='environment'):
        wr.rectify(tmp_path, 'mainnet', apply=True)
    assert target.read_bytes() == before


def test_refuses_bad_or_duplicate_officer(monkeypatch, tmp_path):
    malformed = mother_doc()
    malformed['networks']['mainnet']['wallets']['captain']['address'] = ADDRESSES[1]
    stub_verified(monkeypatch, malformed)
    with pytest.raises(wr.RectifierError, match='signing key'):
        wr._load_mother_officers(tmp_path, 'mainnet')
    malformed = mother_doc(keys=[KEYS[0], KEYS[0], KEYS[2], KEYS[3]], addresses=[ADDRESSES[0], ADDRESSES[0], ADDRESSES[2], ADDRESSES[3]])
    stub_verified(monkeypatch, malformed)
    with pytest.raises(wr.RectifierError, match='not distinct'):
        wr._load_mother_officers(tmp_path, 'mainnet')


def test_no_officer_addresses_accepted_by_cli():
    parser = wr.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(['--network', 'mainnet', '--offices', ','.join(ADDRESSES)])
    with pytest.raises(SystemExit):
        parser.parse_args(['--network', 'dev'])


def test_invalid_mother_reference_stops(monkeypatch, tmp_path):
    def failure(*args, **kwargs):
        raise RuntimeError('MOTHER_STATE_PRIVATE_STATE_REFERENCE_MISMATCH')
    monkeypatch.setattr(wr, 'read_private_state', failure)
    with pytest.raises(RuntimeError, match='REFERENCE_MISMATCH'):
        wr.rectify(tmp_path, 'mainnet', apply=True)
    assert not (tmp_path/'runtime'/'deployments'/'mainnet'/'latest.json').exists()


def test_atomic_replace_rejects_changed_manifest(tmp_path):
    target = tmp_path / 'latest.json'
    target.write_bytes(b'new version')
    with pytest.raises(wr.RectifierError, match='changed'):
        wr._atomic_replace_if_unchanged(target, b'old version', b'replacement')
    assert target.read_bytes() == b'new version'
