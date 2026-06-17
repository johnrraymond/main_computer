from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


StatusCallback = Callable[..., None]


@dataclass(frozen=True)
class DevChainSmokeContext:
    """Per-smoke dev-chain identity set and public deployment metadata."""

    run_id: str
    deployment_path: Path
    rpc_url: str
    chain_id: int | None
    requester_wallet_address: str | None
    hub_admin_wallet_address: str | None
    bridge_escrow_address: str | None
    node_wallet_addresses: tuple[str, ...]
    payout_admin_wallet_addresses: tuple[str, ...]
    before_balances_wei: dict[str, int]
    reset_command: tuple[str, ...]

    def rollup(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "deployment_path": str(self.deployment_path),
            "rpc_url": self.rpc_url,
            "chain_id": self.chain_id,
            "requester_wallet_address": self.requester_wallet_address,
            "hub_admin_wallet_address": self.hub_admin_wallet_address,
            "bridge_escrow_address": self.bridge_escrow_address,
            "node_wallet_count": len(self.node_wallet_addresses),
            "payout_admin_wallet_count": len(self.payout_admin_wallet_addresses),
            "node_wallet_addresses": list(self.node_wallet_addresses),
            "payout_admin_wallet_addresses": list(self.payout_admin_wallet_addresses),
            "before_balances_wei": dict(self.before_balances_wei),
            "reset_command": list(self.reset_command),
        }


def _deployment_output_root(repo_root: Path) -> Path:
    return repo_root / "runtime" / "deployments"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"deployment JSON must be an object: {path}")
    return payload


def _wallet_addresses(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    group = payload.get(key)
    if not isinstance(group, dict):
        return ()
    addresses: list[str] = []
    for item in group.get("wallets", []):
        if not isinstance(item, dict):
            continue
        address = str(item.get("address") or "").strip()
        if address:
            addresses.append(address)
    return tuple(addresses)


def _single_wallet_address(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, dict):
        return None
    address = str(value.get("address") or "").strip()
    return address or None


def _deployment_address(payload: dict[str, Any], key: str) -> str | None:
    contracts = payload.get("contracts") if isinstance(payload.get("contracts"), dict) else {}
    if not contracts:
        contracts = payload.get("deployments") if isinstance(payload.get("deployments"), dict) else {}
    deployment = contracts.get(key) if isinstance(contracts, dict) else None
    if not isinstance(deployment, dict):
        return None
    address = str(deployment.get("address") or "").strip()
    return address or None


def _rpc_balance_wei(rpc_url: str, address: str, *, timeout_s: float = 5.0) -> int:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getBalance",
            "params": [address, "latest"],
        }
    ).encode("utf-8")
    request = Request(rpc_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or "result" not in payload:
        raise ValueError(f"unexpected eth_getBalance response for {address}: {payload}")
    return int(str(payload["result"]), 16)


def snapshot_balances_wei(rpc_url: str, addresses: list[str] | tuple[str, ...]) -> dict[str, int]:
    balances: dict[str, int] = {}
    for address in addresses:
        try:
            balances[address] = _rpc_balance_wei(rpc_url, address)
        except Exception:
            # Balance snapshots are diagnostic rollup data.  Do not make a
            # successful dev-chain bring-up fail because a readback races the node.
            balances[address] = -1
    return balances


def _emit_status(status: StatusCallback | None, event: str, **fields: Any) -> None:
    if status is not None:
        status(event, **fields)


def _display_command(command: tuple[str, ...]) -> str:
    return " ".join(str(part) for part in command)


def _should_forward_reset_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith(("SETUP:", "ERROR:", "Run id:", "Run directory:", "Host RPC URL:", "Generated node wallets:", "Generated payout admin wallets:")):
        return True
    if stripped.startswith(("Smoke client address:", "First node wallet:", "First payout admin wallet:")):
        return True
    if stripped.startswith(("Wrote ", "EIP-1559 preflight passed:", "PUSH0 preflight passed:")):
        return True
    return False


def _run_dev_chain_reset(command: tuple[str, ...], *, cwd: Path, status: StatusCallback | None) -> subprocess.CompletedProcess[str]:
    """Run dev-chain-reset with streamed operator-facing setup output."""

    _emit_status(status, "dev_chain_reset_process_start", command=_display_command(command))
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        list(command),
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )

    output_lines: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        output_lines.append(line)
        stripped = line.rstrip()
        if _should_forward_reset_line(stripped):
            _emit_status(status, "dev_chain_reset_output", line=stripped)

    returncode = process.wait()
    stdout = "".join(output_lines)
    _emit_status(status, "dev_chain_reset_process_done", returncode=returncode)
    return subprocess.CompletedProcess(list(command), returncode, stdout=stdout, stderr="")


def bring_up_dev_chain_for_smoke(
    *,
    repo_root: Path,
    smoke_name: str,
    run_id: str,
    node_wallet_count: int,
    payout_admin_wallet_count: int = 4,
    port_strategy: str = "auto",
    wait_timeout_s: float = 0.0,
    deploy_timeout_s: float = 0.0,
    status: StatusCallback | None = None,
) -> DevChainSmokeContext:
    """Reset/deploy the local dev chain and return per-run identities.

    The generated node and payout-admin wallets are intentionally run-scoped so
    operators can inspect the chain after a smoke and distinguish one run from
    another.  This only provisions the chain and identities; Hub bridge debits,
    credits, and audit lifecycle still run through the existing Hub bridge seam.
    """

    root = repo_root.resolve()
    dev_chain_run_id = f"{smoke_name}-{run_id}"
    accounts = max(4, int(node_wallet_count or 0) + int(payout_admin_wallet_count or 0) + 4)
    command = (
        sys.executable,
        "-u",
        str(root / "tools" / "dev-chain-reset.py"),
        "--yes",
        "--run-id",
        dev_chain_run_id,
        "--port-strategy",
        str(port_strategy),
        "--run-scoped-wallets",
        "--node-wallet-count",
        str(max(0, int(node_wallet_count or 0))),
        "--payout-admin-wallet-count",
        str(max(0, int(payout_admin_wallet_count or 0))),
        "--accounts",
        str(accounts),
        "--wait-timeout-s",
        str(wait_timeout_s),
        "--deploy-timeout-s",
        str(deploy_timeout_s),
    )
    _emit_status(
        status,
        "dev_chain_setup_start",
        run_id=dev_chain_run_id,
        node_wallet_count=max(0, int(node_wallet_count or 0)),
        payout_admin_wallet_count=max(0, int(payout_admin_wallet_count or 0)),
        accounts=accounts,
        port_strategy=port_strategy,
    )
    completed = _run_dev_chain_reset(command, cwd=root, status=status)
    if completed.returncode != 0:
        details = "\n".join(
            part
            for part in (
                f"command: {' '.join(command)}",
                "stdout:",
                completed.stdout.strip(),
                "stderr:",
                completed.stderr.strip(),
            )
            if part
        )
        raise RuntimeError(f"dev-chain bring-up failed for {dev_chain_run_id}\n{details}")

    deployment_path = _deployment_output_root(root) / "dev" / "latest.json"
    _emit_status(status, "dev_chain_deployment_load", deployment_path=deployment_path)
    deployment = _load_json(deployment_path)
    chain = deployment.get("chain") if isinstance(deployment.get("chain"), dict) else {}
    rpc_url = str(chain.get("host_rpc_url") or chain.get("rpc_url") or "").strip()
    if not rpc_url:
        raise RuntimeError(f"dev-chain deployment did not publish an RPC URL: {deployment_path}")
    chain_id_raw = chain.get("chain_id")
    try:
        chain_id = int(chain_id_raw) if chain_id_raw is not None else None
    except (TypeError, ValueError):
        chain_id = None

    requester = _single_wallet_address(deployment, "smoke_client")
    hub_admin = _single_wallet_address(deployment, "hub_admin")
    bridge_escrow = _deployment_address(deployment, "hub_credit_bridge_escrow")
    node_wallets = _wallet_addresses(deployment, "node_wallets")
    payout_admins = _wallet_addresses(deployment, "payout_admin_wallets")
    balance_addresses = [
        address
        for address in ([requester] if requester else [])
        + ([hub_admin] if hub_admin else [])
        + ([bridge_escrow] if bridge_escrow else [])
        + list(node_wallets)
        + list(payout_admins)
    ]
    _emit_status(status, "dev_chain_balance_snapshot_start", address_count=len(balance_addresses), rpc_url=rpc_url)
    before_balances = snapshot_balances_wei(rpc_url, balance_addresses)
    _emit_status(status, "dev_chain_setup_ready", rpc_url=rpc_url, chain_id=chain_id, address_count=len(balance_addresses))

    return DevChainSmokeContext(
        run_id=dev_chain_run_id,
        deployment_path=deployment_path,
        rpc_url=rpc_url,
        chain_id=chain_id,
        requester_wallet_address=requester,
        hub_admin_wallet_address=hub_admin,
        bridge_escrow_address=bridge_escrow,
        node_wallet_addresses=node_wallets,
        payout_admin_wallet_addresses=payout_admins,
        before_balances_wei=before_balances,
        reset_command=command,
    )
