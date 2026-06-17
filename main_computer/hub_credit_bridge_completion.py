from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from main_computer.config import MainComputerConfig
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_credit_models import normalize_address, positive_int


COMPUTE_CREDIT_BASE_UNITS = 10**18
HUB_CREDIT_BRIDGE_COMPLETION_MODE = "hub-credit-bridge-completion-v1"

_DEPOSIT_RECORD_SELECTOR = "546fcf39"
_COMPLETED_DEPOSIT_UNITS_SELECTOR = "861064f2"
_COMPLETE_DEPOSIT_SELECTOR = "8c503dc4"

_HEX_CHARS = set("0123456789abcdefABCDEF")


@dataclass(frozen=True)
class DepositRecord:
    exists: bool
    completed: bool
    account: str
    payer: str
    amount_units: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "completed": self.completed,
            "account": self.account,
            "payer": self.payer,
            "amount_units": str(self.amount_units),
        }


@dataclass(frozen=True)
class BridgeDeployment:
    chain_id: int
    rpc_url: str
    contract_address: str
    bridge_controller_address: str
    hub_admin_address: str
    hub_admin_wallet_path: Path
    deployment_manifest_path: Path

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "rpc_url": self.rpc_url,
            "contract_address": self.contract_address,
            "bridge_controller_address": self.bridge_controller_address,
            "hub_admin_address": self.hub_admin_address,
            "hub_admin_wallet_path": str(self.hub_admin_wallet_path),
            "deployment_manifest_path": str(self.deployment_manifest_path),
        }


def _is_hex(value: str, *, chars: int) -> bool:
    text = str(value or "").strip()
    return len(text) == chars + 2 and text.startswith("0x") and all(ch in _HEX_CHARS for ch in text[2:])


def normalize_bytes32(value: Any, *, field_name: str = "deposit_id") -> str:
    text = str(value or "").strip().lower()
    if not _is_hex(text, chars=64):
        raise ValueError(f"{field_name} must be a 32-byte 0x-prefixed hex value.")
    return text


def normalize_evm_address(value: Any, *, field_name: str = "address") -> str:
    text = normalize_address(str(value or ""))
    if not _is_hex(text, chars=40):
        raise ValueError(f"{field_name} must be a 20-byte 0x-prefixed hex address.")
    return text


def _checksum_evm_address_for_transaction(value: Any, *, field_name: str = "address") -> str:
    """Return an eth-account compatible transaction address.

    The app stores and compares EVM addresses in normalized lowercase form, but
    eth-account validates transaction address fields more strictly and rejects
    some lowercase addresses. Convert only at the transaction-signing boundary.
    """
    normalized = normalize_evm_address(value, field_name=field_name)
    try:
        from eth_utils import to_checksum_address  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "eth-utils is required for Hub admin transaction signing. "
            "Install the project requirements or run bootstrap before completing deposits."
        ) from exc
    try:
        return str(to_checksum_address(normalized))
    except Exception as exc:
        raise ValueError(f"{field_name} must be a valid EIP-55 compatible EVM address.") from exc


def _hex_to_int(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError(f"Expected hex string result, got {type(value).__name__}.")
    return int(value, 16)


def _strip_0x(value: str) -> str:
    return value[2:] if str(value).startswith("0x") else str(value)


def _encode_bytes32(value: str) -> str:
    return _strip_0x(normalize_bytes32(value))


def _encode_address(value: str) -> str:
    return normalize_evm_address(value)[2:].rjust(64, "0")


def _decode_word_bool(word: str) -> bool:
    return int(word, 16) != 0


def _decode_word_uint(word: str) -> int:
    return int(word, 16)


def _decode_word_address(word: str) -> str:
    return "0x" + word[-40:].lower()


def _split_words(result_hex: str, *, min_words: int) -> list[str]:
    data = str(result_hex or "").strip()
    if data.startswith("0x"):
        data = data[2:]
    if len(data) < 64 * min_words:
        raise ValueError("Contract call returned too little data.")
    return [data[index : index + 64] for index in range(0, len(data), 64)]


class JsonRpcClient:
    def __init__(self, rpc_url: str, *, timeout_s: float = 10.0) -> None:
        self.rpc_url = str(rpc_url or "").strip()
        if not self.rpc_url:
            raise ValueError("RPC URL is required.")
        self.timeout_s = max(1.0, float(timeout_s or 10.0))
        self._next_id = 1

    def rpc(self, method: str, params: list[Any] | None = None) -> Any:
        payload = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params or [], "id": self._next_id},
            ensure_ascii=False,
        ).encode("utf-8")
        self._next_id += 1
        request = Request(
            self.rpc_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_s) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("RPC returned a non-object response.")
        if data.get("error"):
            raise RuntimeError(data["error"])
        return data.get("result")

    def eth_call(self, *, to: str, data: str) -> str:
        return str(self.rpc("eth_call", [{"to": to, "data": data}, "latest"]))

    def chain_id(self) -> int:
        return _hex_to_int(self.rpc("eth_chainId"))

    def get_transaction_count(self, address: str) -> int:
        return _hex_to_int(self.rpc("eth_getTransactionCount", [address, "pending"]))

    def gas_price(self) -> int:
        return _hex_to_int(self.rpc("eth_gasPrice"))

    def estimate_gas(self, tx: dict[str, Any]) -> int:
        return _hex_to_int(self.rpc("eth_estimateGas", [tx]))

    def send_raw_transaction(self, raw_tx: bytes | str) -> str:
        if isinstance(raw_tx, bytes):
            raw_hex = "0x" + raw_tx.hex()
        else:
            raw_hex = str(raw_tx)
        return str(self.rpc("eth_sendRawTransaction", [raw_hex]))

    def transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        result = self.rpc("eth_getTransactionReceipt", [tx_hash])
        return result if isinstance(result, dict) else None


class HubCreditBridgeContractClient:
    def __init__(
        self,
        *,
        rpc_url: str,
        contract_address: str,
        chain_id: int,
        admin_private_key: str,
        admin_address: str,
        timeout_s: float = 10.0,
        receipt_timeout_s: float = 30.0,
        rpc_client: JsonRpcClient | None = None,
    ) -> None:
        self.rpc = rpc_client or JsonRpcClient(rpc_url, timeout_s=timeout_s)
        self.contract_address = normalize_evm_address(contract_address, field_name="contract_address")
        self.expected_chain_id = positive_int(chain_id)
        self.admin_private_key = str(admin_private_key or "").strip()
        if not self.admin_private_key.startswith("0x"):
            self.admin_private_key = "0x" + self.admin_private_key
        self.admin_address = normalize_evm_address(admin_address, field_name="admin_address")
        self.receipt_timeout_s = max(1.0, float(receipt_timeout_s or 30.0))

    def deposit_record(self, deposit_id: str) -> DepositRecord:
        clean_deposit_id = normalize_bytes32(deposit_id)
        result = self.rpc.eth_call(
            to=self.contract_address,
            data="0x" + _DEPOSIT_RECORD_SELECTOR + _encode_bytes32(clean_deposit_id),
        )
        words = _split_words(result, min_words=5)
        return DepositRecord(
            exists=_decode_word_bool(words[0]),
            completed=_decode_word_bool(words[1]),
            account=_decode_word_address(words[2]),
            payer=_decode_word_address(words[3]),
            amount_units=_decode_word_uint(words[4]),
        )

    def completed_deposit_units(self, account: str) -> int:
        result = self.rpc.eth_call(
            to=self.contract_address,
            data="0x" + _COMPLETED_DEPOSIT_UNITS_SELECTOR + _encode_address(account),
        )
        words = _split_words(result, min_words=1)
        return _decode_word_uint(words[0])

    def complete_deposit(self, deposit_id: str) -> dict[str, Any]:
        clean_deposit_id = normalize_bytes32(deposit_id)
        data = "0x" + _COMPLETE_DEPOSIT_SELECTOR + _encode_bytes32(clean_deposit_id)
        return self._send_contract_transaction(data=data)

    def _send_contract_transaction(self, *, data: str) -> dict[str, Any]:
        try:
            from eth_account import Account  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "eth-account is required for Hub admin transaction signing. "
                "Install the project requirements or run bootstrap before completing deposits."
            ) from exc

        chain_id = self.rpc.chain_id()
        if self.expected_chain_id and chain_id != self.expected_chain_id:
            raise RuntimeError(f"RPC chain id {chain_id} does not match deployment chain id {self.expected_chain_id}.")

        try:
            derived = normalize_evm_address(Account.from_key(self.admin_private_key).address, field_name="admin_address")
        except Exception as exc:
            raise RuntimeError("Could not derive Hub admin address from private key.") from exc
        if derived != self.admin_address:
            raise RuntimeError(f"Hub admin wallet address mismatch: metadata={self.admin_address}, key={derived}.")

        signing_admin_address = _checksum_evm_address_for_transaction(self.admin_address, field_name="admin_address")
        signing_contract_address = _checksum_evm_address_for_transaction(
            self.contract_address,
            field_name="contract_address",
        )

        tx_for_estimate = {"from": signing_admin_address, "to": signing_contract_address, "value": "0x0", "data": data}
        try:
            gas = int(self.rpc.estimate_gas(tx_for_estimate) * 1.2) + 10_000
        except Exception:
            gas = 250_000

        tx = {
            "chainId": chain_id,
            "nonce": self.rpc.get_transaction_count(signing_admin_address),
            "to": signing_contract_address,
            "value": 0,
            "data": data,
            "gas": gas,
            "gasPrice": self.rpc.gas_price(),
        }
        signed = Account.sign_transaction(tx, self.admin_private_key)
        raw_tx = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
        if raw_tx is None:
            raise RuntimeError("eth-account did not return a raw transaction.")
        tx_hash = self.rpc.send_raw_transaction(raw_tx)
        receipt = self.wait_for_receipt(tx_hash)
        return {"tx_hash": tx_hash, "receipt": receipt}

    def wait_for_receipt(self, tx_hash: str) -> dict[str, Any]:
        deadline = time.time() + self.receipt_timeout_s
        while time.time() <= deadline:
            receipt = self.rpc.transaction_receipt(tx_hash)
            if isinstance(receipt, dict):
                status = receipt.get("status")
                if isinstance(status, str) and status.lower() == "0x0":
                    raise RuntimeError(f"completeDeposit transaction failed: {tx_hash}")
                return receipt
            time.sleep(0.5)
        raise TimeoutError(f"Timed out waiting for completeDeposit receipt: {tx_hash}")


def _repo_root_for_deployment_manifest(path: Path) -> Path:
    parent = path.parent
    if parent.name and parent.parent.name == "deployments" and parent.parent.parent.name == "runtime":
        return parent.parent.parent.parent
    if parent.name == "deployments" and parent.parent.name == "runtime":
        return parent.parent.parent
    return parent.parent.parent


def _candidate_deployment_manifest_paths(config: MainComputerConfig | None) -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("MAIN_COMPUTER_DEPLOYMENT_MANIFEST_PATH")
    if env_path:
        candidates.append(Path(env_path))

    cwd = Path.cwd().resolve()
    for root in [cwd, *cwd.parents]:
        candidates.append(root / "runtime" / "deployments" / "dev" / "latest.json")

    if config is not None:
        for raw in [config.hub_root, config.workspace]:
            try:
                base = Path(raw).resolve()
            except Exception:
                continue
            for root in [base, *base.parents]:
                candidates.append(root / "runtime" / "deployments" / "dev" / "latest.json")

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def load_bridge_deployment(config: MainComputerConfig | None = None, *, deployment_manifest_path: Path | None = None) -> BridgeDeployment:
    paths = [Path(deployment_manifest_path)] if deployment_manifest_path else _candidate_deployment_manifest_paths(config)
    last_error = ""
    for path in paths:
        try:
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            contracts = payload.get("contracts") if isinstance(payload.get("contracts"), dict) else {}
            escrow = contracts.get("hub_credit_bridge_escrow") if isinstance(contracts.get("hub_credit_bridge_escrow"), dict) else {}
            hub_admin = payload.get("hub_admin") if isinstance(payload.get("hub_admin"), dict) else {}
            chain = payload.get("chain") if isinstance(payload.get("chain"), dict) else {}
            contract_address = normalize_evm_address(escrow.get("address"), field_name="contracts.hub_credit_bridge_escrow.address")
            controller = normalize_evm_address(
                escrow.get("bridge_controller_address") or hub_admin.get("address"),
                field_name="contracts.hub_credit_bridge_escrow.bridge_controller_address",
            )
            admin_address = normalize_evm_address(hub_admin.get("address") or controller, field_name="hub_admin.address")
            wallet_raw = str(hub_admin.get("wallet_path") or "").strip()
            if not wallet_raw:
                raise ValueError("hub_admin.wallet_path is missing.")
            repo_root = _repo_root_for_deployment_manifest(path)
            wallet_path = Path(wallet_raw)
            if not wallet_path.is_absolute():
                wallet_path = repo_root / wallet_path
            chain_id = positive_int(escrow.get("chain_id") or chain.get("chain_id") or (config.energy_chain_id if config else 0))
            if chain_id <= 0:
                raise ValueError("deployment chain id is missing.")
            rpc_url = str(chain.get("rpc_url") or chain.get("host_rpc_url") or (config.energy_chain_rpc_url if config else "") or "").strip()
            if not rpc_url:
                raise ValueError("deployment RPC URL is missing.")
            return BridgeDeployment(
                chain_id=chain_id,
                rpc_url=rpc_url,
                contract_address=contract_address,
                bridge_controller_address=controller,
                hub_admin_address=admin_address,
                hub_admin_wallet_path=wallet_path,
                deployment_manifest_path=path,
            )
        except Exception as exc:
            last_error = f"{path}: {exc}"
            continue
    detail = f" Last error: {last_error}" if last_error else ""
    raise FileNotFoundError("Could not find a usable runtime/deployments/dev/latest.json with hub_credit_bridge_escrow metadata." + detail)


def load_hub_admin_private_key(deployment: BridgeDeployment) -> str:
    path = deployment.hub_admin_wallet_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Hub admin wallet file must contain a JSON object.")
    address = normalize_evm_address(payload.get("address"), field_name="hub admin wallet address")
    if address != deployment.hub_admin_address:
        raise ValueError(f"Hub admin wallet address {address} does not match deployment {deployment.hub_admin_address}.")
    private_key = str(payload.get("private_key") or "").strip()
    if not private_key:
        raise ValueError("Hub admin wallet private_key is missing.")
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    if not _is_hex(private_key, chars=64):
        raise ValueError("Hub admin wallet private_key must be a 32-byte 0x-prefixed hex value.")
    return private_key


class HubCreditBridgeCompletionService:
    def __init__(
        self,
        ledger: HubCreditLedger,
        config: MainComputerConfig,
        *,
        client: Any | None = None,
        deployment: BridgeDeployment | None = None,
    ) -> None:
        self.ledger = ledger
        self.config = config
        self._client = client
        self._deployment = deployment

    def status(self) -> dict[str, Any]:
        try:
            deployment = self._deployment or load_bridge_deployment(self.config)
            return {"ok": True, "mode": HUB_CREDIT_BRIDGE_COMPLETION_MODE, "deployment": deployment.as_public_dict()}
        except Exception as exc:
            return {"ok": False, "mode": HUB_CREDIT_BRIDGE_COMPLETION_MODE, "error": str(exc)}

    def complete_wallet_funding_deposit(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("wallet funding completion payload must be a JSON object.")

        deposit_id = normalize_bytes32(payload.get("deposit_id"))
        requested_wallet = str(payload.get("wallet_address") or payload.get("account") or "").strip()
        requested_wallet = normalize_evm_address(requested_wallet, field_name="wallet_address") if requested_wallet else ""

        deployment = self._deployment or load_bridge_deployment(self.config)
        client = self._client or self._build_client(deployment)

        before = client.deposit_record(deposit_id)
        if not before.exists:
            raise ValueError(f"Unknown bridge deposit: {deposit_id}")
        if before.amount_units <= 0:
            raise ValueError(f"Bridge deposit {deposit_id} has zero amount.")
        if requested_wallet and before.account != requested_wallet:
            raise ValueError(
                f"wallet_address does not match deposit account: wallet_address={requested_wallet}, deposit.account={before.account}"
            )

        tx_hash = ""
        receipt: dict[str, Any] | None = None
        completion_sent = False
        if not before.completed:
            completion = client.complete_deposit(deposit_id)
            tx_hash = str(completion.get("tx_hash") or "").strip()
            receipt = completion.get("receipt") if isinstance(completion.get("receipt"), dict) else None
            completion_sent = True

        after = client.deposit_record(deposit_id)
        completed_units = positive_int(client.completed_deposit_units(after.account or before.account))
        chain_completed_credit_wei = completed_units

        account_id = wallet_account_id(after.account or before.account)
        ledger_result = self.ledger.record_completed_bridge_deposit(
            account_id=account_id,
            owner_address=after.account or before.account,
            chain_completed_credit_wei=chain_completed_credit_wei,
            deposit_id=deposit_id,
            completion_tx_hash=tx_hash,
            chain_id=deployment.chain_id,
            contract_address=deployment.contract_address,
            completed_units=completed_units,
            deposit_amount_units=after.amount_units or before.amount_units,
            memo=f"bridge deposit completion {deposit_id}",
            metadata={
                "mode": HUB_CREDIT_BRIDGE_COMPLETION_MODE,
                "completion_sent": completion_sent,
                "receipt_block_number": _hex_to_int(receipt.get("blockNumber")) if isinstance(receipt, dict) and receipt.get("blockNumber") else 0,
            },
        )

        return {
            "ok": True,
            "mode": HUB_CREDIT_BRIDGE_COMPLETION_MODE,
            "funding_model": "hub_credit_bridge_escrow_wallet_v2",
            "deposit_id": deposit_id,
            "wallet_address": after.account or before.account,
            "account_id": account_id,
            "deposit": after.as_dict(),
            "completed_units": str(completed_units),
            "chain_completed_credit_wei": str(chain_completed_credit_wei),
            "chain_completed_credits_display": ledger_result.get("chain_completed_credits_display", "0"),
            "delta_credit_wei": str(ledger_result.get("delta_credit_wei", "0")),
            "delta_credits_display": ledger_result.get("delta_credits_display", "0"),
            "idempotent": bool(ledger_result.get("idempotent")),
            "completion_sent": completion_sent,
            "completion_tx_hash": tx_hash,
            "deployment": deployment.as_public_dict(),
            **ledger_result,
        }

    def _build_client(self, deployment: BridgeDeployment) -> HubCreditBridgeContractClient:
        return HubCreditBridgeContractClient(
            rpc_url=deployment.rpc_url,
            contract_address=deployment.contract_address,
            chain_id=deployment.chain_id,
            admin_private_key=load_hub_admin_private_key(deployment),
            admin_address=deployment.hub_admin_address,
            timeout_s=10.0,
            receipt_timeout_s=30.0,
        )
