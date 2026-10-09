"""Authorize the Hub's committed administrator against its active escrow contract.

Only add-hub.do calls this mutating boundary. No private keys are returned or
persisted in Hub operations; an already-authorized controller is a no-op.
"""
from __future__ import annotations

import re
import time
from typing import Any, Mapping

from tools.mother.common.ethereum_identity import is_address, is_private_key, keccak256, private_key_to_address

from .errors import HubControlError
from .models import HubContext
from .privates import load_private, network_doc
from .deployment import _hub_chain_rpc, _bridge_controller_call_data


def _hex_quantity(raw: Any, method: str) -> int:
    if not isinstance(raw, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", raw):
        raise HubControlError("HUB_BRIDGE_AUTHORIZATION_RPC_INVALID", f"{method} returned an invalid hex quantity")
    return int(raw, 16)


def _contract_bool(rpc: str, escrow: str, admin: str) -> bool:
    raw = _hub_chain_rpc(rpc, "eth_call", [{"to": escrow, "data": _bridge_controller_call_data(admin)}, "latest"])
    return _hex_quantity(raw, "isBridgeController(address)") != 0


def _owner(rpc: str, escrow: str) -> str:
    selector = "0x" + keccak256(b"owner()")[:4].hex()
    raw = _hub_chain_rpc(rpc, "eth_call", [{"to": escrow, "data": selector}, "latest"])
    if not isinstance(raw, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", raw):
        raise HubControlError("HUB_BRIDGE_OWNER_UNVERIFIED", "live escrow owner() returned invalid data")
    return "0x" + raw[-40:]


def _send_owner_authorization(*, rpc: str, chain_id: int, escrow: str, admin: str,
                              deployer_address: str, deployer_key: str) -> str:
    """Submit one signed owner transaction; return only its hash."""
    try:
        from eth_account import Account  # type: ignore
    except ImportError as exc:
        raise HubControlError("HUB_BRIDGE_SIGNER_UNAVAILABLE", "eth-account is required for escrow authorization") from exc
    nonce = _hex_quantity(_hub_chain_rpc(rpc, "eth_getTransactionCount", [deployer_address, "pending"]), "eth_getTransactionCount")
    gas_price = _hex_quantity(_hub_chain_rpc(rpc, "eth_gasPrice", []), "eth_gasPrice")
    data = "0x" + keccak256(b"addBridgeController(address)")[:4].hex() + admin[2:].lower().rjust(64, "0")
    tx_call = {"from": deployer_address, "to": escrow, "data": data, "value": "0x0"}
    # Estimation catches most authorization/revert errors before we sign/send.
    estimate = _hex_quantity(_hub_chain_rpc(rpc, "eth_estimateGas", [tx_call]), "eth_estimateGas")
    gas = max(estimate + max(10_000, estimate // 5), 75_000)
    tx = {"chainId": chain_id, "nonce": nonce, "to": escrow, "value": 0,
          "gas": gas, "gasPrice": gas_price, "data": data}
    try:
        signed = Account.sign_transaction(tx, deployer_key)
        raw = getattr(signed, "raw_transaction", None)
        if raw is None:
            raw = getattr(signed, "rawTransaction")
        raw_hex = "0x" + bytes(raw).hex()
        expected_hash = "0x" + bytes(signed.hash).hex()
    except Exception as exc:
        raise HubControlError("HUB_BRIDGE_AUTHORIZATION_SIGNING_FAILED", f"could not sign owner transaction: {type(exc).__name__}") from exc
    # Do not include the raw signed transaction (or keys) in public error text.
    try:
        submitted_hash = _hub_chain_rpc(rpc, "eth_sendRawTransaction", [raw_hex])
    except Exception as exc:
        raise HubControlError("HUB_BRIDGE_AUTHORIZATION_SEND_UNCERTAIN", f"owner authorization submission failed or is uncertain; tx_hash={expected_hash}; check chain and retry add-hub: {type(exc).__name__}") from exc
    if not isinstance(submitted_hash, str) or submitted_hash.lower() != expected_hash.lower():
        raise HubControlError("HUB_BRIDGE_AUTHORIZATION_HASH_MISMATCH", "chain returned a different owner authorization transaction hash")
    return expected_hash


def ensure_bridge_controller(ctx: HubContext, *, network: str, target: Mapping[str, Any],
                             admin_address: str, timeout_s: float = 120.0, poll_s: float = 3.0) -> dict[str, Any]:
    """Verify or grant one admin permission before Coolify is touched.

    Repeated calls are safe: authorization is queried before every submission.
    An uncertain send/receipt is reported without issuing a second transaction.
    """
    chain = target.get("chain_contract") if isinstance(target.get("chain_contract"), Mapping) else {}
    rpc = str(chain.get("rpc_url") or "")
    chain_id = int(chain.get("chain_id") or 0)
    escrow = str((chain.get("contracts") or {}).get("hub_credit_bridge_escrow") or "")
    if not rpc or chain_id <= 0 or not is_address(escrow) or not is_address(admin_address):
        raise HubControlError("HUB_BRIDGE_AUTHORIZATION_CONFIG_INVALID", "active Chain RPC, chain ID, escrow, or Hub admin address is missing")
    observed_chain = _hex_quantity(_hub_chain_rpc(rpc, "eth_chainId", []), "eth_chainId")
    if observed_chain != chain_id:
        raise HubControlError("HUB_BRIDGE_AUTHORIZATION_CHAIN_MISMATCH", "RPC chain ID differs from prepared Hub Chain authority")
    code = _hub_chain_rpc(rpc, "eth_getCode", [escrow, "latest"])
    if not isinstance(code, str) or code.lower() in ("", "0x", "0x0"):
        raise HubControlError("HUB_BRIDGE_ESCROW_NOT_LIVE", "active Hub escrow has no bytecode")
    if _contract_bool(rpc, escrow, admin_address):
        return {"verified": True, "already_authorized": True, "controller": admin_address, "escrow": escrow}

    wallets = network_doc(load_private(ctx), network).get("wallets") or {}
    deployer = wallets.get("deployer") if isinstance(wallets, Mapping) else None
    if not isinstance(deployer, Mapping):
        raise HubControlError("HUB_BRIDGE_OWNER_IDENTITY_MISSING", "Mother deployer wallet is missing")
    deployer_key = deployer.get("private_key")
    deployer_address = deployer.get("address")
    if not is_private_key(deployer_key) or not is_address(deployer_address):
        raise HubControlError("HUB_BRIDGE_OWNER_IDENTITY_INVALID", "Mother deployer wallet has invalid address or key")
    if private_key_to_address(deployer_key).lower() != deployer_address.lower():
        raise HubControlError("HUB_BRIDGE_OWNER_IDENTITY_INVALID", "Mother deployer private key does not match its address")
    if _owner(rpc, escrow).lower() != deployer_address.lower():
        raise HubControlError("HUB_BRIDGE_OWNER_MISMATCH", "the configured Mother deployer is not the live escrow owner")
    tx_hash = _send_owner_authorization(rpc=rpc, chain_id=chain_id, escrow=escrow,
                                        admin=admin_address, deployer_address=deployer_address,
                                        deployer_key=deployer_key)
    deadline = time.monotonic() + timeout_s
    while True:
        receipt = _hub_chain_rpc(rpc, "eth_getTransactionReceipt", [tx_hash])
        if isinstance(receipt, Mapping):
            if _hex_quantity(receipt.get("status"), "eth_getTransactionReceipt.status") != 1:
                raise HubControlError("HUB_BRIDGE_AUTHORIZATION_REVERTED", f"owner authorization reverted: {tx_hash}")
            break
        if time.monotonic() >= deadline:
            raise HubControlError("HUB_BRIDGE_AUTHORIZATION_RECEIPT_TIMEOUT", f"authorization tx not confirmed: {tx_hash}; retry add-hub after verifying chain state")
        time.sleep(min(poll_s, max(0, deadline - time.monotonic())))
    if not _contract_bool(rpc, escrow, admin_address):
        raise HubControlError("HUB_BRIDGE_AUTHORIZATION_UNVERIFIED", "successful owner transaction did not authorize assigned Hub admin")
    return {"verified": True, "already_authorized": False, "controller": admin_address,
            "escrow": escrow, "transaction_hash": tx_hash}
