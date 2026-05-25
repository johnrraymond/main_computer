from __future__ import annotations

import re
from typing import Any

from main_computer.config import MainComputerConfig
from main_computer.energy_chain import EnergyChainClient


COMPUTE_CREDIT_BASE_UNITS = 10**18
ENG_WEI = COMPUTE_CREDIT_BASE_UNITS  # Deprecated compatibility alias.

_SELECTORS = {
    "XLagBridgeReserve.OFFICE_COUNT()": "05cdb182",
    "XLagBridgeReserve.getOffice(uint8)": "a653a364",
    "XLagBridgeReserve.isOffice(address)": "1f18795c",
    "XLagBridgeReserve.officeIndexPlusOne(address)": "50ac700f",
    "XLagBridgeReserve.maxPayoutWei()": "e21a90a6",
    "XLagBridgeReserve.payoutDelayBlocks()": "5a8d8e42",
    "XLagBridgeReserve.resetDelayBlocks()": "68b44c2d",
    "XLagBridgeReserve.nextProposalId()": "2ab09d14",
    "XLagBridgeReserve.walletSmokeNonce()": "1a0cbd5d",
    "XLagBridgeReserve.lastWalletSmokeFinalizer()": "b22be79d",
    "XLagBridgeReserve.lastWalletSmokeOffice()": "1c13b45c",
    "XLagBridgeReserve.lastWalletSmokeId()": "3c7cfff2",
    "XLagBridgeReserve.lastWalletSmokeMemo()": "b15a70c3",
    "XLagBridgeReserve.lastWalletSmokeBlock()": "7fce9e09",
    "XLagBridgeReserve.frobNonce()": "216a323b",
    "XLagBridgeReserve.lastFrobber()": "40a5be83",
    "XLagBridgeReserve.lastFrobId()": "bf7b5956",
    "XLagBridgeReserve.lastFrobMemo()": "6f6491d0",
    "XLagBridgeReserve.lastFrobBlock()": "6fd8d6db",
}

_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def xlag_contract_status(config: MainComputerConfig) -> dict[str, Any]:
    contract_address = config.xlag_contract_address
    runtime_path = str(config.dev_chain_runtime_path) if config.dev_chain_runtime_path else None
    return {
        "model": "xlag-contract-enforced-bridge-reserve-v0",
        "contract_address": contract_address,
        "configured": bool(contract_address),
        "chain_id": config.xlag_chain_id,
        "chain_id_expected": config.xlag_chain_id,
        "alpha_beta_lockout_contract_address": config.alpha_beta_lockout_contract_address,
        "backend_signing_enabled": False,
        "native_transfers_backend_enabled": False,
        "enforcement": "smart-contract",
        "config_source": {
            "contract_address": config.xlag_contract_address_source,
            "chain_id": config.xlag_chain_id_source,
            "alpha_beta_lockout_contract_address": config.alpha_beta_lockout_contract_address_source,
            "dev_chain_runtime": config.dev_chain_runtime_source,
        },
        "dev_chain": {
            "run_id": config.dev_chain_run_id,
            "runtime_path": runtime_path,
            "runtime_source": config.dev_chain_runtime_source,
            "runtime_error": config.dev_chain_runtime_error,
            "offices": list(config.dev_chain_offices),
        },
        "live": xlag_contract_live_status(config),
        "policies": {
            "payout": {
                "captain_intent_required": True,
                "first_officer_belay_enabled": True,
                "beta_second_required": True,
                "any_office_can_contest": True,
                "contested_default": "non-execution",
            },
            "reset": {
                "approvals_required": 3,
                "total_offices": 4,
                "any_office_can_contest": True,
                "contested_default": "non-execution",
            },
        },
    }


def xlag_contract_live_status(config: MainComputerConfig, client: EnergyChainClient | None = None) -> dict[str, Any]:
    contract_address = _clean_address(config.xlag_contract_address)
    alpha_address = _clean_address(config.alpha_beta_lockout_contract_address)
    live = _empty_live_payload(config, contract_address, alpha_address)

    if not contract_address:
        live["error"] = "xlag contract address is not configured"
        return live
    if not config.energy_chain_rpc_url:
        live["error"] = "energy chain RPC URL is not configured"
        return live

    client = client or EnergyChainClient(
        rpc_url=config.energy_chain_rpc_url,
        expected_chain_id=config.xlag_chain_id,
        rpc_url_source=config.energy_chain_rpc_url_source,
        expected_chain_id_source=config.xlag_chain_id_source,
    )
    live["enabled"] = True

    try:
        chain_id = _hex_to_int(client.rpc("eth_chainId"))
        block_number = _hex_to_int(client.rpc("eth_blockNumber"))
    except Exception as exc:
        live["connected"] = False
        live["chain_id_ok"] = False
        live["error"] = str(exc)
        return live

    live.update(
        {
            "connected": True,
            "chain_id": chain_id,
            "block_number": block_number,
            "chain_id_ok": config.xlag_chain_id is None or chain_id == config.xlag_chain_id,
        }
    )

    try:
        code = client.get_code(contract_address)
        code_bytes = _code_bytes(code)
        live["has_code"] = code_bytes > 0
        live["contract_code_bytes"] = code_bytes
        if alpha_address:
            try:
                alpha_code = client.get_code(alpha_address)
                alpha_code_bytes = _code_bytes(alpha_code)
                live["alpha_beta_lockout_has_code"] = alpha_code_bytes > 0
                live["alpha_beta_lockout_code_bytes"] = alpha_code_bytes
            except Exception as exc:
                live["alpha_beta_lockout_error"] = str(exc)

        if code_bytes <= 0:
            live["error"] = "xlag contract address has no code"
            return live

        reserve_balance_wei = int(client.get_balance(contract_address))
        max_payout_wei = _call_uint(client, contract_address, "XLagBridgeReserve.maxPayoutWei()")
        payout_delay_blocks = _call_uint(client, contract_address, "XLagBridgeReserve.payoutDelayBlocks()")
        reset_delay_blocks = _call_uint(client, contract_address, "XLagBridgeReserve.resetDelayBlocks()")
        next_proposal_id = _call_uint(client, contract_address, "XLagBridgeReserve.nextProposalId()")
        office_count = _call_uint(client, contract_address, "XLagBridgeReserve.OFFICE_COUNT()")

        live.update(
            {
                "reserve_balance_wei": str(reserve_balance_wei),
                "reserve_balance_credits": _format_compute_credits(reserve_balance_wei),
                "reserve_balance_eng": _format_compute_credits(reserve_balance_wei),  # Deprecated compatibility key.
                "max_payout_wei": str(max_payout_wei),
                "max_payout_credits": _format_compute_credits(max_payout_wei),
                "max_payout_eng": _format_compute_credits(max_payout_wei),  # Deprecated compatibility key.
                "payout_delay_blocks": payout_delay_blocks,
                "reset_delay_blocks": reset_delay_blocks,
                "next_proposal_id": next_proposal_id,
                "office_count": office_count,
                "offices": _read_office_status(client, contract_address, config, office_count),
                "wallet_smoke": _read_wallet_smoke_status(client, contract_address),
                "any_user_frobber": _read_any_user_frobber_status(client, contract_address),
                "error": None,
            }
        )
    except Exception as exc:
        live["error"] = str(exc)

    return live


def _empty_live_payload(
    config: MainComputerConfig,
    contract_address: str | None,
    alpha_address: str | None,
) -> dict[str, Any]:
    return {
        "enabled": False,
        "connected": False,
        "rpc_url": config.energy_chain_rpc_url,
        "contract_address": contract_address,
        "alpha_beta_lockout_contract_address": alpha_address,
        "chain_id": None,
        "block_number": None,
        "expected_chain_id": config.xlag_chain_id,
        "chain_id_ok": None,
        "has_code": False,
        "contract_code_bytes": 0,
        "alpha_beta_lockout_has_code": None,
        "alpha_beta_lockout_code_bytes": None,
        "reserve_balance_wei": None,
        "reserve_balance_credits": None,
        "reserve_balance_eng": None,  # Deprecated compatibility key.
        "max_payout_wei": None,
        "max_payout_credits": None,
        "max_payout_eng": None,  # Deprecated compatibility key.
        "payout_delay_blocks": None,
        "reset_delay_blocks": None,
        "next_proposal_id": None,
        "office_count": None,
        "offices": [],
        "wallet_smoke": _wallet_smoke_guide_payload(),
        "any_user_frobber": _any_user_frobber_guide_payload(),
        "error": None,
    }




def _wallet_smoke_guide_payload() -> dict[str, Any]:
    return {
        "function": "finalizeWalletSmokeTest(bytes32,string)",
        "selector": "0xaa46bd04",
        "requires": "one connected X-LAG office wallet",
        "effect": "increments walletSmokeNonce and records the finalizing office, smoke id, memo, and block",
        "dev_guide_command": "python .\\dev-chain-wallet-smoke-guide.py --show-private-keys",
        "available": None,
        "nonce": None,
        "last_finalizer": None,
        "last_office": None,
        "last_smoke_id": None,
        "last_memo": None,
        "last_block": None,
        "error": None,
    }


def _any_user_frobber_guide_payload() -> dict[str, Any]:
    return {
        "function": "frobByAnyUser(bytes32,string)",
        "selector": "0xbcd0a3bf",
        "requires": "any connected browser wallet on the configured dev chain",
        "effect": "increments frobNonce and records the frobber wallet, frob id, memo, and block without moving funds",
        "available": None,
        "nonce": None,
        "last_frobber": None,
        "last_frob_id": None,
        "last_frob_memo": None,
        "last_block": None,
        "error": None,
    }


def _read_wallet_smoke_status(client: EnergyChainClient, contract_address: str) -> dict[str, Any]:
    status = _wallet_smoke_guide_payload()
    try:
        nonce = _call_uint(client, contract_address, "XLagBridgeReserve.walletSmokeNonce()")
        finalizer = _call_address(client, contract_address, "XLagBridgeReserve.lastWalletSmokeFinalizer()")
        office = _call_uint(client, contract_address, "XLagBridgeReserve.lastWalletSmokeOffice()")
        smoke_id = _call_bytes32(client, contract_address, "XLagBridgeReserve.lastWalletSmokeId()")
        memo = _call_string(client, contract_address, "XLagBridgeReserve.lastWalletSmokeMemo()")
        block_number = _call_uint(client, contract_address, "XLagBridgeReserve.lastWalletSmokeBlock()")
        status.update(
            {
                "available": True,
                "nonce": nonce,
                "last_finalizer": finalizer,
                "last_office": office,
                "last_smoke_id": smoke_id,
                "last_memo": memo,
                "last_block": block_number,
                "error": None,
            }
        )
    except Exception as exc:
        # Older deployments will not have the wallet-smoke helper yet. Keep the
        # main live status healthy and surface this as an opt-in redeploy cue.
        status.update({"available": False, "error": str(exc)})
    return status


def _read_any_user_frobber_status(client: EnergyChainClient, contract_address: str) -> dict[str, Any]:
    status = _any_user_frobber_guide_payload()
    try:
        nonce = _call_uint(client, contract_address, "XLagBridgeReserve.frobNonce()")
        frobber = _call_address(client, contract_address, "XLagBridgeReserve.lastFrobber()")
        frob_id = _call_bytes32(client, contract_address, "XLagBridgeReserve.lastFrobId()")
        memo = _call_string(client, contract_address, "XLagBridgeReserve.lastFrobMemo()")
        block_number = _call_uint(client, contract_address, "XLagBridgeReserve.lastFrobBlock()")
        status.update(
            {
                "available": True,
                "nonce": nonce,
                "last_frobber": frobber,
                "last_frob_id": frob_id,
                "last_frob_memo": memo,
                "last_block": block_number,
                "error": None,
            }
        )
    except Exception as exc:
        # Older deployments will not have the any-user frobber helper yet. Keep
        # the main live status healthy and surface this as a redeploy cue.
        status.update({"available": False, "error": str(exc)})
    return status


def _read_office_status(
    client: EnergyChainClient,
    contract_address: str,
    config: MainComputerConfig,
    office_count: int,
) -> list[dict[str, Any]]:
    configured_offices = list(config.dev_chain_offices)
    count = max(0, min(int(office_count), 16))
    if configured_offices:
        count = min(count, len(configured_offices))
    offices: list[dict[str, Any]] = []

    for index in range(count):
        expected_record = configured_offices[index] if index < len(configured_offices) else {}
        expected_address = _clean_address(expected_record.get("address")) if isinstance(expected_record, dict) else None
        actual_address = _call_address(
            client,
            contract_address,
            "XLagBridgeReserve.getOffice(uint8)",
            _abi_uint(index),
        )
        office_record: dict[str, Any] = {
            "index": index,
            "office": expected_record.get("office") if isinstance(expected_record, dict) else None,
            "title": expected_record.get("title") if isinstance(expected_record, dict) else None,
            "expected_address": expected_address,
            "actual_address": actual_address,
            "matches_expected": expected_address is None or actual_address == expected_address,
        }
        if expected_address:
            office_record["is_office"] = _call_bool(
                client,
                contract_address,
                "XLagBridgeReserve.isOffice(address)",
                _abi_address(expected_address),
            )
            office_record["office_index_plus_one"] = _call_uint(
                client,
                contract_address,
                "XLagBridgeReserve.officeIndexPlusOne(address)",
                _abi_address(expected_address),
            )
        offices.append(office_record)

    return offices


def _call_uint(client: EnergyChainClient, to: str, selector_key: str, *args: str) -> int:
    return _decode_uint(client.eth_call(to, _call_data(_SELECTORS[selector_key], *args)))


def _call_bool(client: EnergyChainClient, to: str, selector_key: str, *args: str) -> bool:
    return _decode_uint(client.eth_call(to, _call_data(_SELECTORS[selector_key], *args))) != 0


def _call_address(client: EnergyChainClient, to: str, selector_key: str, *args: str) -> str:
    word = _word_at(client.eth_call(to, _call_data(_SELECTORS[selector_key], *args)))
    return "0x" + word[-40:].lower()


def _call_bytes32(client: EnergyChainClient, to: str, selector_key: str, *args: str) -> str:
    return "0x" + _word_at(client.eth_call(to, _call_data(_SELECTORS[selector_key], *args))).lower()


def _call_string(client: EnergyChainClient, to: str, selector_key: str, *args: str) -> str:
    return _decode_string(client.eth_call(to, _call_data(_SELECTORS[selector_key], *args)))


def _call_data(selector: str, *encoded_args: str) -> str:
    return "0x" + selector + "".join(encoded_args)


def _abi_uint(value: int) -> str:
    if value < 0:
        raise ValueError("negative ABI uint not supported")
    return hex(value)[2:].rjust(64, "0")


def _abi_address(value: str) -> str:
    address = _clean_address(value)
    if address is None:
        raise ValueError(f"not an Ethereum address: {value!r}")
    return address[2:].rjust(64, "0")


def _word_at(hex_result: str, index: int = 0) -> str:
    clean = str(hex_result).removeprefix("0x")
    start = index * 64
    end = start + 64
    if len(clean) < end:
        raise ValueError(f"short ABI result: {hex_result!r}")
    return clean[start:end]


def _decode_uint(hex_result: str, index: int = 0) -> int:
    return int(_word_at(hex_result, index), 16)


def _decode_string(hex_result: str) -> str:
    clean = str(hex_result or "").removeprefix("0x")
    if len(clean) < 128:
        return ""
    offset = int(clean[:64], 16) * 2
    if offset + 64 > len(clean):
        return ""
    length = int(clean[offset : offset + 64], 16)
    start = offset + 64
    end = start + length * 2
    data = clean[start:end]
    try:
        return bytes.fromhex(data).decode("utf-8", errors="replace")
    except ValueError:
        return ""


def _hex_to_int(value: Any) -> int:
    if not isinstance(value, str):
        raise ValueError(f"Expected hex string result, got {type(value).__name__}")
    return int(value, 16)


def _code_bytes(value: str) -> int:
    clean = str(value or "").removeprefix("0x")
    if not clean:
        return 0
    return len(clean) // 2


def _clean_address(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _ADDRESS_RE.fullmatch(text):
        return None
    return text.lower()


def _format_compute_credits(amount_base_units: int) -> str:
    sign = "-" if amount_base_units < 0 else ""
    value = abs(int(amount_base_units))
    whole, fractional = divmod(value, COMPUTE_CREDIT_BASE_UNITS)
    if fractional == 0:
        return f"{sign}{whole}"
    fraction = str(fractional).rjust(18, "0").rstrip("0")
    return f"{sign}{whole}.{fraction}"


def _format_eng(amount_wei: int) -> str:
    """Deprecated compatibility alias for pre-C0 callers."""
    return _format_compute_credits(amount_wei)
