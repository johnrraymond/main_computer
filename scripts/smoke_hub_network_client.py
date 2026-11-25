#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import hashlib
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.contract_config import contract_address_map, load_contract_config, canonical_contract_key
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



PUBLIC_NETWORKS = {"mainnet", "testnet"}
PUBLIC_CONTRACTS = {"hub_credit_bridge_escrow", "xlag-bridge-reserve"}


def verify_stable_hub(profile: HubNetworkProfile, *, hub_url: str, timeout: float) -> dict[str, Any]:
    service = "main_computer.stable_hub"
    health = http_json("GET", join_url(hub_url, "/health"), timeout=timeout)
    identity = http_json("GET", join_url(hub_url, "/api/hub/v1/hub-identity"), timeout=timeout)
    discovery = http_json("GET", join_url(hub_url, "/api/hub/v1/topology"), timeout=timeout)
    for name, row in (("health", health), ("identity", identity), ("topology", discovery)):
        require(row.get("_http_status") == 200 and row.get("ok") is True and row.get("service") == service,
                f"Stable Hub {name} response not verified")
    network = identity.get("network") or {}
    require(network.get("network_key") == profile.network_key, "Stable Hub network mismatch")
    require(network.get("network_kind") == profile.kind, "Stable Hub network kind mismatch")
    require(int(network.get("chain_id", -1)) == profile.chain_id, "Stable Hub chain ID mismatch")
    topology = discovery.get("topology") or {}
    require(topology.get("kind") == "main_computer.stable_hub_topology.v1", "Stable Hub topology kind mismatch")
    cluster = identity.get("cluster_id")
    require(isinstance(cluster, str) and bool(cluster) and cluster == health.get("cluster_id") == topology.get("cluster_id"),
            "Stable Hub cluster identity mismatch")
    members = topology.get("hubs") or []
    require(isinstance(members, list) and all(isinstance(row, dict) for row in members), "Invalid Hub topology members")
    ids = [row.get("hub_id") for row in members]
    require(bool(ids) and all(isinstance(i, str) and i for i in ids) and len(set(ids)) == len(ids), "Invalid Hub topology IDs")
    require(all(row.get("hub_id") in ids for row in (health, identity, discovery)), "Responder is not a topology member")
    require(profile.hub_url.rstrip("/") in [str(url).rstrip("/") for url in topology.get("entry_urls", [])], "Canonical entry URL absent from topology")
    require(any(str(row.get("hub_url", "")).rstrip("/") == str(identity.get("hub_url", "")).rstrip("/") and row.get("hub_id") == identity.get("hub_id") for row in members), "Hub identity URL disagrees with topology")
    for contract in (identity.get("contract") or {}, discovery.get("contract") or {}):
        require(contract.get("auth") == "multisession-wallet" and contract.get("worker_live_session_transport") == "websocket" and contract.get("routing") == "entry-hub-reserves-with-worker-home-hub-then-returns-concrete-execution-hub-url", "Stable Hub live-session/routing contract mismatch")
    return {"cluster_id": cluster, "network": network, "health_hub_id": health["hub_id"],
            "identity_hub_id": identity["hub_id"], "topology_hub_id": discovery["hub_id"],
            "advertised_hub_ids": ids, "temporal": health.get("temporal"),
            "routing_scope": "ADVERTISED_CONTRACT_ONLY_NOT_WORK_EXECUTION"}


def public_contract_records(profile: HubNetworkProfile) -> dict[str, dict[str, str]]:
    loaded = load_contract_config(profile.network_key, path=profile.source_path.with_name(f"{profile.network_key}_contracts.json"), required=True)
    addresses = {canonical_contract_key(k): v for k, v in contract_address_map(loaded[1]).items()}
    require(PUBLIC_CONTRACTS <= addresses.keys(), "Canonical public contract registry missing escrow or Reserve")
    return {k: {"address": v} for k, v in addresses.items()}


def inspect_manifest(profile: HubNetworkProfile, manifest: dict[str, Any], records: dict[str, dict[str, str]]) -> dict[str, Any]:
    require(manifest.get("schema") == "main-computer.deployment.v1", "Unexpected deployment manifest schema")
    require(manifest.get("environment") == profile.network_key, "Deployment manifest environment mismatch")
    chain = manifest.get("chain") or {}
    require(int(chain.get("chain_id", -1)) == profile.chain_id, "Deployment manifest chain ID mismatch")
    old_rpc = str(chain.get("rpc_url") or chain.get("host_rpc_url") or "")
    old_addresses = {canonical_contract_key(k): v for k, v in contract_address_map(manifest).items()}
    differences = {k: {"manifest": old_addresses.get(k), "canonical": v["address"]} for k, v in records.items()
                   if str(old_addresses.get(k) or "").lower() != v["address"].lower()}
    return {"rpc_matches": old_rpc == profile.chain_rpc_url,
            "manifest_rpc_url": old_rpc, "canonical_rpc_url": profile.chain_rpc_url,
            "contract_address_differences": differences,
            "historical_only_contracts": sorted(set(old_addresses) - set(records)),
            "matches": old_rpc == profile.chain_rpc_url and not differences}


def verify_public_contracts(profile: HubNetworkProfile, records: dict[str, dict[str, str]], *, timeout: float) -> dict[str, Any]:
    result = {}
    for name, record in records.items():
        address = record["address"]
        require(bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", address)), f"Invalid contract address: {name}")
        code = rpc_json(profile.chain_rpc_url, "eth_getCode", [address, "latest"], timeout=timeout)
        require(isinstance(code, str) and bool(re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", code)), f"Missing or malformed runtime code: {name} at {address}")
        raw = bytes.fromhex(code[2:])
        result[name] = {"address": address, "code_bytes": len(raw), "code_sha256": hashlib.sha256(raw).hexdigest()}
    return result


def run_public_preflight(profile: HubNetworkProfile, *, hub_url: str, deployment_manifest: str | None, repo_root: Path, timeout: float) -> dict[str, Any]:
    """Independent read-only probes. Never query the historical manifest's RPC or load wallet keys."""
    report = {"ok": False, "phase": "hub-network-client-smoke-v2", "network": profile.network_key,
              "hub_url": hub_url, "scope": "canonical-public-network-read-only", "checks": {}, "errors": {},
              "limitations": ["no paid work, FDB durability, live routing or financial settlement proof", "contract runtime presence and hash, not verified artifact equivalence", "no smoke wallet private key or funding prerequisite"]}
    def probe(name, action):
        try:
            value = action()
            report["checks"][name] = True
            report[name] = value
            return value
        except (SmokeFailure, ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
            report["checks"][name] = False
            report["errors"][name] = str(exc)
            return None
    def canonical():
        profile.validate_runnable()
        require(all(urlparse(url).scheme == "https" and urlparse(url).hostname for url in (profile.chain_rpc_url, hub_url, profile.hub_url)), "Public network requires canonical HTTPS endpoints")
        return True
    if not probe("canonical_endpoints", canonical):
        return report
    probe("hub", lambda: verify_stable_hub(profile, hub_url=hub_url, timeout=timeout))
    chain = probe("chain", lambda: verify_chain(profile, timeout=timeout))
    records = probe("contract_registry", lambda: public_contract_records(profile))
    if chain is not None and records is not None:
        probe("contracts", lambda: verify_public_contracts(profile, records, timeout=timeout))
    else:
        report["checks"]["contracts"] = False
        report["errors"]["contracts"] = "NOT_RUN_CHAIN_OR_REGISTRY_UNVERIFIED"
    def manifest_check():
        path, manifest = load_deployment_manifest(profile.network_key, deployment_manifest, repo_root=repo_root)
        report["deployment_manifest"] = str(path)
        require(records is not None, "Canonical contract registry unavailable")
        evidence = inspect_manifest(profile, manifest, records)
        report["manifest_evidence"] = evidence
        require(evidence["matches"], "Deployment manifest differs from canonical public registry; reconciliation required")
        return evidence
    probe("manifest", manifest_check)
    report["canonical_live_ok"] = all(report["checks"].get(key) is True for key in ("canonical_endpoints", "hub", "chain", "contract_registry", "contracts"))
    report["ok"] = report["canonical_live_ok"] and report["checks"].get("manifest") is True
    return report

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
    raise SmokeFailure(
        "Retired: legacy Hub funding and Worker claim HTTP mutations were deleted. "
        "No credits issued and no Worker claim submitted."
    )


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
    if not skip_level4:
        raise SmokeFailure(
            'Retired: legacy Hub funding and Worker claim HTTP mutation APIs were deleted. '
            'Use --skip-level4 for read-only network verification.'
        )
    registry = load_hub_network_registry(network_config)
    profile = registry.get(network)
    hub_url = (hub_url_override or profile.hub_url).rstrip("/")
    scope_value = clean_scope(scope)
    if profile.network_key in PUBLIC_NETWORKS:
        return run_public_preflight(profile, hub_url=hub_url, deployment_manifest=deployment_manifest, repo_root=repo_root, timeout=timeout)

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

    if result.get("scope") == "canonical-public-network-read-only":
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            for name, passed in result["checks"].items():
                print(f"HUB_NETWORK {result['network']} {name} {'PASS' if passed else 'FAIL'} {result['errors'].get(name, '')}")
            print(f"HUB_NETWORK {result['network']} {'PASS_READ_ONLY' if result['ok'] else 'BLOCKED'} canonical_live={result.get('canonical_live_ok', False)} report={report_path}")
        return 0 if result["ok"] else 1
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
