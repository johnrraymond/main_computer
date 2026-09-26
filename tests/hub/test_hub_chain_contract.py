from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.hub_control.common import chain_contract
from tools.hub_control.common.errors import HubControlError
from tools.hub_control.common.models import DependencyContract, HubContext


ROOT = Path(__file__).resolve().parents[2]
REQUIRED_ADDRESS = "0x650aa75B20315d50E7A23f7024408d637eff08Ca"
OPTIONAL_ADDRESS = "0xB318dCE9A3dd001FfE24e14a63c7f7d41F757946"




def _write_private_state(ctx: HubContext, *, generation: int = 12) -> None:
    ctx.mother_private_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.mother_private_path.write_text(
        """
networks:
  mainnet:
    chain_id: 42424240
    rpc: https://mainnet-rpc.greatlibrary.io
""".strip() + "\n",
        encoding="utf-8",
    )
    ctx.mother_metadata_path.write_text(f'{{"generation": {generation}}}\n', encoding="utf-8")


def _temp_ctx(tmp_path: Path) -> HubContext:
    return HubContext(
        repo_root=tmp_path,
        hub_state_root=tmp_path / "runtime" / "state" / "hub",
        fdb_state_root=tmp_path / "runtime" / "state" / "fdb",
        chain_state_root=tmp_path / "runtime" / "state" / "chain",
        mother_private_path=tmp_path / "runtime" / "state" / "mother" / "identity.private.yaml",
        mother_metadata_path=tmp_path / "runtime" / "state" / "mother" / "identity.private.meta.json",
    )


def _patch_registry(monkeypatch: pytest.MonkeyPatch, *, manifest_path: str = "runtime/deployments/mainnet/latest.json") -> None:
    profile = SimpleNamespace(
        chain_id=42424240,
        chain_rpc_url="https://mainnet-rpc.greatlibrary.io",
        deployment_manifest_path=Path(manifest_path),
    )
    registry = SimpleNamespace(get=lambda _network: profile)
    monkeypatch.setattr(chain_contract, "load_hub_network_registry", lambda _path: registry)


def _contract(*, required_keys: list[str] | None = None) -> DependencyContract:
    payload = {
        "schema": chain_contract.SCHEMA,
        "network": "mainnet",
        "generation": 12,
        "chain_id": 42424240,
        "rpc_url": "https://rpc.invalid",
        "contracts": {
            "hub_credit_bridge_escrow": REQUIRED_ADDRESS,
            "alpha-beta-lockout": OPTIONAL_ADDRESS,
        },
        "required_contract_keys": [] if required_keys is None else required_keys,
        "compatibility": {"json_rpc": "ethereum"},
        "sha256": "test",
    }
    return DependencyContract("chain", "mainnet", 12, "test", payload)


def test_missing_advertised_contract_code_does_not_block_hub_birth(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_rpc(_url: str, method: str, _params: list[object], *, timeout_s: float = 12.0):
        if method == "eth_chainId":
            return hex(42424240)
        assert method == "eth_getCode"
        return "0x"

    monkeypatch.setattr(chain_contract, "_rpc", fake_rpc)
    result = chain_contract.verify_chain_contract(_contract())

    assert result["verified"] is True
    assert result["required_contracts_verified"] is True
    assert result["required_contracts"] == {}
    assert result["optional_contracts"]["hub_credit_bridge_escrow"]["status"] == "missing-code"
    assert result["optional_contracts"]["alpha-beta-lockout"]["status"] == "missing-code"
    assert result["optional_stale_contracts"] == ["alpha-beta-lockout", "hub_credit_bridge_escrow"]


def test_explicit_future_required_contract_missing_code_still_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_rpc(_url: str, method: str, _params: list[object], *, timeout_s: float = 12.0):
        if method == "eth_chainId":
            return hex(42424240)
        return "0x"

    monkeypatch.setattr(chain_contract, "_rpc", fake_rpc)
    with pytest.raises(HubControlError, match="required chain contract 'hub_credit_bridge_escrow'") as excinfo:
        chain_contract.verify_chain_contract(_contract(required_keys=["hub_credit_bridge_escrow"]))
    assert excinfo.value.code == "HUB_CHAIN_CONTRACT_MISSING"


def test_empty_required_contract_list_does_not_fall_back_to_blockchain_service_requirements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    def fake_rpc(_url: str, method: str, params: list[object], *, timeout_s: float = 12.0):
        calls.append((method, tuple(params)))
        if method == "eth_chainId":
            return hex(42424240)
        return "0x"

    monkeypatch.setattr(chain_contract, "_rpc", fake_rpc)
    result = chain_contract.verify_chain_contract(_contract(required_keys=[]))

    assert result["verified"] is True
    assert result["required_contracts"] == {}
    assert any(method == "eth_getCode" and params[0] == REQUIRED_ADDRESS for method, params in calls)
    assert result["optional_contracts"]["hub_credit_bridge_escrow"]["status"] == "missing-code"


def test_optional_probe_failure_is_nonblocking_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_rpc(_url: str, method: str, params: list[object], *, timeout_s: float = 12.0):
        if method == "eth_chainId":
            return hex(42424240)
        if params[0] == OPTIONAL_ADDRESS:
            raise HubControlError("HUB_CHAIN_RPC_FAILED", "optional probe failed")
        return "0x6001600055"

    monkeypatch.setattr(chain_contract, "_rpc", fake_rpc)
    result = chain_contract.verify_chain_contract(_contract())

    assert result["verified"] is True
    assert result["optional_contracts"]["alpha-beta-lockout"]["status"] == "unverifiable"
    assert result["optional_contracts"]["hub_credit_bridge_escrow"]["status"] == "verified"
    assert result["optional_stale_contracts"] == ["alpha-beta-lockout"]


def test_published_contract_declares_no_core_hub_application_contract_requirements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _temp_ctx(tmp_path)
    _write_private_state(ctx)
    _patch_registry(monkeypatch)
    config_path = tmp_path / "main_computer" / "config" / "mainnet_contracts.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{"hub_credit_bridge_escrow":"0x650aa75B20315d50E7A23f7024408d637eff08Ca","alpha-beta-lockout":"0xB318dCE9A3dd001FfE24e14a63c7f7d41F757946"}\n',
        encoding="utf-8",
    )

    contract = chain_contract.load_current_chain_contract(ctx, "mainnet", publish=True)

    assert contract.payload["required_contract_keys"] == []
    assert contract.payload["contracts"]["hub_credit_bridge_escrow"] == REQUIRED_ADDRESS
    assert "alpha-beta-lockout" in contract.payload["contracts"]
    assert (ctx.chain_state_root / "mainnet" / "consumer-contract.json").is_file()


def test_deployment_manifest_is_primary_contract_identity_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _temp_ctx(tmp_path)
    _write_private_state(ctx)
    _patch_registry(monkeypatch)

    config_path = tmp_path / "main_computer" / "config" / "mainnet_contracts.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{"hub_credit_bridge_escrow":"0x650aa75B20315d50E7A23f7024408d637eff08Ca"}\n',
        encoding="utf-8",
    )
    manifest_path = tmp_path / "runtime" / "deployments" / "mainnet" / "latest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        '''{
  "schema": "main-computer.deployment.v1",
  "environment": "mainnet",
  "run_id": "live-run",
  "chain": {"chain_id": 42424240, "rpc_url": "https://mainnet-rpc.greatlibrary.io"},
  "contracts": {
    "hub_credit_bridge_escrow": {"address": "0xf6C11125329793730Fb99DF80C869372c2Ea1e0f"}
  }
}
'''.strip() + "\n",
        encoding="utf-8",
    )

    contract = chain_contract.load_current_chain_contract(ctx, "mainnet")

    assert contract.payload["contracts"]["hub_credit_bridge_escrow"] == "0xf6C11125329793730Fb99DF80C869372c2Ea1e0f"
    assert contract.payload["contract_source"] == {
        "kind": "deployment-manifest",
        "path": "runtime/deployments/mainnet/latest.json",
        "run_id": "live-run",
    }


def test_checked_in_contract_file_is_only_fallback_when_manifest_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _temp_ctx(tmp_path)
    _write_private_state(ctx)
    _patch_registry(monkeypatch)
    config_path = tmp_path / "main_computer" / "config" / "mainnet_contracts.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '{"hub_credit_bridge_escrow":"0x650aa75B20315d50E7A23f7024408d637eff08Ca"}\n',
        encoding="utf-8",
    )

    contract = chain_contract.load_current_chain_contract(ctx, "mainnet")

    assert contract.payload["contracts"]["hub_credit_bridge_escrow"] == REQUIRED_ADDRESS
    assert contract.payload["contract_source"] == {
        "kind": "checked-in-fallback",
        "path": "main_computer/config/mainnet_contracts.json",
    }


def test_deployment_manifest_chain_id_mismatch_blocks_contract_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _temp_ctx(tmp_path)
    _write_private_state(ctx)
    _patch_registry(monkeypatch)
    manifest_path = tmp_path / "runtime" / "deployments" / "mainnet" / "latest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        '''{
  "schema": "main-computer.deployment.v1",
  "environment": "mainnet",
  "chain": {"chain_id": 999, "rpc_url": "https://mainnet-rpc.greatlibrary.io"},
  "contracts": {
    "hub_credit_bridge_escrow": {"address": "0xf6C11125329793730Fb99DF80C869372c2Ea1e0f"}
  }
}
'''.strip() + "\n",
        encoding="utf-8",
    )

    with pytest.raises(HubControlError) as excinfo:
        chain_contract.load_current_chain_contract(ctx, "mainnet")
    assert excinfo.value.code == "HUB_CHAIN_DEPLOYMENT_ID_MISMATCH"
