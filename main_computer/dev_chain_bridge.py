from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from main_computer.contract_config import get_contract_record, load_contract_config


FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"
HUB_CREDIT_BRIDGE_ESCROW_KEY = "hub_credit_bridge_escrow"

StatusCallback = Callable[..., None]
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class DevChainWallet:
    """Local dev-chain wallet with signing material loaded from private runtime files."""

    address: str
    private_key: str
    wallet_path: Path
    role: str


@dataclass(frozen=True)
class DevChainTxResult:
    """Machine-readable summary of one dev-chain bridge transaction."""

    action: str
    transaction_hash: str
    contract_address: str
    from_address: str
    amount_units: int
    external_id: str
    contract_id: str
    command: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "transaction_hash": self.transaction_hash,
            "contract_address": self.contract_address,
            "from_address": self.from_address,
            "amount_units": self.amount_units,
            "external_id": self.external_id,
            "contract_id": self.contract_id,
            "command": list(self.command),
        }


@dataclass(frozen=True)
class DevChainBridgeMovement:
    """The deterministic C2 movement attached to one Hub bridge lifecycle event."""

    external_id: str
    contract_id: str
    amount_units: int
    contract_address: str
    transactions: tuple[DevChainTxResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "contract_id": self.contract_id,
            "amount_units": self.amount_units,
            "contract_address": self.contract_address,
            "transactions": [transaction.to_dict() for transaction in self.transactions],
            "transaction_hashes": [transaction.transaction_hash for transaction in self.transactions],
        }


class DevChainBridgeError(RuntimeError):
    """Raised when the dev-chain bridge movement cannot be recorded."""


class DevChainBridgeAdapter:
    """Smoke-grade adapter for HubCreditBridgeEscrow dev-chain movements.

    This does not replace the Hub/FDB bridge ledger yet.  It records real
    on-chain escrow movements around the existing Hub bridge lifecycle so the
    stress smoke can prove that per-run wallets and contract transactions are
    visible on the dev chain.
    """

    def __init__(
        self,
        *,
        repo_root: Path,
        rpc_url: str,
        network_name: str | None,
        escrow_address: str,
        requester_wallet: DevChainWallet,
        bridge_controller_wallet: DevChainWallet,
        foundry_image: str = FOUNDRY_IMAGE,
        command_runner: CommandRunner | None = None,
        status: StatusCallback | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.rpc_url = rpc_url
        self.network_name = network_name
        self.escrow_address = escrow_address
        self.requester_wallet = requester_wallet
        self.bridge_controller_wallet = bridge_controller_wallet
        self.foundry_image = foundry_image
        self.command_runner = command_runner or self._run_command
        self.status = status

    @classmethod
    def from_deployment(
        cls,
        *,
        repo_root: Path,
        deployment_path: Path,
        contracts_path: Path | None = None,
        network_key: str = "dev",
        command_runner: CommandRunner | None = None,
        status: StatusCallback | None = None,
    ) -> "DevChainBridgeAdapter":
        deployment = _load_json(deployment_path)
        deployment_chain = deployment.get("chain") if isinstance(deployment.get("chain"), dict) else {}

        contract_payload: dict[str, Any] | None = None
        contract_source = deployment_path
        if contracts_path is not None:
            loaded = load_contract_config(network_key, repo_root=repo_root, path=contracts_path)
            if loaded is not None:
                contract_source, contract_payload = loaded
        if contract_payload is None:
            contract_payload = deployment

        contract_chain = contract_payload.get("chain") if isinstance(contract_payload.get("chain"), dict) else {}
        escrow = get_contract_record(contract_payload, HUB_CREDIT_BRIDGE_ESCROW_KEY)
        if not escrow:
            escrow = get_contract_record(deployment, HUB_CREDIT_BRIDGE_ESCROW_KEY)
            contract_source = deployment_path
        if not isinstance(escrow, dict) or not escrow:
            raise DevChainBridgeError(f"contract config is missing {HUB_CREDIT_BRIDGE_ESCROW_KEY}: {contract_source}")
        escrow_address = str(escrow.get("address") or "").strip()
        if not _is_address(escrow_address):
            raise DevChainBridgeError(f"invalid HubCreditBridgeEscrow address in {contract_source}: {escrow_address!r}")

        rpc_url = str(
            contract_payload.get("chain_rpc_url")
            or contract_chain.get("container_rpc_url")
            or contract_chain.get("rpc_url")
            or contract_chain.get("host_rpc_url")
            or deployment_chain.get("container_rpc_url")
            or deployment_chain.get("rpc_url")
            or deployment_chain.get("host_rpc_url")
            or ""
        ).strip()
        network_name = str(
            contract_payload.get("network")
            or contract_chain.get("network")
            or deployment_chain.get("network")
            or network_key
            or ""
        ).strip() or None
        if not rpc_url:
            raise DevChainBridgeError(f"contract config is missing a dev-chain RPC URL: {contract_source}")

        requester_wallet = _load_wallet_from_record(
            repo_root=repo_root,
            deployment_path=deployment_path,
            deployment=deployment,
            key="smoke_client",
            role="requester",
        )
        bridge_controller_wallet = _load_wallet_from_record(
            repo_root=repo_root,
            deployment_path=deployment_path,
            deployment=deployment,
            key="hub_admin",
            role="bridge-controller",
        )

        adapter = cls(
            repo_root=repo_root,
            rpc_url=rpc_url,
            network_name=network_name,
            escrow_address=escrow_address,
            requester_wallet=requester_wallet,
            bridge_controller_wallet=bridge_controller_wallet,
            command_runner=command_runner,
            status=status,
        )
        adapter._emit(
            "dev_chain_bridge_adapter_ready",
            escrow_address=escrow_address,
            rpc_url=rpc_url,
            network_name=network_name,
            contract_source=str(contract_source),
            requester_wallet_address=requester_wallet.address,
            bridge_controller_address=bridge_controller_wallet.address,
        )
        return adapter

    def record_requester_deposit(
        self,
        *,
        account_wallet_address: str,
        amount_units: int,
        deposit_id: str,
        memo: str,
    ) -> DevChainBridgeMovement:
        amount = _positive_amount(amount_units, label="deposit amount")
        contract_id = bytes32_from_text(f"hub-deposit:{deposit_id}")
        self._emit(
            "dev_chain_bridge_deposit_start",
            deposit_id=deposit_id,
            contract_id=contract_id,
            account_wallet_address=account_wallet_address,
            amount_units=amount,
        )
        deposit_tx = self._send(
            action="depositFor",
            wallet=self.requester_wallet,
            function_signature="depositFor(address,uint256,bytes32,string)",
            function_args=[account_wallet_address, str(amount), contract_id, memo],
            external_id=deposit_id,
            contract_id=contract_id,
            amount_units=amount,
            value_wei=amount,
        )
        complete_tx = self._send(
            action="completeDeposit",
            wallet=self.bridge_controller_wallet,
            function_signature="completeDeposit(bytes32)",
            function_args=[contract_id],
            external_id=deposit_id,
            contract_id=contract_id,
            amount_units=amount,
        )
        movement = DevChainBridgeMovement(
            external_id=deposit_id,
            contract_id=contract_id,
            amount_units=amount,
            contract_address=self.escrow_address,
            transactions=(deposit_tx, complete_tx),
        )
        self._emit(
            "dev_chain_bridge_deposit_done",
            deposit_id=deposit_id,
            transaction_hashes=",".join(movement.to_dict()["transaction_hashes"]),
        )
        return movement

    def record_worker_payout(
        self,
        *,
        source_account_wallet_address: str,
        worker_wallet_address: str,
        amount_units: int,
        payout_id: str,
        memo: str,
    ) -> DevChainBridgeMovement:
        amount = _positive_amount(amount_units, label="payout amount")
        contract_id = bytes32_from_text(f"hub-payout:{payout_id}")
        self._emit(
            "dev_chain_bridge_payout_start",
            payout_id=payout_id,
            contract_id=contract_id,
            source_account_wallet_address=source_account_wallet_address,
            worker_wallet_address=worker_wallet_address,
            amount_units=amount,
        )
        release_tx = self._send(
            action="releaseWithdrawal",
            wallet=self.bridge_controller_wallet,
            function_signature="releaseWithdrawal(address,address,uint256,bytes32,string)",
            function_args=[
                source_account_wallet_address,
                worker_wallet_address,
                str(amount),
                contract_id,
                memo,
            ],
            external_id=payout_id,
            contract_id=contract_id,
            amount_units=amount,
        )
        movement = DevChainBridgeMovement(
            external_id=payout_id,
            contract_id=contract_id,
            amount_units=amount,
            contract_address=self.escrow_address,
            transactions=(release_tx,),
        )
        self._emit(
            "dev_chain_bridge_payout_done",
            payout_id=payout_id,
            transaction_hashes=",".join(movement.to_dict()["transaction_hashes"]),
        )
        return movement

    def _send(
        self,
        *,
        action: str,
        wallet: DevChainWallet,
        function_signature: str,
        function_args: list[str],
        external_id: str,
        contract_id: str,
        amount_units: int,
        value_wei: int | None = None,
    ) -> DevChainTxResult:
        command = self._cast_send_command(wallet, function_signature, function_args, value_wei=value_wei)
        self._emit(
            "dev_chain_bridge_tx_start",
            action=action,
            from_address=wallet.address,
            contract_address=self.escrow_address,
            amount_units=amount_units,
        )
        completed = self.command_runner(command)
        if completed.returncode != 0:
            raise DevChainBridgeError(
                f"dev-chain bridge transaction failed action={action} returncode={completed.returncode}\n"
                f"stdout={completed.stdout}\nstderr={completed.stderr}"
            )
        transaction_hash = _parse_transaction_hash((completed.stdout or "") + "\n" + (completed.stderr or ""))
        result = DevChainTxResult(
            action=action,
            transaction_hash=transaction_hash,
            contract_address=self.escrow_address,
            from_address=wallet.address,
            amount_units=amount_units,
            external_id=external_id,
            contract_id=contract_id,
            command=tuple(_redact_private_key(command)),
        )
        self._emit(
            "dev_chain_bridge_tx_done",
            action=action,
            transaction_hash=transaction_hash,
            from_address=wallet.address,
        )
        return result

    def _cast_send_command(
        self,
        wallet: DevChainWallet,
        function_signature: str,
        function_args: list[str],
        *,
        value_wei: int | None = None,
    ) -> list[str]:
        command = [shutil.which("docker") or "docker", "run", "--rm"]
        if self.network_name:
            command.extend(["--network", self.network_name])
        command.extend(
            [
                "-v",
                f"{_docker_mount_path(self.repo_root)}:/workspace",
                "-w",
                "/workspace/contracts",
                "--entrypoint",
                "cast",
                self.foundry_image,
                "send",
                self.escrow_address,
                function_signature,
                *function_args,
            ]
        )
        if value_wei is not None:
            command.extend(["--value", str(value_wei)])
        command.extend(["--rpc-url", self.rpc_url, "--private-key", wallet.private_key, "--json"])
        return command

    def _run_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=None,
        )

    def _emit(self, event: str, **fields: Any) -> None:
        if self.status is not None:
            self.status(event, **fields)


def bytes32_from_text(value: str) -> str:
    return "0x" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _positive_amount(value: int, *, label: str) -> int:
    try:
        amount = int(value)
    except (TypeError, ValueError) as exc:
        raise DevChainBridgeError(f"{label} must be an integer: {value!r}") from exc
    if amount <= 0:
        raise DevChainBridgeError(f"{label} must be positive: {value!r}")
    return amount


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DevChainBridgeError(f"missing dev-chain deployment file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DevChainBridgeError(f"invalid dev-chain deployment JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise DevChainBridgeError(f"dev-chain deployment JSON must be an object: {path}")
    return payload


def _load_wallet_from_record(
    *,
    repo_root: Path,
    deployment_path: Path,
    deployment: dict[str, Any],
    key: str,
    role: str,
) -> DevChainWallet:
    record = deployment.get(key)
    if not isinstance(record, dict):
        raise DevChainBridgeError(f"deployment is missing {key} wallet metadata: {deployment_path}")
    address = str(record.get("address") or "").strip()
    wallet_path_text = str(record.get("wallet_path") or "").strip()
    if not _is_address(address):
        raise DevChainBridgeError(f"invalid {key} wallet address in {deployment_path}: {address!r}")
    if not wallet_path_text:
        raise DevChainBridgeError(f"deployment is missing {key}.wallet_path: {deployment_path}")

    wallet_path = _resolve_runtime_path(repo_root=repo_root, deployment_path=deployment_path, path_text=wallet_path_text)
    wallet_payload = _load_json(wallet_path)
    private_key = str(wallet_payload.get("private_key") or "").strip()
    wallet_address = str(wallet_payload.get("address") or "").strip()
    if not _is_private_key(private_key):
        raise DevChainBridgeError(f"{role} wallet file does not contain a valid private_key: {wallet_path}")
    if not _is_address(wallet_address):
        raise DevChainBridgeError(f"{role} wallet file does not contain a valid address: {wallet_path}")
    if wallet_address.lower() != address.lower():
        raise DevChainBridgeError(
            f"{role} wallet file address does not match deployment metadata: {wallet_address} != {address}"
        )
    return DevChainWallet(address=address, private_key=private_key, wallet_path=wallet_path, role=role)


def _resolve_runtime_path(*, repo_root: Path, deployment_path: Path, path_text: str) -> Path:
    raw = Path(path_text)
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(repo_root / raw)
        candidates.append(deployment_path.parent / raw)
        if raw.parts and raw.parts[0] != "runtime":
            candidates.append(repo_root / "runtime" / raw)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _parse_transaction_hash(output: str) -> str:
    for text in [output]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key in ("transactionHash", "transaction_hash", "hash"):
                value = str(payload.get(key) or "").strip()
                if _is_hash32(value):
                    return value
            receipt = payload.get("receipt")
            if isinstance(receipt, dict):
                for key in ("transactionHash", "transaction_hash", "hash"):
                    value = str(receipt.get(key) or "").strip()
                    if _is_hash32(value):
                        return value
    match = re.search(r"0x[0-9a-fA-F]{64}", output)
    if match:
        return match.group(0)
    raise DevChainBridgeError(f"could not parse transaction hash from cast output: {output!r}")


def _redact_private_key(command: list[str]) -> list[str]:
    redacted = list(command)
    for index, item in enumerate(redacted[:-1]):
        if item == "--private-key":
            redacted[index + 1] = "<redacted>"
    return redacted


def _docker_mount_path(path: Path) -> str:
    resolved = path.resolve()
    if os.name == "nt":
        return resolved.as_posix()
    return str(resolved)


def _is_address(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{40}", value) is not None


def _is_private_key(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value) is not None


def _is_hash32(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"0x[0-9a-fA-F]{64}", value) is not None
