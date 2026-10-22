"""Captain and Data use each network's current public contract registry."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from main_computer import captain_cli
from main_computer.config import MainComputerConfig


STALE_ESCROW = '0x' + '2' * 40
STALE_RESERVE = '0x' + '3' * 40
STALE_CONTROLLER = '0x' + '4' * 40
ACTIVE_ESCROW = '0x' + 'e' * 40
ACTIVE_RESERVE = '0x' + 'c' * 40


def _setup(tmp_path: Path, network: str, *, public: object | None = None) -> Path:
    path = tmp_path / 'runtime' / 'deployments' / network / 'latest.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'chain': {'chain_id': 42424241 if network == 'testnet' else 42424240},
        'contracts': {
            'hub_credit_bridge_escrow': {
                'address': STALE_ESCROW, 'bridge_controller_address': STALE_CONTROLLER,
            },
            'xlag-bridge-reserve': {'address': STALE_RESERVE},
        },
    }), encoding='utf-8')
    if public is not None:
        config_path = tmp_path / 'main_computer' / 'config' / f'{network}_contracts.json'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(public), encoding='utf-8')
    return path


def _runtime(monkeypatch, tmp_path: Path, network: str):
    wallet = captain_cli.CaptainWallet(
        selector='captain', office='O0', title='Captain', address='0x' + '1' * 40,
        private_key='0x' + '1' * 64,
    )
    monkeypatch.setattr(captain_cli, 'resolve_captain_wallet', lambda *args, **kwargs: wallet)
    options = captain_cli.build_captain_options_parser().parse_args([
        '--network', network, '--state', str(tmp_path / 'runtime/deployments' / network / 'latest.json'),
        '--rpc-url', 'https://testnet-rpc.greatlibrary.io', '--hub-url', 'https://testnet-hub.greatlibrary.io',
    ])
    return captain_cli.build_captain_runtime(
        MainComputerConfig(workspace=tmp_path), options=options, selector='captain', cwd=tmp_path,
    )


def test_testnet_uses_public_registry_not_stale_deployment_for_bridge_and_reserve(tmp_path, monkeypatch):
    manifest = _setup(tmp_path, 'testnet', public={
        'hub_credit_bridge_escrow': ACTIVE_ESCROW,
        'xlag-bridge-reserve': ACTIVE_RESERVE,
    })
    runtime = _runtime(monkeypatch, tmp_path, 'testnet')
    assert runtime.deployment_path == manifest
    assert runtime.bridge_escrow_address == ACTIVE_ESCROW
    assert runtime.xlag_address == ACTIVE_RESERVE
    assert runtime.bridge_controller_address == ''  # stale controller is not payment authority
    assert runtime.chain_id == 42424241


def test_mainnet_uses_public_registry_and_does_not_bind_historical_admin(tmp_path, monkeypatch):
    manifest = _setup(tmp_path, 'mainnet', public={
        'hub_credit_bridge_escrow': ACTIVE_ESCROW, 'xlag-bridge-reserve': ACTIVE_RESERVE,
    })
    runtime = _runtime(monkeypatch, tmp_path, 'mainnet')
    assert runtime.deployment_path == manifest
    assert runtime.bridge_escrow_address == ACTIVE_ESCROW
    assert runtime.xlag_address == ACTIVE_RESERVE
    assert runtime.bridge_controller_address == ''
    assert runtime.chain_id == 42424240


@pytest.mark.parametrize('network', ['mainnet', 'testnet'])
@pytest.mark.parametrize('registry', [None, {}, {'hub_credit_bridge_escrow': 'not-a-wallet'}])
def test_missing_invalid_public_registry_blocks_payment(tmp_path, monkeypatch, network, registry):
    _setup(tmp_path, network, public=registry)
    with pytest.raises(captain_cli.CaptainCliError, match=rf'{network} contract'):
        _runtime(monkeypatch, tmp_path, network)


def test_testnet_requires_read_only_hub_controller_when_manifest_controller_is_retired(monkeypatch):
    monkeypatch.setattr(captain_cli, '_get_hub_json', lambda *args, **kwargs: {
        'ok': True, 'deployment': {'chain_id': 42424241, 'contract_address': ACTIVE_ESCROW,
                                   'bridge_controller_address': STALE_CONTROLLER}
    })
    captain_cli._require_hub_bridge_completion_ready(
        'https://testnet-hub.greatlibrary.io', chain_id=42424241,
        contract_address=ACTIVE_ESCROW, controller_address='', timeout_s=5,
    )
    monkeypatch.setattr(captain_cli, '_get_hub_json', lambda *args, **kwargs: {
        'ok': True, 'deployment': {'chain_id': 42424241, 'contract_address': ACTIVE_ESCROW}
    })
    with pytest.raises(captain_cli.CaptainCliError, match='valid controller'):
        captain_cli._require_hub_bridge_completion_ready(
            'https://testnet-hub.greatlibrary.io', chain_id=42424241,
            contract_address=ACTIVE_ESCROW, controller_address='', timeout_s=5,
        )


def test_mainnet_hub_controller_may_differ_from_historical_hub_admin(monkeypatch):
    active_mainnet_hub_controller = '0x' + '5' * 40
    monkeypatch.setattr(captain_cli, '_get_hub_json', lambda *args, **kwargs: {
        'ok': True, 'deployment': {
            'chain_id': 42424240, 'contract_address': ACTIVE_ESCROW,
            'bridge_controller_address': active_mainnet_hub_controller,
        },
    })
    captain_cli._require_hub_bridge_completion_ready(
        'https://mainnet-hub.greatlibrary.io', chain_id=42424240,
        contract_address=ACTIVE_ESCROW, controller_address='', timeout_s=5,
    )


@pytest.mark.parametrize('wrong', ['chain', 'escrow', 'controller'])
def test_mainnet_still_refuses_incorrect_live_hub_authority(monkeypatch, wrong):
    deployment = {
        'chain_id': 42424240, 'contract_address': ACTIVE_ESCROW,
        'bridge_controller_address': '0x' + '5' * 40,
    }
    if wrong == 'chain':
        deployment['chain_id'] = 42424241
    elif wrong == 'escrow':
        deployment['contract_address'] = STALE_ESCROW
    else:
        deployment['bridge_controller_address'] = ''
    monkeypatch.setattr(captain_cli, '_get_hub_json', lambda *args, **kwargs: {
        'ok': True, 'deployment': deployment,
    })
    with pytest.raises(captain_cli.CaptainCliError, match='refusing deposit'):
        captain_cli._require_hub_bridge_completion_ready(
            'https://mainnet-hub.greatlibrary.io', chain_id=42424240,
            contract_address=ACTIVE_ESCROW, controller_address='', timeout_s=5,
        )
