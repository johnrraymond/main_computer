"""Focused safety checks for the operator-run Mother -> deployment office projection."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import wallet_address_rectifier as wr
from tools.mother.common.ethereum_identity import private_key_to_address


KEYS = [f"0x{i:064x}" for i in range(1, 5)]
ADDRESSES = [private_key_to_address(key) for key in KEYS]


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


def create_manifest(root: Path, network: str, offices=None, **extra):
    target = root / 'runtime' / 'deployments' / network / 'latest.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        'environment': network,
        'chain': {'chain_id': 42424240, 'rpc_url': 'https://example.invalid'},
        'deployments': {'xlag-bridge-reserve': {'address': '0x' + 'c' * 40, 'constructor_args': ['OLD DO NOT TOUCH']}},
        'source': {'kind': 'mainnet-operator'},
        'run_id': 'preserve-me',
        **extra,
    }
    if offices is not None:
        manifest['offices'] = offices
    target.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return target


def mock_formatter_root(root, source_root):
    (root / 'tools').mkdir(parents=True)
    (root / 'tools' / 'dev-chain-reset.py').write_bytes((source_root / 'tools' / 'dev-chain-reset.py').read_bytes())


def test_existing_formatter_used_and_secret_not_published():
    generated = wr._office_records_from_dev_chain_reset(wr.REPO_ROOT, ADDRESSES)
    assert len(generated) == 4
    assert [record['office'] for record in generated] == ['O0', 'O1', 'O2', 'O3']
    assert [record['address'] for record in generated] == ADDRESSES
    assert all(set(record) == {'office', 'title', 'address'} for record in generated)
    assert all('private_key' not in record for record in generated)


def test_apply_mainnet_changes_only_offices(monkeypatch, tmp_path):
    stub_verified(monkeypatch, mother_doc())
    mock_formatter_root(tmp_path, wr.REPO_ROOT)
    legacy = [{'office': f'O{i}', 'title': 'old', 'address': '0x' + str(i+1) * 40} for i in range(4)]
    target = create_manifest(tmp_path, 'mainnet', legacy)
    original = json.loads(target.read_text())
    report = wr.rectify(tmp_path, 'mainnet', apply=True)
    result = json.loads(target.read_text())
    assert report['status'] == 'UPDATED' and report['write_performed']
    assert result['offices'] == wr._office_records_from_dev_chain_reset(wr.REPO_ROOT, ADDRESSES)
    assert {k: v for k, v in original.items() if k != 'offices'} == {k: v for k, v in result.items() if k != 'offices'}
    assert 'private_key' not in target.read_text()
    before = target.read_bytes()
    again = wr.rectify(tmp_path, 'mainnet', apply=True)
    assert again['status'] == 'PASS' and not again['write_performed']
    assert target.read_bytes() == before


def test_dry_run_does_not_touch_file(monkeypatch, tmp_path):
    stub_verified(monkeypatch, mother_doc())
    mock_formatter_root(tmp_path, wr.REPO_ROOT)
    target = create_manifest(tmp_path, 'mainnet', [{'address':'0x'+'0'*40}])
    before = target.read_bytes()
    result = wr.rectify(tmp_path, 'mainnet', apply=False)
    assert result['status'] == 'DRIFT' and not result['write_performed']
    assert target.read_bytes() == before


def test_testnet_and_absent_offices(monkeypatch, tmp_path):
    stub_verified(monkeypatch, mother_doc('testnet'))
    mock_formatter_root(tmp_path, wr.REPO_ROOT)
    target = create_manifest(tmp_path, 'testnet')
    result = wr.rectify(tmp_path, 'testnet', apply=True)
    assert result['status'] == 'UPDATED'
    assert [row['address'] for row in json.loads(target.read_text())['offices']] == ADDRESSES


def test_refuses_missing_manifest(monkeypatch, tmp_path):
    stub_verified(monkeypatch, mother_doc())
    mock_formatter_root(tmp_path, wr.REPO_ROOT)
    with pytest.raises(wr.RectifierError, match='missing'):
        wr.rectify(tmp_path, 'mainnet', apply=True)
    assert not (tmp_path/'runtime'/'deployments'/'mainnet'/'latest.json').exists()


def test_refuses_wrong_chain_and_environment(monkeypatch, tmp_path):
    stub_verified(monkeypatch, mother_doc())
    mock_formatter_root(tmp_path, wr.REPO_ROOT)
    target = create_manifest(tmp_path, 'mainnet')
    manifest = json.loads(target.read_text())
    manifest['chain']['chain_id'] = 42424241
    target.write_text(json.dumps(manifest))
    before = target.read_bytes()
    with pytest.raises(wr.RectifierError, match='chain ID'):
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
