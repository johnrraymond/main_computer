#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from main_computer.prod_lock import find_production_lock, require_unlocked_production_state


DEPLOYMENT_SCHEMA = "main-computer.deployment.v1"
PROD_LOCK_SCHEMA = "main-computer.prod-lock.v1"
CURRENT_DEPLOYMENT = Path("runtime/deployments/current.json")
PROD_LOCK = Path(".prod.lock")

CANONICAL_CONTRACT_ALIASES = {
    "XLagBridgeReserve": (
        "XLagBridgeReserve",
        "xlag-bridge-reserve",
        "xlag_bridge_reserve",
        "xlagBridgeReserve",
        "MAIN_COMPUTER_XLAG_CONTRACT_ADDRESS",
    ),
    "AlphaBetaLockout": (
        "AlphaBetaLockout",
        "alpha-beta-lockout",
        "alpha_beta_lockout",
        "alphaBetaLockout",
        "MAIN_COMPUTER_ALPHA_BETA_LOCKOUT_CONTRACT_ADDRESS",
    ),
}

ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")


def log(message: str = "") -> None:
    print(message, flush=True)


def repo_root() -> Path:
    current = Path(__file__).resolve().parent
    for candidate in (current, *current.parents):
        if (
            (candidate / "new_patch.py").exists()
            or (candidate / "pyproject.toml").exists()
            or (candidate / "contracts").is_dir()
            or (candidate / ".git").exists()
        ):
            return candidate
    return current


def stable_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def normalize_address(value: Any) -> str | None:
    text = str(value or "").strip()
    if not ADDRESS_RE.fullmatch(text):
        return None
    return text


def normalized_address_for_compare(value: str | None) -> str | None:
    return value.lower() if isinstance(value, str) else None


def manifest_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json_document(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"deployment manifest is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"deployment manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"deployment manifest must contain a JSON object: {path}")
    return payload


def contract_maps(deployment: dict[str, Any]) -> list[dict[str, Any]]:
    maps: list[dict[str, Any]] = []
    for key in ("contracts", "deployments"):
        value = deployment.get(key)
        if isinstance(value, dict):
            maps.append(value)
    return maps


def collect_contract_addresses(deployment: dict[str, Any], canonical_name: str) -> list[str]:
    aliases = CANONICAL_CONTRACT_ALIASES[canonical_name]
    alias_keys = {stable_key(alias) for alias in aliases}
    addresses: list[str] = []

    for mapping in contract_maps(deployment):
        for raw_key, raw_record in mapping.items():
            key_matches = stable_key(raw_key) in alias_keys
            target_matches = False
            record = raw_record
            if isinstance(record, dict):
                target = record.get("target")
                target_matches = isinstance(target, str) and canonical_name in target
                raw_address = record.get("address")
            else:
                raw_address = record

            if key_matches or target_matches:
                address = normalize_address(raw_address)
                if address:
                    addresses.append(address)

    return addresses


def contract_address(deployment: dict[str, Any], canonical_name: str) -> str | None:
    addresses = collect_contract_addresses(deployment, canonical_name)
    return addresses[0] if addresses else None


def contract_address_conflict(deployment: dict[str, Any], canonical_name: str) -> bool:
    addresses = collect_contract_addresses(deployment, canonical_name)
    unique = {address.lower() for address in addresses}
    return len(unique) > 1


def deployment_environment(deployment: dict[str, Any]) -> str:
    return str(deployment.get("environment") or "").strip()


def deployment_run_id(deployment: dict[str, Any]) -> str | None:
    value = deployment.get("run_id")
    return str(value) if value is not None else None


def deployment_chain_id(deployment: dict[str, Any]) -> int | None:
    chain = deployment.get("chain")
    if not isinstance(chain, dict):
        return None
    value = chain.get("chain_id")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        raw = value.strip()
        try:
            return int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)
        except ValueError:
            return None
    return None


def deployment_rpc_url(deployment: dict[str, Any]) -> str | None:
    chain = deployment.get("chain")
    if not isinstance(chain, dict):
        return None
    for key in ("rpc_url", "host_rpc_url"):
        value = chain.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def validate_deployment_common(deployment: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if deployment.get("schema") != DEPLOYMENT_SCHEMA:
        errors.append(f"schema must be {DEPLOYMENT_SCHEMA!r}")
    if deployment_chain_id(deployment) is None:
        errors.append("chain.chain_id is required")
    if not deployment_rpc_url(deployment):
        errors.append("chain.rpc_url is required")
    for name in CANONICAL_CONTRACT_ALIASES:
        if contract_address_conflict(deployment, name):
            errors.append(f"{name} address is ambiguous")
        elif not contract_address(deployment, name):
            errors.append(f"{name} address is required")
    return errors


def validate_deployment_for_lock(deployment: dict[str, Any]) -> list[str]:
    errors = validate_deployment_common(deployment)

    environment = deployment_environment(deployment)
    if not environment:
        errors.append("environment is required")
    elif environment == "dev":
        errors.append("refusing to lock dev environment")
    if deployment.get("dry_run") is True:
        errors.append("refusing to lock a dry-run deployment manifest")

    return errors


def canonical_contracts(deployment: dict[str, Any]) -> dict[str, str]:
    contracts: dict[str, str] = {}
    for name in CANONICAL_CONTRACT_ALIASES:
        address = contract_address(deployment, name)
        if address:
            contracts[name] = address
    return contracts


def build_lock_payload(deployment_path: Path, deployment: dict[str, Any]) -> dict[str, Any]:
    environment = deployment_environment(deployment)
    return {
        "schema": PROD_LOCK_SCHEMA,
        "protected": True,
        "deployment": environment,
        "environment": environment,
        "run_id": deployment_run_id(deployment),
        "locked_at": dt.datetime.now(dt.UTC).isoformat(),
        "deployment_manifest": CURRENT_DEPLOYMENT.as_posix(),
        "deployment_manifest_sha256": manifest_sha256(deployment_path),
        "chain_id": deployment_chain_id(deployment),
        "rpc_url": deployment_rpc_url(deployment),
        "contracts": canonical_contracts(deployment),
    }


def load_dev_chain_reset_module(root: Path):
    script = root / "dev-chain-reset.py"
    if not script.exists():
        raise FileNotFoundError(f"missing {script}")
    spec = importlib.util.spec_from_file_location("dev_chain_reset_runtime", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def json_rpc(url: str, method: str, params: list | None = None, *, timeout: float = 5.0):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(data["error"])
    return data.get("result")


def parse_rpc_chain_id(value: Any) -> int:
    if isinstance(value, int):
        return value
    raw = str(value or "").strip()
    return int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)


def run_read_only_checks(deployment: dict[str, Any]) -> bool:
    log()
    log("Read-only deployment checks")
    errors = validate_deployment_common(deployment)
    if errors:
        for error in errors:
            log(f"FAIL: manifest: {error}")
        log("FAIL: current deployment did not pass read-only checks.")
        return False

    rpc_url = deployment_rpc_url(deployment)
    expected_chain_id = deployment_chain_id(deployment)
    assert rpc_url is not None
    assert expected_chain_id is not None

    ok = True
    try:
        actual_chain_id = parse_rpc_chain_id(json_rpc(rpc_url, "eth_chainId"))
        if actual_chain_id == expected_chain_id:
            log(f"PASS: chain-id {actual_chain_id}")
        else:
            log(f"FAIL: chain-id expected {expected_chain_id}, got {actual_chain_id}")
            ok = False
    except Exception as exc:  # noqa: BLE001 - operator diagnostic
        log(f"FAIL: rpc: {exc}")
        ok = False
        log("FAIL: current deployment did not pass read-only checks.")
        return False

    for name, address in canonical_contracts(deployment).items():
        try:
            code = json_rpc(rpc_url, "eth_getCode", [address, "latest"])
            if isinstance(code, str) and code not in {"", "0x", "0x0"}:
                log(f"PASS: {name}.code")
            else:
                log(f"FAIL: {name}.code missing at {address}")
                ok = False
        except Exception as exc:  # noqa: BLE001 - operator diagnostic
            log(f"FAIL: {name}.code: {exc}")
            ok = False

    if not ok:
        log("FAIL: current deployment did not pass read-only checks.")
    return ok


def load_lock_payload(lock_path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - status must stay read-only and diagnostic
        return None
    return payload if isinstance(payload, dict) else None


def lock_drift_warnings(root: Path, deployment_path: Path, deployment: dict[str, Any], lock_path: Path) -> list[str]:
    payload = load_lock_payload(lock_path)
    if payload is None:
        return [f"could not parse production lock at {lock_path}"]

    warnings: list[str] = []

    def check_value(label: str, locked: Any, current: Any) -> None:
        if locked != current:
            warnings.append(f"{label} locked={locked!r} current={current!r}")

    check_value("deployment", payload.get("deployment") or payload.get("environment"), deployment_environment(deployment))
    check_value("run_id", payload.get("run_id"), deployment_run_id(deployment))
    check_value("chain_id", payload.get("chain_id"), deployment_chain_id(deployment))
    check_value("rpc_url", payload.get("rpc_url"), deployment_rpc_url(deployment))

    locked_contracts = payload.get("contracts") if isinstance(payload.get("contracts"), dict) else {}
    current_contracts = canonical_contracts(deployment)
    for name in CANONICAL_CONTRACT_ALIASES:
        locked_address = locked_contracts.get(name)
        current_address = current_contracts.get(name)
        if normalized_address_for_compare(locked_address) != normalized_address_for_compare(current_address):
            warnings.append(f"{name} locked={locked_address!r} current={current_address!r}")

    expected_hash = payload.get("deployment_manifest_sha256")
    if isinstance(expected_hash, str) and expected_hash:
        current_hash = manifest_sha256(deployment_path)
        if expected_hash != current_hash:
            warnings.append("deployment_manifest_sha256 locked value does not match current.json")

    return warnings


def print_status(deployment: dict[str, Any] | None, lock_path: Path | None) -> None:
    log(f"Production lock: {'present' if lock_path else 'absent'}")
    if lock_path:
        log(f"Lock file: {lock_path}")
    if deployment is None:
        log("Deployment: missing")
        return
    log("Deployment: present")
    log(f"Environment: {deployment_environment(deployment) or 'unknown'}")
    chain_id = deployment_chain_id(deployment)
    log(f"Chain ID: {chain_id if chain_id is not None else 'unknown'}")
    log(f"RPC URL: {deployment_rpc_url(deployment) or 'unknown'}")
    for name in CANONICAL_CONTRACT_ALIASES:
        log(f"{name}: {contract_address(deployment, name) or 'missing'}")


def command_status(args: argparse.Namespace) -> int:
    root = repo_root()
    deployment_path = root / CURRENT_DEPLOYMENT
    lock_path = find_production_lock(root, deployment_path)

    if not deployment_path.exists():
        print_status(None, lock_path)
        if args.check:
            log()
            log("FAIL: deployment manifest is missing.")
            return 1
        return 0

    try:
        deployment = load_json_document(deployment_path)
    except ValueError as exc:
        log(f"Production lock: {'present' if lock_path else 'absent'}")
        log(f"Deployment: invalid: {exc}")
        return 1

    print_status(deployment, lock_path)

    if lock_path:
        warnings = lock_drift_warnings(root, deployment_path, deployment, lock_path)
        for warning in warnings:
            log(f"WARNING: production lock drift: {warning}")

    if args.check:
        return 0 if run_read_only_checks(deployment) else 1

    return 0


def command_deploy_local(args: argparse.Namespace) -> int:
    root = repo_root()
    if not args.yes and not args.dry_run:
        log("Refusing to deploy-local without --yes or --dry-run.")
        return 1

    require_unlocked_production_state(
        root,
        root / "runtime" / "dev-chain",
        root / "runtime" / "deployments",
        action="deploy local production-shaped runtime",
    )

    reset = load_dev_chain_reset_module(root)
    reset_args = [
        "--environment",
        "prod-local",
        "--project-name",
        "main-computer-prod-local",
        "--port-strategy",
        "auto",
    ]
    if args.run_id:
        reset_args.extend(["--run-id", args.run_id])
    if args.dry_run:
        reset_args.append("--dry-run")
    if args.yes:
        reset_args.append("--yes")
    if args.host_port is not None:
        reset_args.extend(["--host-port", str(args.host_port)])
    if args.host_rpc_url:
        reset_args.extend(["--host-rpc-url", args.host_rpc_url])
    return int(reset.main(reset_args))


def command_lock(args: argparse.Namespace) -> int:
    root = repo_root()
    deployment_path = root / CURRENT_DEPLOYMENT
    lock_path = root / PROD_LOCK

    if lock_path.exists():
        log(f"ERROR: refusing to overwrite existing production lock at {lock_path}")
        return 1

    try:
        deployment = load_json_document(deployment_path)
    except ValueError as exc:
        log(f"ERROR: {exc}")
        return 1

    errors = validate_deployment_for_lock(deployment)
    if errors:
        for error in errors:
            log(f"ERROR: {error}")
        return 1

    payload = build_lock_payload(deployment_path, deployment)
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.dry_run:
        log(f"Dry run: would write {lock_path}")
        print(text, end="")
        return 0

    lock_path.write_text(text, encoding="utf-8")
    log(f"Wrote {lock_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the Main Computer production-shaped local deployment.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Read the current deployment and production lock state.")
    status.add_argument("--check", action="store_true", help="Run read-only RPC and contract-code checks.")
    status.set_defaults(func=command_status)

    deploy_local = subparsers.add_parser("deploy-local", help="Create a resettable prod-local deployment.")
    deploy_local.add_argument("--yes", action="store_true", help="Run the deploy-local workflow.")
    deploy_local.add_argument("--dry-run", action="store_true", help="Preview the deploy-local workflow.")
    deploy_local.add_argument("--run-id", default=None)
    deploy_local.add_argument("--host-port", type=int, default=None)
    deploy_local.add_argument("--host-rpc-url", default=None)
    deploy_local.set_defaults(func=command_deploy_local)

    lock = subparsers.add_parser("lock", help="Write .prod.lock from runtime/deployments/current.json.")
    lock.add_argument("--dry-run", action="store_true", help="Print the lock payload without writing .prod.lock.")
    lock.set_defaults(func=command_lock)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:  # noqa: BLE001 - operator-facing script
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
