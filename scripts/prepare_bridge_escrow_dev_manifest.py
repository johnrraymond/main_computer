#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_STATE_FILE = Path("runtime/deployments/dev/latest.json")
DEFAULT_OUT = Path("runtime/hub/bridge_escrow_dev_manifest.json")
DEFAULT_ENV_OUT = Path("runtime/hub/bridge_escrow_dev.env")

DEFAULT_RPC_URL = "http://127.0.0.1:18545"
DEFAULT_HUB_URL = "http://127.0.0.1:8770"
DEFAULT_CHAIN_ID = 42424242
DEFAULT_CONTRACT_ADDRESS = "0x1111111111111111111111111111111111111111"
DEFAULT_DEPOSIT_CREDITS = 100
DEFAULT_CREDIT_UNIT_SCALE = 1_000_000
DEFAULT_MIN_NATIVE_WEI = 1_000_000_000_000_000

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")


# Standard deterministic Anvil/Foundry dev wallets. These are DEV ONLY and must
# never be used with real funds. The first four are intentionally requesters for
# the multi-wallet escrow smoke.
DEFAULT_DEV_WALLETS = [
    {
        "office": "O0",
        "title": "Requester 0",
        "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "private_key": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    },
    {
        "office": "O1",
        "title": "Requester 1",
        "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "private_key": "0x59c6995e998f97a5a0044966f094538c9e4361d023d65a14d6007a1df0863d9",
    },
    {
        "office": "O2",
        "title": "Requester 2",
        "address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "private_key": "0x5de4111afa1a4b582f56a49c1b5f05b7ec3a943b11f071d72da14ef03ea64d35",
    },
    {
        "office": "O3",
        "title": "Requester 3",
        "address": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
        "private_key": "0x7c8521182947f3db6289eedbc2ba5d66237bca6d0f79f0a2d4c10c86184a8e24",
    },
    {
        "office": "O4",
        "title": "Worker 0",
        "address": "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65",
        "private_key": "0x47e179ec19748826f25cc1a5af897a1c59b64f10c1ee5638b0767f467bdca11f",
    },
    {
        "office": "O5",
        "title": "Bridge Controller",
        "address": "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc",
        "private_key": "0x8b3a350cf5c34c9194ca3a545d1f0b8a7a754e03d6f34e7e65ac8068bddb2ba",
    },
    {
        "office": "O6",
        "title": "Spare",
        "address": "0x976EA74026E726554dB657fA54763abd0C3a0aa9",
        "private_key": "0x92db14eec9e8c55da9ff8d83cf66f3e4b0b6c323ca2f0ce7857f1e46c29306d9",
    },
    {
        "office": "O7",
        "title": "Spare",
        "address": "0x14dC79964da2C08b23698B3D3cc7Ca32193d9955",
        "private_key": "0x4bbbf28a99f03eec7f5efcd9b8f57b887db5e72ab5d3e5b3687dfc6801434c2e",
    },
]


class ManifestFailure(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ManifestFailure(f"State file is not valid JSON: {path}") from exc
    if not isinstance(loaded, dict):
        raise ManifestFailure(f"State file root is not a JSON object: {path}")
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


def bytes32_id(*parts: Any) -> str:
    seed = "|".join(str(part) for part in parts)
    return "0x" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def fake_tx_hash(*parts: Any) -> str:
    return bytes32_id("dev-normalized-escrow-receipt", *parts)


def wei_to_eth_text(wei: int) -> str:
    return format(Decimal(wei) / Decimal(10**18), "f")


def state_chain_config(state: dict[str, Any]) -> tuple[str, int]:
    chain = state.get("chain") if isinstance(state.get("chain"), dict) else {}
    rpc_url = str(chain.get("host_rpc_url") or chain.get("rpc_url") or DEFAULT_RPC_URL)
    try:
        chain_id = int(chain.get("chain_id") or DEFAULT_CHAIN_ID)
    except (TypeError, ValueError):
        chain_id = DEFAULT_CHAIN_ID
    return rpc_url, chain_id


def contract_from_state(state: dict[str, Any]) -> str:
    wanted = {
        "hubcreditbridgeescrow",
        "hub-credit-bridge-escrow",
        "credit-bridge-escrow",
        "bridge-escrow",
        "credit-escrow",
    }

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
            if found and (
                key_text in wanted
                or any(name in key_text for name in wanted)
                or any(name in target for name in wanted)
            ):
                return found

    return ""


def offices_from_state(state: dict[str, Any], *, required_count: int) -> list[dict[str, str]]:
    raw_offices = state.get("offices")
    records: list[dict[str, str]] = []

    if isinstance(raw_offices, list):
        for index, raw in enumerate(raw_offices[:required_count]):
            fallback = DEFAULT_DEV_WALLETS[index] if index < len(DEFAULT_DEV_WALLETS) else {}
            if not isinstance(raw, dict):
                raw = {}

            address = (
                normalize_address(raw.get("address") or raw.get("account"))
                or normalize_address(fallback.get("address"))
            )
            private_key = (
                normalize_private_key(raw.get("private_key") or raw.get("privateKey"))
                or normalize_private_key(fallback.get("private_key"))
            )
            records.append(
                {
                    "office": str(raw.get("office") or fallback.get("office") or f"O{index}"),
                    "title": str(raw.get("title") or fallback.get("title") or f"Office {index}"),
                    "address": address,
                    "private_key": private_key,
                }
            )

    while len(records) < required_count:
        index = len(records)
        if index >= len(DEFAULT_DEV_WALLETS):
            raise ManifestFailure(
                f"Need {required_count} dev wallets, but only {len(DEFAULT_DEV_WALLETS)} defaults are available."
            )
        fallback = DEFAULT_DEV_WALLETS[index]
        records.append(
            {
                "office": fallback["office"],
                "title": fallback["title"],
                "address": normalize_address(fallback["address"]),
                "private_key": normalize_private_key(fallback["private_key"]),
            }
        )

    return records


def http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}

    if body is not None:
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ManifestFailure(
            f"{method} {url} returned HTTP {exc.code}: {detail[:800]}"
        ) from exc
    except URLError as exc:
        raise ManifestFailure(f"{method} {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ManifestFailure(f"{method} {url} timed out after {timeout} seconds") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestFailure(f"{method} {url} did not return JSON: {raw[:800]}") from exc

    if not isinstance(decoded, dict):
        raise ManifestFailure(f"{method} {url} returned non-object JSON: {decoded!r}")

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
        raise ManifestFailure(f"RPC {method} returned error: {decoded['error']}")
    return decoded.get("result")


def check_rpc_balances(
    rpc_url: str,
    actors: list[dict[str, Any]],
    *,
    timeout: float,
    min_native_wei: int,
) -> dict[str, Any]:
    chain_id_hex = rpc_json(rpc_url, "eth_chainId", timeout=timeout)
    try:
        rpc_chain_id = int(str(chain_id_hex), 16)
    except ValueError as exc:
        raise ManifestFailure(f"RPC returned malformed chain id: {chain_id_hex!r}") from exc

    balances: dict[str, Any] = {}

    for actor in actors:
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
            raise ManifestFailure(
                f"RPC returned malformed balance for {actor['id']}: {raw_balance!r}"
            ) from exc

        actor["balance_wei"] = str(balance_wei)
        actor["balance_eth"] = wei_to_eth_text(balance_wei)
        actor["funded_on_chain"] = balance_wei >= min_native_wei

        balances[actor["id"]] = {
            "address": address,
            "balance_wei": str(balance_wei),
            "balance_eth": actor["balance_eth"],
            "funded_on_chain": actor["funded_on_chain"],
            "min_native_wei": str(min_native_wei),
        }

    return {
        "ok": True,
        "rpc_url": rpc_url,
        "chain_id": rpc_chain_id,
        "balances": balances,
    }


def write_env_file(path: Path, manifest_path: Path, manifest: dict[str, Any], *, include_private_keys: bool) -> None:
    requesters = manifest["actors"]["requesters"]
    worker = manifest["actors"]["worker"]
    bridge = manifest["actors"]["bridge_controller"]
    lines = [
        "# Temporary bridge escrow multi-wallet development config.",
        "# Do not use these deterministic dev keys on mainnet or with real funds.",
        f"MAIN_COMPUTER_BRIDGE_ESCROW_MANIFEST={manifest_path.as_posix()}",
        f"MAIN_COMPUTER_HUB_URL={manifest['hub']['url']}",
        f"MAIN_COMPUTER_CREDIT_RPC_URL={manifest['chain']['rpc_url']}",
        f"MAIN_COMPUTER_CREDIT_CHAIN_ID={manifest['chain']['chain_id']}",
        f"MAIN_COMPUTER_CREDIT_CONTRACT_ADDRESS={manifest['chain']['contract_address']}",
        f"MAIN_COMPUTER_BRIDGE_CONTROLLER_WALLET={bridge['address']}",
        f"MAIN_COMPUTER_PAID_WORKER_ID={worker['worker_id']}",
        f"MAIN_COMPUTER_PAID_WORKER_WALLET={worker['address']}",
        f"MAIN_COMPUTER_BRIDGE_ESCROW_DEFAULT_DEPOSIT_CREDITS={manifest['defaults']['deposit_credits']}",
        f"MAIN_COMPUTER_BRIDGE_ESCROW_CREDIT_UNIT_SCALE={manifest['credit_units']['scale']}",
    ]

    for index, requester in enumerate(requesters):
        prefix = f"MAIN_COMPUTER_REQUESTER_{index}"
        lines.extend(
            [
                f"{prefix}_ACCOUNT_ID={requester['account_id']}",
                f"{prefix}_WALLET={requester['address']}",
                f"{prefix}_DEPOSIT_CREDITS={requester['deposit_credits']}",
                f"{prefix}_DEPOSIT_UNITS={requester['deposit_units']}",
            ]
        )

    if include_private_keys:
        for index, requester in enumerate(requesters):
            lines.append(f"MAIN_COMPUTER_REQUESTER_{index}_PRIVATE_KEY={requester.get('private_key', '')}")
        lines.append(f"MAIN_COMPUTER_PAID_WORKER_PRIVATE_KEY={worker.get('private_key', '')}")
        lines.append(f"MAIN_COMPUTER_BRIDGE_CONTROLLER_PRIVATE_KEY={bridge.get('private_key', '')}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def redact_private(actor: dict[str, Any], *, include_private_keys: bool) -> dict[str, Any]:
    clean = dict(actor)
    if not include_private_keys:
        clean.pop("private_key", None)
        clean["private_key_redacted"] = True
    return clean


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a temporary bridge-escrow manifest with the top four dev wallets "
            "as funded requesters plus separate worker and bridge-controller wallets."
        )
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--env-out", type=Path, default=DEFAULT_ENV_OUT)
    parser.add_argument("--no-env", action="store_true")
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--hub-url", default=DEFAULT_HUB_URL)
    parser.add_argument("--chain-id", type=int, default=0)
    parser.add_argument("--contract-address", default="")
    parser.add_argument("--requester-count", type=int, default=4)
    parser.add_argument("--requester-prefix", default="bridge-escrow-requester")
    parser.add_argument("--worker-id", default="paid-mock-worker-01")
    parser.add_argument("--bridge-id", default="bridge-controller")
    parser.add_argument("--deposit-credits", type=int, default=DEFAULT_DEPOSIT_CREDITS)
    parser.add_argument("--credit-unit-scale", type=int, default=DEFAULT_CREDIT_UNIT_SCALE)
    parser.add_argument("--min-native-wei", type=int, default=DEFAULT_MIN_NATIVE_WEI)
    parser.add_argument("--skip-rpc-check", action="store_true")
    parser.add_argument("--strict-rpc", action="store_true")
    parser.add_argument(
        "--include-private-keys",
        action="store_true",
        help="Write deterministic dev private keys into the manifest/env file. DEV ONLY.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.requester_count < 1:
        raise ManifestFailure("requester-count must be at least 1")
    if args.deposit_credits <= 0:
        raise ManifestFailure("deposit-credits must be positive")
    if args.credit_unit_scale <= 0:
        raise ManifestFailure("credit-unit-scale must be positive")

    state = read_json_file(args.state)
    state_rpc_url, state_chain_id = state_chain_config(state)
    rpc_url = args.rpc_url or state_rpc_url
    chain_id = args.chain_id or state_chain_id
    contract_address = (
        normalize_address(args.contract_address)
        or contract_from_state(state)
        or DEFAULT_CONTRACT_ADDRESS
    )
    required_wallet_count = args.requester_count + 2
    offices = offices_from_state(state, required_count=required_wallet_count)

    requesters: list[dict[str, Any]] = []
    deposit_units = int(args.deposit_credits) * int(args.credit_unit_scale)
    for index in range(args.requester_count):
        office = offices[index]
        account_id = clean_id(f"{args.requester_prefix}-{index}", default=f"bridge-escrow-requester-{index}")
        address = normalize_address(office["address"])
        if not address:
            raise ManifestFailure(f"requester {index} does not have a valid address")
        receipt_seed = bytes32_id("bridge-escrow-multi-wallet", chain_id, contract_address, account_id, address)
        requesters.append(
            {
                "id": f"requester_{index}",
                "role": "requester",
                "office": office["office"],
                "title": office["title"],
                "account_id": account_id,
                "address": address,
                "private_key": normalize_private_key(office.get("private_key")),
                "private_key_env": f"MAIN_COMPUTER_REQUESTER_{index}_PRIVATE_KEY",
                "deposit_credits": int(args.deposit_credits),
                "deposit_units": deposit_units,
                "deposit_id": receipt_seed,
                "normalized_receipt_tx_hash": fake_tx_hash(chain_id, contract_address, account_id, address),
                "log_index": index,
            }
        )

    worker_office = offices[args.requester_count]
    bridge_office = offices[args.requester_count + 1]
    worker = {
        "id": "worker",
        "role": "worker",
        "office": worker_office["office"],
        "worker_id": clean_id(args.worker_id, default="paid-mock-worker-01"),
        "address": normalize_address(worker_office["address"]),
        "private_key": normalize_private_key(worker_office.get("private_key")),
        "private_key_env": "MAIN_COMPUTER_PAID_WORKER_PRIVATE_KEY",
    }
    bridge = {
        "id": "bridge_controller",
        "role": "bridge_controller",
        "office": bridge_office["office"],
        "bridge_id": clean_id(args.bridge_id, default="bridge-controller"),
        "address": normalize_address(bridge_office["address"]),
        "private_key": normalize_private_key(bridge_office.get("private_key")),
        "private_key_env": "MAIN_COMPUTER_BRIDGE_CONTROLLER_PRIVATE_KEY",
    }
    if not worker["address"]:
        raise ManifestFailure("worker wallet does not have a valid address")
    if not bridge["address"]:
        raise ManifestFailure("bridge-controller wallet does not have a valid address")

    actors_for_rpc = [*requesters, worker, bridge]
    smoke: dict[str, Any] = {
        "ok": True,
        "rpc_checked": False,
        "warnings": [],
    }

    if normalize_address(contract_address) == normalize_address(DEFAULT_CONTRACT_ADDRESS):
        smoke["warnings"].append(
            "contract_address is the placeholder value; pass --contract-address or deploy/update runtime/deployments/dev/latest.json before chain-backed deposits."
        )

    if not args.skip_rpc_check:
        try:
            rpc = check_rpc_balances(
                rpc_url,
                actors_for_rpc,
                timeout=10.0,
                min_native_wei=args.min_native_wei,
            )
            smoke["rpc"] = rpc
            smoke["rpc_checked"] = True
            rpc_chain_id = int(rpc["chain_id"])
            if rpc_chain_id != chain_id:
                smoke["warnings"].append(
                    f"Configured chain_id={chain_id}, but RPC reports chain_id={rpc_chain_id}; using RPC value."
                )
                chain_id = rpc_chain_id
            missing = [
                actor["id"]
                for actor in actors_for_rpc
                if actor.get("funded_on_chain") is not True
            ]
            if missing:
                message = "Dev wallets below min native balance: " + ", ".join(missing)
                smoke["warnings"].append(message)
                if args.strict_rpc:
                    raise ManifestFailure(message)
        except ManifestFailure as exc:
            smoke["rpc"] = {"ok": False, "error": str(exc), "rpc_url": rpc_url}
            smoke["warnings"].append(f"RPC balance check failed: {exc}")
            if args.strict_rpc:
                raise
    else:
        for actor in actors_for_rpc:
            actor["balance_wei"] = ""
            actor["balance_eth"] = ""
            actor["funded_on_chain"] = None

    manifest = {
        "schema_version": "bridge-escrow-dev-manifest-v0",
        "temporary": True,
        "created_at": utc_now(),
        "warning": (
            "DEV/TEST ONLY. This manifest uses deterministic local-chain wallets. "
            "Do not use these keys or addresses with real funds."
        ),
        "purpose": (
            "Multi-wallet bridge escrow funding prep for paid hub worker smokes. "
            "Top four wallets are requesters; worker and bridge controller are separate actors."
        ),
        "hub": {
            "url": args.hub_url.rstrip("/"),
            "deposit_import_endpoint": "/api/hub/v1/credits/deposits/import",
            "deposits_endpoint": "/api/hub/v1/credits/deposits",
            "balance_endpoint": "/api/hub/v1/credits/balance",
        },
        "chain": {
            "rpc_url": rpc_url,
            "chain_id": chain_id,
            "contract_address": normalize_address(contract_address) or contract_address,
            "contract_name": "HubCreditBridgeEscrow",
            "state_file": str(args.state),
        },
        "credit_units": {
            "name": "compute_credit",
            "scale": int(args.credit_unit_scale),
            "notes": [
                "Hub ledger currently imports whole credits via credits_granted.",
                "The escrow contract tracks integer amountUnits so fractional credits can later be represented as credit atoms.",
            ],
        },
        "defaults": {
            "requester_count": args.requester_count,
            "deposit_credits": int(args.deposit_credits),
            "deposit_units": deposit_units,
            "min_native_wei": str(args.min_native_wei),
        },
        "actors": {
            "requesters": [
                redact_private(actor, include_private_keys=args.include_private_keys)
                for actor in requesters
            ],
            "worker": redact_private(worker, include_private_keys=args.include_private_keys),
            "bridge_controller": redact_private(bridge, include_private_keys=args.include_private_keys),
        },
        "mock_ai": {
            "provider": "mock",
            "worker_id": worker["worker_id"],
            "models": ["mock-fast-chat"],
            "response_template": "mock worker response for request {request_id}",
        },
        "smoke": smoke,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

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
        "requester_count": len(requesters),
        "deposit_credits_each": int(args.deposit_credits),
        "deposit_units_each": deposit_units,
        "total_deposit_credits": int(args.deposit_credits) * len(requesters),
        "contract_address": normalize_address(contract_address) or contract_address,
        "rpc_checked": smoke["rpc_checked"],
        "warnings": smoke["warnings"],
        "requesters": [
            {
                "account_id": actor["account_id"],
                "address": actor["address"],
                "funded_on_chain": actor.get("funded_on_chain"),
            }
            for actor in requesters
        ],
        "worker": {
            "worker_id": worker["worker_id"],
            "address": worker["address"],
        },
        "bridge_controller": {
            "bridge_id": bridge["bridge_id"],
            "address": bridge["address"],
        },
    }

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("Bridge escrow multi-wallet dev manifest prepared.")
        print(f"  manifest: {summary['manifest']}")
        if summary["env_file"]:
            print(f"  env file: {summary['env_file']}")
        print(f"  contract: {summary['contract_address']}")
        print(f"  requesters: {summary['requester_count']}")
        print(f"  deposit each: {summary['deposit_credits_each']} credits / {summary['deposit_units_each']} units")
        for requester in summary["requesters"]:
            print(
                "  requester: "
                f"{requester['account_id']} / {requester['address']} / "
                f"funded_on_chain={requester['funded_on_chain']}"
            )
        print(f"  worker: {summary['worker']['worker_id']} / {summary['worker']['address']}")
        print(
            "  bridge controller: "
            f"{summary['bridge_controller']['bridge_id']} / {summary['bridge_controller']['address']}"
        )
        print(f"  rpc checked: {summary['rpc_checked']}")
        for warning in summary["warnings"]:
            print(f"  warning: {warning}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ManifestFailure as exc:
        print(f"prepare bridge escrow manifest failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
