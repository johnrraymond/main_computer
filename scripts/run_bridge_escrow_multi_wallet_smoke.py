#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


DEFAULT_MANIFEST = Path("runtime/hub/bridge_escrow_dev_manifest.json")
DEFAULT_REPORT = Path("runtime/hub/bridge_escrow_multi_wallet_smoke.json")
FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"


class SmokeFailure(RuntimeError):
    pass


def emit(text: str, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    try:
        stream.write(text)
        stream.flush()
    except UnicodeEncodeError:
        encoding = stream.encoding or "utf-8"
        stream.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
        stream.flush()


def log(text: str = "") -> None:
    emit(text + "\n")


def tail(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[-limit:]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SmokeFailure(
            f"Missing manifest: {path}. Run scripts/prepare_bridge_escrow_dev_manifest.py first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"Manifest is not valid JSON: {path}") from exc
    if not isinstance(loaded, dict):
        raise SmokeFailure(f"Manifest root is not a JSON object: {path}")
    return loaded


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
        raise SmokeFailure(f"{method} {url} returned HTTP {exc.code}: {detail[:1000]}") from exc
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
    return decoded


def rpc_json(
    rpc_url: str,
    method: str,
    params: list[Any] | None = None,
    *,
    timeout: float = 10.0,
) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    decoded = http_json("POST", rpc_url, body=payload, timeout=timeout)
    if "error" in decoded:
        raise SmokeFailure(f"RPC {method} returned error: {decoded['error']}")
    return decoded.get("result")


def clean_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def manifest_requesters(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    actors = manifest.get("actors")
    require(isinstance(actors, dict), "manifest actors must be an object")
    requesters = actors.get("requesters")
    require(isinstance(requesters, list), "manifest actors.requesters must be a list")
    require(len(requesters) >= 4, "manifest must contain at least the top four requester wallets")
    cleaned: list[dict[str, Any]] = []
    for index, raw in enumerate(requesters[:4]):
        require(isinstance(raw, dict), f"requester {index} must be an object")
        account_id = str(raw.get("account_id", "")).strip()
        address = str(raw.get("address", "")).strip()
        require(account_id, f"requester {index} is missing account_id")
        require(address.startswith("0x") and len(address) == 42, f"requester {index} has invalid address")
        deposit_credits = clean_int(raw.get("deposit_credits"), default=100)
        deposit_units = clean_int(raw.get("deposit_units"), default=deposit_credits)
        require(deposit_credits > 0, f"requester {index} deposit_credits must be positive")
        require(deposit_units > 0, f"requester {index} deposit_units must be positive")
        cleaned.append(dict(raw, index=index, deposit_credits=deposit_credits, deposit_units=deposit_units))
    return cleaned


def chain_config(manifest: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    chain = manifest.get("chain") if isinstance(manifest.get("chain"), dict) else {}
    return {
        "rpc_url": args.rpc_url or str(chain.get("rpc_url") or "http://127.0.0.1:18545"),
        "chain_id": clean_int(args.chain_id or chain.get("chain_id"), default=42424242),
        "contract_address": str(args.contract_address or chain.get("contract_address") or "").strip(),
    }


def hub_url_from_manifest(manifest: dict[str, Any], args: argparse.Namespace) -> str:
    hub = manifest.get("hub") if isinstance(manifest.get("hub"), dict) else {}
    return str(args.hub_url or hub.get("url") or "http://127.0.0.1:8770").rstrip("/")


def env_or_manifest_private_key(requester: dict[str, Any]) -> str:
    key = str(requester.get("private_key", "") or "").strip()
    if key:
        return key
    env_name = str(requester.get("private_key_env", "") or "").strip()
    if env_name:
        return os.environ.get(env_name, "").strip()
    return ""


def docker_mount_path(path: Path) -> str:
    return path.resolve().as_posix() if os.name == "nt" else str(path.resolve())


def docker_rpc_url(rpc_url: str) -> str:
    parsed = urlparse(rpc_url)
    if parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost"}:
        netloc = "host.docker.internal"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return rpc_url


def cast_command(base_args: list[str], *, repo_root: Path, rpc_url: str, no_docker: bool) -> tuple[list[str], str]:
    cast = shutil.which("cast")
    if cast:
        return [cast, *base_args], rpc_url

    docker = shutil.which("docker")
    if not docker or no_docker:
        raise SmokeFailure("Neither local cast nor Docker is available for chain-backed deposits.")

    rewritten_rpc = docker_rpc_url(rpc_url)
    docker_args = [
        docker,
        "run",
        "--rm",
        "-e",
        "NO_COLOR=1",
        "-e",
        "CLICOLOR=0",
        "-v",
        f"{docker_mount_path(repo_root)}:/workspace",
        "-w",
        "/workspace",
        "--entrypoint",
        "cast",
        FOUNDRY_IMAGE,
        *base_args,
    ]
    return docker_args, rewritten_rpc


def run_command(command: list[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(command))
    result = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    if result.stdout:
        emit(result.stdout)
        if not result.stdout.endswith("\n"):
            log()
    if result.stderr:
        emit(result.stderr, err=True)
        if not result.stderr.endswith("\n"):
            emit("\n", err=True)
    return result


def parse_cast_tx(stdout: str, *, fallback_hash: str) -> dict[str, Any]:
    text = stdout or ""
    decoded: Any = None
    tx_hash = ""
    block_number = 0

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = None

    if isinstance(decoded, dict):
        for key in ("transactionHash", "transaction_hash", "hash"):
            value = decoded.get(key)
            if isinstance(value, str) and value.startswith("0x") and len(value) == 66:
                tx_hash = value
                break
        for key in ("blockNumber", "block_number"):
            value = decoded.get(key)
            if isinstance(value, str):
                block_number = int(value, 16) if value.startswith("0x") else clean_int(value)
                break
            if isinstance(value, int):
                block_number = value
                break

    if not tx_hash:
        match = re.search(r"(transactionHash|transaction_hash|hash)\s*[:=]\s*(0x[0-9a-fA-F]{64})", text)
        if match:
            tx_hash = match.group(2)

    if not tx_hash:
        tx_hash = fallback_hash

    return {"tx_hash": tx_hash, "block_number": block_number, "raw": decoded if isinstance(decoded, dict) else None}


def send_chain_deposit(
    *,
    requester: dict[str, Any],
    chain: dict[str, Any],
    repo_root: Path,
    no_docker: bool,
    timeout: float,
) -> dict[str, Any]:
    private_key = env_or_manifest_private_key(requester)
    require(
        bool(private_key),
        (
            f"requester {requester['account_id']} has no private key in manifest/env. "
            "Regenerate the manifest with --include-private-keys or set the requester private-key env var."
        ),
    )
    contract_address = str(chain["contract_address"])
    require(
        contract_address.startswith("0x") and len(contract_address) == 42,
        "chain-backed deposits require a valid escrow contract address",
    )
    require(
        contract_address.lower() != "0x1111111111111111111111111111111111111111",
        "chain-backed deposits require a deployed escrow contract, not the placeholder address",
    )

    deposit_id = str(requester.get("deposit_id") or "").strip()
    require(deposit_id.startswith("0x") and len(deposit_id) == 66, f"invalid deposit_id for {requester['account_id']}")

    memo = f"multi-wallet escrow smoke deposit for {requester['account_id']}"
    fallback_hash = str(requester.get("normalized_receipt_tx_hash") or "").strip()

    base_args = [
        "send",
        contract_address,
        "depositFor(address,uint256,bytes32,string)",
        str(requester["address"]),
        str(requester["deposit_units"]),
        deposit_id,
        memo,
        "--value",
        str(requester["deposit_units"]),
        "--private-key",
        private_key,
        "--rpc-url",
        chain["rpc_url"],
        "--json",
    ]
    command, actual_rpc_url = cast_command(base_args, repo_root=repo_root, rpc_url=chain["rpc_url"], no_docker=no_docker)
    if actual_rpc_url != chain["rpc_url"]:
        command = [actual_rpc_url if item == chain["rpc_url"] else item for item in command]

    result = run_command(command, cwd=repo_root, timeout=timeout)
    if result.returncode != 0:
        raise SmokeFailure(
            f"chain deposit failed for {requester['account_id']} with exit code {result.returncode}: "
            f"{tail(result.stderr or result.stdout, 2000)}"
        )

    parsed = parse_cast_tx(result.stdout, fallback_hash=fallback_hash)
    return {
        "ok": True,
        "tx_hash": parsed["tx_hash"],
        "block_number": parsed["block_number"],
        "stdout_tail": tail(result.stdout, 2000),
        "stderr_tail": tail(result.stderr, 2000),
    }


def build_import_payload(
    *,
    requester: dict[str, Any],
    chain: dict[str, Any],
    tx_hash: str,
    block_number: int,
) -> dict[str, Any]:
    return {
        "chain_id": int(chain["chain_id"]),
        "contract_address": str(chain["contract_address"]),
        "tx_hash": tx_hash,
        "log_index": int(requester.get("log_index", requester["index"])),
        "block_number": max(0, int(block_number or 0)),
        "account_id": str(requester["account_id"]),
        "payer_address": str(requester["address"]),
        "payment_asset": "native",
        "payment_amount_base_units": int(requester["deposit_units"]),
        # Import credit atoms into the hub ledger.  The manifest keeps
        # deposit_credits as a human-readable amount, while deposit_units is the
        # integer atom amount used by paid request accounting.
        "credits_granted": int(requester["deposit_units"]),
        "memo": f"bridge escrow multi-wallet smoke deposit for {requester['account_id']}",
    }


def account_balance(hub_url: str, account_id: str, *, timeout: float) -> dict[str, Any]:
    query = urlencode({"account_id": account_id})
    balance = http_json("GET", f"{hub_url}/api/hub/v1/credits/balance?{query}", timeout=timeout)
    account = balance.get("account")
    require(isinstance(account, dict), f"balance response for {account_id} did not include account")
    return account


def import_deposit_twice(
    *,
    hub_url: str,
    requester: dict[str, Any],
    payload: dict[str, Any],
    timeout: float,
    allow_existing: bool,
) -> dict[str, Any]:
    before_account = account_balance(hub_url, requester["account_id"], timeout=timeout)
    before_available = clean_int(before_account.get("available_credits"), default=0)

    first = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/credits/deposits/import",
        body=payload,
        timeout=timeout,
    )
    require(first.get("ok") is True, f"first import did not return ok=true for {requester['account_id']}")
    first_idempotent = first.get("idempotent") is True
    if first_idempotent and not allow_existing:
        raise SmokeFailure(
            f"deposit for {requester['account_id']} already existed; pass --allow-existing to accept reused receipts"
        )
    first_account = first.get("account")
    first_deposit = first.get("deposit")
    require(isinstance(first_account, dict), f"first import for {requester['account_id']} missing account")
    require(isinstance(first_deposit, dict), f"first import for {requester['account_id']} missing deposit")
    require(
        clean_int(first_deposit.get("credits_granted")) == requester["deposit_units"],
        (
            f"unexpected credits_granted for {requester['account_id']}; "
            f"expected atom units={requester['deposit_units']}"
        ),
    )
    deposit_id = str(first_deposit.get("deposit_id") or "")
    require(deposit_id.startswith("dep_"), f"unexpected deposit_id for {requester['account_id']}: {deposit_id!r}")

    second = http_json(
        "POST",
        f"{hub_url}/api/hub/v1/credits/deposits/import",
        body=payload,
        timeout=timeout,
    )
    require(second.get("ok") is True, f"duplicate import did not return ok=true for {requester['account_id']}")
    require(second.get("idempotent") is True, f"duplicate import was not idempotent for {requester['account_id']}")

    second_account = second.get("account")
    second_deposit = second.get("deposit")
    require(isinstance(second_account, dict), f"duplicate import for {requester['account_id']} missing account")
    require(isinstance(second_deposit, dict), f"duplicate import for {requester['account_id']} missing deposit")
    require(second_deposit.get("deposit_id") == deposit_id, f"duplicate import changed deposit_id for {requester['account_id']}")

    after_available = clean_int(second_account.get("available_credits"), default=-1)
    after_held = clean_int(second_account.get("held_credits"), default=0)
    after_spent = clean_int(second_account.get("spent_credits"), default=0)
    if first_idempotent:
        # Existing receipts may already have been partially spent by later
        # paid-request smokes.  For --allow-existing, validate that the
        # receipt itself is the atom-unit receipt we expect and that the
        # account's known credit columns still reconcile to at least the
        # original deposit amount.
        require(
            after_available + after_held + after_spent >= requester["deposit_units"],
            f"existing imported atom-unit deposit for {requester['account_id']} no longer reconciles",
        )
    else:
        require(
            after_available >= before_available + requester["deposit_units"],
            f"fresh import did not increase available atom units for {requester['account_id']}",
        )

    query = urlencode({"account_id": requester["account_id"]})
    deposits = http_json(
        "GET",
        f"{hub_url}/api/hub/v1/credits/deposits?{query}",
        timeout=timeout,
    )
    rows = deposits.get("deposits")
    require(isinstance(rows, list), f"deposits endpoint did not return list for {requester['account_id']}")
    require(
        any(isinstance(row, dict) and row.get("deposit_id") == deposit_id for row in rows),
        f"deposits endpoint did not include deposit {deposit_id} for {requester['account_id']}",
    )

    return {
        "ok": True,
        "account_id": requester["account_id"],
        "address": requester["address"],
        "deposit_id": deposit_id,
        "first_import_idempotent": first_idempotent,
        "duplicate_import_idempotent": True,
        "before_available_credits": before_available,
        "after_available_credits": after_available,
        "before_available_units": before_available,
        "after_available_units": after_available,
        "deposit_credits": requester["deposit_credits"],
        "credits_granted": requester["deposit_units"],
        "payment_amount_base_units": requester["deposit_units"],
    }


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path.cwd().resolve()
    manifest = read_json_file(args.manifest)
    requesters = manifest_requesters(manifest)
    chain = chain_config(manifest, args)
    hub_url = hub_url_from_manifest(manifest, args)

    report: dict[str, Any] = {
        "ok": False,
        "manifest": str(args.manifest),
        "hub_url": hub_url,
        "chain": chain,
        "requester_count": len(requesters),
        "send_chain_deposits": bool(args.send_chain_deposits),
        "manifest_only": bool(args.manifest_only),
        "requesters": [],
        "steps": [],
        "started_at": time.time(),
    }

    require(
        chain["contract_address"].startswith("0x") and len(chain["contract_address"]) == 42,
        "manifest chain.contract_address must be a valid EVM address",
    )

    report["steps"].append({"name": "manifest_loaded", "ok": True})
    log("Bridge escrow multi-wallet smoke")
    log(f"  manifest: {args.manifest}")
    log(f"  hub:      {hub_url}")
    log(f"  chain:    {chain['chain_id']} / {chain['contract_address']}")
    log(f"  wallets:  {len(requesters)} requesters")
    if args.manifest_only:
        report["ok"] = True
        report["completed_at"] = time.time()
        report["steps"].append({"name": "manifest_only", "ok": True})
        return report

    if args.check_rpc:
        chain_id_hex = rpc_json(chain["rpc_url"], "eth_chainId", timeout=args.timeout)
        rpc_chain_id = int(str(chain_id_hex), 16)
        require(rpc_chain_id == int(chain["chain_id"]), f"RPC chain id {rpc_chain_id} != manifest chain id {chain['chain_id']}")
        report["steps"].append({"name": "rpc_chain_id", "ok": True, "chain_id": rpc_chain_id})
        log(f"  [ok] rpc_chain_id: {rpc_chain_id}")

    status = http_json("GET", f"{hub_url}/api/hub/v1/credits/indexer", timeout=args.timeout)
    require(status.get("ok") is True, "credit indexer did not return ok=true")
    require(
        status.get("event") in {"HubCreditBridgeEscrow.CreditDeposited", "HubCreditSale.CreditPurchased"},
        f"unexpected indexer event: {status.get('event')!r}",
    )
    report["steps"].append({"name": "hub_indexer_status", "ok": True, "mode": status.get("mode"), "event": status.get("event")})
    log(f"  [ok] hub_indexer_status: {status.get('mode')} / {status.get('event')}")

    for requester in requesters:
        log(f"  requester {requester['index']}: {requester['account_id']} / {requester['address']}")
        chain_deposit: dict[str, Any] | None = None
        tx_hash = str(requester.get("normalized_receipt_tx_hash") or "").strip()
        block_number = int(args.block_number)
        require(tx_hash.startswith("0x") and len(tx_hash) == 66, f"invalid normalized_receipt_tx_hash for {requester['account_id']}")

        if args.send_chain_deposits:
            chain_deposit = send_chain_deposit(
                requester=requester,
                chain=chain,
                repo_root=repo_root,
                no_docker=args.no_docker,
                timeout=args.command_timeout,
            )
            tx_hash = str(chain_deposit.get("tx_hash") or tx_hash)
            block_number = clean_int(chain_deposit.get("block_number"), default=block_number)
            log(f"    [ok] chain_deposit: {tx_hash}")

        payload = build_import_payload(
            requester=requester,
            chain=chain,
            tx_hash=tx_hash,
            block_number=block_number,
        )
        if args.no_hub_import:
            row = {
                "ok": True,
                "account_id": requester["account_id"],
                "address": requester["address"],
                "skipped_hub_import": True,
                "payload": payload,
                "chain_deposit": chain_deposit,
            }
        else:
            row = import_deposit_twice(
                hub_url=hub_url,
                requester=requester,
                payload=payload,
                timeout=args.timeout,
                allow_existing=args.allow_existing,
            )
            row["payload"] = payload
            row["chain_deposit"] = chain_deposit
            log(
                "    [ok] hub_deposit_import: "
                f"{row['deposit_id']} available={row['after_available_credits']}"
            )
        report["requesters"].append(row)

    if not args.no_hub_import:
        ledger_status = http_json("GET", f"{hub_url}/api/hub/v1/credits", timeout=args.timeout)
        require(ledger_status.get("ok") is True, "credit ledger status did not return ok=true")
        totals = ledger_status.get("totals")
        require(isinstance(totals, dict), "credit ledger status missing totals")
        report["ledger_status"] = ledger_status
        report["steps"].append(
            {
                "name": "ledger_status",
                "ok": True,
                "deposit_count": ledger_status.get("deposit_count", ledger_status.get("purchase_count")),
                "totals": totals,
            }
        )
        log(
            "  [ok] ledger_status: "
            f"deposits={ledger_status.get('deposit_count', ledger_status.get('purchase_count'))} "
            f"available_total={totals.get('available_credits')}"
        )

    report["ok"] = True
    report["completed_at"] = time.time()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke-test multi-wallet bridge escrow funding prep. By default this imports "
            "normalized escrow deposit receipts for the top four requester wallets into the "
            "hub bridge ledger as integer credit atoms. Pass --send-chain-deposits to also submit escrow deposits "
            "to a deployed HubCreditBridgeEscrow contract first."
        )
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--hub-url", default="")
    parser.add_argument("--rpc-url", default="")
    parser.add_argument("--chain-id", type=int, default=0)
    parser.add_argument("--contract-address", default="")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--command-timeout", type=float, default=120.0)
    parser.add_argument("--block-number", type=int, default=0)
    parser.add_argument("--manifest-only", action="store_true")
    parser.add_argument("--check-rpc", action="store_true")
    parser.add_argument("--send-chain-deposits", action="store_true")
    parser.add_argument("--no-docker", action="store_true")
    parser.add_argument("--no-hub-import", action="store_true")
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help="Accept idempotent first imports when reusing deterministic receipts.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report: dict[str, Any] = {
        "ok": False,
        "manifest": str(args.manifest),
        "error": "not started",
    }
    try:
        report = run_smoke(args)
        write_report(args.report, report)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            log()
            log(f"Wrote smoke report: {args.report}")
            log("Bridge escrow multi-wallet smoke passed.")
        return 0
    except Exception as exc:
        report["ok"] = False
        report["error"] = str(exc)
        report["failed_at"] = time.time()
        try:
            write_report(args.report, report)
            print(f"Wrote failed smoke report: {args.report}", file=sys.stderr)
        except Exception as report_exc:
            print(f"Failed to write smoke report: {report_exc}", file=sys.stderr)
        print(f"bridge escrow multi-wallet smoke failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
