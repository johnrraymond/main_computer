from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_smoke_client():
    spec = importlib.util.spec_from_file_location(
        "smoke_hub_network_client",
        ROOT / "scripts" / "smoke_hub_network_client.py",
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class Profile:
    network_key = "test"
    chain_id = 42424241
    chain_rpc_url = "http://127.0.0.1:30010"


def test_validate_manifest_requires_matching_network_chain_rpc_and_smoke_client() -> None:
    smoke = load_smoke_client()
    manifest = {
        "schema": "main-computer.deployment.v1",
        "environment": "test",
        "chain": {
            "chain_id": 42424241,
            "rpc_url": "http://127.0.0.1:30010",
        },
        "contracts": {
            "hub_credit_bridge_escrow": {
                "address": "0x1111111111111111111111111111111111111111",
            },
        },
        "smoke_client": {
            "address": "0x2222222222222222222222222222222222222222",
            "wallet_path": "runtime/deployments/test/smoke-client-wallet-42424241.json",
        },
    }

    validated = smoke.validate_manifest(Profile, manifest)

    assert validated["smoke_client"]["address"] == "0x2222222222222222222222222222222222222222"
    assert validated["contracts"]["hub_credit_bridge_escrow"]["address"] == "0x1111111111111111111111111111111111111111"


def test_validate_manifest_rejects_missing_smoke_client() -> None:
    smoke = load_smoke_client()
    manifest = {
        "schema": "main-computer.deployment.v1",
        "environment": "test",
        "chain": {
            "chain_id": 42424241,
            "rpc_url": "http://127.0.0.1:30010",
        },
        "contracts": {"hub_credit_bridge_escrow": {"address": "0x1111111111111111111111111111111111111111"}},
    }

    try:
        smoke.validate_manifest(Profile, manifest)
    except smoke.SmokeFailure as exc:
        assert "smoke_client.address" in str(exc)
    else:
        raise AssertionError("expected manifest without smoke client to fail")


def test_load_smoke_client_wallet_requires_chain_and_manifest_address(tmp_path: Path) -> None:
    smoke = load_smoke_client()
    wallet_path = tmp_path / "runtime" / "deployments" / "test" / "smoke-client-wallet-42424241.json"
    wallet_path.parent.mkdir(parents=True)
    wallet_path.write_text(
        json.dumps(
            {
                "schema": "main-computer.smoke-client-wallet.v1",
                "chain_id": 42424241,
                "address": "0x2222222222222222222222222222222222222222",
                "private_key": "0x" + "3" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    wallet = smoke.load_smoke_client_wallet(
        {
            "address": "0x2222222222222222222222222222222222222222",
            "wallet_path": "runtime/deployments/test/smoke-client-wallet-42424241.json",
        },
        expected_chain_id=42424241,
        repo_root=tmp_path,
    )

    assert wallet["address"] == "0x2222222222222222222222222222222222222222"
    assert wallet["private_key"] == "0x" + "3" * 64
    assert wallet["_path"] == str(wallet_path)


def test_load_deployment_manifest_prefers_selected_network_latest_over_current(tmp_path: Path) -> None:
    smoke = load_smoke_client()
    test_latest = tmp_path / "runtime" / "deployments" / "test" / "latest.json"
    current = tmp_path / "runtime" / "deployments" / "dev" / "latest.json"
    test_latest.parent.mkdir(parents=True)
    current.parent.mkdir(parents=True, exist_ok=True)
    test_latest.write_text(json.dumps({"environment": "test", "marker": "test-latest"}), encoding="utf-8")
    current.write_text(json.dumps({"environment": "dev", "marker": "current-dev"}), encoding="utf-8")

    path, payload = smoke.load_deployment_manifest("test", repo_root=tmp_path)

    assert path == test_latest
    assert payload["marker"] == "test-latest"


def test_retired_level4_smoke_refuses_before_any_http(monkeypatch):
    smoke = load_smoke_client()
    import pytest
    def forbidden(*args, **kwargs):
        pytest.fail('retired credit-and-worker-claim smoke attempted network')
    monkeypatch.setattr(smoke, 'http_json', forbidden)
    with pytest.raises(smoke.SmokeFailure, match='Retired'):
        smoke.run_level4_paid_credit_flow(
            hub_url='http://127.0.0.1:8780', network='test',
            smoke_wallet_address='0x' + '2'*40,
            scope='unit', timeout=1, funding_credits=10_000, worker_price=1234,
        )


def public_fixture(smoke, monkeypatch, tmp_path, network='testnet'):
    registry = smoke.load_hub_network_registry()
    profile = registry.get(network)
    records = smoke.public_contract_records(profile)
    manifest = {'schema': 'main-computer.deployment.v1', 'environment': network,
                'chain': {'chain_id': profile.chain_id, 'rpc_url': profile.chain_rpc_url},
                'contracts': records}
    path = smoke.default_manifest_path(network, repo_root=tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest))
    base = {'ok': True, '_http_status': 200, 'service': 'main_computer.stable_hub'}
    contract = {'auth': 'multisession-wallet', 'worker_live_session_transport': 'websocket',
                'routing': 'entry-hub-reserves-with-worker-home-hub-then-returns-concrete-execution-hub-url'}
    hub = {'hub_id': 'a', 'hub_url': profile.hub_url}
    payloads = {
        '/health': {**base, 'hub_id': 'a', 'cluster_id': 'cluster'},
        '/api/hub/v1/hub-identity': {**base, **hub, 'cluster_id': 'cluster', 'contract': contract,
             'network': {'network_key': network, 'network_kind': network, 'chain_id': str(profile.chain_id)}},
        '/api/hub/v1/topology': {**base, 'hub_id': 'b', 'contract': contract, 'topology': {
             'kind': 'main_computer.stable_hub_topology.v1', 'cluster_id': 'cluster',
             'hubs': [hub, {'hub_id': 'b', 'hub_url': profile.hub_url}], 'entry_urls': [profile.hub_url]}},
    }
    calls = []
    def http(method, url, **kw):
        assert method == 'GET'
        assert url.startswith(profile.hub_url + '/')
        calls.append(url)
        return payloads[url.removeprefix(profile.hub_url)]
    def rpc(url, method, params=None, **kw):
        assert url == profile.chain_rpc_url
        calls.append(method)
        if method == 'eth_getCode':
            assert params[0] in {r['address'] for r in records.values()}
            return '0x6001'
        return {'eth_chainId': hex(profile.chain_id), 'eth_blockNumber': '0xa',
                'eth_getBlockByNumber': {'baseFeePerGas': '0x1'}, 'eth_estimateGas': '0x10000'}[method]
    monkeypatch.setattr(smoke, 'http_json', http)
    monkeypatch.setattr(smoke, 'rpc_json', rpc)
    monkeypatch.setattr(smoke, 'load_smoke_client_wallet', lambda *a, **k: (_ for _ in ()).throw(AssertionError('private wallet read')))
    return profile, manifest, path, payloads, calls


def test_public_mainnet_uses_stable_contract_not_legacy_status(monkeypatch, tmp_path):
    smoke = load_smoke_client()
    profile, _, _, _, calls = public_fixture(smoke, monkeypatch, tmp_path, 'mainnet')
    result = smoke.run_smoke(network='mainnet', repo_root=tmp_path, skip_level4=True)
    assert result['ok'] and result['canonical_live_ok']
    assert result['hub']['topology_hub_id'] == 'b'  # entry load balancer may route GETs differently
    assert result['hub']['identity_hub_id'] == 'a'
    assert calls[:3] == [profile.hub_url + p for p in ('/health', '/api/hub/v1/hub-identity', '/api/hub/v1/topology')]
    assert 'eth_getBalance' not in calls


def test_public_testnet_stale_manifest_still_probes_canonical_but_blocks_release(monkeypatch, tmp_path):
    smoke = load_smoke_client()
    _, manifest, path, _, calls = public_fixture(smoke, monkeypatch, tmp_path)
    manifest['chain']['rpc_url'] = 'http://198.199.75.153:30010'
    manifest['contracts'] = {**manifest['contracts'], 'hub_credit_bridge_escrow': {'address': '0x' + '1'*40}}
    path.write_text(json.dumps(manifest))
    original = path.read_bytes()
    result = smoke.run_smoke(network='testnet', repo_root=tmp_path, skip_level4=True)
    assert result['canonical_live_ok'] and not result['ok']
    assert result['checks']['manifest'] is False
    assert result['manifest_evidence']['rpc_matches'] is False
    assert 'hub_credit_bridge_escrow' in result['manifest_evidence']['contract_address_differences']
    assert 'eth_getCode' in calls and path.read_bytes() == original


def test_public_wrong_hub_network_still_checks_chain_but_blocks(monkeypatch, tmp_path):
    smoke = load_smoke_client()
    _, _, _, payloads, _ = public_fixture(smoke, monkeypatch, tmp_path)
    payloads['/api/hub/v1/hub-identity']['network']['network_key'] = 'mainnet'
    result = smoke.run_smoke(network='testnet', repo_root=tmp_path, skip_level4=True)
    assert not result['ok'] and not result['checks']['hub']
    assert result['checks']['chain'] and result['checks']['contracts']


def test_public_wrong_manifest_chain_blocks_even_if_registry_matches(monkeypatch, tmp_path):
    smoke = load_smoke_client()
    _, manifest, path, _, _ = public_fixture(smoke, monkeypatch, tmp_path)
    manifest['chain']['chain_id'] = 42424240
    path.write_text(json.dumps(manifest))
    result = smoke.run_smoke(network='testnet', repo_root=tmp_path, skip_level4=True)
    assert result['canonical_live_ok'] and not result['ok']
    assert 'chain ID mismatch' in result['errors']['manifest']


def test_public_missing_code_is_not_success(monkeypatch, tmp_path):
    smoke = load_smoke_client()
    _, _, _, _, _ = public_fixture(smoke, monkeypatch, tmp_path)
    original = smoke.rpc_json
    monkeypatch.setattr(smoke, 'rpc_json', lambda url, method, *a, **k: '0x' if method == 'eth_getCode' else original(url, method, *a, **k))
    result = smoke.run_smoke(network='testnet', repo_root=tmp_path, skip_level4=True)
    assert not result['ok'] and not result['checks']['contracts']


def test_public_failure_report_persists_and_returns_nonzero(monkeypatch, tmp_path):
    smoke = load_smoke_client()
    result = {'ok': False, 'network': 'testnet', 'scope': 'canonical-public-network-read-only',
              'checks': {'hub': False}, 'errors': {'hub': 'test failure'}}
    monkeypatch.setattr(smoke, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(smoke, 'run_smoke', lambda **kw: result)
    assert smoke.main(['--network', 'testnet', '--skip-level4']) == 1
    assert json.loads((tmp_path / 'runtime/hub/hub_network_client_smoke_testnet.json').read_text()) == result
