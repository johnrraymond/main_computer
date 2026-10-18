from pathlib import Path

import pytest

from main_computer.captain_cli import CaptainCliError, resolve_captain_wallet
from tools.clone_project_sensitive_state import (
    EXPLICIT_SECRET_FILES,
    collect_files,
    iter_current_state_reference_files,
)


def _legacy_state(repo: Path) -> Path:
    legacy = repo / 'runtime/state/main_computer.private.yaml'
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        'networks:\n  dev:\n    wallets:\n      captain:\n'
        '        address: 0x' + '3' * 40 + '\n'
        '        private_key: 0x' + '4' * 64 + '\n',
        encoding='utf-8',
    )
    return legacy


def test_dev_captain_does_not_consult_legacy_yaml(tmp_path: Path, monkeypatch) -> None:
    legacy = _legacy_state(tmp_path)
    monkeypatch.setenv('MAIN_COMPUTER_PRIVATE_STATE_PATH', str(legacy))
    deployment_path = tmp_path / 'runtime/deployments/dev/latest.json'
    # A non-Anvil chain must not invent a signer from historical legacy YAML.
    wallet = resolve_captain_wallet(
        'captain',
        deployment={
            'chain': {'chain_id': 9999, 'network_name': 'dev'},
            'offices': [{'office': 'O0', 'title': 'Captain', 'address': '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266'}],
        },
        deployment_path=deployment_path,
        network='dev',
    )
    assert wallet.private_key == ''
    assert wallet.address.lower() == '0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266'


def test_retired_dev_private_state_flag_is_rejected(tmp_path: Path) -> None:
    legacy = _legacy_state(tmp_path)
    with pytest.raises(CaptainCliError, match='--private-state is retired'):
        resolve_captain_wallet(
            'captain', deployment={'chain': {'chain_id': 9999}},
            deployment_path=tmp_path / 'runtime/deployments/dev/latest.json',
            private_state_path=legacy,
            network='dev',
        )


def test_dev_explicit_key_and_anvil_defaults_still_work(tmp_path: Path) -> None:
    path = tmp_path / 'runtime/deployments/dev/latest.json'
    key = '0x' + 'a' * 64
    explicit = resolve_captain_wallet(
        'captain', deployment={'chain': {'chain_id': 9999}},
        deployment_path=path, network='dev', private_key_override=key,
    )
    assert explicit.private_key == key
    anvil = resolve_captain_wallet(
        'captain', deployment={'chain': {'chain_id': 42424242}},
        deployment_path=path, network='dev',
    )
    assert len(anvil.private_key) == 66


def test_clone_neither_copies_nor_reads_legacy_private_yaml(tmp_path: Path) -> None:
    legacy = _legacy_state(tmp_path)
    mother = tmp_path / 'runtime/state/mother/identity.private.yaml'
    mother.parent.mkdir(parents=True, exist_ok=True)
    mother.write_text('authority: mother\n', encoding='utf-8')
    selected = collect_files(tmp_path, tmp_path / 'runtime/state_clones/one')
    copied = {entry['relative_path'] for entry in selected}
    assert 'runtime/state/mother/identity.private.yaml' in copied
    assert 'runtime/state/main_computer.private.yaml' not in copied
    assert legacy not in set(iter_current_state_reference_files(tmp_path))
    assert Path('runtime/state/main_computer.private.yaml') not in EXPLICIT_SECRET_FILES
