#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.hub_networks import HubNetworkConfigError, HubNetworkProfile, load_hub_network_registry, profile_with_deployment_manifest_defaults


PHASE = "hub-network-client-smoke-v1"
DEFAULT_CREDIT_FUNDING = 25_000_000
DEFAULT_WORKER_PRICE = 5_500_123
PUSH0_CANARY_INITCODE = "0x5f60005260206000f3"


class SmokeFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def clean_scope(value: str | None = None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raw = time.strftime("run-%Y%m%d-%H%M%S")
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw) or "hub-network-smoke"


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def http_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
    allow_error: bool = False,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json", "User-Agent": "main-computer-hub-network-client-smoke/1"}
    if body is not None:
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if not allow_error:
            raise SmokeFailure(f"{method} {url} returned HTTP {exc.code}: {raw[:1000]}") from exc
        status = int(exc.code)
    except URLError as exc:
        raise SmokeFailure(f"{method} {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SmokeFailure(f"{method} {url} timed out after {timeout} seconds") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"{method} {url} did not return JSON: {raw[:1000]}") from exc
    if not isinstance(decoded, dict):
        raise SmokeFailure(f"{method} {url} returned non-object JSON: {decoded!r}")
    decoded["_http_status"] = status
    if decoded.get("error") and not allow_error:
        raise SmokeFailure(f"{method} {url} returned error: {decoded['error']}")
    return decoded


def rpc_json(url: str, method: str, params: list[Any] | None = None, *, timeout: float = 10.0) -> Any:
    request = Request(
        url,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SmokeFailure(f"RPC {method} returned HTTP {exc.code}: {body[:1000]}") from exc
    except URLError as exc:
        raise SmokeFailure(f"RPC {method} failed against {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SmokeFailure(f"RPC {method} timed out after {timeout} seconds against {url}") from exc

    if not isinstance(payload, dict):
        raise SmokeFailure(f"RPC {method} returned non-object JSON: {payload!r}")
    if payload.get("error"):
        raise SmokeFailure(f"RPC {method} returned error: {payload['error']}")
    return payload.get("result")


def repo_relative_path(path_text: str, *, repo_root: Path = REPO_ROOT) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return repo_root / path


def default_manifest_path(network: str, *, repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / "runtime" / "deployments" / network / "latest.json"


def load_deployment_manifest(network: str, explicit_path: str | None = None, *, repo_root: Path = REPO_ROOT) -> tuple[Path, dict[str, Any]]:
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(repo_relative_path(explicit_path, repo_root=repo_root))
    else:
        candidates.append(default_manifest_path(network, repo_root=repo_root))
        candidates.append(repo_root / "runtime" / "deployments" / "current.json")

    errors: list[str] = []
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except FileNotFoundError:
            errors.append(f"missing {candidate}")
            continue
        except json.JSONDecodeError as exc:
            raise SmokeFailure(f"deployment manifest is not valid JSON: {candidate}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SmokeFailure(f"deployment manifest root is not an object: {candidate}")
        if str(payload.get("environment") or "") != network and not explicit_path:
            errors.append(f"{candidate} is environment={payload.get('environment')!r}, not {network!r}")
            continue
        return candidate, payload
    raise SmokeFailure("could not find a usable deployment manifest: " + "; ".join(errors))


def validate_manifest(profile: HubNetworkProfile, manifest: dict[str, Any]) -> dict[str, Any]:
    require(str(manifest.get("schema")) == "main-computer.deployment.v1", "deployment manifest has unexpected schema")
    require(str(manifest.get("environment")) == profile.network_key, "deployment manifest environment does not match selected network")
    chain = manifest.get("chain") if isinstance(manifest.get("chain"), dict) else {}
    require(int(chain.get("chain_id", -1)) == int(profile.chain_id or -1), "deployment manifest chain_id does not match selected network")
    require(str(chain.get("rpc_url") or chain.get("host_rpc_url")) == str(profile.chain_rpc_url), "deployment manifest RPC URL does not match selected network")
    contracts = manifest.get("contracts") if isinstance(manifest.get("contracts"), dict) else {}
    require(bool(contracts), "deployment manifest has no contract records")
    smoke_client = manifest.get("smoke_client") if isinstance(manifest.get("smoke_client"), dict) else {}
    require(bool(smoke_client.get("address")), "deployment manifest is missing smoke_client.address")
    require(bool(smoke_client.get("wallet_path")), "deployment manifest is missing smoke_client.wallet_path")
    return {"chain": chain, "contracts": contracts, "smoke_client": smoke_client}


def load_smoke_client_wallet(smoke_client: dict[str, Any], *, expected_chain_id: int, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    wallet_path = repo_relative_path(str(smoke_client.get("wallet_path") or ""), repo_root=repo_root)
    try:
        wallet = json.loads(wallet_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SmokeFailure(f"smoke client wallet file is missing: {wallet_path}") from exc
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"smoke client wallet file is not valid JSON: {wallet_path}: {exc}") from exc
    if not isinstance(wallet, dict):
        raise SmokeFailure(f"smoke client wallet root is not an object: {wallet_path}")
    require(wallet.get("schema") == "main-computer.smoke-client-wallet.v1", "smoke client wallet schema mismatch")
    require(int(wallet.get("chain_id", -1)) == int(expected_chain_id), "smoke client wallet chain_id mismatch")
    require(str(wallet.get("address", "")).lower() == str(smoke_client.get("address", "")).lower(), "smoke client wallet address does not match manifest")
    require(str(wallet.get("private_key", "")).startswith("0x") and len(str(wallet.get("private_key", ""))) == 66, "smoke client wallet private_key is missing or invalid")
    wallet["_path"] = str(wallet_path)
    return wallet


def verify_chain(profile: HubNetworkProfile, *, timeout: float) -> dict[str, Any]:
    require(profile.chain_rpc_url is not None, f"network {profile.network_key!r} has no chain_rpc_url")
    require(profile.chain_id is not None, f"network {profile.network_key!r} has no chain_id")
    chain_id_hex = rpc_json(profile.chain_rpc_url, "eth_chainId", timeout=timeout)
    actual_chain_id = int(str(chain_id_hex), 16)
    require(actual_chain_id == profile.chain_id, f"chain RPC returned chain_id {actual_chain_id}, expected {profile.chain_id}")
    block_number_hex = rpc_json(profile.chain_rpc_url, "eth_blockNumber", timeout=timeout)
    latest_block = rpc_json(profile.chain_rpc_url, "eth_getBlockByNumber", ["latest", False], timeout=timeout)
    require(isinstance(latest_block, dict), "latest block RPC did not return an object")
    base_fee = latest_block.get("baseFeePerGas")
    require(isinstance(base_fee, str) and base_fee.startswith("0x") and int(base_fee, 16) > 0, "latest block does not expose a real EIP-1559 baseFeePerGas")
    push0_estimate = rpc_json(
        profile.chain_rpc_url,
        "eth_estimateGas",
        [{"from": "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266", "data": PUSH0_CANARY_INITCODE}],
        timeout=timeout,
    )
    return {
        "chain_id": actual_chain_id,
        "chain_id_hex": chain_id_hex,
        "block_number": int(str(block_number_hex), 16),
        "base_fee_per_gas": int(base_fee, 16),
        "push0_estimate_gas": int(str(push0_estimate), 16),
    }


def verify_contract_code(profile: HubNetworkProfile, contracts: dict[str, Any], *, timeout: float) -> dict[str, int]:
    code_bytes: dict[str, int] = {}
    for key, record in contracts.items():
        if not isinstance(record, dict):
            continue
        address = str(record.get("address") or "").strip()
        if not address:
            continue
        code = rpc_json(profile.chain_rpc_url or "", "eth_getCode", [address, "latest"], timeout=timeout)
        clean = str(code or "").removeprefix("0x")
        count = len(clean) // 2 if clean else 0
        require(count > 0, f"contract {key} has no code at {address}")
        code_bytes[str(key)] = count
    require(bool(code_bytes), "no deployed contract code was verified")
    return code_bytes


def verify_hub(profile: HubNetworkProfile, *, hub_url: str, timeout: float) -> dict[str, Any]:
    health = http_json("GET", join_url(hub_url, "/api/hub/v1/health"), timeout=timeout)
    require(health.get("ok") is True, "Hub health did not return ok=true")
    status = http_json("GET", join_url(hub_url, "/api/hub/v1/status"), timeout=timeout)
    network = status.get("network") if isinstance(status.get("network"), dict) else {}
    require(network.get("network_key") == profile.network_key, f"Hub reports network {network.get('network_key')!r}, expected {profile.network_key!r}")
    require(int(network.get("chain_id", -1)) == int(profile.chain_id or -1), "Hub status chain_id does not match profile")
    require(str(network.get("chain_rpc_url")) == str(profile.chain_rpc_url), "Hub status chain_rpc_url does not match profile")
    return {"health": health, "status": status, "network": network}


def register_worker(hub_url: str, *, worker_node_id: str, model: str, price: int, timeout: float) -> dict[str, Any]:
    return http_json(
        "POST",
        join_url(hub_url, "/api/hub/v1/workers/register"),
        body={
            "node_id": worker_node_id,
            "endpoint": "http://127.0.0.1:1",
            "model": model,
            "models": [model],
            "credits_per_request": price,
            "execution_mode": "worker_pull_v0",
            "pricing": {
                "pricing_type": "fixed_per_call_v0",
                "credits_per_request": price,
                "unit": "compute_credit",
            },
            "capabilities": {"provider": "network-client-smoke", "worker_pull_v0": True},
            "max_concurrency": 1,
            "metadata": {"phase": PHASE},
        },
        timeout=timeout,
    )


def run_level4_paid_credit_flow(
    *,
    hub_url: str,
    network: str,
    smoke_wallet_address: str,
    scope: str,
    timeout: float,
    funding_credits: int = DEFAULT_CREDIT_FUNDING,
    worker_price: int = DEFAULT_WORKER_PRICE,
) -> dict[str, Any]:
    account_id = wallet_account_id(smoke_wallet_address)
    worker_node_id = f"hub-client-smoke-worker-{network}-{scope}"
    model = f"hub-client-smoke-model-{network}"
    price = int(worker_price)
    require(price > 0, "worker price must be positive")

    issued = http_json(
        "POST",
        join_url(hub_url, "/api/hub/v1/credits/admin/issue"),
        body={
            "account_id": account_id,
            "credits": int(max(funding_credits, price * 4)),
            "memo": f"{PHASE} funding for {network}/{scope}",
            "owner_address": smoke_wallet_address,
            "metadata": {"phase": PHASE, "network": network, "scope": scope},
        },
        timeout=timeout,
    )

    register_worker(hub_url, worker_node_id=worker_node_id, model=model, price=price, timeout=timeout)
    heartbeat = http_json(
        "POST",
        join_url(hub_url, "/api/hub/v1/workers/heartbeat"),
        body={"worker_node_id": worker_node_id, "status": "available", "queue_depth": 1, "models": [model]},
        timeout=timeout,
    )

    quote = http_json(
        "POST",
        join_url(hub_url, "/api/hub/v1/requests/quote"),
        body={
            "account_id": account_id,
            "model": model,
            "messages": [{"role": "user", "content": f"Quote {PHASE} {network}/{scope}."}],
            "max_credits": price,
            "execution_mode": "worker_pull_v0",
            "pricing_mode": "market_offer_fixed_per_call_v0",
            "idempotency_key": f"{PHASE}-{network}-{scope}-quote",
        },
        timeout=timeout,
    )["quote"]

    submitted = http_json(
        "POST",
        join_url(hub_url, "/api/hub/v1/requests"),
        body={
            "account_id": account_id,
            "client_node_id": account_id,
            "quote_id": quote["quote_id"],
            "model": model,
            "messages": [{"role": "user", "content": f"Run {PHASE} {network}/{scope}."}],
            "max_credits": price,
            "execution_mode": "worker_pull_v0",
            "pricing_mode": "market_offer_fixed_per_call_v0",
            "metadata": {"worker_pull_v0": True, "phase": PHASE, "network": network, "scope": scope},
            "idempotency_key": f"{PHASE}-{network}-{scope}-request",
        },
        timeout=timeout,
    )["request"]

    polled = http_json(
        "POST",
        join_url(hub_url, "/api/hub/v1/workers/poll"),
        body={"worker_node_id": worker_node_id},
        timeout=timeout,
    )
    lease = polled.get("lease")
    require(isinstance(lease, dict), "worker did not receive a lease for the smoke request")
    require(lease.get("request_id") == submitted["request_id"], "worker lease request_id does not match submitted request")

    completed = http_json(
        "POST",
        join_url(hub_url, "/api/hub/v1/workers/results"),
        body={
            "worker_node_id": worker_node_id,
            "request_id": lease["request_id"],
            "lease_id": lease["lease_id"],
            "result": {
                "status": "success",
                "response": {
                    "content": f"{PHASE} completed for {network}/{scope}.",
                    "provider": "mock-network-client-worker",
                    "model": model,
                    "metadata": {"phase": PHASE, "network": network, "scope": scope},
                },
            },
        },
        timeout=timeout,
    )["request"]

    charges = http_json(
        "GET",
        join_url(hub_url, f"/api/hub/v1/requests/{lease['request_id']}/charges"),
        timeout=timeout,
    )
    earnings = http_json(
        "GET",
        join_url(
            hub_url,
            "/api/hub/v1/credits/worker-earnings?"
            + urlencode({"worker_node_id": worker_node_id, "request_id": lease["request_id"]}),
        ),
        timeout=timeout,
    )
    claim = http_json(
        "POST",
        join_url(hub_url, "/api/hub/v1/workers/claims"),
        body={
            "worker_node_id": worker_node_id,
            "claim_credits": price,
            "idempotency_key": f"{PHASE}-{network}-{scope}-claim",
            "memo": f"{PHASE} claim for {network}/{scope}",
            "metadata": {"phase": PHASE, "network": network, "scope": scope},
        },
        timeout=timeout,
    )
    balance = http_json(
        "GET",
        join_url(hub_url, "/api/hub/v1/credits/balance?" + urlencode({"wallet_address": smoke_wallet_address})),
        timeout=timeout,
    )

    require(completed.get("state") == "completed", "paid request did not complete")
    require(int(completed.get("charged_credits", 0) or 0) == price, "completed request charged unexpected credits")
    require(int(charges.get("charge_count", 0) or 0) == 1, "paid request did not produce exactly one charge")
    require(int(earnings.get("worker_earning_count", 0) or 0) >= 1, "worker earning was not recorded")
    require(bool(claim.get("ok", True)), "worker claim did not return ok=true")

    return {
        "account_id": account_id,
        "worker_node_id": worker_node_id,
        "model": model,
        "request_id": completed["request_id"],
        "charged_credits": completed["charged_credits"],
        "charge_count": charges.get("charge_count"),
        "worker_earning_count": earnings.get("worker_earning_count"),
        "claim": claim,
        "balance": balance,
        "issued": issued,
        "heartbeat_ok": bool(heartbeat.get("ok")),
    }


def run_smoke(
    *,
    network: str,
    hub_url_override: str | None = None,
    network_config: str | None = None,
    deployment_manifest: str | None = None,
    scope: str | None = None,
    timeout: float = 10.0,
    skip_level4: bool = False,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    registry = load_hub_network_registry(network_config)
    profile = registry.get(network)
    hub_url = (hub_url_override or profile.hub_url).rstrip("/")
    scope_value = clean_scope(scope)

    manifest_path, manifest = load_deployment_manifest(profile.network_key, deployment_manifest, repo_root=repo_root)
    profile = profile_with_deployment_manifest_defaults(profile, manifest, manifest_path=manifest_path)
    profile.validate_runnable()
    manifest_bits = validate_manifest(profile, manifest)
    wallet = load_smoke_client_wallet(manifest_bits["smoke_client"], expected_chain_id=int(profile.chain_id or 0), repo_root=repo_root)

    hub = verify_hub(profile, hub_url=hub_url, timeout=timeout)
    chain = verify_chain(profile, timeout=timeout)
    contracts = verify_contract_code(profile, manifest_bits["contracts"], timeout=timeout)
    native_balance_hex = rpc_json(profile.chain_rpc_url or "", "eth_getBalance", [wallet["address"], "latest"], timeout=timeout)
    native_balance_wei = int(str(native_balance_hex), 16)
    require(native_balance_wei > 0, "smoke client wallet has no native chain balance")

    level4 = None
    if not skip_level4:
        level4 = run_level4_paid_credit_flow(
            hub_url=hub_url,
            network=profile.network_key,
            smoke_wallet_address=wallet["address"],
            scope=scope_value,
            timeout=timeout,
        )

    return {
        "ok": True,
        "phase": PHASE,
        "network": profile.network_key,
        "hub_url": hub_url,
        "chain": chain,
        "hub": {
            "network": hub["network"],
            "api_version": hub["status"].get("api_version"),
            "worker_count": hub["status"].get("worker_count"),
        },
        "deployment_manifest": str(manifest_path),
        "contract_code_bytes": contracts,
        "smoke_client": {
            "address": wallet["address"],
            "wallet_path": wallet["_path"],
            "native_balance_wei": native_balance_wei,
            "account_id": wallet_account_id(wallet["address"]),
        },
        "level4": level4,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an end-to-end Hub network client smoke against dev/test/testnet/mainnet profiles.")
    parser.add_argument("--network", default="dev", help="Hub network profile to smoke: dev, test, testnet, or mainnet.")
    parser.add_argument("--hub-url", default=None, help="Override the Hub URL. Defaults to the profile Hub host/port.")
    parser.add_argument("--network-config", default=None, help="Override the Hub networks JSON path.")
    parser.add_argument("--deployment-manifest", default=None, help="Override deployment manifest path.")
    parser.add_argument("--scope", default=None, help="Unique run scope for idempotency keys.")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--skip-level4", action="store_true", help="Only verify Hub/network/deployment wiring; skip paid credit flow.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    try:
        result = run_smoke(
            network=args.network,
            hub_url_override=args.hub_url,
            network_config=args.network_config,
            deployment_manifest=args.deployment_manifest,
            scope=args.scope,
            timeout=max(1.0, args.timeout),
            skip_level4=args.skip_level4,
        )
    except (SmokeFailure, HubNetworkConfigError, ValueError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"Hub network client smoke failed: {exc}", file=sys.stderr)
        return 1

    report_path = REPO_ROOT / "runtime" / "hub" / f"hub_network_client_smoke_{result['network']}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("Hub network client smoke passed.")
        print(f"  network: {result['network']}")
        print(f"  hub_url: {result['hub_url']}")
        print(f"  chain_id: {result['chain']['chain_id']}")
        print(f"  block_number: {result['chain']['block_number']}")
        print(f"  smoke_client: {result['smoke_client']['address']}")
        print(f"  native_balance_wei: {result['smoke_client']['native_balance_wei']}")
        print(f"  contracts_verified: {', '.join(sorted(result['contract_code_bytes']))}")
        if result.get("level4"):
            print(f"  level4_request_id: {result['level4']['request_id']}")
            print(f"  level4_charged_credits: {result['level4']['charged_credits']}")
            print(f"  level4_worker_node_id: {result['level4']['worker_node_id']}")
        print(f"  report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
