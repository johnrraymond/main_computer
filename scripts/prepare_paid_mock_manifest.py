#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_STATE_FILE = Path("runtime/deployments/current.json")
DEFAULT_OUT = Path("runtime/hub/paid_mock_dev_manifest.json")
DEFAULT_ENV_OUT = Path("runtime/hub/paid_mock_dev.env")

DEFAULT_RPC_URL = "http://127.0.0.1:18545"
DEFAULT_HUB_URL = "http://127.0.0.1:8770"
DEFAULT_CHAIN_ID = 42424242
DEFAULT_CONTRACT_ADDRESS = "0x1111111111111111111111111111111111111111"
DEFAULT_PAYMENT_AMOUNT_BASE_UNITS = 1_000_000_000_000_000_000
DEFAULT_CREDITS_GRANTED = 1_000

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


DEFAULT_DEV_WALLETS = [
    {
        "office": "O0",
        "title": "Requester",
        "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "private_key": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    },
    {
        "office": "O1",
        "title": "Worker",
        "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "private_key": "0x59c6995e998f97a5a0044966f094538c9e4361d023d65a14d6007a1df0863d9",
    },
    {
        "office": "O2",
        "title": "Bridge",
        "address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "private_key": "0x5de4111afa1a4b582f56a49c1b5f05b7ec3a943b11f071d72da14ef03ea64d35",
    },
]


class SmokeFailure(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"State file is not valid JSON: {path}") from exc
    if not isinstance(loaded, dict):
        raise SmokeFailure(f"State file root is not a JSON object: {path}")
    return loaded


def normalize_address(value: Any) -> str:
    text = str(value or "").strip()
    if not ADDRESS_RE.fullmatch(text):
        return ""
    return "0x" + text[2:].lower()


def normalize_private_key(value: Any) -> str:
    text = str(value or "").strip()
    if not PRIVATE_KEY_RE.fullmatch(text):
        return ""
    return "0x" + text[2:].lower()


def clean_id(value: str, *, default: str) -> str:
    text = "".join(
        ch if ch.isalnum() or ch in {"-", "_", "."} else "-"
        for ch in str(value or "").strip().lower()
    )
    return text or default


def contract_from_state(state: dict[str, Any]) -> str:
    escrow_names = {
        "hubcreditbridgeescrow",
        "hub-credit-bridge-escrow",
        "credit-bridge-escrow",
        "bridge-escrow",
        "credit-escrow",
    }
    legacy_sale_names = {
        "hubcreditsale",
        "hub-credit-sale",
        "credit-sale",
        "creditsale",
    }

    candidates: list[tuple[bool, str]] = []

    for container_name in ("contracts", "deployments"):
        container = state.get(container_name)
        if not isinstance(container, dict):
            continue

        for key, raw in container.items():
            key_text = str(key or "").strip().lower()
            address: Any = raw
            target = ""

            if isinstance(raw, dict):
                address = raw.get("address")
                target = str(
                    raw.get("target")
                    or raw.get("contract")
                    or raw.get("name")
                    or ""
                ).strip().lower()

            found = normalize_address(address)
            if not found:
                continue

            is_escrow = (
                key_text in escrow_names
                or any(name in key_text for name in escrow_names)
                or any(name in target for name in escrow_names)
            )
            is_legacy_sale = (
                key_text in legacy_sale_names
                or any(name in key_text for name in legacy_sale_names)
                or any(name in target for name in legacy_sale_names)
            )

            if is_escrow or is_legacy_sale:
                candidates.append((is_escrow, found))

    for is_escrow, address in candidates:
        if is_escrow:
            return address

    return candidates[0][1] if candidates else ""



def offices_from_state(state: dict[str, Any]) -> list[dict[str, str]]:
    raw_offices = state.get("offices")
    if not isinstance(raw_offices, list) or not raw_offices:
        return [dict(item) for item in DEFAULT_DEV_WALLETS]

    records: list[dict[str, str]] = []

    for index, raw in enumerate(raw_offices[:3]):
        fallback = DEFAULT_DEV_WALLETS[index]
        if not isinstance(raw, dict):
            raw = {}

        address = (
            normalize_address(raw.get("address") or raw.get("account"))
            or normalize_address(fallback["address"])
        )
        private_key = (
            normalize_private_key(raw.get("private_key") or raw.get("privateKey"))
            or normalize_private_key(fallback["private_key"])
        )

        records.append(
            {
                "office": str(raw.get("office") or fallback["office"]),
                "title": str(raw.get("title") or fallback["title"]),
                "address": address,
                "private_key": private_key,
            }
        )

    while len(records) < 3:
        records.append(dict(DEFAULT_DEV_WALLETS[len(records)]))

    return records


def state_chain_config(state: dict[str, Any]) -> tuple[str, int]:
    chain = state.get("chain") if isinstance(state.get("chain"), dict) else {}

    rpc_url = str(chain.get("host_rpc_url") or chain.get("rpc_url") or DEFAULT_RPC_URL)

    try:
        chain_id = int(chain.get("chain_id") or DEFAULT_CHAIN_ID)
    except (TypeError, ValueError):
        chain_id = DEFAULT_CHAIN_ID

    return rpc_url, chain_id


def http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}

    if body is not None:
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        headers["Content-Type"] = "application/json"

    if extra_headers:
        headers.update(extra_headers)

    request = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(
            f"{method} {url} returned HTTP {exc.code}: {detail[:800]}"
        ) from exc
    except URLError as exc:
        raise SmokeFailure(f"{method} {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SmokeFailure(f"{method} {url} timed out after {timeout} seconds") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{method} {url} did not return JSON: {raw[:800]}") from exc

    if not isinstance(decoded, dict):
        raise SmokeFailure(f"{method} {url} returned non-object JSON: {decoded!r}")

    return decoded


def rpc_json(
    rpc_url: str,
    method: str,
    params: list[Any] | None = None,
    *,
    timeout: float = 5.0,
) -> Any:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }

    decoded = http_json("POST", rpc_url, body=payload, timeout=timeout)

    if "error" in decoded:
        raise SmokeFailure(f"RPC {method} returned error: {decoded['error']}")

    return decoded.get("result")


def wei_to_eth_text(wei: int) -> str:
    return format(Decimal(wei) / Decimal(10**18), "f")


def check_rpc_balances(
    rpc_url: str,
    actors: dict[str, dict[str, Any]],
    *,
    timeout: float,
) -> dict[str, Any]:
    chain_id_hex = rpc_json(rpc_url, "eth_chainId", timeout=timeout)

    try:
        rpc_chain_id = int(str(chain_id_hex), 16)
    except ValueError as exc:
        raise SmokeFailure(f"RPC returned malformed chain id: {chain_id_hex!r}") from exc

    balances: dict[str, Any] = {}

    for role, actor in actors.items():
        address = actor["address"]
        raw_balance = rpc_json(
            rpc_url,
            "eth_getBalance",
            [address, "latest"],
            timeout=timeout,
        )

        try:
            balance_wei = int(str(raw_balance), 16)
        except ValueError as exc:
            raise SmokeFailure(
                f"RPC returned malformed balance for {role}: {raw_balance!r}"
            ) from exc

        actor["balance_wei"] = str(balance_wei)
        actor["balance_eth"] = wei_to_eth_text(balance_wei)
        actor["funded_on_chain"] = balance_wei > 0

        balances[role] = {
            "address": address,
            "balance_wei": str(balance_wei),
            "balance_eth": actor["balance_eth"],
            "funded_on_chain": actor["funded_on_chain"],
        }

    return {
        "ok": True,
        "rpc_url": rpc_url,
        "chain_id": rpc_chain_id,
        "balances": balances,
    }


def receipt_tx_hash(seed: str, *, unique: bool) -> str:
    if unique:
        seed = f"{seed}:{uuid.uuid4().hex}"
    return "0x" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def build_funding_payload(
    args: argparse.Namespace,
    actors: dict[str, dict[str, Any]],
    chain_id: int,
    contract_address: str,
) -> dict[str, Any]:
    requester = actors["requester"]
    bridge = actors["bridge"]
    account_id = requester["account_id"]

    seed = "|".join(
        [
            "paid-mock-escrow-deposit",
            str(chain_id),
            normalize_address(contract_address),
            normalize_address(requester["address"]),
            normalize_address(bridge["address"]),
            account_id,
            str(args.credits_granted),
            str(args.receipt_label),
        ]
    )

    tx_hash = args.tx_hash or receipt_tx_hash(seed, unique=args.unique_receipt)

    return {
        "chain_id": chain_id,
        "contract_address": normalize_address(contract_address) or contract_address,
        "tx_hash": tx_hash,
        "log_index": args.log_index,
        "block_number": args.block_number,
        "account_id": account_id,
        "payer_address": requester["address"],
        "payment_asset": args.payment_asset,
        "payment_amount_base_units": args.payment_amount_base_units,
        "credits_granted": args.credits_granted,
        "memo": f"paid mock worker dev funding via bridge {bridge['address']}",
    }


def hub_headers(token: str) -> dict[str, str]:
    token = str(token or "").strip()
    if not token:
        return {}

    return {
        "Authorization": f"Bearer {token}",
        "X-Main-Computer-Hub-Admin-Token": token,
    }


def import_credits_to_hub(
    args: argparse.Namespace,
    payload: dict[str, Any],
) -> dict[str, Any]:
    hub_url = args.hub_url.rstrip("/")
    headers = hub_headers(args.hub_token)

    indexer = http_json(
        "GET",
        f"{hub_url}/api/hub/v1/credits/indexer",
        timeout=args.timeout,
        extra_headers=headers,
    )

    if indexer.get("ok") is not True:
        raise SmokeFailure(f"Hub credit indexer did not return ok=true: {indexer}")

    first = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/credits/deposits/import",
        body=payload,
        timeout=args.timeout,
        extra_headers=headers,
    )

    if first.get("ok") is not True:
        raise SmokeFailure(f"First credit import did not return ok=true: {first}")

    second = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/credits/deposits/import",
        body=payload,
        timeout=args.timeout,
        extra_headers=headers,
    )

    if second.get("ok") is not True or second.get("idempotent") is not True:
        raise SmokeFailure(f"Duplicate credit import was not idempotent: {second}")

    account_id = payload["account_id"]
    query = urlencode({"account_id": account_id})

    balance = http_json(
        "GET",
        f"{hub_url}/api/hub/v1/credits/balance?{query}",
        timeout=args.timeout,
        extra_headers=headers,
    )

    account = balance.get("account") if isinstance(balance.get("account"), dict) else {}
    available = int(account.get("available_credits", 0) or 0)

    deposits = http_json(
        "GET",
        f"{hub_url}/api/hub/v1/credits/deposits?{query}",
        timeout=args.timeout,
        extra_headers=headers,
    )

    transactions = http_json(
        "GET",
        f"{hub_url}/api/hub/v1/credits/transactions?{query}",
        timeout=args.timeout,
        extra_headers=headers,
    )

    return {
        "ok": True,
        "hub_url": hub_url,
        "indexer": indexer,
        "first_import_idempotent": bool(first.get("idempotent")),
        "duplicate_import_idempotent": True,
        "account_id": account_id,
        "deposit_id": str(
            (second.get("deposit") or first.get("deposit") or {}).get("deposit_id", "")
        ),
        "event_uid": str(second.get("event_uid") or first.get("event_uid") or ""),
        "available_credits": available,
        "deposit_count": int(deposits.get("deposit_count", deposits.get("purchase_count", 0)) or 0),
        "transaction_count": int(transactions.get("transaction_count", 0) or 0),
    }


def redact_actor(
    actor: dict[str, Any],
    *,
    redact_private_keys: bool,
) -> dict[str, Any]:
    clean = dict(actor)

    if redact_private_keys:
        clean.pop("private_key", None)
        clean["private_key_redacted"] = True
        clean["private_key_env"] = clean.get("private_key_env", "")

    return clean


def write_env_file(
    path: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    *,
    include_private_keys: bool,
) -> None:
    actors = manifest["actors"]

    lines = [
        "# Temporary paid mock worker development config.",
        "# Do not use these dev keys on mainnet or with real funds.",
        f"MAIN_COMPUTER_PAID_MOCK_MANIFEST={manifest_path.as_posix()}",
        "MAIN_COMPUTER_WORKER_PROVIDER=mock",
        f"MAIN_COMPUTER_HUB_URL={manifest['hub']['url']}",
        f"MAIN_COMPUTER_CREDIT_RPC_URL={manifest['chain']['rpc_url']}",
        f"MAIN_COMPUTER_CREDIT_CHAIN_ID={manifest['chain']['chain_id']}",
        f"MAIN_COMPUTER_CREDIT_CONTRACT_ADDRESS={manifest['chain']['contract_address']}",
        f"MAIN_COMPUTER_PAID_REQUESTER_ACCOUNT_ID={actors['requester']['account_id']}",
        f"MAIN_COMPUTER_PAID_REQUESTER_WALLET={actors['requester']['address']}",
        f"MAIN_COMPUTER_PAID_WORKER_ID={actors['worker']['worker_id']}",
        f"MAIN_COMPUTER_PAID_WORKER_WALLET={actors['worker']['address']}",
        f"MAIN_COMPUTER_PAID_BRIDGE_ID={actors['bridge']['bridge_id']}",
        f"MAIN_COMPUTER_PAID_BRIDGE_WALLET={actors['bridge']['address']}",
        "MAIN_COMPUTER_PAID_MOCK_MAX_CREDITS=20",
    ]

    if include_private_keys:
        lines.extend(
            [
                f"MAIN_COMPUTER_PAID_REQUESTER_PRIVATE_KEY={actors['requester'].get('private_key', '')}",
                f"MAIN_COMPUTER_PAID_WORKER_PRIVATE_KEY={actors['worker'].get('private_key', '')}",
                f"MAIN_COMPUTER_PAID_BRIDGE_PRIVATE_KEY={actors['bridge'].get('private_key', '')}",
            ]
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a temporary paid mock-worker dev manifest with requester, "
            "worker, and bridge wallets. Optionally verifies dev-chain balances "
            "and imports requester Compute Credits through a bridge escrow deposit receipt."
        )
    )

    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--env-out", type=Path, default=DEFAULT_ENV_OUT)
    parser.add_argument("--no-env", action="store_true")

    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--chain-id", type=int, default=0)
    parser.add_argument("--contract-address", default="")

    parser.add_argument("--hub-url", default=DEFAULT_HUB_URL)
    parser.add_argument("--hub-token", default="")
    parser.add_argument("--timeout", type=float, default=10.0)

    parser.add_argument("--requester-account-id", default="paid-mock-requester")
    parser.add_argument("--worker-id", default="paid-mock-worker-01")
    parser.add_argument("--bridge-id", default="paid-mock-bridge")

    parser.add_argument("--credits-granted", type=int, default=DEFAULT_CREDITS_GRANTED)
    parser.add_argument("--payment-asset", default="native")
    parser.add_argument(
        "--payment-amount-base-units",
        type=int,
        default=DEFAULT_PAYMENT_AMOUNT_BASE_UNITS,
    )
    parser.add_argument("--block-number", type=int, default=123)
    parser.add_argument("--log-index", type=int, default=0)
    parser.add_argument("--receipt-label", default="paid-mock-dev-manifest-v0")
    parser.add_argument("--tx-hash", default="")
    parser.add_argument("--unique-receipt", action="store_true")

    parser.add_argument("--skip-rpc-check", action="store_true")
    parser.add_argument("--strict-rpc", action="store_true")
    parser.add_argument("--import-credits", action="store_true")
    parser.add_argument("--strict-hub", action="store_true")
    parser.add_argument(
        "--include-private-keys",
        action="store_true",
        help="Also write deterministic dev private keys into the manifest/env file. DEV ONLY.",
    )
    parser.add_argument("--json", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    state = read_json_file(args.state)
    state_rpc_url, state_chain_id = state_chain_config(state)

    rpc_url = args.rpc_url or state_rpc_url
    chain_id = args.chain_id or state_chain_id
    contract_address = (
        normalize_address(args.contract_address)
        or contract_from_state(state)
        or DEFAULT_CONTRACT_ADDRESS
    )

    offices = offices_from_state(state)
    requester_wallet, worker_wallet, bridge_wallet = offices[:3]

    actors: dict[str, dict[str, Any]] = {
        "requester": {
            "role": "requester",
            "office": requester_wallet["office"],
            "account_id": clean_id(
                args.requester_account_id,
                default="paid-mock-requester",
            ),
            "address": normalize_address(requester_wallet["address"]),
            "private_key": normalize_private_key(requester_wallet.get("private_key")),
            "private_key_env": "MAIN_COMPUTER_PAID_REQUESTER_PRIVATE_KEY",
        },
        "worker": {
            "role": "worker",
            "office": worker_wallet["office"],
            "worker_id": clean_id(args.worker_id, default="paid-mock-worker-01"),
            "address": normalize_address(worker_wallet["address"]),
            "private_key": normalize_private_key(worker_wallet.get("private_key")),
            "private_key_env": "MAIN_COMPUTER_PAID_WORKER_PRIVATE_KEY",
        },
        "bridge": {
            "role": "bridge",
            "office": bridge_wallet["office"],
            "bridge_id": clean_id(args.bridge_id, default="paid-mock-bridge"),
            "address": normalize_address(bridge_wallet["address"]),
            "private_key": normalize_private_key(bridge_wallet.get("private_key")),
            "private_key_env": "MAIN_COMPUTER_PAID_BRIDGE_PRIVATE_KEY",
        },
    }

    private_key_warnings: list[str] = []

    for role, actor in actors.items():
        if not actor["address"]:
            raise SmokeFailure(f"{role} wallet does not have a valid address")

        if args.include_private_keys and not actor.get("private_key"):
            private_key_warnings.append(
                f"{role} wallet does not have a valid private key; private key will be blank"
            )

    smoke: dict[str, Any] = {
        "ok": True,
        "rpc_checked": False,
        "hub_credit_import_checked": False,
        "warnings": private_key_warnings,
    }

    if not args.skip_rpc_check:
        try:
            smoke["rpc"] = check_rpc_balances(rpc_url, actors, timeout=args.timeout)
            smoke["rpc_checked"] = True

            rpc_chain_id = int(smoke["rpc"]["chain_id"])
            if rpc_chain_id != chain_id:
                smoke["warnings"].append(
                    f"Configured chain_id={chain_id}, but RPC reports chain_id={rpc_chain_id}"
                )
                chain_id = rpc_chain_id

            if any(not actor.get("funded_on_chain") for actor in actors.values()):
                message = "One or more dev wallets has zero chain balance"
                smoke["warnings"].append(message)

                if args.strict_rpc:
                    raise SmokeFailure(message)

        except SmokeFailure as exc:
            smoke["rpc"] = {
                "ok": False,
                "error": str(exc),
                "rpc_url": rpc_url,
            }
            smoke["warnings"].append(f"RPC balance check failed: {exc}")

            if args.strict_rpc:
                raise
    else:
        for actor in actors.values():
            actor["balance_wei"] = ""
            actor["balance_eth"] = ""
            actor["funded_on_chain"] = None

    funding_payload = build_funding_payload(
        args,
        actors,
        chain_id,
        contract_address,
    )

    credit_import_result: dict[str, Any] = {
        "ok": False,
        "imported": False,
        "reason": (
            "not requested; pass --import-credits to fund requester Compute Credits "
            "through a bridge escrow deposit receipt"
        ),
    }

    if args.import_credits:
        try:
            credit_import_result = import_credits_to_hub(args, funding_payload)
            credit_import_result["imported"] = True
            smoke["hub_credit_import_checked"] = True
        except SmokeFailure as exc:
            credit_import_result = {
                "ok": False,
                "imported": False,
                "error": str(exc),
            }
            smoke["warnings"].append(f"Hub credit import failed: {exc}")

            if args.strict_hub:
                raise

    manifest_actors = {
        role: redact_actor(
            actor,
            redact_private_keys=not args.include_private_keys,
        )
        for role, actor in actors.items()
    }

    manifest = {
        "schema_version": "paid-mock-worker-dev-manifest-v0",
        "temporary": True,
        "created_at": utc_now(),
        "warning": (
            "DEV/TEST ONLY. These are deterministic local-chain wallets and must "
            "never be used with real funds."
        ),
        "purpose": (
            "Temporary requester/worker/bridge wallet manifest for building the "
            "paid remote hub golden path with mocked AI worker execution."
        ),
        "hub": {
            "url": args.hub_url.rstrip("/"),
            "escrow_deposit_import_endpoint": "/api/hub/v1/credits/deposits/import",
        },
        "chain": {
            "rpc_url": rpc_url,
            "chain_id": chain_id,
            "contract_address": normalize_address(contract_address) or contract_address,
            "state_file": str(args.state),
        },
        "actors": manifest_actors,
        "compute_credit_funding": {
            "requester_account_id": actors["requester"]["account_id"],
            "credits_granted": args.credits_granted,
            "receipt": funding_payload,
            "hub_import": credit_import_result,
        },
        "mock_ai": {
            "provider": "mock",
            "worker_id": actors["worker"]["worker_id"],
            "models": ["mock-fast-chat"],
            "response_template": "mock worker response for request {request_id}",
            "notes": [
                "Use this provider while building paid holds, request leasing, charge finalization, and worker earnings.",
                "Real model calls should stay behind a provider switch such as MAIN_COMPUTER_WORKER_PROVIDER=ollama.",
            ],
        },
        "recommended_env": {
            "MAIN_COMPUTER_PAID_MOCK_MANIFEST": str(args.out),
            "MAIN_COMPUTER_WORKER_PROVIDER": "mock",
            "MAIN_COMPUTER_HUB_URL": args.hub_url.rstrip("/"),
            "MAIN_COMPUTER_PAID_REQUESTER_ACCOUNT_ID": actors["requester"]["account_id"],
            "MAIN_COMPUTER_PAID_WORKER_ID": actors["worker"]["worker_id"],
            "MAIN_COMPUTER_PAID_BRIDGE_ID": actors["bridge"]["bridge_id"],
        },
        "smoke": smoke,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if not args.no_env:
        write_env_file(
            args.env_out,
            args.out,
            manifest,
            include_private_keys=args.include_private_keys,
        )

    summary = {
        "ok": True,
        "manifest": str(args.out),
        "env_file": "" if args.no_env else str(args.env_out),
        "requester_account_id": actors["requester"]["account_id"],
        "requester_wallet": actors["requester"]["address"],
        "worker_id": actors["worker"]["worker_id"],
        "worker_wallet": actors["worker"]["address"],
        "bridge_wallet": actors["bridge"]["address"],
        "rpc_checked": smoke["rpc_checked"],
        "hub_credit_import_checked": smoke["hub_credit_import_checked"],
        "credits_imported": bool(credit_import_result.get("imported")),
        "available_credits": credit_import_result.get("available_credits"),
        "warnings": smoke["warnings"],
    }

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("Paid mock-worker dev manifest prepared.")
        print(f"  manifest: {summary['manifest']}")

        if summary["env_file"]:
            print(f"  env file: {summary['env_file']}")

        print(
            f"  requester: {summary['requester_account_id']} / "
            f"{summary['requester_wallet']}"
        )
        print(f"  worker:    {summary['worker_id']} / {summary['worker_wallet']}")
        print(f"  bridge:    {summary['bridge_wallet']}")
        print(f"  rpc checked: {summary['rpc_checked']}")
        print(f"  credits imported: {summary['credits_imported']}")

        if summary["available_credits"] is not None:
            print(f"  requester available credits: {summary['available_credits']}")

        for warning in summary["warnings"]:
            print(f"  warning: {warning}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"prepare paid mock manifest failed: {exc}", file=sys.stderr)
        raise SystemExit(1)