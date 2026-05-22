from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from main_computer.config import MainComputerConfig
from main_computer.energy_chain import EnergyChainClient


ENG_WEI = 10**18
DEFAULT_FAUCET_AMOUNT_WEI = ENG_WEI
MAX_FAUCET_AMOUNT_WEI = 10 * ENG_WEI
_ADDRESS_PREFIX = "0x"


class DevFaucetError(RuntimeError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def xlag_dev_faucet(
    config: MainComputerConfig,
    chain: EnergyChainClient,
    payload: dict[str, Any],
    *,
    remote_addr: str | None = None,
) -> dict[str, Any]:
    """Fund a browser wallet with native ENG on a local dev chain.

    This is deliberately a dev faucet, not a contract permission path. It sends
    native currency from the deterministic local Anvil faucet account through the
    configured RPC endpoint. It is enabled only for local dev runtime sources and
    loopback RPC/client addresses.
    """

    _require_local_request(remote_addr)
    _require_dev_runtime(config)
    _require_loopback_rpc(config.energy_chain_rpc_url)

    target = _normalize_address(payload.get("address") or payload.get("target") or payload.get("account"))
    amount_wei = _parse_amount_wei(payload)

    faucet = _faucet_address(config)
    if not faucet:
        raise DevFaucetError("No dev faucet account is configured in the current deployment runtime.", status=409)

    try:
        chain_id = _hex_to_int(chain.rpc("eth_chainId"))
    except Exception as exc:
        raise DevFaucetError(f"Dev chain is not reachable: {exc}", status=502) from exc

    if config.xlag_chain_id is not None and chain_id != config.xlag_chain_id:
        raise DevFaucetError(
            f"Connected chain id {chain_id} does not match expected {config.xlag_chain_id}.",
            status=409,
        )

    try:
        balance_before = chain.get_balance(target)
        faucet_balance_before = chain.get_balance(faucet)
        tx_hash = str(
            chain.rpc(
                "eth_sendTransaction",
                [
                    {
                        "from": faucet,
                        "to": target,
                        "value": hex(amount_wei),
                    }
                ],
            )
        )
    except Exception as exc:
        raise DevFaucetError(
            "Dev faucet transfer failed. The local dev RPC must expose the deterministic Anvil faucet account as an unlocked account. "
            f"RPC error: {exc}",
            status=502,
        ) from exc

    return {
        "ok": True,
        "mode": "local-dev-faucet",
        "chain_id": chain_id,
        "rpc_url": config.energy_chain_rpc_url,
        "runtime_source": config.dev_chain_runtime_source,
        "runtime_path": str(config.dev_chain_runtime_path) if config.dev_chain_runtime_path else None,
        "from": faucet,
        "to": target,
        "amount_wei": str(amount_wei),
        "amount_eng": _format_eng(amount_wei),
        "target_balance_before_wei": str(balance_before),
        "target_balance_before_eng": _format_eng(balance_before),
        "faucet_balance_before_wei": str(faucet_balance_before),
        "tx_hash": tx_hash,
        "note": "Native ENG was sent from the local dev faucet account; no reserve funds or contract permissions were touched.",
    }


def _require_local_request(remote_addr: str | None) -> None:
    if not remote_addr:
        return
    if remote_addr in {"127.0.0.1", "::1", "localhost"} or remote_addr.startswith("127."):
        return
    raise DevFaucetError("Dev faucet is only available to local browser requests.", status=403)


def _require_dev_runtime(config: MainComputerConfig) -> None:
    source = str(config.dev_chain_runtime_source or "")
    if source not in {"deployment-runtime", "runtime-dev-chain"}:
        raise DevFaucetError("Dev faucet requires a published local dev deployment runtime.", status=403)

    path = config.dev_chain_runtime_path
    if not path:
        return
    environment = _runtime_environment(path)
    if environment and environment != "dev":
        raise DevFaucetError(f"Dev faucet is disabled for deployment environment {environment!r}.", status=403)


def _runtime_environment(path: Path) -> str | None:
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict):
        value = data.get("environment")
        if value is not None:
            return str(value).strip().lower()
    return None


def _require_loopback_rpc(rpc_url: str | None) -> None:
    if not rpc_url:
        raise DevFaucetError("Dev faucet requires a configured local dev RPC URL.", status=409)
    parsed = urlsplit(str(rpc_url))
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "::1"} or host.startswith("127."):
        return
    raise DevFaucetError(f"Dev faucet refuses non-loopback RPC host {host or '<missing>'}.", status=403)


def _normalize_address(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith(_ADDRESS_PREFIX):
        body = text[2:]
    else:
        body = text
    if len(body) != 40 or any(ch not in "0123456789abcdef" for ch in body):
        raise DevFaucetError("A valid 0x-prefixed target wallet address is required.", status=400)
    return "0x" + body


def _faucet_address(config: MainComputerConfig) -> str | None:
    for office in config.dev_chain_offices or ():
        if str(office.get("office") or "").upper() == "O0" and office.get("address"):
            return _normalize_address(office.get("address"))
    if config.dev_chain_offices:
        return _normalize_address(config.dev_chain_offices[0].get("address"))
    return None


def _parse_amount_wei(payload: dict[str, Any]) -> int:
    if payload.get("amount_wei") not in {None, ""}:
        try:
            amount = int(str(payload["amount_wei"]), 0)
        except ValueError as exc:
            raise DevFaucetError("amount_wei must be an integer.", status=400) from exc
    elif payload.get("amount_eng") not in {None, ""}:
        try:
            amount = int(Decimal(str(payload["amount_eng"])) * ENG_WEI)
        except (InvalidOperation, ValueError) as exc:
            raise DevFaucetError("amount_eng must be a decimal number.", status=400) from exc
    else:
        amount = DEFAULT_FAUCET_AMOUNT_WEI

    if amount <= 0:
        raise DevFaucetError("Faucet amount must be greater than zero.", status=400)
    if amount > MAX_FAUCET_AMOUNT_WEI:
        raise DevFaucetError("Faucet amount is too large for the local dev faucet.", status=400)
    return amount


def _hex_to_int(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError(f"Expected hex string result, got {type(value).__name__}")
    return int(value, 16)


def _format_eng(wei: int) -> str:
    value = Decimal(int(wei)) / Decimal(ENG_WEI)
    return format(value.normalize(), "f")
