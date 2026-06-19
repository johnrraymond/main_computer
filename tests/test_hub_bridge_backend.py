from __future__ import annotations

import json
import subprocess
from pathlib import Path

from main_computer.hub_bridge_backend import DevChainHubBridgeBackend, HubBridgeBackendError, MockChainHubBridgeBackend, build_hub_bridge_backend


def _write_wallet(path: Path, *, address: str, private_key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"address": address, "private_key": private_key}) + "\n",
        encoding="utf-8",
    )


def _write_deployment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    addresses = {
        "requester": "0x1111111111111111111111111111111111111111",
        "controller": "0x2222222222222222222222222222222222222222",
        "worker": "0x3333333333333333333333333333333333333333",
        "escrow": "0x4444444444444444444444444444444444444444",
    }
    _write_wallet(
        tmp_path / "runtime" / "deployments" / "dev" / "runs" / "unit" / "smoke-client-wallet-42424242.json",
        address=addresses["requester"],
        private_key="0x" + "1" * 64,
    )
    _write_wallet(
        tmp_path / "runtime" / "deployments" / "dev" / "runs" / "unit" / "hub-admin-wallet-42424242.json",
        address=addresses["controller"],
        private_key="0x" + "2" * 64,
    )
    deployment_path = tmp_path / "runtime" / "deployments" / "dev" / "latest.json"
    deployment_path.parent.mkdir(parents=True, exist_ok=True)
    deployment_path.write_text(
        json.dumps(
            {
                "chain": {"container_rpc_url": "http://chain-unit:8545", "network": "unit-network"},
                "contracts": {"hub_credit_bridge_escrow": {"address": addresses["escrow"]}},
                "smoke_client": {
                    "address": addresses["requester"],
                    "wallet_path": "runtime/deployments/dev/runs/unit/smoke-client-wallet-42424242.json",
                },
                "hub_admin": {
                    "address": addresses["controller"],
                    "wallet_path": "runtime/deployments/dev/runs/unit/hub-admin-wallet-42424242.json",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return deployment_path, addresses


def test_mock_chain_hub_bridge_backend_is_noop() -> None:
    backend = MockChainHubBridgeBackend()

    assert backend.deposit_confirmation_metadata({"deposit_id": "dep"}) == {"bridge_backend": "mock-chain"}
    assert backend.payout_confirmation_metadata({"payout_id": "pay"}) == {"bridge_backend": "mock-chain"}


def test_dev_chain_hub_bridge_backend_records_deposit_and_payout_metadata(tmp_path: Path) -> None:
    deployment_path, addresses = _write_deployment(tmp_path)
    commands: list[list[str]] = []

    def fake_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"transactionHash": "0x" + f"{len(commands):064x}"}) + "\n",
            stderr="",
        )

    backend = DevChainHubBridgeBackend.from_deployment(repo_root=tmp_path, deployment_path=deployment_path)
    backend.adapter.command_runner = fake_runner  # type: ignore[misc]

    deposit_metadata = backend.deposit_confirmation_metadata(
        {
            "deposit_id": "bdep-unit",
            "wallet_address": addresses["requester"],
            "credits": 200,
        }
    )
    payout_metadata = backend.payout_confirmation_metadata(
        {
            "payout_id": "bpayout-unit",
            "wallet_address": addresses["worker"],
            "worker_node_id": "node-1",
            "credits": 2,
        }
    )

    assert len(commands) == 3
    assert deposit_metadata["bridge_backend"] == "dev-chain"
    assert deposit_metadata["dev_chain"]["transaction_hashes"] == ["0x" + "1".zfill(64), "0x" + "2".zfill(64)]
    assert payout_metadata["dev_chain"]["transaction_hashes"] == ["0x" + "3".zfill(64)]
    assert payout_metadata["dev_chain"]["movement"]["amount_units"] == 2


def test_public_contract_config_allows_signer_disabled_startup_without_private_deployment(tmp_path: Path) -> None:
    contracts_path = tmp_path / "main_computer" / "config" / "testnet_contracts.json"
    contracts_path.parent.mkdir(parents=True, exist_ok=True)
    contracts_path.write_text(
        json.dumps({"hub_credit_bridge_escrow": "0x5555555555555555555555555555555555555555"}) + "\n",
        encoding="utf-8",
    )
    missing_deployment_path = tmp_path / "runtime" / "deployments" / "testnet" / "latest.json"

    backend = build_hub_bridge_backend(
        backend_name="dev-chain",
        repo_root=tmp_path,
        dev_chain_deployment_path=missing_deployment_path,
        contracts_path=contracts_path,
        network_key="testnet",
        chain_rpc_url="https://testnet-rpc.greatlibrary.io",
        allow_missing_bridge_signer=True,
    )

    status = backend.status()  # type: ignore[attr-defined]
    assert status["backend"] == "dev-chain"
    assert status["mode"] == "contract-address-only"
    assert status["escrow_address"] == "0x5555555555555555555555555555555555555555"
    assert status["network_key"] == "testnet"
    assert status["chain_rpc_url"] == "https://testnet-rpc.greatlibrary.io"
    assert status["signer_configured"] is False
    assert status["smoke_bridge_enabled"] is False
    assert status["smoke_client_wallet_address"] is None
    assert status["write_operations_enabled"] is False
    assert status["missing_deployment_path"] == str(missing_deployment_path)

    try:
        backend.deposit_confirmation_metadata(
            {
                "deposit_id": "dep-no-signer",
                "wallet_address": "0x1111111111111111111111111111111111111111",
                "credits": 1,
            }
        )
    except HubBridgeBackendError as exc:
        assert "bridge signer is not configured for testnet" in str(exc)
    else:
        raise AssertionError("signer-disabled contract backend should fail bridge writes closed")

def test_public_contract_config_requires_explicit_missing_signer_allowance(tmp_path: Path) -> None:
    contracts_path = tmp_path / "main_computer" / "config" / "testnet_contracts.json"
    contracts_path.parent.mkdir(parents=True, exist_ok=True)
    contracts_path.write_text(
        json.dumps({"hub_credit_bridge_escrow": "0x5555555555555555555555555555555555555555"}) + "\n",
        encoding="utf-8",
    )
    missing_deployment_path = tmp_path / "runtime" / "deployments" / "testnet" / "latest.json"

    try:
        build_hub_bridge_backend(
            backend_name="dev-chain",
            repo_root=tmp_path,
            dev_chain_deployment_path=missing_deployment_path,
            contracts_path=contracts_path,
            network_key="testnet",
            chain_rpc_url="https://testnet-rpc.greatlibrary.io",
            allow_missing_bridge_signer=False,
        )
    except HubBridgeBackendError as exc:
        message = str(exc)
        assert "missing dev-chain deployment file" in message
        assert "allow_missing_bridge_signer" in message
    else:
        raise AssertionError("public contract fallback should require allow_missing_bridge_signer=True")



def test_private_deployment_manifest_does_not_enable_smoke_bridge_by_default(tmp_path: Path) -> None:
    deployment_path, addresses = _write_deployment(tmp_path)
    contracts_path = tmp_path / "main_computer" / "config" / "testnet_contracts.json"
    contracts_path.parent.mkdir(parents=True, exist_ok=True)
    contracts_path.write_text(
        json.dumps({"hub_credit_bridge_escrow": addresses["escrow"]}) + "\n",
        encoding="utf-8",
    )

    backend = build_hub_bridge_backend(
        backend_name="dev-chain",
        repo_root=tmp_path,
        dev_chain_deployment_path=deployment_path,
        contracts_path=contracts_path,
        network_key="testnet",
        chain_rpc_url="https://testnet-rpc.greatlibrary.io",
        allow_missing_bridge_signer=True,
    )

    status = backend.status()  # type: ignore[attr-defined]
    assert status["mode"] == "contract-address-only"
    assert status["smoke_bridge_enabled"] is False
    assert status["smoke_client_wallet_address"] is None
    assert status["write_operations_enabled"] is False
    assert status["missing_deployment_path"] is None


def test_private_deployment_manifest_requires_explicit_smoke_bridge_or_signer_profile(tmp_path: Path) -> None:
    deployment_path, addresses = _write_deployment(tmp_path)
    contracts_path = tmp_path / "main_computer" / "config" / "testnet_contracts.json"
    contracts_path.parent.mkdir(parents=True, exist_ok=True)
    contracts_path.write_text(
        json.dumps({"hub_credit_bridge_escrow": addresses["escrow"]}) + "\n",
        encoding="utf-8",
    )

    try:
        build_hub_bridge_backend(
            backend_name="dev-chain",
            repo_root=tmp_path,
            dev_chain_deployment_path=deployment_path,
            contracts_path=contracts_path,
            network_key="testnet",
            allow_missing_bridge_signer=False,
        )
    except HubBridgeBackendError as exc:
        message = str(exc)
        assert "smoke bridge mode is not enabled" in message
        assert "smoke_client wallet metadata" in message
    else:
        raise AssertionError("private smoke deployment manifests should not be selected by default")


def test_private_deployment_manifest_can_enable_explicit_smoke_bridge(tmp_path: Path) -> None:
    deployment_path, addresses = _write_deployment(tmp_path)

    backend = build_hub_bridge_backend(
        backend_name="dev-chain",
        repo_root=tmp_path,
        dev_chain_deployment_path=deployment_path,
        network_key="dev",
        enable_smoke_bridge=True,
    )

    status = backend.status()  # type: ignore[attr-defined]
    assert status["mode"] == "smoke-bridge"
    assert status["smoke_bridge_enabled"] is True
    assert status["signer_configured"] is True
    assert status["smoke_client_wallet_address"] == addresses["requester"]
    assert status["bridge_controller_address"] == addresses["controller"]
    assert status["write_operations_enabled"] is True
