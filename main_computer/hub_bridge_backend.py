from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from main_computer.contract_config import get_contract_record, load_contract_config
from main_computer.credit_units import credit_wei_to_whole_credits_floor
from main_computer.dev_chain_bridge import (
    HUB_CREDIT_BRIDGE_ESCROW_KEY,
    DevChainBridgeAdapter,
    DevChainBridgeError,
    DevChainBridgeMovement,
)


class HubBridgeBackendError(RuntimeError):
    """Raised when a Hub-owned bridge backend cannot complete its chain-side work."""


class HubBridgeBackend(Protocol):
    """Hub-side bridge backend used by bridge confirm endpoints.

    The dev-chain backend is the default contract-backed path.  The mock-chain
    backend remains the explicit fake/lab backend.  The dev-chain backend records real HubCreditBridgeEscrow movements before the Hub/FDB
    ledger marks a deposit or payout confirmed.
    """

    name: str

    def deposit_confirmation_metadata(self, deposit: dict[str, Any]) -> dict[str, Any]:
        ...

    def payout_confirmation_metadata(self, payout: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class MockChainHubBridgeBackend:
    """No-op backend for the existing mocked chain-lite path."""

    name: str = "mock-chain"

    def deposit_confirmation_metadata(self, deposit: dict[str, Any]) -> dict[str, Any]:
        return {"bridge_backend": self.name}

    def payout_confirmation_metadata(self, payout: dict[str, Any]) -> dict[str, Any]:
        return {"bridge_backend": self.name}


@dataclass(frozen=True)
class ContractOnlyHubBridgeBackend:
    """Contract-aware backend used when signer/private deployment files are absent.

    Remote testnet images contain public contract-address config, but should not
    require private wallet files just to boot and report Hub health.  Write-side
    bridge operations still fail closed until signer material is configured.
    """

    escrow_address: str
    contracts_path: Path
    network_key: str
    missing_deployment_path: Path | None = None
    chain_rpc_url: str | None = None
    name: str = "dev-chain"

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "mode": "contract-address-only",
            "escrow_address": self.escrow_address,
            "contract_address_source": str(self.contracts_path),
            "contracts_path": str(self.contracts_path),
            "network_key": self.network_key,
            "chain_rpc_url": self.chain_rpc_url,
            "signer_configured": False,
            "bridge_controller_address": None,
            "requester_wallet_address": None,
            "smoke_client_wallet_address": None,
            "smoke_bridge_enabled": False,
            "missing_deployment_path": str(self.missing_deployment_path) if self.missing_deployment_path else None,
            "write_operations_enabled": False,
            "signer_disabled_reason": "missing bridge signer",
        }

    def deposit_confirmation_metadata(self, deposit: dict[str, Any]) -> dict[str, Any]:
        raise HubBridgeBackendError(_missing_bridge_signer_message(self.network_key, self.contracts_path))

    def payout_confirmation_metadata(self, payout: dict[str, Any]) -> dict[str, Any]:
        raise HubBridgeBackendError(_missing_bridge_signer_message(self.network_key, self.contracts_path))


class DevChainHubBridgeBackend:
    """Explicit admin-only smoke backend for HubCreditBridgeEscrow movements.

    This path can load the deployment manifest's smoke_client wallet and may
    fabricate requester deposits for smoke tests.  Normal deployed
    requester/worker paths must not select this backend by default.
    """

    name = "dev-chain"

    def __init__(self, adapter: DevChainBridgeAdapter) -> None:
        self.adapter = adapter

    @classmethod
    def from_deployment(
        cls,
        *,
        repo_root: Path,
        deployment_path: Path,
        contracts_path: Path | None = None,
        network_key: str = "dev",
    ) -> "DevChainHubBridgeBackend":
        try:
            adapter = DevChainBridgeAdapter.from_deployment(
                repo_root=repo_root,
                deployment_path=deployment_path,
                contracts_path=contracts_path,
                network_key=network_key,
            )
        except DevChainBridgeError as exc:
            raise HubBridgeBackendError(str(exc)) from exc
        return cls(adapter)

    @property
    def escrow_address(self) -> str:
        return self.adapter.escrow_address

    @property
    def bridge_controller_address(self) -> str:
        return self.adapter.bridge_controller_wallet.address

    @property
    def requester_wallet_address(self) -> str:
        return self.adapter.requester_wallet.address

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "mode": "smoke-bridge",
            "escrow_address": self.escrow_address,
            "signer_configured": self.adapter.signer_configured,
            "bridge_controller_address": self.bridge_controller_address,
            "requester_wallet_address": self.requester_wallet_address,
            "smoke_client_wallet_address": self.requester_wallet_address,
            "smoke_bridge_enabled": True,
            "write_operations_enabled": self.adapter.signer_configured,
        }

    def deposit_confirmation_metadata(self, deposit: dict[str, Any]) -> dict[str, Any]:
        deposit_id = _required_text(deposit, "deposit_id")
        wallet_address = _required_text(deposit, "wallet_address")
        amount_units = _amount_units_from_bridge_payload(deposit)
        try:
            movement = self.adapter.record_requester_deposit(
                account_wallet_address=wallet_address,
                amount_units=amount_units,
                deposit_id=deposit_id,
                memo=f"hub dev-chain deposit {deposit_id}",
            )
        except DevChainBridgeError as exc:
            raise HubBridgeBackendError(str(exc)) from exc
        return _movement_metadata(self.name, movement, operation="deposit_confirmation")

    def payout_confirmation_metadata(self, payout: dict[str, Any]) -> dict[str, Any]:
        payout_id = _required_text(payout, "payout_id")
        worker_wallet_address = _required_text(payout, "wallet_address")
        amount_units = _amount_units_from_bridge_payload(payout)
        try:
            movement = self.adapter.record_worker_payout(
                source_account_wallet_address=self.adapter.requester_wallet.address,
                worker_wallet_address=worker_wallet_address,
                amount_units=amount_units,
                payout_id=payout_id,
                memo=f"hub dev-chain payout {payout_id}",
            )
        except DevChainBridgeError as exc:
            raise HubBridgeBackendError(str(exc)) from exc
        return _movement_metadata(self.name, movement, operation="payout_confirmation")


def build_hub_bridge_backend(
    *,
    backend_name: str,
    repo_root: Path,
    dev_chain_deployment_path: Path | None,
    contracts_path: Path | None = None,
    network_key: str = "dev",
    chain_rpc_url: str | None = None,
    allow_missing_bridge_signer: bool = False,
    enable_smoke_bridge: bool = False,
) -> HubBridgeBackend:
    clean = str(backend_name or "dev-chain").strip().lower()
    if clean in {"mock", "mock-chain", "mock-chain-lite"}:
        return MockChainHubBridgeBackend()
    if clean in {"", "dev", "dev-chain", "devchain", "contract", "contract-chain", "credit-bridge-contract", "evm-contract", "real-chain"}:
        deployment_path = _resolve_optional_repo_path(repo_root, dev_chain_deployment_path)
        resolved_contracts_path = _resolve_optional_repo_path(repo_root, contracts_path)

        if deployment_path is not None and deployment_path.exists():
            if enable_smoke_bridge:
                return DevChainHubBridgeBackend.from_deployment(
                    repo_root=repo_root,
                    deployment_path=deployment_path,
                    contracts_path=resolved_contracts_path,
                    network_key=network_key,
                )
            if resolved_contracts_path is not None and allow_missing_bridge_signer:
                contract_only = _contract_only_backend_from_contracts(
                    repo_root=repo_root,
                    contracts_path=resolved_contracts_path,
                    network_key=network_key,
                    missing_deployment_path=None,
                    chain_rpc_url=chain_rpc_url,
                )
                if contract_only is not None:
                    return contract_only
            raise HubBridgeBackendError(
                f"private dev-chain deployment manifest was found at {deployment_path}, but smoke bridge mode is not enabled. "
                "Normal deployed Hub paths must not load smoke_client wallet metadata. "
                "Use --enable-smoke-bridge only for explicit admin smoke tests, or configure a non-smoke bridge signer profile when signed bridge writes are implemented."
            )

        if resolved_contracts_path is not None:
            if not allow_missing_bridge_signer:
                if deployment_path is None:
                    raise HubBridgeBackendError(
                        "dev-chain bridge backend has a public contracts_path but no private signer deployment path; "
                        "set allow_missing_bridge_signer only for read/status-only public contract startup."
                    )
                raise HubBridgeBackendError(
                    f"missing dev-chain deployment file: {deployment_path}; "
                    "set allow_missing_bridge_signer only for read/status-only public contract startup."
                )

            contract_only = _contract_only_backend_from_contracts(
                repo_root=repo_root,
                contracts_path=resolved_contracts_path,
                network_key=network_key,
                missing_deployment_path=deployment_path,
                chain_rpc_url=chain_rpc_url,
            )
            if contract_only is not None:
                return contract_only

        if deployment_path is None:
            raise HubBridgeBackendError(
                "dev-chain bridge backend requires a private dev-chain deployment path "
                "or an explicit public contracts_path with allow_missing_bridge_signer enabled."
            )
        raise HubBridgeBackendError(f"missing dev-chain deployment file: {deployment_path}")
    raise HubBridgeBackendError(
        f"unknown Hub bridge backend {backend_name!r}; expected dev-chain/credit-bridge-contract or mock-chain."
    )


def _resolve_optional_repo_path(repo_root: Path, path: Path | None) -> Path | None:
    if path is None:
        return None
    resolved = Path(path)
    return resolved if resolved.is_absolute() else repo_root / resolved


def _contract_only_backend_from_contracts(
    *,
    repo_root: Path,
    contracts_path: Path | None,
    network_key: str,
    missing_deployment_path: Path | None,
    chain_rpc_url: str | None = None,
) -> ContractOnlyHubBridgeBackend | None:
    if contracts_path is None:
        return None
    try:
        loaded = load_contract_config(network_key, repo_root=repo_root, path=contracts_path)
    except Exception as exc:
        raise HubBridgeBackendError(f"could not load public contract config {contracts_path}: {exc}") from exc
    if loaded is None:
        return None
    source_path, payload = loaded
    escrow = get_contract_record(payload, HUB_CREDIT_BRIDGE_ESCROW_KEY)
    escrow_address = str(escrow.get("address") or "").strip()
    if not escrow_address:
        raise HubBridgeBackendError(f"contract config is missing {HUB_CREDIT_BRIDGE_ESCROW_KEY}: {source_path}")
    return ContractOnlyHubBridgeBackend(
        escrow_address=escrow_address,
        contracts_path=source_path,
        network_key=str(network_key or "dev").strip() or "dev",
        missing_deployment_path=missing_deployment_path,
        chain_rpc_url=str(chain_rpc_url or "").strip() or None,
    )


def _missing_bridge_signer_message(network_key: str, contracts_path: Path) -> str:
    return (
        f"bridge signer is not configured for {network_key}; "
        f"loaded public contract addresses from {contracts_path}, but private signer wallet metadata is absent."
    )


def _movement_metadata(backend_name: str, movement: DevChainBridgeMovement, *, operation: str) -> dict[str, Any]:
    payload = movement.to_dict()
    return {
        "bridge_backend": backend_name,
        "bridge_backend_operation": operation,
        "dev_chain": {
            "movement": payload,
            "transaction_hashes": payload.get("transaction_hashes", []),
            "contract_id": payload.get("contract_id", ""),
            "contract_address": payload.get("contract_address", ""),
        },
    }


def _required_text(payload: dict[str, Any], field_name: str) -> str:
    text = str(payload.get(field_name) or "").strip()
    if not text:
        raise HubBridgeBackendError(f"bridge payload is missing {field_name}.")
    return text


def _amount_units_from_bridge_payload(payload: dict[str, Any]) -> int:
    raw_credits = payload.get("credits")
    try:
        amount = int(raw_credits)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        try:
            amount = credit_wei_to_whole_credits_floor(int(payload.get("credit_wei", 0) or 0))
        except (TypeError, ValueError):
            amount = 0
    if amount <= 0:
        raise HubBridgeBackendError("bridge payload has no positive credit amount.")
    return amount
