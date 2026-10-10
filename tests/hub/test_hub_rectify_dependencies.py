from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.hub_control import rectify_dependencies as rectify
from tools.hub_control.common.errors import HubControlError
from tools.hub_control.common.models import HubContext
from tools.hub_control.common.state import read_accepted, _atomic_write
from tools.hub_control.common.canonical import canonical_bytes


def make_ctx(tmp_path: Path):
    ctx = HubContext.from_repo(tmp_path)
    accepted = {
        "schema": "main-computer.hub-accepted.v1", "network": "mainnet", "generation": 10,
        "cluster_id": "main-computer-mainnet-hubs",
        "hubs": [
            {"hub_id": "mainneta-hub1", "controller_id": "coolify-a", "host_id": "coolify-a", "public_url": "https://a.invalid",
             "hub_admin_address": "0x" + "aa" * 20,
             "fdb_contract": {"generation": 8, "sha256": "fdb8"}, "chain_contract": {"generation": 8, "sha256": "chain8"}},
            {"hub_id": "mainnetc-hub1", "controller_id": "coolify-c", "host_id": "coolify-c", "public_url": "https://c.invalid",
             "hub_admin_address": "0x" + "cc" * 20,
             "fdb_contract": {"generation": 8, "sha256": "fdb8"}, "chain_contract": {"generation": 6, "sha256": "chain6"}},
        ],
        "fdb_contract": {"generation": 8, "sha256": "fdb8"},
        "chain_contract": {"generation": 8, "sha256": "chain8"},
    }
    _atomic_write(ctx.hub_state_root / 'mainnet' / 'accepted.json', canonical_bytes(accepted) + b'\n')
    return ctx, accepted


def install_fakes(monkeypatch, *, no_c_assignment=False, broken_app=None, failed=None):
    runs = {"claimed": [], "deployed": [], "authorized": [], "observed": [], "chain_generation": 8}
    addresses = {"mainneta-hub1": "0x"+"aa"*20, "mainnetc-hub1": "0x"+"cc"*20}
    def load(_ctx): return {"test": "private"}
    def preview(_p, *, network, hub_id):
        return {"address": addresses[hub_id], "source": "reserve" if no_c_assignment and hub_id.endswith('c-hub1') else "assigned"}
    def fake_target(_ctx, network, private, member, members, fdb, chain):
        return {"hub_id": member["hub_id"], "network": network,
                "application_name": f"main-computer-{member['hub_id']}",
                "chain_contract": chain.payload, "fdb_contract": fdb.payload,
                "public_url": member["public_url"]}
    def inspect(t):
        if t['hub_id'] == broken_app: return {"present": False}
        return {"present": True, "application_uuid": 'uuid-'+t['hub_id'], "placement_mismatch": False}
    def claim(_ctx, *, network, hub_id, operation_id):
        runs['claimed'].append(hub_id)
        if no_c_assignment and hub_id.endswith('c-hub1') and runs['chain_generation'] == 8:
            runs['chain_generation'] = 9
        return {"address": addresses[hub_id], "private_key": "0x"+"01"*32, "private_state_path": 'dummy'}
    def authorize(_ctx, *, network, target, admin_address):
        runs['authorized'].append(target['hub_id'])
        return {"verified": True}
    def deploy(t):
        runs['deployed'].append(t['hub_id'])
        if t['hub_id'] == failed: raise HubControlError('HUB_DEPLOY_TEST_FAILURE', 'simulated failure')
        return {"application_uuid": t['application_uuid'], "action": "updated"}
    def observe(t, *, wait_timeout_s):
        runs['observed'].append((t['hub_id'], wait_timeout_s))
        return {"verified": True}
    def chain(_ctx, network):
        g = runs['chain_generation']
        return SimpleNamespace(generation=g, sha256=f'chain{g}', payload={"generation": g, "sha256": f'chain{g}'},
                               reference=lambda: {"generation": g, "sha256": f'chain{g}'})
    def fdb(_ctx, network):
        return SimpleNamespace(generation=8, sha256='fdb8', payload={"generation": 8, "sha256": 'fdb8'},
                               reference=lambda: {"generation": 8, "sha256": 'fdb8'})
    monkeypatch.setattr(rectify, 'load_private', load)
    monkeypatch.setattr(rectify, '_preview_claims', lambda p, n, members: {m['hub_id']: preview(p, network=n, hub_id=m['hub_id']) for m in members})
    monkeypatch.setattr(rectify, 'reserve_admin', claim)
    monkeypatch.setattr(rectify, 'private_key_to_address', lambda key: addresses[runs['claimed'][-1]])
    monkeypatch.setattr(rectify, 'load_current_chain_contract', chain)
    monkeypatch.setattr(rectify, 'load_current_fdb_contract', fdb)
    monkeypatch.setattr(rectify, 'deployment_target', lambda *_args, **_kw: {})
    monkeypatch.setattr(rectify, '_target', fake_target)
    return runs, inspect, deploy, observe, authorize


def run(ctx, funcs, **kwargs):
    runs, inspect, deploy, observe, authorize = funcs
    return rectify.rectify_network(ctx, 'mainnet', inspect_application=inspect, deployer=deploy,
                                   observer=observe, authorizer=authorize, **kwargs)


def test_preview_does_not_claim_or_deploy_or_modify_state(tmp_path, monkeypatch):
    ctx, original = make_ctx(tmp_path)
    fakes = install_fakes(monkeypatch)
    result = run(ctx, fakes)
    assert result['status'] == 'preview'
    assert result['members'][1]['old_chain_generation'] == 6
    assert result['members'][1]['current_chain_generation'] == 8
    assert not fakes[0]['claimed'] and not fakes[0]['deployed']
    assert read_accepted(ctx, 'mainnet') == original


def test_stale_c_redeployed_only_and_accepted_generation_advanced(tmp_path, monkeypatch):
    ctx, _ = make_ctx(tmp_path)
    fakes = install_fakes(monkeypatch)
    result = run(ctx, fakes, execute=True, confirmed=True)
    assert result['status'] == 'rectified'
    assert result['accepted_generation'] == 11
    assert fakes[0]['deployed'] == ['mainnetc-hub1']
    accepted = read_accepted(ctx, 'mainnet')
    assert accepted['generation'] == 11
    assert all(h['chain_contract'] == {"generation": 8, "sha256": 'chain8'} for h in accepted['hubs'])
    assert len(accepted['hubs']) == 2


def test_new_wallet_advances_chain_and_both_hubs_refresh(tmp_path, monkeypatch):
    ctx, _ = make_ctx(tmp_path)
    fakes = install_fakes(monkeypatch, no_c_assignment=True)
    result = run(ctx, fakes, execute=True, confirmed=True)
    assert result['status'] == 'rectified'
    assert fakes[0]['deployed'] == ['mainneta-hub1', 'mainnetc-hub1']
    accepted = read_accepted(ctx, 'mainnet')
    assert all(h['chain_contract']['generation'] == 9 for h in accepted['hubs'])


def test_no_application_refuses_before_claim_or_escrow_mutation(tmp_path, monkeypatch):
    ctx, original = make_ctx(tmp_path)
    fakes = install_fakes(monkeypatch, broken_app='mainnetc-hub1')
    with pytest.raises(HubControlError) as err:
        run(ctx, fakes, execute=True, confirmed=True)
    assert err.value.code == 'HUB_RECTIFY_APPLICATION_MISSING'
    assert not fakes[0]['claimed'] and not fakes[0]['authorized']
    assert read_accepted(ctx, 'mainnet') == original


def test_failed_deployment_preserves_previous_accepted_generation(tmp_path, monkeypatch):
    ctx, original = make_ctx(tmp_path)
    fakes = install_fakes(monkeypatch, failed='mainnetc-hub1')
    with pytest.raises(HubControlError) as err:
        run(ctx, fakes, execute=True, confirmed=True)
    assert err.value.code == 'HUB_DEPLOY_TEST_FAILURE'
    assert read_accepted(ctx, 'mainnet') == original


def test_repair_is_idempotent(tmp_path, monkeypatch):
    ctx, _ = make_ctx(tmp_path)
    fakes = install_fakes(monkeypatch)
    run(ctx, fakes, execute=True, confirmed=True)
    result = run(ctx, fakes, execute=True, confirmed=True)
    assert result['status'] == 'already-current'
    assert read_accepted(ctx, 'mainnet')['generation'] == 11
    assert fakes[0]['deployed'] == ['mainnetc-hub1']


def test_execute_requires_explicit_confirmation(tmp_path, monkeypatch):
    ctx, original = make_ctx(tmp_path)
    fakes = install_fakes(monkeypatch)
    with pytest.raises(HubControlError) as err:
        run(ctx, fakes, execute=True, confirmed=False)
    assert err.value.code == 'HUB_RECTIFY_MUTATION_NOT_AUTHORIZED'
    assert not fakes[0]['claimed']
    assert read_accepted(ctx, 'mainnet') == original


def test_multi_hub_reserve_simulation_assigns_distinct_without_mutating_private():
    from tools.mother.common.ethereum_identity import private_key_to_address
    def wallet(i):
        key = f'0x{i:064x}'
        return {"address": private_key_to_address(key), "private_key": key}
    private = {"networks": {"mainnet": {"wallets": {"hub_admin_reserve": {
        'reserve1': wallet(1), 'reserve2': wallet(2), 'reserve3': wallet(3)}}, "node_seed_material": {}}}}
    before = copy.deepcopy(private)
    members = [{"hub_id": "mainneta-hub1"}, {"hub_id": "mainnetc-hub1"}]
    candidate = rectify._preview_claims(private, 'mainnet', members)
    assert candidate['mainneta-hub1']['address'] != candidate['mainnetc-hub1']['address']
    assert private == before


def test_reserve_exhaustion_fails_before_any_private_mutation():
    from tools.mother.common.ethereum_identity import private_key_to_address
    key = f'0x{1:064x}'
    private = {"networks": {"mainnet": {"wallets": {"hub_admin_reserve": {
        'reserve1': {"address": private_key_to_address(key), "private_key": key}}},
        "node_seed_material": {}}}}
    before = copy.deepcopy(private)
    members = [{"hub_id": "mainneta-hub1"}, {"hub_id": "mainnetc-hub1"}]
    with pytest.raises(HubControlError) as error:
        rectify._preview_claims(private, 'mainnet', members)
    assert error.value.code == 'HUB_ADMIN_RESERVE_EXHAUSTED'
    assert private == before
