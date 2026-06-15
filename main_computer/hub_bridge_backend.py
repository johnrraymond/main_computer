from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from main_computer.credit_units import credit_wei_to_whole_credits_floor
from main_computer.dev_chain_bridge import DevChainBridgeAdapter, DevChainBridgeError, DevChainBridgeMovement


class HubBridgeBackendError(RuntimeError):
    """Raised when a Hub-owned bridge backend cannot complete its chain-side work."""


class HubBridgeBackend(Protocol):
    """Hub-side bridge backend used by bridge confirm endpoints.

    The mock-chain ledger remains the permanent fake backend.  The dev-chain
    backend records real HubCreditBridgeEscrow movements before the Hub/FDB
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


class DevChainHubBridgeBackend:
    """Hub-owned dev-chain backend for HubCreditBridgeEscrow movements."""

    name = "dev-chain"

    def __init__(self, adapter: DevChainBridgeAdapter) -> None:
        self.adapter = adapter

    @classmethod
    def from_deployment(
        cls,
        *,
        repo_root: Path,
        deployment_path: Path,
    ) -> "DevChainHubBridgeBackend":
        try:
            adapter = DevChainBridgeAdapter.from_deployment(
                repo_root=repo_root,
                deployment_path=deployment_path,
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
            "escrow_address": self.escrow_address,
            "bridge_controller_address": self.bridge_controller_address,
            "requester_wallet_address": self.requester_wallet_address,
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
) -> HubBridgeBackend:
    clean = str(backend_name or "mock-chain").strip().lower()
    if clean in {"", "mock", "mock-chain", "mock-chain-lite"}:
        return MockChainHubBridgeBackend()
    if clean in {"dev", "dev-chain", "devchain"}:
        if dev_chain_deployment_path is None:
            raise HubBridgeBackendError("dev-chain bridge backend requires a dev-chain deployment path.")
        deployment_path = dev_chain_deployment_path
        if not deployment_path.is_absolute():
            deployment_path = repo_root / deployment_path
        return DevChainHubBridgeBackend.from_deployment(repo_root=repo_root, deployment_path=deployment_path)
    raise HubBridgeBackendError(f"unknown Hub bridge backend {backend_name!r}; expected mock-chain or dev-chain.")


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
